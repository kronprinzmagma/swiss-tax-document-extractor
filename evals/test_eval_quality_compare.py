"""Tests fuer den Compare-Modus + Truth-Match-Layer von scripts/eval_quality.py.

Ausschliesslich SYNTHETISCHE Laeufe + synthetische truth.json (in tmp_path,
niemals echte Dateien). Rote Linie: weder der Compare-Report noch die
Truth-Sektion des Reports enthalten je einen Feldwert oder Truth-String —
nur Zahlen, Raten, Feldnamen, Marker-Namen.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import eval_quality as eq

SYNTH_A = "1'000"
SYNTH_B = "2'000"
SYNTH_NAME = "Hans Muster"
SYNTH_INSTITUT = "BANK-T"


def _field(feld: str, value, anchor_valid: bool = True) -> dict:
    return {"feld": feld, "value": value, "anchor_valid": anchor_valid}


def _entry(belegtyp, fields: list[dict], pdf_name: str, status: str = "ok") -> dict:
    return {"pdf_name": pdf_name, "status": status, "belegtyp": belegtyp,
            "fields": fields}


def _bank(pdf: str, vermoegen=SYNTH_A, **overrides) -> dict:
    fields = {
        "institut": _field("institut", SYNTH_INSTITUT),
        "kontoinhaber_name": _field("kontoinhaber_name", SYNTH_NAME),
        "jahr": _field("jahr", "2023"),
        "vermoegensstand_3112": _field("vermoegensstand_3112", vermoegen),
    }
    fields.update(overrides)
    return _entry("bank_zinsausweis", list(fields.values()), pdf)


# ---------------------------------------------------------------------------
# Compare-Modus
# ---------------------------------------------------------------------------


def test_compare_delta_tabelle():
    # Lauf A: 1 Beleg mit kaputtem Pflichtfeld (Platzhalter jahr).
    a = [_bank("x.pdf", jahr=_field("jahr", "nicht angegeben"))]
    # Lauf B: derselbe Beleg vollstaendig → Delta in Pflicht-Abdeckung positiv.
    b = [_bank("x.pdf")]
    ma, mb = eq.compute_metrics(a), eq.compute_metrics(b)
    cmp = eq.compute_compare(ma, mb, a, b)
    d = cmp["delta"]["pflicht_coverage_gesamt"]
    assert d["a"] < d["b"]
    assert d["delta"] > 0


def test_compare_wert_unterschied_zaehler_kein_wert():
    a = [_bank("x.pdf", vermoegen=SYNTH_A)]
    b = [_bank("x.pdf", vermoegen=SYNTH_B)]
    ma, mb = eq.compute_metrics(a), eq.compute_metrics(b)
    cmp = eq.compute_compare(ma, mb, a, b)
    assert cmp["wert_unterschiede"] == 1
    assert cmp["gematchte_belege"] == 1
    report = eq.render_compare_report(cmp)
    assert SYNTH_A not in report
    assert SYNTH_B not in report


def test_compare_hallucination_fokus_verbesserung():
    # Lauf A hat 2 hallucination-Marker, Lauf B nur 1 → Delta negativ (besser).
    a = [_bank("x.pdf",
               institut=_field("institut", "manual_review:hallucination_arbeitgeber"),
               kontoinhaber_name=_field("kontoinhaber_name",
                                        "manual_review:hallucination_arbeitgeber"))]
    b = [_bank("x.pdf",
               institut=_field("institut", "manual_review:hallucination_arbeitgeber"))]
    ma, mb = eq.compute_metrics(a), eq.compute_metrics(b)
    cmp = eq.compute_compare(ma, mb, a, b)
    d = cmp["delta"]["marker_total"]
    assert d["delta"] < 0  # weniger Marker in B = Verbesserung


def test_self_compare_delta_null():
    a = [_bank("x.pdf"), _bank("y.pdf", vermoegen=SYNTH_B)]
    ma = eq.compute_metrics(a)
    cmp = eq.compute_compare(ma, ma, a, a)
    assert cmp["wert_unterschiede"] == 0
    assert cmp["delta"]["pflicht_coverage_gesamt"]["delta"] == 0
    assert cmp["delta"]["marker_total"]["delta"] == 0


# ---------------------------------------------------------------------------
# Truth-Match-Layer
# ---------------------------------------------------------------------------


def _write_truth(samples_dir: Path, stem: str, fields: dict) -> None:
    """Schreibt eine synthetische truth.json (fields.{feld}.value)."""
    truth = {"fields": {f: {"value": v} for f, v in fields.items()}}
    (samples_dir / f"{stem}.truth.json").write_text(json.dumps(truth))


def test_truth_layer_match(tmp_path):
    # Result-vermoegensstand enthaelt den Truth-Wert als Substring → Match.
    results = [_bank("kontoauszug.pdf", vermoegen="1'234.50")]
    _write_truth(tmp_path, "kontoauszug", {"vermoegensstand_3112": "1234.50"})
    tm = eq.compute_truth_match(results, tmp_path)
    g = tm["gesamt"]
    assert g["match_total"] >= 1
    assert g["match_korrekt"] >= 1
    assert not tm["wert_korrektheit_ungeprueft"]


def test_truth_layer_anonymisierte_felder_uebersprungen(tmp_path):
    results = [_bank("kontoauszug.pdf")]
    # institut + kontoinhaber_name sind anonymisiert-nicht-matchbar.
    _write_truth(tmp_path, "kontoauszug", {
        "institut": "Echte Bank AG",
        "kontoinhaber_name": "Echter Name",
        "vermoegensstand_3112": "1'000",
    })
    tm = eq.compute_truth_match(results, tmp_path)
    g = tm["gesamt"]
    assert g["uebersprungen_nicht_matchbar"] == 2  # institut + kontoinhaber_name
    # institut/name werden NICHT als Fail gezaehlt — nur vermoegensstand matchbar.
    assert g["match_total"] == 1


def test_truth_layer_honesty_keine_truth_files(tmp_path):
    results = [_bank("kontoauszug.pdf")]
    # tmp_path ist leer → 0 Truth-Files.
    tm = eq.compute_truth_match(results, tmp_path)
    assert tm["truth_files_gefunden"] == 0
    assert tm["wert_korrektheit_ungeprueft"] is True


def test_truth_layer_honesty_keine_matchbaren_felder(tmp_path):
    results = [_bank("kontoauszug.pdf")]
    # Nur anonymisierte Felder in der Truth → 0 matchbare Felder.
    _write_truth(tmp_path, "kontoauszug", {
        "institut": "Echte Bank AG",
        "kontoinhaber_name": "Echter Name",
    })
    tm = eq.compute_truth_match(results, tmp_path)
    assert tm["match_total"] == 0
    assert tm["wert_korrektheit_ungeprueft"] is True


# ---------------------------------------------------------------------------
# Privacy-Smoke-Tests
# ---------------------------------------------------------------------------


def test_privacy_smoke_compare_report():
    a = [_bank("x.pdf", vermoegen=SYNTH_A)]
    b = [_bank("x.pdf", vermoegen=SYNTH_B)]
    ma, mb = eq.compute_metrics(a), eq.compute_metrics(b)
    cmp = eq.compute_compare(ma, mb, a, b)
    report = eq.render_compare_report(cmp)
    assert SYNTH_A not in report
    assert SYNTH_B not in report
    assert SYNTH_NAME not in report
    assert SYNTH_INSTITUT not in report


def test_privacy_smoke_truth_sektion(tmp_path):
    results = [_bank("kontoauszug.pdf", vermoegen="1'234.50")]
    _write_truth(tmp_path, "kontoauszug", {"vermoegensstand_3112": "1234.50"})
    tm = eq.compute_truth_match(results, tmp_path)
    metrics = eq.compute_metrics(results)
    report = eq.render_report(metrics, truth_match=tm)
    assert "1234.50" not in report
    assert "1'234.50" not in report
    assert SYNTH_NAME not in report
    assert SYNTH_INSTITUT not in report
    # Die ehrliche Sektion-Ueberschrift muss da sein.
    assert "Wert-Korrektheit" in report
