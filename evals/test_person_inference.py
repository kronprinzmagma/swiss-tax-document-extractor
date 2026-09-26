"""Unit-Tests für ``extractors.person_inference`` — Plan 02-02 Wave 1 Task 2.

Behavior-Tests gegen die Test-Fixture ``evals/family.yaml.test`` (Hans/Maria/
Lina/Tim Muster). Deckt D-B1..B5 aus ``02-CONTEXT.md`` ab:

* Exact-Match auf Voll-Name nach :func:`extractors.numbers.normalize`.
* 0/1/2+ Matches → None / role / ``"gemeinsam"``.
* Anker zeigt auf BBox des Vornamens (erste Tag-ID des Match-Spans, D-B3).
* Nur-Nachname-Treffer → None (D-B4 — lieber underspezifiziert als falsch).
* Family=None → None (Person-Inferenz disabled).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from extractors.family import load_family
from extractors.person_inference import PersonResult, infer_person
from extractors.tokenize import TaggedWord


FIXTURE = Path(__file__).parent / "family.yaml.test"


def _words(*texts: str) -> list[TaggedWord]:
    """Hilfsfunktion: erzeugt TaggedWords mit fortlaufenden tag_ids und Dummy-BBox."""
    return [
        TaggedWord(tag_id=i, page=1, bbox=(0.0, float(i), 10.0, float(i) + 5.0), text=t)
        for i, t in enumerate(texts)
    ]


@pytest.fixture(scope="module")
def family():
    fam = load_family(FIXTURE)
    assert fam is not None
    return fam


def test_family_none_returns_no_match():
    """family=None → role=None (Person-Inferenz disabled)."""
    res = infer_person(_words("Hans", "Muster", "AG"), None)
    assert res.role is None
    assert res.matched_members == []
    assert res.anchor_tag_id is None


def test_zero_matches_returns_none(family):
    """Kein Familienmitglied im Text → role=None."""
    res = infer_person(_words("Peter", "Schmid", "AG"), family)
    assert res.role is None
    assert res.matched_members == []


def test_mann_match_anchor_on_first_name(family):
    """Voll-Name Hans Muster im Text → role='mann', Anker auf 'Hans'."""
    words = _words("Lohnausweis", "Hans", "Muster", "Strasse", "1")
    res = infer_person(words, family)
    assert res.role == "mann"
    assert res.matched_members == ["mann"]
    # Anker zeigt auf "Hans" (tag_id=1)
    assert res.anchor_tag_id == 1


def test_frau_match(family):
    res = infer_person(_words("Versicherte", "Maria", "Muster"), family)
    assert res.role == "frau"
    assert res.matched_members == ["frau"]
    assert res.anchor_tag_id == 1


def test_kind1_match(family):
    res = infer_person(_words("Kita", "Lina", "Muster", "2024"), family)
    assert res.role == "kind1"
    assert res.matched_members == ["kind1"]


def test_kind2_match(family):
    res = infer_person(_words("Hort", "Tim", "Muster"), family)
    assert res.role == "kind2"
    assert res.matched_members == ["kind2"]


def test_joint_match_returns_gemeinsam(family):
    """Mann + Frau im Text (Joint-Account) → role='gemeinsam' (D-B2)."""
    words = _words("Konto", "Hans", "Muster", "und", "Maria", "Muster", "Saldo")
    res = infer_person(words, family)
    assert res.role == "gemeinsam"
    assert set(res.matched_members) == {"mann", "frau"}


def test_only_last_name_returns_none(family):
    """Nur Nachname 'Muster' (ohne Vorname) → role=None (D-B4)."""
    res = infer_person(_words("Familie", "Muster", "Strasse"), family)
    assert res.role is None
    assert res.matched_members == []


def test_diakritika_robustness(family):
    """Diakritische Variante 'Hâns Müster' matched gegen 'Hans Muster' via normalize."""
    res = infer_person(_words("Hâns", "Müster"), family)
    assert res.role == "mann"


# Recall-Test: parametrisierte Cases-Liste mit ≥10 Inputs, ≥90% korrekt.
RECALL_CASES = [
    # (description, words_text, expected_role)
    ("mann_solo", ["Hans", "Muster", "Bahnhofstrasse"], "mann"),
    ("frau_solo", ["Maria", "Muster", "AHV"], "frau"),
    ("kind1_solo", ["Lina", "Muster", "Kita"], "kind1"),
    ("kind2_solo", ["Tim", "Muster", "Hort"], "kind2"),
    ("joint_eltern", ["Hans", "Muster", "und", "Maria", "Muster"], "gemeinsam"),
    ("only_lastname", ["Familie", "Muster"], None),
    ("no_match", ["Peter", "Schmid"], None),
    ("mann_in_long_text", ["Lohnausweis", "2024", "Arbeitnehmer", "Hans", "Muster", "Position", "8"], "mann"),
    ("frau_diakritika", ["Mária", "Muster"], "frau"),
    ("kind1_apostrophe", ["L'ina", "Muster"], "kind1"),  # normalize entfernt Apostroph
    ("joint_three_could_match", ["Hans", "Muster", "Maria", "Muster", "Lina", "Muster"], "gemeinsam"),
]


@pytest.mark.parametrize("desc,text,expected", RECALL_CASES, ids=[c[0] for c in RECALL_CASES])
def test_recall_individual_case(family, desc, text, expected):
    """Einzelfall des Recall-Sets — informativ, nicht hart-gated."""
    res = infer_person(_words(*text), family)
    assert res.role == expected, f"Case {desc!r}: erwartet {expected}, erhielt {res.role}"


def test_family_attribution_recall_at_least_90_percent(family):
    """Family-Attribution-Recall ≥ 90% (ROADMAP-Erfolgskriterium 4)."""
    correct = 0
    total = len(RECALL_CASES)
    for _desc, text, expected in RECALL_CASES:
        res = infer_person(_words(*text), family)
        if res.role == expected:
            correct += 1
    recall = correct / total
    assert recall >= 0.9, f"Recall {recall:.0%} < 90% ({correct}/{total})"


def test_person_result_is_frozen():
    """PersonResult ist frozen (kein versehentliches Mutieren)."""
    r = PersonResult(role=None, anchor_tag_id=None, matched_members=[])
    with pytest.raises((AttributeError, TypeError)):
        r.role = "mann"  # type: ignore[misc]
