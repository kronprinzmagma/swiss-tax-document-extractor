#!/usr/bin/env python3
"""Vorhandene Extraktionsergebnisse auf den aktuellen Code-Stempel setzen.

Nur nach einer Änderung, die die Extraktion inhaltlich NICHT verändert
(z. B. engere Berechnung des Stempels, 261024-jga). Sonst stünden alte Werte
neben neuen Regeln. Ausgabe: nur Anzahlen.

NICHT verwenden nach einer inhaltlichen Extraktionsänderung — z. B. nach
261025-kme (Leser, Rohtabelle, Lohnausweis über die Ziffer): dann muss neu
extrahiert werden.

Seit 261025-kme (B4g): vor dem Schreiben eine Kopie in
``_results_full-verlauf/``; gehoben werden nur Einträge mit Fingerabdruck und
Status ``ok`` oder ``out_of_scope`` (Code- und Daten-Stand, dazu Inhalt und
Einlese-Stand aus dem Word-JSON, wenn dessen Fingerabdruck gleich ist).
Fehler- und unknown-Einträge bleiben, wie sie sind — sie sollen neu laufen.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

HEBBAR = ("ok", "out_of_scope")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--samples", type=Path, required=True)
    args = ap.parse_args()
    from scripts.process_samples_full import code_stand, daten_stand
    from scripts.verlauf import kopie_in_verlauf

    # Korpus: Fingerabdruck aus dem Index in Kopien nachtragen, die ihn
    # nicht tragen (aus der Zeit vor dem Index).
    index_pfad = args.samples / "_korpus_index.json"
    if index_pfad.exists():
        for schluessel, stamm in json.loads(index_pfad.read_text(encoding="utf-8")).items():
            k = args.samples / f"{stamm}.json"
            if k.exists():
                d = json.loads(k.read_text(encoding="utf-8"))
                if d.get("pdf_fingerabdruck") != schluessel:
                    d["pdf_fingerabdruck"] = schluessel
                    k.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")
    # Word-JSON je Dokument (Fingerabdruck, Inhalt, Einlese-Stand).
    wort: dict = {}
    for k in args.samples.glob("*.json"):
        if k.name.startswith("_"):
            continue
        try:
            d = json.loads(k.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(d, dict) and "pages" in d:
            wort[d.get("pdf_name")] = d

    pfad = args.samples / "_results_full.json"
    daten = json.loads(pfad.read_text(encoding="utf-8"))
    stand = code_stand()
    d_stand = daten_stand(args.samples)
    gehoben = aktuell = 0
    uebersprungen: Counter = Counter()
    for e in daten:
        if not isinstance(e, dict):
            continue
        w = wort.get(e.get("pdf_name")) or {}
        if not e.get("pdf_fingerabdruck") and w.get("pdf_fingerabdruck"):
            e["pdf_fingerabdruck"] = w["pdf_fingerabdruck"]
        if not e.get("pdf_fingerabdruck") or e.get("status") not in HEBBAR:
            uebersprungen[str(e.get("status") or "ohne Status")] += 1
            continue
        if e.get("code_stand") == stand and e.get("daten_stand") == d_stand:
            aktuell += 1
            continue
        e["code_stand"] = stand
        e["daten_stand"] = d_stand
        if w.get("pdf_fingerabdruck") == e["pdf_fingerabdruck"]:
            for feld in ("pdf_inhalt", "einlese_stand"):
                if w.get(feld):
                    e[feld] = w[feld]
        gehoben += 1
    kopie_in_verlauf(pfad, args.samples / "_results_full-verlauf")
    pfad.write_text(json.dumps(daten, indent=2, ensure_ascii=False))
    print(f"gehoben: {gehoben}, schon aktuell: {aktuell}, "
          f"übersprungen: {sum(uebersprungen.values())}"
          + (" (" + ", ".join(f"{s} {n}" for s, n in sorted(uebersprungen.items())) + ")"
             if uebersprungen else "")
          + f" — von {len(daten)} Ergebnissen")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
