#!/usr/bin/env python3
"""Auf einen früheren Stand der Durchsicht zurückgehen.

Versionen allein nützen nichts, wenn man nicht zurück kann. Vor jedem
Schreiben landet eine Kopie von ``korrekturen.json`` in
``korrekturen-verlauf/``; dieses Skript zeigt sie und stellt eine davon
wieder her.

Zwei Eigenschaften, die es sicher machen:

* **Der aktuelle Stand wird zuerst gesichert.** Zurückrollen ist selbst eine
  Änderung und darf nichts vernichten — auch nicht das, was man gerade
  verwirft.
* **Es wird nie gelöscht, nur überschrieben**, und beide Ablagen
  (``output/latest`` und ``output/latest/json``) bekommen denselben Stand,
  damit sie sich nicht gegenseitig verdecken.

Ausgabe: Zeitpunkt, Anzahl Einträge und was sich zum aktuellen Stand
unterscheidet — nur Zahlen und Zielwert-Bezeichnungen, keine Beträge.

Aufruf::

    PYTHONPATH=. .venv/bin/python scripts/zurueckrollen.py --samples output/latest/json
    PYTHONPATH=. .venv/bin/python scripts/zurueckrollen.py --samples … --auf 20260910-112758
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TRENNER = "─" * 76

INHALT = ("soll", "person", "aussteller", "zielwert_neu", "ziffer_neu",
          "bestaetigt_betrag", "bestaetigt_person", "wert_betrag",
          "wert_person", "neu", "entfernt", "label", "kontext")


def _voll(daten: dict) -> int:
    return sum(1 for e in daten.values()
               if isinstance(e, dict) and any(e.get(f) for f in INHALT))


def _staende(samples: Path) -> list[Path]:
    """Alle Sicherungen, älteste zuerst — aus beiden Ablagen."""
    aus: list[Path] = []
    for ordner in (samples.parent, samples):
        aus += sorted((ordner / "korrekturen-verlauf").glob("korrekturen-*.json"))
    return sorted(aus, key=lambda p: p.name)


def _marke(p: Path) -> str:
    return p.stem.replace("korrekturen-", "")


def _unterschied(alt: dict, neu: dict) -> tuple[list[str], list[str], list[str]]:
    """(nur im alten, nur im neuen, in beiden aber anders) — als Zielwerte."""
    def bez(schluessel: str, daten: dict) -> str:
        e = daten.get(schluessel) or {}
        return (e.get("zielwert") if isinstance(e, dict) else None) \
            or schluessel.partition("|")[2] or "(ohne Zielwert)"

    nur_alt = [bez(k, alt) for k in alt if k not in neu]
    nur_neu = [bez(k, neu) for k in neu if k not in alt]
    anders = [bez(k, neu) for k in alt if k in neu and alt[k] != neu[k]]
    return nur_alt, nur_neu, anders


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--samples", type=Path, required=True)
    ap.add_argument("--auf", default=None,
                    help="Zeitmarke des Standes (z.B. 20260910-112758)")
    args = ap.parse_args()

    from extractors.korrekturen import lade
    from scripts.apply_korrekturen import _sichere

    aktuell_pfad = args.samples / "korrekturen.json"
    aktuell = lade(aktuell_pfad) or lade(args.samples.parent / "korrekturen.json")
    staende = _staende(args.samples)

    if not args.auf:
        print("Frühere Stände der Durchsicht")
        print(TRENNER)
        print(f"   {'Zeitpunkt':<20} {'Einträge':>9} {'mit Inhalt':>11}   "
              f"Unterschied zum jetzigen Stand")
        if not staende:
            print("   keine Sicherungen vorhanden")
        for p in staende:
            d = lade(p)
            nur_alt, nur_neu, anders = _unterschied(d, aktuell)
            teile = []
            if nur_alt:
                teile.append(f"{len(nur_alt)} nur dort")
            if nur_neu:
                teile.append(f"{len(nur_neu)} nur jetzt")
            if anders:
                teile.append(f"{len(anders)} anders")
            print(f"   {_marke(p):<20} {len(d):>9} {_voll(d):>11}   "
                  f"{', '.join(teile) or 'identisch'}")
        print(f"\n   jetzt {' ':<14} {len(aktuell):>9} {_voll(aktuell):>11}")
        print("\nEinen Stand zurückholen:")
        print("   make zurueckrollen AUF=<Zeitpunkt>")
        print("\nDer jetzige Stand wird dabei zuerst gesichert — nichts geht "
              "verloren,\nauch nicht das, was man gerade verwirft.")
        return 0

    treffer = [p for p in staende if _marke(p) == args.auf]
    if not treffer:
        print(f"Kein Stand mit der Zeitmarke {args.auf!r}.", file=sys.stderr)
        print("Verfügbare:", ", ".join(_marke(p) for p in staende) or "keine",
              file=sys.stderr)
        return 1

    quelle = treffer[-1]
    daten = lade(quelle)
    if not daten:
        print(f"{quelle} ist leer — nicht zurückgerollt.", file=sys.stderr)
        return 1

    nur_alt, nur_neu, anders = _unterschied(daten, aktuell)
    print(f"Zurück auf {args.auf}: {len(daten)} Einträge, {_voll(daten)} mit Inhalt.")
    if nur_neu:
        print(f"   {len(nur_neu)} Eintrag/Einträge des jetzigen Standes fallen weg:")
        for z in sorted(set(nur_neu))[:15]:
            print(f"      · {z}")
    if anders:
        print(f"   {len(anders)} Eintrag/Einträge unterscheiden sich.")

    text = json.dumps(daten, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    for ziel in (args.samples.parent / "korrekturen.json", aktuell_pfad):
        _sichere(ziel)          # der jetzige Stand kommt zuerst in den Verlauf
        ziel.write_text(text)
        print(f"   geschrieben: {ziel}")
    print("\nDer vorherige Stand liegt jetzt selbst im Verlauf — dieser "
          "Schritt ist umkehrbar.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
