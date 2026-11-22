"""Ollamas ``format=``-Schema fuehrt jedes Feld als Pflichtschluessel.

Hintergrund (Modellvergleich 2026-11-20): Pydantic listet Felder mit Default
None nicht unter ``required``. gemma4 und qwen3 liessen solche Schluessel im
strukturierten Modus weg, auch wenn der Wert im Text stand (BVG-Abzug im
Lohnausweis, Steuerbefreiungs-Hinweis der Spendenquittung). Der Wert darf
weiterhin null sein; nur der Schluessel muss erscheinen.
"""
from __future__ import annotations

import contextlib
import json

import ollama

from extractors.llm_extract import format_schema
from extractors.schema import ClassifierFallbackResult, LohnausweisRaw, SpendenquittungRaw


def test_alle_felder_pflicht_und_null_bleibt_erlaubt() -> None:
    schema = format_schema(LohnausweisRaw)
    assert set(schema["required"]) == set(schema["properties"])
    assert "bvg_abzug_pos10a" in schema["required"]
    bvg = schema["properties"]["bvg_abzug_pos10a"]
    assert {"type": "null"} in bvg["anyOf"]


def test_pydantic_schema_bleibt_unveraendert() -> None:
    format_schema(LohnausweisRaw)
    assert "bvg_abzug_pos10a" not in LohnausweisRaw.model_json_schema().get("required", [])


class _Msg:
    def __init__(self, content: str) -> None:
        self.content = content


class _Antwort:
    def __init__(self, content: str) -> None:
        self.message = _Msg(content)
        self.done_reason = "stop"


def test_extract_und_klassifikator_nutzen_format_schema(monkeypatch) -> None:
    from extractors.llm_classifier import classify_via_llm
    from extractors.llm_extract import extract

    aufrufe: list[dict] = []

    def chat(**kw):
        aufrufe.append(kw)
        if kw["format"].get("title") == "ClassifierFallbackResult":
            res = ClassifierFallbackResult(belegtyp="unknown", confidence=0.9, reasoning="-")
            return _Antwort(res.model_dump_json())
        return _Antwort(json.dumps({}))

    monkeypatch.setattr(ollama, "chat", chat)
    with contextlib.suppress(Exception):  # nur der Aufruf interessiert
        extract("[T0] dummy", schema_class=SpendenquittungRaw)
    classify_via_llm([{"text": "irgendwas"}])
    assert len(aufrufe) == 2
    for a in aufrufe:
        assert set(a["format"]["required"]) == set(a["format"]["properties"])
