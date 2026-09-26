"""Tests fuer scripts/bank_matrix.py — Status-Matrix der 5 Bank-Pflichtfelder.

Ausschliesslich synthetische Fixtures (Fantasy-Daten). Kernzusicherung:
der Output enthaelt NIE Feldwerte, nur Status-Codes und Dateinamen.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import bank_matrix as bm

SYNTH_BETRAG = "12'345.67"
SYNTH_NAME = "Hans Muster"


def _field(feld: str, value, anchor_valid: bool = True) -> dict:
    return {"feld": feld, "value": value, "anchor_valid": anchor_valid}


def _bank_entry(pdf: str, **overrides) -> dict:
    fields = {
        "institut": _field("institut", "BANK-T"),
        "kontotyp": _field("kontotyp", "Sparkonto"),
        "kontoinhaber_name": _field("kontoinhaber_name", SYNTH_NAME),
        "vermoegensstand_3112": _field("vermoegensstand_3112", SYNTH_BETRAG),
        "bruttoertrag": _field("bruttoertrag", "12.50"),
    }
    fields.update(overrides)
    return {
        "pdf_name": pdf, "status": "ok", "belegtyp": "bank_zinsausweis",
        "fields": [f for f in fields.values() if f is not None],
    }


def test_vollstaendiger_beleg_alle_haken():
    rows, kopf = bm.build_matrix([_bank_entry("voll.pdf")])
    assert "1/1 Belege mit 5/5" in kopf
    assert rows[0].count(bm.OK_ANCHORED) == 5


def test_fehlendes_feld_und_marker():
    e = _bank_entry(
        "teilweise.pdf",
        kontotyp=None,
        bruttoertrag=_field("bruttoertrag", "manual_review:unklar"),
    )
    rows, kopf = bm.build_matrix([e])
    assert "0/1 Belege" in kopf
    assert bm.GAP_FEHLT in rows[0]
    assert bm.GAP_MARKER in rows[0]


def test_unparsebarer_betrag():
    e = _bank_entry("kaputt.pdf", vermoegensstand_3112=_field("vermoegensstand_3112", "abc"))
    rows, _ = bm.build_matrix([e])
    assert bm.GAP_UNPARSEBAR in rows[0]


def test_wert_ohne_anker_zaehlt_als_vorhanden():
    e = _bank_entry("ohne_anker.pdf", institut=_field("institut", "BANK-T", anchor_valid=False))
    rows, kopf = bm.build_matrix([e])
    assert bm.OK_UNANCHORED in rows[0]
    assert "1/1 Belege" in kopf  # ohne Anker zaehlt als vorhanden


def test_keine_feldwerte_im_output():
    rows, kopf = bm.build_matrix([_bank_entry("privacy.pdf")])
    out = kopf + "\n" + "\n".join(rows)
    assert SYNTH_BETRAG not in out
    assert SYNTH_NAME not in out
    assert "Sparkonto" not in out
    assert "BANK-T" not in out


def test_nicht_bank_belege_ignoriert():
    e = {"pdf_name": "lohn.pdf", "status": "ok", "belegtyp": "lohnausweis", "fields": []}
    rows, kopf = bm.build_matrix([e])
    assert rows == []
    assert "0/0" in kopf


def test_not_in_beleg_by_design_zaehlt_als_erfuellt():
    # Eine verifizierte Feld-Abwesenheit (Bruttozins by-design nicht im Beleg)
    # darf den Beleg nicht aus 5/5 werfen — bekommt aber ein EIGENES Symbol
    # (n/a), NICHT ○ (Finding 6).
    e = _bank_entry(
        "by_design.pdf",
        bruttoertrag=_field("bruttoertrag", "not_in_beleg_by_design", anchor_valid=False),
    )
    st = bm.field_status(
        {"feld": "bruttoertrag", "value": "not_in_beleg_by_design", "anchor_valid": False},
        ist_betrag=True,
    )
    assert st == bm.NA_BY_DESIGN
    assert st != bm.OK_UNANCHORED
    rows, kopf = bm.build_matrix([e])
    assert "1/1 Belege mit 5/5" in kopf
    assert bm.GAP_UNPARSEBAR not in rows[0]


def test_kopfzeile_weist_anker_ohne_anker_und_na_separat_aus():
    # Drei Belege: einer voll verankert, einer mit ○ (institut ohne Anker),
    # einer mit by-design-bruttoertrag (n/a). Kopfzeile muss alle drei
    # Kategorien getrennt zählen.
    voll = _bank_entry("voll.pdf")
    ohne_anker = _bank_entry(
        "ohne_anker.pdf", institut=_field("institut", "BANK-T", anchor_valid=False)
    )
    by_design = _bank_entry(
        "by_design.pdf",
        bruttoertrag=_field("bruttoertrag", "not_in_beleg_by_design", anchor_valid=False),
    )
    _, kopf = bm.build_matrix([voll, ohne_anker, by_design])
    assert "○ ohne Anker" in kopf
    assert "n/a by design" in kopf
    assert "✓ verankert" in kopf
    # Genau ein ○ (institut) und genau ein n/a (bruttoertrag).
    assert "1 ○ ohne Anker" in kopf
    assert "1 n/a by design" in kopf
