"""Mehrere Positionen gleicher Art aus EINEM Dokument (260923-dua).

Ein Hypothekarbeleg nennt drei Hypotheken und drei Schuldzinsen; die
Extraktion findet je eine. Die anderen muss der Mensch von Hand erfassen —
und genau das ging nicht:

* Der Schluessel einer Korrektur war ``<Dokument>|<Zielwert>``. Die zweite
  Position derselben Art ueberschrieb damit die erste.
* ``ergaenze_eigene_zeilen`` uebersprang einen Schluessel, den die Extraktion
  schon lieferte. Die von Hand erfasste zweite Hypothek landete nie in der
  Tabelle — ohne Meldung.

Beides wird hier festgenagelt. Dazu die Umbenennung: sie darf den Eintrag
mitnehmen und keinen Waisen hinterlassen, der sich fuer immer als „fehlt"
meldet.
"""
from __future__ import annotations

import json

from extractors.korrekturen import (
    baue_schluessel, finde, ist_gueltiger_zielwert, vergleiche, zerlege,
)
from scripts.apply_korrekturen import uebernehme
from scripts.build_tax_output import (
    ergaenze_eigene_zeilen, nummeriere_positionen, wende_korrekturen_an,
)


# --- Der Schluessel selbst --------------------------------------------------

def test_erste_position_behaelt_ihren_schluessel():
    """Sonst muessten alle bereits bestaetigten Eintraege migriert werden."""
    assert baue_schluessel("a.pdf", "Schuldzinsen") == "a.pdf|Schuldzinsen"
    assert baue_schluessel("a.pdf", "Schuldzinsen", 1) == "a.pdf|Schuldzinsen"


def test_weitere_positionen_tragen_ihre_nummer():
    assert baue_schluessel("a.pdf", "Schuldzinsen", 2) == "a.pdf|Schuldzinsen#2"
    assert baue_schluessel("a.pdf", "Schuldzinsen", 3) == "a.pdf|Schuldzinsen#3"


def test_zerlegen_ist_die_umkehrung():
    for pos in (1, 2, 17):
        k = baue_schluessel("b c.pdf", "Hypothekarschuld", pos)
        assert zerlege(k) == ("b c.pdf", "Hypothekarschuld", pos)


def test_raute_im_zielwert_bleibt_unangetastet():
    """Eine Kontonummer ist keine Position 1234."""
    assert zerlege("a.pdf|Konto #1234") == ("a.pdf", "Konto #1234", 1)
    assert zerlege("a.pdf|Konto#1234") == ("a.pdf", "Konto#1234", 1)
    assert zerlege("a.pdf|Zins") == ("a.pdf", "Zins", 1)
    # Position 1 wird nie geschrieben — "#1" ist deshalb Text.
    assert zerlege("a.pdf|Zins#1") == ("a.pdf", "Zins#1", 1)
    assert zerlege("a.pdf|Zins#02") == ("a.pdf", "Zins#02", 1)


def test_verwechselbare_bezeichnung_wird_abgewiesen():
    """Sonst zeigen zwei verschiedene Werte auf denselben Schluessel."""
    assert ist_gueltiger_zielwert("Schuldzinsen")
    assert ist_gueltiger_zielwert("Konto #1234")
    assert not ist_gueltiger_zielwert("Schuldzinsen#2")


def test_finde_trennt_die_positionen():
    korr = {"a.pdf|Schuldzinsen": {"soll": "100"},
            "a.pdf|Schuldzinsen#2": {"soll": "200"}}
    assert finde(korr, "a.pdf", "Schuldzinsen")["soll"] == "100"
    assert finde(korr, "a.pdf", "Schuldzinsen", 2)["soll"] == "200"


# --- Nummerierung der Laufzeilen -------------------------------------------

def test_gleiche_zielwerte_werden_durchnummeriert():
    rows = [{"pdf_name": "h.pdf", "beschreibung": "Schuldzinsen"},
            {"pdf_name": "h.pdf", "beschreibung": "Schuldzinsen"},
            {"pdf_name": "h.pdf", "beschreibung": "Hypothekarschuld"},
            {"pdf_name": "x.pdf", "beschreibung": "Schuldzinsen"}]
    nummeriere_positionen(rows)
    assert [r["pos"] for r in rows] == [1, 2, 1, 1]


def test_vorhandene_nummer_bleibt_stehen():
    """Selbst erfasste Zeilen kennen ihre Nummer aus dem Schluessel."""
    rows = [{"pdf_name": "h.pdf", "beschreibung": "Zins"},
            {"pdf_name": "h.pdf", "beschreibung": "Zins", "pos": 5}]
    nummeriere_positionen(rows)
    assert [r["pos"] for r in rows] == [1, 5]


# --- Speichern --------------------------------------------------------------

def _schreibe(tmp_path, zeilen, **kw):
    ziel = tmp_path / "korrekturen.json"
    uebernehme(zeilen, ziel, **kw)
    return json.loads(ziel.read_text())


def test_zweite_position_ueberschreibt_die_erste_nicht(tmp_path):
    """Der Kern: drei Hypotheken auf einem Beleg."""
    daten = _schreibe(tmp_path, [
        {"beleg": "h.pdf", "zielwert": "Hypothekarschuld", "korrektur": "100000",
         "position": "1"},
        {"beleg": "h.pdf", "zielwert": "Hypothekarschuld", "korrektur": "250000",
         "position": "2"},
        {"beleg": "h.pdf", "zielwert": "Hypothekarschuld", "korrektur": "80000",
         "position": "3"},
    ])
    assert daten["h.pdf|Hypothekarschuld"]["soll"] == "100000"
    assert daten["h.pdf|Hypothekarschuld#2"]["soll"] == "250000"
    assert daten["h.pdf|Hypothekarschuld#3"]["soll"] == "80000"


def test_ohne_positionsangabe_bleibt_alles_wie_bisher(tmp_path):
    daten = _schreibe(tmp_path, [
        {"beleg": "a.pdf", "zielwert": "Nettolohn", "korrektur": "90000"}])
    assert list(daten) == ["a.pdf|Nettolohn"]


def test_umbenennung_nimmt_den_eintrag_mit(tmp_path):
    """Sonst bleibt der alte Schluessel liegen und meldet sich fuer immer als
    „fehlt" — Bereitschafts-Punkt 8 wird nie wieder gruen."""
    ziel = tmp_path / "korrekturen.json"
    uebernehme([{"beleg": "w.pdf", "zielwert": "Bruttoertrag",
                 "korrektur": "1200", "bestaetigt": "Betrag"}], ziel)
    uebernehme([{"beleg": "w.pdf", "zielwert": "Bruttoertrag",
                 "zielwert_neu": "Bruttoertrag ohne Verrechnungssteuer",
                 "korrektur": "1200"}], ziel)
    daten = json.loads(ziel.read_text())
    assert "w.pdf|Bruttoertrag" not in daten
    eintrag = daten["w.pdf|Bruttoertrag ohne Verrechnungssteuer"]
    assert eintrag["soll"] == "1200"
    # Und die Bestaetigung aus dem ersten Durchgang faehrt mit.
    assert eintrag.get("bestaetigt_betrag") is True


def test_umbenennung_trifft_nur_die_gemeinte_position(tmp_path):
    ziel = tmp_path / "korrekturen.json"
    uebernehme([
        {"beleg": "h.pdf", "zielwert": "Zins", "korrektur": "10", "position": "1"},
        {"beleg": "h.pdf", "zielwert": "Zins", "korrektur": "20", "position": "2"},
    ], ziel)
    uebernehme([{"beleg": "h.pdf", "zielwert": "Zins", "position": "2",
                 "zielwert_neu": "Schuldzinsen", "korrektur": "20"}], ziel)
    daten = json.loads(ziel.read_text())
    assert daten["h.pdf|Zins"]["soll"] == "10"
    assert "h.pdf|Zins#2" not in daten
    assert daten["h.pdf|Schuldzinsen#2"]["soll"] == "20"


# --- Tabellenbau ------------------------------------------------------------

def _korpus(tmp_path, korrekturen: dict):
    samples = tmp_path / "json"
    samples.mkdir()
    (samples / "korrekturen.json").write_text(
        json.dumps(korrekturen, ensure_ascii=False))
    return samples


def test_selbst_erfasste_zweite_position_kommt_in_die_tabelle(tmp_path):
    """Der Fehler, der die zweite Hypothek verschwinden liess: der Schluessel
    galt als „schon vorhanden", weil die Extraktion die erste geliefert hat."""
    samples = _korpus(tmp_path, {
        "h.pdf|Hypothekarschuld#2": {"soll": "250000", "neu": True,
                                     "ziffer": "34"},
        "h.pdf|Hypothekarschuld#3": {"soll": "80000", "neu": True,
                                     "ziffer": "34"},
    })
    rows = [{"pdf_name": "h.pdf", "beschreibung": "Hypothekarschuld",
             "betrag": "100000", "ziffer": "34"}]
    ergaenzt = ergaenze_eigene_zeilen(rows, samples)
    assert ergaenzt == 2
    betraege = sorted(r["betrag"] for r in rows)
    assert betraege == ["100000", "250000", "80000"]
    assert sorted(r["pos"] for r in rows) == [1, 2, 3]


def test_korrektur_trifft_die_richtige_position(tmp_path):
    samples = _korpus(tmp_path, {
        "h.pdf|Schuldzinsen": {"soll": "1000"},
        "h.pdf|Schuldzinsen#2": {"soll": "2000"},
    })
    rows = [{"pdf_name": "h.pdf", "beschreibung": "Schuldzinsen", "betrag": "1"},
            {"pdf_name": "h.pdf", "beschreibung": "Schuldzinsen", "betrag": "2"}]
    wende_korrekturen_an(rows, samples)
    assert [r["betrag"] for r in rows] == ["1000", "2000"]


def test_bestaetigte_positionen_gelten_einzeln():
    """Vorher verglich der Abgleich beide Positionen gegen denselben Wert und
    meldete eine davon als Abweichung."""
    korr = {"h.pdf|Schuldzinsen": {"soll": "1000"},
            "h.pdf|Schuldzinsen#2": {"soll": "2000"}}
    zeilen = [{"pdf_name": "h.pdf", "beschreibung": "Schuldzinsen",
               "betrag": "1000", "pos": 1},
              {"pdf_name": "h.pdf", "beschreibung": "Schuldzinsen",
               "betrag": "2000", "pos": 2}]
    assert [a.zustand for a in vergleiche(korr, zeilen)] == ["stimmt", "stimmt"]


def test_abweichung_nennt_die_position():
    korr = {"h.pdf|Schuldzinsen#2": {"soll": "2000"}}
    zeilen = [{"pdf_name": "h.pdf", "beschreibung": "Schuldzinsen",
               "betrag": "9", "pos": 2}]
    a = vergleiche(korr, zeilen)[0]
    assert a.zustand == "weicht_ab"
    assert "Position 2" in a.zielwert


def test_zweiter_lauf_haelt_die_positionen(tmp_path):
    """Nichts darf beim naechsten `make productive` verschwinden."""
    samples = _korpus(tmp_path, {
        "h.pdf|Hypothekarschuld": {"soll": "100000", "bestaetigt_betrag": True,
                                   "wert_betrag": "100000"},
        "h.pdf|Hypothekarschuld#2": {"soll": "250000", "neu": True},
    })
    for _ in range(2):
        rows = [{"pdf_name": "h.pdf", "beschreibung": "Hypothekarschuld",
                 "betrag": "77"}]
        wende_korrekturen_an(rows, samples)
        ergaenze_eigene_zeilen(rows, samples)
        assert sorted(r["betrag"] for r in rows) == ["100000", "250000"]


# --- Die Aufstellung fuer den Steuerberater ---------------------------------

def test_xlsx_uebernimmt_die_korrigierten_werte(tmp_path):
    """Die Markdown-Tabelle war korrigiert, das xlsx nicht — und genau das
    ging zum Steuerberater (260923-dua)."""
    from scripts.build_steueraufstellung import wende_korrekturen_an_entries

    samples = _korpus(tmp_path, {
        "l.pdf|Nettolohn": {"soll": "91234", "bestaetigt_betrag": True,
                            "wert_betrag": "91234"},
    })
    entries = [{
        "pdf_name": "l.pdf", "status": "ok", "belegtyp": "lohnausweis",
        # Der Feldname ist der des Feldvertrags, nicht die Umgangssprache.
        "fields": [{"feld": "nettolohn_pos11", "value": "88000"},
                   {"feld": "jahr", "value": "2025"}],
    }]
    angewandt, ergaenzt = wende_korrekturen_an_entries(entries, samples)
    werte = {f["feld"]: f["value"] for f in entries[0]["fields"]}
    assert werte["nettolohn_pos11"] == "91234"
    assert angewandt >= 1


def test_xlsx_fuehrt_selbst_erfasste_positionen_auf(tmp_path):
    """Sonst fehlen die zweite und dritte Hypothek in der Aufstellung, ohne
    dass die Datei es sagt."""
    from scripts.build_steueraufstellung import (
        build_workbook, wende_korrekturen_an_entries,
    )

    samples = _korpus(tmp_path, {
        "h.pdf|Hypothekarschuld#2": {"soll": "250000", "neu": True,
                                     "ziffer": "34"},
    })
    entries = [{"pdf_name": "h.pdf", "status": "ok",
                "belegtyp": "hypothek_zinsbestaetigung", "fields": []}]
    _, ergaenzt = wende_korrekturen_an_entries(entries, samples)
    assert len(ergaenzt) == 1
    wb = build_workbook(entries, ergaenzt)
    assert "Von Hand ergänzt" in wb.sheetnames
    ws = wb["Von Hand ergänzt"]
    assert ws.cell(row=2, column=6).value == 2      # Position
    assert ws.cell(row=2, column=7).value == 250000.0
