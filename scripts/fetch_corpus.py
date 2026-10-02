"""Download the corpus (Project Gutenberg books) and strip Gutenberg's boilerplate.

Why strip: every Gutenberg file carries the same few thousand words of licence
text. Counting in it would add identical occurrences of words like "project" or
"license" to every book. Only the text between the "*** START OF ..." and
"*** END OF ..." marker lines is kept.

Why standard library only: runs on any machine with Python 3.10+, no install step,
before any container exists. Books are saved as corpus/<text_id>.txt; the file
name is the ID clients use (see wordcount/corpus.py).

Usage (from the repository root):
    python scripts/fetch_corpus.py                  # download into ./corpus
    python scripts/fetch_corpus.py --from-dir raw   # use files you downloaded by hand
Without Python on the host:
    docker run --rm -v "${PWD}:/w" -w /w python:3.12-slim python scripts/fetch_corpus.py
"""

from __future__ import annotations

import argparse
import re
import sys
import time
import urllib.request
from pathlib import Path

# text_id -> Project Gutenberg ebook number. Public domain in the US. The sizes
# differ on purpose (from ~27k to ~570k words), so counting cost varies per request.
BOOKS = {
    "alice_in_wonderland": 11,
    "frankenstein": 84,
    "tale_of_two_cities": 98,
    "dracula": 345,
    "pride_and_prejudice": 1342,
    "sherlock_holmes": 1661,
    "moby_dick": 2701,
    "war_and_peace": 2600,
}
URL_PATTERNS = (
    "https://www.gutenberg.org/cache/epub/{n}/pg{n}.txt",
    "https://www.gutenberg.org/files/{n}/{n}-0.txt",
)
_START = re.compile(
    r"^\*\*\* ?START OF (?:THE|THIS) PROJECT GUTENBERG EBOOK.*$", re.MULTILINE | re.IGNORECASE
)
_END = re.compile(r"^\*\*\* ?END OF (?:THE|THIS) PROJECT GUTENBERG EBOOK.*$", re.MULTILINE | re.IGNORECASE)


def strip_boilerplate(raw: str) -> str:
    """Return only the book text between Gutenberg's START and END marker lines."""
    start, end = _START.search(raw), _END.search(raw)
    if not start or not end or end.start() <= start.end():
        raise ValueError("Project Gutenberg START/END markers not found")
    return raw[start.end() : end.start()].strip() + "\n"


def download(number: int) -> str:
    """Fetch one book as text, trying Gutenberg's two usual plain-text URLs."""
    last_error: Exception | None = None
    for pattern in URL_PATTERNS:
        request = urllib.request.Request(pattern.format(n=number), headers={"User-Agent": "tue-ads-lab/1.0"})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.read().decode("utf-8-sig")
        except OSError as exc:  # URLError and HTTPError are OSErrors
            last_error = exc
    raise OSError(f"could not download ebook {number}: {last_error}")


def read_local(directory: Path, number: int) -> str:
    """Find a hand-downloaded copy of ebook `number` in `directory`."""
    for name in (f"pg{number}.txt", f"{number}-0.txt", f"{number}.txt"):
        if (directory / name).is_file():
            return (directory / name).read_text(encoding="utf-8-sig")
    raise FileNotFoundError(f"no pg{number}.txt in {directory}")


def main(argv: list[str] | None = None) -> int:
    """Fetch (or read) every book, strip it, save it as corpus/<text_id>.txt; exit 1 if any failed."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=Path("corpus"))
    parser.add_argument("--from-dir", type=Path, help="read pg<N>.txt files from here instead of downloading")
    args = parser.parse_args(argv)

    args.out.mkdir(parents=True, exist_ok=True)
    failed = 0
    for i, (text_id, number) in enumerate(BOOKS.items()):
        try:
            if args.from_dir:
                raw = read_local(args.from_dir, number)
            else:
                if i:
                    time.sleep(2)  # be polite to Project Gutenberg's servers
                raw = download(number)
            text = strip_boilerplate(raw)
        except (OSError, ValueError) as exc:
            print(f"FAILED {text_id}: {exc}", file=sys.stderr)
            failed += 1
            continue
        (args.out / f"{text_id}.txt").write_text(text, encoding="utf-8")
        print(f"{text_id:<22} ebook {number:<5} {len(text) / 1e6:5.2f} MB")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
