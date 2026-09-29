#!/usr/bin/env python3
"""Auf einen früheren Stand der Durchsicht zurückgehen.

Versionen allein nützen nichts, wenn man nicht zurück kann. Vor jedem
Schreiben landet eine Kopie von ``korrekturen.json`` in
``korrekturen-verlauf/``; dieses Skript zeigt sie und stellt eine davon
wieder her.

Zwei Eigenschaften, die es sicher machen:

* **Der aktuelle Stand wird zuerst gesichert.** Zurückrollen ist selbst eine
  Änderung und darf nichts vernichten — auch nicht das, was man gerade
  verwirft.
* **Es wird nie gelöscht, nur überschrieben**, und beide Ablagen
  (``output/latest`` und ``output/latest/json``) bekommen denselben Stand,
  damit sie sich nicht gegenseitig verdecken.

Ausgabe: Zeitpunkt, Anzahl Einträge und was sich zum aktuellen Stand
unterscheidet — nur Zahlen und Zielwert-Bezeichnungen, keine Beträge.

Aufruf::

    PYTHONPATH=. .venv/bin/python scripts/zurueckrollen.py --samples output/latest/json
    PYTHONPATH=. .venv/bin/python scripts/zurueckrollen.py --samples … --auf 20260910-112758
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TRENNER = "─" * 76

INHALT = ("soll", "person", "aussteller", "zielwert_neu", "ziffer_neu",
          "bestaetigt_betrag", "bestaetigt_person", "wert_betrag",
          "wert_person", "neu", "entfernt", "label", "kontext")


def _voll(daten: dict) -> int:
    return sum(1 for e in daten.values()
               if isinstance(e, dict) and any(e.get(f) for f in INHALT))


def _staende(samples: Path) -> list[Path]:
    """Alle Sicherungen, älteste zuerst — aus beiden Ablagen."""
    aus: list[Path] = []
    for ordner in (samples.parent, samples):
        aus += sorted((ordner / "korrekturen-verlauf").glob("korrekturen-*.json"))
    return sorted(aus, key=lambda p: p.name)


def _marke(p: Path) -> str:
    return p.stem.replace("korrekturen-", "")


def _unterschied(alt: dict, neu: dict) -> tuple[list[str], list[str], list[str]]:
    """(nur im alten, nur im neuen, in beiden aber anders) — als Zielwerte."""
    def bez(schluessel: str, daten: dict) -> str:
        e = daten.get(schluessel) or {}
        return (e.get("zielwert") if isinstance(e, dict) else None) \
            or schluessel.partition("|")[2] or "(ohne Zielwert)"

    nur_alt = [bez(k, alt) for k in alt if k not in neu]
    nur_neu = [bez(k, neu) for k in neu if k not in alt]
    anders = [bez(k, neu) for k in alt if k in neu and alt[k] != neu[k]]
    return nur_alt, nur_neu, anders


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--samples", type=Path, required=True)
    ap.add_argument("--auf", default=None,
                    help="Zeitmarke des Standes (z.B. 20260910-112758)")
    ap.add_argument("--aus", default=None,
                    help="Bei doppelter Zeitmarke: aus welchem Ordner")
    ap.add_argument("--felder", default=None,
                    help="Zeigt, WELCHE Felder sich zu diesem Stand "
                         "unterscheiden — nur Feldnamen, keine Werte")
    ap.add_argument("--trotz-review", action="store_true",
                    help="Auch zurueckrollen, wenn die Review laeuft")
    args = ap.parse_args()

    from extractors.korrekturen import lade, lade_alle, zerlege
    from scripts.apply_korrekturen import _sichere

    aktuell_pfad = args.samples / "korrekturen.json"
    # Gegen den WIRKSAMEN Stand vergleichen, nicht gegen eine der beiden
    # Ablagen.
    #
    # Hier stand `lade(a) or lade(b)` — also „die erste, die es gibt". Der
    # Vergleich und die Warnung „N Eintraege fallen weg" kannten damit nur
    # eine Ablage, geschrieben wurde aber in beide: jeder Eintrag, den nur die
    # andere hatte, verschwand, ohne je in der Warnung aufgetaucht zu sein
    # (260923-dua).
    aktuell = lade_alle(args.samples)
    staende = _staende(args.samples)

    # Feldweiser Unterschied zu einem frueheren Stand.
    #
    # „4 anders" sagt nicht, WAS anders ist. Fuer die Frage „habe ich eine
    # Bestaetigung verloren?" braucht es die Feldnamen — die sind teilbar,
    # die Werte nicht (260923-dua).
    if args.felder:
        treffer = [p for p in staende if _marke(p) == args.felder]
        if not treffer:
            print(f"Kein Stand mit der Zeitmarke {args.felder!r}.",
                  file=sys.stderr)
            return 1
        frueher = lade(treffer[-1])
        print(f"Unterschied zu {args.felder} — nur Feldnamen, teilbar")
        print(TRENNER)
        weg = [k for k in frueher if k not in aktuell]
        dazu = [k for k in aktuell if k not in frueher]
        print(f"   {len(weg):3}  Eintrag/Einträge nur im alten Stand")
        # Und WELCHE. Die blosse Zahl liess offen, ob Arbeit verloren ist
        # oder nur ein veralteter Schluessel wegfiel (260923-dua).
        for k in sorted(weg):
            _, ziel_, pos_ = zerlege(k)
            inhalt = "mit Inhalt" if any(
                frueher[k].get(f) not in (None, "", False)
                for f in ("soll", "person", "aussteller", "bestaetigt_betrag",
                          "bestaetigt_person", "wert_betrag", "wert_person",
                          "neu")) else "leer"
            print(f"        - {ziel_}"
                  f"{f' (Position {pos_})' if pos_ > 1 else ''}  · {inhalt}")
        print(f"   {len(dazu):3}  nur jetzt")
        for k in sorted(dazu):
            _, ziel_, pos_ = zerlege(k)
            print(f"        + {ziel_}"
                  f"{f' (Position {pos_})' if pos_ > 1 else ''}")
        anders = 0
        zeilen_aus: list[str] = []
        for k in sorted(set(frueher) & set(aktuell)):
            a, b = frueher[k], aktuell[k]
            if a == b:
                continue
            anders += 1
            felder = sorted(set(a) | set(b))
            verloren = [f for f in felder
                        if a.get(f) not in (None, "", False)
                        and b.get(f) in (None, "", False)]
            neu_da = [f for f in felder
                      if b.get(f) not in (None, "", False)
                      and a.get(f) in (None, "", False)]
            geaendert = [f for f in felder
                         if f in a and f in b and a[f] != b[f]
                         and f not in verloren and f not in neu_da]
            teile = []
            if verloren:
                teile.append("VERLOREN: " + ", ".join(verloren))
            if neu_da:
                teile.append("neu: " + ", ".join(neu_da))
            if geaendert:
                teile.append("geändert: " + ", ".join(geaendert))
            _, ziel, pos = zerlege(k)
            zeilen_aus.append(f"   {ziel}{f' #{pos}' if pos > 1 else ''}"
                              f"  ·  {' | '.join(teile)}")
        print(f"   {anders:3}  Eintrag/Einträge mit anderem Inhalt")
        for zeile in zeilen_aus:
            print(zeile)
        return 0

    if not args.auf:
        print("Frühere Stände der Durchsicht")
        print(TRENNER)
        print(f"   {'Zeitpunkt':<20} {'Einträge':>9} {'mit Inhalt':>11}   "
              f"Unterschied zum jetzigen Stand")
        if not staende:
            print("   keine Sicherungen vorhanden")
        for p in staende:
            d = lade(p)
            nur_alt, nur_neu, anders = _unterschied(d, aktuell)
            teile = []
            if nur_alt:
                teile.append(f"{len(nur_alt)} nur dort")
            if nur_neu:
                teile.append(f"{len(nur_neu)} nur jetzt")
            if anders:
                teile.append(f"{len(anders)} anders")
            herkunft = p.parent.parent.name or ""
            print(f"   {_marke(p):<20} {len(d):>9} {_voll(d):>11}   "
                  f"{', '.join(teile) or 'identisch'}"
                  f"{'   [' + herkunft + ']' if herkunft else ''}")
        print(f"\n   jetzt {' ':<14} {len(aktuell):>9} {_voll(aktuell):>11}")
        print("\nEinen Stand zurückholen:")
        print("   make zurueckrollen AUF=<Zeitpunkt>")
        print("\nDer jetzige Stand wird dabei zuerst gesichert — nichts geht "
              "verloren,\nauch nicht das, was man gerade verwirft.")
        return 0

    treffer = [p for p in staende if _marke(p) == args.auf]
    if not treffer:
        print(f"Kein Stand mit der Zeitmarke {args.auf!r}.", file=sys.stderr)
        print("Verfügbare:", ", ".join(_marke(p) for p in staende) or "keine",
              file=sys.stderr)
        return 1

    # Dieselbe Zeitmarke kann in beiden Verlauf-Ordnern liegen und zwei
    # verschiedene Staende bezeichnen. Vorher nahm `treffer[-1]` still einen
    # davon — bei einer Wiederherstellung ist Raten das Letzte, was man will
    # (260923-dua).
    if len(treffer) > 1 and len({p.read_bytes() for p in treffer}) > 1:
        print(f"Die Zeitmarke {args.auf!r} gibt es zweimal, mit "
              f"unterschiedlichem Inhalt:", file=sys.stderr)
        for p in treffer:
            herkunft = p.parent.parent.name or "?"
            print(f"   {len(lade(p)):3} Eintraege   aus {herkunft}/",
                  file=sys.stderr)
        print("Bitte den Ordner angeben: --aus <Ordnername>", file=sys.stderr)
        if not args.aus:
            return 1
        treffer = [p for p in treffer if p.parent.parent.name == args.aus]
        if not treffer:
            print(f"Kein Stand {args.auf!r} in {args.aus!r}.", file=sys.stderr)
            return 1

    quelle = treffer[-1]
    daten = lade(quelle)
    if not daten:
        print(f"{quelle} ist leer — nicht zurückgerollt.", file=sys.stderr)
        return 1

    # Laeuft die Review, haelt sie den alten Stand im Browser und schreibt ihn
    # beim naechsten Tastendruck zurueck — das Zurueckrollen waere lautlos
    # rueckgaengig gemacht (260923-dua).
    if not args.trotz_review:
        try:
            from scripts.review_server import review_laeuft
            if review_laeuft(args.samples):
                print("Die Review läuft gerade. Sie hält den jetzigen Stand "
                      "im Browser und würde ihn zurückschreiben.\n"
                      "Erst beenden (Ctrl-C im Review-Fenster), dann "
                      "zurückrollen.\n"
                      "Wirklich trotzdem: --trotz-review", file=sys.stderr)
                return 1
        except Exception:
            pass

    nur_alt, nur_neu, anders = _unterschied(daten, aktuell)
    print(f"Zurück auf {args.auf}: {len(daten)} Einträge, {_voll(daten)} mit Inhalt.")
    if nur_neu:
        print(f"   {len(nur_neu)} Eintrag/Einträge des jetzigen Standes fallen weg:")
        for z in sorted(set(nur_neu))[:15]:
            print(f"      · {z}")
    if anders:
        print(f"   {len(anders)} Eintrag/Einträge unterscheiden sich.")

    text = json.dumps(daten, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    for ziel in (args.samples.parent / "korrekturen.json", aktuell_pfad):
        _sichere(ziel)          # der jetzige Stand kommt zuerst in den Verlauf
        ziel.write_text(text)
        print(f"   geschrieben: {ziel}")
    print("\nDer vorherige Stand liegt jetzt selbst im Verlauf — dieser "
          "Schritt ist umkehrbar.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
