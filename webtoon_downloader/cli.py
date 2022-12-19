import logging
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.logging import RichHandler
from typer import BadParameter, Context, Option, Typer

from webtoon_downloader.utils import pop_query_param, series_downloader

app = Typer()


@app.callback(context_settings={"obj": {}})
def callback(
    ctx: Context,
    verbose: int = Option(  # noqa: B008
        1, "-v", "--verbose", min=1, max=4, clamp=True, count=True
    ),
):
    console = Console(stderr=True)
    handler = RichHandler(console=console)
    logging.basicConfig(
        level=50 - verbose * 10, force=True, handlers=[handler]
    )
    # pass the console around so we can reuse it
    ctx.obj["console"] = console


def _ensure_directory(path: Path) -> Path:
    """Ensures :param:`path` exists and is a directory

    Args:
        path (Path): Path to ensure is a directory

    Returns:
        Path: Fully resolved :param:`path`, including user expansion
    """
    path = path.resolve().expanduser()
    path.mkdir(parents=True, exist_ok=True)
    return path


@app.command()
def download(
    ctx: Context,
    url: str = Option(..., help="URL to webtoon to download"),  # noqa: B008
    destination: Path = Option(  # noqa: B008
        Path(".").resolve().expanduser(),  # noqa: B008
        "-d",
        "--dest",
        "--destination",
        help="Parent folder for downloads",
        callback=_ensure_directory,
    ),
    start: int = Option(  # noqa: B008
        None, help="Chapter to start downloading from"
    ),
    end: Optional[int] = Option(  # noqa: B008
        None, help="Last chapter to download"
    ),
    latest: bool = Option(  # noqa: B008
        False, help="Only download latest chapter"
    ),
    compress: bool = Option(  # noqa: B008
        True, help="Compress chapters to .cbz after downloading"
    ),
):
    if latest and (start is not None or end is not None):
        raise BadParameter(
            f"({start=} or {end=}) and {latest=} are mutually exclusive"
        )

    if end is not None and end < start:
        raise BadParameter(f"{end=} should not be less than {start=}")

    url = pop_query_param(url, key="page")

    series_downloader(
        url=url,
        destination=destination,
        start=start,
        end=end,
        console=ctx.obj["console"],
        download_latest_chapter=latest,
        compress=compress,
    )


if __name__ == "__main__":
    app()
