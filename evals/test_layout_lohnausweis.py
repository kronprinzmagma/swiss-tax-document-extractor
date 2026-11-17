"""Tests für den deterministischen Form-11-Layout-Extraktor.

Die synthetischen Wortlisten bilden die Geometrie echter Lohnausweise nach
(Betragsspalte bei x0 ≈ 540 auf einer 595×842-Seite). Die verwendeten Beträge
sind synthetisch, aber arithmetisch konsistent (Brutto − Abzüge = Netto).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from extractors.layout_lohnausweis import extract_lohnausweis_layout

BREITE, HOEHE = 595.0, 842.0
ROOT = Path(__file__).resolve().parent.parent


def wort(text: str, top: float, x0: float = 541.0) -> dict:
    """Wortbox in der Betragsspalte."""
    return {"text": text, "x0": x0, "top": top, "x1": x0 + 21.0,
            "bottom": top + 10.0, "page": 1}


def test_standardlayout_brutto_und_netto():
    """Grösster Wert ist Brutto, zweitgrösster Netto — Abzüge dazwischen."""
    words = [
        wort("75600", 470.5),   # Ziffer 8
        wort("4008", 483.7),    # Ziffer 9
        wort("3100", 502.4),    # Ziffer 10.1
        wort("68492", 533.4),   # Ziffer 11
        wort("1200", 625.8),    # Ziffer 13.2.1 Spesen — unterhalb, kein Abzug
    ]
    r = extract_lohnausweis_layout(words, BREITE, HOEHE)
    assert r is not None
    assert r["bruttolohn_pos8"]["value"] == "75600"
    assert r["nettolohn_pos11"]["value"] == "68492"
    assert r["plausibel"] is True
    assert r["abzuege_summe"] == pytest.approx(7108.0)


def test_spesen_unterhalb_netto_zaehlen_nicht_als_abzug():
    """Ohne die Zeilenbedingung würden die Spesen die Gegenprobe kippen."""
    words = [wort("75600", 470.5), wort("4008", 483.7), wort("3100", 502.4),
             wort("68492", 533.4), wort("1200", 625.8)]
    r = extract_lohnausweis_layout(words, BREITE, HOEHE)
    assert r["abzuege_summe"] == pytest.approx(7108.0)   # ohne die 1200


def test_ziffer_1_gleich_ziffer_8_nimmt_die_summenzeile():
    """Trägt Ziffer 1 denselben Betrag wie Ziffer 8, gilt die untere Zeile."""
    words = [
        wort("24500", 269.0),   # Ziffer 1 Lohn
        wort("24500", 437.0),   # Ziffer 8 Bruttolohn (Summenzeile)
        wort("1650", 450.0),    # Ziffer 9
        wort("1350", 469.0),    # Ziffer 10.1
        wort("21500", 502.0),   # Ziffer 11
    ]
    r = extract_lohnausweis_layout(words, BREITE, HOEHE)
    assert r["bruttolohn_pos8"]["value"] == "24500"
    assert r["bruttolohn_pos8"]["bbox"][1] == pytest.approx(437.0)
    assert r["nettolohn_pos11"]["value"] == "21500"
    assert r["plausibel"] is True


def test_kopfband_wird_ignoriert():
    """Formularnummern im Kopfband sehen wie Grossbeträge aus."""
    words = [
        wort("391697", 22.0),   # Formularnummer — darf nicht Brutto werden
        wort("7200", 448.7),
        wort("6300", 510.8),
    ]
    r = extract_lohnausweis_layout(words, BREITE, HOEHE)
    assert r["bruttolohn_pos8"]["value"] == "7200"
    assert r["nettolohn_pos11"]["value"] == "6300"


def test_fehlende_abzuege_melden_unplausibel():
    """Zerlegt die OCR die Abzüge, sagt die Gegenprobe ehrlich 'nicht belegt'."""
    words = [wort("7200", 448.7), wort("6300", 510.8), wort("1775", 660.9)]
    r = extract_lohnausweis_layout(words, BREITE, HOEHE)
    assert r["plausibel"] is False


def test_linke_spalte_wird_ignoriert():
    """Beträge ausserhalb der Betragsspalte sind Fliesstext-Zahlen."""
    words = [wort("99999", 300.0, x0=60.0), wort("7200", 448.7), wort("6300", 510.8)]
    r = extract_lohnausweis_layout(words, BREITE, HOEHE)
    assert r["bruttolohn_pos8"]["value"] == "7200"


def test_kleinbetraege_werden_ignoriert():
    words = [wort("5", 440.0), wort("7200", 448.7), wort("6300", 510.8)]
    r = extract_lohnausweis_layout(words, BREITE, HOEHE)
    assert r["bruttolohn_pos8"]["value"] == "7200"


def test_zu_wenige_werte_gibt_none():
    """Bei einer einzigen Zahl bleibt der LLM-Pfad zuständig."""
    assert extract_lohnausweis_layout([wort("7200", 448.7)], BREITE, HOEHE) is None
    assert extract_lohnausweis_layout([], BREITE, HOEHE) is None


def test_seitenmasse_werden_geschaetzt():
    """Ohne explizite Seitenmasse aus den Boxen ableiten."""
    words = [wort("75600", 470.5), wort("4008", 483.7),
             wort("3100", 502.4), wort("68492", 533.4)]
    r = extract_lohnausweis_layout(words)
    assert r is not None
    assert r["nettolohn_pos11"]["value"] == "68492"


def test_bbox_ist_der_anker():
    """Jeder Wert trägt seine Box — der Nachweis kommt gratis mit."""
    words = [wort("7200", 448.7), wort("6300", 510.8)]
    r = extract_lohnausweis_layout(words, BREITE, HOEHE)
    for feld in ("bruttolohn_pos8", "nettolohn_pos11"):
        assert len(r[feld]["bbox"]) == 4
        assert r[feld]["page"] == 1
        assert r[feld]["snippet"]


# Sample-Erwartungen (Stem -> [Brutto, Netto]) liegen NEBEN dem anonymisierten
# Korpus im gitignorten Ordner — sie sind echte Belegwerte und gehoeren nicht
# in den getrackten Test.
_ERWARTET_PFAD = ROOT / "evals" / "samples_real_2023" / "_erwartet_lohnausweis.json"


def test_gegen_anonymisierte_samples():
    """Integrationstest gegen den anonymisierten Korpus (falls vorhanden).

    Die Erwartungswerte stammen wortwörtlich aus den Belegen und liegen deshalb
    zusammen mit dem Korpus ausserhalb der Versionskontrolle. Ohne die Datei
    wird der Test uebersprungen, nicht bestanden.
    """
    if not _ERWARTET_PFAD.exists():
        pytest.skip("Sample-Erwartungen nicht vorhanden (gitignored)")
    erwartungen = json.loads(_ERWARTET_PFAD.read_text(encoding="utf-8"))
    assert erwartungen, "Erwartungsdatei ist leer"
    for stem, erwartet in erwartungen.items():
        p = ROOT / "evals" / "samples_real_2023" / f"{stem}.json"
        if not p.exists():
            pytest.skip(f"{stem} nicht vorhanden (Samples sind gitignored)")
        doc = json.loads(p.read_text())
        pg = doc["pages"][0]
        words = [{**w, "page": pg["page_num"]} for w in pg["words"]]
        r = extract_lohnausweis_layout(words, pg["width"], pg["height"])
        assert r is not None, stem
        assert (r["bruttolohn_pos8"]["value"], r["nettolohn_pos11"]["value"]) \
            == tuple(erwartet), stem


# ── Feld E: Anstellung von – bis (261024-jga) ─────────────────────────────

from extractors.layout_lohnausweis import extract_lohnausweis_periode  # noqa: E402


def kopf(text: str, x: float, y: float) -> dict:
    """Wortbox im Kopf, Lage als Anteil der Seite."""
    return wort(text, y * HOEHE, x0=x * BREITE)


def test_periode_links_von_rechts_bis():
    words = [
        kopf("12.03.1984", 0.33, 0.07),   # Geburtsdatum — frueheres Jahr
        kopf("01.02.2025", 0.28, 0.12),
        kopf("31.12.2025", 0.40, 0.12),
        kopf("15.01.2026", 0.19, 0.92),   # Unterschrift — am Fuss
    ]
    r = extract_lohnausweis_periode(words, BREITE, HOEHE)
    assert r["periode_von"]["value"] == "01.02.2025"
    assert r["periode_bis"]["value"] == "31.12.2025"


def test_periode_zerlegtes_datum_bleibt_leer():
    """Die OCR hat „von" zerlegt: dann nur „bis", nichts geraten."""
    words = [kopf("1.00", 0.28, 0.09), kopf("208", 0.31, 0.09),
             kopf("15.12.2025", 0.40, 0.09)]
    r = extract_lohnausweis_periode(words, BREITE, HOEHE)
    assert r["periode_von"] is None
    assert r["periode_bis"]["value"] == "15.12.2025"


def test_periode_verkehrt_herum_ist_keine_periode():
    words = [kopf("31.12.2025", 0.28, 0.1), kopf("01.01.2025", 0.40, 0.1)]
    r = extract_lohnausweis_periode(words, BREITE, HOEHE)
    assert r == {"periode_von": None, "periode_bis": None}


def test_periode_auf_den_anonymisierten_belegen():
    """Gegen die drei anonymisierten Lohnausweise, Werte von Hand abgelesen.

    Ohne Dateinamen — die nennen Arbeitgeber und bleiben im lokalen Korpus.
    """
    pfade = sorted((ROOT / "evals" / "samples_real_2023").glob("Lohnausweis*.json"))
    if len(pfade) != 3:
        pytest.skip("Korpus fehlt")
    gefunden = set()
    for pfad in pfade:
        seite = json.loads(pfad.read_text(encoding="utf-8"))["pages"][0]
        r = extract_lohnausweis_periode(
            [dict(w, page=1) for w in seite["words"]],
            seite["width"], seite["height"])
        gefunden.add(tuple((r[k] or {}).get("value")
                           for k in ("periode_von", "periode_bis")))
    assert gefunden == {("01.02.2023", "31.12.2023"),
                        ("01.01.2023", "31.01.2023"),
                        (None, "15.12.2023")}


def test_abzuege_in_fester_reihenfolge_und_ganze_franken():
    """Ziffer 9 vor 10.1; Gegenprobe mit ganzen Franken (±2) (261024-jga)."""
    words = [wort("60000", 470), wort("3500", 483), wort("3000", 502),
             wort("53501", 533)]
    r = extract_lohnausweis_layout(words, BREITE, HOEHE)
    assert r["ahv_alv_nbu_abzug_pos9"]["value"] == "3500"
    assert r["bvg_abzug_pos10a"]["value"] == "3000"
    assert r["plausibel"] is True


def test_periode_mit_ocr_leerzeichen():
    w = [kopf("01", 0.28, 0.12), kopf(".01", 0.295, 0.12), kopf(".2025", 0.31, 0.12),
         kopf("30.06.2025", 0.40, 0.12)]
    for i, x in enumerate(w[:3]):
        x["x1"] = x["x0"] + 8
    r = extract_lohnausweis_periode(w, BREITE, HOEHE)
    assert r["periode_von"]["value"] == "01.01.2025"
    assert r["periode_bis"]["value"] == "30.06.2025"
