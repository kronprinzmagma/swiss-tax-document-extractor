"""Tests fuer scripts/eval_quality.py — Kern der Eval-Qualitaets-Harness.

Ausschliesslich SYNTHETISCHE Fixtures (Fantasy-Daten). Rote Linie: der
Report-String enthaelt NIE einen Feldwert — nur Zahlen, Raten, Feldnamen,
Marker-Namen, Regel-Namen. Der Privacy-Smoke-Test (unten) beweist das
strukturell, indem er asserted, dass keiner der synthetischen Werte im
gerenderten Report auftaucht.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import eval_quality as eq

# Synthetische Markerwerte — wenn einer davon im Report auftaucht, leakt das Tool.
SYNTH_BETRAG = "1'000"
SYNTH_NAME = "Hans Muster"
SYNTH_INSTITUT = "BANK-T"
SYNTH_JAHR_KAPUTT = "12413"


def _field(feld: str, value, anchor_valid: bool = True) -> dict:
    return {"feld": feld, "value": value, "anchor_valid": anchor_valid}


def _entry(belegtyp, fields: list[dict], status: str = "ok",
           pdf_name: str = "synth.pdf") -> dict:
    return {
        "pdf_name": pdf_name,
        "status": status,
        "belegtyp": belegtyp,
        "fields": fields,
    }


def _voller_bank_beleg(pdf_name: str = "voll.pdf", **overrides) -> dict:
    """Bank-Zinsausweis mit allen Pflichtfeldern echt + verankert.

    Pflicht (bank_zinsausweis): institut, kontoinhaber_name, jahr,
    vermoegensstand_3112.
    """
    fields = {
        "institut": _field("institut", SYNTH_INSTITUT),
        "kontoinhaber_name": _field("kontoinhaber_name", SYNTH_NAME),
        "jahr": _field("jahr", "2023"),
        "vermoegensstand_3112": _field("vermoegensstand_3112", SYNTH_BETRAG),
    }
    fields.update(overrides)
    return _entry("bank_zinsausweis", [f for f in fields.values() if f is not None],
                  pdf_name=pdf_name)


# ---------------------------------------------------------------------------
# (a) Pflichtfeld-Abdeckung
# ---------------------------------------------------------------------------


def test_alle_pflichtfelder_erfuellt_coverage_1():
    m = eq.compute_metrics([_voller_bank_beleg()])
    cov = m["pflicht_coverage"]["bank_zinsausweis"]
    assert cov["erfuellt"] == cov["erwartet"]
    assert cov["rate"] == 1.0


def test_platzhalter_zaehlt_als_nicht_erfuellt():
    e = _voller_bank_beleg(jahr=_field("jahr", "nicht angegeben"))
    m = eq.compute_metrics([e])
    cov = m["pflicht_coverage"]["bank_zinsausweis"]
    assert cov["erfuellt"] == cov["erwartet"] - 1
    assert cov["rate"] < 1.0


def test_manual_review_marker_zaehlt_als_nicht_erfuellt():
    e = _voller_bank_beleg(
        institut=_field("institut", "manual_review:hallucination_arbeitgeber"))
    m = eq.compute_metrics([e])
    cov = m["pflicht_coverage"]["bank_zinsausweis"]
    assert cov["erfuellt"] == cov["erwartet"] - 1


def test_not_in_beleg_by_design_zaehlt_als_nicht_erfuellt_conditional_nie_fehlend():
    # vermoegensstand_3112 ist Pflicht — by-design zaehlt als nicht erfuellt.
    e = _voller_bank_beleg(
        vermoegensstand_3112=_field("vermoegensstand_3112", "not_in_beleg_by_design",
                                    anchor_valid=False))
    m = eq.compute_metrics([e])
    cov = m["pflicht_coverage"]["bank_zinsausweis"]
    assert cov["erfuellt"] == cov["erwartet"] - 1
    # bruttoertrag ist conditional (bank_zinsausweis) — fehlt hier ganz, darf
    # die Pflicht-Rate NICHT druecken. erwartet = nur die 4 Pflichtfelder.
    assert cov["erwartet"] == 4


def test_echter_wert_ohne_anker_zaehlt_als_nicht_erfuellt():
    e = _voller_bank_beleg(
        vermoegensstand_3112=_field("vermoegensstand_3112", SYNTH_BETRAG,
                                    anchor_valid=False))
    m = eq.compute_metrics([e])
    cov = m["pflicht_coverage"]["bank_zinsausweis"]
    assert cov["erfuellt"] == cov["erwartet"] - 1


# ---------------------------------------------------------------------------
# (b) Anker-Validity-Rate
# ---------------------------------------------------------------------------


def test_anker_validity_rate_mix():
    # 3 verankert, 1 ohne Anker, 1 manual_review (zaehlt nicht in den Nenner).
    e = _entry("bank_zinsausweis", [
        _field("institut", SYNTH_INSTITUT, anchor_valid=True),
        _field("kontoinhaber_name", SYNTH_NAME, anchor_valid=True),
        _field("jahr", "2023", anchor_valid=True),
        _field("vermoegensstand_3112", SYNTH_BETRAG, anchor_valid=False),
        _field("bruttoertrag", "manual_review:foo", anchor_valid=False),
    ])
    m = eq.compute_metrics([e])
    av = m["anker_validity"]["gesamt"]
    # Nenner: 4 nicht-leere, nicht-manual_review Felder; Zaehler: 3 verankert.
    assert av["mit_anker"] == 3
    assert av["gesamt"] == 4
    assert abs(av["rate"] - 0.75) < 1e-9


# ---------------------------------------------------------------------------
# (c) Marker-Zaehlung
# ---------------------------------------------------------------------------


def test_marker_zaehlung_suffix_gruppiert_plus_separate_counts():
    e = _entry("bank_zinsausweis", [
        _field("institut", "manual_review:hallucination_arbeitgeber"),
        _field("kontoinhaber_name", "manual_review:hallucination_arbeitgeber"),
        _field("vermoegensstand_3112", "manual_review:value_not_in_document_betrag"),
        _field("kontotyp", "nicht angegeben"),            # is_placeholder
        _field("bruttoertrag", "not_in_beleg_by_design"),  # by_design
    ])
    m = eq.compute_metrics([e])
    mk = m["marker"]
    assert mk["manual_review"]["hallucination_arbeitgeber"] == 2
    assert mk["manual_review"]["value_not_in_document_betrag"] == 1
    assert mk["placeholder_total"] == 1
    assert mk["not_in_beleg_by_design_total"] == 1


# ---------------------------------------------------------------------------
# (d) Plausibilitaets-Verletzungen
# ---------------------------------------------------------------------------


def test_plausibilitaet_verletzung_nur_anzahl_kein_werttext():
    # jahr="12413" laesst rule_jahr_plausibel feuern (kein 4-stelliges Jahr).
    e = _voller_bank_beleg(jahr=_field("jahr", SYNTH_JAHR_KAPUTT))
    m = eq.compute_metrics([e])
    plaus = m["plausibility"]["bank_zinsausweis"]
    assert plaus == 1
    assert m["plausibility"]["gesamt"] == 1
    # Sicherstellen, dass nirgends ein Befund-Text mit dem kaputten Wert steht.
    import json
    blob = json.dumps(m)
    assert SYNTH_JAHR_KAPUTT not in blob


# ---------------------------------------------------------------------------
# (e) Belegtyp- + Status-Verteilung
# ---------------------------------------------------------------------------


def test_belegtyp_und_status_verteilung():
    results = [
        _voller_bank_beleg(pdf_name="a.pdf"),
        _voller_bank_beleg(pdf_name="b.pdf"),
        _entry("lohnausweis", [], status="ok", pdf_name="c.pdf"),
        _entry("spenden", [], status="out_of_scope", pdf_name="d.pdf"),
        _entry(None, [], status="unknown_belegtyp", pdf_name="e.pdf"),
        _entry(None, [], status="empty", pdf_name="f.pdf"),
    ]
    m = eq.compute_metrics(results)
    assert m["belegtyp_verteilung"]["bank_zinsausweis"] == 2
    assert m["belegtyp_verteilung"]["lohnausweis"] == 1
    assert m["status_verteilung"]["ok"] == 3
    assert m["status_verteilung"]["out_of_scope"] == 1
    assert m["status_verteilung"]["unknown_belegtyp"] == 1
    assert m["status_verteilung"]["empty"] == 1


# ---------------------------------------------------------------------------
# Privacy-Smoke-Test — die strukturelle Garantie
# ---------------------------------------------------------------------------


def test_privacy_smoke_kein_feldwert_im_report():
    results = [
        _voller_bank_beleg(pdf_name="a.pdf"),
        _voller_bank_beleg(pdf_name="b.pdf",
                           jahr=_field("jahr", SYNTH_JAHR_KAPUTT)),
        _entry("bank_zinsausweis", [
            _field("institut", "manual_review:hallucination_arbeitgeber"),
        ], pdf_name="c.pdf"),
    ]
    m = eq.compute_metrics(results)
    report = eq.render_report(m)
    assert SYNTH_BETRAG not in report
    assert SYNTH_NAME not in report
    assert SYNTH_INSTITUT not in report
    assert SYNTH_JAHR_KAPUTT not in report
    # Marker-NAMEN duerfen erscheinen (das sind keine Feldwerte).
    assert "hallucination_arbeitgeber" in report


def test_render_report_enthaelt_kern_sektionen():
    m = eq.compute_metrics([_voller_bank_beleg()])
    report = eq.render_report(m)
    assert "Pflicht" in report
    assert "Anker" in report
    assert "Status" in report
