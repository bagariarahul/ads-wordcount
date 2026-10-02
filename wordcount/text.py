"""What counts as a word: the one definition used everywhere in this code base.

The assignment asks for "the number of occurrences of a keyword" but never defines
a word. Without one fixed rule, "Whale", "whale," and "WHALE" could be counted
differently by the server, the trace generator and the tests, and the counts
could not be verified. This module is that rule; nothing else tokenizes text.

The rule (also stated in the report, Sec. II):
  * matching is case-insensitive, using Unicode case folding ("STRASSE" == "straße");
  * a word is a run of letters and/or digits, optionally joined by apostrophes, so
    "don't" and "whale's" are single words ("whale's" does NOT count as "whale");
  * curly apostrophes are treated like ASCII ones, because Project Gutenberg's
    UTF-8 books use both;
  * everything else separates words: spaces, punctuation, underscores and dashes
    ("sea-captain" is two words).

Used by: corpus.TextCatalog (tokenizes each text once at startup),
server.WordCountService (normalizes the requested keyword),
bench.trace (keyword pool and expected counts), tests/test_text.py.
"""

from __future__ import annotations

import re

# Typographic apostrophes found in Gutenberg texts -> ASCII apostrophe.
_APOSTROPHES = str.maketrans({"\u2019": "'", "\u2018": "'", "\u02bc": "'"})

# [^\W_] is "any Unicode letter or digit": \w without the underscore.
_WORD_RE = re.compile(r"[^\W_]+(?:'[^\W_]+)*")

# Longest keyword we accept. English words rarely exceed ~30 letters; the cap stops a
# client from sending megabytes as a "keyword" and keeps Redis keys short.
MAX_KEYWORD_LENGTH = 64


def normalize(text: str) -> str:
    """Unify apostrophes and case-fold. First step of both tokenize() and normalize_keyword().

    Why a separate function: texts and keywords must be normalized identically,
    otherwise a keyword could never match its own occurrences.
    """
    return text.translate(_APOSTROPHES).casefold()


def tokenize(text: str) -> list[str]:
    """Split `text` into normalized words, in order of appearance.

    Used by corpus.TextCatalog.from_directory() once per text at startup, and by
    normalize_keyword() so a keyword is parsed by the very same rule as the texts.
    """
    return _WORD_RE.findall(normalize(text))


def normalize_keyword(keyword: str) -> str:
    """Return the canonical form of a requested keyword, or raise ValueError.

    "Whale", "whale!" and " WHALE " all become "whale". A request must name exactly
    one word; "new york" or "!!!" are rejected rather than silently answered with 0,
    so a malformed request is distinguishable from a word that does not occur.

    Raises ValueError (a built-in type) on purpose: RPyC re-creates built-in
    exceptions on the client, so the caller can `except ValueError` normally.
    """
    if len(keyword) > MAX_KEYWORD_LENGTH:
        raise ValueError(f"keyword longer than {MAX_KEYWORD_LENGTH} characters")
    words = tokenize(keyword)
    if len(words) != 1:
        raise ValueError(f"keyword must be exactly one word, got {keyword!r}")
    return words[0]
