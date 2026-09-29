#!/usr/bin/env python3
"""Die geprüften Werte als Datei für die Browser-Erweiterung.

Die Erweiterung liest diese Datei **lokal** — sie wird nirgends hochgeladen und
die Erweiterung baut keine Netzwerkverbindung auf. Sie enthält echte Beträge
und gehört deshalb nach ``output/`` (gitignored), wie jede andere Ausgabe mit
Werten.

Aufbau: nach Formular in der Reihenfolge des Ausfüllens, darin je Konto bzw.
Person ein Block, darin die Werte mit Ziffer und amtlicher Feldnummer. Also
genau die Gliederung der Übertragungstabelle — die Erweiterung zeigt dieselbe
Ordnung wie das Formular vor einem.

Eine Angabe je Wert ist für die Erweiterung wichtiger als alle anderen:
``gerechnet``. Für die Ziffern 4, 12, 30.1 und 34 rechnet ZHprivateTax die
Summe selbst aus den Einzelpositionen des Wertschriften- bzw.
Schuldenverzeichnisses. Wer sie zusätzlich einträgt, verdoppelt sie. Solche
Summen darf die Erweiterung nur als Kontrollzahl zeigen, nie zum Einsetzen
anbieten.

Aufruf::

    make extension-daten
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TRENNER = "─" * 70


def baue(samples: Path) -> dict:
    """Die geprüften Positionen, gegliedert wie die Formulare."""
    from extractors.schlusspruefung import betrag_oder_nichts
    from extractors.zielwerte import (SPALTEN_340, feldcode, formular_fuer,
                                      formular_sortierung, spalte_340,
                                      wird_gerechnet, ziffer_name,
                                      ziffer_sortierung)
    from scripts.build_review_html import personennamen
    from scripts.build_tax_output import konto_key

    namen = personennamen()
    # Dieselben Zeilen wie die Uebertragungstabelle, nicht nur die Ablage.
    #
    # Die Ablage fuehrt die gepruefte Grundmenge; was der Mensch danach in der
    # Oberflaeche ergaenzt, steht in der Tabelle, aber (noch) nicht dort. Wer
    # nur die Ablage exportiert, laesst die Leiste genau diese Positionen
    # nicht anbieten — der Mensch sucht sie und findet sie nicht
    # (260924-dua).
    from scripts.etax_daten import _zeilen_der_tabelle
    zeilen = _zeilen_der_tabelle(samples)

    def person_text(rolle: str) -> str:
        r = str(rolle or "").strip()
        if r == "familie":
            return "Familie"
        return namen.get(r, r)

    nach_formular: dict[str, dict[tuple, list[dict]]] = {}
    for z in zeilen:
        form = formular_fuer(str(z.get("zielwert") or "")) or "Übrige"
        dok = str(z.get("pdf_name") or "")
        schluessel = (konto_key(dok) or str(z.get("aussteller") or "") or dok,
                      str(z.get("person") or ""))
        nach_formular.setdefault(form, {}).setdefault(schluessel, []).append(z)

    formulare = []
    gesamt: dict[str, Decimal] = {}
    for form in sorted(nach_formular, key=formular_sortierung):
        bloecke = []
        for schluessel in sorted(nach_formular[form],
                                 key=lambda k: (str(k[1]), str(k[0]))):
            rohzeilen = sorted(
                nach_formular[form][schluessel],
                key=lambda r: (ziffer_sortierung(str(r.get("ziffer") or "")),
                               str(r.get("zielwert") or "")))
            werte = []
            for r in rohzeilen:
                ziffer = str(r.get("ziffer") or "")
                betrag = str(r.get("betrag") or "").strip()
                wert = betrag_oder_nichts(betrag)
                if wert is not None and ziffer:
                    gesamt[ziffer] = (gesamt.get(ziffer, Decimal(0))
                                      + Decimal(str(wert)))
                werte.append({
                    "id": r.get("ablage_id") or "",
                    "ziffer": ziffer,
                    "ziffername": ziffer_name(ziffer),
                    "feld": feldcode(ziffer, str(r.get("person") or "")),
                    "bezeichnung": str(r.get("zielwert") or ""),
                    "unterscheidung": str(r.get("unterscheidung") or ""),
                    "betrag": betrag,
                    "jahr": str(r.get("jahr") or ""),
                    # Eine Einzelposition wird IMMER eingetragen.
                    #
                    # `wird_gerechnet` gilt der Summe auf dem Hauptformular
                    # (400, 150, 250, 470), nicht der Zeile im Verzeichnis.
                    # Hier stand versehentlich dieselbe Markierung — damit
                    # haette die Leiste fuer jede Kontoposition den Knopf
                    # verweigert und behauptet, das Formular rechne sie
                    # selbst (260923-dua).
                    "gerechnet": False,
                    "spalte": spalte_340(str(r.get("zielwert") or "")),
                    "quelle": str(r.get("pdf_name") or ""),
                })
            aussteller = next((str(r.get("aussteller") or "")
                               for r in rohzeilen if r.get("aussteller")), "")
            # Die Zeile des Wertschriftenverzeichnisses: mehrere Spalten,
            # in der Reihenfolge, in der das Formular sie abfragt. Ein Konto
            # ist dort keine einzelne Eingabe.
            konto = konto_key(str(rohzeilen[0].get("pdf_name") or ""))
            nach_spalte = {w["spalte"]: w for w in werte if w.get("spalte")}
            zeile = []
            if nach_spalte:
                for schluessel_spalte, titel in SPALTEN_340:
                    if schluessel_spalte == "bezeichnung":
                        wert = " · ".join(t for t in (
                            aussteller, f"Konto {konto}" if konto else "") if t)
                        zeile.append({"spalte": schluessel_spalte,
                                      "titel": titel, "wert": wert,
                                      "art": "text"})
                        continue
                    w = nach_spalte.get(schluessel_spalte)
                    zeile.append({
                        "spalte": schluessel_spalte, "titel": titel,
                        "wert": w["betrag"] if w else "",
                        "id": w["id"] if w else "",
                        "art": "betrag",
                    })

            bloecke.append({
                "titel": " · ".join(
                    t for t in (aussteller, person_text(schluessel[1])) if t)
                or "Ohne Zuordnung",
                "aussteller": aussteller,
                "person": person_text(schluessel[1]),
                "zeile": zeile,
                "werte": werte,
            })
        formulare.append({"formular": form, "bloecke": bloecke})

    summen = [{"ziffer": z, "ziffername": ziffer_name(z),
               "betrag": f"{gesamt[z]:.2f}", "feld": feldcode(z),
               "gerechnet": wird_gerechnet(z)}
              for z in sorted(gesamt, key=ziffer_sortierung)]

    return {
        "erzeugt": datetime.now().isoformat(timespec="seconds"),
        "positionen": len(zeilen),
        "formulare": formulare,
        "summen": summen,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--samples", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    daten = baue(args.samples)
    ziel = args.out or args.samples.parent / "steuerwerte.json"
    ziel.write_text(json.dumps(daten, indent=2, ensure_ascii=False),
                    encoding="utf-8")

    print("Werte für die Erweiterung — nur Zahlen, dieser Teil ist teilbar")
    print(TRENNER)
    print(f"   {daten['positionen']:3}  Positionen")
    for f in daten["formulare"]:
        anzahl = sum(len(b["werte"]) for b in f["bloecke"])
        print(f"      {anzahl:>3}×  {f['formular']}  "
              f"({len(f['bloecke'])} Block/Blöcke)")
    gerechnet = sum(1 for s in daten["summen"] if s["gerechnet"])
    print(f"   {len(daten['summen']):3}  Ziffern, davon {gerechnet} vom "
          f"Formular selbst gerechnet")
    print(f"\n→ {ziel}")
    print("\nDiese Datei enthält echte Beträge. Sie bleibt lokal; die "
          "Erweiterung\nliest sie vom Rechner und sendet nichts.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
