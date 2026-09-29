"""Regression-Tests für den Privacy-Gate (scripts/privacy_gate.py).

Hintergrund (Quick-Task 260611-kle): Ein Einzeltoken eines mehrteiligen
Vornamens (Doppel-Vorname) überlebte die Anonymisierung und wurde vom Gate
nicht gemeldet, weil generate_name_variants nur Varianten der Gesamtphrase
erzeugte. Diese Tests nageln fest:

1. Der Gate findet Einzeltokens mehrteiliger family.yaml-Namensfelder in
   Text-Artefakten (txt/json/md).
2. Der Gate scannt xlsx-Dateien textuell (openpyxl) und findet Namen darin.

Alle Namen hier sind synthetisch — keine echten Daten.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# scripts/ ist kein Package — privacy_gate per Pfad laden.
_spec = importlib.util.spec_from_file_location(
    "privacy_gate", ROOT / "scripts" / "privacy_gate.py"
)
privacy_gate = importlib.util.module_from_spec(_spec)
sys.modules["privacy_gate"] = privacy_gate
_spec.loader.exec_module(privacy_gate)

from extractors.family import Family, FamilyMember


@pytest.fixture()
def synth_family() -> Family:
    """Synthetische Familie mit Doppel-Vorname (Regression-Kern)."""
    return Family(members=[
        FamilyMember(role="mann", first_name="Bruno Kasimir", last_name="Testmann"),
        FamilyMember(role="frau", first_name="Lena", last_name="Testfrau Alt"),
    ])


def _scan_text(tmp_path: Path, family: Family, content: str, suffix: str = ".txt"):
    f = tmp_path / f"artefakt{suffix}"
    f.write_text(content, encoding="utf-8")
    patterns = privacy_gate.build_family_pattern_list(family)
    return privacy_gate.scan_file(f, patterns, [])


def test_gate_findet_einzeltoken_des_doppel_vornamens(tmp_path, synth_family):
    # Nur der Zweitname-Token taucht auf — genau der Fall, der bisher
    # unentdeckt blieb.
    hits = _scan_text(tmp_path, synth_family, "Versicherte Person: Kasimir\n")
    assert any(cat == "family_name_variant" for _, cat, _ in hits), \
        "Einzeltoken eines Doppel-Vornamens muss gemeldet werden"


def test_gate_findet_einzeltoken_des_mehrteiligen_nachnamens(tmp_path, synth_family):
    hits = _scan_text(tmp_path, synth_family, "Kontoinhaberin: L. Alt\n")
    assert any(cat == "family_name_variant" for _, cat, _ in hits)


def test_gate_findet_gesamtphrase_weiterhin(tmp_path, synth_family):
    hits = _scan_text(tmp_path, synth_family, "Bruno Kasimir Testmann\n")
    assert any(cat == "family_name_variant" for _, cat, _ in hits)


def test_gate_meldet_keine_treffer_in_sauberem_text(tmp_path, synth_family):
    hits = _scan_text(
        tmp_path, synth_family,
        "Zinsertrag 123.45 CHF per 31.12. — Inhaber: Hans Muster\n",
    )
    assert hits == []


def test_gate_report_enthaelt_nie_den_klartext(tmp_path, synth_family):
    hits = _scan_text(tmp_path, synth_family, "Kasimir\n")
    assert hits
    for _, cat, fp in hits:
        assert "Kasimir" not in cat and "Kasimir" not in fp


def test_gate_scannt_xlsx_zellen(tmp_path, synth_family):
    openpyxl = pytest.importorskip("openpyxl")
    f = tmp_path / "steueraufstellung.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws["A1"] = "Person"
    ws["A2"] = "Kasimir"  # Einzeltoken in einer Zelle
    wb.save(f)
    patterns = privacy_gate.build_family_pattern_list(synth_family)
    hits = privacy_gate.scan_file(f, patterns, [])
    assert any(cat == "family_name_variant" for _, cat, _ in hits), \
        "xlsx-Zellen müssen textuell gescannt werden"


def test_gate_xlsx_sauber_ohne_namen(tmp_path, synth_family):
    openpyxl = pytest.importorskip("openpyxl")
    f = tmp_path / "leer.xlsx"
    wb = openpyxl.Workbook()
    wb.active["A1"] = "Betrag CHF 100"
    wb.save(f)
    patterns = privacy_gate.build_family_pattern_list(synth_family)
    assert privacy_gate.scan_file(f, patterns, []) == []


def test_default_extensions_enthalten_xlsx():
    # Default-Extension-Liste des CLI muss xlsx abdecken (steueraufstellung.xlsx).
    import inspect
    src = inspect.getsource(privacy_gate.main)
    assert ".xlsx" in src
