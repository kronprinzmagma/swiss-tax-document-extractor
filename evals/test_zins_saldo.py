"""Tests der Zins-zu-Saldo-Plausibilität (260904-rmx).

Aufgedeckt an einem echten Beleg: pdfplumber hatte den Saldo an einer
Kerning-Lücke zerteilt, die führende Ziffer fehlte. Der Wert war verankert und
grün — auffällig war nur das Verhältnis.
"""
from __future__ import annotations

from extractors.steuer_zielmodell import rule_zins_zu_saldo_plausibel as regel


def test_realistischer_zins_ohne_befund():
    assert regel({"vermoegensstand_3112": "3877.15", "bruttoertrag": "9.45"}) == []


def test_fehlende_ziffer_im_saldo_faellt_auf():
    """Der reale Fall: 23.79 Zins auf 71.88 Saldo waeren 33 Prozent."""
    b = regel({"vermoegensstand_3112": "71.88", "bruttoertrag": "23.79"})
    assert len(b) == 1
    assert "Ziffer" in b[0]


def test_korrigierter_saldo_ohne_befund():
    """Nach dem Join stimmt das Verhaeltnis."""
    assert regel({"vermoegensstand_3112": "371.88", "bruttoertrag": "23.79"}) == []


def test_unerheblicher_zins_ausgenommen():
    """Unter 5 CHF Zins ist das Verhaeltnis rauschanfaellig und egal."""
    assert regel({"vermoegensstand_3112": "70.42", "bruttoertrag": "1.00"}) == []


def test_ohne_zins_kein_befund():
    assert regel({"vermoegensstand_3112": "5000.00", "bruttoertrag": "0.00"}) == []


def test_fehlende_werte_kein_befund():
    assert regel({}) == []
    assert regel({"vermoegensstand_3112": "5000.00"}) == []


def test_regel_ist_registriert():
    from extractors.steuer_zielmodell import SPECS
    spec = SPECS["bank_zinsausweis"]
    assert regel in spec.plausibility_rules
