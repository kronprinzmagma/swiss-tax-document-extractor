#!/usr/bin/env python3
"""Jahresabschluss und Vorjahresvergleich (261029).

    make profil               Profil des Jahres schreiben (wiederholbar)
    make jahr-abschliessen    Profil schreiben, Ablage und Ordner sperren
    make jahr-oeffnen GRUND="…"           Sperre aufheben (in den Verlauf)
    make vorjahr-quittieren KURZ=V-… GRUND="…"   „fehlt" ist erklärt
    make vorjahr-quittieren KURZ=V-… ZURUECK=1   Quittung zurücknehmen

Der Abgleich selbst läuft in ``make pruefen`` (Abschnitt „Vorjahr").
Siehe ``extractors/vorjahr.py`` und ``extractors/steuerjahr.py``.

Teilbar: die Ausgabe nennt Anzahlen und Kürzel, keine Namen, keine Beträge.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from extractors import steuerjahr, vorjahr  # noqa: E402


def leiste(samples: Path, pdf_dir: Path | None) -> dict:
    """Leistendaten mit Rollen statt Namen — wie ``make pruefen``."""
    from scripts import export_extension
    with mock.patch("scripts.build_review_html.personennamen", return_value={}):
        return export_extension.baue(samples, pdf_dir=pdf_dir)


def profil(samples: Path, jahr: int, pdf_dir: Path | None) -> int:
    posten = vorjahr.posten_der_leiste(leiste(samples, pdf_dir))
    if not posten:
        print("Keine Posten in der Leiste — kein Profil geschrieben.")
        return 1
    vorjahr.schreibe_profil(steuerjahr.ordner_von(samples), jahr, posten)
    je: dict[str, int] = {}
    for p in posten:
        je[p.formular] = je.get(p.formular, 0) + 1
    print(f"Profil {jahr} — nur Zahlen, teilbar")
    print("─" * 70)
    for form, n in je.items():
        print(f"   {n:3}  {form}")
    print(f"   {len(posten):3}  Posten insgesamt → {vorjahr.PROFIL}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--samples", type=Path, required=True)
    ap.add_argument("--jahr", type=int, required=True)
    ap.add_argument("--pdf-dir", type=Path, default=None)
    ap.add_argument("was", choices=("profil", "abschliessen", "oeffnen", "quittieren"))
    ap.add_argument("--grund", default="")
    ap.add_argument("--kurz", default="")
    ap.add_argument("--zurueck", action="store_true")
    ap.add_argument("--vorjahr", type=Path, default=None,
                    help="Ausgabeordner des Vorjahrs (dort liegt profil.json)")
    args = ap.parse_args()
    ordner = steuerjahr.ordner_von(args.samples)

    if args.was == "profil":
        return profil(args.samples, args.jahr, args.pdf_dir)

    if args.was == "abschliessen":
        if steuerjahr.abschluss(ordner):
            print(f"Steuerjahr {args.jahr} ist bereits abgeschlossen.")
            return 0
        if profil(args.samples, args.jahr, args.pdf_dir):
            return 1
        from extractors.ablage import setze_jahr_zustand
        setze_jahr_zustand(args.samples, True, f"abgeschlossen {args.jahr}")
        steuerjahr.schliesse_ab(ordner, args.jahr)
        print(f"\nSteuerjahr {args.jahr} abgeschlossen. Ablage und Durchsicht sind gesperrt;\n"
              "lesen, prüfen, sichern, einfrieren und die Leiste gehen weiter.\n"
              f"Nächstes Jahr:  make wo STEUERJAHR={args.jahr + 1}")
        return 0

    if args.was == "oeffnen":
        try:
            ziel = steuerjahr.oeffne_wieder(ordner, args.grund)
        except ValueError as e:
            print(e)
            return 1
        from extractors.ablage import jahr_ist_zu, setze_jahr_zustand
        if jahr_ist_zu(args.samples):
            setze_jahr_zustand(args.samples, False, args.grund)
        print("Wieder offen." if ziel else "War nicht abgeschlossen.")
        return 0

    # quittieren
    steuerjahr.pruefe_offen(ordner, "eine Quittung")
    # Nur ein Kürzel, das es gibt (261029: das Beispiel aus der Anleitung wurde
    # wörtlich quittiert und angenommen).
    if not args.zurueck:
        alt = vorjahr.lade_profil(args.vorjahr) if args.vorjahr else None
        if alt is None:
            print("Kein Profil des Vorjahrs — es gibt nichts zu quittieren.")
            return 1
        if args.kurz not in {p.kurz for p in alt}:
            print(f"{args.kurz} gibt es im Profil des Vorjahrs nicht. Die Kürzel "
                  "stehen in make pruefen unter „Vorjahr“ (✗ fehlt).")
            return 1
    try:
        vorjahr.quittiere(ordner, args.kurz, args.grund, zurueck=args.zurueck)
    except ValueError as e:
        print(e)
        return 1
    print(f"{args.kurz}: Quittung {'zurückgenommen' if args.zurueck else 'festgehalten'}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
