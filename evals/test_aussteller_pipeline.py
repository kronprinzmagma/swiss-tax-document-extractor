"""End-to-end-Test für den Aussteller-Lifecycle (Plan 03-06 Task 2).

Verifiziert über zwei sequenzielle ``main()``-Läufe, dass:

1. Lauf 1 mit einem Bank-Zinsausweis legt einen Eintrag in
   ``aussteller.json`` an (Key ``"zkbzürcherkantonalbank__bank_zinsausweis"``).
2. Lauf 2 mit nur einem Lohnausweis (ohne Bank-Beleg) erkennt den Vorjahres-
   Aussteller BANK-Z als fehlend und schreibt ihn in die Report-Sektion
   "Fehlende Vorjahres-Aussteller".
3. ``aussteller.json.bak`` existiert nach Lauf 2 (atomarer Write-Pattern,
   D-B7 / Pitfall 6).

Marker ``@pytest.mark.llm_full``: der Test ruft ``process_one`` zweimal pro
PDF auf, was Ollama braucht (~30s × 2 PDFs ≈ 1 min).
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from click.testing import CliRunner

from steuer_extraktor import main as cli_main

FIXTURES = Path(__file__).parent / "fixtures"
FAMILY_TEST_YAML = Path(__file__).parent / "family.yaml.test"


@pytest.fixture(scope="module", autouse=True)
def _generate_fixtures() -> None:
    """Idempotente Fixture-Generierung (analog ``test_extraction.py``)."""
    if not (FIXTURES / "bank_zinsausweis_standard.pdf").exists():
        from evals.generate import main as gen_main

        gen_main()


@pytest.mark.llm_full
def test_aussteller_lifecycle_end_to_end(tmp_path: Path, model_check: dict) -> None:
    """Zwei sequenzielle Läufe: Lauf 1 schreibt, Lauf 2 erkennt Vorjahres-Aussteller."""
    # --- Setup: isolierter Workdir mit eigener aussteller.json, Output- und
    # Input-Ordner. Belege werden in zwei Sub-Ordner für die zwei Läufe gelegt.
    aussteller_path = tmp_path / "aussteller.json"
    output_dir = tmp_path / "output"
    input_run1 = tmp_path / "input_run1"
    input_run2 = tmp_path / "input_run2"
    input_run1.mkdir()
    input_run2.mkdir()

    # Lauf 1: nur Bank-Zinsausweis (BANK-Z / Hans Muster / 2024).
    shutil.copy(
        FIXTURES / "bank_zinsausweis_standard.pdf",
        input_run1 / "bank_zinsausweis_standard.pdf",
    )
    # Lauf 2: nur Lohnausweis — BANK-Z fehlt → muss als „Fehlend" auftauchen.
    shutil.copy(
        FIXTURES / "lohnausweis_standard.pdf",
        input_run2 / "lohnausweis_standard.pdf",
    )

    runner = CliRunner()

    # --- Lauf 1 ---
    result1 = runner.invoke(
        cli_main,
        [
            "--input", str(input_run1),
            "--aussteller-history", str(aussteller_path),
            "--output", str(output_dir),
            "--family", str(FAMILY_TEST_YAML),
            "--year", "2024",
        ],
        catch_exceptions=False,
    )
    assert result1.exit_code == 0, f"Lauf 1 failed: {result1.output}"

    # aussteller.json existiert, hat den BANK-Z-Eintrag (D-B3 Key-Pattern).
    assert aussteller_path.exists(), "aussteller.json wurde in Lauf 1 nicht angelegt"
    import json
    history = json.loads(aussteller_path.read_text(encoding="utf-8"))
    expected_key = "zkbzürcherkantonalbank__bank_zinsausweis"
    assert expected_key in history, (
        f"Erwarteter Aussteller-Key {expected_key!r} fehlt; got: {list(history.keys())}"
    )
    assert history[expected_key]["last_seen_year"] == 2024
    assert history[expected_key]["belegtyp"] == "bank_zinsausweis"

    # --- Lauf 2: nur Lohnausweis, BANK-Z-Bank fehlt → find_missing-Treffer ---
    result2 = runner.invoke(
        cli_main,
        [
            "--input", str(input_run2),
            "--aussteller-history", str(aussteller_path),
            "--output", str(output_dir),
            "--family", str(FAMILY_TEST_YAML),
            "--year", "2024",
        ],
        catch_exceptions=False,
    )
    assert result2.exit_code == 0, f"Lauf 2 failed: {result2.output}"

    # .bak-Datei existiert (atomarer Write hat das Original gesichert).
    bak_path = aussteller_path.with_suffix(aussteller_path.suffix + ".bak")
    assert bak_path.exists(), "aussteller.json.bak fehlt nach Lauf 2 (atomarer Write)"

    # Report-Sektion „Fehlende Vorjahres-Aussteller" enthält BANK-Z + bank_zinsausweis.
    run_dirs = sorted(output_dir.glob("*"))
    assert len(run_dirs) >= 2, f"Erwartete zwei Run-Dirs, gefunden: {run_dirs}"
    latest_report = run_dirs[-1] / "report.md"
    report_text = latest_report.read_text(encoding="utf-8")
    assert "Fehlende Vorjahres-Aussteller" in report_text, (
        f"Report enthält keinen Vorjahres-Aussteller-Hinweis:\n{report_text}"
    )
    assert "BANK-Z" in report_text, f"Report enthält kein 'BANK-Z':\n{report_text}"
    assert "bank_zinsausweis" in report_text, (
        f"Report enthält kein 'bank_zinsausweis':\n{report_text}"
    )


# --------------------------------------------------------------------------- #
# Finding 7: run_productive_local-Vorjahresvergleich (deterministisch)         #
# --------------------------------------------------------------------------- #

import json

import scripts.run_productive_local as rpl


def _bank_entry(institut: str, year: str) -> dict:
    return {
        "pdf_name": f"{institut}.pdf", "status": "ok",
        "belegtyp": "bank_zinsausweis",
        "fields": [
            {"feld": "institut", "value": institut, "anchor_valid": True},
            {"feld": "jahr", "value": year, "anchor_valid": True},
        ],
    }


def test_lifecycle_meldet_fehlenden_vorjahres_aussteller(tmp_path, capsys):
    # History mit Vorjahres-Aussteller BANK-A (2022); aktueller Lauf hat nur
    # BANK-B (2022) → BANK-A fehlt und muss gemeldet werden.
    import extractors.aussteller as am
    hist = tmp_path / "aussteller.json"
    history: dict = {}
    am.update(history, "BANK-A", "bank_zinsausweis", 2022)
    am.write_json(hist, history)

    results = tmp_path / "_results_full.json"
    results.write_text(json.dumps([_bank_entry("BANK-B", "2022")]))

    rpl._run_aussteller_lifecycle(results, hist)
    err = capsys.readouterr().err
    assert "Vorjahres-Aussteller fehlen" in err
    assert "BANK-A" in err
    # History wurde aktualisiert (BANK-B nun enthalten).
    updated = json.loads(hist.read_text())
    assert any("bank-b" in k for k in updated)


def test_lifecycle_ohne_history_datei_kein_crash(tmp_path, capsys):
    # Fehlende History-Datei → kein Crash, kein Vorjahres-Treffer.
    hist = tmp_path / "fehlt.json"
    results = tmp_path / "_results_full.json"
    results.write_text(json.dumps([_bank_entry("BANK-A", "2022")]))
    rpl._run_aussteller_lifecycle(results, hist)  # darf nicht werfen
    err = capsys.readouterr().err
    assert "fehlen" not in err  # nichts zu melden im Erst-Lauf
    # History wurde neu geschrieben.
    assert hist.exists()


def test_lifecycle_fehlende_results_kein_crash(tmp_path):
    # Fehlende _results_full.json → früher return, kein Crash.
    rpl._run_aussteller_lifecycle(tmp_path / "fehlt.json", tmp_path / "h.json")


# --------------------------------------------------------------------------- #
# Finding 8: grade_run 19. Check check_steueraufstellung_valid (fail-closed)   #
# --------------------------------------------------------------------------- #

def test_check_steueraufstellung_valid_fehlende_xlsx_ist_fail(tmp_path):
    from scripts.grade_run import check_steueraufstellung_valid, CHECKS
    r = check_steueraufstellung_valid(tmp_path)
    assert r.passed is False
    assert "fehlt" in r.summary
    assert check_steueraufstellung_valid in CHECKS
    # 20 seit dem Regressionsschutz durch bestaetigte Werte (260904-rmx).
    assert len(CHECKS) == 20
