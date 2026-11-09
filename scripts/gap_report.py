#!/usr/bin/env python3
"""Privacy-taugliches Lücken-Triage-Werkzeug für eine Steueraufstellung.

Beantwortet maschinell die Frage „wo sind die Löcher in der Aufstellung und
warum" — pro Beleg und pro steuerrelevantem Feld wird genau EINE Ursache
klassifiziert, warum der Wert (nicht) automatisch übertragbar ist.

Zwei Ausgabe-Modi:

* **Aggregat** (Default) — teilbar, harte Privacy-Linie wie ``grade_run.py`` /
  ``check_aufstellung.py``: Totale-Kopfzeile (NUR Zahlen) + Tabelle
  ``belegtyp | feld | ursache | anzahl`` (NUR Zeilen mit Lücke, sortiert nach
  Anzahl). KEINE Dateinamen, KEINE Feldwerte.
* **Detail** (``--detail``) — lokaler Report: pro Quelldatei die Lücken-Felder
  mit Ursache (Dateinamen erlaubt, Feldwerte NIE).

Acht Ursachen-Kategorien (Reihenfolge der Prüfung ist load-bearing):

1. ``beleg_fehlgeschlagen`` — ``entry["status"] != "ok"`` (greift VOR allen
   Feld-Checks; alle Felder des Belegs erhalten diese Ursache).
2. ``feld_fehlt`` — Feld nicht extrahiert oder Wert null/leer/Placeholder.
3. ``manual_review_marker`` — Wert ist ein ``manual_review:*``-Marker.
4. ``person_unbestimmt`` — Personenfeld, realer Wert, aber Rolle nicht
   zuordenbar (``map_person_to_role`` == ``ROLE_UNKNOWN``).
5. ``nicht_parsebar`` — Betragsfeld, realer Wert, aber kein gültiger CH-Betrag.
6. ``kein_anker`` — realer (ggf. parsebarer) Wert, aber ``anchor_valid`` False.
7. ``text_abgeschnitten`` — ``entry["text_truncated"]`` True und Feld wäre sonst
   ok — Truncation als Ursache markiert.
8. ``ok`` — realer Wert, parsebar (falls Betrag), Anker valide, (falls Person)
   Rolle bestimmt, keine Truncation.

Der Report blockiert NIE (Exit immer 0) — er ist ein Befund, kein Gate.

PRIVACY HART: liest ausschliesslich ``<samples>/_results_full.json``; gibt im
Default-Modus weder Dateinamen noch Feldwerte aus.

Aufruf::

    PYTHONPATH=. python scripts/gap_report.py --samples output/latest/json
    PYTHONPATH=. python scripts/gap_report.py --samples evals/samples_real_2022 --detail
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from extractors.numbers import parse_swiss_amount
from extractors.steuer_zielmodell import (
    SPECS,
    get_spec,
    relevant_fields,
)
from scripts.build_tax_output import (
    ROLE_UNKNOWN,
    is_manual_review_marker,
    is_real_value,
    map_person_to_role,
)

# Substring-Heuristik auf Feldnamen (case-insensitive): markiert Betragsfelder,
# für die ein gültiger CH-Betrag parsebar sein muss. Analog
# check_aufstellung._AMOUNT_HEADER_KEYWORDS, aber auf Feldnamen statt Spaltenköpfe.
_BETRAGS_KEYWORDS = (
    "betrag", "lohn", "ertrag", "abzug", "stand", "praemie", "prämie",
    "kvg", "vvg", "einzahlung", "steuer", "zins",
)


def _ist_betragsfeld(feld: str) -> bool:
    """True wenn der Feldname auf ein parsbares Betragsfeld hindeutet."""
    if not isinstance(feld, str):
        return False
    low = feld.lower()
    return any(kw in low for kw in _BETRAGS_KEYWORDS)


def _ist_parsebar(value: str) -> bool:
    """True wenn ``value`` ein gültiger CH-Betrag ist (None/ValueError → False)."""
    try:
        return parse_swiss_amount(value) is not None
    except ValueError:
        return False


def classify_field_gap(
    entry: dict,
    feld: str,
    field_dict: dict | None,
) -> str:
    """Klassifiziert genau eine Ursache für einen Beleg×Feld-Fall.

    Reihenfolge ist load-bearing — siehe Modul-Docstring.
    """
    # 1. Beleg-Ebene: nicht-ok schlägt alle Feld-Checks.
    if entry.get("status") != "ok":
        return "beleg_fehlgeschlagen"

    value = field_dict.get("value") if field_dict else None

    # 2. Feld fehlt / leer / Placeholder (aber kein manual_review-Marker).
    if not is_real_value(value):
        if is_manual_review_marker(value):
            # 3. Expliziter Manual-Review-Marker.
            return "manual_review_marker"
        return "feld_fehlt"

    # Ab hier: value ist real (kein None/leer/Placeholder/Marker).
    belegtyp = entry.get("belegtyp", "")
    spec = get_spec(belegtyp)
    person_field = spec.person_field if spec is not None else None

    # 4. Personenfeld mit realem Wert, aber Rolle nicht zuordenbar.
    if person_field is not None and feld == person_field:
        if map_person_to_role(value) == ROLE_UNKNOWN:
            return "person_unbestimmt"

    # 5. Betragsfeld, das sich nicht als CH-Betrag parsen lässt.
    if _ist_betragsfeld(feld) and not _ist_parsebar(value):
        return "nicht_parsebar"

    # 6. Realer (ggf. parsebarer) Wert ohne validen Anker.
    if not (field_dict or {}).get("anchor_valid", False):
        return "kein_anker"

    # 7. Beleg-Text war abgeschnitten — Feld wäre sonst ok.
    if entry.get("text_truncated"):
        return "text_abgeschnitten"

    # 8. Alles in Ordnung.
    return "ok"


def collect_gaps(entries: list[dict]) -> list[dict]:
    """Baut pro Beleg×relevantem-Feld einen Datensatz.

    Liefert ALLE Felder inkl. Ursache ``ok`` (das Aggregat braucht ok-Zähler
    für die Lückenquote). Bei nicht-ok-Belegen ohne relevante Felder (unbekannter
    / out-of-scope Belegtyp) wird eine Sammel-Lücke unter ``feld == "<beleg>"``
    erzeugt, damit der Beleg nicht stillschweigend verschwindet.
    """
    records: list[dict] = []
    for entry in entries:
        pdf_name = entry.get("pdf_name", "")
        belegtyp = entry.get("belegtyp", "")
        felder = relevant_fields(belegtyp)
        # Feld-dicts nach Feldname indexieren.
        by_name: dict[str, dict] = {}
        for fd in entry.get("fields", []) or []:
            name = fd.get("feld")
            if name is not None and name not in by_name:
                by_name[name] = fd

        if not felder:
            # Kein relevantes Feld bekannt. Nur tragen, wenn der Beleg selbst
            # fehlgeschlagen ist — als Sammel-Lücke, damit er sichtbar bleibt.
            if entry.get("status") != "ok":
                records.append({
                    "pdf_name": pdf_name,
                    "belegtyp": belegtyp,
                    "feld": "<beleg>",
                    "ursache": "beleg_fehlgeschlagen",
                })
            continue

        for feld in felder:
            ursache = classify_field_gap(entry, feld, by_name.get(feld))
            records.append({
                "pdf_name": pdf_name,
                "belegtyp": belegtyp,
                "feld": feld,
                "ursache": ursache,
            })
    return records


def print_aggregate(records: list[dict]) -> None:
    """Druckt das privacy-taugliche Aggregat (NUR Zahlen + Ursachen-Zähler)."""
    distinct_belege = len({r["pdf_name"] for r in records})
    total_felder = len(records)
    ok_felder = sum(1 for r in records if r["ursache"] == "ok")
    luecken = total_felder - ok_felder
    quote = (luecken / total_felder * 100.0) if total_felder else 0.0

    print("=== Gap-Report (Aggregat) ===")
    print(
        f"Belege: {distinct_belege} | Felder geprüft: {total_felder} | "
        f"ok: {ok_felder} | Lücken: {luecken} | Lückenquote: {quote:.1f}%"
    )
    print()

    # Tabelle: NUR Zeilen mit Lücke (ursache != ok), nach Anzahl absteigend.
    counter: Counter[tuple[str, str, str]] = Counter()
    for r in records:
        if r["ursache"] != "ok":
            counter[(r["belegtyp"], r["feld"], r["ursache"])] += 1

    if not counter:
        print("Keine Lücken — alle relevanten Felder ok.")
        return

    rows = sorted(
        counter.items(),
        key=lambda kv: (-kv[1], kv[0][0], kv[0][1], kv[0][2]),
    )
    print(f"{'belegtyp':<24} {'feld':<28} {'ursache':<22} anzahl")
    print(f"{'-' * 24} {'-' * 28} {'-' * 22} ------")
    for (belegtyp, feld, ursache), anzahl in rows:
        print(f"{belegtyp:<24} {feld:<28} {ursache:<22} {anzahl}")


def print_detail(records: list[dict]) -> None:
    """Druckt pro Datei die Lücken-Felder (Dateinamen erlaubt, Werte NIE)."""
    by_file: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for r in records:
        if r["ursache"] != "ok":
            by_file[r["pdf_name"]].append((r["feld"], r["ursache"]))

    print()
    print("=== Gap-Report (Detail) ===")
    if not by_file:
        print("Keine Lücken — keine Detail-Zeilen.")
        return
    for pdf_name in sorted(by_file):
        print(f"{pdf_name}:")
        for feld, ursache in sorted(by_file[pdf_name]):
            print(f"  {feld}: {ursache}")


def _load_entries(results_path: Path) -> list[dict] | None:
    if not results_path.exists():
        print(
            f"FEHLT: {results_path} — erst process_samples_full laufen lassen.",
            file=sys.stderr,
        )
        return None
    data = json.loads(results_path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and "entries" in data:
        data = data["entries"]
    return list(data)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Privacy-taugliche Lücken-Triage: warum fehlt ein Wert in der "
            "Steueraufstellung (Aggregat + optional Detail). Exit immer 0."
        )
    )
    parser.add_argument(
        "--samples", "--input", dest="samples", type=Path,
        default=ROOT / "evals" / "samples_real_2022",
        help="Verzeichnis mit _results_full.json (default: evals/samples_real_2022)",
    )
    parser.add_argument(
        "--detail", action="store_true",
        help="zusätzlich Detail-Report pro Datei (Dateinamen, NIE Werte)",
    )
    args = parser.parse_args(argv)

    samples = args.samples if args.samples.is_absolute() else ROOT / args.samples
    results_path = samples / "_results_full.json"

    entries = _load_entries(results_path)
    if entries is None:
        # Report blockiert nie — fehlende Datei ist Hinweis, kein Fehler.
        return 0

    records = collect_gaps(entries)
    print_aggregate(records)
    if args.detail:
        print_detail(records)
    return 0


if __name__ == "__main__":
    sys.exit(main())
