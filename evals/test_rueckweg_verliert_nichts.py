"""Der Rückweg darf nichts wegnehmen, was schon gespeichert war (260904-rmx).

Beobachtet im Betrieb: nach dem ersten Start des Live-Servers waren die
Personenzuordnungen und die selbst erfassten Positionen aus der Übertragung
verschwunden, die Beträge aber noch da.

Zwei Ursachen, beide hier festgenagelt:

**Der Eintrag wurde ersetzt statt ergänzt.** Eine Zeile, die nur eine
Bestätigung mitbrachte, schrieb ihren Eintrag komplett neu — die zuvor
gespeicherte Person fiel dabei heraus.

**Die Oberfläche verglich gegen den falschen Bezugspunkt.** Sie prüfte den
Eingabewert gegen den bereits korrigierten Wert. Eine übernommene Korrektur
sah damit aus wie „unverändert" und wurde nicht mehr mitgeschickt.
"""
from __future__ import annotations

import json

from scripts.apply_korrekturen import uebernehme
from scripts.build_tax_output import wende_korrekturen_an


def lege(pfad, inhalt: dict):
    pfad.write_text(json.dumps(inhalt, ensure_ascii=False))


def zeile(**kw) -> dict:
    basis = {"pdf_name": "a.pdf", "beschreibung": "Bruttoertrag",
             "betrag": "1.00", "person": "", "aussteller": "B", "ziffer": "4",
             "herkunft": "modell", "anchor_valid": False, "plaus_errs": [],
             "plaus_hinweis": None, "manual_review_marker": None,
             "status": "manual_review", "field_name": "bruttoertrag",
             "derived": False}
    basis.update(kw)
    return basis


# --- Ergänzen statt ersetzen (CSV-Weg) --------------------------------------

def test_bestaetigung_loescht_die_person_nicht(tmp_path):
    """Der Fall aus dem Betrieb, in einer Zeile."""
    ziel = tmp_path / "korrekturen.json"
    lege(ziel, {"a.pdf|Bruttoertrag": {"zielwert": "Bruttoertrag", "ziffer": "4",
                                       "person": "elternteil_2", "soll": "250.00"}})
    uebernehme([{"beleg": "a.pdf", "zielwert": "Bruttoertrag", "ziffer": "4",
                 "bestaetigt": "Betrag"}], ziel)
    e = json.loads(ziel.read_text())["a.pdf|Bruttoertrag"]
    assert e["person"] == "elternteil_2"
    assert e["soll"] == "250.00"
    assert e["bestaetigt_betrag"] is True


def test_selbst_erfasste_position_bleibt_selbst_erfasst(tmp_path):
    ziel = tmp_path / "korrekturen.json"
    lege(ziel, {"a.pdf|Eigene": {"zielwert": "Eigene", "neu": True,
                                 "soll": "99.00"}})
    uebernehme([{"beleg": "a.pdf", "zielwert": "Eigene", "bestaetigt": "Person"}],
               ziel)
    e = json.loads(ziel.read_text())["a.pdf|Eigene"]
    assert e["neu"] is True
    assert e["soll"] == "99.00"


# --- Vollständiger Stand (Live-Weg) -----------------------------------------

def test_zuruecknehmen_nur_auf_ausdrueckliche_ansage(tmp_path):
    """Schweigen heisst NICHT loeschen.

    Vorher genuegte eine Zeile ohne Inhalt, um den Eintrag zu entfernen —
    und die Oberflaeche schickt im Live-Betrieb jede Zeile, bei jedem
    Speichern. Damit hat das blosse Oeffnen der Seite die Durchsicht
    geloescht (260904-rmx).
    """
    ziel = tmp_path / "korrekturen.json"
    lege(ziel, {"a.pdf|Bruttoertrag": {"zielwert": "Bruttoertrag", "ziffer": "4",
                                       "soll": "250.00"}})
    uebernehme([{"beleg": "a.pdf", "zielwert": "Bruttoertrag", "ziffer": "4"}],
               ziel, ersetzen=True)
    assert json.loads(ziel.read_text())["a.pdf|Bruttoertrag"]["soll"] == "250.00"

    uebernehme([{"beleg": "a.pdf", "zielwert": "Bruttoertrag",
                 "zuruecknehmen": "ja"}], ziel, ersetzen=True)
    assert json.loads(ziel.read_text()) == {}


def test_vor_jedem_schreiben_liegt_eine_sicherung(tmp_path):
    """Diese Datei ist die ganze Durchsicht. Sie einmal ohne Netz zu
    ueberschreiben hat gereicht."""
    ziel = tmp_path / "korrekturen.json"
    lege(ziel, {"a.pdf|X": {"zielwert": "X", "soll": "1.00"}})
    uebernehme([{"beleg": "a.pdf", "zielwert": "X", "korrektur": "2.00"}], ziel)
    sicherungen = list((tmp_path / "korrekturen-verlauf").glob("*.json"))
    assert len(sicherungen) == 1
    assert json.loads(sicherungen[0].read_text())["a.pdf|X"]["soll"] == "1.00"


def test_live_stand_hebt_eine_streichung_auf(tmp_path):
    ziel = tmp_path / "korrekturen.json"
    lege(ziel, {"kk.pdf|VVG": {"zielwert": "VVG", "entfernt": True,
                               "grund": "nicht_im_dokument"}})
    uebernehme([{"beleg": "kk.pdf", "zielwert": "VVG", "bestaetigt": "Betrag"}],
               ziel, ersetzen=True)
    e = json.loads(ziel.read_text())["kk.pdf|VVG"]
    assert "entfernt" not in e
    assert e["bestaetigt_betrag"] is True


def test_csv_weg_nimmt_nichts_zurueck(tmp_path):
    """Die CSV enthaelt nur geaenderte Zeilen — Schweigen heisst dort nichts."""
    ziel = tmp_path / "korrekturen.json"
    lege(ziel, {"a.pdf|Bruttoertrag": {"zielwert": "Bruttoertrag", "soll": "250.00"}})
    uebernehme([{"beleg": "a.pdf", "zielwert": "Bruttoertrag",
                 "bestaetigt": "Betrag"}], ziel)
    assert json.loads(ziel.read_text())["a.pdf|Bruttoertrag"]["soll"] == "250.00"


# --- Gestrichene Zeilen: Tabelle vs. Review ---------------------------------

def test_gestrichene_zeile_faellt_aus_der_tabelle(tmp_path):
    lege(tmp_path / "korrekturen.json", {"a.pdf|Bruttoertrag": {"entfernt": True}})
    rows = [zeile()]
    wende_korrekturen_an(rows, tmp_path)
    assert rows == []


def test_gestrichene_zeile_bleibt_in_der_review_sichtbar(tmp_path):
    """Sonst laesst sich eine versehentliche Streichung nie aufheben."""
    lege(tmp_path / "korrekturen.json", {"a.pdf|Bruttoertrag": {"entfernt": True}})
    rows = [zeile()]
    wende_korrekturen_an(rows, tmp_path, entfernen=False)
    assert len(rows) == 1
    assert rows[0]["gestrichen"] is True


# --- Der eigentliche Befund aus dem Code-Review -----------------------------

def test_streichung_erreicht_die_uebertragungstabelle(tmp_path):
    """``auto_rows + manual_rows`` war eine Kopie — das Entfernen verpuffte.

    Genau hier trennen sich Review und Tabelle: der Mensch sah die Zeile nicht
    mehr, in steuer_uebertragung.md stand sie weiter.
    """
    lege(tmp_path / "korrekturen.json", {"a.pdf|Bruttoertrag": {"entfernt": True}})
    auto = [zeile(status="auto")]
    manual = [zeile(pdf_name="b.pdf")]

    alle = auto + manual
    wende_korrekturen_an(alle, tmp_path)
    uebrig = {id(r) for r in alle}
    auto = [r for r in auto if id(r) in uebrig]
    manual = [r for r in manual if id(r) in uebrig]

    assert auto == []
    assert len(manual) == 1


def test_eigene_position_bleibt_nach_dem_rundlauf_erhalten(tmp_path):
    """Sie kommt als gewoehnliche Zeile in die Seite — und muss trotzdem als
    selbst erfasst zurueckgeschickt werden, sonst legt sie beim naechsten
    Aufbau niemand mehr an."""
    ziel = tmp_path / "korrekturen.json"
    lege(ziel, {"a.pdf|Eigene": {"zielwert": "Eigene", "ziffer": "4",
                                 "neu": True, "soll": "12.00",
                                 "person": "elternteil_1"}})
    # So schickt die Oberflaeche eine als `neu` markierte Zeile zurueck.
    uebernehme([{"beleg": "a.pdf", "zielwert": "Eigene", "ziffer": "4",
                 "korrektur": "12.00", "person_neu": "elternteil_1",
                 "notiz": "NEU"}], ziel, ersetzen=True)
    e = json.loads(ziel.read_text())["a.pdf|Eigene"]
    assert e["neu"] is True
    assert e["soll"] == "12.00"
    assert e["person"] == "elternteil_1"


def test_oberflaeche_markiert_ergaenzte_zeilen_als_eigene():
    """Ohne das Kennzeichen faende die Seite keine NEU-Zeile zum Schicken."""
    import inspect

    from scripts import build_review_html
    quelle = inspect.getsource(build_review_html.sammle_zeilen)
    assert 'r["ist_eigene"] = True' in quelle
    assert '"neu": bool(r.get("ist_eigene"))' in inspect.getsource(build_review_html)
