#!/usr/bin/env python3
"""Einen Aussteller in der Ablage vereinheitlichen.

Dieselbe Bank unter zwei Schreibweisen — einmal mit Rechtsformzusatz, einmal
ohne — sieht harmlos aus und ist es nicht: Hypothek und Schuldzins landen in
getrennten Töpfen und lassen sich nicht mehr paaren. Im eSteuerauszug stünde
dann eine Schuld ohne Zinsabzug.

Der Aussteller ist ein **gesperrtes Feld** der Ablage, und das bleibt so. Diese
Änderung geht deshalb den ausdrücklichen Weg: Zeile freigeben, ändern, wieder
sperren — jeder Schritt im Protokoll. Genau wie eine Buchung in einer
abgeschlossenen Periode, die man nur nach ausdrücklicher Öffnung korrigiert.

Beträge, Personen und Ziffern werden dabei **nicht** angefasst.

Aufruf::

    make ablage-aussteller ALT="Muster Bank" NEU="Muster Bank AG"
    make ablage-aussteller ALT="…" NEU="…" PROBE=1
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TRENNER = "─" * 70


def vereinheitliche(samples: Path, alt: str, neu: str,
                    probe: bool = False) -> int:
    """Alle Positionen von ``alt`` auf ``neu`` umschreiben. Gibt die Anzahl."""
    from extractors.ablage import oeffne, pfad_fuer

    verbindung = oeffne(pfad_fuer(samples))
    betroffen = [r["id"] for r in verbindung.execute(
        "SELECT id FROM position WHERE aussteller = ?", (alt,))]
    if probe or not betroffen:
        return len(betroffen)

    # Ganz oder gar nicht.
    #
    # Die drei Schritte — freigeben, aendern, sperren — sind zusammen eine
    # Aenderung. Bricht etwas dazwischen ab, bliebe eine gepruefte Position
    # freigegeben zurueck: der Schutz waere still ausgehebelt, und niemand
    # saehe es. Deshalb eine Transaktion; scheitert sie, ist nichts passiert
    # (260923-dua, Code-Review).
    try:
        with verbindung:
            for kennung in betroffen:
                verbindung.execute(
                    "UPDATE position SET status='vorschlag' WHERE id=?",
                    (kennung,))
                verbindung.execute(
                    "UPDATE position SET aussteller=?, "
                    "geaendert_am=datetime('now') WHERE id=?", (neu, kennung))
                verbindung.execute(
                    "UPDATE position SET status='geprueft' WHERE id=?",
                    (kennung,))
                verbindung.execute(
                    "INSERT INTO protokoll (id, was, feld, vorher, nachher) "
                    "VALUES (?,?,?,?,?)",
                    (kennung, "aussteller vereinheitlicht", "aussteller",
                     alt, neu))
    except Exception:
        # Sicherheitsnetz: was auch immer schiefging, keine Position darf
        # freigegeben zurueckbleiben.
        verbindung.rollback()
        raise

    offen = verbindung.execute(
        "SELECT count(*) AS n FROM position WHERE status='vorschlag'"
    ).fetchone()["n"]
    if offen:
        raise SystemExit(f"{offen} Position(en) sind freigegeben geblieben — "
                         f"das darf nicht sein. Bitte melden.")
    return len(betroffen)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--samples", type=Path, required=True)
    ap.add_argument("--alt", required=True)
    ap.add_argument("--neu", required=True)
    ap.add_argument("--probe", action="store_true")
    args = ap.parse_args()

    if not args.alt.strip() or not args.neu.strip():
        print("ALT und NEU dürfen nicht leer sein.", file=sys.stderr)
        return 1

    anzahl = vereinheitliche(args.samples, args.alt, args.neu,
                             probe=args.probe)

    print("Aussteller vereinheitlichen — nur Zahlen, dieser Teil ist teilbar")
    print(TRENNER)
    print(f"   {anzahl:3}  Position(en) betroffen")
    if not anzahl:
        print("\n→ Nichts zu tun. Schreibweise exakt prüfen "
              "(`make ablage-stand` zeigt sie).")
        return 1
    if args.probe:
        print("\n→ Probe — nichts geschrieben.")
    else:
        print("\n→ Geändert und wieder gesperrt; jeder Schritt steht im "
              "Protokoll.")
        print("   Beträge, Personen und Ziffern wurden nicht angefasst.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
