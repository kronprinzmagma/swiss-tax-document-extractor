"""Fehlerklassen-Matrix für alle Samples — reine Diagnose, keine Fixes.

Pro PDF eine Zeile mit:
- Klassifikation (Belegtyp, Pipeline-Status)
- OCR/Textqualität (cid-Token-Anteil, Wortdichte)
- Anonymisierungsintegrität (Jahres-Token erhalten, Identitäten halten)
- Fehlende Pflichtfelder (Zielmodell-Liste)
- Anchor-Status (extrahiert / mit Anker)
- Plausibilität (welche Regeln greifen, welche failen)

Output: `evals/samples_real_2022/_diagnose_matrix.md`.

Bewusst KEINE Werte im Output — nur Metadaten, Counts, Flags. Privacy.
"""
from __future__ import annotations
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from extractors.steuer_zielmodell import (
    get_spec, run_plausibility, is_placeholder, rule_jahr_plausibel,
)

CID_RE = re.compile(r"\(cid:\d+\)")
YEAR_TOKEN_RE = re.compile(r"^20\d{2}$")
AMOUNT_RE = re.compile(r"^[\d'.,]+$")


def _parse_amount(s: str) -> float | None:
    try:
        return float(s.replace("'", "").replace(",", "."))
    except (ValueError, TypeError, AttributeError):
        return None


def analyze_sample(json_path: Path, result_entry: dict) -> dict:
    """Erzeugt Diagnose-Zeile pro Sample."""
    doc = json.loads(json_path.read_text())
    all_words = [w for p in doc["pages"] for w in p["words"]]

    # 1. OCR/Textqualität
    total_words = len(all_words)
    cid_tokens = sum(1 for w in all_words if CID_RE.search(w["text"]))
    n_pages = len(doc["pages"])
    word_density = total_words / max(n_pages, 1)

    # 2. Anonymisierungsintegrität: Jahres-Tokens erhalten?
    year_tokens = sum(1 for w in all_words if YEAR_TOKEN_RE.match(w["text"]))
    # Identity-Heuristik: bei Lohnausweis Pos11=Pos8-Pos9-Pos10?
    fmap = {f["feld"]: f for f in result_entry.get("fields", [])}
    identity_check = "n/a"
    bt = result_entry.get("belegtyp", "")
    if bt == "lohnausweis":
        p8 = _parse_amount(fmap.get("bruttolohn_pos8", {}).get("value", ""))
        p9 = _parse_amount(fmap.get("ahv_alv_nbu_abzug_pos9", {}).get("value", ""))
        p10 = _parse_amount(fmap.get("bvg_abzug_pos10a", {}).get("value", "")) or 0
        p11 = _parse_amount(fmap.get("nettolohn_pos11", {}).get("value", ""))
        if None not in (p8, p9, p11):
            diff = abs(p11 - (p8 - p9 - p10))
            identity_check = "✓" if diff <= 5 else f"Δ={diff:.0f}"
    elif bt == "bank_zinsausweis":
        bru = _parse_amount(fmap.get("bruttoertrag", {}).get("value", ""))
        vrs = _parse_amount(fmap.get("verrechnungssteuer", {}).get("value", ""))
        if bru is not None and vrs is not None and bru > 200:
            expected = bru * 0.35
            diff = abs(vrs - expected)
            identity_check = "✓" if diff <= 1 else f"Δ={diff:.0f}"
        elif bru is not None and bru <= 200:
            identity_check = "n/a (≤200)"

    # 3. Zielmodell-Pflichtfelder
    spec = get_spec(bt) if bt and bt not in ("unknown",) else None
    missing_mand: list[str] = []
    anchor_total = 0
    anchor_ok = 0
    if spec:
        for fname in spec.mandatory_field_names():
            f = fmap.get(fname)
            v = f.get("value") if f else None
            if not f or is_placeholder(v):
                # Sonderfall jahr: kann via Inferenz OK sein
                if fname == "jahr" and v and v.isdigit() and 2018 <= int(v) <= 2030:
                    continue
                missing_mand.append(fname)
            # Anchor-Status
            anchor_total += 1
            if f and f.get("anchor_valid", False):
                anchor_ok += 1

    # 4. Plausibilität
    values = {f["feld"]: f.get("value", "") for f in result_entry.get("fields", [])}
    if bt:
        plaus_errors = run_plausibility(bt, values, skip_arithmetic=False)
        plaus_ok = len(plaus_errors) == 0
        plaus_summary = "✓" if plaus_ok else f"{len(plaus_errors)} fail"
        plaus_classes = sorted({e.split(":")[0] for e in plaus_errors})
    else:
        plaus_summary = "—"
        plaus_classes = []

    return {
        "pdf": result_entry.get("pdf_name", "?"),
        "belegtyp": bt or "—",
        "status": result_entry.get("status", "?"),
        "pages": n_pages,
        "words": total_words,
        "word_density": word_density,
        "cid_tokens": cid_tokens,
        "cid_pct": (cid_tokens / total_words * 100) if total_words else 0,
        "year_tokens": year_tokens,
        "identity": identity_check,
        "missing_mandatory": missing_mand,
        "anchor_ok": anchor_ok,
        "anchor_total": anchor_total,
        "plaus": plaus_summary,
        "plaus_classes": plaus_classes,
    }


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--samples", type=Path,
                        default=ROOT / "evals" / "samples_real_2022")
    args = parser.parse_args()

    samples_dir = args.samples
    results_file = samples_dir / "_results_full.json"
    if not results_file.exists():
        print(f"FEHLT: {results_file}", file=sys.stderr)
        sys.exit(1)

    results = json.loads(results_file.read_text())
    rows = []
    for entry in results:
        # Sample-JSON aus pdf_name ableiten (gleiches Stem-Mapping wie process)
        pdf_name = entry.get("pdf_name", "")
        from scripts.anonymize_belege import safe_filename
        stem = safe_filename(Path(pdf_name).stem)
        json_path = samples_dir / f"{stem}.json"
        if not json_path.exists():
            # Fallback: probiere ohne safe-mapping
            json_path = samples_dir / f"{Path(pdf_name).stem}.json"
        if not json_path.exists():
            continue
        rows.append(analyze_sample(json_path, entry))

    # Output: Markdown
    out = samples_dir / "_diagnose_matrix.md"
    lines = [
        f"# Diagnose-Matrix — {samples_dir}",
        "",
        f"Pro PDF: Klassifikation · OCR-Qualität · Anonymisierungs-Integrität · "
        f"Pflichtfelder · Anchor · Plausibilität.",
        f"Bewusst nur Metadaten, keine Werte (Privacy).",
        "",
        "## Übersicht",
        "",
        f"- Samples: **{len(rows)}** / {len(results)}",
        f"- Belegtyp-Klassifiziert: {sum(1 for r in rows if r['status']=='ok')}",
        f"- Out-of-Scope: {sum(1 for r in rows if r['status']=='out_of_scope')}",
        f"- Unknown: {sum(1 for r in rows if r['status']=='unknown_belegtyp')}",
        f"- Identität ✓: {sum(1 for r in rows if r['identity']=='✓')} "
        f"(nur Lohnausweis + Bank mit Brutto>200)",
        f"- Plausibilität ✓: {sum(1 for r in rows if r['plaus']=='✓')}",
        f"- Hohe OCR-Artefakt-Rate (cid>5%): {sum(1 for r in rows if r['cid_pct']>5)}",
        "",
        "## Matrix",
        "",
        "| Datei | Belegtyp | Status | Seiten | Wörter | OCR-cid% | "
        "Jahr-Tok | Identität | Pflicht fehlt | Anker | Plausibilität |",
        "|---|---|---|---:|---:|---:|---:|---|---|---|---|",
    ]
    for r in sorted(rows, key=lambda x: (x["belegtyp"], x["pdf"])):
        pdf_short = (r["pdf"][:35] + "…") if len(r["pdf"]) > 36 else r["pdf"]
        missing = ",".join(r["missing_mandatory"]) if r["missing_mandatory"] else "—"
        anker = f"{r['anchor_ok']}/{r['anchor_total']}" if r["anchor_total"] else "—"
        cid_warn = "⚠️ " if r["cid_pct"] > 5 else ""
        lines.append(
            f"| `{pdf_short}` | {r['belegtyp']} | {r['status']} | "
            f"{r['pages']} | {r['words']} | {cid_warn}{r['cid_pct']:.1f}% | "
            f"{r['year_tokens']} | {r['identity']} | {missing} | "
            f"{anker} | {r['plaus']} |"
        )

    # Fehlerklassen-Bucketing
    lines += ["", "## Fehlerklassen", ""]
    buckets: dict[str, list[str]] = {}
    for r in rows:
        keys = []
        if r["status"] == "unknown_belegtyp":
            keys.append("F1-Klassifikation-unbekannt")
        if r["status"] == "out_of_scope":
            keys.append("F0-Out-of-Scope (kein Fehler)")
        if r["cid_pct"] > 5:
            keys.append("F2-OCR-Artefakte (cid>5%)")
        if r["year_tokens"] == 0 and r["status"] == "ok":
            keys.append("F3-Anonymisierungs-Defekt-Jahr")
        if r["identity"] not in ("✓", "n/a", "n/a (≤200)") and r["identity"].startswith("Δ"):
            keys.append("F4-Anonymisierungs-Defekt-Arithmetik")
        if r["missing_mandatory"]:
            keys.append("F5-Pflichtfeld-Lücke")
        if r["anchor_total"] > 0 and r["anchor_ok"] < r["anchor_total"]:
            keys.append("F6-Anker-Lücke")
        if r["plaus"] != "✓" and r["plaus"] != "—":
            keys.append("F7-Plausibilitäts-Fail")
        for k in keys:
            buckets.setdefault(k, []).append(r["pdf"])

    for k in sorted(buckets):
        files = buckets[k]
        lines += [
            f"### {k} ({len(files)})",
            "",
        ]
        for f in files:
            short = f[:60] + ("…" if len(f) > 60 else "")
            lines.append(f"- `{short}`")
        lines.append("")

    out.write_text("\n".join(lines))
    print(f"Geschrieben: {out}")
    print(f"  {len(rows)} Samples analysiert, {len(buckets)} Fehlerklassen")


if __name__ == "__main__":
    main()
