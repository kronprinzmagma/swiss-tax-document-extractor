"""Leere oder abgeschnittene Ollama-Antworten werden beim Namen genannt.

Hintergrund: Beim Modellvergleich 2026-11-18 lieferte ein Kandidat einmal eine
leere Antwort; im Ergebnis stand nur ein Pydantic-Validierungsfehler, und der
LLM-Klassifikator fiel still auf ``unknown``. Alle Tests mocken ``ollama.chat``,
es wird kein Modell aufgerufen.
"""
from __future__ import annotations

import logging
from unittest.mock import patch

import pytest

from extractors.llm_classifier import classify_via_llm
from extractors.llm_extract import ModellAntwortFehler, ollama_antwort_text
from extractors.schema import ReasonCode


def _antwort(content: str, done_reason: str | None = "stop") -> dict:
    return {"message": {"content": content}, "done_reason": done_reason}


class _ObjektAntwort:
    def __init__(self, content: str, done_reason: str | None) -> None:
        self.message = type("M", (), {"content": content})()
        self.done_reason = done_reason


def test_normale_antwort_wird_durchgereicht() -> None:
    assert ollama_antwort_text(_antwort('{"x": 1}')) == '{"x": 1}'
    assert ollama_antwort_text(_ObjektAntwort('{"x": 1}', "stop")) == '{"x": 1}'


def test_fehlender_done_reason_ist_kein_fehler() -> None:
    assert ollama_antwort_text(_antwort('{"x": 1}', None)) == '{"x": 1}'


@pytest.mark.parametrize("content", ["", "   \n"])
def test_leere_antwort_heisst_modell_leer(content: str) -> None:
    with pytest.raises(ModellAntwortFehler, match="modell_leer"):
        ollama_antwort_text(_antwort(content))


def test_abbruch_wegen_laenge_heisst_modell_abgeschnitten() -> None:
    with pytest.raises(ModellAntwortFehler, match="modell_abgeschnitten"):
        ollama_antwort_text(_ObjektAntwort('{"x": ', "length"))


def test_klassifikator_meldet_leere_antwort_im_log(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING), patch("ollama.chat", return_value=_antwort("")):
        ergebnis = classify_via_llm([{"text": "Irgendein Beleg"}])
    assert ergebnis == ("unknown", ReasonCode.UNSUPPORTED_TYPE)
    assert "modell_leer" in caplog.text
