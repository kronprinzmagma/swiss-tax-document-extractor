#!/usr/bin/env python3
"""Wie viele Konten gibt es, und wem gehören sie?

Bevor man einen eSteuerauszug baut, muss klar sein, was hineingehört: ein
Auszug gehört einer steuerpflichtigen Person bei einem Institut. Diese Zahlen
sagen, wie viele das sind — und ob Konten zweier Personen bei derselben Bank
liegen.

Nur Zahlen, keine Namen.

Aufruf::

    make konten-uebersicht
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

WERTSCHRIFTEN = ("Saldo 31.12.", "Steuerwert 31.12.",
                 "Bruttoertrag mit Verrechnungssteuer",
                 "Bruttoertrag ohne Verrechnungssteuer")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--samples", type=Path, required=True)
    args = ap.parse_args()

    from scripts.build_review_html import sammle_zeilen

    zeilen = [r for r in sammle_zeilen(args.samples, pdf_dir=None,
                                       mit_crops=False)
              if not r.get("gestrichen")
              and str(r.get("zielwert") or "") in WERTSCHRIFTEN]

    dokumente = {str(r.get("beleg") or "") for r in zeilen}
    institute = {str(r.get("aussteller") or "") for r in zeilen}
    paare = {(str(r.get("aussteller") or ""), str(r.get("person") or ""))
             for r in zeilen}

    print("Konten — nur Zahlen, dieser Teil ist teilbar")
    print(TRENNER)
    print(f"   {len(zeilen):3}  Wertschriften-Positionen")
    print(f"   {len(dokumente):3}  verschiedene Dokumente")
    print(f"   {len(institute):3}  Institute")
    print(f"   {len(paare):3}  Kombinationen Institut + Person")

    # Bei welchen Instituten liegen Konten mehrerer Personen?
    personen_je_institut = collections.defaultdict(set)
    for haus, person in paare:
        personen_je_institut[haus].add(person)
    gemischt = [h for h, p in personen_je_institut.items() if len(p) > 1]
    print(f"   {len(gemischt):3}  Institut(e) mit Konten mehrerer Personen")

    print("\n   Positionen je Person:")
    for person, n in collections.Counter(
            str(r.get("person") or "—") for r in zeilen).most_common():
        print(f"      {n:>3}×  {person}")

    print("\n   Dokumente je Institut+Person (ohne Namen):")
    je_paar = collections.Counter(
        (str(r.get("aussteller") or ""), str(r.get("person") or ""))
        for r in zeilen)
    print(f"      {sorted(je_paar.values())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
