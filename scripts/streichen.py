#!/usr/bin/env python3
"""Eine maschinell erkannte Position streichen, weil der Mensch es besser weiss.

Streichen heisst hier: **dieses Dokument weist den Wert nicht aus** — die
Extraktion hat sich vertan. Es ist keine Aussage darueber, ob der Wert in die
Steuererklaerung gehoert; er kann in einem anderen Dokument stehen und dort
richtig sein.

Der Anlass: ein Kinderbetreuungsbeleg fuer zwei Kinder. Der Mensch hat die
zwei Werte selbst erfasst, die Extraktion lieferte zusaetzlich einen dritten —
ohne Person, und in der Summe zu viel. Eine allgemeine Regel ("wo eigene
Positionen stehen, gelten nur diese") waere falsch: beim Hypothekarbeleg hat
die Extraktion zwei von drei Hypotheken richtig gefunden und der Mensch nur
die dritte nachgetragen. Die Entscheidung gehoert deshalb ausgesprochen, nicht
erraten — und sie steht danach nachlesbar in ``korrekturen.json``.

Gestrichen wird ueber denselben Weg wie in der Oberflaeche: ein Eintrag
``entfernt: true`` mit Grund. Die Zeile bleibt in der Durchsicht sichtbar und
laesst sich dort mit einem Klick zurueckholen; vor dem Schreiben entsteht eine
Sicherung im Verlauf.

Aufruf::

    make streichen ZIELWERT="Kosten Kinderbetreuung" GRUND="zwei Kinder, zwei Werte"
    make streichen ZIELWERT="..." PROBE=1     # nur zeigen, nichts schreiben
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TRENNER = "─" * 70


def finde(samples: Path, zielwert: str, nur_maschine: bool) -> list[dict]:
    """Die betroffenen Zeilen — mit Dokument und Position."""
    from scripts.build_review_html import sammle_zeilen

    aus = []
    for r in sammle_zeilen(samples, pdf_dir=None, mit_crops=False):
        if str(r.get("zielwert") or "") != zielwert:
            continue
        if r.get("gestrichen"):
            continue
        if nur_maschine and r.get("neu"):
            continue
        aus.append(r)
    return aus


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--samples", type=Path, required=True)
    ap.add_argument("--zielwert", required=True)
    ap.add_argument("--grund", default="vom Menschen als zu viel bewertet")
    ap.add_argument("--auch-eigene", action="store_true",
                    help="Auch selbst erfasste Positionen streichen")
    ap.add_argument("--probe", action="store_true",
                    help="Nur zeigen, was gestrichen wuerde")
    args = ap.parse_args()

    treffer = finde(args.samples, args.zielwert, not args.auch_eigene)

    print(f"Streichen: {args.zielwert} — nur Zahlen, dieser Teil ist teilbar")
    print(TRENNER)
    print(f"   {len(treffer):3}  Position(en) betroffen")
    if not treffer:
        print("\n→ Nichts zu tun.")
        return 0
    dok = len({str(r.get("beleg") or "") for r in treffer})
    print(f"   {dok:3}  Dokument(e)")
    print(f"   Grund: {args.grund}")

    if args.probe:
        print("\n→ Probe — nichts geschrieben.")
        return 0

    from scripts.apply_korrekturen import uebernehme

    zeilen = [{"beleg": str(r.get("beleg") or ""),
               "zielwert": args.zielwert,
               "position": str(r.get("pos") or 1),
               "ziffer": str(r.get("ziffer") or ""),
               "streichen": "x",
               "notiz": args.grund} for r in treffer]
    bericht = uebernehme(zeilen, args.samples / "korrekturen.json")
    print(f"\n→ {bericht['geaendert']} geändert, {bericht['neu']} neu; "
          f"{bericht['gesamt']} Einträge insgesamt.")
    print("Zurueckholen: in der Durchsicht die Zeile aufklappen und die "
          "Streichung aufheben.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
