"""Unit-Tests für extractors/classifier.py — Header-Regex (Phase 1, Tuple-Return migriert in Plan 02-04)."""
import pytest

from extractors.classifier import LOHNAUSWEIS_HEADER_RE, classify
from extractors.schema import ReasonCode


def _w(text: str) -> dict:
    return {"text": text, "page": 1, "x0": 0, "top": 0, "x1": 1, "bottom": 1}


@pytest.mark.parametrize(
    "words,expected",
    [
        ([_w("Lohnausweis"), _w("Form"), _w("11")], ("lohnausweis", None)),
        ([_w("LOHNAUSWEIS")], ("lohnausweis", None)),
        ([_w("Certificat"), _w("de"), _w("salaire")], ("lohnausweis", None)),
        ([_w("Salary"), _w("Statement")], ("lohnausweis", None)),
        # Phase-1-Annahme war: "Bank-Zinsausweis" → unknown. Phase 2 erkennt
        # das tatsächlich als Bank — der Test wird auf das neue Verhalten
        # migriert (RESEARCH-Korrektur: Score-Cascade greift).
        ([_w("Bank-Zinsausweis"), _w("BANK-U")], ("bank_zinsausweis", None)),
        # Phase-1+2-Annahme war: "Quittung Spende" → unknown. Phase 3 Wave 3
        # (Plan 03-04) erkennt "Spende" als Spendenquittung — Test auf das
        # neue Verhalten migriert.
        ([_w("Quittung"), _w("Spende")], ("spenden", None)),
        # Phase 3 Wave 3: weitere belegtypen-spezifische Header.
        ([_w("Wertschriftenverzeichnis"), _w("BANK-U")], ("wertschriftenverzeichnis", None)),
        ([_w("Depot-Auszug"), _w("BANK-Z")], ("wertschriftenverzeichnis", None)),
        ([_w("Spendenbescheinigung"), _w("WWF")], ("spenden", None)),
        ([_w("Zuwendungsbestätigung"), _w("Stiftung")], ("spenden", None)),
        ([_w("Kursbestätigung"), _w("EB"), _w("Zürich")], ("berufsauslagen", None)),
        ([_w("Weiterbildung"), _w("HSG")], ("berufsauslagen", None)),
        ([], ("unknown", ReasonCode.UNSUPPORTED_TYPE)),
    ],
)
def test_classify(words, expected):
    assert classify(words) == expected


def test_regex_case_insensitive():
    assert LOHNAUSWEIS_HEADER_RE.search("lohnausweis")
    assert LOHNAUSWEIS_HEADER_RE.search("LOHNAUSWEIS")
    assert LOHNAUSWEIS_HEADER_RE.search("Form 11")
    assert LOHNAUSWEIS_HEADER_RE.search("Salary Statement")
    assert LOHNAUSWEIS_HEADER_RE.search("Certificat de salaire")


def test_regex_no_false_positive():
    # "Bank-Zinsausweis" matcht das Lohnausweis-Pattern weiterhin nicht.
    assert not LOHNAUSWEIS_HEADER_RE.search("Bank-Zinsausweis 2024")
    assert not LOHNAUSWEIS_HEADER_RE.search("Quittung")


# --------------------------------------------------------------------------- #
# E5 — Freizügigkeitskonto-Klassifikation (Quick 260612-l2g). Synthetisch.     #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "words",
    [
        [_w("Freizügigkeitsstiftung"), _w("Guthaben")],
        [_w("Rubrik"), _w("FZK")],
        [_w("Vorsorgetechnische"), _w("Daten")],
        # OCR-Varianten (ü → u).
        [_w("Fronzugigkeitsstiftung")],
        [_w("Freizugigkeit")],
        # FZK trägt Bank-Vokabular, gewinnt trotzdem über bank_zinsausweis.
        [_w("Saldoverzeichnis"), _w("Freizügigkeitskonto"), _w("Zinsabschluss")],
    ],
)
def test_classify_freizuegigkeitskonto(words):
    assert classify(words) == ("freizuegigkeitskonto", None)


def test_freizuegigkeit_schlaegt_bank():
    """FZK-Vorrang: Bank-Header allein wäre bank_zinsausweis, mit FZK gewinnt FZK."""
    bank_only = [_w("Saldoverzeichnis"), _w("Zinsabschluss")]
    assert classify(bank_only) == ("bank_zinsausweis", None)
    mit_fzk = bank_only + [_w("Freizügigkeitsstiftung")]
    assert classify(mit_fzk) == ("freizuegigkeitskonto", None)
