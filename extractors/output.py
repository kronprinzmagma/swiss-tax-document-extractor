"""Bucket-Routing + CSV-Writer + Markdown-Report.

Drei Buckets (D-D1): ``extracted`` (alle Pflicht-Anker valid),
``unverified`` (mind. ein Pflicht-Anker invalid) und ``unprocessed``
(Pipeline-Fehler vor Schema-Validierung — reason_code aus
:class:`extractors.schema.ReasonCode`).

Pro PDF EIN Bucket (RESEARCH §Pitfall 5: doppelte Buckets verfälschen
Pre-/Post-Count). Die Pre-/Post-Count-Asserts werden im Aufrufer
(``steuer_extraktor.main``) gegen ``len(input_pdfs)`` geprüft — D-D2.

CSV-Schema-Reihenfolge ist locked (siehe 01-CONTEXT.md §specifics, D-D4):
``person`` ist in Phase 1 immer leer; Phase 2 füllt sie via PER-*.
"""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field as dc_field
from pathlib import Path

from rich.console import Console
from rich.table import Table

from extractors.aussteller import AusstellerEntry

# Locked CSV-Schema (Phase 1) — Reihenfolge nicht ändern, ohne PLAN.md anzupassen.
CSV_COLUMNS = [
    "belegtyp",
    "aussteller",
    "person",
    "datum",
    "betrag",
    "währung",
    "feld",
    "wert",
    "konfidenz",
    "anker_datei",
    "anker_seite",
    "anker_bbox",
    "quelle_snippet",
]
UNPROCESSED_COLUMNS = ["belegtyp", "datei", "reason_code", "fehler_text"]


@dataclass
class FieldRow:
    """Eine Feld-Zeile (long-format: pro Beleg × Feld)."""

    feld: str
    wert_raw: str
    wert_decimal: str | None
    konfidenz: float
    anchor_valid: bool
    anchor_page: int | None
    anchor_bbox: list[float] | None
    snippet: str


@dataclass
class ProcessResult:
    """Ergebnis eines ``process_one``-Laufs für genau eine PDF.

    Genau eines: ``bucket in {"extracted", "unverified", "unprocessed"}``.
    Bei ``unprocessed`` sind ``fields`` leer und ``reason_code`` gesetzt.

    Phase-2-Erweiterung (PER-01..03): ``person`` enthält die Familienrolle
    (``mann | frau | kind1 | kind2 | gemeinsam``) oder ``None`` (kein Match
    bzw. ohne ``family.yaml`` deaktiviert). ``person_anchor_page`` zeigt
    auf die Seite des Vornamens-Tags des Match-Spans (D-B3).
    """

    bucket: str  # "extracted" | "unverified" | "unprocessed"
    pdf_path: Path
    belegtyp: str = "unknown"
    aussteller: str | None = None
    datum_iso: str | None = None
    fields: list[FieldRow] = dc_field(default_factory=list)
    reason_code: str | None = None
    error: str | None = None
    # Phase-2-Erweiterung — Plan 02-05.
    person: str | None = None
    person_anchor_page: int | None = None
    # Phase-3-Erweiterung (Plan 03-06): Werterhaltend-Heuristik-Ergebnis für
    # Liegenschaftsunterhalt. ``True``/``False`` = eindeutige Regex-Klass.,
    # ``None`` = unklar (Report-Warnung). Andere Belegtypen: immer ``None``.
    werterhaltend_hint: bool | None = None


# Belegtypen, deren Pflicht-/Optional-Geldfelder in CHF denominiert sind.
# Plan 02-05: Bank/KK/3a-Belege bekommen ebenfalls "CHF" (für die Lohn-/Bank-/
# KK-/3a-Beträge sind alle in CHF — keine Mehrwährung in v1).
_CHF_BELEGTYPEN = {
    "lohnausweis",
    "bank_zinsausweis",
    "kk_praemienbescheinigung",
    "saeule_3a",
    # Phase-3 (Plan 03-06): alle neuen Belegtypen sind CHF-denominiert.
    "wertschriftenverzeichnis",
    "spenden",
    "berufsauslagen",
    "kinderbetreuung",
    "hypothek_zinsbestaetigung",
    "liegenschaftsunterhalt",
    "krankheitskosten",
}


def _row_for_field(res: ProcessResult, fr: FieldRow) -> dict:
    """Baut eine CSV-Zeile aus ``ProcessResult`` + einer ``FieldRow``.

    Plan 02-05: ``person`` wird aus :attr:`ProcessResult.person` befüllt
    (Phase 1 hat die Spalte immer leer gelassen).
    """
    anker_bbox_json = json.dumps(fr.anchor_bbox) if fr.anchor_bbox else ""
    return {
        "belegtyp": res.belegtyp,
        "aussteller": res.aussteller or "",
        "person": res.person or "",  # PER-01..03 — Phase 2 füllt aus ProcessResult.
        "datum": res.datum_iso or "",
        "betrag": fr.wert_decimal or "",
        "währung": "CHF" if res.belegtyp in _CHF_BELEGTYPEN else "",
        "feld": fr.feld,
        "wert": fr.wert_raw,
        "konfidenz": f"{fr.konfidenz:.3f}",
        "anker_datei": res.pdf_path.name,
        "anker_seite": str(fr.anchor_page) if fr.anchor_page is not None else "",
        "anker_bbox": anker_bbox_json,
        "quelle_snippet": fr.snippet,
    }


def write_csvs(run_dir: Path, buckets: dict[str, list[ProcessResult]]) -> None:
    """Schreibt die drei Bucket-CSVs (extraction/unverified/unprocessed).

    Erstellt ``run_dir`` falls nötig und garantiert, dass alle drei Dateien
    mit Header-Zeile existieren — auch bei leeren Buckets.
    """
    run_dir.mkdir(parents=True, exist_ok=True)

    # extraction.csv — Felder mit validem Anker (Bucket "extracted").
    with (run_dir / "extraction.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        w.writeheader()
        for res in buckets.get("extracted", []):
            for fr in res.fields:
                w.writerow(_row_for_field(res, fr))

    # unverified.csv — gleiches Schema, aber Bucket "unverified".
    with (run_dir / "unverified.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        w.writeheader()
        for res in buckets.get("unverified", []):
            for fr in res.fields:
                w.writerow(_row_for_field(res, fr))

    # unprocessed.csv — kürzeres Schema mit reason_code.
    with (run_dir / "unprocessed.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=UNPROCESSED_COLUMNS)
        w.writeheader()
        for res in buckets.get("unprocessed", []):
            w.writerow(
                {
                    "belegtyp": res.belegtyp,
                    "datei": res.pdf_path.name,
                    "reason_code": res.reason_code or "",
                    "fehler_text": (res.error or "")[:500],
                }
            )


def write_report(
    run_dir: Path,
    buckets: dict[str, list[ProcessResult]],
    model_info: dict,
    family_warnings: list[str] | None = None,
    aussteller_missing: list[AusstellerEntry] | None = None,
    aussteller_load_warning: str | None = None,
) -> None:
    """Schreibt ``report.md`` mit Modell-Tag, Digest, Belegtyp-Aggregat und
    Privacy-Warnungen (Plan 02-05).

    Sektionen:

    * Modell-/Digest-Header und Bucket-Counts.
    * "Belegtyp-Aggregat" — pro Belegtyp eine Zeile mit n_pdfs,
      Person-Verteilung (mann/frau/kind1/kind2/gemeinsam/leer) und
      ø-Konfidenz über alle FieldRows der Bucket-Treffer
      (``extracted`` + ``unverified``).
    * "Privacy-Warnungen" (nur wenn ``family_warnings`` non-empty) —
      bullet-Liste der Family.warnings-Strings (PII-frei, T-02-16).
    * "Unprocessed Reasons" (nur wenn unprocessed-Bucket non-empty).
    """
    n_e = len(buckets.get("extracted", []))
    n_u = len(buckets.get("unverified", []))
    n_x = len(buckets.get("unprocessed", []))
    n_total = n_e + n_u + n_x
    digest = str(model_info.get("digest", ""))
    digest_short = digest[:12] if digest else "—"
    lines = [
        "# Run-Report",
        "",
        f"- **Modell:** `{model_info['model']}` (digest `{digest_short}`)",
        f"- **PDFs total:** {n_total}",
        f"- **Extrahiert (Anker valid):** {n_e}",
        f"- **Unverified (Anker invalid):** {n_u}",
        f"- **Unprocessed (Pipeline-Fehler):** {n_x}",
        "",
        "## Belegtyp-Aggregat",
        "",
    ]

    # Aggregation pro Belegtyp aus extracted + unverified Buckets (NICHT unprocessed).
    person_keys = ["mann", "frau", "kind1", "kind2", "gemeinsam", "leer"]
    per_typ: dict[str, dict] = {}
    for bucket_name in ("extracted", "unverified"):
        for res in buckets.get(bucket_name, []):
            agg = per_typ.setdefault(
                res.belegtyp,
                {
                    "n_pdfs": 0,
                    "persons": dict.fromkeys(person_keys, 0),
                    "konfidenz_sum": 0.0,
                    "konfidenz_count": 0,
                },
            )
            agg["n_pdfs"] += 1
            person_key = res.person if res.person else "leer"
            if person_key in agg["persons"]:
                agg["persons"][person_key] += 1
            for fr in res.fields:
                agg["konfidenz_sum"] += fr.konfidenz
                agg["konfidenz_count"] += 1

    if per_typ:
        header = (
            "| typ | n_pdfs | mann | frau | kind1 | kind2 | gemeinsam | leer | "
            "ø-konfidenz |"
        )
        sep = "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"
        lines.append(header)
        lines.append(sep)
        for typ, agg in sorted(per_typ.items()):
            avg_conf = (
                agg["konfidenz_sum"] / agg["konfidenz_count"]
                if agg["konfidenz_count"]
                else 0.0
            )
            row = "| {typ} | {n} | {mann} | {frau} | {k1} | {k2} | {gem} | {leer} | {conf:.3f} |".format(
                typ=typ,
                n=agg["n_pdfs"],
                mann=agg["persons"]["mann"],
                frau=agg["persons"]["frau"],
                k1=agg["persons"]["kind1"],
                k2=agg["persons"]["kind2"],
                gem=agg["persons"]["gemeinsam"],
                leer=agg["persons"]["leer"],
                conf=avg_conf,
            )
            lines.append(row)
    else:
        lines.append("- (keine extrahierten/unverified Belege)")

    # Sektion "## Hinweise" — Aussteller-Load-Warning UND/ODER
    # Werterhaltend-Unklarheit (Plan 03-06). Sammeln, dann gemeinsam rendern.
    hinweise: list[str] = []
    if aussteller_load_warning:
        hinweise.append(aussteller_load_warning)
    for res in buckets.get("extracted", []) + buckets.get("unverified", []):
        if (
            res.belegtyp == "liegenschaftsunterhalt"
            and res.werterhaltend_hint is None
        ):
            hinweise.append(
                f"Werterhaltend-Heuristik für {res.pdf_path.name}: "
                "unklar — manuelle Prüfung empfohlen."
            )
    if hinweise:
        lines += ["", "## Hinweise", ""]
        for h in hinweise:
            lines.append(f"- {h}")

    if aussteller_missing:
        lines += [
            "",
            "## Fehlende Vorjahres-Aussteller",
            "",
            "| Belegtyp | Aussteller | Letzte Sichtung |",
            "| --- | --- | --- |",
        ]
        for entry in aussteller_missing:
            lines.append(
                f"| {entry['belegtyp']} | {entry['aussteller_name']} | "
                f"{entry['last_seen_year']} |"
            )

    if family_warnings:
        lines += ["", "## Privacy-Warnungen", ""]
        for w in family_warnings:
            lines.append(f"- {w}")

    if n_x:
        lines += ["", "## Unprocessed Reasons", ""]
        reason_counts: dict[str, int] = {}
        for res in buckets.get("unprocessed", []):
            rc = res.reason_code or "unknown"
            reason_counts[rc] = reason_counts.get(rc, 0) + 1
        for rc, n in sorted(reason_counts.items()):
            lines.append(f"- `{rc}`: {n}")

    (run_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def print_markdown_table(
    extracted: list[ProcessResult],
    console: Console | None = None,
) -> None:
    """Druckt die extrahierten Belege als ``rich.Table`` auf die Konsole."""
    c = console or Console()
    if not extracted:
        c.print("[yellow]Keine extrahierten Belege.[/]")
        return
    t = Table(title="Extrahierte Belege")
    for col in ("belegtyp", "aussteller", "feld", "wert", "konfidenz", "anker"):
        t.add_column(col)
    for res in extracted:
        for fr in res.fields:
            anker = f"{res.pdf_path.name}:p{fr.anchor_page}"
            t.add_row(
                res.belegtyp,
                res.aussteller or "—",
                fr.feld,
                fr.wert_raw,
                f"{fr.konfidenz:.2f}",
                anker,
            )
    c.print(t)
