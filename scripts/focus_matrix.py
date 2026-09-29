"""Fokussierte Matrix nach User-Vorgabe:
PDF | Belegtyp | Hauptklasse | Pflicht? | Anker? | Plaus? | Restaktion

Pro Phase-F-Schritt nach jedem Pipeline-Lauf aktualisieren.
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from extractors.steuer_zielmodell import (
    get_spec, run_plausibility, is_placeholder,
)


def _parse_amount(s):
    try:
        return float(s.replace("'", "").replace(",", "."))
    except (ValueError, TypeError, AttributeError):
        return None


def restaktion(belegtyp, missing_mand, anchor_lacks, plaus_errors, entry) -> str:
    """Schlägt nächste Aktion vor — basiert auf Phase-F-Plan."""
    if entry["status"] == "out_of_scope":
        return "—"
    if entry["status"] == "unknown_belegtyp":
        return "F1: Klassifikator-Regel ergänzen"
    actions = []
    # F6: Anker-Lücken bei Personen-Feldern
    for fname in anchor_lacks:
        if "name" in fname:
            actions.append(f"F6: Anker für {fname}")
    # F5: Pflichtfeld-Lücken
    for fname in missing_mand:
        if fname == "kind_name":
            actions.append("F5: family.yaml-Match oder manual_review")
        elif fname == "jahr":
            actions.append("F3: filename_rule für Steuerjahr")
        elif fname.startswith("periode_"):
            actions.append("F5: periode aus Lohnausweis-Layout")
        elif fname == "bruttoertrag":
            actions.append("F5: Layout-Regel oder not_in_beleg_by_design")
        else:
            actions.append(f"F5: {fname}")
    # F7: Plausibilität
    for err in plaus_errors:
        if "VRS" in err or "Verrechnungssteuer" in err:
            actions.append("F7: manual_review (VRS not reported)")
    return "; ".join(actions) if actions else "✓ keine Restaktion"


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--samples", type=Path,
                        default=ROOT / "evals" / "samples_real_2022")
    args = parser.parse_args()

    results = json.loads((args.samples / "_results_full.json").read_text())
    rows = []
    for e in results:
        bt = e.get("belegtyp", "—")
        spec = get_spec(bt)
        fmap = {f["feld"]: f for f in e.get("fields", [])}

        missing_mand = []
        anchor_lacks = []
        auto_ok_count = 0
        manual_review_count = 0
        missing_count = 0
        conditional_manual_review_count = 0
        conditional_manual_review_fields: list[str] = []
        if spec:
            # Conditional Felder (z.B. verrechnungssteuer/_total): manual_review:*
            # zählt SEPARAT — auto_ok/missing wäre hier nicht aussagekräftig
            # (Feld erscheint nur unter Bedingung). User-Wunsch: conditional MR
            # soll Hauptklasse MR triggern bzw. eigene Spalte bekommen.
            for fname in spec.conditional_field_names():
                f = fmap.get(fname)
                v = f.get("value") if f else None
                if isinstance(v, str) and v.startswith("manual_review:"):
                    conditional_manual_review_count += 1
                    conditional_manual_review_fields.append(fname)
            for fname in spec.mandatory_field_names():
                f = fmap.get(fname)
                v = f.get("value") if f else None
                if not f or is_placeholder(v):
                    if fname == "jahr" and v and v.isdigit() and 2018 <= int(v) <= 2030:
                        auto_ok_count += 1
                        continue  # filename-inferred OK
                    missing_mand.append(fname)
                    missing_count += 1
                    continue
                # Felder die ohne BBox-Anker als OK gelten (analog eval_anchors)
                _INFERRED_OK = {
                    "jahr", "verrechnungssteuer_total",
                    "versicherte_person_name", "person_name", "kontoinhaber_name",
                    "arbeitnehmer_name", "spender_name",
                }
                if v.startswith("manual_review:"):
                    manual_review_count += 1
                elif f.get("anchor_valid") or fname in _INFERRED_OK:
                    auto_ok_count += 1
                else:
                    missing_count += 1  # Wert da aber kein Anker → nicht auto_ok
                    missing_mand.append(fname)
                if f and not f.get("anchor_valid", False) and v and not is_placeholder(v):
                    if fname not in _INFERRED_OK:
                        # manual_review:* und not_in_beleg_by_design brauchen keinen BBox-Anker
                        if not (v.startswith("manual_review:") or v == "not_in_beleg_by_design"):
                            anchor_lacks.append(fname)

        plaus_errors = []
        if bt and spec:
            vals = {f["feld"]: f.get("value", "") for f in e.get("fields", [])}
            # skip_arithmetic=True: anonymisierte Real-Samples haben nicht-konsistente
            # Beträge (Anonymizer-Skalierung). Arithmetische Regeln nur in Produktion.
            plaus_errors = run_plausibility(bt, vals, skip_arithmetic=True)

        # Hauptklasse bestimmen — manual_review (egal ob mandatory oder conditional)
        # triggert MR, damit conditional vrs/_total nicht still grün durchläuft.
        total_mr = manual_review_count + conditional_manual_review_count
        hauptklasse = "F0" if e["status"] == "out_of_scope" else ""
        if not hauptklasse:
            if e["status"] == "unknown_belegtyp": hauptklasse = "F1"
            elif missing_count > 0: hauptklasse = "F5"
            elif total_mr > 0: hauptklasse = "MR"
            elif anchor_lacks: hauptklasse = "F6"
            elif plaus_errors: hauptklasse = "F7"
            else: hauptklasse = "✓"

        rows.append({
            "pdf": e["pdf_name"],
            "belegtyp": bt,
            "hauptklasse": hauptklasse,
            "auto_ok": str(auto_ok_count),
            "manual_review_count": str(manual_review_count),
            "cond_mr_count": str(conditional_manual_review_count),
            "cond_mr_fields": ",".join(conditional_manual_review_fields),
            "missing_count": str(missing_count),
            "anker_ok": "✓" if not anchor_lacks else f"✗ ({len(anchor_lacks)})",
            "plaus_ok": "✓" if not plaus_errors else f"✗ ({len(plaus_errors)})",
            "restaktion": restaktion(bt, missing_mand, anchor_lacks, plaus_errors, e),
        })

    # Output
    out = args.samples / "_focus_matrix.md"
    lines = [
        "# Focus-Matrix — Phase-F",
        "",
        f"Stand: {args.samples}",
        "",
        "| PDF | Belegtyp | Hauptklasse | auto_ok | manual_review | cond_MR | missing | Plaus? | Restaktion |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in sorted(rows, key=lambda x: (x["hauptklasse"], x["belegtyp"], x["pdf"])):
        pdf_short = (r["pdf"][:42] + "…") if len(r["pdf"]) > 43 else r["pdf"]
        cond_cell = r["cond_mr_count"]
        if r["cond_mr_fields"]:
            cond_cell = f"{r['cond_mr_count']} ({r['cond_mr_fields']})"
        lines.append(
            f"| `{pdf_short}` | {r['belegtyp']} | {r['hauptklasse']} | "
            f"{r['auto_ok']} | {r['manual_review_count']} | {cond_cell} | {r['missing_count']} | "
            f"{r['plaus_ok']} | {r['restaktion']} |"
        )

    # Stats
    by_class = {}
    for r in rows:
        by_class.setdefault(r["hauptklasse"], 0)
        by_class[r["hauptklasse"]] += 1
    total_auto_ok = sum(int(r["auto_ok"]) for r in rows if r["auto_ok"].isdigit())
    total_mr = sum(int(r["manual_review_count"]) for r in rows if r["manual_review_count"].isdigit())
    total_cond_mr = sum(int(r["cond_mr_count"]) for r in rows if r["cond_mr_count"].isdigit())
    total_missing = sum(int(r["missing_count"]) for r in rows if r["missing_count"].isdigit())
    lines += ["", "## Verteilung", ""]
    for cls in sorted(by_class):
        desc = {
            "✓": "vollständig automatisch übertragbar",
            "MR": "manuell prüfen (manual_review:* Felder)",
            "F0": "out-of-scope",
            "F1": "unbekannter Belegtyp",
            "F5": "Pflichtfeld fehlt / kein Anker",
            "F6": "Anker-Lücke (Wert da, kein BBox)",
            "F7": "Plausibilitätsfehler",
        }.get(cls, "")
        lines.append(f"- **{cls}** ({desc}): {by_class[cls]}")
    lines += [
        "",
        "## Pflichtfeld-Buckets (alle Belege)",
        "",
        f"- auto_ok: {total_auto_ok}",
        f"- manual_review (mandatory): {total_mr}  ← nie als grün gezählt",
        f"- manual_review (conditional): {total_cond_mr}  ← separat, z.B. verrechnungssteuer/_total",
        f"- missing: {total_missing}",
    ]

    out.write_text("\n".join(lines))
    print(f"Geschrieben: {out}")
    for cls in sorted(by_class):
        print(f"  {cls}: {by_class[cls]}")
    print(f"  Pflichtfeld-Buckets: auto_ok={total_auto_ok}  manual_review={total_mr}  cond_MR={total_cond_mr}  missing={total_missing}")


if __name__ == "__main__":
    main()
