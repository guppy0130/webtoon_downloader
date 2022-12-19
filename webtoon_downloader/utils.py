import concurrent.futures
import logging
import math
import os
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Set
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse
from zipfile import ZipFile

import requests
import requests.adapters
from bs4 import BeautifulSoup, Tag
from lxml import etree
from rich.console import Console
from rich.progress import MofNCompleteColumn, Progress, TaskID

from webtoon_downloader.content_info import ChapterInfo, PageInfo, SeriesInfo

logger = logging.getLogger(__name__)

if os.name == "nt":
    USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
        "AppleWebKit/537.36 (KHTML, like Gecko)"
        "Chrome/92.0.4515.107 Safari/537.36"
    )
else:
    USER_AGENT = (
        "Mozilla/5.0 (X11; Linux ppc64le; rv:75.0)"
        "Gecko/20100101 Firefox/75.0"
    )
HEADERS = {
    "dnt": "1",
    "user-agent": USER_AGENT,
    "accept-language": "en-US,en;q=0.9",
}
IMAGE_HEADERS = {"referer": "https://www.webtoons.com/", **HEADERS}


def pop_query_param(url: str, key: str) -> str:
    u = urlparse(url)
    q = parse_qs(u.query, keep_blank_values=True)
    q.pop(key, "")
    u = u._replace(query=urlencode(q, True))
    return urlunparse(u)


def parse_meta_from_series(soup: BeautifulSoup) -> SeriesInfo:
    """Parses opengraph and other meta tags for series info"""
    opengraph_keys = ["title", "url", "image", "description"]
    series_info = {}
    for key in opengraph_keys:
        for opengraph_entry in soup.find_all("meta", property=f"og:{key}"):
            series_info[key] = opengraph_entry["content"]

    author_soup = soup.find("meta", property="com-linewebtoon:webtoon:author")
    if isinstance(author_soup, Tag):
        series_info["author"] = author_soup["content"]

    series_info["genre"] = [
        g.text for g in soup.find_all("h2", {"class": "genre"})
    ]

    return SeriesInfo(**series_info)


def get_chapters_on_page(soup: BeautifulSoup) -> List[ChapterInfo]:
    """Gets the chapters on a page"""
    chapters: List[ChapterInfo] = []
    for chapter_entry in soup.find_all("li", attrs={"data-episode-no": True}):
        date = chapter_entry.find("span", attrs={"class": "date"}).text
        date_format = "%b %d, %Y"
        chapters.append(
            ChapterInfo(
                title=chapter_entry.find("span", attrs={"class": "subj"}).text,
                data_episode_no=int(chapter_entry["data-episode-no"]),
                date_released=datetime.strptime(date, date_format),
                content_url=str(chapter_entry.find("a")["href"]),
            )
        )
    return chapters


def get_all_chapter_sets(
    url: str,
    session: requests.Session,
    soup: BeautifulSoup,
    progress: Progress,
    task_id: TaskID,
    _size: int = 0,
) -> Set[str]:
    """
    Handles paginating through all the chapter sets (e.g., that page that shows
    ~10 chapters)
    """
    urls = set()
    if pagination_div := soup.find("div", attrs={"class": "paginate"}):
        logger.info("Found pagination, fetching more chapters/sections")
        if not isinstance(pagination_div, Tag):
            logger.exception(
                "Unable to paginate for more chapters, returning what we have"
            )
            return urls

        pagination_div_a_s = pagination_div.find_all("a")
        _size += len(pagination_div_a_s)
        progress.update(
            task_id=task_id,
            total=_size,
            refresh=True,
        )
        for page in pagination_div_a_s:
            if page.text == "Next Page":
                # add the URL of the next page here, because once you index to
                # the next set of pages, you won't have this URL anymore (it'll
                # just be `#`)
                urls.add(page["href"])
                # this is O(N) to the number of pages and we can't parallelize
                # because we don't know how many pages there'll be
                more_soup = _get_soup_without_special_headers(
                    urljoin(url, page["href"]), session=session
                )
                urls = urls.union(
                    get_all_chapter_sets(
                        url=url,
                        session=session,
                        soup=more_soup,
                        progress=progress,
                        task_id=task_id,
                        _size=_size,
                    )
                )
            elif page["href"] == "#":
                continue
            else:
                progress.update(task_id=task_id, advance=1, refresh=True)
                urls.add(page["href"])

    return urls


def _get_soup_without_special_headers(
    url: str, session: requests.Session
) -> BeautifulSoup:
    return BeautifulSoup(session.get(url, headers=HEADERS).text, "lxml")


def get_chapters_in_series(
    url: str,
    session: requests.Session,
    soup: BeautifulSoup,
    console: Console,
) -> List[ChapterInfo]:
    """
    Gets all the chapters in a series. Automatically paginates if necessary.
    """

    chapters = []
    # TODO: optimize - we fetch 1, 11, 21, ..., twice
    with Progress(console=console, transient=True) as progress:
        task_id = progress.add_task("Fetch chapter list")
        chapter_sets = get_all_chapter_sets(
            url=url,
            session=session,
            soup=soup,
            progress=progress,
            task_id=task_id,
        )

    with Progress(
        console=console, transient=True
    ) as progress, concurrent.futures.ThreadPoolExecutor() as executor:
        chapter_set_futures = []
        task_id = progress.add_task("Fetching chapter metadata")
        for chapter_set in chapter_sets:
            u = urljoin(url, chapter_set)
            future = executor.submit(
                _get_soup_without_special_headers, url=u, session=session
            )
            chapter_set_futures.append(future)
        for future in concurrent.futures.as_completed(chapter_set_futures):
            res = future.result()
            progress.update(
                task_id=task_id, total=len(chapters) + len(res), refresh=True
            )
            chapters.extend(get_chapters_on_page(res))
            progress.update(task_id=task_id, advance=len(res), refresh=True)

    return sorted(chapters)


def download_image(
    session: requests.Session,
    image: PageInfo,
    chapter_directory: Path,
    zero_padding: int,
):
    """Downloads an image. Updates the :param:`image` size as necessary"""
    image_response = session.get(image.url, headers=IMAGE_HEADERS, stream=True)
    if image_response.status_code == 200:
        filetype = image_response.headers.get("content-type")
        if filetype == "image/jpeg":
            suffix = ".jpg"
        elif filetype == "image/png":
            suffix = ".png"
        else:
            raise Exception(f"Unable to handle {filetype=}")
        Path(
            chapter_directory, f"{image.number:0{zero_padding}}{suffix}"
        ).write_bytes(image_response.content)
        # update the size
        image.size = len(image_response.content)
    else:
        logger.exception(f"Could not retrieve {image.url}")


def compute_comicinfo_xml(
    series_info: SeriesInfo, chapter_info: ChapterInfo, pages: List[PageInfo]
) -> etree._Element:
    comic_info = etree.Element("ComicInfo", attrib=None, nsmap=None)

    # handle the root elements
    etree_to_text_mapping = {
        "Title": chapter_info.title,
        "Series": series_info.title,
        "Number": chapter_info.data_episode_no,
        "Summary": series_info.description,
        "Year": chapter_info.date_released.year,
        "Month": chapter_info.date_released.month,
        "Day": chapter_info.date_released.day,
        "Writer": series_info.author,
        "Genre": ",".join(series_info.genre),
        "PageCount": len(pages),
        "BlackAndWhite": "No",
        "Manga": "No",
        "Web": chapter_info.content_url,
    }
    for k, v in etree_to_text_mapping.items():
        elem = etree.Element(k, attrib=None, nsmap=None)
        elem.text = str(v)
        comic_info.append(elem)

    # and then handle the pages
    page_array = etree.Element("Pages", attrib=None, nsmap=None)
    for page in pages:
        page_element = etree.Element(
            "Page",
            attrib={
                "Image": str(page.number),
                "Type": "FrontCover" if page.number == 0 else "Story",
                "ImageSize": str(page.size),
                "ImageWidth": str(page.width),
                "ImageHeight": str(page.height),
            },
            nsmap=None,
        )
        page_array.append(page_element)
    comic_info.append(page_array)

    return comic_info


def download_chapter(
    chapter: ChapterInfo,
    series_info: SeriesInfo,
    session: requests.Session,
    series_directory: Path,
    chapter_zero_padding: int,
    task_id: TaskID,
    progress: Progress,
    compress: bool = False,
):
    # create the directory for the chapter images
    chapter_directory = Path(
        series_directory, f"{chapter.data_episode_no:0{chapter_zero_padding}}"
    )
    chapter_directory.mkdir(parents=True, exist_ok=True)

    # request the viewer URL so we can get the list of images
    soup = _get_soup_without_special_headers(
        chapter.content_url, session=session
    )

    # extract images
    image_container = soup.find("div", attrs={"id": "_imageList"})
    if not image_container or not isinstance(image_container, Tag):
        raise Exception(
            f"Unable to download chapter {chapter.data_episode_no}"
        )

    images = image_container.find_all("img", recursive=False)
    # pad zeroes to filename as necessary
    zero_padding = math.ceil(math.log10(len(images))) + 1

    progress.update(
        task_id=task_id, total=len(images), visible=True, refresh=True
    )
    progress.start_task(task_id=task_id)

    pages: List[PageInfo] = [
        PageInfo(
            number=image_idx,
            width=math.ceil(float(image["width"])),
            height=math.ceil(float(image["height"])),
            url=image["data-url"],
            size=0,
        )
        for image_idx, image in enumerate(images)
    ]

    with concurrent.futures.ThreadPoolExecutor() as executor:
        for page_info in pages:
            future = executor.submit(
                download_image,
                session=session,
                image=page_info,
                chapter_directory=chapter_directory,
                zero_padding=zero_padding,
            )
            future.add_done_callback(
                lambda _: progress.update(
                    task_id=task_id, advance=1, refresh=True
                )
            )

    # compute ComicInfo.xml
    comic_info = compute_comicinfo_xml(
        series_info=series_info, chapter_info=chapter, pages=pages
    )
    Path(chapter_directory, "ComicInfo.xml").write_text(
        etree.tostring(
            comic_info, pretty_print=True, encoding=str  # type: ignore
        )
    )

    if compress:
        chapter_cbz = Path(str(chapter_directory) + ".cbz")
        with ZipFile(chapter_cbz, mode="w") as zipfile:
            for i in chapter_directory.iterdir():
                zipfile.write(i, i.name)


def series_downloader(
    url: str,
    start: Optional[int],
    end: Optional[int],
    destination: Path,
    console: Console,
    download_latest_chapter: bool = False,
    compress: bool = False,
):
    """Downloads a series"""

    session = requests.session()
    # multithreading performance should be improved here
    adapter = requests.adapters.HTTPAdapter(pool_maxsize=100)
    session.mount("https://", adapter=adapter)
    session.cookies.set("needGDPR", "FALSE", domain=".webtoons.com")
    session.cookies.set("needCCPA", "FALSE", domain=".webtoons.com")
    session.cookies.set("needCOPPA", "FALSE", domain=".webtoons.com")

    soup = _get_soup_without_special_headers(url=url, session=session)

    series_info = parse_meta_from_series(soup=soup)
    logger.debug(series_info)

    series_directory = Path(destination, series_info.title)
    series_directory.mkdir(parents=True, exist_ok=True)

    logger.info(f"Series downloading to: {series_directory}")

    chapters = get_chapters_in_series(
        url=url, session=session, soup=soup, console=console
    )

    chapter_zero_padding = math.ceil(math.log10(len(chapters))) + 1

    if download_latest_chapter:
        chapters = [chapters[-1]]
    else:
        start = start or 0
        end = end or chapters[-1].data_episode_no
        chapters = list(
            filter(
                lambda chapter: chapter.data_episode_no >= start
                and chapter.data_episode_no <= end,
                chapters,
            )
        )

    with Progress(
        *Progress.get_default_columns(),
        MofNCompleteColumn(),
        transient=True,
        console=console,
    ) as progress, concurrent.futures.ThreadPoolExecutor() as executor:
        try:
            future_to_task_id_mapping = {}
            for chapter in chapters:
                task_id = progress.add_task(
                    description=f"Download chapter {chapter.data_episode_no}",
                    start=False,
                    visible=False,
                )
                future = executor.submit(
                    download_chapter,
                    session=session,
                    series_info=series_info,
                    series_directory=series_directory,
                    chapter_zero_padding=chapter_zero_padding,
                    chapter=chapter,
                    task_id=task_id,
                    progress=progress,
                    compress=compress,
                )
                future_to_task_id_mapping[future] = task_id
            for future in concurrent.futures.as_completed(
                future_to_task_id_mapping
            ):
                task_id = future_to_task_id_mapping[future]
                progress.update(task_id=task_id, visible=False, refresh=True)
        except KeyboardInterrupt:
            logger.exception("Received SIGINT, letting work drain/complete")
            executor.shutdown(wait=True, cancel_futures=True)
