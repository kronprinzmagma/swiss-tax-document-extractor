"""Die Browser-Erweiterung: Aufbau, Privacy und die eine kritische Stelle.

Der Rest der Erweiterung ist Darstellung — diese drei Dinge sind es nicht:

1. **Sie darf nichts senden.** Kein `fetch`, kein `XMLHttpRequest`, keine
   Berechtigung ausser `storage`. Die Werte sind echte Steuerbeträge.
2. **Der Wert muss ankommen.** Ein blosses ``el.value = …`` trägt ihn sichtbar
   ein, aber eine Oberfläche, die auf Ereignisse horcht, kennt ihn nicht —
   beim Speichern wäre er weg. Deshalb nativer Setter plus ``input`` und
   ``change``.
3. **Summen, die das Formular selbst rechnet, dürfen nicht angeboten werden.**
   Wer sie zusätzlich einträgt, verdoppelt sie.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

WURZEL = Path(__file__).resolve().parent.parent / "extension"
PANEL = WURZEL / "panel.js"
NODE = shutil.which("node")


def test_manifest_fordert_nur_speicher():
    m = json.loads((WURZEL / "manifest.json").read_text(encoding="utf-8"))
    assert m["manifest_version"] == 3
    assert m["permissions"] == ["storage"], (
        "mehr als der lokale Speicher waere nicht zu rechtfertigen")
    assert "host_permissions" not in m
    for muster in m["content_scripts"][0]["matches"]:
        assert "zh.ch" in muster or "zhprivatetax.ch" in muster, muster


def test_keine_netzwerkaufrufe():
    quelle = PANEL.read_text(encoding="utf-8")
    for verboten in ("fetch(", "XMLHttpRequest", "navigator.sendBeacon",
                     "new WebSocket", "import("):
        assert verboten not in quelle, (
            f"{verboten} in der Erweiterung — sie darf nichts senden")


def test_der_wert_wird_mit_ereignissen_gesetzt():
    """Die Stelle, an der naive Erweiterungen scheitern."""
    quelle = PANEL.read_text(encoding="utf-8")
    assert "getOwnPropertyDescriptor" in quelle, (
        "ohne nativen Setter merkt eine Angular-/React-Oberflaeche nichts")
    for ereignis in ("'input'", "'change'"):
        assert f"new Event({ereignis}" in quelle, ereignis


def test_gerechnete_summen_bekommen_keinen_knopf():
    quelle = PANEL.read_text(encoding="utf-8")
    # Der Knopf haengt an der Bedingung `w.gerechnet ? '' : …`.
    assert "w.gerechnet ? '' :" in quelle
    assert "s.gerechnet ? '' :" in quelle


@pytest.mark.skipif(NODE is None, reason="node nicht vorhanden")
def test_das_skript_laesst_sich_laden():
    fertig = subprocess.run([NODE, "--check", str(PANEL)],
                            capture_output=True, text=True, timeout=30)
    assert fertig.returncode == 0, fertig.stderr[-1500:]


def test_der_export_kennzeichnet_gerechnete_ziffern():
    """Ohne dieses Kennzeichen wuerde die Leiste zum Verdoppeln einladen."""
    from extractors.zielwerte import wird_gerechnet

    for ziffer in ("4", "12", "30.1", "34"):
        assert wird_gerechnet(ziffer), ziffer
    for ziffer in ("1.1", "15", "16.6", "22.1", "14"):
        assert not wird_gerechnet(ziffer), ziffer


def test_ein_konto_ist_eine_zeile_mit_spalten():
    """Ein Kontoeintrag im Verzeichnis ist mehrere Felder, nicht eines.

    Bezeichnung, Steuerwert, Bruttoertrag A, Bruttoertrag B — in dieser
    Reihenfolge fragt das Formular 340 sie ab. Wer nur den Saldo einsetzt, hat
    die Zeile nicht ausgefüllt (260923-dua, vom Nutzer gemeldet).
    """
    from extractors.zielwerte import SPALTEN_340, spalte_340

    assert [s for s, _ in SPALTEN_340] == [
        "bezeichnung", "steuerwert", "ertrag_a", "ertrag_b"]
    assert spalte_340("Saldo 31.12.") == "steuerwert"
    assert spalte_340("Steuerwert 31.12.") == "steuerwert"
    assert spalte_340("Bruttoertrag mit Verrechnungssteuer") == "ertrag_a"
    assert spalte_340("Bruttoertrag ohne Verrechnungssteuer") == "ertrag_b"
    assert spalte_340("Nettolohn") == ""


def test_die_leiste_zeigt_die_zeile_in_formularreihenfolge():
    quelle = PANEL.read_text(encoding="utf-8")
    assert "b.zeile" in quelle, "die Feldfolge je Konto fehlt"
    assert "zeile340" in quelle


def test_einzelpositionen_sind_keine_gerechneten_summen():
    """Der Unterschied, der beim ersten Wurf falsch war.

    `wird_gerechnet` gilt der Summe auf dem Hauptformular (400, 150), nicht
    der Zeile im Verzeichnis. Waeren Einzelpositionen als gerechnet markiert,
    verweigerte die Leiste fuer jedes Konto den Knopf.
    """
    quelle = (WURZEL.parent / "scripts" / "export_extension.py").read_text(
        encoding="utf-8")
    assert '"gerechnet": False,' in quelle
    assert '"gerechnet": wird_gerechnet(ziffer),' not in quelle
