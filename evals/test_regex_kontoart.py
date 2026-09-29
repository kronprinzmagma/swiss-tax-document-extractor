"""Eval-Test für den Kontoart-Regex-Override in ``extractors/regex_extract``.

Deterministisch (kein Ollama). Nur synthetischer Text mit Fantasy-Daten.
"""
from __future__ import annotations

from extractors.regex_extract import regex_overrides


def test_kontoart_positiv_mehrteilig():
    text = "Auszug Kontoart / Währung Sparkonto Plus / CHF Saldo 1'234.50"
    overrides = regex_overrides(text, "bank_zinsausweis")
    assert overrides["kontotyp"] == "Sparkonto Plus"


def test_kontoart_positiv_einteilig():
    text = "Kontoart / Währung Privatkonto / CHF"
    overrides = regex_overrides(text, "bank_zinsausweis")
    assert overrides["kontotyp"] == "Privatkonto"


def test_kontoart_negativ_kein_label():
    text = "Auszug ohne Kontoart-Label, nur Saldo 1'234.50"
    overrides = regex_overrides(text, "bank_zinsausweis")
    assert "kontotyp" not in overrides
