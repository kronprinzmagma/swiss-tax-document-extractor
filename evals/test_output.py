"""Tests für ``extractors/output.py`` — Bucket-Routing, CSV-Format, Pre-/Post-Count."""
from __future__ import annotations

import csv
import json
from pathlib import Path

from extractors.output import (
    CSV_COLUMNS,
    UNPROCESSED_COLUMNS,
    FieldRow,
    ProcessResult,
    write_csvs,
    write_report,
)


def _mk_field(feld: str = "bruttolohn_pos8") -> FieldRow:
    return FieldRow(
        feld=feld,
        wert_raw="95'400.00",
        wert_decimal="95400.00",
        konfidenz=0.95,
        anchor_valid=True,
        anchor_page=1,
        anchor_bbox=[1.0, 2.0, 3.0, 4.0],
        snippet="95'400.00",
    )


def test_write_csvs_columns(tmp_path: Path) -> None:
    res = ProcessResult(
        bucket="extracted",
        pdf_path=Path("/tmp/x.pdf"),
        belegtyp="lohnausweis",
        aussteller="ACME AG",
        datum_iso="2024-12-31",
        fields=[_mk_field()],
    )
    write_csvs(tmp_path, {"extracted": [res], "unverified": [], "unprocessed": []})
    with (tmp_path / "extraction.csv").open() as f:
        rows = list(csv.DictReader(f))
    assert list(rows[0].keys()) == CSV_COLUMNS
    assert rows[0]["belegtyp"] == "lohnausweis"
    assert rows[0]["aussteller"] == "ACME AG"
    assert rows[0]["person"] == ""  # D-D4
    assert rows[0]["währung"] == "CHF"
    assert json.loads(rows[0]["anker_bbox"]) == [1.0, 2.0, 3.0, 4.0]


def test_write_csvs_unprocessed(tmp_path: Path) -> None:
    res = ProcessResult(
        bucket="unprocessed",
        pdf_path=Path("/tmp/y.pdf"),
        belegtyp="unknown",
        reason_code="unsupported_type",
        error="kein Lohnausweis-Header",
    )
    write_csvs(tmp_path, {"extracted": [], "unverified": [], "unprocessed": [res]})
    with (tmp_path / "unprocessed.csv").open() as f:
        rows = list(csv.DictReader(f))
    assert list(rows[0].keys()) == UNPROCESSED_COLUMNS
    assert rows[0]["reason_code"] == "unsupported_type"
    assert rows[0]["datei"] == "y.pdf"


def test_write_csvs_empty_buckets_have_headers(tmp_path: Path) -> None:
    """Auch bei leeren Buckets müssen alle 3 Dateien mit Header existieren."""
    write_csvs(tmp_path, {"extracted": [], "unverified": [], "unprocessed": []})
    for name in ("extraction.csv", "unverified.csv", "unprocessed.csv"):
        assert (tmp_path / name).exists(), f"{name} fehlt"
    # Header-Check
    with (tmp_path / "extraction.csv").open() as f:
        assert next(csv.reader(f)) == CSV_COLUMNS
    with (tmp_path / "unprocessed.csv").open() as f:
        assert next(csv.reader(f)) == UNPROCESSED_COLUMNS


def test_write_report(tmp_path: Path) -> None:
    res = ProcessResult(
        bucket="extracted",
        pdf_path=Path("/tmp/x.pdf"),
        belegtyp="lohnausweis",
        fields=[_mk_field()],
    )
    write_report(
        tmp_path,
        {"extracted": [res], "unverified": [], "unprocessed": []},
        model_info={"model": "qwen2.5:7b-instruct-q4_K_M", "digest": "abc123def456ghi"},
    )
    report = (tmp_path / "report.md").read_text()
    assert "qwen2.5:7b-instruct-q4_K_M" in report
    assert "PDFs total" in report
    # Plan 02-05: Belegtyp-Aggregat-Tabelle (statt Phase-1-Bullet-Liste).
    assert "Belegtyp-Aggregat" in report
    assert "lohnausweis" in report
    # Privacy-Warnungen-Sektion fehlt, wenn keine Family geladen.
    assert "Privacy-Warnungen" not in report


def test_pre_post_count_assert_concept() -> None:
    """Konzept-Test: Aufrufer prüft, dass alle Buckets zusammen == n_input."""
    n_input = 3
    buckets = {
        "extracted": [ProcessResult(bucket="extracted", pdf_path=Path("a.pdf"))],
        "unverified": [ProcessResult(bucket="unverified", pdf_path=Path("b.pdf"))],
        "unprocessed": [ProcessResult(bucket="unprocessed", pdf_path=Path("c.pdf"))],
    }
    assert n_input == sum(len(b) for b in buckets.values())


def test_csv_columns_count_locked() -> None:
    """Locked Schema: 13 Spalten in extraction.csv, 4 in unprocessed.csv."""
    assert len(CSV_COLUMNS) == 13
    assert len(UNPROCESSED_COLUMNS) == 4
