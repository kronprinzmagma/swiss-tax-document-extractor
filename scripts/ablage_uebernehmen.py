#!/usr/bin/env python3
"""Die geprüften Werte einmalig in die Ablage übernehmen.

Einmaliger Schritt beim Umstieg: was der Mensch bereits geprüft hat, wandert
aus der bisherigen Durchsicht in die Ablage und bekommt dort eine feste
Kennung. Ab dann ist es gespeichert statt hergeleitet — kein Lauf fügt es mehr
neu zusammen, und keiner kann es ändern.

Der Schritt ist wiederholbar: eine Position, die schon in der Ablage steht,
wird erkannt und nicht doppelt angelegt. Erkannt wird sie über Dokument,
Zielwert und Position — das ist hier zulässig, weil es der **einzige** Moment
ist, in dem diese Angaben noch als Identität dienen. Danach nie wieder.

Aufruf::

    make ablage-uebernehmen          # übernehmen
    make ablage-uebernehmen PROBE=1  # nur zeigen
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TRENNER = "─" * 70


def _anker(zeile: dict) -> str:
    """Seite und Stelle — oder gar nichts.

    Gesucht wurde unter „seite"; die Zeilen fuehren die Seite aber als
    „page". Heraus kam `S.None`: ein Feld, das belegt aussieht und nichts
    sagt. Ein leerer Anker ist ehrlicher (260924-dua).
    """
    seite = zeile.get("page", zeile.get("seite"))
    bbox = zeile.get("bbox")
    if seite is None:
        return ""
    return f"S.{seite} {tuple(bbox)}" if bbox else f"S.{seite}"


def uebernehme(samples: Path, db_pfad: Path, probe: bool = False) -> dict:
    """Jede Zeile des jetzigen Standes wandert in die Ablage.

    Nur in eine **leere** Ablage. Ein zweiter Lauf hiesse, jede Zeile wieder
    identifizieren zu muessen — ueber Dokument, Zielwert und Position, also
    genau ueber die Identitaet, die sich als unzuverlaessig erwiesen hat. Zwei
    Zeilen mit demselben Tripel wuerden dabei zu einer verschmelzen, lautlos.
    Deshalb: einmal, vollstaendig, oder gar nicht.
    """
    from extractors.ablage import naechste_id, oeffne
    from extractors.korrekturen import finde, gilt_als_bestaetigt, lade_alle
    from scripts.build_review_html import sammle_zeilen

    korrekturen = lade_alle(samples)
    # Der Fingerabdruck macht das Dokument unabhaengig von seinem Namen.
    from extractors.ablage import namen_je_fingerabdruck
    finger_je_name = {n: f for f, n in namen_je_fingerabdruck(samples).items()}
    verbindung = oeffne(db_pfad)
    schon = verbindung.execute(
        "SELECT count(*) AS n FROM position").fetchone()["n"]
    if schon:
        raise SystemExit(
            f"Die Ablage enthaelt bereits {schon} Position(en). Die Uebernahme "
            f"laeuft nur einmal.\nSoll sie wiederholt werden, die Datei "
            f"vorher zur Seite legen:\n   {db_pfad}")

    zeilen = [z for z in sammle_zeilen(samples, pdf_dir=None, mit_crops=False)
              if not z.get("gestrichen")]

    neu = als_vorschlag = 0
    for z in zeilen:
        dok = str(z.get("beleg") or "")
        ziel = str(z.get("zielwert") or "")
        pos = int(z.get("pos") or 1)
        # Dieselbe Frage wie in der Oberflaeche und in `make bereit`:
        # ``finde`` loest frueherer Bezeichnungen und verschobene Nummern auf.
        eintrag = finde(korrekturen, dok, ziel, pos)
        ist_geprueft = bool(eintrag) and gilt_als_bestaetigt(eintrag)
        if not ist_geprueft:
            als_vorschlag += 1
        neu += 1
        if probe:
            continue
        kennung = naechste_id(verbindung)
        verbindung.execute(
            "INSERT INTO position (id, dokument, dokument_id, zielwert, pos, "
            "ziffer, person, aussteller, betrag, jahr, unterscheidung, anker, "
            "herkunft, status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (kennung, dok, finger_je_name.get(dok, ""), ziel, pos,
             str(z.get("ziffer") or ""),
             str(z.get("person") or ""), str(z.get("aussteller") or ""),
             str(z.get("betrag") or ""), str(z.get("jahr") or ""),
             str(z.get("unterscheidung") or ""), _anker(z),
             str(z.get("herkunft") or "modell"),
             "geprueft" if ist_geprueft else "vorschlag"))
        verbindung.execute(
            "INSERT INTO protokoll (id, was, nachher) VALUES (?,?,?)",
            (kennung, "uebernommen",
             "geprueft" if ist_geprueft else "vorschlag"))
    if not probe:
        verbindung.commit()
        # Gegenprobe: so viele Zeilen wie der jetzige Stand, keine weniger.
        drin = verbindung.execute(
            "SELECT count(*) AS n FROM position").fetchone()["n"]
        if drin != len(zeilen):
            raise SystemExit(f"Uebernahme unvollstaendig: {drin} von "
                             f"{len(zeilen)} Zeilen in der Ablage.")
    return {"neu": neu, "zeilen": len(zeilen), "vorschlag": als_vorschlag,
            "geprueft": neu - als_vorschlag}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--samples", type=Path, required=True)
    ap.add_argument("--db", type=Path, default=None)
    ap.add_argument("--probe", action="store_true")
    args = ap.parse_args()

    db = args.db or args.samples.parent / "positionen.db"
    bericht = uebernehme(args.samples, db, probe=args.probe)

    print("Ablage — nur Zahlen, dieser Teil ist teilbar")
    print(TRENNER)
    print(f"   {bericht['neu']:3}  Position(en) übernommen")
    print(f"        davon {bericht['geprueft']:>3} geprüft (gesperrt)")
    print(f"        davon {bericht['vorschlag']:>3} als Vorschlag")
    print(f"   {bericht['zeilen']:3}  Zeilen hat der jetzige Stand")
    if args.probe:
        print("\n→ Probe — nichts geschrieben.")
    else:
        print(f"\n→ {db}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
