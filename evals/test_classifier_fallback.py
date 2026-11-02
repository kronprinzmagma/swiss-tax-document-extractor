"""LLM-Fallback-Klassifikator-Tests (CLS-02 step 3, Plan 03-06 Wave 5).

Verifiziert das Verhalten von :func:`extractors.classifier.classify` für den
Fall, dass keiner der 11 Header-Regex matcht (``max_score == 0``):

* Synthetisches Lohnausweis-PDF ohne Header-Marker → LLM-Fallback erkennt
  korrekt ``"lohnausweis"`` mit ``confidence ≥ 0.6`` (D-C6).
* Garbage-Text (Lorem ipsum) → LLM-Fallback liefert ``"unknown"`` mit
  ``UNSUPPORTED_TYPE``.
* In-process Schwellwert-Check: ``classify_via_llm`` mappt ``confidence <
  0.6`` UND explizites ``belegtyp == "unknown"`` jeweils auf ``("unknown",
  UNSUPPORTED_TYPE)`` — Pitfall 7 (RESEARCH §5).

Alle PDF-basierten Tests sind ``@pytest.mark.llm_full``, weil sie einen
Ollama-Call brauchen. Der Schwellwert-Check ist ein reiner Unit-Test ohne
Ollama (mockt ``ollama.chat`` via Monkey-Patch).
"""
from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import patch

import pytest
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from extractors.classifier import classify
from extractors.llm_classifier import (
    CLASSIFIER_FALLBACK_THRESHOLD,
    classify_via_llm,
)
from extractors.pdf_reader import read_pdf
from extractors.schema import ClassifierFallbackResult, ReasonCode

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module", autouse=True)
def _generate_fixtures() -> None:
    """Stellt sicher, dass die LLM-Fallback-Fixture generiert ist."""
    if not (FIXTURES / "lohnausweis_no_header.pdf").exists():
        from evals.generate import main as gen_main

        gen_main()


def _make_garbage_pdf(out_path: Path) -> None:
    """Erzeugt ein PDF mit reinem Lorem-Ipsum — keiner der 11 Regex matcht."""
    c = canvas.Canvas(str(out_path), pagesize=A4, invariant=1)
    c.setProducer("steuer-extraktor evals/test_classifier_fallback")
    c.setFont("Helvetica", 12)
    y = 800
    for _ in range(10):
        c.drawString(60, y, "Lorem ipsum dolor sit amet consectetur adipiscing elit")
        y -= 22
    c.showPage()
    c.save()


# ---------------------------------------------------------------------------
# Unit-Test: Threshold-Mapping ohne Ollama (synchron, schnell).
# ---------------------------------------------------------------------------


def test_classify_via_llm_threshold_maps_low_confidence_to_unknown() -> None:
    """``confidence < 0.6`` ⇒ ``("unknown", UNSUPPORTED_TYPE)`` (D-C4, Pitfall 7)."""
    low_conf_result = ClassifierFallbackResult(
        belegtyp="lohnausweis", confidence=0.4, reasoning="unsicher"
    )

    class _FakeMsg:
        content = low_conf_result.model_dump_json()

    class _FakeResponse:
        message = _FakeMsg()

    with patch("ollama.chat", return_value=_FakeResponse()):
        belegtyp, reason = classify_via_llm([{"text": "irgendwas"}])
    assert belegtyp == "unknown"
    assert reason == ReasonCode.UNSUPPORTED_TYPE


def test_classify_via_llm_explicit_unknown_maps_to_unsupported() -> None:
    """``belegtyp == "unknown"`` ⇒ ``("unknown", UNSUPPORTED_TYPE)`` (D-C5)."""
    unknown_result = ClassifierFallbackResult(
        belegtyp="unknown", confidence=0.9, reasoning="kein Beleg-Typ erkennbar"
    )

    class _FakeMsg:
        content = unknown_result.model_dump_json()

    class _FakeResponse:
        message = _FakeMsg()

    with patch("ollama.chat", return_value=_FakeResponse()):
        belegtyp, reason = classify_via_llm([{"text": "irgendwas"}])
    assert belegtyp == "unknown"
    assert reason == ReasonCode.UNSUPPORTED_TYPE


def test_classify_via_llm_ollama_exception_graceful() -> None:
    """Ollama-Exception ⇒ ``("unknown", UNSUPPORTED_TYPE)`` — niemals Crash."""
    with patch("ollama.chat", side_effect=RuntimeError("Ollama down")):
        belegtyp, reason = classify_via_llm([{"text": "x"}])
    assert belegtyp == "unknown"
    assert reason == ReasonCode.UNSUPPORTED_TYPE


def test_classifier_fallback_threshold_constant() -> None:
    """Sanity: Threshold ist 0.6 wie in D-C4 spezifiziert."""
    assert CLASSIFIER_FALLBACK_THRESHOLD == 0.6


# ---------------------------------------------------------------------------
# End-to-End-LLM-Tests (brauchen Ollama — @llm_full).
# ---------------------------------------------------------------------------


@pytest.mark.llm_full
def test_classifier_fallback_recognizes_headerless_lohnausweis(
    model_check: dict,
) -> None:
    """D-C6: PDF ohne ``Lohnausweis``-Header — LLM-Fallback erkennt korrekt.

    Die Fixture ``lohnausweis_no_header.pdf`` enthält ALLE Pos-Labels eines
    Lohnausweises (Pos 8 Bruttolohn, Pos 9 AHV, Pos 10.1 BVG, Pos 11
    Nettolohn), aber KEINEN der 11 Header-Regex (``Lohnausweis``, ``Form
    11``, ``Bank-Steuerausweis``, ``Krankenkasse``, etc.). ``classify()``
    muss daher in den LLM-Fallback fallen und das Dokument als
    ``"lohnausweis"`` klassifizieren.
    """
    pdf = FIXTURES / "lohnausweis_no_header.pdf"
    words = read_pdf(pdf)
    belegtyp, reason = classify(words)
    assert belegtyp == "lohnausweis", (
        f"LLM-Fallback erkannte den header-losen Lohnausweis NICHT: "
        f"belegtyp={belegtyp!r}, reason={reason!r}"
    )
    assert reason is None


@pytest.mark.llm_full
def test_classifier_fallback_garbage_text_returns_unknown(
    tmp_path: Path,
    model_check: dict,
) -> None:
    """Garbage-PDF (Lorem ipsum) ⇒ ``("unknown", UNSUPPORTED_TYPE)``."""
    garbage_pdf = tmp_path / "garbage.pdf"
    _make_garbage_pdf(garbage_pdf)
    words = read_pdf(garbage_pdf)
    belegtyp, reason = classify(words)
    assert belegtyp == "unknown", (
        f"Lorem-Ipsum-PDF wurde als {belegtyp!r} klassifiziert "
        f"(erwartet: 'unknown')"
    )
    assert reason == ReasonCode.UNSUPPORTED_TYPE
