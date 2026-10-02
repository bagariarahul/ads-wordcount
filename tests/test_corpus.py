"""TextCatalog (wordcount/corpus.py): loading, lookups and counting."""

import pytest

from wordcount.corpus import TextCatalog


def test_ids_come_from_file_names_sorted(catalog):
    assert catalog.ids() == ("fable", "note")


@pytest.mark.parametrize(
    ("text_id", "word", "expected"),
    [
        ("fable", "whale", 4),  # Whale, whale!, whale_shark count; whale's does not
        ("fable", "the", 3),
        ("fable", "don't", 1),
        ("note", "whale", 1),  # "whales" is a different word
        ("fable", "absent", 0),
    ],
)
def test_count(catalog, text_id, word, expected):
    assert catalog.count(text_id, word) == expected


def test_unknown_id_lists_the_valid_ones(catalog):
    with pytest.raises(LookupError, match="available: fable, note"):
        catalog.get("moby_dick")


def test_words_are_interned(catalog):
    words = catalog.get("fable").words
    assert words[1] == words[4] == "whale"
    assert words[1] is words[4]  # one shared object per distinct word


def test_invalid_file_name_stops_loading(tmp_path):
    (tmp_path / "Moby Dick.txt").write_text("call me Ishmael", encoding="utf-8")
    with pytest.raises(ValueError, match="file names must be"):
        TextCatalog.from_directory(tmp_path)


def test_empty_or_missing_directory_stops_loading(tmp_path):
    with pytest.raises(FileNotFoundError):
        TextCatalog.from_directory(tmp_path)
    with pytest.raises(FileNotFoundError):
        TextCatalog.from_directory(tmp_path / "missing")
