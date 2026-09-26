#!/usr/bin/env python3
"""Steht jeder Wert wirklich im Dokument? Und wo?

Das Kernversprechen dieses Projekts: jeder Betrag muss bis aufs Wort im
Original nachweisbar sein. Für die maschinell erkannten Werte gilt das — sie
tragen einen Anker. Für die von Hand eingetragenen galt es **nicht**: niemand
hat je geprüft, ob `4'756.50` im Beleg überhaupt vorkommt. Ein Tippfehler wäre
unentdeckt geblieben.

Dieses Skript sucht jeden Betrag der Ablage im Wort-JSON seines Dokuments —
in allen Schweizer Schreibweisen (``4756.50``, ``4'756.50``, ``4 756.50``,
``4756,50``) und zusätzlich über zwei benachbarte Wörter, weil pdfplumber
Beträge gelegentlich trennt.

Drei Ergebnisse:

* **gefunden** — Seite und Stelle stehen fest; mit ``JETZT=1`` wird der Anker
  nachgetragen, wo er fehlt.
* **nicht gefunden** — ansehen. Entweder ein Tippfehler, oder der Wert ist
  berechnet (eine Summe, die so nirgends steht).
* **mehrfach gefunden** — der Betrag kommt öfter vor; welche Stelle gemeint
  ist, kann nur der Mensch sagen.

Der Anker ist **kein gesperrtes Feld**: er beschreibt, wo ein Wert herkommt,
und ändert ihn nicht. Beträge werden hier nie angefasst.

Aufruf::

    make nachweis           # nur prüfen
    make nachweis JETZT=1   # gefundene Anker nachtragen
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TRENNER = "─" * 70


def schreibweisen(betrag: str) -> set[str]:
    """Alle Arten, wie dieser Betrag im Beleg stehen kann."""
    roh = str(betrag or "").strip().replace("'", "").replace(" ", "")
    try:
        wert = Decimal(roh)
    except (InvalidOperation, ValueError):
        return set()

    # Zuerst der Wert, wie er dasteht.
    #
    # Alles Weitere sind Umformungen auf zwei Nachkommastellen — und genau
    # daran scheiterte die Suche bei einem Betrag mit dreien: aus „44.271"
    # wurde „44.27", und das kommt im Beleg nicht vor. Die woertliche
    # Schreibweise gehoert immer dazu (260924-dua).
    woertlich = {roh, vereinheitliche(str(betrag)).strip()}

    ganz = f"{wert:.2f}"
    vor, nach = ganz.split(".")
    mit_tausender = f"{int(vor):,}".replace(",", "'")
    aus = {ganz, f"{mit_tausender}.{nach}", f"{vor}.{nach}",
           f"{mit_tausender},{nach}", f"{vor},{nach}",
           f"{mit_tausender}.{nach}".replace("'", " ")}
    # Ohne Rappen — das Hauptformular rechnet „CHF ohne Rappen".
    if nach == "00":
        aus |= {vor, mit_tausender}
        # Und die Schweizer Schreibweise fuer „null Rappen": 1'234.— oder
        # 1'234.-. Sie ist auf Bankbelegen ueblich und war der Suche unbekannt
        # (260924-dua).
        for strich in ("—", "–", "-"):
            aus |= {f"{mit_tausender}.{strich}", f"{vor}.{strich}"}
    # Eine Nachkommastelle statt zwei: „1'234.5" meint dasselbe wie
    # „1'234.50". Wer nur zwei Stellen kennt, findet den Betrag nicht, obwohl
    # er dasteht.
    if nach.endswith("0"):
        eine = nach[0]
        aus |= {f"{mit_tausender}.{eine}", f"{vor}.{eine}",
                f"{mit_tausender},{eine}", f"{vor},{eine}"}
    return {a for a in (aus | woertlich) if a}


# Alle Zeichen, die als Tausendertrennung auftreten. Der gerade Apostroph ist
# nur einer davon — Schweizer PDFs setzen meist den typografischen ’ (U+2019),
# gelegentlich ein schmales Leerzeichen. Wer nur den geraden kennt, findet
# keinen einzigen vierstelligen Betrag und meldet ihn als fehlend
# (260924-dua).
_TRENNER_VARIANTEN = "'\u2019\u02bc\u00b4\u0060\u2018 \u00a0\u202f\u2009"


def vereinheitliche(text: str) -> str:
    """Trennzeichen vereinheitlichen, damit Schreibweisen vergleichbar sind."""
    aus = str(text or "")
    for zeichen in _TRENNER_VARIANTEN:
        aus = aus.replace(zeichen, "'")
    return aus


def _saubere(text: str) -> str:
    """Randzeichen weg, die pdfplumber mitliefert (CHF, Klammern, Sternchen)."""
    # Der Gedankenstrich am Ende bleibt: „1'234.—" ist eine Zahl, kein
    # Zierrat. Ihn wegzuschneiden machte aus dem Betrag „1'234." und damit
    # etwas, das nirgends passt.
    aus = re.sub(r"^[^\d\-]+", "", vereinheitliche(text))
    return re.sub(r"[^\d\u2014\u2013\-]+$", "", aus)


def suche(worte: list[dict], gesucht: set[str]) -> list[tuple]:
    """Alle Fundstellen als (Seite, oben, links, rechts, unten).

    Gesucht wird nicht nur Wort für Wort: pdfplumber trennt Beträge
    regelmässig — „1'234" und „.55" werden zwei Wörter, „1 234.55" sogar
    drei. Wer nur einzelne Wörter vergleicht, findet solche Beträge nie und
    meldet sie als fehlend. Genau das tat die erste Fassung, obwohl ihr
    Docstring das Gegenteil versprach (260924-dua).

    Deshalb zusätzlich zwei und drei benachbarte Wörter derselben Zeile,
    zusammengezogen.
    """
    treffer = []
    for i, w in enumerate(worte):
        # Ein Wort.
        if _saubere(w["text"]) in gesucht:
            treffer.append((w["seite"], w["top"], w["x0"], w["x1"],
                            w["bottom"]))
            continue
        # Zwei oder drei benachbarte Woerter derselben Zeile.
        for laenge in (2, 3):
            teile = worte[i:i + laenge]
            if len(teile) < laenge:
                continue
            if any(t["seite"] != w["seite"] for t in teile):
                continue
            # Dieselbe Zeile: die Oberkanten duerfen kaum auseinanderliegen.
            if max(abs(t["top"] - w["top"]) for t in teile) > 2.0:
                continue
            zusammen = _saubere("".join(
                vereinheitliche(t["text"]) for t in teile))
            if zusammen in gesucht:
                treffer.append((w["seite"], w["top"], w["x0"],
                                teile[-1]["x1"], teile[-1]["bottom"]))
                break
    return treffer


_ANKER_FORM = re.compile(r"^S\.\d+\s*\([\d.]+,")


def brauchbar(anker) -> bool:
    """Trägt der Anker wirklich Seite und Stelle?"""
    return bool(_ANKER_FORM.match(str(anker or "").strip()))


def _json_zu(samples: Path, dokument: str) -> Path:
    """Das Wort-JSON zu einem Dokument — ueber `pdf_name`, nicht ueber den Namen.

    Dieselbe Aufloesung wie in `sammle_seiten`; zwei verschiedene Wege zum
    selben Ziel waeren zwei Gelegenheiten, es unterschiedlich falsch zu machen.
    """
    from scripts.build_review_html import _pdf_name_of

    for pfad in sorted(samples.glob("*.json")):
        if pfad.name.startswith("_") or pfad.name == "korrekturen.json":
            continue
        if _pdf_name_of(pfad) == dokument:
            return pfad
    return samples / f"{Path(dokument).stem}.json"


def worte_aus(pfad: Path) -> list[dict]:
    """Alle Wörter eines Dokuments, mit Seitennummer."""
    try:
        doc = json.loads(pfad.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    aus = []
    for seite in doc.get("pages", []):
        nr = int(seite.get("page_num") or 0)
        for w in seite.get("words", []):
            aus.append({"text": w.get("text", ""), "seite": nr,
                        "x0": float(w.get("x0", 0)),
                        "top": float(w.get("top", 0)),
                        "x1": float(w.get("x1", 0)),
                        "bottom": float(w.get("bottom", 0))})
    return aus


def _woanders(samples: Path, eigenes: str, gesucht: set[str],
              zwischenspeicher: dict) -> str:
    """Kommt der Betrag in einem anderen Dokument vor? Dann welches.

    Gibt den Dateinamen zurueck — der gehoert in den lokalen Teil, nie in den
    teilbaren.
    """
    for pfad in sorted(samples.glob("*.json")):
        if pfad.name.startswith("_") or pfad.name == "korrekturen.json":
            continue
        from scripts.build_review_html import _pdf_name_of
        if _pdf_name_of(pfad) == eigenes:
            continue
        if pfad.name not in zwischenspeicher:
            zwischenspeicher[pfad.name] = worte_aus(pfad)
        if suche(zwischenspeicher[pfad.name], gesucht):
            return pfad.stem
    return ""


def _naechste_abweichung(worte: list[dict], betrag: str) -> float | None:
    """Wie weit ist die nächstgelegene Zahl im Dokument entfernt, in Prozent."""
    try:
        ziel = Decimal(vereinheitliche(betrag).replace("'", ""))
    except (InvalidOperation, ValueError):
        return None
    if ziel == 0:
        return None
    beste = None
    beste_form = ""
    for w in worte:
        roh = _saubere(w["text"]).replace("'", "").replace(",", ".")
        if not roh or roh.count(".") > 1:
            continue
        try:
            wert = Decimal(roh)
        except (InvalidOperation, ValueError):
            continue
        if wert == 0:
            continue
        abweichung = abs(float((wert - ziel) / ziel)) * 100
        if beste is None or abweichung < beste:
            beste = abweichung
            # Die Form, nicht der Betrag: Ziffern werden zu 9. So laesst sich
            # eine unbekannte Schreibweise erkennen, ohne die Zahl zu nennen.
            beste_form = "".join("9" if c.isdigit() else c
                                 for c in vereinheitliche(w["text"]))
    return (beste, beste_form) if beste is not None else None


def pruefe(samples: Path, jetzt: bool = False) -> dict:
    from extractors.ablage import oeffne, pfad_fuer

    verbindung = oeffne(pfad_fuer(samples))
    zeilen = list(verbindung.execute("SELECT * FROM position"))

    zwischenspeicher: dict[str, list[dict]] = {}
    gefunden, fehlt, mehrfach, ohne_betrag = [], [], [], []
    nachgetragen = 0

    for z in zeilen:
        betrag = str(z["betrag"] or "").strip()
        if not betrag:
            ohne_betrag.append(z)
            continue
        dokument = str(z["dokument"] or "")
        if dokument not in zwischenspeicher:
            # Das Wort-JSON heisst nicht wie das PDF.
            #
            # Der Dateiname steht IM JSON (`pdf_name`), und seit den
            # sprechenden Umbenennungen stimmen die beiden ohnehin nicht mehr
            # ueberein. Ueber den Dateinamen gesucht fand die Pruefung neun
            # Dokumente nicht und meldete sie als „nicht eingelesen" — dabei
            # lagen sie da. Genau diesen Zeilen fehlte dann auch das
            # Seitenbild zum Markieren (260924-dua, vom Nutzer gemeldet).
            zwischenspeicher[dokument] = worte_aus(_json_zu(samples, dokument))
        worte = zwischenspeicher[dokument]
        if not worte:
            fehlt.append((z, "Dokument nicht eingelesen", ""))
            continue

        # Mehrfach vorzukommen ist kein Mangel.
        #
        # Ein Betrag steht oft zweimal im Beleg — in der Zeile und noch einmal
        # im Total. Fuer den Nachweis zaehlt nur: kommt er ueberhaupt vor?
        # „Stelle unklar" als eigener Befund war strenger als noetig und
        # erzeugte mehr Rauschen als Erkenntnis (260924-dua).
        stellen = suche(worte, schreibweisen(betrag))
        if not stellen:
            # Nicht im eigenen Dokument — steht er in einem anderen?
            #
            # Dann haengt die Position am falschen Beleg. Das kommt vor, wenn
            # eine Bank zwei Dateien liefert (Saldo- und Zinsbestaetigung) und
            # der Wert beim falschen gelandet ist. Diese Unterscheidung ist
            # der Unterschied zwischen „vermutlich eine Summe" und „hier
            # stimmt die Zuordnung nicht" (260924-dua).
            woanders = _woanders(samples, dokument, schreibweisen(betrag),
                                 zwischenspeicher)
            # Der Grund muss teilbar bleiben — der Dateiname reist getrennt
            # mit und erscheint nur im lokalen Teil. Die erste Fassung
            # schrieb ihn in den Grund und damit mitten in den Block, der
            # ausdruecklich als teilbar ueberschrieben ist (260924-dua).
            if woanders:
                fehlt.append((z, "steht in einem ANDEREN Dokument", woanders))
            else:
                # Warum nicht? Die Antwort steckt im Dokument selbst.
                #
                # Steht der Betrag laut Mensch klar auf Seite 1 und die Suche
                # findet ihn nicht, gibt es drei Moeglichkeiten: kein
                # Textlayer (ein Scan), ein Zahlenformat, das die Suche nicht
                # kennt, oder ein Wert, der vom Beleg abweicht. Welche es ist,
                # sagt die Zaehlung der zahlartigen Woerter (260924-dua).
                zahlwoerter = sum(1 for w in worte
                                  if any(c.isdigit() for c in w["text"]))
                if not zahlwoerter:
                    grund = ("Dokument hat keinen Textlayer — ein Scan ohne "
                             "OCR")
                else:
                    # Wie weit ist die naechste Zahl entfernt?
                    #
                    # Ein Promille daneben heisst: gerundet oder vertippt.
                    # Weit daneben heisst: der Wert ist gerechnet und steht
                    # so nirgends. Das unterscheidet einen Fehler von einer
                    # Summe — und nur die Abweichung in Prozent wird genannt,
                    # nie der Betrag.
                    ergebnis = _naechste_abweichung(worte, betrag)
                    naechste, form = ergebnis if ergebnis else (None, "")
                    if naechste is None:
                        grund = (f"Betrag steht so nicht im Dokument "
                                 f"({zahlwoerter} zahlartige Wörter)")
                    elif naechste < 0.5:
                        grund = (f"fast gefunden — nächste Zahl weicht um "
                                 f"{naechste:.3f} % ab, steht als „{form}“")
                    else:
                        grund = (f"Betrag steht so nicht im Dokument — "
                                 f"nächste Zahl {naechste:.0f} % daneben "
                                 f"(vermutlich eine Summe)")
                fehlt.append((z, grund, ""))
        else:
            gefunden.append(z)
            if len(stellen) > 1:
                mehrfach.append((z, len(stellen)))
            seite, top, x0, x1, bottom = stellen[0]
            anker = f"S.{seite} ({x0:.1f}, {top:.1f}, {x1:.1f}, {bottom:.1f})"
            # „Leer" heisst hier: leer ODER unbrauchbar.
            #
            # Die Uebernahme schrieb `S.None`, weil sie die Seite unter einem
            # anderen Schluessel suchte, als die Zeilen sie fuehren. Das Feld
            # war damit belegt und doch wertlos — und diese Pruefung hielt
            # sich fuer fertig (260924-dua).
            if jetzt and not brauchbar(z["anker"]):
                verbindung.execute(
                    "UPDATE position SET anker=? WHERE id=?", (anker, z["id"]))
                verbindung.execute(
                    "INSERT INTO protokoll (id, was, feld, nachher) "
                    "VALUES (?,?,?,?)",
                    (z["id"], "anker nachgetragen", "anker", anker))
                nachgetragen += 1
    if jetzt:
        verbindung.commit()

    return {"gesamt": len(zeilen), "gefunden": gefunden, "fehlt": fehlt,
            "mehrfach": mehrfach, "ohne_betrag": ohne_betrag,
            "nachgetragen": nachgetragen}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--samples", type=Path, required=True)
    ap.add_argument("--jetzt", action="store_true")
    args = ap.parse_args()

    b = pruefe(args.samples, jetzt=args.jetzt)

    print("Nachweis im Dokument — nur Zahlen, dieser Teil ist teilbar")
    print(TRENNER)
    print(f"   {b['gesamt']:3}  Positionen")
    print(f"   {len(b['gefunden']):3}  wörtlich im Dokument gefunden")
    print(f"   {len(b['fehlt']):3}  NICHT gefunden — das sind die zum Ansehen")
    print(f"        ({len(b['mehrfach'])} davon kommen mehrfach vor; der "
          f"Anker zeigt auf die erste Stelle)")
    if b["ohne_betrag"]:
        print(f"   {len(b['ohne_betrag']):3}  ohne Betrag")
    if args.jetzt:
        print(f"   {b['nachgetragen']:3}  Anker nachgetragen")
    else:
        print("\n→ Nur geprüft. Mit JETZT=1 werden fehlende Anker nachgetragen.")

    # Nach Grund und Zielwert — das ist teilbar und sagt, ob es ein Problem
    # der Daten oder eines der Pruefung ist.
    if b["fehlt"]:
        import collections
        print("\n   Nicht gefunden, nach Grund:")
        for grund, n in collections.Counter(
                g for _, g, _ in b["fehlt"]).most_common():
            print(f"      {n:>3}×  {grund}")
        print("\n   Nicht gefunden, nach Zielwert:")
        for ziel, n in collections.Counter(
                z["zielwert"] for z, _, _ in b["fehlt"]).most_common():
            print(f"      {n:>3}×  {ziel}")

    if b["fehlt"]:
        print("\nWelche — NICHT teilen, enthält Dateinamen")
        print(TRENNER)
        for z, grund, woanders in b["fehlt"]:
            print(f"   nicht gefunden: {z['zielwert']:<38} {grund}")
            print(f"                   angehaengt an: {z['dokument']}")
            if woanders:
                print(f"                   steht aber in:  {woanders}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
