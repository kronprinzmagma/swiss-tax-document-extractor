"""Tests für die Mehrkonten-Total-Heuristik (R4, 260612-m8t).

bank_zinsausweis-Belege mit ≥2 Endsaldo-Zeilen UND einer Zusammenfassung
("Total 12'164.86") sollen den Total-Vermögensstand + kontotyp
"mehrere Konten (N)" liefern (Anker auf der Total-Zeile). Ohne Zusammenfassung
→ manual_review:mehrkonten_ohne_total.

Ausschliesslich synthetische Wort-Tokens (Fantasy-Beträge).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from process_samples_full import detect_mehrkonten_total


def _word(text: str, x0: float = 0.0, top: float = 0.0, page: int = 1) -> dict:
    return {
        "text": text, "x0": x0, "top": top,
        "x1": x0 + 10, "bottom": top + 8, "page": page,
    }


def _zeile(tokens: list[str], top: float) -> list[dict]:
    return [_word(t, x0=20.0 * idx, top=top) for idx, t in enumerate(tokens)]


def _mehrkonten_mit_total() -> list[dict]:
    words: list[dict] = []
    words += _zeile(["Kontoauszug", "BANK-A"], 0)
    words += _zeile(["Sparkonto", "A", "Endsaldo", "5'000.00"], 20)
    words += _zeile(["Sparkonto", "B", "Endsaldo", "7'164.86"], 40)
    words += _zeile(["Zusammenfassung", "Total", "12'164.86"], 60)
    return words


def test_mehrkonten_mit_total_liefert_total():
    res = detect_mehrkonten_total(_mehrkonten_mit_total())
    assert res is not None
    assert res["kontotyp"] == "mehrere Konten (2)"
    # Total-Wert als Vermögensstand (Apostroph-tolerant verglichen).
    assert res["vermoegensstand_3112"].replace("'", "") == "12164.86"
    # Anker-Snippet enthält das Total-Label.
    assert "Total" in res.get("snippet", "")


def test_mehrkonten_ohne_total_manual_review():
    words: list[dict] = []
    words += _zeile(["Sparkonto", "A", "Endsaldo", "5'000.00"], 0)
    words += _zeile(["Sparkonto", "B", "Endsaldo", "7'164.86"], 20)
    res = detect_mehrkonten_total(words)
    assert res is not None
    assert res["vermoegensstand_3112"] == "manual_review:mehrkonten_ohne_total"


def test_einzelkonto_kein_eingriff():
    """Nur 1 Endsaldo → Heuristik greift NICHT (None)."""
    words = _zeile(["Sparkonto", "Endsaldo", "5'000.00"], 0)
    assert detect_mehrkonten_total(words) is None


def test_kein_endsaldo_kein_eingriff():
    words = _zeile(["Irgendein", "Text", "ohne", "Saldo"], 0)
    assert detect_mehrkonten_total(words) is None
