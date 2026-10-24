"""Smoke- und Strukturtests für Phase-2-Fixtures (Plan 02-03).

Diese Suite validiert die in Plan 02-03 erzeugten Fixtures (Bank-Zinsausweis,
KK-Prämienbescheinigung, Säule 3a) auf:

* (a) ``python -m evals.generate`` läuft fehlerfrei und erzeugt alle Phase-2-PDFs.
* (b) Pro Phase-2-Fixture: PDF ist non-empty (≥20 Wörter), expected.json ist
  valides JSON.
* (c) Pro Phase-2-Fixture: alle Pflichtfelder gemäss
  :data:`extractors.schema.MANDATORY_FIELDS_FOR_BELEGTYP` sind im expected
  dict mit ``value: str`` und ``anchor_present: True``.
* (d) ``_meta.belegtyp`` matcht die Filename-Konvention.
* (e) Determinismus: zwei Runs eines Generators in ``tmp_path`` liefern
  byte-identische PDFs (sha256-Vergleich).
* (f) Familien-Namen-Coverage: Mindestens je 1 Fixture für mann, frau, kind1
  und gemeinsam (Joint-Account) in der Phase-2-Familie.

Alle Phase-2-PDFs sind via ``evals/fixtures/*.pdf`` gitignored — der Test
regeneriert sie, falls fehlend.

Die Tests sind reine Strukturtests; Anker-Resolver-Roundtrips über pdfplumber
für Bank/KK/3a-Beträge erfolgen separat (siehe Plan 02-05 End-to-End-Suite).
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pdfplumber
import pytest

from evals.generate import (
    EXPECTED_DIR,
    FIXTURES_DIR,
    generate_all,
    generate_bank_standard,
)
from extractors.numbers import normalize
from extractors.schema import MANDATORY_FIELDS_FOR_BELEGTYP

# ---------------------------------------------------------------------------
# Fixture-Liste mit Belegtyp-Mapping
# ---------------------------------------------------------------------------

PHASE2_FIXTURES: list[tuple[str, str]] = [
    ("bank_zinsausweis_standard", "bank_zinsausweis"),
    ("bank_zinsausweis_zkb", "bank_zinsausweis"),
    ("bank_zinsausweis_custom_font", "bank_zinsausweis"),
    ("kk_praemienbescheinigung_standard", "kk_praemienbescheinigung"),
    ("kk_praemienbescheinigung_helsana", "kk_praemienbescheinigung"),
    ("saeule_3a_standard", "saeule_3a"),
    ("saeule_3a_viac", "saeule_3a"),
]


def _ensure_generated() -> None:
    """Regeneriert alle Phase-2-Fixtures, falls eine fehlt."""
    needed = [FIXTURES_DIR / f"{name}.pdf" for name, _ in PHASE2_FIXTURES]
    if not all(p.exists() for p in needed):
        generate_all()


@pytest.fixture(scope="module", autouse=True)
def _generate() -> None:
    _ensure_generated()


# ---------------------------------------------------------------------------
# (a) generate.main() läuft ohne Exception (subprocess-Test)
# ---------------------------------------------------------------------------


def test_generate_main_runs_clean() -> None:
    """``python -m evals.generate`` läuft ohne Exception (Exit-Code 0)."""
    result = subprocess.run(
        [sys.executable, "-m", "evals.generate"],
        cwd=str(Path(__file__).resolve().parent.parent),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"generate.main() failed: stderr={result.stderr!r}"
    )


# ---------------------------------------------------------------------------
# (b) PDF non-empty + expected.json valides JSON
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fixture,_belegtyp", PHASE2_FIXTURES)
def test_phase2_pdf_non_empty(fixture: str, _belegtyp: str) -> None:
    """Phase-2-PDFs liefern mindestens 20 Wörter via pdfplumber."""
    pdf_path = FIXTURES_DIR / f"{fixture}.pdf"
    assert pdf_path.exists(), f"{pdf_path} fehlt — generate_all() failed?"
    with pdfplumber.open(str(pdf_path)) as pdf:
        words = pdf.pages[0].extract_words(
            use_text_flow=True,
            keep_blank_chars=False,
        )
    assert len(words) >= 20, (
        f"{fixture}: nur {len(words)} Wörter — PDF zu leer oder Mojibake?"
    )


@pytest.mark.parametrize("fixture,_belegtyp", PHASE2_FIXTURES)
def test_phase2_expected_is_valid_json(fixture: str, _belegtyp: str) -> None:
    """expected/<fixture>.json ist parsbares JSON."""
    expected_path = EXPECTED_DIR / f"{fixture}.json"
    assert expected_path.exists(), f"{expected_path} fehlt"
    data = json.loads(expected_path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)


# ---------------------------------------------------------------------------
# (c) Pflichtfelder gemäss MANDATORY_FIELDS_FOR_BELEGTYP
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fixture,belegtyp", PHASE2_FIXTURES)
def test_phase2_mandatory_fields_present(fixture: str, belegtyp: str) -> None:
    """Pro Phase-2-Fixture müssen alle Pflichtfelder mit korrektem Typ existieren."""
    expected_path = EXPECTED_DIR / f"{fixture}.json"
    data = json.loads(expected_path.read_text(encoding="utf-8"))
    pflichtfelder = MANDATORY_FIELDS_FOR_BELEGTYP[belegtyp]
    for feld in pflichtfelder:
        assert feld in data, (
            f"{fixture}: Pflichtfeld {feld!r} fehlt in expected.json "
            f"(belegtyp={belegtyp})"
        )
        val = data[feld]
        assert isinstance(val, dict), f"{fixture}/{feld}: kein Dict ({val!r})"
        assert isinstance(val.get("value"), str), (
            f"{fixture}/{feld}: value ist kein str"
        )
        assert val.get("anchor_present") is True, (
            f"{fixture}/{feld}: anchor_present ist nicht True"
        )


# ---------------------------------------------------------------------------
# (d) _meta.belegtyp matcht Filename-Konvention
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fixture,belegtyp", PHASE2_FIXTURES)
def test_phase2_meta_belegtyp_matches_filename(fixture: str, belegtyp: str) -> None:
    """_meta.belegtyp im expected.json matcht den deklarierten Belegtyp."""
    expected_path = EXPECTED_DIR / f"{fixture}.json"
    data = json.loads(expected_path.read_text(encoding="utf-8"))
    assert "_meta" in data, f"{fixture}: _meta-Block fehlt"
    assert data["_meta"].get("belegtyp") == belegtyp, (
        f"{fixture}: _meta.belegtyp ist {data['_meta'].get('belegtyp')!r}, "
        f"erwartet {belegtyp!r}"
    )
    assert "level" in data["_meta"], f"{fixture}: _meta.level fehlt"


# ---------------------------------------------------------------------------
# (e) Determinismus: 2x dieselbe Fixture → identische sha256
# ---------------------------------------------------------------------------


def test_phase2_byte_deterministic(tmp_path: Path) -> None:
    """Zwei Runs von ``generate_bank_standard`` erzeugen byte-identische PDFs."""
    p1 = tmp_path / "run1.pdf"
    p2 = tmp_path / "run2.pdf"
    e1 = tmp_path / "exp1.json"
    e2 = tmp_path / "exp2.json"
    generate_bank_standard(p1, e1)
    generate_bank_standard(p2, e2)
    h1 = hashlib.sha256(p1.read_bytes()).hexdigest()
    h2 = hashlib.sha256(p2.read_bytes()).hexdigest()
    assert h1 == h2, (
        f"Bank-Fixture nicht reproduzierbar: {h1[:16]} != {h2[:16]}"
    )


# ---------------------------------------------------------------------------
# (f) Familien-Namen-Coverage: mann, frau, kind1, gemeinsam
# ---------------------------------------------------------------------------


def test_phase2_family_coverage() -> None:
    """Phase-2-Fixtures decken mann (Hans), frau (Maria), kind1 (Lina), gemeinsam ab.

    Liest die _meta.expected_person-Markierung aller Phase-2-expected.json
    und prüft Set-Inklusion.
    """
    seen: set[str] = set()
    for fixture, _belegtyp in PHASE2_FIXTURES:
        expected_path = EXPECTED_DIR / f"{fixture}.json"
        data = json.loads(expected_path.read_text(encoding="utf-8"))
        person = data.get("_meta", {}).get("expected_person")
        if person:
            seen.add(person)
    required = {"mann", "frau", "kind1", "gemeinsam"}
    missing = required - seen
    assert not missing, (
        f"Familien-Coverage unvollständig: fehlende Rollen={sorted(missing)}; "
        f"gesehen={sorted(seen)}"
    )


# ---------------------------------------------------------------------------
# Spezial: 3a-Limit-2024-Verification (RESEARCH-Korrektur)
# ---------------------------------------------------------------------------


def test_saeule_3a_standard_uses_2024_limit() -> None:
    """saeule_3a_standard.einzahlung_betrag = "7'056.00" (2024-Limit, NICHT 7'258)."""
    data = json.loads(
        (EXPECTED_DIR / "saeule_3a_standard.json").read_text(encoding="utf-8")
    )
    val = data["einzahlung_betrag"]["value"]
    assert val == "7'056.00", (
        f"3a-Standard-Limit muss 7'056.00 (2024) sein, nicht {val!r}"
    )


# ---------------------------------------------------------------------------
# Spezial: Bank Pflicht-Beträge im PDF wiederfindbar (normalize-Roundtrip)
# ---------------------------------------------------------------------------


def test_bank_standard_bruttoertrag_findable() -> None:
    """Bank-Standard-Bruttoertrag ist als ein einzelnes Wort im PDF (Pitfall 3)."""
    expected = json.loads(
        (EXPECTED_DIR / "bank_zinsausweis_standard.json").read_text(encoding="utf-8")
    )
    soll = expected["bruttoertrag"]["value"]
    pdf_path = FIXTURES_DIR / "bank_zinsausweis_standard.pdf"
    with pdfplumber.open(str(pdf_path)) as pdf:
        words = pdf.pages[0].extract_words(
            use_text_flow=True,
            keep_blank_chars=False,
        )
    norm_words = {normalize(w["text"]) for w in words}
    assert normalize(soll) in norm_words, (
        f"Bruttoertrag {soll!r} (norm={normalize(soll)!r}) nicht in Wörtern"
    )


def test_saeule_3a_einzahlung_findable() -> None:
    """3a-Einzahlung-Betrag wird als einzelnes Wort im PDF gefunden."""
    expected = json.loads(
        (EXPECTED_DIR / "saeule_3a_standard.json").read_text(encoding="utf-8")
    )
    soll = expected["einzahlung_betrag"]["value"]
    pdf_path = FIXTURES_DIR / "saeule_3a_standard.pdf"
    with pdfplumber.open(str(pdf_path)) as pdf:
        words = pdf.pages[0].extract_words(
            use_text_flow=True,
            keep_blank_chars=False,
        )
    norm_words = {normalize(w["text"]) for w in words}
    assert normalize(soll) in norm_words, (
        f"Einzahlung {soll!r} (norm={normalize(soll)!r}) nicht in Wörtern"
    )
