"""Was der Audit nach dem Verlust vom 2026-09-10 gefunden hat.

Zwei bestätigte Wege, auf denen die Durchsicht ein zweites Mal verschwunden
wäre — beide gefunden, bevor sie zuschlagen konnten.
"""
from __future__ import annotations

import json

from scripts.build_tax_output import _sichere_ausgefuellte_csv
from scripts.run_productive_local import GESCHUETZT, _json_abgleich

KOPF = ("nr,beleg,ziffer,zielwert,person,aussteller,betrag,konfidenz,grund,"
        "herkunft,korrektur,person_neu,aussteller_neu,zielwert_neu,ziffer_neu,"
        "bestaetigt,streichen,pos_x,pos_y,pos_seite,zeilenkontext,notiz\n")


def ordner(tmp_path):
    quelle, ziel = tmp_path / "quelle", tmp_path / "json"
    quelle.mkdir()
    ziel.mkdir()
    return quelle, ziel


# --- make productive darf die Durchsicht nicht loeschen ---------------------

def test_korrekturen_json_ueberlebt_den_abgleich(tmp_path):
    """Der Abgleich loeschte jede JSON ohne pdf_name — und korrekturen.json
    hat keins. Jeder produktive Lauf hat damit die Durchsicht geloescht."""
    quelle, ziel = ordner(tmp_path)
    (ziel / "korrekturen.json").write_text(json.dumps(
        {"a.pdf|Nettolohn": {"soll": "85420.00"}}))
    _json_abgleich(quelle, ziel)
    assert (ziel / "korrekturen.json").exists()
    assert json.loads((ziel / "korrekturen.json").read_text())


def test_auch_andere_handgepflegte_dateien_ueberleben(tmp_path):
    quelle, ziel = ordner(tmp_path)
    for name in GESCHUETZT:
        (ziel / name).write_text("{}")
    _json_abgleich(quelle, ziel)
    for name in GESCHUETZT:
        assert (ziel / name).exists(), name


def test_fremde_json_ohne_pages_wird_nicht_angefasst(tmp_path):
    """Namensliste allein genuegt nicht — die naechste Datei heisst anders."""
    quelle, ziel = ordner(tmp_path)
    (ziel / "irgendwas.json").write_text(json.dumps({"was": "auch immer"}))
    _json_abgleich(quelle, ziel)
    assert (ziel / "irgendwas.json").exists()


def test_verwaistes_word_json_wird_weiterhin_entfernt(tmp_path):
    """Der Abgleich soll seine Arbeit tun — nur eben nur an Word-JSONs."""
    quelle, ziel = ordner(tmp_path)
    (ziel / "weg.json").write_text(json.dumps(
        {"pdf_name": "weg.pdf", "pdf_fingerabdruck": "1:2", "pages": []}))
    assert _json_abgleich(quelle, ziel) == (1, 0)
    assert not (ziel / "weg.json").exists()


# --- eine ausgefuellte korrektur.csv wird gesichert -------------------------

def test_ausgefuellte_csv_wird_vor_dem_ueberschreiben_gesichert(tmp_path):
    p = tmp_path / "korrektur.csv"
    p.write_text(KOPF + "1,a.pdf,4,Bruttoertrag,,B,1.00,unsicher,g,modell,"
                        "250.00,,,,,,,,,,,\n")
    _sichere_ausgefuellte_csv(p)
    kopien = list((tmp_path / "korrekturen-verlauf").glob("korrektur-*.csv"))
    assert len(kopien) == 1
    assert "250.00" in kopien[0].read_text()


def test_leere_vorlage_wird_nicht_gesichert(tmp_path):
    """Sonst fuellt sich der Verlauf mit Vorlagen und verdeckt das Wichtige."""
    p = tmp_path / "korrektur.csv"
    p.write_text(KOPF + "1,a.pdf,4,Bruttoertrag,,B,1.00,unsicher,g,modell,"
                        ",,,,,,,,,,,\n")
    _sichere_ausgefuellte_csv(p)
    assert not (tmp_path / "korrekturen-verlauf").exists()


# --- Streichen darf nichts nebenbei loeschen --------------------------------

def test_streichen_behaelt_die_uebrigen_felder(tmp_path):
    """Eine Streichung sagt "steht nicht in diesem Dokument" — sie sagt
    nichts ueber den bestaetigten Betrag oder die Person, die daneben
    gespeichert sind."""
    from scripts.apply_korrekturen import uebernehme
    ziel = tmp_path / "korrekturen.json"
    ziel.write_text(json.dumps({"a.pdf|X": {
        "zielwert": "X", "ziffer": "15", "soll": "3120.40",
        "person": "kind_1", "label": "Ihr Anteil",
        "bestaetigt_betrag": True, "wert_betrag": "3120.40"}}))
    uebernehme([{"beleg": "a.pdf", "zielwert": "X", "streichen": "ja"}],
               ziel, ersetzen=True)
    e = json.loads(ziel.read_text())["a.pdf|X"]
    assert e["entfernt"] is True
    for feld in ("soll", "person", "label", "wert_betrag"):
        assert feld in e, feld


def test_zuruecknehmen_der_streichung_laesst_den_rest_stehen(tmp_path):
    from scripts.apply_korrekturen import uebernehme
    ziel = tmp_path / "korrekturen.json"
    ziel.write_text(json.dumps({"a.pdf|X": {
        "zielwert": "X", "soll": "3120.40", "person": "kind_1",
        "entfernt": True, "grund": "nicht_im_dokument"}}))
    uebernehme([{"beleg": "a.pdf", "zielwert": "X", "bestaetigt": "Betrag",
                 "betrag": "3120.40"}], ziel, ersetzen=True)
    e = json.loads(ziel.read_text())["a.pdf|X"]
    # Die Streichung ist aufgehoben — ausdruecklich als Wert, nicht durch
    # Weglassen: Abwesenheit ueberlebt keine Zusammenfuehrung (260923-dua).
    from extractors.korrekturen import ist_gestrichen
    assert not ist_gestrichen(e)
    assert e["entfernt"] is False
    assert e["soll"] == "3120.40"
    assert e["person"] == "kind_1"


# --- Umbenannte Zielwerte erzeugen keinen zweiten Eintrag -------------------

def test_umbenennung_legt_keinen_doppelten_eintrag_an(tmp_path):
    """Sonst haengt ergaenze_eigene_zeilen beide an — derselbe Betrag stuende
    zweimal in der Steuererklaerung."""
    from scripts.apply_korrekturen import uebernehme
    ziel = tmp_path / "korrekturen.json"
    ziel.write_text(json.dumps({"a.pdf|Bruttozins / Bruttoertrag": {
        "zielwert": "Bruttozins / Bruttoertrag", "soll": "371.88"}}))
    uebernehme([{"beleg": "a.pdf",
                 "zielwert": "Bruttoertrag ohne Verrechnungssteuer",
                 "korrektur": "400.00"}], ziel, ersetzen=True)
    daten = json.loads(ziel.read_text())
    assert len(daten) == 1, daten
    assert next(iter(daten.values()))["soll"] == "400.00"


# --- korrekturen.json ist kein Dokument -------------------------------------

def test_extraktion_haelt_korrekturen_nicht_fuer_ein_dokument(tmp_path):
    """Seit der Abgleich sie verschont, liegt sie neben den Word-JSONs — und
    die Extraktion zaehlte sie als 36. Sample."""
    from scripts.process_samples_full import _ist_word_json
    (tmp_path / "korrekturen.json").write_text(json.dumps(
        {"a.pdf|X": {"soll": "1.00"}}))
    (tmp_path / "doc.json").write_text(json.dumps(
        {"pdf_name": "doc.pdf", "pages": []}))
    assert not _ist_word_json(tmp_path / "korrekturen.json")
    assert _ist_word_json(tmp_path / "doc.json")


# --- Ruecknahmen ueberleben jede Zusammenfuehrung (260923-dua) ---------------

def test_ruecknahme_wird_nicht_wiederauferweckt(tmp_path):
    """Der Kern: Abwesenheit ueberlebt keine Zusammenfuehrung.

    Frueher entfernte eine Ruecknahme den Schluessel. Die zweite Ablage, eine
    Sicherung im Verlauf oder `make korrekturen-zurueck` brachten ihn zurueck
    — und die Ruecknahme war wirkungslos.
    """
    import json as _json

    from extractors.korrekturen import lade_alle
    from scripts.apply_korrekturen import uebernehme

    samples = tmp_path / "json"
    samples.mkdir()
    # Die aeussere Ablage kennt den Eintrag noch.
    (tmp_path / "korrekturen.json").write_text(
        _json.dumps({"a.pdf|Zins": {"soll": "100"}}))

    uebernehme([{"beleg": "a.pdf", "zielwert": "Zins", "zuruecknehmen": "ja"}],
               samples / "korrekturen.json")

    zusammen = lade_alle(samples)
    assert zusammen["a.pdf|Zins"].get("zurueckgenommen") is True
    assert "soll" not in zusammen["a.pdf|Zins"]


def test_zurueckgenommene_zeile_kommt_nicht_in_die_tabelle(tmp_path):
    import json as _json

    from scripts.build_tax_output import ergaenze_eigene_zeilen

    samples = tmp_path / "json"
    samples.mkdir()
    (samples / "korrekturen.json").write_text(_json.dumps({
        "a.pdf|Eigene": {"soll": "50", "neu": True, "zurueckgenommen": True},
        "a.pdf|Andere": {"soll": "60", "neu": True},
    }))
    rows: list[dict] = []
    assert ergaenze_eigene_zeilen(rows, samples) == 1
    assert [r["beschreibung"] for r in rows] == ["Andere"]


def test_gestrichene_position_gilt_nicht_als_abweichung():
    """Der Mensch streicht eine faelschlich erkannte Position — und die
    Pruefung warf ihm genau das als Rueckschritt vor."""
    from extractors.korrekturen import vergleiche

    korr = {"a.pdf|VVG": {"soll": "200", "entfernt": True, "grund": "x"},
            "a.pdf|KVG": {"soll": "300"}}
    zeilen = [{"pdf_name": "a.pdf", "beschreibung": "KVG", "betrag": "300"}]
    zustaende = {a.zielwert: a.zustand for a in vergleiche(korr, zeilen)}
    assert zustaende == {"KVG": "stimmt"}


def test_unberuehrte_zeile_legt_keine_leere_huelse_an(tmp_path):
    """Im Live-Betrieb geht JEDE Zeile mit. Daraus entstand fuer jede eine
    Huelse mit nichts als Zielwert und Ziffer — sie blaehten die Datei auf und
    liessen `make bereit` mehr Durchsicht melden, als es gab."""
    import json as _json

    from scripts.apply_korrekturen import uebernehme

    ziel = tmp_path / "korrekturen.json"
    uebernehme([
        {"beleg": "a.pdf", "zielwert": "Zins", "ziffer": "4"},
        {"beleg": "a.pdf", "zielwert": "Saldo", "ziffer": "30.1",
         "korrektur": "1000"},
    ], ziel, ersetzen=True)
    daten = _json.loads(ziel.read_text())
    assert list(daten) == ["a.pdf|Saldo"]
