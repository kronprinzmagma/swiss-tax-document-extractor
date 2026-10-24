#!/usr/bin/env python3
"""Positionsnummern ansehen und geradeziehen.

Zwei Werte desselben Dokuments mit derselben Nummer sind nicht mehr
auseinanderzuhalten. Jede Auswertung, die über (Dokument, Zielwert, Position)
paart, verliert dann einen davon — beim eSteuerauszug ist so eine Hypothek
verschwunden (260923-dua).

``--zeigen`` listet die betroffenen Zeilen. Der teilbare Teil nennt nur
Kennungen, Nummern und eine laufende Dokumentziffer; Beträge und Dateinamen
stehen darunter im lokalen Teil.

``--umnummerieren`` vergibt je Dokument die Nummern 1..n neu. Die Reihenfolge
kommt aus dem **Anker** — also aus der Stelle im Dokument, an der der Wert
steht. Das ist die einzige Ordnung, die etwas bedeutet: so steht es im Beleg,
und so gehört Zins zu Hypothek. Fehlt der Anker, bleibt die bisherige
Reihenfolge.

Die Position ist ein gesperrtes Feld. Geändert wird deshalb wie überall:
freigeben, ändern, sperren — in einer Transaktion, jeder Schritt im Protokoll.

Aufruf::

    make ablage-positionen ZIELWERT="Hypothekarschuld 31.12."
    make ablage-positionen ZIELWERT="…" JETZT=1
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TRENNER = "─" * 70

# „S.2 (71.0, 305.4, 120.0, 314.9)" → (2, 305.4)
_ANKER = re.compile(r"S\.(\d+)\s*\(([\d.]+),\s*([\d.]+)")


def anker_ordnung(anker: str) -> tuple:
    """Seite und Höhe aus dem Anker — die Reihenfolge im Dokument.

    Ohne Anker ein Wert, der ans Ende sortiert: dann entscheidet die bisherige
    Reihenfolge, und nichts wird durcheinandergebracht.
    """
    treffer = _ANKER.search(str(anker or ""))
    if not treffer:
        return (9999, 9999.0)
    return (int(treffer.group(1)), float(treffer.group(3)))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--samples", type=Path, required=True)
    ap.add_argument("--zielwert", required=True)
    ap.add_argument("--jetzt", action="store_true",
                    help="Umnummerieren; ohne dies nur zeigen")
    args = ap.parse_args()

    from extractors.ablage import oeffne, pfad_fuer

    verbindung = oeffne(pfad_fuer(args.samples))
    zeilen = list(verbindung.execute(
        "SELECT * FROM position WHERE zielwert = ? ORDER BY dokument, pos, id",
        (args.zielwert,)))
    if not zeilen:
        print(f"Keine Position mit dem Zielwert {args.zielwert!r}.")
        return 1

    # Dokumente nur als laufende Ziffer — Namen gehoeren nicht in den
    # teilbaren Teil.
    nummer: dict[str, int] = {}
    for z in zeilen:
        nummer.setdefault(z["dokument"], len(nummer) + 1)

    print(f"Positionen: {args.zielwert} — nur Zahlen, dieser Teil ist teilbar")
    print(TRENNER)
    print(f"   {len(zeilen):3}  Position(en) in {len(nummer)} Dokument(en)")
    mit_anker = sum(1 for z in zeilen if anker_ordnung(z["anker"])[0] != 9999)
    print(f"   {mit_anker:3}  davon mit Anker — nur sie lassen sich nach der "
          f"Stelle im Beleg ordnen")
    for z in zeilen:
        print(f"      {z['id']}  Dokument {nummer[z['dokument']]}  "
              f"Position {z['pos']}"
              f"{'' if anker_ordnung(z['anker'])[0] != 9999 else '  (ohne Anker)'}")

    # Neue Nummern je Dokument, geordnet nach der Stelle im Beleg.
    neu_je_id: dict[str, int] = {}
    for dokument in nummer:
        gruppe = sorted((z for z in zeilen if z["dokument"] == dokument),
                        key=lambda z: (anker_ordnung(z["anker"]), z["pos"],
                                       z["id"]))
        for i, z in enumerate(gruppe, start=1):
            if z["pos"] != i:
                neu_je_id[z["id"]] = i

    if not neu_je_id:
        print("\n→ Die Nummern sind bereits fortlaufend. Nichts zu tun.")
        return 0

    print(f"\n   {len(neu_je_id):3}  Position(en) bekämen eine neue Nummer:")
    for kennung, neu in neu_je_id.items():
        alt = next(z["pos"] for z in zeilen if z["id"] == kennung)
        print(f"      {kennung}  {alt} → {neu}")

    if not args.jetzt:
        print("\n→ Nur gezeigt. Mit JETZT=1 ausführen.")
        return 0

    try:
        with verbindung:
            for kennung, neu in neu_je_id.items():
                alt = next(z["pos"] for z in zeilen if z["id"] == kennung)
                verbindung.execute(
                    "UPDATE position SET status='vorschlag' WHERE id=?",
                    (kennung,))
                verbindung.execute(
                    "UPDATE position SET pos=?, geaendert_am=datetime('now') "
                    "WHERE id=?", (neu, kennung))
                verbindung.execute(
                    "UPDATE position SET status='geprueft' WHERE id=?",
                    (kennung,))
                verbindung.execute(
                    "INSERT INTO protokoll (id, was, feld, vorher, nachher) "
                    "VALUES (?,?,?,?,?)",
                    (kennung, "umnummeriert", "pos", str(alt), str(neu)))
    except Exception:
        verbindung.rollback()
        raise

    offen = verbindung.execute(
        "SELECT count(*) AS n FROM position WHERE status='vorschlag'"
    ).fetchone()["n"]
    if offen:
        raise SystemExit(f"{offen} Position(en) sind freigegeben geblieben.")

    print(f"\n→ {len(neu_je_id)} umnummeriert, wieder gesperrt, protokolliert.")
    print("   Beträge wurden nicht angefasst.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
