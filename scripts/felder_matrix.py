#!/usr/bin/env python3
"""Jedes Dokument, jedes Feld, sein Zustand — die feine Auflösung.

``make bereit`` zählt auf Dokumentebene: „49 von 53 Positionen haben einen
Betrag". Das sagt nicht, *welches Feld welchen Dokuments* fehlt, und es zeigt
nur, was da ist — nicht, was laut Feldvertrag da sein müsste. Ein
Krankenkassenbeleg soll KVG, VVG und selbst getragene Kosten liefern; kommt
nur die KVG, taucht in der groben Zählung nichts auf.

Diese Matrix geht andersherum vor: sie nimmt für jeden Belegtyp die
**Sollfelder aus** ``extractors/zielwerte.py`` und hält daneben, was der Lauf
tatsächlich hergegeben hat. Pro Feld:

    Wert     ist ein Betrag da?
    Anker    ist er im PDF verankert (Korrektheits-Constraint)?
    Person   welche Rolle, wenn das Feld personenbezogen ist?
    Herkunft regel / modell / mensch
    Zustand  bestätigt · korrigiert · gestrichen · offen · FEHLT

Ausgabe wie bei den übrigen Werkzeugen zweigeteilt: zuerst eine Bilanz je
Zielwert (nur Zahlen, teilbar), danach die Matrix mit Dateinamen (lokal).
Beträge erscheinen nie — nur ob einer da ist.

Aufruf::

    PYTHONPATH=. .venv/bin/python scripts/felder_matrix.py \\
        --input belege --samples output/latest/json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TRENNER = "─" * 100

# Zielwerte, bei denen eine Person dazugehört. Bei einem Kontosaldo ist die
# Rolle nebensächlich, bei einer Krankenkassenprämie entscheidet sie über den
# Abzug.
PERSOENLICH = ("KVG", "Grundversicherung", "VVG", "Zusatzversicherung",
               "Nettolohn", "Säule 3a", "Selbst getragene", "Kinderbetreuung")


def _ist_null(betrag: str) -> bool:
    """Ist das ein Nullbetrag? Apostroph, Komma, Leerzeichen egal.

    Auch die Schweizer Schreibweise mit Strich statt Rappen: ``0.-``, ``0.—``
    heissen null Franken. Ohne das galt eine solche Zeile als Betrag, und die
    Berichte widersprachen sich wieder.
    """
    t = betrag.replace("'", "").replace("’", "").replace(" ", "").replace(",", ".")
    for strich in ("-", "—", "–"):
        if t.endswith("." + strich):
            t = t[: -len(strich)] + "00"
    try:
        return float(t) == 0.0
    except ValueError:
        return False


def _zustand(eintrag: dict | None, r: dict | None) -> str:
    """Was ist mit diesem Feld passiert?"""
    if eintrag is None:
        return "offen" if r is not None else "FEHLT"
    if eintrag.get("entfernt"):
        return "gestrichen"
    if eintrag.get("soll") or eintrag.get("person") or eintrag.get("zielwert_neu"):
        return "korrigiert"
    if eintrag.get("bestaetigt_betrag") or eintrag.get("bestaetigt_person"):
        return "bestätigt"
    if eintrag.get("neu"):
        return "selbst erfasst"
    return "offen" if r is not None else "FEHLT"


def sammle(input_dir: Path, samples: Path) -> list[dict]:
    """Pro Dokument alle Soll- und Ist-Felder."""
    from extractors.korrekturen import finde, lade_alle
    from extractors.vollstaendigkeit import rolle_aus_person
    from extractors.zielwerte import ZIELWERTE
    from scripts.build_tax_output import (
        build_rows, ergaenze_eigene_zeilen, wende_korrekturen_an,
    )
    from scripts.dokumente_check import _stationen

    stand = _stationen(input_dir, samples)
    korr = lade_alle(samples)

    ergebnisse = samples / "_results_full.json"
    daten = json.loads(ergebnisse.read_text()) if ergebnisse.exists() else []

    # Zeilen wie in der Tabelle — inklusive Korrekturen, aber ohne die
    # gestrichenen zu entfernen: die sollen in der Matrix sichtbar bleiben.
    zeilen: list[dict] = []
    for e in daten:
        if e.get("status") == "ok":
            zeilen.extend(build_rows(e))
    wende_korrekturen_an(zeilen, samples, entfernen=False)
    ergaenze_eigene_zeilen(zeilen, samples)

    je_dokument: dict[str, list[dict]] = {}
    for r in zeilen:
        je_dokument.setdefault(r.get("pdf_name", "?"), []).append(r)

    aus: list[dict] = []
    for name in sorted(stand):
        st = stand[name]
        if not st.get("im_ordner"):
            continue
        belegtyp = st.get("belegtyp") or ""
        vorhanden = je_dokument.get(name, [])

        # Verbunden wird ueber den FELDNAMEN, nicht ueber die Beschriftung.
        #
        # Feldvertrag und Uebertragungstabelle benennen dieselben Felder
        # unterschiedlich ("Saldo 31.12." vs. "Vermögensstand 31.12."). Ein
        # Abgleich ueber die Beschriftung meldete deshalb "12 erwartet, 0 mit
        # Wert", obwohl alle zwoelf da waren. Der Feldname ist in beiden
        # Welten derselbe (260904-rmx).
        soll_felder = [(z.feld, z.bezeichnung) for z in ZIELWERTE.get(belegtyp, [])]
        soll_namen = {f for f, _ in soll_felder}
        zusaetzlich = sorted({(r.get("field_name") or "",
                               r.get("beschreibung") or "")
                              for r in vorhanden
                              if (r.get("field_name") or "") not in soll_namen})
        felder = []
        for feld, bez in soll_felder + zusaetzlich:
            r = next((x for x in vorhanden
                      if (x.get("field_name") or "") == feld), None)
            if r is None and not feld:
                r = next((x for x in vorhanden
                          if x.get("beschreibung") == bez), None)
            # Korrekturen haengen an der Beschriftung der Zeile, nicht am
            # Feldnamen — deshalb hier ueber beide Wege suchen.
            eintrag = (finde(korr, name, (r or {}).get("beschreibung") or bez)
                       or finde(korr, name, bez))
            rolle = rolle_aus_person(r.get("person")) if r else ""
            betrag_roh = str((r or {}).get("betrag") or "").strip()
            # Ein Betrag von 0.00 ist etwas anderes als ein fehlender: die
            # Zeile existiert, wird aber vom Tabellenbau verworfen. Wer beides
            # gleich zaehlt, bekommt Widersprueche zwischen den Berichten —
            # "3 mit Wert" hier, "2 stumm" dort (260904-rmx).
            null = bool(betrag_roh) and _ist_null(betrag_roh)
            felder.append({
                "zielwert": bez,
                "feld": feld,
                "soll": feld in soll_namen,
                "wert": bool(betrag_roh) and not null,
                "null": null,
                "anker": bool(r and r.get("anchor_valid")),
                "person": rolle or ("—" if any(m in bez for m in PERSOENLICH)
                                    else ""),
                "personnoetig": any(m in bez for m in PERSOENLICH),
                "herkunft": (r or {}).get("herkunft", ""),
                "ziffer": (r or {}).get("ziffer", ""),
                # Nur fuer die lokale Datei — die Terminalausgabe zeigt den
                # Betrag nie.
                "betrag_anzeige": betrag_roh,
                "zustand": _zustand(eintrag, r),
            })
        aus.append({"dokument": name, "belegtyp": belegtyp or "— keine",
                    "felder": felder})
    return aus


def _bilanz(alle: list[dict]) -> None:
    """Je Zielwert: wie oft erwartet, wie oft geliefert, wie oft geprüft."""
    from collections import Counter
    erwartet: Counter[str] = Counter()
    mit_wert: Counter[str] = Counter()
    nullbetrag: Counter[str] = Counter()
    mit_person: Counter[str] = Counter()
    geprueft: Counter[str] = Counter()

    for d in alle:
        for f in d["felder"]:
            if not f["soll"]:
                continue
            erwartet[f["zielwert"]] += 1
            if f["wert"]:
                mit_wert[f["zielwert"]] += 1
            if f.get("null"):
                nullbetrag[f["zielwert"]] += 1
            if not f["personnoetig"] or (f["person"] not in ("", "—",
                                                             "unbekannt/manuell")):
                mit_person[f["zielwert"]] += 1
            if f["zustand"] in ("bestätigt", "korrigiert", "gestrichen",
                                "selbst erfasst"):
                geprueft[f["zielwert"]] += 1

    # Erst je Belegart: wie viele Dokumente, wie viele davon stumm. Ohne das
    # widersprechen sich die Zaehlungen scheinbar — "3 Zielwerte erwartet"
    # gegen "2 stumme Dokumente derselben Belegart" ist nur aufloesbar, wenn
    # man weiss, wie viele Dokumente es je Art ueberhaupt gibt (260904-rmx).
    doks: Counter[str] = Counter()
    stumme: Counter[str] = Counter()
    for d in alle:
        doks[d["belegtyp"]] += 1
        if not any(f["wert"] for f in d["felder"]):
            stumme[d["belegtyp"]] += 1

    print("Dokumente je Belegart — nur Zahlen, dieser Teil ist teilbar")
    print(TRENNER)
    print(f"   {'Belegart':<48} {'Dokumente':>9} {'ohne Wert':>10}")
    for t in sorted(doks, key=lambda k: -doks[k]):
        print(f"   {t:<48} {doks[t]:>9} {stumme[t]:>10}")
    print()

    print("Feld-Bilanz — nur Zahlen, dieser Teil ist teilbar")
    print(TRENNER)
    print(f"   {'Zielwert':<48} {'erwartet':>9} {'mit Wert':>9} {'0.00':>6} "
          f"{'zugeordnet':>11} {'geprüft':>8}")
    for z in sorted(erwartet, key=lambda k: -erwartet[k]):
        print(f"   {z:<48} {erwartet[z]:>9} {mit_wert[z]:>9} "
              f"{nullbetrag[z]:>6} {mit_person[z]:>11} {geprueft[z]:>8}")
    print(f"\n   {'Summe':<48} {sum(erwartet.values()):>9} "
          f"{sum(mit_wert.values()):>9} {sum(nullbetrag.values()):>6} "
          f"{sum(mit_person.values()):>11} {sum(geprueft.values()):>8}")


def _matrix(alle: list[dict]) -> None:
    print("\n")
    print("Jedes Dokument, jedes Feld — NICHT teilen, enthält Dateinamen")
    print(TRENNER)
    for d in alle:
        offen = [f for f in d["felder"]
                 if f["zustand"] in ("FEHLT", "offen") or not f["wert"]]
        marke = "!" if offen else " "
        print(f"\n{marke} {d['dokument']}   [{d['belegtyp']}]")
        if not d["felder"]:
            print("     — kein Zielwert fuer diese Belegart")
            continue
        print(f"     {'Zielwert':<46} {'Wert':<5} {'Anker':<6} "
              f"{'Person':<18} {'Herkunft':<9} Zustand")
        for f in d["felder"]:
            print(f"     {f['zielwert']:<46} "
                  f"{'✓' if f['wert'] else ('0.00' if f.get('null') else '—'):<5} "
                  f"{'✓' if f['anker'] else '—':<6} "
                  f"{(f['person'] or ''):<18} "
                  f"{f['herkunft']:<9} "
                  f"{f['zustand']}{'' if f['soll'] else '  (nicht im Vertrag)'}")
    print("\n   ! = mindestens ein Feld ohne Wert oder ungeprüft")


def _schreibe_md(alle: list[dict], ziel: Path) -> int:
    """Die Feldmatrix als lesbares Dokument — mit Beträgen.

    Die Terminalausgabe zeigt nur ✓/—, weil sie geteilt werden kann. Diese
    Datei bleibt auf dem Gerät und darf deshalb zeigen, worauf es ankommt:
    welcher Betrag in welcher Zelle steht, ob er vom Modell kommt oder vom
    Menschen, und was noch niemand angeschaut hat.

    Sortiert nach Dringlichkeit: Dokumente mit offenen Zellen zuerst.
    """
    def dringlichkeit(d: dict) -> tuple:
        offen = sum(1 for f in d["felder"]
                    if f["soll"] and not f["wert"] and not f.get("null"))
        ungeprueft = sum(1 for f in d["felder"] if f["zustand"] == "offen")
        return (-offen, -ungeprueft, d["dokument"])

    zeilen = [
        "# Feldstand je Dokument",
        "",
        "Jede Zelle, die der Feldvertrag verlangt — mit ihrem Betrag, ihrer",
        "Herkunft und ihrem Zustand. Dokumente mit offenen Zellen stehen oben.",
        "",
        "| Zustand | heisst |",
        "|---|---|",
        "| **offen** | niemand hat hingeschaut — Wert kommt aus Extraktion |",
        "| bestätigt | im Original geprüft und für richtig befunden |",
        "| korrigiert | Wert oder Zuordnung von Hand geändert |",
        "| selbst erfasst | Position von Hand angelegt |",
        "| gestrichen | steht nicht in diesem Dokument |",
        "| **FEHLT** | der Vertrag verlangt das Feld, es kam nichts |",
        "",
        "Diese Datei enthält echte Beträge und gehört nicht aus dem Haus.",
        "",
    ]
    for d in sorted(alle, key=dringlichkeit):
        luecken = [f for f in d["felder"]
                   if f["soll"] and not f["wert"] and not f.get("null")]
        marke = "⚠ " if luecken else ""
        zeilen += [f"## {marke}{d['dokument']}", "",
                   f"Belegart: `{d['belegtyp']}`", ""]
        if not d["felder"]:
            zeilen += ["— kein Zielwert für diese Belegart.", ""]
            continue
        zeilen += ["| Zielwert | Ziffer | Betrag | Person | Herkunft | Zustand |",
                   "|---|---|---:|---|---|---|"]
        for f in d["felder"]:
            betrag = f.get("betrag_anzeige") or ("0.00" if f.get("null") else "—")
            hinweis = "" if f["soll"] else " ¹"
            zeilen.append(
                f"| {f['zielwert']}{hinweis} | {f.get('ziffer') or '—'} "
                f"| {betrag} | {f['person'] or '—'} | {f['herkunft'] or '—'} "
                f"| {f['zustand']} |")
        if any(not f["soll"] for f in d["felder"]):
            zeilen += ["", "¹ nicht im Feldvertrag dieser Belegart."]
        zeilen.append("")
    ziel.write_text("\n".join(zeilen) + "\n", encoding="utf-8")
    return len(alle)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", type=Path, default=Path("belege"))
    ap.add_argument("--samples", type=Path, required=True)
    ap.add_argument("--nur-zahlen", action="store_true")
    ap.add_argument("--md", type=Path, default=None,
                    help="Feldstand zusaetzlich als Markdown schreiben")
    args = ap.parse_args()

    if not (args.samples / "_results_full.json").exists():
        print(f"Kein Lauf gefunden: {args.samples}/_results_full.json",
              file=sys.stderr)
        return 2

    alle = sammle(args.input, args.samples)
    _bilanz(alle)
    if args.md:
        n = _schreibe_md(alle, args.md)
        print(f"\n   Feldstand geschrieben: {args.md}  ({n} Dokumente)")
    if not args.nur_zahlen:
        _matrix(alle)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
