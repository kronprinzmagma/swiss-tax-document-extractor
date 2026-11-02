"""pytest-Konfiguration für Eval-Suite.

Stellt session-scope `model_check` bereit, der den SHA-Digest des aktiven
Ollama-Modells gegen `evals/baseline_model.json` prüft (D-B4 Drift-Protection).

Verhalten:
- Ollama nicht erreichbar / Modell nicht gepullt → `pytest.skip` (CI ohne Ollama
  blockiert nicht den ganzen Lauf).
- Erreichbar, aber Digest-Mismatch → `pytest.fail` (HARTER Fail, zwingt zu
  manueller Re-Baseline-Review).
- Übereinstimmung → liefert {model, digest} an Tests, die die Fixture nutzen.

Da der Baseline-Digest in der Kurzform (12 Hex-Zeichen aus `ollama list`)
gepinnt ist (`digest_kind: "short"`), wird der Vergleich als Substring-Match
durchgeführt: der Baseline-Wert muss im aktuellen Digest enthalten sein.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

BASELINE_PATH = Path(__file__).parent / "baseline_model.json"


@pytest.fixture(scope="session")
def model_check() -> dict[str, str]:
    """Failed laut bei Modell-Drift, sonst gibt {model, digest} zurück."""
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    try:
        from extractors.llm_extract import get_model_digest

        actual = get_model_digest(baseline["model"])
    except Exception as exc:
        pytest.skip(f"Ollama nicht erreichbar — model_check übersprungen ({exc!r})")

    baseline_digest = str(baseline["digest"])
    actual_digest = str(actual["digest"])

    # Bei Kurzform-Baseline (digest_kind == "short") prüfen wir per Substring,
    # weil `ollama show` ggf. die Vollform zurückgibt und `ollama list` die Kurzform.
    if baseline.get("digest_kind") == "short":
        ok = baseline_digest in actual_digest or actual_digest in baseline_digest
    else:
        ok = actual_digest == baseline_digest

    if not ok:
        pytest.fail(
            f"Modell-Drift: baseline={baseline_digest[:12]}, "
            f"actual={actual_digest[:12]}. Re-Baseline nach manueller Review der Eval-Outputs."
        )
    return actual
