"""Alle Ollama-Aufrufe schalten das «Denken» explizit aus (think=False).

Hintergrund (Modellvergleich 2026-11-19): Ein Denk-Modell wie qwen3.5 denkt
ohne diese Angabe standardmaessig und verbraucht das Tokenbudget mit
Denktext; die Antwort bricht dann ab oder bleibt leer. Mit think=False
bleibt ein Modellwechsel ohne stillen Qualitaetsverlust.
"""
from __future__ import annotations

import contextlib
import json
import sys
from pathlib import Path

import ollama

from extractors.schema import ClassifierFallbackResult

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))


class _Msg:
    def __init__(self, content: str) -> None:
        self.content = content


class _Antwort:
    def __init__(self, content: str) -> None:
        self.message = _Msg(content)
        self.done_reason = "stop"


def _aufzeichnen(monkeypatch, content: str) -> list[dict]:
    aufrufe: list[dict] = []

    def chat(**kw):
        aufrufe.append(kw)
        return _Antwort(content)

    monkeypatch.setattr(ollama, "chat", chat)
    return aufrufe


def test_extract_schaltet_denken_aus(monkeypatch) -> None:
    from extractors.llm_extract import extract
    from extractors.schema import SpendenquittungRaw

    tf = {"value": "x", "tag_refs": []}
    payload = {"empfaenger": tf, "spender_name": tf, "betrag": {"value": "50.00", "tag_refs": []},
               "jahr": {"value": "2024", "tag_refs": []}}
    aufrufe = _aufzeichnen(monkeypatch, json.dumps(payload))
    with contextlib.suppress(Exception):  # nur der Aufruf interessiert
        extract("[T0] dummy", schema_class=SpendenquittungRaw)
    assert aufrufe and aufrufe[0]["think"] is False


def test_klassifikator_schaltet_denken_aus(monkeypatch) -> None:
    from extractors.llm_classifier import classify_via_llm

    res = ClassifierFallbackResult(belegtyp="unknown", confidence=0.9, reasoning="-")
    aufrufe = _aufzeichnen(monkeypatch, res.model_dump_json())
    classify_via_llm([{"text": "irgendwas"}])
    assert aufrufe and aufrufe[0]["think"] is False


def test_anonymizer_schaltet_denken_aus(monkeypatch) -> None:
    import anonymize_belege as ab

    aufrufe = _aufzeichnen(monkeypatch, "[]")
    ab.detect_institutions_via_llm("Beispiel AG")
    with contextlib.suppress(Exception):  # Ausfallpfad ist hier egal
        ab.detect_persons_via_llm("Hans Muster")
    assert len(aufrufe) == 2
    assert all(a["think"] is False for a in aufrufe)
