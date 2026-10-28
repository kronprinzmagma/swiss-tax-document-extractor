"""Zurueckrollen: Versionen nuetzen nur, wenn man zurueck kann (260904-rmx).

Nach dem Datenverlust gibt es Sicherungen vor jedem Schreiben. Ohne einen Weg
zurueck waeren sie ein Archiv, das niemand oeffnen kann.

Die zwei Eigenschaften, die das Zurueckrollen sicher machen, sind hier
festgenagelt: der jetzige Stand wird zuerst gesichert (auch das Verwerfen ist
umkehrbar), und beide Ablagen bekommen denselben Stand, damit sie sich nicht
gegenseitig verdecken.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def baue(tmp_path, jetzt: dict, staende: dict[str, dict]):
    samples = tmp_path / "json"
    samples.mkdir()
    verlauf = samples / "korrekturen-verlauf"
    verlauf.mkdir()
    (samples / "korrekturen.json").write_text(json.dumps(jetzt, ensure_ascii=False))
    (tmp_path / "korrekturen.json").write_text(json.dumps(jetzt, ensure_ascii=False))
    for marke, daten in staende.items():
        (verlauf / f"korrekturen-{marke}.json").write_text(
            json.dumps(daten, ensure_ascii=False))
    return samples


def lauf(samples: Path, *extra) -> str:
    r = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "zurueckrollen.py"),
         "--samples", str(samples), *extra],
        capture_output=True, text=True,
        env={"PYTHONPATH": str(ROOT), "PATH": "/usr/bin:/bin"})
    return r.stdout + r.stderr


ALT = {"a.pdf|X": {"zielwert": "X", "soll": "100.00"}}
NEU = {"a.pdf|X": {"zielwert": "X", "soll": "250.00"},
       "b.pdf|Y": {"zielwert": "Y", "person": "kind_1"}}


def test_liste_zeigt_die_staende(tmp_path):
    s = baue(tmp_path, NEU, {"20260910-101010": ALT})
    aus = lauf(s)
    assert "20260910-101010" in aus
    assert "1 nur jetzt" in aus or "nur jetzt" in aus


def test_zurueckrollen_stellt_den_stand_her(tmp_path):
    s = baue(tmp_path, NEU, {"20260910-101010": ALT})
    lauf(s, "--auf", "20260910-101010")
    assert json.loads((s / "korrekturen.json").read_text()) == ALT


def test_beide_ablagen_bekommen_denselben_stand(tmp_path):
    """Sonst verdeckt die eine die andere — genau der Fehler von vorher."""
    s = baue(tmp_path, NEU, {"20260910-101010": ALT})
    lauf(s, "--auf", "20260910-101010")
    a = json.loads((s / "korrekturen.json").read_text())
    b = json.loads((s.parent / "korrekturen.json").read_text())
    assert a == b == ALT


def test_der_verworfene_stand_landet_im_verlauf(tmp_path):
    """Zurueckrollen ist selbst eine Aenderung und muss umkehrbar sein."""
    s = baue(tmp_path, NEU, {"20260910-101010": ALT})
    lauf(s, "--auf", "20260910-101010")
    gesichert = [json.loads(p.read_text())
                 for p in (s / "korrekturen-verlauf").glob("korrekturen-*.json")]
    assert NEU in gesichert, "der verworfene Stand fehlt im Verlauf"


def test_unbekannte_zeitmarke_aendert_nichts(tmp_path):
    s = baue(tmp_path, NEU, {"20260910-101010": ALT})
    aus = lauf(s, "--auf", "19990101-000000")
    assert "Kein Stand" in aus
    assert json.loads((s / "korrekturen.json").read_text()) == NEU


def test_leerer_stand_wird_nicht_zurueckgerollt(tmp_path):
    """Eine leere Sicherung ist kein Ziel — sonst holt man sich den Verlust
    zurueck, den man gerade repariert hat."""
    s = baue(tmp_path, NEU, {"20260910-101010": {}})
    aus = lauf(s, "--auf", "20260910-101010")
    assert "leer" in aus
    assert json.loads((s / "korrekturen.json").read_text()) == NEU


# --- Sperre waehrend der Review (260923-dua) --------------------------------

def test_sperre_haengt_am_korpus_nicht_am_port(tmp_path):
    """Ein Server, der einen ganz anderen Ordner bedient, darf das
    Zurueckrollen nicht aufhalten — und ein Test schon gar nicht davon
    abhaengen, was gerade auf Port 8765 laeuft."""
    from scripts.review_server import (
        review_laeuft, sperre_loesen, sperre_setzen,
    )

    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    assert review_laeuft(a) is False

    sperre_setzen(a)
    assert review_laeuft(a) is True
    assert review_laeuft(b) is False, "die Sperre gilt nur fuer ihren Korpus"

    sperre_loesen(a)
    assert review_laeuft(a) is False


def test_verwaiste_sperre_blockiert_nicht_fuer_immer(tmp_path):
    """Ein abgestuerzter Server darf die Wiederherstellung nicht aussperren."""
    import json as _json

    from scripts.review_server import SPERRDATEI, review_laeuft

    samples = tmp_path / "json"
    samples.mkdir()
    # Eine PID, die es sicher nicht gibt.
    (samples / SPERRDATEI).write_text(_json.dumps({"pid": 2 ** 31 - 1}))
    assert review_laeuft(samples) is False
    assert not (samples / SPERRDATEI).exists(), "die Leiche wird aufgeraeumt"


def test_kaputte_sperrdatei_blockiert_nicht(tmp_path):
    from scripts.review_server import SPERRDATEI, review_laeuft

    samples = tmp_path / "json"
    samples.mkdir()
    (samples / SPERRDATEI).write_text("kein json")
    assert review_laeuft(samples) is False


def test_sperrdatei_ist_kein_dokument():
    """Sie liegt im Sample-Ordner und darf weder als Beleg gezaehlt noch vom
    naechsten Lauf weggeraeumt werden."""
    from scripts.review_server import SPERRDATEI
    from scripts.run_productive_local import GESCHUETZT
    assert SPERRDATEI in GESCHUETZT
