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

# Was das Lohnformular neben dem Betrag abfragt — Arbeitgeber und die Dauer
# der Anstellung. Der Lohnausweis traegt beides im Kopf, die Extraktion liest
# es auch, aber in der Leiste kam nur der Nettolohn an. Wer zwei Lohnausweise
# hat, musste dafuer die PDFs oeffnen (261024-jga, vom Nutzer gemeldet).
#
# Der Arbeitgeber ist der Aussteller der Uebertragungstabelle (in der
# Durchsicht gesetzt), nicht das Feld des Modells: das las auf einem Beleg die Adresse des
# Arbeitnehmers als Arbeitgeber — verankert und trotzdem falsch.
LOHN_ANGABEN = (
    ("periode_von", "Anstellung von"),
    ("periode_bis", "Anstellung bis"),
)


def _periode_nach_regel(samples: Path) -> dict[str, dict]:
    """``pdf_name`` → Feld E (von – bis), geometrisch aus den Wortboxen.

    Regel vor Modell: Form 11 ist standardisiert, die Daten stehen an
    fester Stelle im Kopf. Die Wortboxen liegen ohnehin neben dem Lauf.
    """
    from extractors.layout_lohnausweis import extract_lohnausweis_periode

    aus: dict[str, dict] = {}
    for pfad in samples.glob("*.json"):
        if pfad.name.startswith("_") or pfad.name.endswith(".truth.json"):
            continue
        try:
            dok = json.loads(pfad.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(dok, dict) or dok.get("belegtyp") != "lohnausweis":
            continue
        seiten = dok.get("pages") or []
        if not seiten:
            continue
        seite = seiten[0]
        woerter = [dict(w, page=seite.get("page_num", 1))
                   for w in seite.get("words") or []]
        gefunden = extract_lohnausweis_periode(
            woerter, seite.get("width"), seite.get("height"))
        name = str(dok.get("pdf_name") or "")
        aus[name] = gefunden
        aus.setdefault(Path(name).stem, gefunden)
    return aus


def _lohnfelder(samples: Path) -> dict[str, dict]:
    """``pdf_name`` → Felder jedes Lohnausweises aus dem Extraktionslauf.

    Feld E kommt aus der Regel, wo sie eindeutig ist; das Modell fuellt nur
    die Luecken.
    """
    pfad = samples / "_results_full.json"
    try:
        eintraege = json.loads(pfad.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if isinstance(eintraege, dict):
        eintraege = eintraege.get("results") or eintraege.get("entries") or []
    regel = _periode_nach_regel(samples)
    aus: dict[str, dict] = {}
    for e in eintraege:
        if not isinstance(e, dict) or e.get("belegtyp") != "lohnausweis":
            continue
        felder = {f.get("feld"): f for f in e.get("fields") or []
                  if isinstance(f, dict)}
        name = str(e.get("pdf_name") or "")
        for feld, gefunden in (regel.get(name) or {}).items():
            if gefunden:
                felder[feld] = {"value": gefunden["value"],
                                "anchor_valid": True, "herkunft": "regel"}
        aus[name] = felder
        aus.setdefault(Path(name).stem, felder)
    return aus


def lohnangaben(felder: dict | None, arbeitgeber: str = "") -> list[dict]:
    """Arbeitgeber und Anstellungsdauer eines Lohnausweises, zum Einsetzen.

    Jede Angabe traegt, woher sie kommt: ``tabelle`` (Aussteller), ``belegt``
    (woertlich im Beleg verankert) oder ``offen``. Die Daten der Anstellung
    hat niemand in der Durchsicht bestaetigt; die Leiste zeigt das an, und
    ein Wert ohne Anker wird nicht als sicher ausgegeben.
    """
    if arbeitgeber.startswith(("⚠", "manual_review")):
        arbeitgeber = ""
    aus = [{"titel": "Arbeitgeber", "wert": arbeitgeber,
            "herkunft": "tabelle" if arbeitgeber else "offen"}]
    for feld, titel in LOHN_ANGABEN:
        f = (felder or {}).get(feld) or {}
        wert = str(f.get("value") or "").strip()
        if wert.startswith("manual_review"):
            wert = ""
        aus.append({"titel": titel, "wert": wert,
                    "herkunft": ("belegt" if wert and f.get("anchor_valid")
                                 else "offen")})
    return aus


def baue(samples: Path) -> dict:
    """Die geprüften Positionen, gegliedert wie die Formulare."""
    from extractors.schlusspruefung import betrag_oder_nichts
    from extractors.zielwerte import (feldcode, formular_fuer,
                                      formular_sortierung,
                                      wird_gerechnet, ziffer_name,
                                      ziffer_sortierung)
    from scripts.build_review_html import personennamen
    from scripts.build_tax_output import konto_key

    namen = personennamen()
    lohn = _lohnfelder(samples)
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

    from extractors.eintraege import (anzeige, eingabe, iban_je_dokument,
                                      konten, schulden)

    # Summen je Ziffer aus allen Zeilen — auch denen, die unten zu
    # Eintraegen gebuendelt werden.
    gesamt: dict[str, Decimal] = {}
    for z in zeilen:
        ziffer = str(z.get("ziffer") or "")
        wert = betrag_oder_nichts(str(z.get("betrag") or "").strip())
        if wert is not None and ziffer:
            gesamt[ziffer] = gesamt.get(ziffer, Decimal(0)) + Decimal(str(wert))

    # Wertschriften und Schulden: je Eintrag der Online-Maske ein Block
    # (Bank + Kennung bzw. Glaeubiger), Felder in Maskenreihenfolge —
    # `FELDLISTE-ZHPRIVATETAX-2025.md` (261024-jga).
    def eintrag_bloecke(eintraege) -> list[dict]:
        return [{"titel": e.titel, "person": person_text(e.person),
                 "maske": e.maske, "dokumente": e.dokumente,
                 "felder": [dict(vars(f), wert=person_text(f.wert))
                            if f.titel.startswith("Zugehörigkeit")
                            else vars(f) for f in e.felder],
                 "werte": []}
                for e in eintraege]

    gebuendelt = {
        "Wertschriftenverzeichnis (400)": lambda: eintrag_bloecke(konten(
            zeilen, iban_je_dokument(samples), konto_key)),
        "Schuldenverzeichnis": lambda: eintrag_bloecke(schulden(zeilen)),
    }

    formulare = []
    for form in sorted(nach_formular, key=formular_sortierung):
        if form in gebuendelt:
            formulare.append({"formular": form, "bloecke": gebuendelt[form]()})
            continue
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
                roh = str(r.get("betrag") or "").strip()
                wert = betrag_oder_nichts(roh)
                # Gelesen, nicht durchgereicht: `21.083` ist 21'083.
                betrag = anzeige(Decimal(str(wert))) if wert is not None else ""
                werte.append({
                    "id": r.get("ablage_id") or "",
                    "ziffer": ziffer,
                    "ziffername": ziffer_name(ziffer),
                    "feld": feldcode(ziffer, str(r.get("person") or "")),
                    "bezeichnung": str(r.get("zielwert") or ""),
                    "unterscheidung": str(r.get("unterscheidung") or ""),
                    "betrag": betrag,
                    "einsetzen": (eingabe(Decimal(str(wert)))
                                  if wert is not None else ""),
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
                    "quelle": str(r.get("pdf_name") or ""),
                })
            aussteller = next((str(r.get("aussteller") or "")
                               for r in rohzeilen if r.get("aussteller")), "")
            angaben = []
            if form == "Lohnausweis (100)":
                for dok in dict.fromkeys(str(r.get("pdf_name") or "")
                                         for r in rohzeilen):
                    angaben.append({
                        "quelle": dok,
                        "werte": lohnangaben(lohn.get(dok)
                                             or lohn.get(Path(dok).stem),
                                             aussteller),
                    })

            bloecke.append({
                "titel": " · ".join(
                    t for t in (aussteller, person_text(schluessel[1])) if t)
                or "Ohne Zuordnung",
                "aussteller": aussteller,
                "person": person_text(schluessel[1]),
                "angaben": angaben,
                "werte": werte,
            })
        formulare.append({"formular": form, "bloecke": bloecke})

    summen = [{"ziffer": z, "ziffername": ziffer_name(z),
               "betrag": anzeige(gesamt[z]), "einsetzen": eingabe(gesamt[z]),
               "feld": feldcode(z),
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
        if any(b.get("felder") for b in f["bloecke"]):
            luecken = sum(1 for b in f["bloecke"] for x in b["felder"]
                          if x["herkunft"] in ("fehlt", "widerspruch"))
            print(f"      {len(f['bloecke']):>3}×  {f['formular']}  "
                  f"(Einträge; {luecken} fehlende/widersprüchliche Felder)")
            continue
        anzahl = sum(len(b["werte"]) for b in f["bloecke"])
        print(f"      {anzahl:>3}×  {f['formular']}  "
              f"({len(f['bloecke'])} Block/Blöcke)")
    lohn = [w for f in daten["formulare"] for b in f["bloecke"]
            for a in b.get("angaben") or [] for w in a["werte"]]
    if lohn:
        print(f"   {sum(1 for w in lohn if w['wert']):3}  von {len(lohn)} "
              f"Lohnangaben (Arbeitgeber, Anstellung von/bis) gefunden, "
              f"{sum(1 for w in lohn if w['herkunft'] == 'offen' and w['wert'])}"
              f" davon ohne Nachweis")
    gerechnet = sum(1 for s in daten["summen"] if s["gerechnet"])
    print(f"   {len(daten['summen']):3}  Ziffern, davon {gerechnet} vom "
          f"Formular selbst gerechnet")
    print(f"\n→ {ziel}")
    print("\nDiese Datei enthält echte Beträge. Sie bleibt lokal; die "
          "Erweiterung\nliest sie vom Rechner und sendet nichts.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
