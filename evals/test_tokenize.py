"""Unit-Tests für extractors/tokenize.py — Tag-Format-Roundtrip und tag_map-Konsistenz."""
import re

from extractors.tokenize import TaggedWord, tokenize


def test_tokenize_basic():
    words = [
        {"page": 1, "x0": 10, "top": 20, "x1": 30, "bottom": 35, "text": "Lohnausweis"},
        {"page": 1, "x0": 35, "top": 20, "x1": 50, "bottom": 35, "text": "Form"},
    ]
    text, tag_map = tokenize(words)
    assert "[T0] Lohnausweis" in text
    assert "[T1] Form" in text
    assert tag_map[0].text == "Lohnausweis"
    assert tag_map[1].text == "Form"
    assert tag_map[0].page == 1
    assert tag_map[0].bbox == (10, 20, 30, 35)
    assert tag_map[1].tag_id == 1


def test_tokenize_multipage():
    words = [
        {"page": 1, "x0": 10, "top": 20, "x1": 30, "bottom": 35, "text": "Seite1"},
        {"page": 2, "x0": 10, "top": 20, "x1": 30, "bottom": 35, "text": "Seite2"},
    ]
    _, tag_map = tokenize(words)
    assert tag_map[0].page == 1
    assert tag_map[1].page == 2


def test_tokenize_empty():
    text, tag_map = tokenize([])
    assert text == ""
    assert tag_map == {}


def test_tokenize_tag_id_monotonic():
    words = [
        {"page": 1, "x0": 0, "top": 0, "x1": 1, "bottom": 1, "text": f"w{i}"}
        for i in range(5)
    ]
    _, tag_map = tokenize(words)
    assert sorted(tag_map.keys()) == [0, 1, 2, 3, 4]


def test_tokenize_re_parsable():
    """Aus dem zurückgegebenen String müssen Tag-IDs per Regex extrahierbar sein."""
    words = [
        {"page": 1, "x0": 0, "top": 0, "x1": 1, "bottom": 1, "text": "ACME"},
        {"page": 1, "x0": 2, "top": 0, "x1": 3, "bottom": 1, "text": "AG"},
    ]
    text, tag_map = tokenize(words)
    matches = re.findall(r"\[T(\d+)\] (\S+)", text)
    assert [(int(i), w) for i, w in matches] == [(0, "ACME"), (1, "AG")]


def test_tokenize_strips_whitespace_in_text():
    """Wörter mit nur-Whitespace werden übersprungen, aber tag_id-Counter läuft."""
    words = [
        {"page": 1, "x0": 0, "top": 0, "x1": 1, "bottom": 1, "text": "  ACME  "},
    ]
    text, tag_map = tokenize(words)
    assert tag_map[0].text == "ACME"
    assert "[T0] ACME" in text


def test_tagged_word_is_frozen():
    tw = TaggedWord(tag_id=0, page=1, bbox=(0.0, 0.0, 1.0, 1.0), text="x")
    try:
        tw.text = "y"  # type: ignore[misc]
    except (AttributeError, Exception):
        return
    raise AssertionError("TaggedWord must be frozen")
