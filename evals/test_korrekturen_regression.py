"""Tests des Regressionsschutzes durch bestätigte Werte (260904-rmx)."""
from __future__ import annotations

import json

from extractors.korrekturen import Abgleich, bericht, lade, vergleiche


def k(soll=None, label=None) -> dict:
    e: dict = {"zielwert": "Selbstkosten", "ziffer": "22.1"}
    if soll is not None:
        e["soll"] = soll
    if label is not None:
        e["label"] = label
    return e


def zeile(beleg, ziel, betrag) -> dict:
    return {"pdf_name": beleg, "beschreibung": ziel, "betrag": betrag}


# --- Laden ------------------------------------------------------------------

def test_fehlende_datei_ergibt_leer(tmp_path):
    assert lade(tmp_path / "gibtsnicht.json") == {}


def test_kaputte_datei_bricht_nicht(tmp_path):
    p = tmp_path / "k.json"
    p.write_text("{kein json")
    assert lade(p) == {}


def test_laden_liefert_eintraege(tmp_path):
    p = tmp_path / "k.json"
    p.write_text(json.dumps({"a.pdf|Selbstkosten": k("732.75")}))
    assert "a.pdf|Selbstkosten" in lade(p)


# --- Vergleich --------------------------------------------------------------

def test_uebereinstimmung():
    a = vergleiche({"a.pdf|Selbstkosten": k("732.75")},
                   [zeile("a.pdf", "Selbstkosten", "732.75")])
    assert a == [Abgleich("a.pdf", "Selbstkosten", "stimmt")]


def test_abweichung_wird_erkannt():
    """Der Fall, der monatelang unbemerkt blieb: Brutto statt Selbstanteil."""
    a = vergleiche({"a.pdf|Selbstkosten": k("732.75")},
                   [zeile("a.pdf", "Selbstkosten", "3980.25")])
    assert a[0].zustand == "weicht_ab"


def test_fehlender_wert_wird_erkannt():
    a = vergleiche({"a.pdf|Selbstkosten": k("732.75")},
                   [zeile("a.pdf", "Selbstkosten", "")])
    assert a[0].zustand == "fehlt"


def test_ganz_fehlende_zeile_wird_erkannt():
    a = vergleiche({"a.pdf|Selbstkosten": k("732.75")}, [])
    assert a[0].zustand == "fehlt"


def test_schreibweise_egal():
    """3'120.40 und 3120.40 sind derselbe Betrag."""
    a = vergleiche({"a.pdf|Prämie": k("3'120.40")},
                   [zeile("a.pdf", "Prämie", "3120.40")])
    assert a[0].zustand == "stimmt"


def test_nachkommanullen_egal():
    a = vergleiche({"a.pdf|Prämie": k("1000")},
                   [zeile("a.pdf", "Prämie", "1000.00")])
    assert a[0].zustand == "stimmt"


def test_eintrag_ohne_sollwert_wird_uebersprungen():
    """Nur eine notierte Beschriftung ist ein Regel-Hinweis, kein Sollwert."""
    assert vergleiche({"a.pdf|Selbstkosten": k(label="Franchise")}, []) == []


# --- Bericht ----------------------------------------------------------------

def test_bericht_ohne_sollwerte_ist_sauber():
    text, sauber = bericht([])
    assert sauber is True
    assert "nichts zu prüfen" in text


def test_bericht_meldet_abweichung_als_unsauber():
    text, sauber = bericht([Abgleich("a.pdf", "Selbstkosten", "weicht_ab")])
    assert sauber is False
    assert "weicht ab" in text


def test_bericht_enthaelt_keine_betraege():
    """Der Bericht muss teilbar bleiben."""
    a = vergleiche({"a.pdf|Selbstkosten": k("732.75")},
                   [zeile("a.pdf", "Selbstkosten", "3980.25")])
    text, _ = bericht(a)
    assert "732.75" not in text
    assert "3980.25" not in text


def test_nur_uebereinstimmungen_sind_sauber():
    _, sauber = bericht([Abgleich("a.pdf", "X", "stimmt")])
    assert sauber is True


# --- Grader-Integration -----------------------------------------------------

def test_grader_check_ohne_sollwerte_besteht(tmp_path):
    """Ehrlich: ohne bestaetigte Werte kann der Check nichts pruefen."""
    from scripts.grade_run import check_bestaetigte_werte
    (tmp_path / "_results_full.json").write_text("[]")
    r = check_bestaetigte_werte(tmp_path)
    assert r.passed is True
    assert "nichts zu prüfen" in r.summary


def test_grader_check_findet_korrekturen_eine_ebene_hoeher(tmp_path):
    """Produktiver Lauf: Ergebnisse in json/, korrekturen.json darueber."""
    from scripts.grade_run import check_bestaetigte_werte
    (tmp_path / "korrekturen.json").write_text(
        json.dumps({"a.pdf|X": {"soll": "1.00"}}))
    unter = tmp_path / "json"
    unter.mkdir()
    (unter / "_results_full.json").write_text("[]")
    r = check_bestaetigte_werte(unter)
    assert r.passed is False, "fehlender Sollwert muss auffallen"


def test_grader_check_ohne_ergebnisdatei_faellt_durch(tmp_path):
    from scripts.grade_run import check_bestaetigte_werte
    (tmp_path / "korrekturen.json").write_text(
        json.dumps({"a.pdf|X": {"soll": "1.00"}}))
    r = check_bestaetigte_werte(tmp_path)
    assert r.passed is False
