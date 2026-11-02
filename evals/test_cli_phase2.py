"""Phase-2-CLI-Tests (Plan 02-05 Task 2).

Smoke-Tests für das ``--family``-Flag, das Privacy-Warnungs-Verhalten
(D-A4, T-02-16: AHV-Klartext darf nicht in Logs/Reports) und die
Belegtyp-Aggregat-Sektion in ``write_report``.

Tests die einen vollen Run benötigen, bleiben Sache der manuellen
Verify-Checkpoint (Task 4) — hier prüfen wir nur die CLI-Verdrahtung
selbst, ohne tatsächliche Ollama-Aufrufe.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from click.testing import CliRunner

from extractors.output import ProcessResult, write_report
from steuer_extraktor import main


def flat(output: str) -> str:
    """Zeilenumbrueche und Mehrfach-Leerzeichen zu einfachen Leerzeichen glaetten.

    rich bricht die CLI-Ausgabe an der Terminalbreite um. Lokal (breites
    Terminal) blieb "nicht gefunden" zusammen, in CI (80 Spalten) wurde
    daraus "nicht\ngefunden" — der Test schlug fehl, obwohl die Ausgabe
    inhaltlich korrekt war (2026-08-18). Assertions auf Ausgabetext laufen
    darum ueber diesen Helfer statt direkt gegen r.output.
    """
    return re.sub(r"\s+", " ", output)


def test_help_lists_family_option() -> None:
    """``--help`` zeigt das neue --family-Flag."""
    r = CliRunner().invoke(main, ["--help"])
    assert r.exit_code == 0, r.output
    assert "--family" in flat(r.output)
    # Phase-1-Optionen bleiben sichtbar.
    for opt in ("--input", "--output", "--engine", "--year", "--aussteller-history"):
        assert opt in flat(r.output), f"Option {opt} fehlt in --help"


def test_engine_cloud_still_blocked() -> None:
    """PRV-01 bleibt aktiv: --engine cloud → UsageError (exit 2)."""
    r = CliRunner().invoke(main, ["--engine", "cloud"])
    assert r.exit_code != 0
    assert "Cloud-Engine" in flat(r.output) or "cloud" in flat(r.output).lower()


def test_family_nonexistent_explicit_path_warns_and_continues(
    tmp_path: Path,
) -> None:
    """Explizit gesetzter --family-Pfad, der nicht existiert → Hinweis + exit 0
    (Run mit leerem Input-Ordner crasht nicht)."""
    empty_input = tmp_path / "leer"
    empty_input.mkdir()
    nonexistent_family = tmp_path / "nope.yaml"
    r = CliRunner().invoke(
        main,
        ["--input", str(empty_input), "--family", str(nonexistent_family)],
    )
    # Empty input → "Keine PDFs" + exit 0; family-Hinweis kommt VOR dem
    # PDF-Scan, daher beides im Output.
    assert r.exit_code == 0
    assert "nicht gefunden" in flat(r.output)
    assert "Person-Inferenz deaktiviert" in flat(r.output)


def test_family_default_path_silent_when_missing(tmp_path: Path) -> None:
    """Default ``family.yaml`` fehlt → silent (kein Hinweis), kein Crash."""
    empty_input = tmp_path / "leer"
    empty_input.mkdir()
    runner = CliRunner()
    # CliRunner setzt cwd auf isolated_filesystem; family.yaml existiert dort nicht.
    with runner.isolated_filesystem():
        r = runner.invoke(main, ["--input", str(empty_input)])
    assert r.exit_code == 0
    assert "Keine PDFs" in flat(r.output)
    assert "Person-Inferenz deaktiviert" not in flat(r.output)


def test_write_report_contains_belegtyp_aggregat(tmp_path: Path) -> None:
    """write_report rendert die Belegtyp-Aggregat-Tabelle pro Typ."""
    res_lohn = ProcessResult(
        bucket="extracted",
        pdf_path=Path("a.pdf"),
        belegtyp="lohnausweis",
        person="mann",
    )
    res_bank = ProcessResult(
        bucket="extracted",
        pdf_path=Path("b.pdf"),
        belegtyp="bank_zinsausweis",
        person="frau",
    )
    res_kk = ProcessResult(
        bucket="unverified",
        pdf_path=Path("c.pdf"),
        belegtyp="kk_praemienbescheinigung",
        person=None,
    )
    write_report(
        tmp_path,
        {
            "extracted": [res_lohn, res_bank],
            "unverified": [res_kk],
            "unprocessed": [],
        },
        model_info={"model": "qwen2.5:7b-instruct-q4_K_M", "digest": "deadbeef1234"},
    )
    report = (tmp_path / "report.md").read_text()
    assert "## Belegtyp-Aggregat" in report
    # Kopfzeile der Markdown-Tabelle.
    assert "| typ |" in report
    assert "ø-konfidenz" in report
    # Zeilen pro Belegtyp.
    assert "| lohnausweis |" in report
    assert "| bank_zinsausweis |" in report
    assert "| kk_praemienbescheinigung |" in report


def test_write_report_privacy_warnings_section(tmp_path: Path) -> None:
    """Bei nicht-leerem family_warnings: Sektion 'Privacy-Warnungen' erscheint."""
    res = ProcessResult(
        bucket="extracted",
        pdf_path=Path("a.pdf"),
        belegtyp="lohnausweis",
        person="mann",
    )
    warnings_strs = [
        "AHV-Feld bei mann gesetzt — Privacy-Warnung: AHV ist PII und sollte "
        "nur gespeichert werden, wenn unbedingt nötig.",
    ]
    write_report(
        tmp_path,
        {"extracted": [res], "unverified": [], "unprocessed": []},
        model_info={"model": "x", "digest": "y"},
        family_warnings=warnings_strs,
    )
    report = (tmp_path / "report.md").read_text()
    assert "## Privacy-Warnungen" in report
    assert "AHV-Feld bei mann gesetzt" in report
    # T-02-16: Die Warnung enthält keinen AHV-Klartext (756.xxx) — die
    # Family.warnings-Strings sind PII-frei formuliert.
    assert "756." not in report


def test_run_jsonl_no_ahv_clear_text(tmp_path: Path) -> None:
    """Sanity-Test: Family.warnings-Strings enthalten keinen AHV-Klartext.

    Damit ist garantiert, dass das Loggen von ``family_warning``-Events in
    run.jsonl keinen AHV-Wert leakt (T-02-16, PRV-04).
    """
    from extractors.family import Family, FamilyMember

    family = Family(
        members=[
            FamilyMember(
                role="mann",
                first_name="Hans",
                last_name="Muster",
                ahv="756.1234.5678.97",
            )
        ]
    )
    assert family.warnings, "Family sollte AHV-Warnung erzeugen"
    for w in family.warnings:
        assert "756." not in w, f"Warnung enthält AHV-Klartext: {w!r}"

    # Simuliere die Schreib-Logik in main(): jeden Warning als JSONL-Event.
    log_file = tmp_path / "run.jsonl"
    for w in family.warnings:
        log_file.open("a").write(
            json.dumps({"event": "family_warning", "warning": w}) + "\n"
        )
    content = log_file.read_text()
    assert "756." not in content
    assert "family_warning" in content
