#!/usr/bin/env python3
"""Steht derselbe Wert zweimal in der Tabelle?

Verdacht: die Ablage führt Dokumentnamen, die es nach einer Umbenennung nicht
mehr gibt. Dann greift die Ersetzung nicht — sie fällt nur für Dokumente, die
die Ablage kennt —, und derselbe Betrag erscheint zweimal: einmal aus der
Ablage unter dem alten Namen, einmal aus der Extraktion unter dem neuen.

Gesucht wird deshalb nach Paaren mit gleichem Zielwert und gleichem Betrag,
aber verschiedenem Dokument. Ausgegeben werden nur Zahlen; welche Dokumente es
sind, steht im lokalen Teil.

Aufruf::

    make doppelte
"""
from __future__ import annotations

import argparse
import collections
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TRENNER = "─" * 70


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--samples", type=Path, required=True)
    ap.add_argument("--input", type=Path, required=True)
    args = ap.parse_args()

    from extractors.ablage import lade_geprueft
    from extractors.korrekturen import _normalisiere as norm
    from scripts.build_review_html import sammle_zeilen

    zeilen = [z for z in sammle_zeilen(args.samples, pdf_dir=None,
                                       mit_crops=False)
              if not z.get("gestrichen")]

    # Gleicher Zielwert, gleicher Betrag, verschiedene Dokumente.
    nach_wert: dict[tuple, set] = {}
    for z in zeilen:
        betrag = norm(z.get("betrag"))
        ziel = str(z.get("zielwert") or "")
        if not betrag or not ziel:
            continue
        nach_wert.setdefault((ziel, betrag), set()).add(str(z.get("beleg") or ""))
    doppelt = {k: v for k, v in nach_wert.items() if len(v) > 1}

    # Welche Dokumentnamen der Ablage haben keine Datei mehr?
    in_ablage = {str(z.get("pdf_name") or "") for z in lade_geprueft(args.samples)}
    vorhanden = {p.name for p in args.input.glob("*.pdf")}
    verwaist = sorted(n for n in in_ablage if n and n not in vorhanden)

    print("Doppelte Werte — nur Zahlen, dieser Teil ist teilbar")
    print(TRENNER)
    print(f"   {len(zeilen):3}  Zeilen in der Tabelle")
    print(f"   {len(doppelt):3}  Betrag/Zielwert-Paare unter MEHREREN Dokumenten")
    print(f"   {sum(len(v) for v in doppelt.values()):3}  Zeilen davon betroffen")
    print(f"   {len(verwaist):3}  Dokumentnamen der Ablage ohne PDF")
    if doppelt:
        print("\n   Betroffene Zielwerte:")
        for ziel, n in collections.Counter(
                k[0] for k in doppelt).most_common():
            print(f"      {n:>3}×  {ziel}")

    if doppelt or verwaist:
        print("\nWelche — NICHT teilen, enthält Dateinamen")
        print(TRENNER)
        for (ziel, _betrag), dokumente in sorted(doppelt.items()):
            print(f"   {ziel}:")
            for d in sorted(dokumente):
                print(f"      {d}")
        for n in verwaist:
            print(f"   Ablage kennt, Datei fehlt: {n}")
    return 1 if doppelt else 0


if __name__ == "__main__":
    raise SystemExit(main())
