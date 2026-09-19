"""Tests fuer die eSteuerauszug-Erkennung (eCH-0196)."""
from __future__ import annotations

import pytest

from scripts.esteuerauszug_check import MARKER_RE, pdf417_gefunden, xml_anhaenge

zxingcpp = pytest.importorskip("zxingcpp")
Image = pytest.importorskip("PIL.Image")


@pytest.fixture(scope="module")
def pdfs(tmp_path_factory):
    from PIL import Image as I
    d = tmp_path_factory.mktemp("esta")
    nutz = "<taxStatement><totalTaxValue>12345.67</totalTaxValue></taxStatement>"
    bc = zxingcpp.write_barcode(zxingcpp.BarcodeFormat.PDF417, nutz, width=1200, height=300)
    img = bc if isinstance(bc, I.Image) else I.fromarray(bc)
    seite = I.new("RGB", (1240, 1754), "white")
    seite.paste(img.convert("RGB"), (60, 900))
    mit = d / "mit.pdf"
    seite.save(mit, "PDF", resolution=150.0)
    ohne = d / "ohne.pdf"
    I.new("RGB", (1240, 1754), "white").save(ohne, "PDF", resolution=150.0)
    return mit, ohne


def test_barcode_wird_gefunden(pdfs):
    mit, _ = pdfs
    gefunden, laenge = pdf417_gefunden(mit)
    assert gefunden is True
    assert laenge > 20, "Nutzdaten sollten dekodiert werden"


def test_ohne_barcode_kein_treffer(pdfs):
    _, ohne = pdfs
    assert pdf417_gefunden(ohne) == (False, 0)


def test_kein_absturz_bei_fehlenden_anhaengen(pdfs):
    _, ohne = pdfs
    assert xml_anhaenge(ohne) == []


@pytest.mark.parametrize("text,erwartet", [
    ("eSteuerauszug 2025", True),
    ("E-Steuerauszug", True),
    ("eCH-0196", True),
    ("Tax Statement", True),
    ("Kontoauszug", False),
])
def test_textmarker(text, erwartet):
    assert bool(MARKER_RE.search(text)) is erwartet
