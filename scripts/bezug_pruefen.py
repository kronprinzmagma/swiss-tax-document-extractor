#!/usr/bin/env python3
"""Wie viele gespeicherte Korrekturen finden noch ihre Zeile?

Jede Umbenennung einer Zielwert-Bezeichnung kann gespeicherte Arbeit
unauffindbar machen: Korrekturen hängen am Schlüssel ``Dokument|Bezeichnung``.
Genau das ist passiert, und es sah aus wie Datenverlust.

Dieses Skript misst den Bezug — vor und nach einer Änderung auszuführen. Die
Zahl darf nie sinken.

Ausgabe: nur Zählungen, teilbar.

Aufruf::

    PYTHONPATH=. .venv/bin/python scripts/bezug_pruefen.py \\
        --samples output/latest/json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def messe(samples: Path) -> dict:
    from extractors.korrekturen import finde, lade_alle
    from scripts.build_tax_output import build_rows

    korr = lade_alle(samples)
    ergebnisse = samples / "_results_full.json"
    daten = json.loads(ergebnisse.read_text()) if ergebnisse.exists() else []

    zeilen = []
    for e in daten:
        if e.get("status") == "ok":
            zeilen.extend(build_rows(e))

    # Ein Eintrag hat Bezug, wenn eine Zeile ihn findet — ueber die aktuelle
    # Bezeichnung oder ueber eine fruehere (Alias).
    bezug = 0
    for r in zeilen:
        if finde(korr, r.get("pdf_name", ""), r.get("beschreibung", "")):
            bezug += 1

    # Und umgekehrt: welche Eintraege findet keine Zeile? Das sind die
    # verwaisten — selbst erfasste Positionen ausgenommen, die haben
    # absichtlich keine.
    erreicht = set()
    for r in zeilen:
        for k, e in korr.items():
            if e is finde(korr, r.get("pdf_name", ""), r.get("beschreibung", "")):
                erreicht.add(k)
    verwaist = [k for k, e in korr.items()
                if k not in erreicht and isinstance(e, dict)
                and not e.get("neu")]

    return {"eintraege": len(korr), "zeilen": len(zeilen),
            "zeilen_mit_eintrag": bezug, "verwaist": len(verwaist)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--samples", type=Path, required=True)
    args = ap.parse_args()
    m = messe(args.samples)
    print("Bezug zwischen Korrekturen und Zeilen — nur Zahlen, teilbar")
    print("─" * 62)
    print(f"   {m['eintraege']:4}  gespeicherte Eintraege")
    print(f"   {m['zeilen']:4}  Zeilen im Lauf")
    print(f"   {m['zeilen_mit_eintrag']:4}  Zeilen, die ihren Eintrag finden")
    print(f"   {m['verwaist']:4}  Eintraege ohne Zeile (verwaist)")
    print("\n   Diese Zahlen vor und nach jeder Umbenennung vergleichen.")
    print("   'Zeilen, die ihren Eintrag finden' darf nie sinken.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
