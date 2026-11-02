"""Unit-Tests für extractors/pdf_reader.py.

Roundtrip gegen Wave-2-Fixture `lohnausweis_standard.pdf`.
"""
from pathlib import Path

import pytest

from extractors.pdf_reader import read_pdf

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module", autouse=True)
def _generate():
    # Stelle sicher, dass die Fixture existiert (Wave 2 erzeugt sie deterministisch).
    if not (FIXTURES / "lohnausweis_standard.pdf").exists():
        from evals.generate import main  # type: ignore[import-not-found]

        main()


def test_read_pdf_returns_words():
    words = read_pdf(FIXTURES / "lohnausweis_standard.pdf")
    assert len(words) >= 20
    for w in words:
        assert "page" in w and isinstance(w["page"], int)
        for k in ("x0", "top", "x1", "bottom", "text"):
            assert k in w, f"Schlüssel {k} fehlt in {w!r}"


def test_read_pdf_page_is_1_based():
    words = read_pdf(FIXTURES / "lohnausweis_standard.pdf")
    assert min(w["page"] for w in words) == 1


def test_read_pdf_missing_file():
    with pytest.raises(FileNotFoundError):
        read_pdf(FIXTURES / "does_not_exist.pdf")


# --------------------------------------------------------------------------- #
# R9: OCR-Kommandokonstruktion mit Orientierungs-Korrektur (260612-m8t)        #
# --------------------------------------------------------------------------- #
# Scan-PDFs liegen oft rotiert vor. build_ocr_command baut das ocrmypdf-Argv
# inkl. --rotate-pages — reine Kommandokonstruktion, kein echter OCR-Aufruf.

from extractors.pdf_reader import build_ocr_command


def test_build_ocr_command_enthaelt_rotate_pages():
    cmd = build_ocr_command("in.pdf", "out.pdf")
    assert cmd[0] == "ocrmypdf"
    assert "--rotate-pages" in cmd
    # Input + Output stehen am Ende des Argv.
    assert cmd[-2:] == ["in.pdf", "out.pdf"]


def test_build_ocr_command_sprache_deutsch_default():
    cmd = build_ocr_command("in.pdf", "out.pdf")
    assert "-l" in cmd
    li = cmd.index("-l")
    assert cmd[li + 1] == "deu"


def test_build_ocr_command_force_fuegt_force_ocr_hinzu():
    cmd = build_ocr_command("in.pdf", "out.pdf", force=True)
    assert "--force-ocr" in cmd
    # Flag-Konflikt vermeiden: nicht zusammen mit --redo-ocr/--skip-text.
    assert "--redo-ocr" not in cmd
    assert "--skip-text" not in cmd


def test_build_ocr_command_ohne_force_kein_force_ocr():
    cmd = build_ocr_command("in.pdf", "out.pdf", force=False)
    assert "--force-ocr" not in cmd


def test_build_ocr_command_pfade_als_path_akzeptiert():
    cmd = build_ocr_command(Path("a.pdf"), Path("b.pdf"))
    assert cmd[-2:] == ["a.pdf", "b.pdf"]
