"""Tests für Modell-Drift-Protection (D-B4, EVL-05).

Prüft, dass ``baseline_model.json`` valide Felder hat und dass
``get_model_digest`` einen vergleichbaren Digest liefert. Falls Ollama
nicht erreichbar ist, wird der Live-Test geskippt (CI-Robustheit).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

BASELINE = Path(__file__).parent / "baseline_model.json"


def test_baseline_file_exists_and_valid() -> None:
    b = json.loads(BASELINE.read_text())
    assert b["model"] == "qwen2.5:7b-instruct-q4_K_M"
    assert len(b["digest"]) >= 12


def test_get_model_digest_or_skip() -> None:
    try:
        from extractors.llm_extract import get_model_digest

        info = get_model_digest("qwen2.5:7b-instruct-q4_K_M")
    except Exception as exc:
        pytest.skip(f"Ollama nicht erreichbar: {exc!r}")
    assert "model" in info and "digest" in info
    assert len(info["digest"]) >= 12
