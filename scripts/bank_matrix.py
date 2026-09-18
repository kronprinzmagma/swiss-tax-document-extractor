#!/usr/bin/env python3
"""Bank-Matrix — Feld-Status der 5 Bank-Pflichtfelder pro Bankbeleg.

User-Fokus: Bankauszüge brauchen genau Institut, Kontoart, Name (Inhaber),
Schlussstand (31.12.) und Zinsen. Diese Matrix zeigt pro
``bank_zinsausweis``-Beleg den Status jedes Felds — als Mess-Latte für die
gezielte Verbesserung der Bank-Extraktion.

Privacy: Zellen enthalten AUSSCHLIESSLICH Status-Symbole/Lücken-Codes,
niemals Feldwerte. Der Output ist damit auch für produktive Läufe teilbar
(Dateinamen können lokal bleiben relevant sein — bei Bedarf weglassen).

Status-Codes:
  ✓             echter Wert mit validem Anker
  ○             echter Wert, aber ohne validen Anker
  n/a           bewusst verifizierte Feld-Abwesenheit (by design)
  feld_fehlt    Feld leer/nicht vorhanden
  mr_marker     manual_review:*-Marker statt Wert
  nicht_parsebar  Betragsfeld nicht als CH-Betrag parsebar

WICHTIG: Geprüft wird ausschliesslich Existenz, Anker-Gültigkeit und
Parsebarkeit — NICHT die inhaltliche Korrektheit des Werts. Ein ✓ bedeutet
"verankert und parsebar", nicht "fachlich richtig".

Aufruf::

    PYTHONPATH=. python scripts/bank_matrix.py --samples evals/samples_real_2022
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from extractors.numbers import parse_swiss_amount
from scripts.build_tax_output import (
    is_manual_review_marker,
    is_real_value,
)

# Die 5 Bank-Pflichtfelder (User-Vorgabe) → Feldnamen in BankZinsausweisRaw.
BANK_FIELDS: list[tuple[str, str, bool]] = [
    # (Spaltentitel, Feldname, ist_betrag)
    ("Institut", "institut", False),
    ("Kontoart", "kontotyp", False),
    ("Name", "kontoinhaber_name", False),
    ("Schlussstand", "vermoegensstand_3112", True),
    ("Zinsen", "bruttoertrag", True),
]

OK_ANCHORED = "✓"
OK_UNANCHORED = "○"
NA_BY_DESIGN = "n/a"
GAP_FEHLT = "feld_fehlt"
GAP_MARKER = "mr_marker"
GAP_UNPARSEBAR = "nicht_parsebar"


def field_status(field: dict | None, ist_betrag: bool) -> str:
    """Klassifiziert ein einzelnes Feld in einen Status-Code (nie den Wert).

    Geprüft wird ausschliesslich Existenz, Anker-Gültigkeit und Parsebarkeit —
    NICHT die inhaltliche Korrektheit des Werts.
    """
    if field is None:
        return GAP_FEHLT
    value = field.get("value")
    if value is None or value == "":
        return GAP_FEHLT
    # Bewusste, verifizierte Feld-Abwesenheit (field_exceptions): zählt als
    # erfüllt (der Beleg kann 5/5 erreichen), bekommt aber ein EIGENES Symbol
    # (n/a), damit es in der Kopfzeilen-Statistik nicht als "○ ohne Anker"
    # getarnt wird (Finding 6). Diese Prüfung MUSS vor dem
    # ist_betrag/parse_swiss_amount-Zweig stehen, sonst würfe der Parse einen
    # ValueError (nicht_parsebar).
    if value == "not_in_beleg_by_design":
        return NA_BY_DESIGN
    if is_manual_review_marker(value):
        return GAP_MARKER
    if not is_real_value(value):
        return GAP_FEHLT
    if ist_betrag:
        try:
            if parse_swiss_amount(value) is None:
                return GAP_FEHLT
        except ValueError:
            return GAP_UNPARSEBAR
    return OK_ANCHORED if field.get("anchor_valid") else OK_UNANCHORED


def build_matrix(entries: list[dict]) -> tuple[list[str], str]:
    """Erzeugt die Markdown-Zeilen und die Kopfzeilen-Statistik."""
    rows: list[str] = []
    complete = 0
    col_ok = {title: 0 for title, _, _ in BANK_FIELDS}
    # ○ (ohne Anker) und n/a (by design) separat aggregieren, damit die
    # Kopfzeile ehrlich ausweist, wie viele Felder faktisch ungeprüft sind.
    col_anchor = 0
    col_unanchored = 0
    col_na = 0
    bank = [e for e in entries if e.get("belegtyp") == "bank_zinsausweis"]
    for entry in bank:
        fmap = {f["feld"]: f for f in entry.get("fields", [])}
        statuses = []
        all_ok = True
        for title, feld, ist_betrag in BANK_FIELDS:
            st = field_status(fmap.get(feld), ist_betrag)
            statuses.append(st)
            # n/a (by design) zählt weiter als "Feld erfüllt" für 5/5.
            if st in (OK_ANCHORED, OK_UNANCHORED, NA_BY_DESIGN):
                col_ok[title] += 1
            else:
                all_ok = False
            if st == OK_ANCHORED:
                col_anchor += 1
            elif st == OK_UNANCHORED:
                col_unanchored += 1
            elif st == NA_BY_DESIGN:
                col_na += 1
        if all_ok:
            complete += 1
        rows.append(
            "| " + " | ".join([entry.get("pdf_name", "?")] + statuses) + " |"
        )
    spalten = ", ".join(f"{t}: {col_ok[t]}/{len(bank)}" for t, _, _ in BANK_FIELDS)
    kopf = (
        f"Bank-Matrix: {complete}/{len(bank)} Belege mit 5/5 Feldern — "
        f"{col_anchor} ✓ verankert, {col_unanchored} ○ ohne Anker, "
        f"{col_na} n/a by design — {spalten}"
    )
    return rows, kopf


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=Path, default=ROOT / "evals" / "samples_real_2022")
    args = parser.parse_args()

    results_path = args.samples / "_results_full.json"
    if not results_path.exists():
        print(f"FEHLER: {results_path} nicht gefunden.", file=sys.stderr)
        return 0  # Report, kein Gate
    data = json.loads(results_path.read_text(encoding="utf-8"))
    entries = data if isinstance(data, list) else data.get("entries", [])

    rows, kopf = build_matrix(entries)
    print(f"# {kopf}")
    print()
    print("| Datei | " + " | ".join(t for t, _, _ in BANK_FIELDS) + " |")
    print("| --- | " + " | ".join("---" for _ in BANK_FIELDS) + " |")
    for row in rows:
        print(row)
    return 0


if __name__ == "__main__":
    sys.exit(main())
