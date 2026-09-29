"""Charakterisierungstest der Übertragungstabelle (260904-rmx).

Nagelt die heutige Ausgabe von ``build_tax_output`` an einem vollständig
**synthetischen** Lauf fest — keine echten und keine anonymisierten Daten,
deshalb committierbar.

Zweck: ``build_tax_output.py`` ist mit 1'438 Zeilen die fehleranfälligste
Datei des Projekts; fast alle Fehler dieser Session sassen dort. Ein Umbau ist
richtig, aber nur gefahrlos, wenn eine Regression sofort auffällt. Dieser Test
beschreibt nicht, was *richtig* wäre — er hält fest, was *ist*. Ändert sich das
Verhalten absichtlich, wird der Test bewusst angepasst; ändert es sich
versehentlich, schlägt er fehl.
"""
from __future__ import annotations

import json

import pytest

from scripts.build_tax_output import (
    build_rows,
    drop_zero_rows,
    konfidenz_fuer,
    konfidenz_grund,
    mark_duplicate_positions,
    normalize_aussteller,
)


def _feld(name, value, *, anker=True, herkunft="regel", bbox=(10.0, 20.0, 40.0, 30.0)):
    return {"feld": name, "value": value, "bbox": list(bbox), "page": 1,
            "snippet": str(value), "anchor_valid": anker,
            "inference_source": "text:bbox_anchor", "herkunft": herkunft}


@pytest.fixture
def lauf() -> list[dict]:
    """Ein synthetischer Lauf über drei Belegtypen."""
    return [
        {
            "pdf_name": "lohn-synthetisch.pdf", "status": "ok",
            "belegtyp": "lohnausweis",
            "fields": [
                _feld("arbeitgeber", "Beispiel AG"),
                _feld("arbeitnehmer_name", "Hans Muster"),
                _feld("jahr", "2025"),
                _feld("nettolohn_pos11", "68492.00"),
            ],
        },
        {
            "pdf_name": "bank-synthetisch.pdf", "status": "ok",
            "belegtyp": "bank_zinsausweis",
            "fields": [
                _feld("bank", "Bank Beispiel"),
                _feld("jahr", "2025"),
                _feld("vermoegensstand_3112", "3877.15"),
                _feld("bruttoertrag", "9.45"),
                _feld("verrechnungssteuer", "0.00"),
            ],
        },
        {
            "pdf_name": "kk-synthetisch.pdf", "status": "ok",
            "belegtyp": "kk_praemienbescheinigung",
            "fields": [
                _feld("kasse", "Kasse Beispiel"),
                _feld("versicherte_person_name", "Hans Muster"),
                _feld("jahr", "2025"),
                _feld("praemie_kvg_total", "3120.40"),
                _feld("selbstgetragene_kosten", "732.75", herkunft="modell"),
            ],
        },
    ]


def _zeilen(lauf):
    rows = []
    for e in lauf:
        for r in build_rows(e):
            r["aussteller"] = normalize_aussteller(r.get("aussteller"))
            rows.append(r)
    rows, _ = drop_zero_rows(rows)
    return rows


def test_zielwerte_und_ziffern_sind_stabil(lauf):
    """Der Feldvertrag bestimmt, welcher Wert in welche Ziffer geht."""
    paare = sorted({(r["ziffer"], r["beschreibung"]) for r in _zeilen(lauf)})
    # Die Bezeichnungen kommen jetzt aus dem Feldvertrag. Vorher trug die
    # Tabelle eigene ("Vermögensstand 31.12." statt "Saldo 31.12."), und
    # jede Pruefung, die beide vergleicht, war blind (260904-rmx).
    assert paare == [
        ("1.1", "Nettolohn"),
        ("15", "Prämie Grundversicherung KVG"),
        ("22.1", "Selbst getragene Krankheits- und Unfallkosten"),
        ("30.1", "Saldo 31.12."),
        # Ziffer 4 heisst jetzt, was man im Formular ausfuellt: den
        # Bruttoertrag in der Abteilung mit oder ohne Verrechnungssteuer.
        # Vorher hiessen die beiden Zeilen "Bruttoertrag" und
        # "Verrechnungssteuer 35 %" — Feldnamen statt Formularzeilen
        # (260904-rmx).
        ("4", "Bruttoertrag ohne Verrechnungssteuer"),
    ]


def test_verrechnungssteuer_ist_keine_uebertragungszeile(lauf):
    """Sie ist ein abgeleiteter Wert — 35 % der Ertraege in Abteilung A —
    und keine Zahl, die man ins Formular abschreibt."""
    felder = {r.get("field_name") for r in _zeilen(lauf)}
    assert "verrechnungssteuer" not in felder
    assert "verrechnungssteuer_total" not in felder


def test_konfidenz_haengt_an_der_herkunft(lauf):
    nach_feld = {r["field_name"]: r for r in _zeilen(lauf)}
    assert konfidenz_fuer(nach_feld["nettolohn_pos11"]) == "sicher"
    assert konfidenz_fuer(nach_feld["selbstgetragene_kosten"]) == "wahrscheinlich"


def test_grund_ist_aussagekraeftig(lauf):
    nach_feld = {r["field_name"]: r for r in _zeilen(lauf)}
    assert "Regel" in konfidenz_grund(nach_feld["nettolohn_pos11"])
    assert "Modell" in konfidenz_grund(nach_feld["selbstgetragene_kosten"])


def test_jede_zeile_traegt_die_pflichtfelder(lauf):
    """Renderer und Export verlassen sich auf diese Schluessel."""
    noetig = {"steuerbereich", "ziffer", "person", "aussteller", "beschreibung",
              "betrag", "jahr", "pdf_name", "status", "field_name", "herkunft",
              "belegtyp", "anchor_valid"}
    for r in _zeilen(lauf):
        fehlend = noetig - set(r)
        assert not fehlend, f"{r['beschreibung']}: fehlende Schlüssel {fehlend}"


def test_betraege_bleiben_wortwoertlich(lauf):
    """Kein Umformatieren zwischen Extraktion und Tabelle."""
    betraege = {r["betrag"] for r in _zeilen(lauf)}
    assert {"68492.00", "3877.15", "9.45", "3120.40", "732.75"} <= betraege


def test_duplikate_werden_markiert_nicht_entfernt(lauf):
    doppelt = _zeilen(lauf)
    zwilling = dict(doppelt[0])
    zwilling["pdf_name"] = "lohn-synthetisch-kopie.pdf"
    # Gleiche Kontokennung erzwingen, damit die Regel greift.
    for r in (doppelt[0], zwilling):
        r["pdf_name"] = r["pdf_name"].replace(".pdf", "_123456.pdf")
    alle = doppelt + [zwilling]
    vorher = len(alle)
    mark_duplicate_positions(alle)
    assert len(alle) == vorher


def test_serialisierbar(lauf):
    """Die Zeilen muessen sich fuer CSV und HTML serialisieren lassen."""
    json.dumps(_zeilen(lauf), default=str)
