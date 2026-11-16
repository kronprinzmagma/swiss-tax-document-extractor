"""Rolle und Anzeigename sind zweierlei (260904-rmx).

Die Extraktion schreibt in die Personenspalte einen Anzeigetext:
„Vorname Nachname (elternteil_1)". Zwei Stellen verglichen diesen String
direkt mit „elternteil_1":

* die Vollständigkeitsprüfung — sie fand für kein Familienmitglied etwas und
  meldete lauter fehlende Dokumente, obwohl alle Zeilen zugeordnet waren;
* das Dropdown der Oberfläche — keine Option passte, das Feld blieb leer.

Beides sah nach „die Zuordnung fehlt" aus, während sie in Wahrheit vorhanden
und nur anders geschrieben war.
"""
from __future__ import annotations

from extractors.vollstaendigkeit import (
    pruefe_familienabdeckung,
    pruefe_person_zuordnung,
    rolle_aus_person,
)

ROLLEN = ("elternteil_1", "elternteil_2", "kind_1", "kind_2")


def test_anzeigetext_wird_auf_die_rolle_zurueckgefuehrt():
    assert rolle_aus_person("Vorname Nachname (elternteil_1)") == "elternteil_1"
    assert rolle_aus_person("Nachname, Vorname (elternteil_2)") == "elternteil_2"


def test_blosse_rolle_bleibt():
    for r in ROLLEN + ("familie", "unbekannt/manuell"):
        assert rolle_aus_person(r) == r


def test_name_ohne_rolle_gilt_als_nicht_zugeordnet():
    """Nicht raten: eine unerkannte Zuordnung gehoert sichtbar zu sein."""
    assert rolle_aus_person("Nachname, Vorname") == ""
    assert rolle_aus_person("") == ""
    assert rolle_aus_person(None) == ""


def test_fremde_klammer_ist_keine_rolle():
    assert rolle_aus_person("Konto (Sparen)") == ""
    assert rolle_aus_person("Police (elternteil_9)") == ""


def test_pruefung_erkennt_den_anzeigetext():
    """Der eigentliche Fall: alles zugeordnet, trotzdem 'nichts gefunden'."""
    zeilen = [{"person": f"Vorname Nachname ({r})",
               "beschreibung": "Prämie Grundversicherung KVG",
               "pdf_name": "kk.pdf"} for r in ROLLEN]
    assert pruefe_familienabdeckung(zeilen, ROLLEN) == []


def test_zeile_ohne_erkennbare_rolle_wird_gemeldet():
    zeilen = [{"person": "Nachname, Vorname", "beschreibung": "Prämie KVG",
               "pdf_name": "kk.pdf"}]
    codes = [b.code for b in pruefe_person_zuordnung(zeilen)]
    assert codes == ["person_fehlt"]


def test_oberflaeche_stellt_rolle_und_namen_getrennt_bereit():
    """Die Rolle geht ins Dropdown, der Name bleibt als Hinweis daneben."""
    from scripts.build_review_html import baue_html
    doc = baue_html([{"nr": 1, "beleg": "a.pdf", "belegtyp": "lohnausweis",
                      "ziffer": "1.1", "zielwert": "Nettolohn",
                      "person": "elternteil_1",
                      "personName": "Vorname Nachname (elternteil_1)",
                      "aussteller": "F", "betrag": "1.00", "konfidenz": "sicher",
                      "grund": "g", "herkunft": "regel", "seite": 1,
                      "snippet": ""}], ["elternteil_1"])
    assert "aus dem Beleg:" in doc
    assert "r.personName" in doc


# --- Die Rolle kommt aus der Quelle, nicht aus dem Anzeigetext -------------

def test_rolle_familie_ueberlebt_den_anzeigetext():
    """Bei Rolle 'familie' enthaelt der Anzeigetext nur den Namen — aus ihm
    laesst sich nichts mehr zurueckgewinnen. Die Zeile fuehrt die Rolle mit."""
    from scripts.build_review_html import _rolle_der_zeile
    assert _rolle_der_zeile({"person": "Nachname, Vorname",
                             "person_rolle": "familie"}) == "familie"


def test_korrigierte_rolle_schlaegt_die_extrahierte():
    from scripts.build_review_html import _rolle_der_zeile
    assert _rolle_der_zeile({"person": "kind_2",
                             "person_rolle": "elternteil_1"}) == "kind_2"


def test_ohne_beides_nicht_zugeordnet():
    from scripts.build_review_html import _rolle_der_zeile
    assert _rolle_der_zeile({"person": "Nachname, Vorname"}) == ""
    assert _rolle_der_zeile({"person": "", "person_rolle": "quatsch"}) == ""


def test_build_rows_fuehrt_die_rolle_mit():
    import inspect

    from scripts import build_tax_output
    assert '"person_rolle": person_role' in inspect.getsource(build_tax_output)
