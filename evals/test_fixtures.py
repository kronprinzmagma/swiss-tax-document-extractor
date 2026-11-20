"""Smoke-Tests: Generator erzeugt PDFs, pdfplumber findet Soll-Beträge.

Diese Tests garantieren, dass die in ``expected/<fixture>.json`` hinterlegten
Beträge im PDF tatsächlich als pdfplumber-Wort auftauchen — der zentrale
Round-trip ``reportlab → pdfplumber``, auf dem die ganze Eval-Suite (Wave 4)
aufbaut. Wenn der CH-Apostroph (U+0027 oder U+2019) durch reportlab nicht
durchgereicht würde, wäre die Anker-Validity-Metrik silently broken.

Die Tests sind defensiv konzipiert:

* Apostroph-Vergleich erfolgt **nach** :func:`extractors.numbers.normalize`,
  damit U+0027 vs. U+2019 vs. NBSP-Varianten als äquivalent gelten.
* `extract_words(use_text_flow=True)` — RESEARCH §Pitfall 3, sonst
  unvorhersagbare Wort-Reihenfolge bei dichten Layouts.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pdfplumber
import pytest

from evals.generate import (
    EXPECTED_DIR,
    FIXTURES_DIR,
    generate_f1_standard,
    main as generate_main,
)
from extractors.numbers import normalize


# ---------------------------------------------------------------------------
# Auto-Generate-Fixture: regeneriert die PDFs einmal pro Test-Modul, falls fehlend
# ---------------------------------------------------------------------------


def _ensure_generated() -> None:
    """Regeneriert die Fixtures, falls eine fehlt (PDFs sind gitignored)."""
    needed = [
        FIXTURES_DIR / "lohnausweis_standard.pdf",
        FIXTURES_DIR / "lohnausweis_quellensteuer.pdf",
        FIXTURES_DIR / "lohnausweis_custom_font.pdf",
    ]
    if not all(p.exists() for p in needed):
        generate_main()


@pytest.fixture(scope="module", autouse=True)
def _generate() -> None:
    _ensure_generated()


# ---------------------------------------------------------------------------
# Pflicht-Smoke-Tests gemäss <acceptance_criteria>
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fixture",
    [
        "lohnausweis_standard",
        "lohnausweis_quellensteuer",
        "lohnausweis_custom_font",
    ],
)
def test_fixture_pdf_extractable(fixture: str) -> None:
    """Jedes Fixture-PDF liefert mindestens 20 Wörter via pdfplumber.

    Schwellwert 20 fängt sowohl leere PDFs als auch Custom-Font-Mojibake
    (T-01-06): wenn die CMap kaputt ist, bekommt pdfplumber meist nur
    Glyph-IDs als unbekannte Strings zurück.
    """
    pdf_path = FIXTURES_DIR / f"{fixture}.pdf"
    assert pdf_path.exists(), f"{pdf_path} fehlt — generate.main() failed?"
    with pdfplumber.open(str(pdf_path)) as pdf:
        words = pdf.pages[0].extract_words(
            use_text_flow=True,
            keep_blank_chars=False,
        )
    assert len(words) >= 20, (
        f"{fixture}: nur {len(words)} Wörter — PDF zu leer oder Mojibake?"
    )


@pytest.mark.parametrize(
    "fixture",
    [
        "lohnausweis_standard",
        "lohnausweis_quellensteuer",
        "lohnausweis_custom_font",
    ],
)
def test_fixture_bruttolohn_findable(fixture: str) -> None:
    """Der Soll-Bruttolohn aus expected.json findet sich (nach normalize) als Wort.

    Kritischer Smoke-Check für den Anker-Match in Wave 3: wenn diese
    Assertion failt, hat reportlab den Apostroph anders geschrieben als
    erwartet, oder pdfplumber hat das Wort an Whitespace zerteilt.
    """
    expected = json.loads(
        (EXPECTED_DIR / f"{fixture}.json").read_text(encoding="utf-8")
    )
    soll = expected["bruttolohn_pos8"]["value"]
    pdf_path = FIXTURES_DIR / f"{fixture}.pdf"
    with pdfplumber.open(str(pdf_path)) as pdf:
        words = pdf.pages[0].extract_words(
            use_text_flow=True,
            keep_blank_chars=False,
        )
    norm_words = {normalize(w["text"]) for w in words}
    assert normalize(soll) in norm_words, (
        f"{fixture}: Soll-Bruttolohn {soll!r} (norm={normalize(soll)!r}) "
        f"nicht in extrahierten Wörtern. Vorhandene Number-Wörter: "
        f"{[w['text'] for w in words if any(c.isdigit() for c in w['text'])]}"
    )


def test_quellensteuer_apostroph_is_u2019() -> None:
    """F2-Apostroph muss U+2019 sein (testet RESEARCH §Pitfall 2 explizit)."""
    expected = json.loads(
        (EXPECTED_DIR / "lohnausweis_quellensteuer.json").read_text(encoding="utf-8")
    )
    v = expected["bruttolohn_pos8"]["value"]
    assert "’" in v, f"F2 muss U+2019 enthalten: {v!r}"


def test_quellensteuer_pos12_is_correct_value() -> None:
    """F2 Pos 12 = 16'134.40 (NICHT 115'200.00) — MUST_FIX #2 aus dem Plan-Review.

    Verwechslung von Quellensteuer und Bruttolohn würde die F2-Anchor-
    Validity silently invalidieren (LLM extrahiert 115'200, eval matcht
    erwartet 115'200, aber das ist semantisch der falsche Wert).
    """
    expected = json.loads(
        (EXPECTED_DIR / "lohnausweis_quellensteuer.json").read_text(encoding="utf-8")
    )
    qs = expected["quellensteuer_pos12"]["value"]
    assert qs == "16’134.40", f"Pos 12 falsch: {qs!r}"
    assert "’" in qs
    assert qs != expected["bruttolohn_pos8"]["value"]


def test_custom_font_meta_present() -> None:
    """F3 expected.json trägt _meta.level (L2-custom-font ODER L1-fallback)."""
    expected = json.loads(
        (EXPECTED_DIR / "lohnausweis_custom_font.json").read_text(encoding="utf-8")
    )
    assert "_meta" in expected
    assert expected["_meta"]["level"] in {"L2-custom-font", "L1-fallback-no-inter"}
    assert expected["_meta"]["font"] in {"Inter", "Helvetica"}


def test_f1_classifier_markers_present() -> None:
    """F1-PDF muss `Lohnausweis` und `Form 11` enthalten (Klassifizierer-Anker)."""
    pdf_path = FIXTURES_DIR / "lohnausweis_standard.pdf"
    with pdfplumber.open(str(pdf_path)) as pdf:
        text = pdf.pages[0].extract_text() or ""
    assert "Lohnausweis" in text
    assert "Form 11" in text


def test_f1_quellensteuer_pos12_is_null() -> None:
    """F1 hat keinen Quellensteuer-Eintrag — expected.json: ``null``."""
    expected = json.loads(
        (EXPECTED_DIR / "lohnausweis_standard.json").read_text(encoding="utf-8")
    )
    assert expected["quellensteuer_pos12"] is None


def test_no_pos17_anywhere() -> None:
    """Plan-Constraint-Gate: keinerlei `pos17`-Referenz in Generator/Expected."""
    repo_root = Path(__file__).resolve().parent.parent
    suspects = list((repo_root / "evals" / "expected").glob("*.json")) + [
        repo_root / "evals" / "generate.py"
    ]
    for path in suspects:
        assert "pos17" not in path.read_text(encoding="utf-8"), (
            f"`pos17` in {path} gefunden — Plan-Constraint #6 verletzt"
        )


def test_pdf_byte_reproducibility(tmp_path: Path) -> None:
    """Zwei Generator-Runs erzeugen byte-identische PDFs (Determinismus-Gate).

    Erforderlich für Snapshot-Drift-Erkennung in Wave 3+: wenn das PDF
    bei jedem Run neue Bytes bekommt (z. B. ``CreationDate = now()``),
    rauscht die git-Diff und ein echter Drift wird unsichtbar.
    """
    p1 = tmp_path / "run1.pdf"
    p2 = tmp_path / "run2.pdf"
    e1 = tmp_path / "exp1.json"
    e2 = tmp_path / "exp2.json"
    generate_f1_standard(p1, e1)
    generate_f1_standard(p2, e2)
    h1 = hashlib.sha256(p1.read_bytes()).hexdigest()
    h2 = hashlib.sha256(p2.read_bytes()).hexdigest()
    assert h1 == h2, (
        f"PDF nicht reproduzierbar: {h1[:16]} != {h2[:16]}. "
        f"Producer/CreationDate prüfen."
    )
