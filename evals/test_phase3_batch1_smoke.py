"""Smoke-Tests für Phase-3-Wave-3-Batch-1-Fixtures (Plan 03-04).

Diese Tests prüfen die Generator-Outputs OHNE Ollama-Call:

* Alle 6 Batch-1-Fixtures (PDF + expected.json) sind generiert.
* expected.json erfüllt das ``_validate_expected``-Schema-Gate
  (Pflichtfelder + ``_meta.expected_person``).
* pdfplumber-Roundtrip: Pflichtfeld-Werte sind im PDF-Text wiederfindbar
  (anchor_present-Vertrag — Voraussetzung für Plan 03-06 End-to-End).
* Spenden-Custom-Font-Fixture trägt Level ``L2-custom-font`` (bzw.
  ``L1-fallback-no-inter`` falls Inter-Regular.ttf fehlt).

Reine Datei-/PDF-Verifikation — kein LLM-Call, daher in jedem Lauf
ausgeführt (kein ``llm_full``-Marker).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from extractors.pdf_reader import read_pdf
from extractors.schema import MANDATORY_FIELDS_FOR_BELEGTYP

EVALS_DIR = Path(__file__).parent
FIXTURES = EVALS_DIR / "fixtures"
EXPECTED = EVALS_DIR / "expected"

# Tuple aus (Fixture-Stem, Belegtyp, erwartete expected_person).
_BATCH1_FIXTURES: list[tuple[str, str, str]] = [
    ("wertschriftenverzeichnis_standard", "wertschriftenverzeichnis", "mann"),
    ("wertschriftenverzeichnis_joint", "wertschriftenverzeichnis", "gemeinsam"),
    ("spenden_standard", "spenden", "frau"),
    ("spenden_custom_font", "spenden", "frau"),
    ("berufsauslagen_standard", "berufsauslagen", "mann"),
    ("berufsauslagen_hsg", "berufsauslagen", "frau"),
]


@pytest.fixture(scope="module", autouse=True)
def _generate_fixtures() -> None:
    """Generiert alle Fixtures idempotent (falls noch nicht da)."""
    if not (FIXTURES / "wertschriftenverzeichnis_standard.pdf").exists():
        from evals.generate import main as gen_main

        gen_main()


@pytest.mark.parametrize(
    ("stem", "belegtyp", "expected_person"),
    _BATCH1_FIXTURES,
    ids=[t[0] for t in _BATCH1_FIXTURES],
)
def test_batch1_fixture_files_exist(stem: str, belegtyp: str, expected_person: str) -> None:
    """PDF und expected.json existieren."""
    assert (FIXTURES / f"{stem}.pdf").is_file(), f"PDF fehlt: {stem}.pdf"
    assert (EXPECTED / f"{stem}.json").is_file(), f"expected fehlt: {stem}.json"


@pytest.mark.parametrize(
    ("stem", "belegtyp", "expected_person"),
    _BATCH1_FIXTURES,
    ids=[t[0] for t in _BATCH1_FIXTURES],
)
def test_batch1_expected_schema(stem: str, belegtyp: str, expected_person: str) -> None:
    """expected.json hat alle Pflichtfelder und ``_meta.expected_person``."""
    data = json.loads((EXPECTED / f"{stem}.json").read_text(encoding="utf-8"))
    pflicht = set(MANDATORY_FIELDS_FOR_BELEGTYP[belegtyp])
    soll_keys = {k for k, v in data.items() if not k.startswith("_") and v is not None}
    missing = pflicht - soll_keys
    assert not missing, f"{stem}: fehlende Pflichtfelder {sorted(missing)}"

    meta = data.get("_meta", {})
    assert meta.get("belegtyp") == belegtyp, f"{stem}: _meta.belegtyp ≠ {belegtyp}"
    assert meta.get("expected_person") == expected_person, (
        f"{stem}: _meta.expected_person={meta.get('expected_person')!r}, "
        f"erwartet {expected_person!r}"
    )


@pytest.mark.parametrize(
    ("stem", "belegtyp", "expected_person"),
    _BATCH1_FIXTURES,
    ids=[t[0] for t in _BATCH1_FIXTURES],
)
def test_batch1_pdf_text_roundtrip(stem: str, belegtyp: str, expected_person: str) -> None:
    """pdfplumber findet alle Pflichtfeld-Werte im PDF-Text wieder.

    Mindest-Vertrag für Plan-03-06-Pipeline-Integration: der Anker-Resolver
    muss Pflichtwerte im Wort-Strom wiederfinden — wenn schon die rohe
    Wort-Concat-Suche scheitert, ist die Fixture defekt.

    Normalisierung: Whitespace-Zusammenklappen, damit Multi-Word-Werte
    ("UBS Switzerland AG") trotz Wort-Tokenisierung matchen.
    """
    pdf_path = FIXTURES / f"{stem}.pdf"
    expected_path = EXPECTED / f"{stem}.json"
    data = json.loads(expected_path.read_text(encoding="utf-8"))

    words = read_pdf(pdf_path)
    text = re.sub(r"\s+", " ", " ".join(w["text"] for w in words))

    for field in MANDATORY_FIELDS_FOR_BELEGTYP[belegtyp]:
        spec = data.get(field)
        if spec is None:
            continue
        value = spec["value"]
        # Whitespace-tolerant: normalisierte Form des Werts muss im Text vorkommen.
        norm_value = re.sub(r"\s+", " ", value).strip()
        assert norm_value in text, (
            f"{stem}: Pflichtwert {field}={value!r} nicht im PDF-Text gefunden "
            f"(suchte normalisiert {norm_value!r})"
        )


def test_spenden_custom_font_level() -> None:
    """``spenden_custom_font`` trägt L2- bzw. fallback-Level (Plan-Empfehlung)."""
    data = json.loads((EXPECTED / "spenden_custom_font.json").read_text(encoding="utf-8"))
    level = data["_meta"]["level"]
    assert level in ("L2-custom-font", "L1-fallback-no-inter"), (
        f"spenden_custom_font: unerwartetes _meta.level={level!r}"
    )


def test_batch1_count() -> None:
    """Genau 6 neue Fixtures in Batch 1 (Plan-Korrektur — nicht 7)."""
    new_stems = {t[0] for t in _BATCH1_FIXTURES}
    pdfs = {p.stem for p in FIXTURES.glob("*.pdf")}
    missing = new_stems - pdfs
    assert not missing, f"Fehlende Batch-1-Fixtures: {sorted(missing)}"
    assert len(new_stems) == 6, "Batch 1 = exakt 6 Fixtures (Plan-Korrektur Wave 3)"
