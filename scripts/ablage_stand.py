#!/usr/bin/env python3
"""Was steht in der Ablage? Nur Zahlen und Zielwerte — teilbar.

Der Blick in den Tresor, ohne ihn zu öffnen: wie viele Positionen, wie viele
davon gesperrt, welche Zielwerte, und wo eine Zahl fehlt.
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
    args = ap.parse_args()

    from extractors.ablage import oeffne, pfad_fuer

    pfad = pfad_fuer(args.samples)
    if not pfad.exists():
        print(f"Keine Ablage unter {pfad}")
        return 1
    v = oeffne(pfad)
    zeilen = list(v.execute("SELECT * FROM position"))
    geschlossen = list(v.execute("SELECT * FROM gruppe_geschlossen"))

    print("Ablage — nur Zahlen und Zielwerte, dieser Teil ist teilbar")
    print(TRENNER)
    print(f"   {len(zeilen):3}  Positionen")
    nach_status = collections.Counter(z["status"] for z in zeilen)
    for status, n in sorted(nach_status.items()):
        print(f"        {n:>3}  {status}")
    ohne_betrag = [z for z in zeilen if not str(z["betrag"] or "").strip()]
    ohne_person = [z for z in zeilen if not str(z["person"] or "").strip()]
    print(f"   {len(ohne_betrag):3}  ohne Betrag")
    print(f"   {len(ohne_person):3}  ohne Person")
    print(f"   {len(geschlossen):3}  abgeschlossene Gruppe(n)")

    # Zwei Positionen desselben Dokuments mit derselben Nummer.
    #
    # Sie sind nicht mehr auseinanderzuhalten: jede Auswertung, die ueber
    # (Dokument, Zielwert, Position) paart, verliert eine davon — beim
    # eSteuerauszug ist so eine Hypothek verschwunden. Ein Eindeutigkeits-
    # Index waere die naheliegende Loesung und wurde verworfen: er machte die
    # bestehende Ablage unlesbar. Also gemeldet statt verboten
    # (260923-dua, Code-Review).
    doppelte = list(v.execute(
        "SELECT dokument, zielwert, pos, count(*) AS n FROM position "
        "GROUP BY dokument, zielwert, pos HAVING n > 1"))
    if doppelte:
        print(f"\n   ⚠ {len(doppelte)} Positionsnummer(n) doppelt vergeben "
              f"({sum(d['n'] for d in doppelte)} Positionen betroffen).")
        print("     Zwei Werte desselben Dokuments tragen dieselbe Nummer und")
        print("     sind damit nicht mehr zu unterscheiden. Betroffen:")
        for d in doppelte:
            print(f"        {d['n']}×  {d['zielwert']} (Position {d['pos']})")

    print("\n   Positionen je Zielwert:")
    for (ziel, n) in sorted(collections.Counter(
            z["zielwert"] for z in zeilen).items(),
            key=lambda kv: (-kv[1], kv[0])):
        leer = sum(1 for z in zeilen if z["zielwert"] == ziel
                   and not str(z["betrag"] or "").strip())
        print(f"      {n:>3}×  {ziel}"
              f"{f'   ({leer} ohne Betrag)' if leer else ''}")

    # Welcher Zielwert bei welchem Aussteller — der lokale Teil.
    #
    # Ohne diese Sicht bleibt unklar, warum sich eine Hypothek nicht mit
    # ihrem Zins paaren laesst: liegt sie bei einem anderen Institut, sieht
    # man es nur hier (260923-dua).
    print("\n\nWelche Aussteller — NICHT teilen, enthaelt Bankbeziehungen")
    print(TRENNER)
    je_paar = collections.Counter(
        (str(z["aussteller"] or "—"), z["zielwert"]) for z in zeilen)
    for (aussteller, ziel) in sorted(je_paar):
        print(f"   {aussteller[:34]:<34} {je_paar[(aussteller, ziel)]:>2}×  "
              f"{ziel}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
