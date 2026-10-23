"""Phase-2-Tests für extractors/classifier.py — Score-Cascade + Tuple-Return.

Deckt D-D1..D4 (02-CONTEXT.md) ab: 4 Belegtypen, Tie-Detection,
UNSUPPORTED_TYPE bei Score==0, Lohnausweis-Tie-Wins-Stabilität.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from extractors.classifier import classify
from extractors.pdf_reader import read_pdf
from extractors.schema import ReasonCode

FIXTURES = Path(__file__).parent / "fixtures"


def _w(text: str) -> dict:
    return {"text": text, "page": 1, "x0": 0, "top": 0, "x1": 1, "bottom": 1}


def _words(*texts: str) -> list[dict]:
    return [_w(t) for t in texts]


# (a) Lohnausweis-Phase-1-Rückwärtskompatibilität.
def test_classify_lohnausweis_words():
    assert classify(_words("Lohnausweis", "Form", "11")) == ("lohnausweis", None)


# (b) Bank-Zinsausweis.
def test_classify_bank_zinsausweis_words():
    assert classify(_words("Steuerausweis", "Zinsausweis", "BANK-Z")) == (
        "bank_zinsausweis",
        None,
    )


# (c) KK-Prämienbescheinigung.
def test_classify_kk_words():
    assert classify(
        _words("Prämienbescheinigung", "Krankenkasse", "KK-H")
    ) == ("kk_praemienbescheinigung", None)


# (d) Säule 3a.
def test_classify_saeule_3a_words():
    assert classify(_words("Säule", "3a", "Bescheinigung", "STIFTUNG-V")) == (
        "saeule_3a",
        None,
    )


# (e) Tie zwischen zwei nicht-Lohnausweis-Typen → MULTIPLE_BELEGTYP_MATCH.
def test_classify_tie_bank_kk_returns_multiple_match():
    belegtyp, reason = classify(
        _words("Zinsausweis", "Prämienbescheinigung", "Foo", "Bar")
    )
    assert belegtyp == "unknown"
    assert reason == ReasonCode.MULTIPLE_BELEGTYP_MATCH


# (f) Score==0 für alle → UNSUPPORTED_TYPE.
def test_classify_unknown_returns_unsupported():
    assert classify(_words("Hallo", "Welt", "lorem", "ipsum")) == (
        "unknown",
        ReasonCode.UNSUPPORTED_TYPE,
    )


# (g) Lohnausweis-Tie-Wins: Lohnausweis am Tie beteiligt → Lohnausweis wins.
def test_classify_tie_with_lohnausweis_wins_lohnausweis():
    # Beide Patterns matchen je 1× — Lohnausweis muss gewinnen (Stabilität).
    assert classify(_words("Lohnausweis", "Säule", "3a")) == ("lohnausweis", None)


# (h) Empty word-list → UNSUPPORTED_TYPE.
def test_classify_empty_returns_unsupported():
    assert classify([]) == ("unknown", ReasonCode.UNSUPPORTED_TYPE)


# (i) Echtes Phase-2-Fixture-PDF — Smoke-Test.
def test_classify_real_bank_pdf():
    pdf = FIXTURES / "bank_zinsausweis_standard.pdf"
    if not pdf.exists():
        pytest.skip("Fixture fehlt — Plan 02-03 nicht ausgeführt")
    words = read_pdf(pdf)
    assert classify(words) == ("bank_zinsausweis", None)
