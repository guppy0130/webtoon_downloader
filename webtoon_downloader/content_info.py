from dataclasses import dataclass
from datetime import datetime
from typing import List
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse


@dataclass(eq=True, repr=True)
class SeriesInfo:
    title: str
    description: str
    image: str
    url: str
    author: str
    genre: List[str]

    def __post_init__(self):
        # strip out the ?type=cropNNN if it's present in the image URL
        u = urlparse(self.image)
        q = parse_qs(u.query, keep_blank_values=True)
        if "type" in q:
            q["type"] = list(filter(lambda v: "crop" not in v, q["type"]))
        u = u._replace(query=urlencode(q, True))
        self.image = urlunparse(u)


@dataclass(eq=True, repr=True)
class ChapterInfo:
    #: chapter title
    title: str
    #: chapter number referenced by webtoon server
    data_episode_no: int
    #: chapter release date
    date_released: datetime
    #: viewer URL
    content_url: str

    def __lt__(self, o: object) -> bool:
        if not isinstance(o, ChapterInfo):
            raise TypeError("Must compare with ChapterInfo")
        return self.data_episode_no < o.data_episode_no


@dataclass(eq=True, repr=True)
class PageInfo:
    number: int
    width: int
    height: int
    url: str
    size: int

    def __lt__(self, o: object) -> bool:
        if not isinstance(o, PageInfo):
            raise TypeError("Must compare with PageInfo")
        return self.number < o.number
