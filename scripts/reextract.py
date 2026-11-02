#!/usr/bin/env python3
"""Extrahiert ein einzelnes Dokument neu — optional mit vorgegebenem Belegtyp.

Wozu: erkennt der Header-Regex einen Belegtyp nicht, landet das Dokument als
``unknown_belegtyp`` in der Tabelle und müsste von Hand erfasst werden. Mit
diesem Skript sagt man dem Extraktor, was für ein Beleg es ist, und lässt ihn
den Versuch wiederholen — statt aufzugeben.

Der Rest des Laufs bleibt unangetastet: nur der Eintrag dieses einen Dokuments
in ``_results_full.json`` wird ersetzt. Danach ``build_tax_output.py`` bzw.
``build_review_html.py`` erneut laufen lassen.

Aufruf::

    PYTHONPATH=. .venv/bin/python scripts/reextract.py \
        --samples output/latest/json --pdf "Auszug.pdf" \
        --belegtyp kk_praemienbescheinigung

Ohne ``--belegtyp`` wird schlicht neu klassifiziert und extrahiert — sinnvoll,
nachdem eine Extraktionsregel ergänzt wurde.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from extractors.zielwerte import ZIELWERTE


def _nfc(name) -> str:
    """Getippter und gespeicherter Name in einer Schreibweise (Umlaute)."""
    import unicodedata
    return unicodedata.normalize("NFC", str(name or ""))


def json_zu_pdf(samples_dir: Path, pdf_name: str) -> Path | None:
    """Findet das Word-JSON, das zu einem PDF-Namen gehoert."""
    for p in sorted(samples_dir.glob("*.json")):
        if p.name.startswith("_"):
            continue
        try:
            if _nfc(json.loads(p.read_text()).get("pdf_name")) == _nfc(pdf_name):
                return p
        except (OSError, json.JSONDecodeError):
            continue
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--samples", type=Path, required=True,
                    help="Verzeichnis mit den Word-JSONs und _results_full.json")
    ap.add_argument("--pdf", required=True,
                    help="Dateiname des Dokuments, exakt wie in der Tabelle")
    ap.add_argument("--belegtyp", default=None, choices=sorted(ZIELWERTE),
                    help="Belegtyp erzwingen (sonst wird neu klassifiziert)")
    ap.add_argument("--model", default=None, help="Ollama-Modell-Override")
    args = ap.parse_args()
    # Abgeschlossenes Steuerjahr: nichts mehr hineinschreiben (261029).
    from extractors.steuerjahr import pruefe_offen
    pruefe_offen(Path(args.samples), "die Neu-Extraktion")

    ergebnisse_pfad = args.samples / "_results_full.json"
    if not ergebnisse_pfad.exists():
        print(f"Nicht gefunden: {ergebnisse_pfad}", file=sys.stderr)
        return 1

    json_pfad = json_zu_pdf(args.samples, args.pdf)
    if json_pfad is None:
        print(f"Kein Word-JSON fuer {args.pdf!r} in {args.samples}.\n"
              f"Der Name muss exakt dem Eintrag in der Tabelle entsprechen.",
              file=sys.stderr)
        return 1

    from scripts.process_samples_full import code_stand, daten_stand, process_json_sample

    print(f"Extrahiere {args.pdf} neu"
          + (f" als {args.belegtyp}" if args.belegtyp else " (Neuklassifikation)")
          + " …")
    neu = process_json_sample(json_pfad, model=args.model,
                              belegtyp_override=args.belegtyp)
    # Stempel wie im regulären Lauf (261025-hbv, B9): ohne Fingerabdruck und
    # Code-Stand extrahierte der nächste `make productive` das Dokument neu —
    # ohne die Vorgabe, und der gesetzte Typ war wieder weg.
    try:
        wort_json = json.loads(json_pfad.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        wort_json = {}
    # Dieselben Stempel wie process_samples_full (261025-kme, B4a/B4c).
    neu["pdf_fingerabdruck"] = wort_json.get("pdf_fingerabdruck")
    neu["pdf_inhalt"] = wort_json.get("pdf_inhalt")
    neu["einlese_stand"] = wort_json.get("einlese_stand")
    neu["daten_stand"] = daten_stand(args.samples)
    neu["code_stand"] = code_stand(args.model)
    if args.belegtyp:
        neu["belegtyp_override"] = args.belegtyp

    daten = json.loads(ergebnisse_pfad.read_text())
    ersetzt = False
    for i, eintrag in enumerate(daten):
        if _nfc(eintrag.get("pdf_name")) == _nfc(args.pdf):
            daten[i] = neu
            ersetzt = True
            break
    if not ersetzt:
        daten.append(neu)

    # Bestandsschutz 2025 (261025-kme): vorher eine Kopie in den Verlauf.
    from scripts.verlauf import kopie_in_verlauf
    kopie_in_verlauf(ergebnisse_pfad, ergebnisse_pfad.parent / "_results_full-verlauf")
    ergebnisse_pfad.write_text(
        json.dumps(daten, indent=2, ensure_ascii=False) + "\n")

    felder = neu.get("fields", [])
    mit_wert = sum(1 for f in felder
                   if f.get("value") and str(f["value"]) not in ("null", ""))
    verankert = sum(1 for f in felder if f.get("anchor_valid"))
    print(f"  Status:    {neu.get('status')}")
    print(f"  Belegtyp:  {neu.get('belegtyp')}")
    print(f"  Felder:    {mit_wert} mit Wert, {verankert} verankert")
    print(f"\n{'ersetzt' if ersetzt else 'ergaenzt'} in {ergebnisse_pfad}")
    print("Jetzt die Ausgabe neu bauen:  make review")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
