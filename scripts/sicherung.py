#!/usr/bin/env python3
"""Das Unersetzliche zur Seite legen — vor jedem Lauf, der aufräumt.

Zwei Dateien tragen menschliche Arbeit: die Ablage mit den geprüften
Positionen und die Durchsicht. Alles andere entsteht neu. Diese beiden bekommen
vor einem Extraktionslauf eine datierte Kopie, weil ein abgebrochener Lauf
schon einmal Artefakte weggeräumt hat (260923-dua).

Kopien werden nie überschrieben und nie aufgeräumt. Es sind Kilobytes.

Aufruf::

    make sicherung
"""
from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TRENNER = "─" * 70

# Was kopiert wird, relativ zum Ordner über den Word-JSONs.
WICHTIG = ("positionen.db", "korrekturen.json", "json/korrekturen.json")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--samples", type=Path, required=True)
    args = ap.parse_args()

    wurzel = args.samples.parent
    marke = datetime.now().strftime("%Y%m%d-%H%M%S")
    ziel = wurzel / "sicherungen" / marke
    ziel.mkdir(parents=True, exist_ok=True)

    print("Sicherung — nur Zahlen, dieser Teil ist teilbar")
    print(TRENNER)
    kopiert = 0
    for name in WICHTIG:
        quelle = wurzel / name
        if not quelle.exists():
            print(f"   fehlt:   {name}")
            continue
        zielname = name.replace("/", "_")
        shutil.copy2(quelle, ziel / zielname)
        print(f"   kopiert: {zielname:<28} {quelle.stat().st_size:>8} Bytes")
        kopiert += 1
    print(f"\n   {kopiert} Datei(en) → {ziel}")
    return 0 if kopiert else 1


if __name__ == "__main__":
    raise SystemExit(main())
