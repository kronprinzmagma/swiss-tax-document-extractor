"""Tests für die VSt-Härtung (R5, 260612-m8t).

Ein Nicht-Null-Verrechnungssteuer-Wert, dessen Anker-Snippet KEIN VSt-Label
in der Nähe hat, ist verdächtig (vermutlich aus einer Ertragsspalte gegriffen)
→ value="manual_review:vrs_label_fehlt", anchor_valid=False.

AUSNAHME: Wert == 0.00 (oder unparsbar) bleibt unverändert — viele Belege
weisen 0.00 ohne explizites Label aus.

Synthetische Feld-Records.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from process_samples_full import harden_vrs_fields


def _field(feld: str, value: str, snippet: str, anchor_valid: bool = True) -> dict:
    return {
        "feld": feld, "value": value, "snippet": snippet,
        "anchor_valid": anchor_valid, "bbox": [0, 0, 10, 10], "page": 1,
    }


def test_nicht_null_ohne_label_demotiert():
    fields = [_field("verrechnungssteuer", "48.90", "Ertrag Rubrik B 48.90")]
    harden_vrs_fields(fields)
    assert fields[0]["value"] == "manual_review:vrs_label_fehlt"
    assert fields[0]["anchor_valid"] is False


def test_null_ohne_label_bleibt():
    fields = [_field("verrechnungssteuer", "0.00", "Ertrag Rubrik B 0.00")]
    harden_vrs_fields(fields)
    assert fields[0]["value"] == "0.00"
    assert fields[0]["anchor_valid"] is True


def test_nicht_null_mit_label_bleibt():
    fields = [_field("verrechnungssteuer", "48.90",
                     "Verrechnungssteuer 35% 48.90")]
    harden_vrs_fields(fields)
    assert fields[0]["value"] == "48.90"
    assert fields[0]["anchor_valid"] is True


def test_total_feld_ebenfalls_gehaertet():
    fields = [_field("verrechnungssteuer_total", "120.00",
                     "Subtotal Ertrag 120.00")]
    harden_vrs_fields(fields)
    assert fields[0]["value"] == "manual_review:vrs_label_fehlt"


def test_manual_review_wert_unangetastet():
    fields = [_field("verrechnungssteuer", "manual_review:vrs_not_reported", "")]
    harden_vrs_fields(fields)
    assert fields[0]["value"] == "manual_review:vrs_not_reported"


def test_unparsbarer_wert_bleibt():
    fields = [_field("verrechnungssteuer", "not_in_beleg_by_design", "")]
    harden_vrs_fields(fields)
    assert fields[0]["value"] == "not_in_beleg_by_design"
