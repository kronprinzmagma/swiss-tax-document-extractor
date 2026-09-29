#!/usr/bin/env python3
"""Zeigt, mit welchen Beschriftungen eine Krankenkasse die selbst getragenen
Kosten ausweist — ohne einen einzigen Betrag preiszugeben.

Hintergrund: die Komponenten der selbst getragenen Krankheits- und Unfallkosten
(Franchise, Selbstbehalt, Spitalbeitrag, nicht versicherte Leistungen) heissen
je nach Kasse anders. Erkennt das Muster eine Variante nicht, faellt die Summe
zu klein aus. Dieses Skript sagt, welche Varianten in den eigenen Dokumenten
vorkommen, damit die Muster ergaenzt werden koennen.

Privacy: **jede Ziffer wird durch # ersetzt.** Ausgegeben werden nur
Beschriftungstexte, Feldnamen und Trefferzahlen — niemals ein Betrag, ein Name
oder ein Datum. Die Ausgabe ist damit gefahrlos teilbar.

Aufruf::

    PYTHONPATH=. .venv/bin/python scripts/kk_labels.py --samples output/latest/json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from extractors.regex_extract import KK_KOMPONENTEN

# Woerter, in deren Naehe die Selbstkosten stehen.
_NAEHE = re.compile(
    r"franchise|selbstbehalt|spital|kostenbeteiligung|anteil|nicht.{0,3}versichert|"
    r"nicht.{0,3}pflichtig|selbst.{0,3}getragen|bezahlte\s+kosten|"
    r"nicht\s+getragen|behandlungskosten",
    re.IGNORECASE,
)

# Beschriftung + Betrag: das Label interessiert, der Betrag nicht.
_LABEL_BETRAG = re.compile(
    r"([A-Za-zÄÖÜäöüß][A-Za-zÄÖÜäöüß.\- ]{3,44}?)\s+"
    r"(?:Total\s+|CHF\s+)?\d[\d'’ ]*(?:\.\d{2})?"
)


def maskiere(s: str) -> str:
    """Ersetzt jede Ziffer durch # — Betraege, Daten und Nummern verschwinden."""
    return re.sub(r"\d", "#", s)


def analysiere(text: str) -> tuple[list[str], list[str]]:
    """Returns (erkannte Komponenten, unerkannte Label-Kandidaten)."""
    erkannt = [name for name, muster in KK_KOMPONENTEN.items() if muster.search(text)]

    kandidaten: list[str] = []
    gesehen: set[str] = set()
    for m in _LABEL_BETRAG.finditer(text):
        label = " ".join(m.group(1).split())
        if not _NAEHE.search(label):
            continue
        if any(muster.search(m.group(0)) for muster in KK_KOMPONENTEN.values()):
            continue  # bereits abgedeckt
        key = label.lower()
        if key in gesehen:
            continue
        gesehen.add(key)
        kandidaten.append(maskiere(label))
    return erkannt, kandidaten


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--samples", type=Path, required=True,
                    help="Verzeichnis mit den Word-JSONs eines Laufs")
    args = ap.parse_args()

    dateien = [p for p in sorted(args.samples.glob("*.json"))
               if not p.name.startswith("_") and "mapping" not in p.name]
    if not dateien:
        print(f"Keine JSONs in {args.samples}", file=sys.stderr)
        return 1

    print("Krankenkassen-Beschriftungen — nur Labels, alle Ziffern maskiert\n")
    n = 0
    for i, p in enumerate(dateien, start=1):
        try:
            doc = json.loads(p.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if doc.get("belegtyp") not in ("kk_praemienbescheinigung", "krankheitskosten"):
            continue
        text = " ".join(w["text"] for pg in doc.get("pages", [])
                        for w in pg.get("words", []))
        erkannt, kandidaten = analysiere(text)
        n += 1
        # Dokumente werden durchnummeriert — der Dateiname bleibt aussen vor.
        print(f"Dokument {n}  ({doc.get('belegtyp')})")
        print(f"   erkannte Komponenten: {', '.join(erkannt) if erkannt else 'KEINE'}")
        if kandidaten:
            print("   nicht erkannte Beschriftungen mit Betrag:")
            for k in kandidaten[:12]:
                print(f"      • {k}")
        print()

    if n == 0:
        print("Keine Krankenkassen-Dokumente gefunden.")
    else:
        print(f"{n} Dokument(e) geprueft. Diese Ausgabe enthaelt keine Betraege "
              f"und keine Namen und kann geteilt werden.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
