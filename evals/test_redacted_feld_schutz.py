"""``*_redacted``-Felder nehmen nie eine volle Konto-/Depot-Nummer oder IBAN auf.

Hintergrund (Code-Review 2026-11-19): #48 verlangt das nur im Prompt. Ein
Modell haelt sich nicht immer daran; der Code leert solche Werte darum selbst.
Alle Nummern sind synthetisch (offizielle Beispiel-IBAN).
"""
from __future__ import annotations

import json

import ollama
import pytest

from extractors.llm_extract import extract, nicht_maskierte_nummer
from extractors.schema import BankZinsausweisRaw


@pytest.mark.parametrize("wert, erwartet", [
    ("XXXX-1234", False),
    ("****1234", False),
    ("CH93 XXXX XXXX 5295 7", False),
    ("", False),
    ("Sparkonto", False),
    ("CH93 0076 2011 6238 5295 7", True),  # gitleaks:allow
    ("80-123456-7", True),
    ("1234.5678.90", True),
])
def test_nicht_maskierte_nummer(wert: str, erwartet: bool) -> None:
    assert nicht_maskierte_nummer(wert) is erwartet


class _Msg:
    def __init__(self, content: str) -> None:
        self.content = content


class _Antwort:
    def __init__(self, content: str) -> None:
        self.message = _Msg(content)
        self.done_reason = "stop"


def _bank_antwort(konto: str) -> str:
    def tf(v: str) -> dict:
        return {"value": v, "tag_refs": []}
    return json.dumps({
        "institut": tf("Beispielbank"), "kontoinhaber_name": tf("Hans Muster"),
        "bruttoertrag": tf("10.00"), "vermoegensstand_3112": tf("1000.00"),
        "verrechnungssteuer": tf("3.50"), "jahr": tf("2025"),
        "konto_nr_redacted": tf(konto),
    })


@pytest.mark.parametrize("konto, bleibt", [
    ("CH93 0076 2011 6238 5295 7", False),  # gitleaks:allow
    ("XXXX-1234", True),
])
def test_extract_leert_volle_kontonummer(monkeypatch, konto: str, bleibt: bool) -> None:
    monkeypatch.setattr(ollama, "chat", lambda **kw: _Antwort(_bank_antwort(konto)))
    res = extract("[T0] Beispielbank Zinsausweis", schema_class=BankZinsausweisRaw)
    if bleibt:
        assert res.konto_nr_redacted is not None
        assert res.konto_nr_redacted.value == konto
    else:
        assert res.konto_nr_redacted is None
