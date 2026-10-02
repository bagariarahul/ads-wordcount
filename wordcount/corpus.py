"""The texts the service counts in, loaded once when a server starts.

Why text IDs instead of file names or paths: the assignment has clients send "a
reference to a text stored on the server". That reference is an ID, the file name
without ".txt" (e.g. "moby_dick"). The server only looks IDs up in a dictionary
built at startup, so it never opens a file whose name came from a client.

Why tokenize at startup: every text is split into words once (text.tokenize) and
kept in memory as a tuple. Counting is then a single scan in C (tuple.count)
instead of reading and re-tokenizing a multi-megabyte file on every request.
Words are interned (sys.intern) so each distinct word is stored once; for a novel
that shrinks the tuple's strings by roughly an order of magnitude.

Deliberately NOT done: precomputing a word -> count table per text. That would
make every count O(1) and leave the Redis cache, which the assignment requires,
with no work to save. The per-request scan is the cost the cache amortizes.

Used by: server.main() (builds the catalog), server.WordCountService (lookups and
counts), bench.trace (vocabulary and expected counts), tests/test_corpus.py.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from wordcount.text import tokenize

# Text IDs double as file names: lowercase letters, digits, '_' and '-', max 64 chars.
TEXT_ID_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")


@dataclass(frozen=True)
class Text:
    """One loaded text: its ID and its words in reading order."""

    text_id: str
    words: tuple[str, ...]


class TextCatalog:
    """Read-only mapping from text ID to Text. Safe to share between server threads:
    nothing in it changes after construction."""

    def __init__(self, texts: Mapping[str, Text]) -> None:
        if not texts:
            raise ValueError("a catalog needs at least one text")
        self._texts = dict(texts)

    @classmethod
    def from_directory(cls, directory: Path) -> TextCatalog:
        """Load every *.txt file in `directory`; the file name (minus .txt) becomes its ID.

        Fails at startup, not at request time, if the directory is missing, empty,
        or holds a file whose name is not a valid ID: a server that cannot serve its
        corpus should not start at all.
        """
        if not directory.is_dir():
            raise FileNotFoundError(f"corpus directory {directory} does not exist")
        texts: dict[str, Text] = {}
        for path in sorted(directory.glob("*.txt")):
            text_id = path.stem
            if not TEXT_ID_RE.fullmatch(text_id):
                raise ValueError(f"{path.name}: file names must be lowercase letters, digits, '_' or '-'")
            words = tuple(sys.intern(w) for w in tokenize(path.read_text(encoding="utf-8")))
            texts[text_id] = Text(text_id, words)
        if not texts:
            raise FileNotFoundError(f"no *.txt files in {directory}")
        return cls(texts)

    def ids(self) -> tuple[str, ...]:
        """All text IDs, sorted. A tuple so it can be returned over RPyC by value."""
        return tuple(sorted(self._texts))

    def get(self, text_id: str) -> Text:
        """Return the text with this ID, or raise LookupError listing the valid IDs.

        LookupError is built in, so RPyC re-raises it as LookupError on the client.
        """
        try:
            return self._texts[text_id]
        except KeyError:
            # [:80]: never echo an arbitrarily long client string back in full.
            raise LookupError(
                f"unknown text id {text_id[:80]!r}; available: {', '.join(self.ids())}"
            ) from None

    def count(self, text_id: str, word: str) -> int:
        """Occurrences of `word` in the text, by a full scan.

        `word` must already be normalized (text.normalize_keyword); this method does
        not normalize, so the server validates input exactly once.
        """
        return self.get(text_id).words.count(word)

    def total_words(self) -> int:
        """Number of words across all texts; logged at server startup."""
        return sum(len(t.words) for t in self._texts.values())
