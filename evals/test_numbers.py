"""Unit-Tests für extractors/numbers.py — parse_swiss_amount, parse_swiss_date, normalize.

Deckt die in 01-RESEARCH.md §parse_swiss_amount und §Pitfall 2 dokumentierten
Edge-Cases ab: Apostroph-Varianten (U+0027/U+2019/U+2018), NBSP/NNBSP/THINSP,
Komma-Dezimaltrenner, Currency-Präfix, Null-Strich-Repräsentationen.
"""
from decimal import Decimal

import pytest

from extractors.numbers import normalize, parse_swiss_amount, parse_swiss_date


@pytest.mark.parametrize(
    "inp,expected",
    [
        ("1'234.50", Decimal("1234.50")),
        ("1’234.50", Decimal("1234.50")),  # U+2019 right single quote
        ("1 234.50", Decimal("1234.50")),  # NBSP
        ("1 234.50", Decimal("1234.50")),  # NNBSP
        ("1 234,50", Decimal("1234.50")),  # Space + Komma als Dezimaltrenner
        ("CHF 1'234.50", Decimal("1234.50")),
        ("Fr. 1'234.50", Decimal("1234.50")),
        ("95'400.00", Decimal("95400.00")),
        ("115’200.00", Decimal("115200.00")),  # F2-Fixture-Form
        ("123'456'789.00", Decimal("123456789.00")),
        ("0.00", Decimal("0.00")),
        ("-1'234.50", Decimal("-1234.50")),
    ],
)
def test_parse_amount_roundtrip(inp, expected):
    assert parse_swiss_amount(inp) == expected


@pytest.mark.parametrize("inp", [None, "", "-", "–", "–.–", "-.-", ".--"])
def test_parse_amount_null(inp):
    assert parse_swiss_amount(inp) is None


def test_parse_amount_invalid():
    with pytest.raises(ValueError):
        parse_swiss_amount("nicht-ein-betrag")


@pytest.mark.parametrize(
    "inp,iso",
    [
        ("01.01.2024", "2024-01-01"),
        ("31.12.2024", "2024-12-31"),
        ("2024-01-01", "2024-01-01"),
        ("1.1.2024", "2024-01-01"),
        ("9.7.2024", "2024-07-09"),
    ],
)
def test_parse_date(inp, iso):
    assert parse_swiss_date(inp) == iso


@pytest.mark.parametrize("inp", ["01/01/2024", "ungültig", "", "2024.01.01"])
def test_parse_date_invalid(inp):
    with pytest.raises(ValueError):
        parse_swiss_date(inp)


def test_normalize_apostroph_equivalence():
    # U+0027, U+2019, NBSP — alle drei MÜSSEN identisch normalisieren
    assert normalize("1'234.50") == normalize("1’234.50") == normalize("1 234.50")


def test_normalize_nnbsp_equivalence():
    # NNBSP (U+202F) ist die häufigste Form in Schweizer Tausender-Trennung neuerer PDFs
    assert normalize("1'234.50") == normalize("1 234.50")


def test_normalize_left_quote_equivalence():
    # U+2018 left single quote — selten, aber möglich
    assert normalize("1'234") == normalize("1‘234")


# --------------------------------------------------------------------------- #
# R6: Müllwert-Guard (260612-m8t)                                              #
# --------------------------------------------------------------------------- #
# LLM-Halluzinationen liefern manchmal Müllwerte wie "{CHF 8.718}". Der frühere
# Punkt-Tausender-Pfad hätte "8.718" als 8718 interpretiert — falscher Betrag.
# Klammern/Buchstaben (ausser Currency-Präfix) → ValueError, kein Best-Effort.

@pytest.mark.parametrize(
    "inp",
    [
        "{CHF 8.718}",
        "{8.718}",
        "[1234.50]",
        "1234.50abc",
        "ca. 1234.50",
        "8.718x",
    ],
)
def test_parse_amount_muellwert_raises(inp):
    with pytest.raises(ValueError):
        parse_swiss_amount(inp)


def test_parse_amount_currency_praefix_bleibt_gueltig():
    # Echter Currency-Präfix darf NICHT als Müll-Buchstabe gewertet werden.
    assert parse_swiss_amount("CHF 1'234.50") == Decimal("1234.50")
    assert parse_swiss_amount("Fr. 1'234.50") == Decimal("1234.50")


def test_normalize_whitespace_squeeze():
    assert normalize("ACME AG") == normalize("acme  ag")


def test_normalize_idempotent():
    assert normalize(normalize("1'234.50")) == normalize("1'234.50")


def test_normalize_keeps_currency_prefix():
    # normalize strippt Whitespace + Apostrophe, aber nicht das CHF — Konsistenz prüfen
    assert "chf" in normalize("CHF 1'234.50")


# --------------------------------------------------------------------------- #
# Punkt-Tausendertrennung (Quick-Fix nach Basis-Tabelle-Triage)                 #
# --------------------------------------------------------------------------- #

def test_parse_dot_thousands_einfach():
    # "7.793" = 7'793 (3 Ziffern nach Punkt → Tausender, nicht Dezimal)
    assert parse_swiss_amount("7.793") == Decimal("7793")


def test_parse_dot_thousands_mehrgruppen():
    assert parse_swiss_amount("1.234.567") == Decimal("1234567")


def test_parse_dot_thousands_negativ():
    assert parse_swiss_amount("-12.211") == Decimal("-12211")


def test_parse_zwei_dezimalstellen_bleibt_dezimal():
    # 2 Nachkommastellen → klassischer Rappen-Betrag
    assert parse_swiss_amount("5487.50") == Decimal("5487.50")


def test_parse_eine_dezimalstelle_bleibt_dezimal():
    assert parse_swiss_amount("9.4") == Decimal("9.4")


def test_parse_apostroph_und_dezimal_unveraendert():
    assert parse_swiss_amount("1'234.50") == Decimal("1234.50")


# --------------------------------------------------------------------------- #
# Finding 1: Faktor-1000-Bug — "0.125" darf NIE zu 125 werden                    #
# --------------------------------------------------------------------------- #

def test_parse_fuehrende_null_kein_faktor_1000_punkt():
    # "0.125" hat führende 0 → KEINE Punkt-Tausendertrennung; 3 Nachkommastellen
    # sind kein gültiger CHF-Rappen-Betrag → ValueError (nicht Decimal("125")).
    with pytest.raises(ValueError):
        parse_swiss_amount("0.125")


def test_parse_fuehrende_null_kein_faktor_1000_komma():
    # "0,125" wird zu "0.125" normalisiert → derselbe Pfad → ValueError, nie 125.
    with pytest.raises(ValueError):
        parse_swiss_amount("0,125")


def test_parse_dot_thousands_fuehrende_ziffer_bleibt():
    # Regression: "7.793" bleibt 7793, "1.234.567" bleibt 1234567 (führende Ziffer >= 1).
    assert parse_swiss_amount("7.793") == Decimal("7793")
    assert parse_swiss_amount("1.234.567") == Decimal("1234567")
