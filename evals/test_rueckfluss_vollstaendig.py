"""Der Rückweg darf nichts verlieren (260904-rmx).

Erste Fassung schrieb strukturierte Änderungen — Person, Aussteller, Zielwert —
als Fliesstext in die Notiz-Spalte. Beim Zurücklesen wurde daraus nichts: nur
der Betrag hatte eine eigene Spalte und kam an. Wer Zuordnungen korrigiert
hatte, fand sie nach dem nächsten Lauf nicht wieder.
"""
from __future__ import annotations

import json

from scripts.apply_korrekturen import lies_korrekturen
from scripts.build_tax_output import wende_korrekturen_an

KOPF = ("nr,beleg,ziffer,zielwert,person,aussteller,betrag,konfidenz,grund,"
        "herkunft,korrektur,person_neu,aussteller_neu,zielwert_neu,ziffer_neu,"
        "bestaetigt,pos_x,pos_y,pos_seite,zeilenkontext,notiz\n")

ALT_KOPF = ("nr,beleg,ziffer,zielwert,person,aussteller,betrag,konfidenz,"
            "grund,herkunft,korrektur,notiz\n")


def zeile(**kw) -> dict:
    basis = {"pdf_name": "a.pdf", "beschreibung": "Bruttoertrag",
             "betrag": "100.00", "person": "", "aussteller": "Alt",
             "ziffer": "4", "herkunft": "modell", "anchor_valid": False,
             "plaus_errs": [], "plaus_hinweis": None,
             "manual_review_marker": None, "status": "manual_review",
             "field_name": "bruttoertrag", "derived": False}
    basis.update(kw)
    return basis


def schreibe(tmp_path, inhalt: dict):
    (tmp_path / "korrekturen.json").write_text(json.dumps(inhalt, ensure_ascii=False))
    return tmp_path


# --- Einzelne Felder --------------------------------------------------------

def test_person_allein_wird_uebernommen(tmp_path):
    """Es gibt Zeilen, bei denen nur die Zuordnung falsch war."""
    schreibe(tmp_path, {"a.pdf|Bruttoertrag": {"person": "elternteil_2"}})
    rows = [zeile()]
    assert wende_korrekturen_an(rows, tmp_path) == 1
    assert rows[0]["person"] == "elternteil_2"
    assert rows[0]["herkunft"] == "mensch"


def test_aussteller_allein_wird_uebernommen(tmp_path):
    schreibe(tmp_path, {"a.pdf|Bruttoertrag": {"aussteller": "Neue Bank"}})
    rows = [zeile()]
    wende_korrekturen_an(rows, tmp_path)
    assert rows[0]["aussteller"] == "Neue Bank"


def test_zielwert_und_ziffer_werden_uebernommen(tmp_path):
    """Der Fall Wertschriften: eine Zeile bekommt eine andere Bedeutung."""
    schreibe(tmp_path, {"a.pdf|Bruttoertrag": {
        "zielwert_neu": "Ertrag ohne Verrechnungssteuer", "ziffer_neu": "4"}})
    rows = [zeile()]
    wende_korrekturen_an(rows, tmp_path)
    assert rows[0]["beschreibung"] == "Ertrag ohne Verrechnungssteuer"


def test_betrag_und_zuordnung_zusammen(tmp_path):
    schreibe(tmp_path, {"a.pdf|Bruttoertrag": {
        "soll": "250.00", "person": "kind_1", "aussteller": "X"}})
    rows = [zeile()]
    wende_korrekturen_an(rows, tmp_path)
    assert (rows[0]["betrag"], rows[0]["person"], rows[0]["aussteller"]) \
        == ("250.00", "kind_1", "X")


def test_ohne_aenderung_kein_treffer(tmp_path):
    schreibe(tmp_path, {"a.pdf|Bruttoertrag": {"person": ""}})
    assert wende_korrekturen_an([zeile()], tmp_path) == 0


# --- CSV-Spalten ------------------------------------------------------------

def test_alle_spalten_werden_gelesen(tmp_path):
    p = tmp_path / "k.csv"
    p.write_text(KOPF + "1,a.pdf,4,Bruttoertrag,,Alt,100.00,wahrscheinlich,g,"
                        "modell,,elternteil_2,Neue Bank,Ertrag ohne VSt,4,,,,,,\n")
    z = lies_korrekturen(p)[0]
    assert z["person_neu"] == "elternteil_2"
    assert z["aussteller_neu"] == "Neue Bank"
    assert z["zielwert_neu"] == "Ertrag ohne VSt"


def test_zeile_nur_mit_bestaetigung_zaehlt(tmp_path):
    p = tmp_path / "k.csv"
    p.write_text(KOPF + "1,a.pdf,4,Bruttoertrag,e1,B,100.00,sicher,g,regel,,,,,,"
                        "Betrag+Person,,,,,\n")
    assert len(lies_korrekturen(p)) == 1


# --- Rueckwaertskompatibilitaet --------------------------------------------

def test_alte_csv_mit_notiz_wird_verstanden(tmp_path):
    """Wer noch eine CSV der ersten Fassung hat, verliert nichts."""
    from scripts.apply_korrekturen import main
    import sys
    p = tmp_path / "alt.csv"
    p.write_text(ALT_KOPF + "1,a.pdf,4,Bruttoertrag,,Alt,100.00,wahrscheinlich,"
                            "g,modell,,Person: elternteil_2 | Betrag bestätigt\n")
    ziel = tmp_path / "korrekturen.json"
    argv = sys.argv
    sys.argv = ["x", "--file", str(p), "--out", str(ziel)]
    try:
        main()
    finally:
        sys.argv = argv
    e = json.loads(ziel.read_text())["a.pdf|Bruttoertrag"]
    assert e["person"] == "elternteil_2"
    assert e["bestaetigt_betrag"] is True


# --- Ziffer folgt dem Zielwert ----------------------------------------------

def test_ziffer_kommt_mit_dem_zielwert_mit(tmp_path):
    """Der Fall Säule 3a: ein als Bankbeleg erkanntes Dokument.

    Der Zielwert liess sich schon immer aendern, die Ziffer nicht — sie war
    fuer bereits erkannte Zeilen nur Text. Wer die Zeile auf "Einzahlung
    Säule 3a" umstellte, liess sie unter Ziffer 30.1 stehen: der Betrag
    landete im Vermoegen statt bei den Abzuegen.
    """
    schreibe(tmp_path, {"a.pdf|Saldo 31.12.": {
        "zielwert_neu": "Einzahlung Säule 3a", "ziffer_neu": "14",
        "person": "elternteil_2"}})
    rows = [zeile(beschreibung="Saldo 31.12.", ziffer="30.1")]
    assert wende_korrekturen_an(rows, tmp_path) == 1
    assert rows[0]["beschreibung"] == "Einzahlung Säule 3a"
    assert rows[0]["ziffer"] == "14"
    assert rows[0]["person"] == "elternteil_2"


def test_oberflaeche_kennt_die_ziffer_zu_jedem_zielwert():
    """Die Zuordnung stammt aus dem Feldvertrag, nicht aus einer zweiten Liste."""
    from extractors.zielwerte import ZIELWERTE
    from scripts.build_review_html import ZIELWERT_ZIFFER
    for werte in ZIELWERTE.values():
        for z in werte:
            assert ZIELWERT_ZIFFER[z.bezeichnung] == z.ziffer


def test_ziffer_ist_in_jeder_zeile_editierbar():
    """Auch als Notausgang, wenn der Zielwert frei getippt wurde."""
    from scripts.build_review_html import baue_html
    doc = baue_html([{"nr": 1, "beleg": "a.pdf", "belegtyp": "bank_zinsausweis",
                      "ziffer": "30.1", "zielwert": "Saldo 31.12.",
                      "person": "familie", "aussteller": "B", "betrag": "1.00",
                      "konfidenz": "sicher", "grund": "g", "herkunft": "regel",
                      "seite": 1, "snippet": ""}], ["elternteil_1"])
    assert "function setZiel(" in doc
    # Kein r.neu-Vorbehalt mehr vor dem Ziffer-Eingabefeld.
    assert 'class="ziffer"' in doc
    assert "${r.neu\n            ? `<input class=\"ziffer\"" not in doc
