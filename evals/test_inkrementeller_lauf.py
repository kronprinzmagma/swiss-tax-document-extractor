"""Ein zweiter Lauf darf nicht alles neu einlesen (260904-rmx).

Der Abgleich vergleicht den Fingerabdruck im Word-JSON mit dem des PDFs.
Erste Fassung verglich gegen einen Schlüssel, den niemand schrieb: das
Ergebnis war ``None != "1234:567"`` — also wurde bei jedem Lauf jedes JSON
verworfen und jedes PDF erneut durch pdfplumber geschickt. Nach aussen sah
das aus wie ein normaler Lauf, nur eben eine halbe Stunde lang.
"""
from __future__ import annotations

import json

from reportlab.pdfgen import canvas

from scripts.pdf_to_word_json import pdf_fingerabdruck, pdf_to_json
from scripts.run_productive_local import _json_abgleich, _pdf_fingerabdruck


def mach_pdf(pfad, text="Lohnausweis Bruttolohn 100.00"):
    c = canvas.Canvas(str(pfad))
    c.drawString(72, 720, text)
    c.save()
    return pfad


def ordner(tmp_path):
    """Input- und JSON-Ordner wie im produktiven Lauf."""
    quelle, ziel = tmp_path / "quelle", tmp_path / "json"
    quelle.mkdir()
    ziel.mkdir()
    return quelle, ziel


def test_erzeuger_und_abgleich_nutzen_dieselbe_formel(tmp_path):
    pdf = mach_pdf(tmp_path / "a.pdf")
    assert _pdf_fingerabdruck(pdf) == pdf_fingerabdruck(pdf)


def test_word_json_traegt_den_fingerabdruck(tmp_path):
    pdf = mach_pdf(tmp_path / "a.pdf")
    doc = pdf_to_json(pdf)
    assert doc["pdf_fingerabdruck"] == pdf_fingerabdruck(pdf)


def test_unveraendertes_pdf_wird_nicht_neu_eingelesen(tmp_path):
    """Der eigentliche Punkt: zweiter Lauf, nichts zu tun."""
    quelle, ziel = ordner(tmp_path)
    pdf = mach_pdf(quelle / "a.pdf")
    (ziel / "a.json").write_text(json.dumps(pdf_to_json(pdf)))

    entfernt, behalten = _json_abgleich(quelle, ziel)
    assert (entfernt, behalten) == (0, 1)
    assert (ziel / "a.json").exists()


def test_geaendertes_pdf_wird_verworfen(tmp_path):
    quelle, ziel = ordner(tmp_path)
    pdf = mach_pdf(quelle / "a.pdf")
    (ziel / "a.json").write_text(json.dumps(pdf_to_json(pdf)))

    mach_pdf(pdf, "Lohnausweis Bruttolohn 200.00 zusaetzlicher Text")
    entfernt, behalten = _json_abgleich(quelle, ziel)
    assert (entfernt, behalten) == (1, 0)


def test_verwaistes_json_wird_entfernt(tmp_path):
    """PDF geloescht — das JSON darf nicht in der Tabelle weiterleben."""
    quelle, ziel = ordner(tmp_path)
    (ziel / "weg.json").write_text(json.dumps(
        {"pdf_name": "weg.pdf", "pdf_fingerabdruck": "1:2", "pages": []}))
    assert _json_abgleich(quelle, ziel) == (1, 0)
