# webtoon_downloader

## Usage

```bash
pip install -e '.[dev]'
webtoon-downloader --help
```

## Comparison

Upstream: <https://github.com/Zehina/Webtoon-Downloader>

Additional features:

* `ComicInfo.xml` generation per chapter
* `--compress` to `.cbz` per chapter
* image type auto-detection based on `Content-Type` response
* `typer` for CLI
* linting (black, flake8, isort)

Removed:

* `--separate` option, always download into chapter folders
* `--image-type` unless there's a need for converting images
