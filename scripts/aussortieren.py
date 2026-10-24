#!/usr/bin/env python3
"""Abgehakte Belege aus dem Eingangsordner nehmen.

Nicht jedes PDF im Ordner gehört in die Steuererklärung — eine Saldomeldung
ohne Einzahlung, ein Begleitschreiben, ein Formular der Gemeinde. Wer es
einmal angesehen und als **irrelevant** abgehakt hat, will es nicht bei jedem
Lauf wieder einlesen und nicht in jeder Liste wiedersehen.

Dieses Skript verschiebt solche Dokumente in einen Unterordner. Sie sind damit
aus dem Glob des nächsten Laufs heraus — und trotzdem nicht weg:

* Die Datei liegt einen Ordner tiefer, nicht im Papierkorb.
* Die Durchsicht bleibt unangetastet: der Eintrag „abgehakt" ist der Grund,
  warum verschoben wurde, und er bleibt der Nachweis dafür.
* Jeder Schritt steht mit Zeitpunkt im Journal; ``--zurueck`` holt sie zurück.

In der Übersicht bleibt das Dokument sichtbar — als *ausgeschlossen*. Etwas,
das man einmal beurteilt hat, soll nicht unsichtbar werden; sonst weiss man
im nächsten Jahr nicht mehr, ob man es übersehen oder bewusst weggelassen hat.

Ausgabe in zwei Teilen: zuerst Zahlen (teilbar), dann die Namen (bleibt auf
dem Gerät).

Aufruf::

    PYTHONPATH=. .venv/bin/python scripts/aussortieren.py \\
        --input belege --samples output/latest/json
    …  --jetzt      wirklich verschieben
    …  --zurueck    zurueckholen
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TRENNER = "─" * 70
ORDNER = "irrelevant"
JOURNAL = "aussortiert.json"


def ziel_ordner(input_dir: Path) -> Path:
    return input_dir / ORDNER


def _journal(samples: Path) -> Path:
    return samples.parent / JOURNAL


def plane(samples: Path, input_dir: Path) -> list[str]:
    """Welche Dokumente sind abgehakt und liegen noch im Eingangsordner?"""
    from extractors.korrekturen import ausgeschlossene_dokumente, lade_alle

    abgehakt = {b for b, art in ausgeschlossene_dokumente(
        lade_alle(samples)).items() if art == "irrelevant"}
    return sorted(n for n in abgehakt if (input_dir / n).is_file())


def fuehre_aus(samples: Path, input_dir: Path, plan: list[str],
               nur: str | None = None) -> list[dict]:
    """Verschiebt und schreibt das Journal. Überschreibt nie etwas."""
    ordner = ziel_ordner(input_dir)
    getan: list[dict] = []
    for name in plan:
        if nur and name != nur:
            continue
        quelle = input_dir / name
        if not quelle.is_file():
            continue
        try:
            ordner.mkdir(exist_ok=True)
        except OSError:
            break
        ziel = ordner / name
        if ziel.exists():
            # Gleicher Name, anderer Inhalt — nichts ueberschreiben.
            stamm, punkt, endung = name.rpartition(".")
            n = 2
            while ziel.exists():
                ziel = ordner / (f"{stamm}-{n}.{endung}" if punkt
                                 else f"{name}-{n}")
                n += 1
        try:
            quelle.rename(ziel)
        except OSError:
            continue
        getan.append({"zeit": datetime.now().isoformat(timespec="seconds"),
                      "name": name, "nach": ziel.name})

    if getan:
        pfad = _journal(samples)
        bisher: list[dict] = []
        if pfad.exists():
            try:
                bisher = json.loads(pfad.read_text())
            except (OSError, json.JSONDecodeError):
                bisher = []
        pfad.write_text(json.dumps(bisher + getan, ensure_ascii=False,
                                   indent=2))
    return getan


def zurueck(samples: Path, input_dir: Path) -> list[dict]:
    """Alles wieder in den Eingangsordner."""
    pfad = _journal(samples)
    if not pfad.exists():
        return []
    try:
        journal = json.loads(pfad.read_text())
    except (OSError, json.JSONDecodeError):
        return []
    ordner = ziel_ordner(input_dir)
    erledigt: list[dict] = []
    for eintrag in reversed(journal):
        quelle = ordner / eintrag.get("nach", eintrag.get("name", ""))
        ziel = input_dir / eintrag.get("name", "")
        if not quelle.is_file() or ziel.exists():
            continue
        try:
            quelle.rename(ziel)
        except OSError:
            continue
        erledigt.append(eintrag)
    rest = [e for e in journal if e not in erledigt]
    pfad.write_text(json.dumps(rest, ensure_ascii=False, indent=2))
    return erledigt


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", type=Path, default=Path("belege"))
    ap.add_argument("--samples", type=Path, required=True)
    ap.add_argument("--jetzt", action="store_true",
                    help="Wirklich verschieben (sonst nur zeigen)")
    ap.add_argument("--zurueck", action="store_true",
                    help="Aussortierte Dokumente zurueckholen")
    ap.add_argument("--nur-zahlen", action="store_true")
    args = ap.parse_args()

    if not args.input.is_dir():
        print(f"Nicht gefunden: {args.input}", file=sys.stderr)
        return 1

    if args.zurueck:
        erledigt = zurueck(args.samples, args.input)
        print(f"{len(erledigt)} Dokument(e) zurückgeholt.")
        if erledigt and not args.nur_zahlen:
            print("\nWelche — NICHT teilen, enthält Dateinamen")
            print(TRENNER)
            for e in erledigt:
                print(f"   {e.get('name')}")
        return 0

    plan = plane(args.samples, args.input)
    liegt_schon = len(list(ziel_ordner(args.input).glob("*.pdf"))) \
        if ziel_ordner(args.input).is_dir() else 0

    print("Abgehakte Belege — nur Zahlen, dieser Teil ist teilbar")
    print(TRENNER)
    print(f"   {len(plan):3}  als irrelevant abgehakt und noch im Eingangsordner")
    print(f"   {liegt_schon:3}  bereits aussortiert")

    if not plan:
        print("\n→ Nichts zu tun. Dokumente hakt man in der Review ab: "
              "Belegart auf „irrelevant\" setzen.")
        return 0

    if not args.jetzt:
        print(f"\n→ Nichts verschoben. Wirklich aussortieren:")
        print("   make aussortieren JETZT=1")
        print(f"   Ziel: {args.input}/{ORDNER}/  ·  zurück mit "
              f"make aussortieren-zurueck")
    else:
        getan = fuehre_aus(args.samples, args.input, plan)
        print(f"\n→ {len(getan)} Dokument(e) nach {args.input}/{ORDNER}/ "
              f"verschoben. Der nächste Lauf liest sie nicht mehr ein.")
        print("   Zurückholen: make aussortieren-zurueck")

    if args.nur_zahlen:
        return 0

    print("\n")
    print("Welche — NICHT teilen, enthält Dateinamen")
    print(TRENNER)
    for name in plan:
        print(f"   {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
