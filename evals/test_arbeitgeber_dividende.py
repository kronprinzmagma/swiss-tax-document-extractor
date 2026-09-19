"""Tests für Arbeitgeber-Plausi + Dividenden-Doppelzählung (R7, R8; 260612-m8t).

R7a: Arbeitgeber = family.yaml-Person (mit Anrede, Reihenfolge Nachname
     Vorname) → erkannt als Person.
R7b: Arbeitgeber = reine Adresse ("Musterstrasse 34, 8000 Musterhausen")
     → erkannt als Adresse.
R8:  bank_zinsausweis-bruttoertrag, dessen Anker-Snippet "Dividende" enthält
     → manual_review:dividende_im_kontoauszug.

Ausschliesslich synthetische Daten — Hans Muster / ROCHE-GS-Synthetik.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import process_samples_full as psf


class _Member:
    def __init__(self, first_name, last_name, role="elternteil1"):
        self.first_name = first_name
        self.last_name = last_name
        self.role = role


class _Family:
    def __init__(self, members):
        self.members = members


# --------------------------------------------------------------------------- #
# R7a: Arbeitgeber ist eine family.yaml-Person                                 #
# --------------------------------------------------------------------------- #
def test_arbeitgeber_familienname_mit_anrede_und_reihenfolge(monkeypatch):
    fam = _Family([_Member("Hans", "Muster")])
    monkeypatch.setattr("extractors.family.load_family", lambda *a, **k: fam)
    # Anrede + Reihenfolge Nachname Vorname.
    assert psf._arbeitgeber_enthaelt_familienname("Herr Muster Hans") is True
    # Auch Reihenfolge Vorname Nachname.
    assert psf._arbeitgeber_enthaelt_familienname("Hans Muster AG") is True


def test_echte_firma_kein_familienname(monkeypatch):
    fam = _Family([_Member("Hans", "Muster")])
    monkeypatch.setattr("extractors.family.load_family", lambda *a, **k: fam)
    assert psf._arbeitgeber_enthaelt_familienname("ACME AG") is False


# --------------------------------------------------------------------------- #
# R7b: Arbeitgeber ist eine reine Adresse                                      #
# --------------------------------------------------------------------------- #
def test_arbeitgeber_ist_adresse_strasse_hausnummer():
    assert psf._arbeitgeber_ist_adresse("Musterstrasse 34, 8000 Musterhausen") is True


def test_arbeitgeber_ist_adresse_weitere_strassentypen():
    assert psf._arbeitgeber_ist_adresse("Bahnhofweg 1, 3000 Musterhausen") is True
    assert psf._arbeitgeber_ist_adresse("Dorfplatz 5") is True


def test_firma_ist_keine_adresse():
    assert psf._arbeitgeber_ist_adresse("ACME AG") is False
    assert psf._arbeitgeber_ist_adresse("BANK-A") is False


# --------------------------------------------------------------------------- #
# R8: Dividende im Kontoauszug → Doppelzählungsgefahr                          #
# --------------------------------------------------------------------------- #
def test_bruttoertrag_snippet_dividende_erkannt():
    assert psf.bruttoertrag_snippet_is_dividende("Dividende ROCHE GS 116.70") is True


def test_bruttoertrag_snippet_ohne_dividende():
    assert psf.bruttoertrag_snippet_is_dividende("Bruttozins 116.70") is False
    assert psf.bruttoertrag_snippet_is_dividende("") is False


def test_harden_dividende_demotiert_feld():
    fields = [{
        "feld": "bruttoertrag", "value": "116.70",
        "snippet": "Dividende ROCHE GS 116.70",
        "anchor_valid": True, "bbox": [0, 0, 10, 10], "page": 1,
    }]
    psf.harden_dividende_bruttoertrag(fields)
    assert fields[0]["value"] == "manual_review:dividende_im_kontoauszug"
    assert fields[0]["anchor_valid"] is False


def test_harden_dividende_laesst_echten_zins():
    fields = [{
        "feld": "bruttoertrag", "value": "116.70",
        "snippet": "Bruttozins 116.70",
        "anchor_valid": True, "bbox": [0, 0, 10, 10], "page": 1,
    }]
    psf.harden_dividende_bruttoertrag(fields)
    assert fields[0]["value"] == "116.70"


def test_harden_dividende_ignoriert_manual_review_und_null():
    fields = [
        {"feld": "bruttoertrag", "value": "manual_review:vrs_label_fehlt",
         "snippet": "Dividende 0.00", "anchor_valid": False},
        {"feld": "bruttoertrag", "value": "0.00",
         "snippet": "Dividende 0.00", "anchor_valid": True},
    ]
    psf.harden_dividende_bruttoertrag(fields)
    assert fields[0]["value"] == "manual_review:vrs_label_fehlt"
    # 0.00 ist kein echter Ertrag → kein Doppelzählungsrisiko, bleibt.
    assert fields[1]["value"] == "0.00"


# --------------------------------------------------------------------------- #
# A4-Erweiterung (260630-dsn): bruttoertrag ankert auf Summenzeile             #
# "Total Gutschrift", Dividende steht auf separater Detailzeile derselben      #
# Seite. Seiten-Kontext + Gutschrift-Anker → Doppelzählungs-Demotion.          #
# --------------------------------------------------------------------------- #
def test_harden_dividende_summenzeile_mit_seiten_kontext():
    # Anker-Zeile (top=180): "Total Gutschrift 116.70" — kein Zins-Label.
    # Detailzeile (top=251): "Dividende ROCHE GS 116.70" — separate Zeile.
    words = [
        {"text": "Total", "top": 180.0, "x0": 50, "page": 2},
        {"text": "Gutschrift", "top": 180.0, "x0": 90, "page": 2},
        {"text": "116.70", "top": 180.0, "x0": 207, "page": 2},
        {"text": "Dividende", "top": 251.0, "x0": 50, "page": 2},
        {"text": "ROCHE", "top": 251.0, "x0": 100, "page": 2},
        {"text": "GS", "top": 251.0, "x0": 140, "page": 2},
        {"text": "116.70", "top": 251.0, "x0": 207, "page": 2},
    ]
    fields = [{
        "feld": "bruttoertrag", "value": "116.70",
        "snippet": "116.70",  # enger Anker, kein "Dividende" darin
        "anchor_valid": True, "bbox": [207.6, 180.77, 235.1, 189.77], "page": 2,
    }]
    psf.harden_dividende_bruttoertrag(fields, words=words)
    assert fields[0]["value"] == "manual_review:dividende_im_kontoauszug"
    assert fields[0]["anchor_valid"] is False


def test_harden_dividende_summenzeile_ohne_dividende_bleibt():
    # Gutschrift-Summenzeile, aber KEINE Dividende auf der Seite → echter
    # Ertrag, bleibt unangetastet (konservativ, keine Über-Demotion).
    words = [
        {"text": "Total", "top": 180.0, "x0": 50, "page": 2},
        {"text": "Gutschrift", "top": 180.0, "x0": 90, "page": 2},
        {"text": "116.70", "top": 180.0, "x0": 207, "page": 2},
        {"text": "Zinsgutschrift", "top": 251.0, "x0": 50, "page": 2},
        {"text": "116.70", "top": 251.0, "x0": 207, "page": 2},
    ]
    fields = [{
        "feld": "bruttoertrag", "value": "116.70", "snippet": "116.70",
        "anchor_valid": True, "bbox": [207.6, 180.77, 235.1, 189.77], "page": 2,
    }]
    psf.harden_dividende_bruttoertrag(fields, words=words)
    assert fields[0]["value"] == "116.70"
