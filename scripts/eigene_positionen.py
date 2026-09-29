#!/usr/bin/env python3
"""Welche Positionen hat der Mensch selbst erfasst — und stehen sie doppelt?

Selbst erfasste Zeilen (``"neu": true`` in der Durchsicht) lagen lange im
toten Winkel: liegt so eine Position auf einem Dokument, das die Ablage
führt, warf der Tabellenbau sie kommentarlos weg (260929-dua). Seit dem Fix
überleben sie — damit kommen aber auch alte Einträge zurück, die längst in
ein anderes Dokument übertragen wurden.

Diese Übersicht macht beides sichtbar. Dokumente erscheinen als ``D1``,
``D2`` … — der Bericht ist damit teilbar.

Aufruf::

    make eigene
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TRENNER = "─" * 70


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--samples", type=Path, required=True)
    ap.add_argument("--uebernehmen", default="",
                    help="Kuerzel (D3) — nur dessen fehlende Positionen in "
                         "die Ablage schreiben")
    ap.add_argument("--zuruecknehmen", default="",
                    help="Kuerzel (D2) — dessen Eintraege OHNE Zielwert "
                         "streichen (rueckholbar)")
    ap.add_argument("--grund", default="Eintrag ohne Zielwert")
    ap.add_argument("--probe", action="store_true",
                    help="nur zeigen, nichts schreiben")
    args = ap.parse_args()

    from extractors.ablage import gruppen_in_ablage, lade_geprueft
    from extractors.korrekturen import (
        ist_gestrichen, ist_zurueckgenommen, lade_alle, zerlege,
    )

    gruppen = gruppen_in_ablage(args.samples)
    dokumente = {d for d, _ in gruppen}
    in_ablage = {(r["pdf_name"], r["beschreibung"], r["pos"] or 1)
                 for r in lade_geprueft(args.samples)}

    namen: dict[str, str] = {}

    def kurz(dokument: str) -> str:
        return namen.setdefault(dokument, f"D{len(namen) + 1}")

    # Was die Ablage fuer dasselbe Dokument bereits fuehrt. Ohne diese
    # Gegenueberstellung ist nicht entscheidbar, ob ein Handeintrag ein
    # fehlender Wert oder der alte Name eines schon gespeicherten ist.
    ablage_je_dokument: dict[str, list[tuple[str, str]]] = {}
    for r in lade_geprueft(args.samples):
        ablage_je_dokument.setdefault(r["pdf_name"], []).append(
            (r["beschreibung"], str(r["betrag"] or "")))

    zeilen = []
    for schluessel, eintrag in sorted(lade_alle(args.samples).items()):
        if not isinstance(eintrag, dict) or not eintrag.get("neu"):
            continue
        if not eintrag.get("soll"):
            continue
        if ist_gestrichen(eintrag) or ist_zurueckgenommen(eintrag):
            continue
        beleg, ziel, pos = zerlege(schluessel)
        zeilen.append({
            "beleg": beleg, "ziel": ziel, "pos": pos,
            "soll": eintrag["soll"],
            "belegart": eintrag.get("belegtyp") or "—",
            "auf_ablage_dokument": beleg in dokumente,
            "auch_in_ablage": (beleg, ziel, pos) in in_ablage,
        })

    print("Selbst erfasste Positionen — teilbar, Dokumente als D1, D2 …")
    print(TRENNER)
    print(f"   {len(zeilen):3}  selbst erfasst")
    print(f"   {sum(1 for z in zeilen if z['auf_ablage_dokument']):3}  "
          f"davon auf einem Dokument, das die Ablage führt")
    print(f"   {sum(1 for z in zeilen if z['auch_in_ablage']):3}  "
          f"davon führt die Ablage dieselbe Position bereits")
    print()
    for z in sorted(zeilen, key=lambda z: (kurz(z["beleg"]), z["ziel"])):
        lage = ("Ablage führt dieselbe" if z["auch_in_ablage"]
                else "auf Ablage-Dokument" if z["auf_ablage_dokument"]
                else "eigenes Dokument")
        print(f"   {kurz(z['beleg']):>4}  pos {z['pos']}  {z['ziel'][:34]:<34}"
              f"  {z['soll']:>12}  {z['belegart'][:16]:<16}  {lage}")

    fehlend = [z for z in zeilen
               if z["auf_ablage_dokument"] and not z["auch_in_ablage"]]
    if fehlend:
        print("\nNicht in der Ablage — was fuehrt die Ablage stattdessen?")
        print(TRENNER)
        for z in sorted(fehlend, key=lambda z: kurz(z["beleg"])):
            gleich = [(b, w) for b, w in ablage_je_dokument.get(z["beleg"], [])
                      if w == z["soll"]]
            print(f"   {kurz(z['beleg']):>4}  {z['ziel'][:30]:<30}"
                  f"  {z['soll']:>12}")
            for b, w in sorted(ablage_je_dokument.get(z["beleg"], [])):
                zeichen = "= gleicher Betrag" if w == z["soll"] else ""
                print(f"         Ablage: {b[:30]:<30}  {w:>12}  {zeichen}")
            if not ablage_je_dokument.get(z["beleg"]):
                print("         Ablage: — nichts fuer dieses Dokument")
            if not gleich:
                print("         → fehlt wirklich")

    if args.zuruecknehmen:
        # Eintraege OHNE Zielwert zuruecknehmen.
        #
        # Sie entstanden, solange der Server einen leeren Zielwert annahm.
        # Eine solche Position kann in keiner Ziffer landen und meldet sich
        # danach dauerhaft als Eintrag ohne Bezug. Geloescht wird nichts —
        # es entsteht ein Grabstein, den die Durchsicht wieder aufheben kann
        # (260929-dua).
        gesucht = args.zuruecknehmen.strip().upper()
        treffer = [d for d, m in namen.items() if m == gesucht]
        if not treffer:
            print(f"\nKein Dokument mit dem Kuerzel {gesucht}.")
            return 1
        dokument = treffer[0]
        ohne = [z for z in zeilen
                if z["beleg"] == dokument
                and not z["ziel"].strip().strip("#0123456789")]
        print(f"\n{gesucht}: {len(ohne)} Eintrag/Eintraege ohne Zielwert")
        print(TRENNER)
        for z in ohne:
            print(f"   pos {z['pos']}  [{z['ziel']}]  {z['soll']:>12}")
        if not ohne:
            print("   nichts zu tun")
            return 0
        if args.probe:
            print("   (Probe — nichts geschrieben)")
            return 0
        from scripts.apply_korrekturen import uebernehme
        bericht = uebernehme(
            [{"beleg": dokument, "zielwert": z["ziel"],
              "position": str(z["pos"]), "streichen": "x",
              "notiz": args.grund} for z in ohne],
            args.samples / "korrekturen.json")
        print(f"   zurueckgenommen: {bericht['geaendert']} geaendert, "
              f"{bericht['neu']} neu")
        print("   Zurueckholen: in der Durchsicht die Streichung aufheben.")
        return 0

    if args.uebernehmen:
        gesucht = args.uebernehmen.strip().upper()
        treffer = [d for d, m in namen.items() if m == gesucht]
        if not treffer:
            print(f"\nKein Dokument mit dem Kuerzel {gesucht}.")
            return 1
        dokument = treffer[0]
        offen = {(z["beleg"], z["ziel"], z["pos"]) for z in fehlend
                 if z["beleg"] == dokument}
        print(f"\n{gesucht}: {len(offen)} Position(en) in die Ablage")
        print(TRENNER)
        for z in sorted(fehlend, key=lambda z: z["ziel"]):
            if z["beleg"] == dokument:
                print(f"   {z['ziel'][:34]:<34}  {z['soll']:>12}")
        if args.probe:
            print("   (Probe — nichts geschrieben)")
            return 0
        from extractors.ablage import uebernimm_durchsicht
        bericht = uebernimm_durchsicht(args.samples, nur=offen)
        print(f"   geschrieben: {bericht or 'nichts'}")
        return 0

    if namen:
        print("\nWelches Dokument ist welches — NICHT teilen")
        print(TRENNER)
        for dokument, marke in sorted(namen.items(), key=lambda p: p[1]):
            print(f"   {marke:>4}  {dokument}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
