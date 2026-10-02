"""The definition of a word (wordcount/text.py), rule by rule."""

import pytest

from wordcount.text import MAX_KEYWORD_LENGTH, normalize_keyword, tokenize


def test_tokenize_applies_every_rule():
    text = (
        "The whale met the Whale. THE whale's friend said: don\u2019t go, whale! Sea-captain whale_shark 42"
    )
    assert tokenize(text) == [
        "the",
        "whale",
        "met",
        "the",
        "whale",
        "the",
        "whale's",
        "friend",
        "said",
        "don't",
        "go",
        "whale",
        "sea",
        "captain",
        "whale",
        "shark",
        "42",
    ]


def test_case_folding_is_unicode_aware():
    assert tokenize("STRASSE Straße") == ["strasse", "strasse"]


def test_accented_letters_stay_inside_words():
    assert tokenize("café crème") == ["café", "crème"]


@pytest.mark.parametrize("raw", ["whale", "Whale", " WHALE ", "whale!", "(whale)"])
def test_keyword_variants_normalize_to_the_same_word(raw):
    assert normalize_keyword(raw) == "whale"


def test_curly_and_straight_apostrophes_are_the_same_keyword():
    assert normalize_keyword("don\u2019t") == normalize_keyword("don't") == "don't"


@pytest.mark.parametrize("raw", ["new york", "", "   ", "!!!", "sea-captain"])
def test_keyword_must_be_exactly_one_word(raw):
    with pytest.raises(ValueError, match="exactly one word"):
        normalize_keyword(raw)


def test_overlong_keyword_is_rejected():
    with pytest.raises(ValueError, match="longer than"):
        normalize_keyword("a" * (MAX_KEYWORD_LENGTH + 1))
