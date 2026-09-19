#!/usr/bin/env python3
"""Welches Dokument ist wo hängengeblieben?

Der Kern des Projekts ist, dass nichts vergessen geht. Geprüft wurde bisher
nur, ob die *gefundenen* Werte stimmen — nicht, ob überhaupt jedes Dokument
ankommt. Ein Scan ohne Textebene, für den kein OCR verfügbar war, erzeugt
eine Warnung auf stderr und verschwindet danach lautlos: kein Word-JSON,
keine Zeile, kein Eintrag in irgendeiner Liste. Wer die Warnung im
Scrollback übersieht, hat einen Beleg weniger in der Steuererklärung und
merkt es nie.

Dieses Skript geht den Weg rückwärts und sagt für jedes PDF, wie weit es
gekommen ist:

    PDF  →  Word-JSON  →  Extraktionsergebnis  →  Zeile(n) in der Tabelle

Ausgabe in zwei Teilen. Zuerst die **Zahlen** — die sind teilbar und
enthalten keine Namen. Danach die **Liste der betroffenen Dokumente**; sie
nennt Dateinamen, und Steuerbelege heissen nach den Menschen, um die es
geht. Dieser Teil bleibt auf dem Gerät.

Aufruf::

    PYTHONPATH=. .venv/bin/python scripts/dokumente_check.py \\
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

TRENNER = "─" * 66


def _stationen(input_dir: Path, samples: Path) -> dict[str, dict]:
    """Pro PDF-Name: wie weit ist es gekommen?"""
    stand: dict[str, dict] = {}
    for pdf in sorted(input_dir.glob("*.pdf")):
        stand[pdf.name] = {"im_ordner": True, "json": False, "ergebnis": None,
                           "zeilen": 0, "belegtyp": None}

    for jp in sorted(samples.glob("*.json")):
        if jp.name.startswith("_"):
            continue
        try:
            doc = json.loads(jp.read_text())
        except (OSError, ValueError):
            continue
        # Nur Word-JSONs zaehlen. korrekturen.json liegt im selben Ordner und
        # wurde als Dokument mitgezaehlt — daher "33 eingelesen" bei 32 PDFs
        # und ein Phantom namens "korrekturen" in der Liste (260904-rmx).
        if not isinstance(doc, dict) or "pages" not in doc:
            continue
        name = doc.get("pdf_name") or jp.stem
        stand.setdefault(name, {"im_ordner": False, "json": False,
                                "ergebnis": None, "zeilen": 0,
                                "belegtyp": None})
        stand[name]["json"] = True
        stand[name]["belegtyp"] = doc.get("belegtyp")

    ergebnisse = samples / "_results_full.json"
    if ergebnisse.exists():
        from scripts.build_tax_output import build_rows
        for e in json.loads(ergebnisse.read_text()):
            name = e.get("pdf_name") or "?"
            eintrag = stand.setdefault(
                name, {"im_ordner": False, "json": False, "ergebnis": None,
                       "zeilen": 0, "belegtyp": None})
            eintrag["ergebnis"] = e.get("status") or "?"
            eintrag["belegtyp"] = e.get("belegtyp") or eintrag["belegtyp"]
            if e.get("status") == "ok":
                try:
                    eintrag["zeilen"] = len(build_rows(e))
                except Exception:
                    eintrag["zeilen"] = -1
    return stand


def _einordnen(stand: dict[str, dict]) -> dict[str, list[str]]:
    """Gruppiert nach dem Punkt, an dem es hängen blieb."""
    gruppen: dict[str, list[str]] = {
        "kein_json": [], "kein_ergebnis": [], "nicht_ok": [],
        "ohne_zeile": [], "vollstaendig": [], "verwaist": [],
    }
    for name, s in sorted(stand.items()):
        # Entscheidend ist, ob das PDF ueberhaupt (noch) im Eingangsordner
        # liegt — nicht, ob ein Zwischenschritt fehlt. Erste Fassung
        # verwechselte genau das und beschriftete beide Faelle vertauscht.
        if not s.get("im_ordner"):
            gruppen["verwaist"].append(name)
        elif not s["json"]:
            gruppen["kein_json"].append(name)
        elif s["ergebnis"] is None:
            gruppen["kein_ergebnis"].append(name)
        elif s["ergebnis"] != "ok":
            gruppen["nicht_ok"].append(name)
        elif s["zeilen"] == 0:
            gruppen["ohne_zeile"].append(name)
        else:
            gruppen["vollstaendig"].append(name)
    return gruppen


BESCHREIBUNG = {
    "kein_json": ("PDF nicht eingelesen",
                  "Meist ein Scan ohne Textebene und ohne verfuegbares OCR. "
                  "Diese Dokumente fehlen vollstaendig."),
    "kein_ergebnis": ("eingelesen, aber nicht extrahiert",
                      "Das Word-JSON existiert, im Ergebnis fehlt es. Der "
                      "Extraktionslauf wurde vermutlich abgebrochen."),
    "nicht_ok": ("Extraktion nicht erfolgreich",
                 "Belegtyp nicht erkannt oder Fehler. In der Review unter "
                 "'Nicht zugeordnete Dokumente' zu bearbeiten."),
    "ohne_zeile": ("extrahiert, aber ohne Wert",
                   "Der Belegtyp wurde erkannt, es kam kein uebertragbarer "
                   "Wert heraus. Hier fehlt am ehesten etwas."),
    "verwaist": ("Ergebnis ohne PDF",
                 "Das Dokument liegt nicht mehr im Eingangsordner."),
}


def _suchform(t: str) -> str:
    """Vergleichsform eines Dateinamens — Prozentkodierung und Trenner egal."""
    import re
    t = str(t or "").replace("%20", " ")
    t = re.sub(r"[_\-]+", " ", t)
    return re.sub(r"\s+", " ", t).strip().lower()


def _einzelnes(samples: Path, stand: dict[str, dict], muster: str) -> int:
    """Ein Dokument, ein Weg — und wo er endet.

    Die Ausgabe nennt den Dateinamen, den der Nutzer selbst eingegeben hat,
    und sonst nur Zielwert-Bezeichnungen, Konfidenz und Zaehlungen. Keine
    Betraege, keine Personennamen.
    """
    gesucht = _suchform(muster)
    treffer = [n for n in stand if gesucht in _suchform(n)]
    if not treffer:
        print(f"Kein Dokument passt zu {muster!r}.")
        print("\nMoegliche Gruende:")
        print("  · das PDF liegt nicht im Eingangsordner")
        print("  · der Dateiname lautet anders als erinnert")
        print("\nAlle Dokumente sieht man mit `make dokumente` (ohne --dokument).")
        return 1

    from scripts.build_tax_output import build_rows, drop_zero_rows

    ergebnisse = samples / "_results_full.json"
    daten = json.loads(ergebnisse.read_text()) if ergebnisse.exists() else []

    for name in treffer:
        s = stand[name]
        print(f"\n{name}")
        print(TRENNER)
        print(f"   im Eingangsordner : {'ja' if s.get('im_ordner') else 'NEIN'}")
        print(f"   eingelesen        : {'ja' if s['json'] else 'NEIN'}")
        print(f"   Belegart          : {s.get('belegtyp') or '— nicht erkannt'}")
        print(f"   Extraktion        : {s['ergebnis'] or '— kein Ergebnis'}")

        eintrag = next((e for e in daten if e.get("pdf_name") == name), None)
        if eintrag is None:
            print("\n→ Es gibt kein Extraktionsergebnis. Das Dokument taucht in "
                  "der Review nicht auf.")
            continue
        felder = eintrag.get("fields") or []
        print(f"   Felder extrahiert : {len(felder)}")
        if felder:
            print("   davon:")
            for f in felder:
                wert = f.get("value")
                zustand = "leer" if wert in (None, "") else "gefuellt"
                print(f"      · {f.get('feld','?'):<28} {zustand}")

        zeilen = build_rows(eintrag) if eintrag.get("status") == "ok" else []
        behalten, _ = drop_zero_rows(list(zeilen))
        print(f"\n   Zeilen aus dem Vertrag : {len(zeilen)}")
        print(f"   davon mit Betrag       : {len(behalten)}")
        for r in zeilen:
            marke = "  " if r in behalten else "✕ "
            print(f"      {marke}{r.get('beschreibung','?')}  "
                  f"[Ziffer {r.get('ziffer') or '—'}]")

        if not zeilen:
            print("\n→ Die Belegart liefert fuer dieses Dokument keinen "
                  "Zielwert. Es steht in der Review oben bei den nicht "
                  "zugeordneten Dokumenten — dort Belegart setzen oder "
                  "Position von Hand erfassen.")
        elif not behalten:
            print("\n→ Alle Zeilen haben Betrag null und werden verworfen. Das "
                  "Dokument steht oben bei den nicht zugeordneten.")
        else:
            print("\n→ Das Dokument hat Zeilen in der Tabelle. Findest du es in "
                  "der Review nicht, suche nach der Zielwert-Bezeichnung statt "
                  "nach dem Dateinamen.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", type=Path, default=Path("belege"),
                    help="Ordner mit den PDFs")
    ap.add_argument("--samples", type=Path, required=True,
                    help="Ordner mit den Word-JSONs und _results_full.json")
    ap.add_argument("--nur-zahlen", action="store_true",
                    help="Nur den teilbaren Teil ausgeben")
    ap.add_argument("--dokument", default=None,
                    help="Nur dieses eine Dokument (Teil des Dateinamens) — "
                         "sagt, warum es in der Review fehlt")
    args = ap.parse_args()

    if not args.input.is_dir():
        print(f"Nicht gefunden: {args.input}", file=sys.stderr)
        return 1
    if not args.samples.is_dir():
        print(f"Nicht gefunden: {args.samples}", file=sys.stderr)
        return 1

    stand = _stationen(args.input, args.samples)
    if args.dokument:
        return _einzelnes(args.samples, stand, args.dokument)
    gruppen = _einordnen(stand)

    print("Dokumente auf ihrem Weg — nur Zahlen, dieser Teil ist teilbar")
    print(TRENNER)
    print(f"   {len(list(args.input.glob('*.pdf'))):3}  PDF(s) im Eingangsordner")
    print(f"   {sum(1 for s in stand.values() if s['json']):3}  eingelesen "
          f"(Word-JSON vorhanden)")
    print(f"   {sum(1 for s in stand.values() if s['ergebnis'] is not None):3}  "
          f"im Extraktionsergebnis")
    print(f"   {len(gruppen['vollstaendig']):3}  mit mindestens einer Zeile "
          f"in der Tabelle")
    print(f"   {sum(s['zeilen'] for s in stand.values() if s['zeilen'] > 0):3}  "
          f"Zeilen insgesamt")

    problem = sum(len(gruppen[k]) for k in BESCHREIBUNG)
    if not problem:
        print("\n→ Jedes Dokument ist angekommen.")
        return 0

    print(f"\n   {problem} Dokument(e) sind unterwegs haengengeblieben:")
    for schluessel, (titel, _) in BESCHREIBUNG.items():
        n = len(gruppen[schluessel])
        if n:
            print(f"      {n:3}×  {titel}")

    if args.nur_zahlen:
        return 1

    print("\n")
    print("Welche Dokumente — NICHT teilen, enthaelt Dateinamen")
    print(TRENNER)
    for schluessel, (titel, hinweis) in BESCHREIBUNG.items():
        namen = gruppen[schluessel]
        if not namen:
            continue
        print(f"\n{titel} ({len(namen)})")
        print(f"  {hinweis}")
        for name in namen:
            typ = stand[name].get("belegtyp") or "—"
            print(f"    · {name}   [{typ}]")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
