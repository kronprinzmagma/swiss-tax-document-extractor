"""Bestaetigte Arbeit verschwindet nie still.

Dreimal an einem Tag ist dasselbe passiert: ein Lauf schrieb die Korrekturen
neu, und hinterher waren Positionen weg, die der Mensch bestaetigt hatte —
einmal durch eine Umbenennung, die den Bezug abriss, zweimal durch eine
Aufraeum-Regel, die die falsche Kopie behielt. Gemerkt hat es niemand beim
Schreiben; erst die Tabelle war kuerzer.

`pruefe_schutz` steht seither vor dem einzigen Schreibbefehl. Diese Tests
halten fest, was sie durchlaesst und was nicht.
"""
from __future__ import annotations

import json

import pytest

from extractors.korrekturen import (SchutzVerletzt, pruefe_schutz)
from scripts.apply_korrekturen import uebernehme

BESTAETIGT = {"soll": "1234.00", "bestaetigt_betrag": True,
              "zielwert": "Saldo 31.12."}


def test_ein_bestaetigter_wert_darf_nicht_verschwinden():
    vorher = {"a.pdf|Saldo 31.12.|1": BESTAETIGT}
    assert pruefe_schutz(vorher, {}) == ["a.pdf|Saldo 31.12.|1"]


def test_aendern_ist_erlaubt():
    vorher = {"a.pdf|Saldo 31.12.|1": BESTAETIGT}
    nachher = {"a.pdf|Saldo 31.12.|1": dict(BESTAETIGT, soll="999.00")}
    assert pruefe_schutz(vorher, nachher) == []


def test_ausdrueckliche_ruecknahme_ist_erlaubt():
    """Ein Grabstein ist ein Eintrag, kein Loch."""
    vorher = {"a.pdf|Saldo 31.12.|1": BESTAETIGT}
    nachher = {"a.pdf|Saldo 31.12.|1": {"zurueckgenommen": True,
                                        "zielwert": "Saldo 31.12."}}
    assert pruefe_schutz(vorher, nachher) == []


def test_streichen_ist_erlaubt():
    vorher = {"a.pdf|Saldo 31.12.|1": BESTAETIGT}
    nachher = {"a.pdf|Saldo 31.12.|1": dict(BESTAETIGT, entfernt=True)}
    assert pruefe_schutz(vorher, nachher) == []


def test_unbestaetigtes_darf_wegfallen():
    """Nur Bestaetigtes ist geschuetzt — Vorschlaege duerfen sich aendern."""
    vorher = {"a.pdf|Saldo 31.12.|1": {"zielwert": "Saldo 31.12."}}
    assert pruefe_schutz(vorher, {}) == []


def test_selbst_erfasstes_gilt_als_bestaetigt():
    """Was der Mensch selbst eingetippt hat, ist erst recht geschuetzt.

    Genau diese Zeilen waren es, die zweimal verlorengingen.
    """
    vorher = {"a.pdf|Schuldzinsen Hypothek|3": {"neu": True, "soll": "820.00",
                                                "zielwert": "Schuldzinsen "
                                                            "Hypothek"}}
    assert pruefe_schutz(vorher, {}) == ["a.pdf|Schuldzinsen Hypothek|3"]


def test_aufraeumen_entfernt_nie_einen_bestaetigten_wert(tmp_path):
    """Die Stelle, die es zweimal getan hat.

    `make verwaiste --putzen` raeumt Reste weg, deren Dokument verschwunden
    ist. Zweimal hat es dabei selbst eingetippte, bestaetigte Positionen
    mitgenommen. Seither steht Bestaetigtes ausserhalb seiner Reichweite —
    auch wenn es ausdruecklich auf der Loeschliste steht.
    """
    from scripts.verwaiste import entferne

    (tmp_path / "json").mkdir()
    ablage = tmp_path / "json" / "korrekturen.json"
    daten = {"weg.pdf|Saldo 31.12.|1": {"zielwert": "Saldo 31.12."},
             "weg.pdf|Schuldzinsen Hypothek|3": {"neu": True, "soll": "820.00",
                                                 "zielwert": "Schuldzinsen "
                                                             "Hypothek"}}
    ablage.write_text(json.dumps(daten), encoding="utf-8")

    entfernt = entferne(tmp_path / "json", list(daten))

    uebrig = json.loads(ablage.read_text())
    assert "weg.pdf|Schuldzinsen Hypothek|3" in uebrig, (
        "Ein selbst eingetippter, bestaetigter Wert wurde weggeraeumt")
    assert "weg.pdf|Saldo 31.12.|1" not in uebrig
    assert entfernt == 1


def test_ein_befund_der_schranke_verhindert_das_schreiben(tmp_path,
                                                          monkeypatch):
    """Eine Schranke, die nur meldet, ist keine Schranke.

    `uebernehme` loescht heute von sich aus nichts — der Befund kaeme also nie
    zustande. Geprueft wird deshalb die Verdrahtung: **wenn** die Schranke
    anschlaegt, bricht der Vorgang ab und die Platte bleibt unberuehrt. Das
    ist der Teil, der bei einem kuenftigen Umbau stillschweigend wegfallen
    koennte.
    """
    import scripts.apply_korrekturen as ak

    ziel = tmp_path / "korrekturen.json"
    ziel.write_text(json.dumps({"a.pdf|Saldo 31.12.|1": BESTAETIGT}),
                    encoding="utf-8")
    vorher_roh = ziel.read_bytes()

    monkeypatch.setattr("extractors.korrekturen.pruefe_schutz",
                        lambda vorher, nachher: ["a.pdf|Erfunden|1"])

    with pytest.raises(SchutzVerletzt):
        ak.uebernehme([{"beleg": "a.pdf", "zielwert": "Saldo 31.12.",
                        "korrektur": "7.00", "bestaetigt": "x"}], ziel)

    assert ziel.read_bytes() == vorher_roh, (
        "Die Datei wurde angefasst, obwohl die Schranke angeschlagen hat")


def _zeile(beleg, zielwert, betrag, pos=1):
    return {"beleg": beleg, "zielwert": zielwert, "betrag": betrag, "pos": pos}


def test_verschobene_position_gilt_nicht_als_verloren():
    """Dieselbe Bezeichnung, andere Nummer — die Arbeit ist da."""
    from extractors.korrekturen import vergleiche

    korr = {"a.pdf|Schuldzinsen Hypothek": {"soll": "820.00",
                                            "bestaetigt_betrag": True}}
    zeilen = [_zeile("a.pdf", "Schuldzinsen Hypothek", "820.00", pos=3)]
    assert [a.zustand for a in vergleiche(korr, zeilen)] == ["stimmt"]


def test_zahl_unter_anderem_namen_ist_rueckstand_kein_verlust():
    """Veralteter Schluessel, richtige Zahl — kein Rueckschritt.

    Solche Reste entstehen, wenn Positionen zwischen Dokumenten neu geordnet
    werden. Als "fehlt" gemeldet schicken sie den Menschen auf die Suche nach
    etwas, das er laengst in der Tabelle hat.
    """
    from extractors.korrekturen import vergleiche

    korr = {"a.pdf|Vermögensstand 31.12.": {"soll": "820.00",
                                            "bestaetigt_betrag": True}}
    zeilen = [_zeile("a.pdf", "Schuldzinsen Hypothek", "820.00")]
    ergebnis = vergleiche(korr, zeilen)
    assert [a.zustand for a in ergebnis] == ["andernorts"]
    assert "stattdessen Schuldzinsen Hypothek" in ergebnis[0].grund


def test_eine_wirklich_verlorene_zahl_bleibt_ein_befund():
    """Die Abgrenzung muss in beide Richtungen halten."""
    from extractors.korrekturen import vergleiche

    korr = {"a.pdf|Vermögensstand 31.12.": {"soll": "820.00",
                                            "bestaetigt_betrag": True}}
    zeilen = [_zeile("a.pdf", "Schuldzinsen Hypothek", "999.00")]
    ergebnis = vergleiche(korr, zeilen)
    assert [a.zustand for a in ergebnis] == ["fehlt"]


def test_eine_umbenennung_ist_kein_verlust(tmp_path):
    """Ein umgehaengter Schluessel verschwindet links und erscheint rechts.

    Die erste Fassung der Schranke stolperte darueber — und ueber einen
    Namen, den die Funktion schon fuer etwas anderes benutzte. Das flog erst
    auf, als ein anderes Werkzeug den Pfad betrat.
    """
    ziel = tmp_path / "korrekturen.json"
    ziel.write_text(json.dumps({"a.pdf|Bruttoertrag": {
        "soll": "12.40", "bestaetigt_betrag": True,
        "zielwert": "Bruttoertrag"}}), encoding="utf-8")

    bericht = uebernehme([{"beleg": "a.pdf", "zielwert": "Bruttoertrag",
                           "zielwert_neu": "Bruttoertrag ohne "
                                           "Verrechnungssteuer",
                           "korrektur": "12.40", "bestaetigt": "Betrag"}],
                         ziel)

    danach = json.loads(ziel.read_text())
    assert "a.pdf|Bruttoertrag ohne Verrechnungssteuer" in danach
    assert bericht["gesamt"] == 1
