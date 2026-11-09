"""Eine Bestätigung hält den Wert fest, nicht nur ein Häkchen (260904-rmx).

Beobachtet im Betrieb: nach einem erneuten ``make productive`` waren die
Personenzuordnungen der Krankenkassenbelege weg — obwohl niemand etwas
geändert und obwohl der Mensch sie zuvor 46-mal bestätigt hatte.

Die Ursache ist unangenehm einfach: ``bestaetigt_betrag`` und
``bestaetigt_person`` wurden ausschliesslich benutzt, um in der Oberfläche
das Häkchen vorzusetzen. Auf die Tabelle wirkten sie nicht. Und weil das
Sprachmodell nicht deterministisch ist, liefert derselbe Beleg im nächsten
Lauf einen anderen Wert oder gar keinen.

Eine Prüfung, die den geprüften Wert nicht festhält, ist keine Prüfung —
sie ist eine Notiz darüber, dass einmal jemand hingeschaut hat.
"""
from __future__ import annotations

import json
import sys

from scripts.apply_korrekturen import uebernehme
from scripts.build_tax_output import wende_korrekturen_an

KOPF = ("nr,beleg,ziffer,zielwert,person,aussteller,betrag,konfidenz,grund,"
        "herkunft,korrektur,person_neu,aussteller_neu,zielwert_neu,ziffer_neu,"
        "bestaetigt,streichen,pos_x,pos_y,pos_seite,zeilenkontext,notiz\n")


def zeile(**kw) -> dict:
    basis = {"pdf_name": "kk.pdf", "beschreibung": "Prämie Grundversicherung KVG",
             "betrag": "3120.40", "person": "kind_1", "aussteller": "KK-A",
             "ziffer": "15", "herkunft": "modell", "anchor_valid": True,
             "plaus_errs": [], "plaus_hinweis": None,
             "manual_review_marker": None, "status": "auto",
             "field_name": "praemie_kvg_total", "derived": False}
    basis.update(kw)
    return basis


def bestaetige(ziel, **spalten) -> dict:
    r = {"beleg": "kk.pdf", "zielwert": "Prämie Grundversicherung KVG",
         "ziffer": "15", "person": "kind_1", "betrag": "3120.40",
         "bestaetigt": "Betrag+Person"}
    r.update(spalten)
    uebernehme([r], ziel, ersetzen=True)
    return json.loads(ziel.read_text())["kk.pdf|Prämie Grundversicherung KVG"]


# --- Was eine Bestaetigung speichert ----------------------------------------

def test_bestaetigung_speichert_den_betrag(tmp_path):
    e = bestaetige(tmp_path / "korrekturen.json")
    assert e["bestaetigt_betrag"] is True
    assert e["wert_betrag"] == "3120.40"


def test_bestaetigung_speichert_die_person(tmp_path):
    e = bestaetige(tmp_path / "korrekturen.json")
    assert e["bestaetigt_person"] is True
    assert e["wert_person"] == "kind_1"


def test_bestaetigung_ohne_wert_speichert_nur_das_haekchen(tmp_path):
    e = bestaetige(tmp_path / "korrekturen.json", person="", betrag="")
    assert e["bestaetigt_person"] is True
    assert "wert_person" not in e


def test_geaenderte_person_gewinnt_ueber_die_angezeigte(tmp_path):
    """person_neu ist die Wahl des Menschen, person nur der Anzeigestand."""
    e = bestaetige(tmp_path / "korrekturen.json", person_neu="kind_2")
    assert e["wert_person"] == "kind_2"


# --- Was sie beim naechsten Lauf bewirkt ------------------------------------

def test_verlorene_person_wird_wiederhergestellt(tmp_path):
    """Der Fall aus dem Betrieb, in einer Zeile."""
    bestaetige(tmp_path / "korrekturen.json")
    # Der neue Lauf hat die Person nicht mehr erkannt.
    rows = [zeile(person="")]
    assert wende_korrekturen_an(rows, tmp_path) == 1
    assert rows[0]["person"] == "kind_1"
    assert rows[0]["herkunft"] == "mensch"


def test_abweichender_betrag_wird_zurueckgesetzt(tmp_path):
    bestaetige(tmp_path / "korrekturen.json")
    rows = [zeile(betrag="1234.00")]
    wende_korrekturen_an(rows, tmp_path)
    assert rows[0]["betrag"] == "3120.40"


def test_unveraenderter_wert_zaehlt_nicht_als_aenderung(tmp_path):
    bestaetige(tmp_path / "korrekturen.json")
    assert wende_korrekturen_an([zeile()], tmp_path) == 0


def test_der_ganze_weg_ueber_die_csv(tmp_path):
    p = tmp_path / "k.csv"
    p.write_text(KOPF + "1,kk.pdf,15,Prämie Grundversicherung KVG,kind_1,KK-A,"
                        "3120.40,sicher,g,modell,,,,,,Betrag+Person,,,,,,\n")
    argv = sys.argv
    sys.argv = ["x", "--file", str(p)]
    try:
        from scripts.apply_korrekturen import main
        main()
    finally:
        sys.argv = argv
    rows = [zeile(person="", betrag="")]
    wende_korrekturen_an(rows, tmp_path)
    assert rows[0]["person"] == "kind_1"
    assert rows[0]["betrag"] == "3120.40"
