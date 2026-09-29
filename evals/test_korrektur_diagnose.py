"""Die Diagnose muss sehen, was tatsaechlich drinsteht (260904-rmx).

Erste Fassung prueft nur die Spalte ``soll`` und brach mit „Eintraege ohne
Sollwert aendern nichts an der Tabelle" ab. Das ist falsch: Person,
Aussteller, Zielwert und Ziffer werden unabhaengig vom Betrag uebernommen.
Wer eine fehlende Personenzuordnung sucht, bekam also die Auskunft, es gebe
nichts zu holen — waehrend der Eintrag danebenlag.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def lauf(samples: Path) -> str:
    r = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "korrekturen_diagnose.py"),
         "--samples", str(samples)],
        capture_output=True, text=True, env={"PYTHONPATH": str(ROOT),
                                             "PATH": "/usr/bin:/bin"})
    return r.stdout


def baue(tmp_path, korrekturen: dict, wo: str = "parent") -> Path:
    samples = tmp_path / "json"
    samples.mkdir()
    samples.joinpath("_results_full.json").write_text("[]")
    ziel = (tmp_path if wo == "parent" else samples) / "korrekturen.json"
    ziel.write_text(json.dumps(korrekturen, ensure_ascii=False))
    return samples


def test_personenzuordnung_wird_gezaehlt(tmp_path):
    s = baue(tmp_path, {"a.pdf|X": {"zielwert": "X", "person": "elternteil_2"}})
    aus = lauf(s)
    assert "Personenzuordnung" in aus
    assert "aendern nichts an der Tabelle" not in aus


def test_beide_ablagen_werden_gezeigt(tmp_path):
    s = baue(tmp_path, {"a.pdf|X": {"zielwert": "X", "soll": "1.00"}})
    (s / "korrekturen.json").write_text(json.dumps(
        {"b.pdf|Y": {"zielwert": "Y", "person": "kind_1"}}))
    aus = lauf(s)
    assert aus.count("korrekturen.json") >= 2
    assert "2 Eintraege zusammengefuehrt" in aus


def test_selbst_erfasste_gelten_nicht_als_fehltreffer(tmp_path):
    s = baue(tmp_path, {"a.pdf|Eigene": {"zielwert": "Eigene", "neu": True,
                                         "soll": "5.00"}})
    assert "selbst erfasst — das ist richtig so" in lauf(s)


def test_probelauf_zaehlt_die_wirkung(tmp_path):
    s = baue(tmp_path, {"a.pdf|X": {"zielwert": "X", "person": "kind_1"}})
    aus = lauf(s)
    assert "4. Probelauf:" in aus
    assert "Zeilen mit zugeordneter Person danach" in aus


def test_ausgabe_nennt_keine_dokumentnamen(tmp_path):
    """Sie soll teilbar sein — Zaehlungen und Zielwerte, sonst nichts."""
    s = baue(tmp_path, {"geheim-dokument.pdf|X": {"zielwert": "X",
                                                  "person": "elternteil_1",
                                                  "soll": "4711.00"}})
    aus = lauf(s)
    assert "geheim-dokument" not in aus
    assert "4711" not in aus
    assert "elternteil_1" not in aus


def test_ohne_datei_klarer_hinweis(tmp_path):
    samples = tmp_path / "json"
    samples.mkdir()
    samples.joinpath("_results_full.json").write_text("[]")
    assert "make korrekturen-neuste" in lauf(samples)


def test_belegart_zuweisungen_gelten_nicht_als_fehltreffer(tmp_path):
    """Ein Dokument, dem nur eine Belegart zugewiesen wurde, hat keine Zeile —
    das ist kein Fehler, sondern der Normalfall bei 'irrelevant'."""
    s = baue(tmp_path, {"x.pdf|irrelevant": {"zielwert": "irrelevant"}})
    aus = lauf(s)
    assert "Belegart-Zuweisung" in aus
    assert "Kein einziges Dokument wiedererkannt" not in aus
    assert "Alles erklaerbar" in aus


# --- Abdeckungsmatrix -------------------------------------------------------

def abdeckung(zeilen) -> str:
    import io
    from contextlib import redirect_stdout

    from scripts.korrekturen_diagnose import _abdeckung
    puffer = io.StringIO()
    with redirect_stdout(puffer):
        _abdeckung(zeilen)
    return puffer.getvalue()


def test_matrix_zeigt_zielwert_je_rolle():
    aus = abdeckung([{"beschreibung": "Prämie Grundversicherung KVG",
                      "person": "elternteil_1"}])
    assert "Prämie Grundversicherung KVG" in aus
    assert "elternteil_1" in aus


def test_matrix_verschweigt_selbst_vergebene_bezeichnungen():
    """Sie koennten einen Namen enthalten — die Ausgabe soll teilbar bleiben."""
    aus = abdeckung([{"beschreibung": "Rechnung von Frau Beispiel",
                      "person": "kind_1"}])
    assert "Beispiel" not in aus
    assert "(eigene Bezeichnung)" in aus


def test_matrix_sagt_wonach_die_pruefung_sucht():
    """Der eigentliche Zweck: 'Praemie vorhanden, KVG nicht' erklaert den
    Befund 'keine Grundversicherung' auf einen Blick."""
    aus = abdeckung([{"beschreibung": "Prämie Total (nicht aufgeteilt)",
                      "person": "elternteil_2"}])
    assert "NICHTS GEFUNDEN" in aus
    zeile_kvg = [z for z in aus.splitlines() if z.strip().startswith("kvg")][0]
    zeile_pr = [z for z in aus.splitlines() if z.strip().startswith("praemie")][0]
    assert "NICHTS GEFUNDEN" in zeile_kvg
    assert "vorhanden" in zeile_pr
