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

import pytest

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


# ---------------------------------------------------------------------------
# Zeilen-Kontext (2026-11-19): Der Anker-Snippet enthaelt beim Verbatim-Anker
# nur den Betrag selbst. Ohne Blick auf die Zeile wurde jede Nicht-Null-VSt
# demotiert, auch «Verrechnungssteuer (35%) 437.50».
# ---------------------------------------------------------------------------


def _w(text: str, x0: float, top: float, page: int = 1) -> dict:
    return {"text": text, "x0": x0, "x1": x0 + 8 * len(text), "top": top,
            "bottom": top + 10, "page": page}


def _vst_feld(value: str, x0: float, top: float, feld: str = "verrechnungssteuer") -> dict:
    return {"feld": feld, "value": value, "snippet": value, "anchor_valid": True,
            "bbox": [x0, top, x0 + 40, top + 10], "page": 1}


def test_label_links_in_der_zeile_bleibt():
    words = [_w("Verrechnungssteuer", 50, 204), _w("(35%)", 210, 204), _w("437.50", 400, 204)]
    fields = [_vst_feld("437.50", 400, 204)]
    harden_vrs_fields(fields, words=words)
    assert fields[0]["value"] == "437.50"
    assert fields[0]["anchor_valid"] is True


def test_total_feld_mit_label_in_der_zeile_bleibt():
    words = [_w("Verrechnungssteuer", 50, 248), _w("(35%)", 210, 248), _w("4'340.00", 400, 248)]
    fields = [_vst_feld("4'340.00", 400, 248, feld="verrechnungssteuer_total")]
    harden_vrs_fields(fields, words=words)
    assert fields[0]["value"] == "4'340.00"


def test_ertragsspalte_mit_vst_weiter_rechts_wird_demotiert():
    """Label rechts vom Betrag zaehlt nicht: der Betrag ist der Ertrag."""
    words = [_w("Ertrag", 50, 300), _w("48.90", 200, 300),
             _w("Verrechnungssteuer", 260, 300), _w("17.12", 420, 300)]
    fields = [_vst_feld("48.90", 200, 300)]
    harden_vrs_fields(fields, words=words)
    assert fields[0]["value"] == "manual_review:vrs_label_fehlt"


def test_label_vor_anderem_betrag_zaehlt_nicht():
    """«Verrechnungssteuer 17.12 Ertrag 48.90»: 48.90 hat das Label «Ertrag»."""
    words = [_w("Verrechnungssteuer", 50, 300), _w("17.12", 200, 300),
             _w("Ertrag", 260, 300), _w("48.90", 420, 300)]
    fields = [_vst_feld("48.90", 420, 300)]
    harden_vrs_fields(fields, words=words)
    assert fields[0]["value"] == "manual_review:vrs_label_fehlt"


def test_label_in_zeile_darueber_bleibt():
    """Zweizeilige Beschriftung: Label steht links oberhalb, Zeile selbst leer."""
    words = [_w("Verrechnungssteuer", 50, 280), _w("437.50", 400, 300)]
    fields = [_vst_feld("437.50", 400, 300)]
    harden_vrs_fields(fields, words=words)
    assert fields[0]["value"] == "437.50"


def test_andere_zeile_ohne_label_wird_demotiert():
    words = [_w("Ertrag", 50, 300), _w("Rubrik", 110, 300), _w("B", 170, 300),
             _w("48.90", 400, 300)]
    fields = [_vst_feld("48.90", 400, 300)]
    harden_vrs_fields(fields, words=words)
    assert fields[0]["value"] == "manual_review:vrs_label_fehlt"


# ---------------------------------------------------------------------------
# Code-Review 2026-11-19: Eine Beschriftung, die die VSt nur erwaehnt, aber
# einen anderen Betrag beschriftet, darf den Betrag nicht als VSt durchlassen.
# Im Zweifel manuelle Pruefung wie vor #47.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("feld", ["verrechnungssteuer", "verrechnungssteuer_total"])
@pytest.mark.parametrize("beschriftung, betrag", [
    (["Bruttoertrag", "mit", "VSt"], "1'250.00"),
    (["Nettozins", "nach", "Abzug", "Verrechnungssteuer"], "812.50"),
    (["Ertrag", "Rubrik", "A"], "1'250.00"),
    (["Bruttoertrag", "vor", "Verrechnungssteuer"], "1'250.00"),
    (["VSt-pflichtiger", "Ertrag"], "1'250.00"),
    (["Verrechnungssteuer", "auf", "Zinsen"], "1'250.00"),
])
def test_vst_wort_in_fremder_beschriftung_wird_demotiert(feld, beschriftung, betrag):
    words, x = [], 50
    for wort in beschriftung:
        words.append(_w(wort, x, 300))
        x += 8 * len(wort) + 10
    words.append(_w(betrag, 420, 300))
    fields = [_vst_feld(betrag, 420, 300, feld=feld)]
    harden_vrs_fields(fields, words=words)
    assert fields[0]["value"] == "manual_review:vrs_label_fehlt"


def test_bruttobetrag_nach_vst_satz_wird_demotiert():
    """«Verrechnungssteuer 35% von 1'250.00 437.50»: 1'250.00 ist der Bruttobetrag."""
    words = [_w("Verrechnungssteuer", 50, 300), _w("35%", 210, 300), _w("von", 250, 300),
             _w("1'250.00", 300, 300), _w("437.50", 420, 300)]
    fields = [_vst_feld("1'250.00", 300, 300)]
    harden_vrs_fields(fields, words=words)
    assert fields[0]["value"] == "manual_review:vrs_label_fehlt"


def test_titel_in_zeile_darueber_zaehlt_nicht():
    """Zahlenzeile ohne Text links unter dem Titel «Erträge mit Verrechnungssteuer»."""
    words = [_w("Erträge", 50, 280), _w("mit", 110, 280), _w("Verrechnungssteuer", 140, 280),
             _w("1'250.00", 400, 300), _w("437.50", 500, 300)]
    fields = [_vst_feld("1'250.00", 400, 300, feld="verrechnungssteuer_total")]
    harden_vrs_fields(fields, words=words)
    assert fields[0]["value"] == "manual_review:vrs_label_fehlt"


@pytest.mark.parametrize("beschriftung", [
    ["Verrechnungssteuer", "(35%)"],
    ["Verrechnungssteuer"],
    ["Total", "Verrechnungssteuer"],
    ["Abzug", "VSt", "35%"],
])
def test_reine_vst_beschriftung_bleibt(beschriftung):
    words, x = [], 50
    for wort in beschriftung:
        words.append(_w(wort, x, 300))
        x += 8 * len(wort) + 10
    words.append(_w("437.50", 420, 300))
    fields = [_vst_feld("437.50", 420, 300)]
    harden_vrs_fields(fields, words=words)
    assert fields[0]["value"] == "437.50"


def test_wsv_vrs_obergrenze():
    from extractors.steuer_zielmodell import rule_wsv_vrs_plausibel

    # Bruttoertrag als VSt gegriffen: groesser als 35 % → Befund
    assert rule_wsv_vrs_plausibel({"bruttoertrag_total": "3'200.00",
                                   "verrechnungssteuer_total": "3'200.00"})
    # genau 35 % und Rundung innerhalb der Toleranz → kein Befund
    assert rule_wsv_vrs_plausibel({"bruttoertrag_total": "3'200.00",
                                   "verrechnungssteuer_total": "1'120.00"}) == []
    assert rule_wsv_vrs_plausibel({"bruttoertrag_total": "3'200.00",
                                   "verrechnungssteuer_total": "1'120.90"}) == []
    # Auslandsanteil (weniger als 35 %) bleibt erlaubt
    assert rule_wsv_vrs_plausibel({"bruttoertrag_total": "3'200.00",
                                   "verrechnungssteuer_total": "700.00"}) == []
