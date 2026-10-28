"""Smoke-Tests für Phase-3-Wave-4-Batch-2-Fixtures (Plan 03-05).

Diese Tests prüfen die Generator-Outputs OHNE Ollama-Call:

* Alle 8 Batch-2-Fixtures (PDF + expected.json) sind generiert
  (4 Belegtypen × 2 Varianten: Kinderbetreuung, Hypothek,
  Liegenschaftsunterhalt, Krankheitskosten).
* expected.json erfüllt das ``_validate_expected``-Schema-Gate
  (Pflichtfelder + ``_meta.expected_person``).
* pdfplumber-Roundtrip: Pflichtfeld-Werte sind im PDF-Text wiederfindbar
  (anchor_present-Vertrag — Voraussetzung für Plan 03-06 End-to-End).
* Liegenschaftsunterhalt-Fixtures tragen ``_meta.expected_werterhaltend``
  (True für Reparatur-Variante, False für Anbau-Variante).
* PDF-Generator ist byte-stabil (zwei Re-Runs → identische SHA-256).
* ``classify_werterhaltend`` deckt alle drei Branches + Edge-Cases ab
  (≥ 5 Unit-Tests, Plan 03-05 Constraint 5).

Reine Datei-/PDF-Verifikation — kein LLM-Call.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from extractors.confidence import classify_werterhaltend
from extractors.pdf_reader import read_pdf
from extractors.schema import MANDATORY_FIELDS_FOR_BELEGTYP

EVALS_DIR = Path(__file__).parent
FIXTURES = EVALS_DIR / "fixtures"
EXPECTED = EVALS_DIR / "expected"

# Tuple aus (Fixture-Stem, Belegtyp, erwartete expected_person).
_BATCH2_FIXTURES: list[tuple[str, str, str]] = [
    ("kinderbetreuung_standard", "kinderbetreuung", "kind1"),
    ("kinderbetreuung_edge_cap", "kinderbetreuung", "kind2"),
    ("hypothek_zinsbestaetigung_standard", "hypothek_zinsbestaetigung", "gemeinsam"),
    ("hypothek_zinsbestaetigung_raiffeisen", "hypothek_zinsbestaetigung", "mann"),
    ("liegenschaftsunterhalt_reparatur", "liegenschaftsunterhalt", "mann"),
    ("liegenschaftsunterhalt_anbau", "liegenschaftsunterhalt", "frau"),
    ("krankheitskosten_arzt", "krankheitskosten", "frau"),
    ("krankheitskosten_apotheke", "krankheitskosten", "kind2"),
]


@pytest.fixture(scope="module", autouse=True)
def _generate_fixtures() -> None:
    """Generiert alle Fixtures idempotent (falls noch nicht da)."""
    if not (FIXTURES / "kinderbetreuung_standard.pdf").exists():
        from evals.generate import main as gen_main

        gen_main()


@pytest.mark.parametrize(
    ("stem", "belegtyp", "expected_person"),
    _BATCH2_FIXTURES,
    ids=[t[0] for t in _BATCH2_FIXTURES],
)
def test_batch2_fixture_files_exist(stem: str, belegtyp: str, expected_person: str) -> None:
    """PDF und expected.json existieren."""
    assert (FIXTURES / f"{stem}.pdf").is_file(), f"PDF fehlt: {stem}.pdf"
    assert (EXPECTED / f"{stem}.json").is_file(), f"expected fehlt: {stem}.json"


@pytest.mark.parametrize(
    ("stem", "belegtyp", "expected_person"),
    _BATCH2_FIXTURES,
    ids=[t[0] for t in _BATCH2_FIXTURES],
)
def test_batch2_expected_schema(stem: str, belegtyp: str, expected_person: str) -> None:
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
    _BATCH2_FIXTURES,
    ids=[t[0] for t in _BATCH2_FIXTURES],
)
def test_batch2_pdf_text_roundtrip(stem: str, belegtyp: str, expected_person: str) -> None:
    """pdfplumber findet alle Pflichtfeld-Werte im PDF-Text wieder.

    Mindest-Vertrag für Plan-03-06-Pipeline-Integration: der Anker-Resolver
    muss Pflichtwerte im Wort-Strom wiederfinden.
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
        norm_value = re.sub(r"\s+", " ", value).strip()
        assert norm_value in text, (
            f"{stem}: Pflichtwert {field}={value!r} nicht im PDF-Text gefunden "
            f"(suchte normalisiert {norm_value!r})"
        )


@pytest.mark.parametrize(
    ("stem", "expected_werterhaltend"),
    [
        ("liegenschaftsunterhalt_reparatur", True),
        ("liegenschaftsunterhalt_anbau", False),
    ],
)
def test_liegenschaftsunterhalt_expected_werterhaltend(
    stem: str, expected_werterhaltend: bool
) -> None:
    """Liegenschaftsunterhalt-Fixtures tragen ``_meta.expected_werterhaltend``.

    Plan 03-06 konsumiert dieses Feld, um die Regex-Heuristik
    :func:`classify_werterhaltend` end-to-end zu testen (auf dem
    Beschreibungstext der Fixture).
    """
    data = json.loads((EXPECTED / f"{stem}.json").read_text(encoding="utf-8"))
    assert data["werterhaltend"] is None, (
        f"{stem}: werterhaltend muss null sein (LLM lässt das Feld leer); "
        f"got {data['werterhaltend']!r}"
    )
    meta = data["_meta"]
    assert meta["expected_werterhaltend"] is expected_werterhaltend, (
        f"{stem}: expected_werterhaltend={meta['expected_werterhaltend']!r}, "
        f"erwartet {expected_werterhaltend!r}"
    )
    # Beschreibungstext muss auch im Beleg-Text vorkommen — Plan-03-06-Vertrag.
    beschreibung = meta["beschreibung"]
    words = read_pdf(FIXTURES / f"{stem}.pdf")
    text = re.sub(r"\s+", " ", " ".join(w["text"] for w in words))
    assert beschreibung in text, (
        f"{stem}: Beschreibungstext {beschreibung!r} nicht im PDF-Text"
    )
    # End-to-End: Regex-Heuristik auf der Beschreibung liefert das erwartete Ergebnis.
    assert classify_werterhaltend(beschreibung) is expected_werterhaltend


def test_batch2_count() -> None:
    """Genau 8 neue Fixtures in Batch 2 (Plan 03-05: 4 Belegtypen × 2 Varianten)."""
    new_stems = {t[0] for t in _BATCH2_FIXTURES}
    pdfs = {p.stem for p in FIXTURES.glob("*.pdf")}
    missing = new_stems - pdfs
    assert not missing, f"Fehlende Batch-2-Fixtures: {sorted(missing)}"
    assert len(new_stems) == 8, "Batch 2 = exakt 8 Fixtures (Plan 03-05)"


def test_batch2_generator_byte_stable() -> None:
    """Generator ist byte-stabil — zwei Re-Runs liefern dieselben SHA-256.

    Sanity-Check für ``_new_canvas(invariant=1)`` (vermeidet false-Diff-Drift
    in Plan-03-06-Evals).
    """
    pdf_path = FIXTURES / "hypothek_zinsbestaetigung_standard.pdf"
    h1 = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
    # Re-run nur dieses einen Generators (schnell — kein full generate_all-Lauf).
    subprocess.run(
        [sys.executable, "-m", "evals.generate"],
        check=True,
        capture_output=True,
    )
    h2 = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
    assert h1 == h2, f"Generator nicht byte-stabil: {h1} ≠ {h2}"


# ---------------------------------------------------------------------------
# classify_werterhaltend Unit-Tests (Plan 03-05 Constraint 5: ≥ 5 Cases)
# ---------------------------------------------------------------------------


def test_classify_werterhaltend_only_werterhaltend() -> None:
    """Nur WERTERHALTEND-Pattern matched → True."""
    assert classify_werterhaltend("Reparatur Wasserschaden Bad") is True
    assert classify_werterhaltend("Renovation Küche 2024") is True
    assert classify_werterhaltend("Service Heizung Jahreswartung") is True
    assert classify_werterhaltend("Instandhaltung Dachrinne") is True


def test_classify_werterhaltend_only_wertvermehrend() -> None:
    """Nur WERTVERMEHREND-Pattern matched → False."""
    assert classify_werterhaltend("Anbau Wintergarten gemäss Offerte") is False
    assert classify_werterhaltend("Neubau Carport hinter Garage") is False
    assert classify_werterhaltend("Umbau Estrich zu Wohnraum") is False
    assert classify_werterhaltend("Ausbau Keller-Hobbyraum") is False


def test_classify_werterhaltend_neither() -> None:
    """Weder noch matched → None (Heuristik-Unsicherheit)."""
    assert classify_werterhaltend("Diverse Arbeiten gemäss Auftrag") is None
    assert classify_werterhaltend("Rechnung Nr. 12345") is None


def test_classify_werterhaltend_both() -> None:
    """Beide Patterns matched gleichzeitig → None (Heuristik-Unsicherheit)."""
    assert classify_werterhaltend("Reparatur nach Anbau") is None
    assert classify_werterhaltend("Renovation und Umbau gleichzeitig") is None


def test_classify_werterhaltend_empty() -> None:
    """Leerer Text → None."""
    assert classify_werterhaltend("") is None


def test_classify_werterhaltend_case_insensitive() -> None:
    """Regex ist case-insensitive (Plan-Constraint)."""
    assert classify_werterhaltend("REPARATUR FASSADE") is True
    assert classify_werterhaltend("anbau wintergarten") is False
