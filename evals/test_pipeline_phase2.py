"""Phase-2-Pipeline-Tests (Plan 02-05 Task 1).

Validiert das Schema-Dispatch-Skelett in :mod:`steuer_extraktor`:

* SCHEMA_DISPATCH-Map deckt genau die 4 Belegtypen.
* AMOUNT_FIELDS_FOR_BELEGTYP / _AUSSTELLER_FIELD_FOR_BELEGTYP konsistent.
* ``process_one`` akzeptiert die neue Signatur ``(pdf_path, family, year)``.
* ``ProcessResult`` hat ``person`` und ``person_anchor_page``.
* CSV-Helper befüllt die ``person``-Spalte aus ``ProcessResult.person``.

Diese Tests sind LLM-frei (Unit-Tests gegen Konstanten + Datentypen). Die
End-to-End-LLM-Coverage liegt in test_extraction.py (Task 3).
"""
from __future__ import annotations

from pathlib import Path

from extractors.output import (
    CSV_COLUMNS,
    FieldRow,
    ProcessResult,
    _row_for_field,
)
from extractors.schema import (
    BankZinsausweisRaw,
    KkPraemienbescheinigungRaw,
    LohnausweisRaw,
    MANDATORY_FIELDS_FOR_BELEGTYP,
    Saeule3aRaw,
)


def test_schema_dispatch_keys() -> None:
    """SCHEMA_DISPATCH enthält die 4 Phase-2-Belegtypen mit ihren Raw-Klassen.

    Plan 03-06 hat SCHEMA_DISPATCH auf 11 Einträge erweitert; dieser Test
    verifiziert weiterhin den Phase-2-Subset (Lohnausweis + Bank + KK + 3a)
    sowohl in der Key-Menge als auch über das Klassen-Mapping.
    """
    from steuer_extraktor import SCHEMA_DISPATCH

    phase2_subset = {
        "lohnausweis",
        "bank_zinsausweis",
        "kk_praemienbescheinigung",
        "saeule_3a",
    }
    assert phase2_subset.issubset(set(SCHEMA_DISPATCH.keys()))
    assert SCHEMA_DISPATCH["lohnausweis"] is LohnausweisRaw
    assert SCHEMA_DISPATCH["bank_zinsausweis"] is BankZinsausweisRaw
    assert SCHEMA_DISPATCH["kk_praemienbescheinigung"] is KkPraemienbescheinigungRaw
    assert SCHEMA_DISPATCH["saeule_3a"] is Saeule3aRaw


def test_schema_dispatch_consistent_with_mandatory_fields() -> None:
    """Jeder Pflichtfeldname muss tatsächlich Schema-Feld der Klasse sein."""
    from steuer_extraktor import SCHEMA_DISPATCH

    for belegtyp, schema in SCHEMA_DISPATCH.items():
        for fname in MANDATORY_FIELDS_FOR_BELEGTYP[belegtyp]:
            assert fname in schema.model_fields, (
                f"{belegtyp}: Pflichtfeld {fname!r} fehlt im Schema {schema.__name__}"
            )


def test_amount_fields_subset_of_schema() -> None:
    """AMOUNT_FIELDS_FOR_BELEGTYP enthält nur deklarierte Schema-Felder."""
    from steuer_extraktor import AMOUNT_FIELDS_FOR_BELEGTYP, SCHEMA_DISPATCH

    for belegtyp, fields in AMOUNT_FIELDS_FOR_BELEGTYP.items():
        schema = SCHEMA_DISPATCH[belegtyp]
        unknown = fields - set(schema.model_fields.keys())
        assert not unknown, f"{belegtyp}: AMOUNT_FIELDS hat Phantom-Felder {unknown}"


def test_aussteller_field_for_belegtyp_valid() -> None:
    """Jeder _AUSSTELLER_FIELD_FOR_BELEGTYP-Wert ist ein Pflichtfeld der Klasse."""
    from steuer_extraktor import _AUSSTELLER_FIELD_FOR_BELEGTYP, SCHEMA_DISPATCH

    for belegtyp, fname in _AUSSTELLER_FIELD_FOR_BELEGTYP.items():
        schema = SCHEMA_DISPATCH[belegtyp]
        assert fname in schema.model_fields, (
            f"{belegtyp}: Aussteller-Feld {fname!r} fehlt im Schema {schema.__name__}"
        )
        assert fname in MANDATORY_FIELDS_FOR_BELEGTYP[belegtyp], (
            f"{belegtyp}: Aussteller-Feld {fname!r} sollte Pflichtfeld sein"
        )


def test_process_one_signature_accepts_family_and_year() -> None:
    """process_one akzeptiert (pdf_path, family, year) — rückwärtskompatibel."""
    import inspect

    from steuer_extraktor import process_one

    sig = inspect.signature(process_one)
    params = list(sig.parameters.keys())
    assert params[0] == "pdf_path"
    assert "family" in params
    assert "year" in params
    # Default-Args müssen rückwärtskompatibel sein.
    assert sig.parameters["family"].default is None
    assert sig.parameters["year"].default is None


def test_process_result_has_person_fields() -> None:
    """ProcessResult muss ``person`` und ``person_anchor_page`` haben (PER-01..03)."""
    pr = ProcessResult(bucket="extracted", pdf_path=Path("dummy.pdf"))
    assert hasattr(pr, "person")
    assert hasattr(pr, "person_anchor_page")
    assert pr.person is None
    assert pr.person_anchor_page is None


def test_csv_row_uses_process_result_person() -> None:
    """_row_for_field füllt die `person`-Spalte aus ProcessResult.person."""
    pr = ProcessResult(
        bucket="extracted",
        pdf_path=Path("foo.pdf"),
        belegtyp="bank_zinsausweis",
        person="frau",
    )
    fr = FieldRow(
        feld="bruttoertrag",
        wert_raw="1'250.00",
        wert_decimal="1250.00",
        konfidenz=0.95,
        anchor_valid=True,
        anchor_page=1,
        anchor_bbox=[1.0, 2.0, 3.0, 4.0],
        snippet="...1'250.00...",
    )
    row = _row_for_field(pr, fr)
    assert row["person"] == "frau"
    # CSV-Spalten-Reihenfolge bleibt locked.
    assert "person" in CSV_COLUMNS


def test_csv_row_person_empty_when_none() -> None:
    """Wenn ProcessResult.person None ist, bleibt CSV-Spalte leer (Phase-1-Verhalten)."""
    pr = ProcessResult(
        bucket="extracted",
        pdf_path=Path("foo.pdf"),
        belegtyp="lohnausweis",
        person=None,
    )
    fr = FieldRow(
        feld="bruttolohn_pos8",
        wert_raw="95'400.00",
        wert_decimal="95400.00",
        konfidenz=1.0,
        anchor_valid=True,
        anchor_page=1,
        anchor_bbox=[0.0, 0.0, 1.0, 1.0],
        snippet="…",
    )
    row = _row_for_field(pr, fr)
    assert row["person"] == ""


def test_currency_filled_for_all_4_belegtypen() -> None:
    """Bank/KK/3a-Belege bekommen ebenfalls 'CHF' in der `währung`-Spalte."""
    fr = FieldRow(
        feld="bruttoertrag",
        wert_raw="100.00",
        wert_decimal="100.00",
        konfidenz=1.0,
        anchor_valid=True,
        anchor_page=1,
        anchor_bbox=None,
        snippet="…",
    )
    for belegtyp in (
        "lohnausweis",
        "bank_zinsausweis",
        "kk_praemienbescheinigung",
        "saeule_3a",
    ):
        pr = ProcessResult(bucket="extracted", pdf_path=Path("x.pdf"), belegtyp=belegtyp)
        row = _row_for_field(pr, fr)
        assert row["währung"] == "CHF", f"{belegtyp}: währung != CHF"
