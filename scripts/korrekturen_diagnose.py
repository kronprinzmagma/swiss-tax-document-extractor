#!/usr/bin/env python3
"""Sagt, warum eigene Korrekturen nicht in der Übersicht ankommen.

Der Rückfluss hat mehrere Stellen, an denen er scheitern kann, und von aussen
sehen alle gleich aus — die Oberfläche zeigt die alten Werte:

1. ``korrekturen.json`` existiert gar nicht (``apply_korrekturen`` lief nie
   oder fand keine ausgefüllten Zeilen).
2. Sie liegt an einem Ort, an dem der Tabellenbau sie nicht sucht.
3. Sie existiert, aber ihre Schlüssel passen zu keiner Zeile des Laufs — etwa
   weil sich ein Dokumentname geändert hat.
4. Sie passt, enthält aber gar nicht das, was man vermisst: eine
   Personenzuordnung ist kein Sollwert, und wer nur nach Sollwerten sucht,
   findet sie nicht.

Punkt 4 hat diese Diagnose selbst lange verschwiegen — sie prüfte nur die
Spalte ``soll`` und meldete „ändert nichts an der Tabelle", obwohl Person,
Aussteller, Zielwert und Ziffer unabhängig davon übernommen werden.

Privacy: die Ausgabe nennt Anzahlen und Zielwert-Bezeichnungen. Zielwerte sind
generisch („Bruttozins / Bruttoertrag"). Dokumentnamen, Personennamen und
Beträge erscheinen **nicht** — die Ausgabe ist teilbar.

Aufruf::

    PYTHONPATH=. .venv/bin/python scripts/korrekturen_diagnose.py \\
        --samples output/latest/json
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Welche Felder eines Eintrags bewirken was? Reihenfolge = Ausgabereihenfolge.
FELDER: tuple[tuple[str, str], ...] = (
    ("soll", "korrigierter Betrag"),
    ("person", "Personenzuordnung"),
    ("aussteller", "Aussteller"),
    ("zielwert_neu", "anderer Zielwert"),
    ("ziffer_neu", "andere Ziffer"),
    ("bestaetigt_betrag", "Betrag bestätigt"),
    ("bestaetigt_person", "Person bestätigt"),
    ("neu", "selbst erfasste Position"),
    ("entfernt", "gestrichen (nicht im Dokument)"),
    ("label", "notierte Beschriftung"),
)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--samples", type=Path, required=True,
                    help="Verzeichnis mit _results_full.json")
    args = ap.parse_args()

    from extractors.korrekturen import lade, lade_alle

    print("1. Wo liegt korrekturen.json?")
    kandidaten = [args.samples.parent / "korrekturen.json",
                  args.samples / "korrekturen.json"]
    for k in kandidaten:
        n = len(lade(k)) if k.exists() else 0
        marke = f"{n:3} Eintraege" if k.exists() else "      fehlt"
        print(f"   {marke}  {k}")

    # Genau so liest es auch der Tabellenbau — beide Ablagen, zusammengefuehrt.
    daten = lade_alle(args.samples)
    if not daten:
        print("\n→ Keine Korrekturdatei. Die Durchsicht wurde nie eingelesen.")
        print("  Such die ausgefuellte CSV und spiel sie ein:")
        print("    make korrekturen-neuste")
        return 1

    print(f"\n2. Inhalt: {len(daten)} Eintraege zusammengefuehrt.")
    print("   Was steht drin (ein Eintrag kann mehreres tragen):")
    for feld, beschreibung in FELDER:
        n = sum(1 for v in daten.values() if isinstance(v, dict) and v.get(feld))
        if n:
            print(f"      {n:3}×  {beschreibung}")
    leer = sum(1 for v in daten.values() if isinstance(v, dict)
               and not any(v.get(f) for f, _ in FELDER))
    if leer:
        print(f"      {leer:3}×  (nichts davon — bewirkt nichts)")

    ergebnisse = args.samples / "_results_full.json"
    if not ergebnisse.exists():
        print(f"\n→ {ergebnisse} fehlt — Lauf zuerst ausfuehren.")
        return 1

    from scripts.build_tax_output import build_rows, wende_korrekturen_an

    zeilen = []
    for e in json.loads(ergebnisse.read_text()):
        if e.get("status") == "ok":
            zeilen.extend(build_rows(e))
    schluessel_lauf = {f"{r.get('pdf_name','')}|{r.get('beschreibung','')}"
                       for r in zeilen}
    belege_lauf = {r.get("pdf_name", "") for r in zeilen}

    treffer = [k for k in daten if k in schluessel_lauf]
    daneben = [k for k in daten if k not in schluessel_lauf]
    print(f"\n3. Abgleich mit dem Lauf ({len(zeilen)} Zeilen):")
    print(f"   {len(treffer):3} Eintrag/Eintraege passen zu einer Zeile")
    print(f"   {len(daneben):3} passen zu keiner")

    if daneben:
        # Selbst erfasste Positionen haben absichtlich keine Entsprechung.
        eigene = sum(1 for k in daneben
                     if isinstance(daten.get(k), dict) and daten[k].get("neu"))
        if eigene:
            print(f"       davon {eigene} selbst erfasst — das ist richtig so, "
                  f"sie werden ergaenzt")
        # Belegart-Zuweisungen aus dem Bereich „nicht zugeordnete Dokumente"
        # haben absichtlich keine Zeile: dort steht der Belegtyp im
        # Zielwert-Feld, und das Dokument hat (noch) nichts geliefert.
        from scripts.build_review_html import BELEGTYP_WAHL
        belegarten = [k for k in daneben if k.partition("|")[2] in BELEGTYP_WAHL]
        if belegarten:
            print(f"       davon {len(belegarten)} Belegart-Zuweisung(en) zu "
                  f"Dokumenten ohne Zeile — ebenfalls in Ordnung")
        rest = [k for k in daneben
                if not (isinstance(daten.get(k), dict) and daten[k].get("neu"))
                and k.partition("|")[2] not in BELEGTYP_WAHL]
        if not rest:
            print("\n→ Alles erklaerbar: nichts liegt daneben.")
        if rest:
            beleg_ok = sum(1 for k in rest if k.partition("|")[0] in belege_lauf)
            print(f"\n   Von den uebrigen {len(rest)}:")
            print(f"      Dokument bekannt, Zielwert nicht: {beleg_ok}")
            print(f"      Dokument unbekannt:               {len(rest) - beleg_ok}")
            zielwerte = Counter(k.partition("|")[2] for k in rest)
            print("\n   Betroffene Zielwerte (keine Dokumentnamen, keine Betraege):")
            for z, n in zielwerte.most_common(12):
                print(f"      {n:3}×  {z or '(leer)'}")
            if beleg_ok == 0:
                print("\n→ Kein einziges Dokument wiedererkannt: die Dateinamen "
                      "im Lauf unterscheiden sich von denen in der "
                      "Korrekturdatei.")
            else:
                print("\n→ Dokumente passen, Zielwert-Bezeichnungen nicht. Meist, "
                      "weil eine Zeile in der Oberflaeche umbenannt wurde.")

    # Die ehrlichste Auskunft: einmal wirklich anwenden und zaehlen.
    probe = copy.deepcopy(zeilen)
    vorher = len(probe)
    angewandt = wende_korrekturen_an(probe, args.samples)
    print(f"\n4. Probelauf: {angewandt} Eintrag/Eintraege wirken sich aus, "
          f"{vorher - len(probe)} Zeile(n) fallen weg (gestrichen).")
    mit_person = sum(1 for r in probe if str(r.get("person") or "").strip()
                     not in ("", "unbekannt/manuell", "—"))
    print(f"   Zeilen mit zugeordneter Person danach: {mit_person} von {len(probe)}")
    if angewandt == 0:
        print("\n→ Nichts wirkt. Die Eintraege passen zu keiner Zeile — siehe "
              "Punkt 3.")

    _abdeckung(probe)
    return 0


def _abdeckung(zeilen: list[dict]) -> None:
    """Welcher Zielwert liegt fuer welche Rolle vor?

    Die Vollstaendigkeitspruefung sucht nach Bezeichnungen („KVG",
    „Nettolohn"). Meldet sie „fehlt", obwohl die Zeilen zugeordnet sind, dann
    heissen sie anders als erwartet — und genau das zeigt diese Matrix.

    Privacy: ausgegeben werden nur Bezeichnungen aus dem Feldvertrag und
    Rollennamen. Selbst vergebene Bezeichnungen koennten alles enthalten und
    werden deshalb zusammengefasst statt gezeigt.
    """
    from extractors.vollstaendigkeit import MUSTER
    from extractors.zielwerte import ZIELWERTE

    bekannt = {z.bezeichnung for w in ZIELWERTE.values() for z in w}
    rollen: list[str] = []
    tabelle: dict[str, Counter] = {}
    eigene = 0
    for r in zeilen:
        bez = str(r.get("beschreibung") or "")
        rolle = str(r.get("person") or "").strip() or "(keine)"
        if bez not in bekannt:
            eigene += 1
            bez = "(eigene Bezeichnung)"
        tabelle.setdefault(bez, Counter())[rolle] += 1
        if rolle not in rollen:
            rollen.append(rolle)

    print("\n5. Was liegt vor — Zielwert je Rolle:")
    rollen.sort()
    breite = max((len(b) for b in tabelle), default=10)
    kopf = " " * (breite + 2) + "  ".join(f"{r:>14}" for r in rollen)
    print("   " + kopf)
    for bez in sorted(tabelle):
        zellen = "  ".join(f"{tabelle[bez].get(r, 0):>14}" for r in rollen)
        print(f"   {bez:<{breite}}  {zellen}")
    if eigene:
        print(f"\n   {eigene} Zeile(n) tragen eine selbst vergebene Bezeichnung "
              f"(nicht angezeigt — sie koennte Namen enthalten).")

    # Wonach die Pruefung sucht, und ob es das gibt.
    print("\n   Wonach die Vollstaendigkeitspruefung sucht:")
    for schluessel, muster in MUSTER.items():
        treffer = sum(n for bez, c in tabelle.items() for n in [sum(c.values())]
                      if any(m in bez for m in muster))
        marke = "vorhanden" if treffer else "NICHTS GEFUNDEN"
        print(f"      {schluessel:<14} {'/'.join(muster):<34} {marke} ({treffer})")


if __name__ == "__main__":
    raise SystemExit(main())
