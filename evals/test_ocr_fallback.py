"""Tests für den OCR-Fallback im produktiven PDF→Word-JSON-Schritt (260904-rmx).

Vorher wurde ein Scan ohne Textebene stillschweigend übersprungen — nicht
vor-OCRte Dokumente verschwanden damit aus der Übertragungstabelle, ohne dass
der Vollständigkeits-Constraint (CLAUDE.md #3) anschlug.
"""
from __future__ import annotations

import shutil

import pytest

from scripts.pdf_to_word_json import pdf_to_json

pytestmark = pytest.mark.skipif(
    shutil.which("ocrmypdf") is None, reason="ocrmypdf nicht installiert"
)


@pytest.fixture(scope="module")
def scan_pdf(tmp_path_factory):
    """Reines Bild-PDF ohne Textebene — simuliert einen frischen Scan."""
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (1240, 1754), "white")
    d = ImageDraw.Draw(img)
    try:
        f = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 46)
    except OSError:  # pragma: no cover — plattformabhängig
        f = ImageFont.load_default()
    d.text((120, 300), "Lohnausweis", fill="black", font=f)
    d.text((120, 420), "8. Bruttolohn total", fill="black", font=f)
    d.text((950, 420), "9409", fill="black", font=f)
    d.text((120, 520), "11. Nettolohn", fill="black", font=f)
    d.text((950, 520), "8197", fill="black", font=f)
    p = tmp_path_factory.mktemp("ocr") / "scan.pdf"
    img.save(p, "PDF", resolution=150.0)
    return p


def test_scan_ohne_textebene_hat_wirklich_keinen_text(scan_pdf):
    """Vorbedingung: pdfplumber findet nichts — sonst testet der Test nichts."""
    import pdfplumber
    with pdfplumber.open(scan_pdf) as pdf:
        assert pdf.pages[0].extract_words() == []


def test_ocr_fallback_liest_den_scan(scan_pdf):
    doc = pdf_to_json(scan_pdf)
    assert doc is not None, "Scan wurde übersprungen statt OCRt"
    assert doc.get("ocr_applied") is True
    text = " ".join(w["text"] for w in doc["pages"][0]["words"])
    assert "9409" in text
    assert "8197" in text


def test_ocr_ergebnis_wird_klassifiziert(scan_pdf):
    doc = pdf_to_json(scan_pdf)
    assert doc["belegtyp"] == "lohnausweis"


def test_originalname_bleibt_erhalten(scan_pdf):
    """Der Temp-Name der OCR-Datei darf nicht in die Tabelle wandern."""
    doc = pdf_to_json(scan_pdf)
    assert doc["pdf_name"] == scan_pdf.name
    assert "ocr.pdf" != doc["pdf_name"]


def test_keine_endlosrekursion_wenn_ocr_nichts_liefert(scan_pdf):
    """Zweiter Durchlauf ist gesperrt — _ocr_attempted verhindert die Schleife."""
    assert pdf_to_json(scan_pdf, _ocr_attempted=True) is None
