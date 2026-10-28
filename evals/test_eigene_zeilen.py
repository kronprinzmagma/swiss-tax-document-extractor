"""Tests für selbst erfasste Positionen (260904-rmx).

Nicht jeder Zielwert lässt sich aus dem Beleg herauslesen. Beobachtet beim
Wertschriftenverzeichnis: es weist Ertrag **mit** und **ohne**
Verrechnungssteuer getrennt aus, der Feldvertrag kannte aber nur einen
Bruttoertrag — zwei Schubladen für drei Dinge. Statt das Schema zu erweitern
und die Extraktion anzufassen, legt der Mensch die Zeile selbst an.
"""
from __future__ import annotations

import json

from scripts.apply_korrekturen import lies_korrekturen
from scripts.build_tax_output import ergaenze_eigene_zeilen

KOPF = ("nr,beleg,ziffer,zielwert,person,aussteller,betrag,konfidenz,grund,"
        "herkunft,korrektur,notiz\n")


def schreibe_json(tmp_path, inhalt: dict):
    (tmp_path / "korrekturen.json").write_text(json.dumps(inhalt, ensure_ascii=False))
    return tmp_path


# --- Tabellenbau ------------------------------------------------------------

def test_neue_position_wird_ergaenzt(tmp_path):
    schreibe_json(tmp_path, {
        "depot.pdf|Ertrag mit Verrechnungssteuer": {
            "soll": "1000.00", "ziffer": "4", "neu": True,
            "person": "elternteil_1", "aussteller": "Bank",
        }})
    rows: list[dict] = []
    assert ergaenze_eigene_zeilen(rows, tmp_path) == 1
    assert rows[0]["beschreibung"] == "Ertrag mit Verrechnungssteuer"
    assert rows[0]["betrag"] == "1000.00"
    assert rows[0]["ziffer"] == "4"
    assert rows[0]["person"] == "elternteil_1"


def test_neue_position_ist_hoechste_konfidenz(tmp_path):
    from scripts.build_tax_output import KONFIDENZ_SICHER, konfidenz_fuer
    schreibe_json(tmp_path, {"a.pdf|X": {"soll": "1.00", "neu": True}})
    rows: list[dict] = []
    ergaenze_eigene_zeilen(rows, tmp_path)
    assert rows[0]["herkunft"] == "mensch"
    assert konfidenz_fuer(rows[0]) == KONFIDENZ_SICHER


def test_ohne_neu_flag_wird_nichts_ergaenzt(tmp_path):
    """Eine gewoehnliche Korrektur aendert eine Zeile, sie legt keine an."""
    schreibe_json(tmp_path, {"a.pdf|X": {"soll": "1.00"}})
    rows: list[dict] = []
    assert ergaenze_eigene_zeilen(rows, tmp_path) == 0


def test_bestehende_zeile_wird_nicht_verdoppelt(tmp_path):
    schreibe_json(tmp_path, {"a.pdf|X": {"soll": "1.00", "neu": True}})
    rows = [{"pdf_name": "a.pdf", "beschreibung": "X", "betrag": "2.00"}]
    assert ergaenze_eigene_zeilen(rows, tmp_path) == 0
    assert len(rows) == 1


def test_ohne_sollwert_keine_zeile(tmp_path):
    schreibe_json(tmp_path, {"a.pdf|X": {"neu": True}})
    rows: list[dict] = []
    assert ergaenze_eigene_zeilen(rows, tmp_path) == 0


def test_ohne_datei_passiert_nichts(tmp_path):
    assert ergaenze_eigene_zeilen([], tmp_path) == 0


def test_neue_zeile_traegt_alle_pflichtschluessel(tmp_path):
    """Renderer und Export verlassen sich darauf."""
    schreibe_json(tmp_path, {"a.pdf|X": {"soll": "1.00", "neu": True}})
    rows: list[dict] = []
    ergaenze_eigene_zeilen(rows, tmp_path)
    noetig = {"steuerbereich", "ziffer", "person", "aussteller", "beschreibung",
              "betrag", "jahr", "pdf_name", "status", "field_name", "herkunft",
              "belegtyp", "anchor_valid", "plaus_errs", "manual_review_marker"}
    assert noetig <= set(rows[0])


# --- CSV-Rueckweg -----------------------------------------------------------

def test_csv_markiert_neue_zeilen(tmp_path):
    p = tmp_path / "k.csv"
    p.write_text(KOPF + "10000,depot.pdf,4,Ertrag mit Verrechnungssteuer,"
                        "elternteil_1,Bank,,manuell,selbst erfasst,mensch,"
                        "1000.00,NEU\n")
    zeilen = lies_korrekturen(p)
    assert len(zeilen) == 1
    assert zeilen[0]["notiz"].startswith("NEU")
    assert zeilen[0]["korrektur"] == "1000.00"


# --- Position und Zeilenkontext ---------------------------------------------
#
# Die Beschriftung abzutippen ist doppelte Arbeit: entweder folgt sie aus der
# Dokumentart, oder der Wert steht weit von jedem Label entfernt. Der Klick im
# Seitenbild liefert dagegen Position UND den Zeilentext links davon — beides
# automatisch, und beides ist das Material fuer eine Layout-Regel.

KOPF_POS = ("nr,beleg,ziffer,zielwert,person,aussteller,betrag,konfidenz,grund,"
            "herkunft,korrektur,pos_x,pos_y,pos_seite,zeilenkontext,notiz\n")


def test_position_und_kontext_werden_gelesen(tmp_path):
    p = tmp_path / "k.csv"
    p.write_text(KOPF_POS + "1,a.pdf,4,Bruttoertrag,e1,Bank,10.00,unsicher,g,"
                            "modell,1250.00,0.912,0.604,1,Bruttoertrag (Zinsen),\n")
    z = lies_korrekturen(p)[0]
    assert z["pos_x"] == "0.912"
    assert z["pos_y"] == "0.604"
    assert z["zeilenkontext"] == "Bruttoertrag (Zinsen)"


def test_zeile_ohne_korrektur_aber_mit_markierung_zaehlt(tmp_path):
    """Wer nur markiert, ohne den Betrag zu aendern, bestaetigt die Position."""
    p = tmp_path / "k.csv"
    p.write_text(KOPF_POS + "1,a.pdf,4,Bruttoertrag,e1,Bank,10.00,sicher,g,"
                            "regel,,0.9,0.6,1,Bruttoertrag,geprüft\n")
    assert len(lies_korrekturen(p)) == 1
