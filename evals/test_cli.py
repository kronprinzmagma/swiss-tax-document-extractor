"""CLI-Smoke-Tests via :class:`click.testing.CliRunner`.

Deckt CLI-01..04 ab: --help, --engine cloud blockiert, --input fehlend,
--input leer. Keine Ollama-Calls — alle Cases hängen vor dem ersten LLM-Call.
"""
from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from steuer_extraktor import main


def test_cli_help() -> None:
    r = CliRunner().invoke(main, ["--help"])
    assert r.exit_code == 0, r.output
    for opt in ("--input", "--output", "--engine", "--year", "--aussteller-history"):
        assert opt in r.output, f"Option {opt} fehlt in --help: {r.output}"


def test_cli_cloud_engine_blocked() -> None:
    r = CliRunner().invoke(main, ["--engine", "cloud"])
    assert r.exit_code != 0
    assert "Cloud-Engine" in r.output or "cloud" in r.output.lower()


def test_cli_missing_input(tmp_path: Path) -> None:
    r = CliRunner().invoke(main, ["--input", str(tmp_path / "nope")])
    assert r.exit_code == 1
    assert "existiert nicht" in r.output


def test_cli_empty_input(tmp_path: Path) -> None:
    empty = tmp_path / "leer"
    empty.mkdir()
    r = CliRunner().invoke(main, ["--input", str(empty)])
    assert r.exit_code == 0
    assert "Keine PDFs" in r.output
