#!/usr/bin/env python3
"""Das Unersetzliche zur Seite legen — vor jedem Lauf, der aufräumt.

Zwei Dateien tragen menschliche Arbeit: die Ablage mit den geprüften
Positionen und die Durchsicht. Diese beiden bekommen vor einem
Extraktionslauf eine datierte Kopie, weil ein abgebrochener Lauf schon
einmal Artefakte weggeräumt hat (260923-dua).

Bestandsschutz 2025 (261025-kme, B4h): Der Anwender hat die Steuererklärung 2025
fertiggestellt — dazu gehören auch die Entscheidungen samt ihrem Verlauf,
defekt beiseitegelegte Entscheidungen, das Extraktionsergebnis, die
Übertragungstabelle, der Nachweis, die Werte der Erweiterung, die Eingabe
und die PDFs des eSteuerauszugs. Alles das kommt jetzt mit. Vor dem nächsten
``make productive`` erst ``make sicherung`` — ``run_productive_local`` ruft
``sichere`` ab jetzt auch selbst auf.

Kopien werden nie überschrieben und nie aufgeräumt. Jede Sicherung bekommt
ihren eigenen Ordner, auch zwei in derselben Sekunde.

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
# Nur, wenn vorhanden. Ordner werden ganz kopiert, Muster mit * als Glob.
WENN_DA = (
    "json/entscheide.json",
    "json/entscheide-verlauf",
    "json/entscheide.defekt-*.json",
    "json/_results_full.json",
    "steuer_uebertragung.md",
    "nachweis_anhang.md",
    "steuerwerte.json",
    "etax_eingabe.json",
    "steueraufstellung.xlsx",
    "steuer_uebertragung.csv",
    "esteuerauszug",
)


def _neuer_ordner(basis: Path) -> Path:
    marke = datetime.now().strftime("%Y%m%d-%H%M%S")
    ziel, n = basis / marke, 2
    while ziel.exists():
        ziel = basis / f"{marke}-{n}"
        n += 1
    ziel.mkdir(parents=True)
    return ziel


def _quellen(wurzel: Path, name: str) -> list[Path]:
    if "*" in name:
        return sorted(wurzel.glob(name))
    q = wurzel / name
    return [q] if q.exists() else []


def sichere(wurzel: Path, ausgabe: bool = True) -> tuple[int, Path]:
    """Kopiert, was es von WICHTIG und WENN_DA gibt; (Anzahl Dateien, Ziel).

    Gezählt werden Dateien — ein Ordner zählt mit seinen Dateien. Gibt es
    nichts zu sichern, bleibt kein leerer Ordner zurück.
    """
    wurzel = Path(wurzel)
    ziel = _neuer_ordner(wurzel / "sicherungen")
    kopiert = 0
    for name in WICHTIG + WENN_DA:
        quellen = _quellen(wurzel, name)
        if not quellen:
            if name in WICHTIG and ausgabe:
                print(f"   fehlt:   {name}")
            continue
        for quelle in quellen:
            zielname = str(quelle.relative_to(wurzel)).replace("/", "_")
            if quelle.is_dir():
                shutil.copytree(quelle, ziel / zielname)
                n = sum(1 for p in (ziel / zielname).rglob("*") if p.is_file())
                kopiert += n
                if ausgabe:
                    print(f"   kopiert: {zielname:<28} {n:>8} Dateien")
            else:
                shutil.copy2(quelle, ziel / zielname)
                kopiert += 1
                if ausgabe:
                    print(f"   kopiert: {zielname:<28} {quelle.stat().st_size:>8} Bytes")
    if not kopiert:
        ziel.rmdir()
    return kopiert, ziel


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--samples", type=Path, required=True)
    args = ap.parse_args()

    print("Sicherung — nur Zahlen, dieser Teil ist teilbar")
    print(TRENNER)
    kopiert, ziel = sichere(args.samples.parent)
    print(f"\n   {kopiert} Datei(en) → {ziel}")
    return 0 if kopiert else 1


if __name__ == "__main__":
    raise SystemExit(main())
