"""Tests des Form-21-Layout-Extraktors (Säule 3a, 260904-rmx).

Wie Form 11 ist Form 21 ein eidgenössisch standardisiertes Formular. Die
Beträge stammen aus dem anonymisierten Korpus.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from extractors.layout_saeule3a import extract_saeule3a_layout

BREITE, HOEHE = 595.0, 842.0
ROOT = Path(__file__).resolve().parent.parent


def w(text: str, top: float, x0: float = 550.0) -> dict:
    return {"text": text, "x0": x0, "top": top, "x1": x0 + 26.0,
            "bottom": top + 9.0, "page": 1}


def label(text: str, top: float, x0: float = 100.0) -> dict:
    return {"text": text, "x0": x0, "top": top, "x1": x0 + 200.0,
            "bottom": top + 9.0, "page": 1}


def test_total_wird_ueber_die_beschriftung_gefunden():
    words = [
        label("Vertrag 2020 2045", 557.0),
        w("4'463", 557.0),
        label("Total der Beiträge an die Säule 3a", 621.0),
        w("4'463", 621.0),
    ]
    r = extract_saeule3a_layout(words, BREITE, HOEHE)
    assert r is not None
    assert r["einzahlung_betrag"]["value"] == "4'463"
    assert r["plausibel"] is True


def test_jahreszahlen_werden_nicht_als_betrag_genommen():
    """Abschluss- und Faelligkeitsjahr stehen in derselben Spalte."""
    words = [
        label("Abschluss Fälligkeit Jahr", 525.0),
        w("2023", 525.0, x0=543.0),
        label("Vertrag", 557.0),
        w("2045", 557.0, x0=448.0),
        w("2'187", 557.0),
        label("Total der Beiträge an die Säule 3a", 621.0),
        w("2'187", 621.0),
    ]
    r = extract_saeule3a_layout(words, BREITE, HOEHE)
    assert r["einzahlung_betrag"]["value"] == "2'187"


def test_mehrere_vertraege_summieren_sich():
    words = [
        label("Vertrag A", 500.0), w("3'000", 500.0),
        label("Vertrag B", 530.0), w("1'463", 530.0),
        label("Total Beiträge Säule 3a", 621.0), w("4'463", 621.0),
    ]
    r = extract_saeule3a_layout(words, BREITE, HOEHE)
    assert r["vertragssumme"] == pytest.approx(4463.0)
    assert r["plausibel"] is True


def test_unstimmige_summe_meldet_unplausibel():
    words = [
        label("Vertrag A", 500.0), w("3'000", 500.0),
        label("Total Beiträge Säule 3a", 621.0), w("4'463", 621.0),
    ]
    assert extract_saeule3a_layout(words, BREITE, HOEHE)["plausibel"] is False


def test_ohne_totalzeile_kein_ergebnis():
    """Dann bleibt der LLM-Pfad zustaendig."""
    words = [label("Vertrag", 557.0), w("4'463", 557.0)]
    assert extract_saeule3a_layout(words, BREITE, HOEHE) is None


def test_fusszeile_wird_ignoriert():
    """Formularcodes am Seitenende sehen wie Betraege aus."""
    words = [
        label("Total Beiträge Säule 3a", 621.0), w("4'463", 621.0),
        label("Eidg. Steuerverwaltung", 823.0), w("605040", 823.0),
    ]
    r = extract_saeule3a_layout(words, BREITE, HOEHE)
    assert r["einzahlung_betrag"]["value"] == "4'463"


def test_bbox_ist_der_anker():
    words = [label("Total Säule 3a", 621.0), w("4'463", 621.0)]
    r = extract_saeule3a_layout(words, BREITE, HOEHE)
    assert len(r["einzahlung_betrag"]["bbox"]) == 4
    assert r["einzahlung_betrag"]["page"] == 1


def test_leere_eingabe():
    assert extract_saeule3a_layout([], BREITE, HOEHE) is None


@pytest.mark.parametrize("stem,erwartet", [
    ("steueraufstellung-2023_-_1", "4'463"),
    ("steueraufstellung-2023_2", "2'187"),
])
def test_gegen_anonymisierte_samples(stem, erwartet):
    p = ROOT / "evals" / "samples_real_2023" / f"{stem}.json"
    if not p.exists():
        pytest.skip("Sample nicht vorhanden")
    doc = json.loads(p.read_text())
    pg = doc["pages"][0]
    words = [{**x, "page": pg["page_num"]} for x in pg["words"]]
    r = extract_saeule3a_layout(words, pg["width"], pg["height"])
    assert r is not None
    assert r["einzahlung_betrag"]["value"] == erwartet
    assert r["plausibel"] is True, "Vertragszeile muss das Total bestaetigen"
