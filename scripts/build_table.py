"""Baut die Steuer-Tabelle aus ``_results_full.json``.

Long-Format-Markdown im CLAUDE.md-Schema. **Alle Belegtyp-Metadaten
(Aussteller-/Person-/Datum-Felder, Pflicht-/Conditional-Felder, Währung)
werden aus dem maschinenlesbaren Zielmodell** ``extractors.steuer_zielmodell``
gelesen — kein doppeltes Listen-Inventory hier (Codex' Punkt 5).

Konfidenz-Heuristik:
- 🟢 (1.0) = Anker valid + Wert da
- 🟡 (0.5) = nur eines, ODER conditional-Field ohne Wert (legitim)
- 🔴 (0.0) = Pflichtfeld fehlt komplett

Coverage-Statistik unterscheidet jetzt explizit Pflicht/Conditional und
wendet Plausibilitätsregeln des Zielmodells an (Jahr in [2018,2030],
VRS≈35% etc.) — Codex' Punkt 1.
"""
from __future__ import annotations
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from extractors.steuer_zielmodell import (
    SPECS, get_spec, is_placeholder, run_plausibility,
    rule_jahr_plausibel, rule_datum_plausibel,
)

_DEFAULT_SAMPLES = ROOT / "evals" / "samples_real"
RESULTS = _DEFAULT_SAMPLES / "_results_full.json"
# DEBUG-Output: vollständige Extraktions-Sicht pro Feld, mit Konfidenz, BBox,
# Snippet, Plausibilitäts-Befunden. NICHT die Übertragungstabelle für die
# Steuererklärung — die kommt aus build_tax_output.py.
OUTPUT = _DEFAULT_SAMPLES / "steuer_extraktion_debug.md"


def _parse_samples_arg() -> None:
    """Erlaubt --samples <dir> Override."""
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--samples", type=Path, default=None)
    args, _ = parser.parse_known_args()
    if args.samples:
        global RESULTS, OUTPUT
        d = args.samples if args.samples.is_absolute() else ROOT / args.samples
        RESULTS = d / "_results_full.json"
        OUTPUT = d / "steuer_extraktion_debug.md"


def fmt_value(v: str | None) -> str:
    if v is None or v == "null" or v == "":
        return "—"
    return v.replace("|", "\\|")


def field_status(field: dict, field_name: str) -> tuple[float, str]:
    """Konfidenz + Marker pro Feld.

    Unterschiedliche Plausibilisierungen (Codex' #3 — jahr ≠ datum):
    - `jahr`: 4-stellig, [2018, 2030]
    - `periode_von` / `periode_bis`: DD.MM.YYYY-Format
    - Sonstige: nur Anker-Validity + Wert-Vorhandensein

    Placeholder-Werte (Codex' #2: "nicht angegeben", "null" etc.) gelten
    als nicht-erfüllt.
    """
    value = field.get("value")
    has_value = not is_placeholder(value)
    has_anchor = field.get("anchor_valid", False)
    if not has_value:
        if value not in (None, "", "null"):
            return (0.0, f"`0.00` ⚠ placeholder")
        return (0.0, "`0.00` ⚠")

    # Feldspezifische Plausibilität
    if field_name == "jahr":
        if not rule_jahr_plausibel({"jahr": value}):
            return (1.0, "**1.00**")
        return (0.0, "`0.00` ⚠ implausibel")
    if field_name in ("periode_von", "periode_bis"):
        if not rule_datum_plausibel({field_name: value}, field_name):
            return (1.0, "**1.00**")
        return (0.0, "`0.00` ⚠ implausibel")

    if has_anchor:
        return (1.0, "**1.00**")
    return (0.5, "~~0.50~~")


def fmt_anker(field: dict, pdf_name: str) -> str:
    page = field.get("page")
    bbox = field.get("bbox")
    if page is None or bbox is None:
        # jahr-via-Filename oder ähnlich
        if field.get("value") not in (None, "", "null"):
            return "📁 inferred"
        return "⚠️ kein Anker"
    x0, top, _, _ = bbox
    return f"S.{page} ({x0:.0f},{top:.0f})"


def truncate(s: str, n: int = 50) -> str:
    if not s:
        return ""
    s = s.replace("\n", " ").replace("|", "\\|")
    return s if len(s) <= n else s[: n - 1] + "…"


def main() -> None:
    _parse_samples_arg()
    if not RESULTS.exists():
        print(f"FEHLT: {RESULTS} — erst `python -m scripts.process_samples_full` laufen lassen.",
              file=sys.stderr)
        sys.exit(1)

    data = json.loads(RESULTS.read_text())

    by_belegtyp: dict[str, list[dict]] = defaultdict(list)
    unprocessed: list[dict] = []
    out_of_scope: list[dict] = []
    for entry in data:
        bt = entry.get("belegtyp", "unknown")
        status = entry.get("status")
        if status == "out_of_scope":
            out_of_scope.append(entry)
            continue
        if bt == "unknown" or status != "ok":
            unprocessed.append(entry)
            continue
        by_belegtyp[bt].append(entry)

    lines: list[str] = []
    lines.append("# Debug-Tabelle — Extraktion (vollständige Feld-Sicht)")
    lines.append("")
    lines.append(
        "> Dies ist die **Debug-Sicht** der Extraktion (alle Felder pro Beleg "
        "mit Konfidenz, Anker-Koordinaten, Snippet, Plausibility-Befunden). "
        "Für die **Übertragungstabelle für ZHprivateTax** siehe "
        "`steuer_uebertragung.md`."
    )
    lines.append("")
    lines.append(f"Generiert aus `_results_full.json` ({len(data)} Belege total, "
                 f"{len(data)-len(unprocessed)-len(out_of_scope)} extrahiert, "
                 f"{len(out_of_scope)} out-of-scope, {len(unprocessed)} unknown).")
    lines.append("")
    lines.append("**Spalten:** Aussteller | Person | Datum/Jahr | CHF | Datei | Feld | Wert | "
                 "Konfidenz | Anker | Snippet")
    lines.append("")
    lines.append("**Konfidenz-Marker:** **1.00** = Anker valid + Wert plausibel · "
                 "~~0.50~~ = nur eines · `0.00` ⚠ = Pflichtfeld fehlt · 📁 = aus Dateiname inferred")
    lines.append("")

    # Coverage-Statistik: Pflicht vs. Conditional getrennt, mit Plausibilitäts-Check
    lines.append("## Coverage-Statistik")
    lines.append("")
    lines.append("| Belegtyp | Belege | Pflicht-Felder | Pflicht ✓ | Cond.-Felder | Cond. ✓ | Plausibilität |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|")
    for bt in sorted(by_belegtyp):
        entries = by_belegtyp[bt]
        spec = get_spec(bt)
        if spec is None:
            continue
        mandatory = spec.mandatory_field_names()
        conditional = spec.conditional_field_names()
        m_total, m_ok = 0, 0
        c_total, c_ok = 0, 0
        plaus_pass = 0
        for e in entries:
            fmap = {f["feld"]: f for f in e.get("fields", [])}
            for fname in mandatory:
                m_total += 1
                f = fmap.get(fname)
                if f is None:
                    continue
                conf, _ = field_status(f, fname)
                if conf >= 1.0:
                    m_ok += 1
            for fname in conditional:
                c_total += 1
                f = fmap.get(fname)
                if f is None:
                    continue
                if f.get("value") not in (None, "", "null") and f.get("anchor_valid"):
                    c_ok += 1
            # Plausibilität: Belegtyp-spezifische Rules
            values = {f["feld"]: f.get("value", "") for f in e.get("fields", [])}
            if not run_plausibility(bt, values):
                plaus_pass += 1
        lines.append(
            f"| {spec.display_name} | {len(entries)} | {m_total} | {m_ok} "
            f"| {c_total} | {c_ok} | {plaus_pass}/{len(entries)} |"
        )
    lines.append("")

    # Detail-Tabellen pro Belegtyp
    for bt in sorted(by_belegtyp):
        entries = by_belegtyp[bt]
        spec = get_spec(bt)
        if spec is None:
            continue
        lines.append(f"## {spec.display_name} ({len(entries)} Beleg{'e' if len(entries)!=1 else ''}) "
                     f"— ZHprivateTax-Ziffer {spec.zhprivatetax_ziffer}")
        lines.append("")
        lines.append("| Aussteller | Person | Datum | CHF | Datei | Feld | Wert | Konf | Anker | Snippet |")
        lines.append("|---|---|---|---|---|---|---|---|---|---|")

        relevant_field_names = spec.mandatory_field_names() + spec.conditional_field_names()
        chf_marker = spec.waehrung or "—"

        for entry in entries:
            pdf = entry["pdf_name"]
            fmap = {f["feld"]: f for f in entry.get("fields", [])}

            aussteller = (
                fmt_value(fmap.get(spec.aussteller_field, {}).get("value"))
                if spec.aussteller_field else "—"
            )
            person = (
                fmt_value(fmap.get(spec.person_field, {}).get("value"))
                if spec.person_field else "—"
            )
            datum = (
                fmt_value(fmap.get(spec.datum_field, {}).get("value"))
                if spec.datum_field else "—"
            )

            shown: set[str] = set()
            for fname in relevant_field_names:
                shown.add(fname)
                f = fmap.get(fname)
                if f is None:
                    # Conditional-Felder: legitim leer → 🟡 statt 🔴
                    status_field = next((fs for fs in spec.fields if fs.name == fname), None)
                    if status_field and status_field.status == "conditional":
                        marker = "🟡 by design"
                    else:
                        marker = "`0.00` ⚠ fehlt"
                    lines.append(f"| {truncate(aussteller, 25)} | {truncate(person, 25)} | "
                                 f"{datum} | {chf_marker} | `{truncate(pdf, 35)}` | "
                                 f"{fname} | — | {marker} | ⚠️ — | — |")
                    continue
                wert = fmt_value(f.get("value"))
                _, konf = field_status(f, fname)
                anker = fmt_anker(f, pdf)
                snippet = truncate(f.get("snippet", ""), 40)
                lines.append(f"| {truncate(aussteller, 25)} | {truncate(person, 25)} | "
                             f"{datum} | {chf_marker} | `{truncate(pdf, 35)}` | "
                             f"{fname} | {wert} | {konf} | {anker} | {snippet} |")

            # Optionale + Zusatz-Felder
            skip = {spec.aussteller_field, spec.person_field, spec.datum_field} | shown
            for f in entry.get("fields", []):
                if f["feld"] in skip:
                    continue
                wert = fmt_value(f.get("value"))
                _, konf = field_status(f, f["feld"])
                anker = fmt_anker(f, pdf)
                snippet = truncate(f.get("snippet", ""), 40)
                lines.append(f"| {truncate(aussteller, 25)} | {truncate(person, 25)} | "
                             f"{datum} | {chf_marker} | `{truncate(pdf, 35)}` | "
                             f"_{f['feld']}_ | {wert} | {konf} | {anker} | {snippet} |")

            # Plausibilitäts-Befunde nach den Feldern
            values = {f["feld"]: f.get("value", "") for f in entry.get("fields", [])}
            errs = run_plausibility(bt, values)
            for err in errs:
                lines.append(f"| {truncate(aussteller, 25)} | — | {datum} | — | "
                             f"`{truncate(pdf, 35)}` | _plausibility_ | ⚠ | "
                             f"`0.00` | — | {truncate(err, 70)} |")
        lines.append("")

    if out_of_scope:
        lines.append("## Out of Scope — bewusst nicht extrahiert")
        lines.append("")
        lines.append("Belege, die das Tool versteht, für diese Familie aber **keine Extraktion benötigen** "
                     "(siehe `STEUER-ZIELMODELL.md` § Out of Scope).")
        lines.append("")
        lines.append("| Datei | Belegtyp-Hinweis | Grund |")
        lines.append("|---|---|---|")
        for entry in out_of_scope:
            pdf = entry["pdf_name"]
            bh = entry.get("belegtyp", "")
            reason = entry.get("out_of_scope_reason", "")
            lines.append(f"| `{pdf}` | {bh} | {reason} |")
        lines.append("")

    if unprocessed:
        lines.append("## Nicht-extrahiert / Unknown")
        lines.append("")
        lines.append("Belege, deren Typ das Tool nicht klassifizieren konnte. Manuelle Prüfung empfohlen.")
        lines.append("")
        lines.append("| Datei | Status | Grund |")
        lines.append("|---|---|---|")
        for entry in unprocessed:
            pdf = entry["pdf_name"]
            status = entry.get("status", "ok")
            reason = entry.get("error", "") or (
                "unknown belegtyp" if entry.get("belegtyp") == "unknown" else "")
            lines.append(f"| `{pdf}` | {status} | {reason} |")
        lines.append("")

    OUTPUT.write_text("\n".join(lines))
    print(f"Geschrieben: {OUTPUT}")
    print(f"  {sum(len(v) for v in by_belegtyp.values())} Belege in {len(by_belegtyp)} Belegtypen")
    print(f"  {len(out_of_scope)} out-of-scope")
    print(f"  {len(unprocessed)} unknown")


if __name__ == "__main__":
    main()
