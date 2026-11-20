"""Rohtabelle: Zeilen, Zellen, Randtext und Beträge aus Wortboxen (261025-kme).
Alle Werte erfunden.

Bisher war ``rohtabelle.baue`` nur über die Leser getestet. Hier mit
erfundenen Wortboxen auf einer A4-Seite (595 × 842 pt).
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from extractors import rohtabelle
from extractors.numbers import parse_swiss_amount


def w(text, x0, top, bottom=None, x1=None):
    """Wortbox; ohne Angabe 6 pt Breite je Zeichen und 10 pt Höhe."""
    return {"text": text, "x0": float(x0),
            "x1": float(x1 if x1 is not None else x0 + 6 * len(text)),
            "top": float(top),
            "bottom": float(bottom if bottom is not None else top + 10)}


def baue(*woerter):
    return rohtabelle.baue({"pdf_name": "t.pdf", "pages": [
        {"page_num": 1, "width": 595, "height": 842, "words": list(woerter)}]})


def zelle(text, x0=100.0):
    z = rohtabelle.Zelle(text, x0, x0 + 40)
    return rohtabelle.Zeile(1, 100.0, x0, [z], (x0, 96.0, x0 + 40, 104.0))


# --- Zellen -----------------------------------------------------------------

def test_luecke_trennt_zellen():
    t = baue(w("Total", 100, 100, x1=130), w("1'234.50", 150, 100))
    assert [c.text for c in t.zeilen[0].zellen] == ["Total", "1'234.50"]
    t = baue(w("Total", 100, 100, x1=130), w("CHF", 138, 100))
    assert [c.text for c in t.zeilen[0].zellen] == ["Total CHF"]


# --- Zeilen: ungleiche Schriftgrade -----------------------------------------

def test_fette_zeile_neben_kleinem_betrag_bleibt_eine_zeile():
    """Mitten 3.5 pt auseinander, Kästen überlappen fast ganz — eine Zeile."""
    t = baue(w("Total", 100, 96, bottom=111, x1=140), w("1'234.50", 300, 103, bottom=111))
    assert len(t.zeilen) == 1
    assert [c.text for c in t.zeilen[0].zellen] == ["Total", "1'234.50"]


def test_zwei_gleich_grosse_zeilen_bleiben_getrennt():
    t = baue(w("Muster", 100, 100), w("Beispiel", 100, 112))
    assert len(t.zeilen) == 2


# --- Randtext -----------------------------------------------------------------

def test_einzelzeichen_am_rand_allein_faellt_weg():
    t = baue(w("A", 10, 300), w("Musterposten", 100, 100))
    assert [z.text for z in t.zeilen] == ["Musterposten"]


def test_zeilenanfang_am_rand_bleibt():
    """„1." bei x0 20 neben dem Text derselben Zeile ist ein Zeilenanfang."""
    t = baue(w("1.", 20, 100), w("Musterposten", 60, 100))
    assert t.zeilen[0].zellen[0].text == "1."
    assert t.zeilen[0].zellen[1].text == "Musterposten"


def test_senkrechter_randtext_neben_tabellenzeile_faellt_weg():
    stapel = [w(c, 10, top, bottom=top + 6) for c, top in
              zip("ABCD", (100, 107, 114, 121))]
    t = baue(*stapel, w("Tabellenwort", 300, 106, bottom=114), w("12.00", 400, 106, bottom=114))
    (z,) = t.zeilen
    assert z.zellen[0].text == "Tabellenwort"
    assert all(c.text not in "ABCD" for c in z.zellen)


# --- Beträge ------------------------------------------------------------------

def test_mehrere_betraege_in_einer_zelle():
    assert zelle("1'000.00 10.00").betraege() == [(0, Decimal("1000.00")),
                                                 (0, Decimal("10.00"))]
    assert zelle("1 000.00").betraege() == [(0, Decimal("1000.00"))]
    assert zelle("Total 1'000.00").betraege() == []
    assert zelle("2025").betraege() == []


@pytest.mark.parametrize("text", ["12,345", "1,234.50", "1'234.50", "1'500, 75", "1'365.--"])
def test_eine_zahl_logik(text):
    assert rohtabelle.betrag_in(text) == parse_swiss_amount(text)
