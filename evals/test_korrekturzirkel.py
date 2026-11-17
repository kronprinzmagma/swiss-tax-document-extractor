"""Tests fuer den Rueckweg des Optimierungszirkels (260904-rmx)."""
from __future__ import annotations

import json

from scripts.apply_korrekturen import lies_korrekturen

KOPF = ("nr,beleg,ziffer,zielwert,person,aussteller,betrag,konfidenz,"
        "herkunft,korrektur,notiz\n")


def schreibe(tmp_path, *zeilen):
    p = tmp_path / "korrektur.csv"
    p.write_text(KOPF + "".join(z + "\n" for z in zeilen), encoding="utf-8")
    return p


def test_leere_zeilen_werden_uebersprungen(tmp_path):
    p = schreibe(tmp_path, "1,a.pdf,15,Prämie KVG,e1,K,100.00,sicher,regel,,")
    assert lies_korrekturen(p) == []


def test_zeile_mit_korrektur_wird_gelesen(tmp_path):
    p = schreibe(tmp_path, "1,a.pdf,22.1,Selbstkosten,e1,K,412.60,unsicher,regel,712.60,Franchise")
    zeilen = lies_korrekturen(p)
    assert len(zeilen) == 1
    assert zeilen[0]["korrektur"] == "712.60"
    assert zeilen[0]["notiz"] == "Franchise"


def test_nur_notiz_ohne_wert_zaehlt_auch(tmp_path):
    """Eine Beschriftung allein genuegt, um eine Regel abzuleiten."""
    p = schreibe(tmp_path, "1,a.pdf,15,Prämie,e1,K,100.00,wahrscheinlich,modell,,Ihr Anteil")
    assert len(lies_korrekturen(p)) == 1


def test_ingest_schreibt_sollwerte(tmp_path):
    import subprocess, sys, pathlib
    root = pathlib.Path(__file__).resolve().parent.parent
    p = schreibe(tmp_path, "1,a.pdf,22.1,Selbstkosten,e1,K,412.60,unsicher,regel,712.60,Franchise")
    ziel = tmp_path / "korrekturen.json"
    r = subprocess.run(
        [sys.executable, str(root / "scripts" / "apply_korrekturen.py"),
         "--file", str(p), "--out", str(ziel)],
        capture_output=True, text=True, env={"PYTHONPATH": str(root), "PATH": "/usr/bin:/bin"},
    )
    assert r.returncode == 0, r.stderr
    daten = json.loads(ziel.read_text())
    eintrag = daten["a.pdf|Selbstkosten"]
    assert eintrag["soll"] == "712.60"
    assert eintrag["label"] == "Franchise"
    # Privacy: die Konsolenausgabe darf keinen Betrag enthalten.
    assert "712.60" not in r.stdout
    assert "412.60" not in r.stdout
