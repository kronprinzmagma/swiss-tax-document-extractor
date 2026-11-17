"""Herkunft pro Wert im Nachweis-Anhang (Regel / Modell / Mensch / berechnet).

Nur synthetische Zeilen; kein Beleg, keine echten Werte.
"""
from __future__ import annotations

from pathlib import Path

import scripts.build_tax_output as bto


def _row(**overrides) -> dict:
    row = {
        "pdf_name": "synthetisch.pdf",
        "field_name": "bruttoertrag",
        "beschreibung": "Zinsertrag",
        "status": "auto",
        "page": 1,
        "bbox": [10.0, 20.0, 30.0, 40.0],
        "snippet": "Zinsertrag 1'000.00",
        "herkunft": "modell",
        "derived": False,
        "plaus_errs": [],
        "manual_review_marker": None,
    }
    row.update(overrides)
    return row


def test_fmt_herkunft_bekannte_werte():
    assert bto.fmt_herkunft(_row(herkunft="regel")) == "Regel"
    assert bto.fmt_herkunft(_row(herkunft="modell")) == "Modell"
    assert bto.fmt_herkunft(_row(herkunft="mensch")) == "Mensch"


def test_fmt_herkunft_berechnet_und_fehlend():
    assert bto.fmt_herkunft(_row(derived=True)) == "berechnet"
    # Ein vom Menschen gesetzter Wert ist nicht mehr berechnet.
    assert bto.fmt_herkunft(_row(derived=True, herkunft="mensch")) == "Mensch"
    assert bto.fmt_herkunft(_row(herkunft=None)) == "—"


def test_anhang_zeigt_herkunftsspalte():
    rows = [
        _row(field_name="bruttoertrag", herkunft="regel"),
        _row(field_name="vermoegensstand_3112", herkunft="modell"),
    ]
    md = bto.render_anhang(rows, Path("evals/samples_x"))
    assert "| Feld | Beschreibung | Status | Herkunft | Seite | BBox | Snippet |" in md
    zeilen = [z for z in md.splitlines() if z.startswith("| `")]
    assert len(zeilen) == 2
    assert "| Regel |" in zeilen[0]
    assert "| Modell |" in zeilen[1]
    # Spaltenzahl der Datenzeilen passt zum Kopf.
    assert all(z.count("|") == 8 for z in zeilen)
