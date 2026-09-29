#!/usr/bin/env python3
"""Den Stand einfrieren — eine feste Tabelle, die niemand neu berechnet.

Alles in diesem Werkzeug wird bei jedem Aufruf neu gebaut: die Extraktion
liefert Zeilen, die Korrekturen werden daraufgelegt, und der Bezug zwischen
beiden hängt an einem Schlüssel aus Dokumentname, Zielwert und Position.
Verschiebt sich einer davon — durch eine Umbenennung, eine neue Position, ein
Dokument, das aus dem Ordner wandert —, muss der Bezug nachgezogen werden.
Meistens klappt das. Wenn nicht, sucht man.

Das ist für die Durchsicht richtig: dort will man, dass ein neuer Lauf die
eigene Arbeit findet. Für das **Ergebnis** ist es falsch. Wer seine Zahlen
beisammen hat, will sie nicht jedes Mal neu herleiten lassen — er will sie
festhalten.

Dieses Skript schreibt den jetzigen Stand als schlichte Tabelle:

    output/latest/staende/stand-JJJJMMTT-HHMMSS.csv
    output/latest/staende/stand-JJJJMMTT-HHMMSS.md

Fixe Werte, eine Zeile je Position, mit Formular und Ziffer. **Nichts
überschreibt sie**, kein Lauf fasst sie an, keine Umbenennung verschiebt sie.
Geht danach irgendetwas schief, liegt die Wahrheit hier.

Aufruf::

    make einfrieren          # Stand festhalten
    make staende-liste       # welche gibt es
"""
from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TRENNER = "─" * 70
ORDNER = "staende"

SPALTEN = ("formular", "ziffer", "person", "aussteller", "beschreibung",
           "unterscheidung", "betrag", "jahr", "dokument", "herkunft")


def sammle(samples: Path) -> list[dict]:
    """Die Zeilen, wie sie in der Übertragungstabelle stehen."""
    from extractors.zielwerte import formular_fuer, formular_sortierung, ziffer_sortierung
    from scripts.build_review_html import personennamen, sammle_zeilen

    namen = personennamen()
    aus: list[dict] = []
    for r in sammle_zeilen(samples, pdf_dir=None, mit_crops=False):
        if r.get("gestrichen"):
            continue
        rolle = str(r.get("person") or "").strip()
        aus.append({
            "formular": formular_fuer(str(r.get("zielwert") or "")) or "Übrige",
            "ziffer": str(r.get("ziffer") or ""),
            "person": namen.get(rolle, rolle) if rolle != "familie" else "Familie",
            "aussteller": str(r.get("aussteller") or ""),
            "beschreibung": str(r.get("zielwert") or ""),
            "unterscheidung": str(r.get("unterscheidung") or ""),
            "betrag": str(r.get("betrag") or ""),
            "jahr": str(r.get("jahr") or ""),
            "dokument": str(r.get("beleg") or ""),
            "herkunft": str(r.get("herkunft") or "modell"),
        })
    aus.sort(key=lambda z: (formular_sortierung(z["formular"]),
                            z["aussteller"].lower(),
                            ziffer_sortierung(z["ziffer"]),
                            z["beschreibung"]))
    return aus


def _chf(text: str) -> str:
    from extractors.schlusspruefung import _chf as fmt
    from extractors.schlusspruefung import betrag_oder_nichts

    wert = betrag_oder_nichts(text)
    if wert is None:
        return text
    s = fmt(wert)
    return s if "." in s else s + ".00"


def schreibe(zeilen: list[dict], ziel: Path, marke: str) -> tuple[Path, Path]:
    """CSV zum Weiterverarbeiten, Markdown zum Lesen."""
    ziel.mkdir(parents=True, exist_ok=True)
    csv_pfad = ziel / f"stand-{marke}.csv"
    md_pfad = ziel / f"stand-{marke}.md"

    with csv_pfad.open("w", encoding="utf-8", newline="") as fh:
        schreiber = csv.DictWriter(fh, fieldnames=list(SPALTEN))
        schreiber.writeheader()
        for z in zeilen:
            schreiber.writerow({k: z.get(k, "") for k in SPALTEN})

    zeit = datetime.strptime(marke, "%Y%m%d-%H%M%S")
    md = [f"# Stand vom {zeit:%d.%m.%Y, %H:%M} Uhr",
          "",
          "Feste Werte. Diese Datei wird von keinem Lauf angefasst und von "
          "keiner Umbenennung verschoben.",
          ""]
    aktuelles_formular = None
    summe_je_ziffer: dict[str, float] = {}
    for z in zeilen:
        if z["formular"] != aktuelles_formular:
            aktuelles_formular = z["formular"]
            md += ["", f"## {aktuelles_formular}", "",
                   "| Ziffer | Person | Aussteller | Beschreibung | Betrag CHF |",
                   "|---|---|---|---|---:|"]
        bez = z["beschreibung"]
        if z["unterscheidung"]:
            bez += f" ({z['unterscheidung']})"
        md.append(f"| {z['ziffer']} | {z['person'] or '—'} | "
                  f"{z['aussteller'] or '—'} | {bez} | {_chf(z['betrag'])} |")
        from extractors.schlusspruefung import betrag_oder_nichts
        wert = betrag_oder_nichts(z["betrag"])
        if wert is not None and z["ziffer"]:
            summe_je_ziffer[z["ziffer"]] = summe_je_ziffer.get(z["ziffer"], 0) + wert

    if summe_je_ziffer:
        from extractors.zielwerte import ziffer_name, ziffer_sortierung
        md += ["", "## Summen je Ziffer", "",
               "| Ziffer | | Summe CHF |", "|---|---|---:|"]
        for ziffer in sorted(summe_je_ziffer, key=ziffer_sortierung):
            md.append(f"| {ziffer} | {ziffer_name(ziffer)} | "
                      f"{_chf(f'{summe_je_ziffer[ziffer]:.2f}')} |")
    md_pfad.write_text("\n".join(md) + "\n", encoding="utf-8")
    return csv_pfad, md_pfad


def vergleiche(ziel: Path, a: str, b: str) -> int:
    """Zwei festgehaltene Stände nebeneinander — nur Zielwerte, teilbar.

    Die Zahl der Zeilen allein sagt nicht, welche fehlt. Für die Frage „ist mir
    etwas abhandengekommen?" braucht es die Bezeichnungen; Beträge und
    Dokumentnamen bleiben draussen.
    """
    import collections

    def lies(marke: str) -> collections.Counter:
        pfad = ziel / f"stand-{marke}.csv"
        if not pfad.exists():
            raise SystemExit(f"Kein Stand {marke} in {ziel}")
        with pfad.open(encoding="utf-8") as fh:
            return collections.Counter(
                (z["formular"], z["beschreibung"]) for z in csv.DictReader(fh))

    alt, neu = lies(a), lies(b)
    print(f"Stand {a} → {b} — nur Zielwerte, teilbar")
    print(TRENNER)
    print(f"   {sum(alt.values()):3}  →  {sum(neu.values()):3}  Positionen")
    unterschiede = 0
    for k in sorted(set(alt) | set(neu)):
        if alt.get(k, 0) == neu.get(k, 0):
            continue
        unterschiede += 1
        print(f"   {k[1]:<44} {alt.get(k, 0)} → {neu.get(k, 0)}   [{k[0]}]")
    if not unterschiede:
        print("\n→ Kein Unterschied.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--samples", type=Path, required=True)
    ap.add_argument("--vergleiche", nargs=2, metavar=("ALT", "NEU"),
                    help="Zwei festgehaltene Staende vergleichen")
    ap.add_argument("--liste", action="store_true",
                    help="Nur zeigen, welche Staende festgehalten sind")
    args = ap.parse_args()

    ziel = args.samples.parent / ORDNER

    if args.vergleiche:
        return vergleiche(ziel, *args.vergleiche)

    if args.liste:
        vorhanden = sorted(ziel.glob("stand-*.csv")) if ziel.is_dir() else []
        print("Festgehaltene Staende")
        print(TRENNER)
        if not vorhanden:
            print("   keine — mit `make einfrieren` einen anlegen")
            return 0
        for p in vorhanden:
            n = sum(1 for _ in p.open(encoding="utf-8")) - 1
            print(f"   {p.stem.replace('stand-', ''):<18} {n:>3} Zeilen   "
                  f"{p.name}")
        print(f"\n   Ordner: {ziel}")
        return 0

    zeilen = sammle(args.samples)
    marke = datetime.now().strftime("%Y%m%d-%H%M%S")
    csv_pfad, md_pfad = schreibe(zeilen, ziel, marke)

    print("Stand eingefroren — nur Zahlen, dieser Teil ist teilbar")
    print(TRENNER)
    print(f"   {len(zeilen):3}  Positionen festgehalten")
    formulare: dict[str, int] = {}
    for z in zeilen:
        formulare[z["formular"]] = formulare.get(z["formular"], 0) + 1
    for form, n in formulare.items():
        print(f"      {n:>3}×  {form}")
    print(f"\n→ Geschrieben:\n   {csv_pfad}\n   {md_pfad}")
    print("\nDiese Dateien werden von keinem Lauf angefasst. Geht etwas "
          "schief,\nsteht die Wahrheit hier.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
