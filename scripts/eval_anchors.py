"""Anker-basierte Eval: prüft Label-Nähe statt Stringwert.

Codex-Review-Antwort: nicht mehr "stimmt der Stringwert", sondern
"hat das System den richtigen Kandidaten aus der richtigen räumlichen
Region gewählt".

Ablauf:
1. ``_results_full.json`` liefert pro Sample × Feld einen BBox-Anker + Seite.
2. Truth-File enthält pro Feld optional ein ``near_label`` (Token, das visuell
   in der Nähe stehen muss — Spalten-Label, Tabellenzelle).
3. Eval prüft pro Feld: existiert eine Vorkommnis von ``near_label`` im
   JSON-Sample, dessen BBox dem extrahierten Anker räumlich benachbart ist?

"Räumlich benachbart" = einer der beiden Fälle:
- **Gleiche Zeile** (gleiche ``top``-Range, ±5 pt) und Label links vom Wert
  (typische "Label  Wert"-Tabellenzelle).
- **Direkt darunter** (Label-bottom + 30pt > Wert-top) und x-Überlapp ≥ 30%
  (Label über der Spalte).

Datums-Fragmente, Distractor-Beträge etc. werden so von Layout-Position
ausgeschlossen — und das Mass gibt Codex' Frage eine Antwort:
"Findet das System bei vermoegensstand_3112 den Betrag neben Endsaldo
und nicht den neben Zinszahlung?"
"""
from __future__ import annotations

import json
import re
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from extractors.steuer_zielmodell import (
    get_spec, is_placeholder, mandatory_fields, run_plausibility,
)

SAMPLES_DIR = Path("evals/samples_real")
RESULTS_FILE = SAMPLES_DIR / "_results_full.json"


def _parse_samples_arg() -> None:
    """Erlaubt --samples <dir> Override für SAMPLES_DIR und RESULTS_FILE."""
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--samples", type=Path, default=None)
    args, _ = parser.parse_known_args()
    if args.samples:
        global SAMPLES_DIR, RESULTS_FILE
        SAMPLES_DIR = args.samples
        RESULTS_FILE = SAMPLES_DIR / "_results_full.json"


def _safe_stem(name: str) -> str:
    """Spiegelt ``scripts.anonymize_belege.safe_filename`` für den Stem.

    Truth-/JSON-Sample-Dateien werden unter ``safe_filename(pdf_stem)`` abgelegt;
    der ``pdf_name`` im Result-File ist aber der Originalname (mit Umlauten,
    Leerzeichen). Ohne Anpassung würden 13/32 Samples still übersprungen
    (Codex-Finding P2).
    """
    stem = Path(name).stem
    norm = unicodedata.normalize("NFKD", stem).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^A-Za-z0-9._-]+", "_", norm).strip("_")

# Räumliche Toleranzen (PDF-Punkte)
SAME_ROW_TOP_TOLERANCE = 5.0   # max top-Differenz für "gleiche Zeile"
BELOW_LABEL_MAX_DIST = 30.0    # max top-Differenz für "direkt darunter"
BELOW_X_OVERLAP_MIN = 0.3      # min x-Überlapp für "direkt darunter"


def _bbox_overlap_x(a: list[float], b: list[float]) -> float:
    """Relativer x-Überlapp (0..1) basierend auf der kleineren Box."""
    a_x0, _, a_x1, _ = a[0], a[1], a[2], a[3]
    b_x0, _, b_x1, _ = b[0], b[1], b[2], b[3]
    overlap = max(0.0, min(a_x1, b_x1) - max(a_x0, b_x0))
    a_w = max(1e-6, a_x1 - a_x0)
    b_w = max(1e-6, b_x1 - b_x0)
    return overlap / min(a_w, b_w)


def _is_neighbor(value_bbox: list[float], label_bbox: list[float]) -> tuple[bool, str]:
    """True wenn label_bbox räumlich neben value_bbox steht (Tabellenzelle)."""
    v_x0, v_top, v_x1, v_bot = value_bbox
    l_x0, l_top, l_x1, l_bot = label_bbox

    # Same-row: Top-Differenz klein, Label links vom Wert
    if abs(l_top - v_top) <= SAME_ROW_TOP_TOLERANCE and l_x1 <= v_x0 + 5:
        return True, "same_row"

    # Below-label: Label oberhalb, x-Spalten-Überlapp
    vertical_dist = v_top - l_bot
    if 0 < vertical_dist <= BELOW_LABEL_MAX_DIST:
        ox = _bbox_overlap_x(value_bbox, label_bbox)
        if ox >= BELOW_X_OVERLAP_MIN:
            return True, f"below_x_overlap={ox:.2f}"

    return False, ""


def _find_label_bboxes(json_doc: dict, label_text: str, page: int | None) -> list[list[float]]:
    """Findet alle Wörter, die ``label_text`` (case-insensitive Substring) enthalten."""
    bboxes: list[list[float]] = []
    label_lower = label_text.lower()
    for p in json_doc["pages"]:
        if page is not None and p["page_num"] != page:
            continue
        # Multi-Token-Label: erst einzelne Wörter, dann concat von benachbarten
        words = p["words"]
        for i, w in enumerate(words):
            # Single-Token-Match
            if label_lower in w["text"].lower():
                bboxes.append([w["x0"], w["top"], w["x1"], w["bottom"]])
                continue
            # Multi-Token: bis zu 4 nachfolgende Wörter joinen (für "Saldo zu Ihren Gunsten")
            for span in range(2, 5):
                if i + span > len(words):
                    break
                joined = " ".join(words[i + k]["text"] for k in range(span)).lower()
                if label_lower in joined:
                    x0 = min(words[i + k]["x0"] for k in range(span))
                    x1 = max(words[i + k]["x1"] for k in range(span))
                    top = min(words[i + k]["top"] for k in range(span))
                    bot = max(words[i + k]["bottom"] for k in range(span))
                    bboxes.append([x0, top, x1, bot])
                    break
    return bboxes


def evaluate_sample(result: dict, truth: dict, json_doc: dict) -> list[dict]:
    """Prüft alle Felder eines Samples gegen die Truth (Wert + Label-Nähe).

    Phase-D-Erweiterung (Codex' Punkt #3):
    * ``truth.status == "out_of_scope"`` → Pflicht-Check, dass die Pipeline
      diesen Beleg als out_of_scope erkennt (statt still als "unknown").
    * ``truth.fields[fname]`` ohne ``near_label`` → reiner Existenz-Check:
      Feld muss extrahiert sein, Wert muss matchen. Damit ist auch ohne
      räumliche Anker-Truth ein Pflichtfeld-Vollständigkeits-Eval möglich.
    """
    checks: list[dict] = []
    fields = result.get("fields", [])
    field_by_name = {f["feld"]: f for f in fields}

    # 0. Status-Check (Phase D, Codex' Punkt 4): out_of_scope muss erkannt werden.
    if truth.get("status") == "out_of_scope":
        got_status = result.get("status")
        checks.append({
            "feld": "status",
            "expected": "out_of_scope",
            "got": got_status,
            "passed": got_status == "out_of_scope",
            "kind": "out_of_scope",
        })
        # Bei out_of_scope keine Feld-Checks — Pipeline extrahiert bewusst nichts.
        return checks

    # 1. Belegtyp-Check
    if "belegtyp" in truth:
        checks.append({
            "feld": "belegtyp",
            "expected": truth["belegtyp"],
            "got": result.get("belegtyp"),
            "passed": result.get("belegtyp") == truth["belegtyp"],
            "kind": "exact",
        })

    # 1b. Zielmodell-Pflichtfeld-Check (Codex' #3, jetzt strukturell + #2 gehärtet):
    # Jeder mandatory-Feld aus dem Zielmodell MUSS:
    #  - extrahiert sein
    #  - kein Placeholder-Wert ("nicht angegeben", "null", etc.)
    #  - Anker valid ODER aus deklarierter Inferenz-Quelle (jahr aus Filename)
    # zugelassene Inferenz-Quellen:
    # - jahr: aus Dateiname/Header
    # - verrechnungssteuer_total: BROKER-S-Layout-Rule (35% × Subtotal-Ertrag-A)
    # Felder die ohne BBox-Anker akzeptiert werden:
    # - "jahr": filename/header-inferred
    # - "verrechnungssteuer_total": layout-rule BROKER-S
    # - Personen-/Namens-Felder: anonymisierte Fantasy-Namen haben keine BBox
    #   (Anonymizer ersetzt Tokens, die räumliche Zuordnung geht verloren).
    #   Ein Wert mit Fantasy-Token gilt als hinreichend belegt.
    INFERRED_FIELDS = {
        "jahr", "verrechnungssteuer_total",
        "versicherte_person_name", "person_name", "kontoinhaber_name",
        "arbeitnehmer_name", "spender_name",
    }
    belegtyp = result.get("belegtyp") or truth.get("belegtyp")
    if belegtyp:
        for fname in mandatory_fields(belegtyp):
            extracted = field_by_name.get(fname)
            value = extracted.get("value") if extracted else None
            is_real_value = extracted and not is_placeholder(value)
            has_anchor = extracted and extracted.get("anchor_valid", False)
            is_inferred_ok = fname in INFERRED_FIELDS and is_real_value
            is_manual_review = isinstance(value, str) and value.startswith("manual_review:")
            passed = bool(is_real_value and not is_manual_review and (has_anchor or is_inferred_ok))
            kind = "manual_review" if is_manual_review else "zielmodell_mandatory"
            reason = ""
            if not passed and not is_manual_review:
                if not extracted:
                    reason = "Feld fehlt in Extraktion"
                elif is_placeholder(value):
                    reason = f"Placeholder-Wert: {value!r}"
                elif not has_anchor and not is_inferred_ok:
                    reason = "kein Anker und keine zugelassene Inferenz-Quelle"
            checks.append({
                "feld": fname,
                "expected": "extracted (Wert + Anker, kein Placeholder)",
                "got": value,
                "passed": passed,
                "kind": kind,
                "anchor_reason": reason,
            })

    # 1c. Plausibilität (Codex' #1): Belegtyp-spezifische Regeln aus Zielmodell.
    # skip_arithmetic=True für anonymisierte Real-Samples — die Werte sind
    # nicht arithmetisch konsistent zwischen Beträgen, aber Layout + Labels
    # bleiben echt (Codex' #4).
    if belegtyp:
        values = {f["feld"]: f.get("value", "") for f in fields}
        plaus_errors = run_plausibility(belegtyp, values, skip_arithmetic=True)
        if plaus_errors:
            for err in plaus_errors:
                checks.append({
                    "feld": "plausibility",
                    "expected": "rule passes",
                    "got": err,
                    "passed": False,
                    "kind": "plausibility",
                })
        else:
            checks.append({
                "feld": "plausibility",
                "expected": "all rules pass",
                "got": "ok",
                "passed": True,
                "kind": "plausibility",
            })

    # 2. Feld-Checks mit Label-Nähe
    field_checks = truth.get("fields", {})
    for fname, spec in field_checks.items():
        expected_value = spec.get("value")
        near_label = spec.get("near_label")
        expected_page = spec.get("page")

        extracted = field_by_name.get(fname)
        if extracted is None:
            checks.append({
                "feld": fname,
                "expected": expected_value,
                "got": None,
                "passed": False,
                "kind": "missing",
            })
            continue

        # 2a. Stringwert-Check (locker: Substring)
        got_value = extracted.get("value", "")
        value_ok = (
            expected_value is None
            or expected_value.replace("'", "").replace(",", "")
               in got_value.replace("'", "").replace(",", "")
        )

        # 2b. Label-Nähe-Check (BBox-spatial)
        # Wenn near_label spezifiziert ist, MUSS ein räumlicher Match existieren —
        # fehlende BBox ist dann ein harter Fail (Codex-Finding P3).
        anchor_ok: bool | None = None
        anchor_reason = ""
        if near_label is not None:
            if extracted.get("bbox") is None:
                anchor_ok = False
                anchor_reason = "Wert hat keine BBox (Verbatim-Fallback fehlgeschlagen)"
            else:
                label_bboxes = _find_label_bboxes(
                    json_doc, near_label, expected_page or extracted.get("page")
                )
                if not label_bboxes:
                    anchor_ok = False
                    anchor_reason = f"label '{near_label}' nicht gefunden"
                else:
                    value_bbox = extracted["bbox"]
                    for lb in label_bboxes:
                        ok, reason = _is_neighbor(value_bbox, lb)
                        if ok:
                            anchor_ok = True
                            anchor_reason = reason
                            break
                    if anchor_ok is None:
                        anchor_ok = False
                        anchor_reason = "kein Label in BBox-Nähe"

        # passed: Stringwert muss passen UND (wenn near_label spezifiziert ist)
        # der Anker muss räumlich beim Label sitzen.
        passed = value_ok and (anchor_ok is not False)
        checks.append({
            "feld": fname,
            "expected": expected_value,
            "got": got_value,
            "value_ok": value_ok,
            "near_label": near_label,
            "anchor_ok": anchor_ok,
            "anchor_reason": anchor_reason,
            "passed": passed,
            "kind": "anchor" if near_label else "value",
        })

    return checks


def main() -> None:
    _parse_samples_arg()
    if not RESULTS_FILE.exists():
        print(f"FEHLT: {RESULTS_FILE} — erst `python -m scripts.process_samples_full` ausführen.")
        return
    results = json.loads(RESULTS_FILE.read_text())

    total = 0
    passed = 0
    failures: list[str] = []
    manual_review_lines: list[str] = []

    skipped: list[str] = []
    for res in results:
        pdf_name = res["pdf_name"]
        # Truth-/JSON-Sample-Files liegen unter safe_filename(stem) — niemals
        # unter dem Originalnamen mit Umlauten/Leerzeichen.
        stem = _safe_stem(pdf_name)
        truth_path = SAMPLES_DIR / f"{stem}.truth.json"
        json_path = SAMPLES_DIR / f"{stem}.json"
        if not json_path.exists():
            skipped.append(pdf_name)
            continue
        # Ohne Truth-File: Zielmodell- und Plausibilitäts-Checks trotzdem ausführen
        # (nur Wert-/Anker-/Exact-Checks werden übersprungen, da kein Ground-Truth).
        if truth_path.exists():
            truth = json.loads(truth_path.read_text())
        else:
            truth = {}
        json_doc = json.loads(json_path.read_text())

        checks = evaluate_sample(res, truth, json_doc)
        for c in checks:
            if c.get("kind") == "manual_review":
                # manual_review:* Felder zählen nie als grün — separat ausgeben,
                # nicht im pass/fail-Zähler (DONE_CRITERIA Punkt 4).
                manual_review_lines.append(
                    f"  ⚠ {stem[:50]:50s} {c['feld']:25s} manual_review: "
                    f"got={c.get('got')!r}"
                )
                continue
            total += 1
            if c["passed"]:
                passed += 1
            else:
                failures.append(
                    f"  ✗ {stem[:50]:50s} {c['feld']:25s} {c.get('kind','')}: "
                    f"expected={c.get('expected')!r} got={c.get('got')!r} "
                    f"anchor={c.get('anchor_reason','')}"
                )

    # Zielmodell- und Plausibilitäts-Checks auch für Samples ohne Truth-File
    # (nur json_path muss existieren — leeres Truth-Dict verwenden).
    by_kind = {
        "exact": [0, 0], "value": [0, 0], "anchor": [0, 0],
        "missing": [0, 0], "out_of_scope": [0, 0],
        "zielmodell_mandatory": [0, 0], "plausibility": [0, 0],
        "manual_review": [0, 0],
    }
    for res in results:
        stem = _safe_stem(res["pdf_name"])
        truth_path = SAMPLES_DIR / f"{stem}.truth.json"
        json_path = SAMPLES_DIR / f"{stem}.json"
        if not json_path.exists():
            continue
        if truth_path.exists():
            truth = json.loads(truth_path.read_text())
        else:
            truth = {}
        json_doc = json.loads(json_path.read_text())
        for c in evaluate_sample(res, truth, json_doc):
            kind = c.get("kind", "value")
            if kind in by_kind:
                by_kind[kind][0] += 1 if c["passed"] else 0
                by_kind[kind][1] += 1

    if total == 0:
        print("=" * 80)
        print(f"FAIL: Keine Checks ausgeführt — keine Truth-Files in {SAMPLES_DIR}")
        print(f"Truth-Files (*.truth.json) werden manuell gepflegt — pro Beleg in "
              f"{SAMPLES_DIR} anlegen (siehe CLAUDE.md, Anonymisierungs-Pipeline).")
        print("=" * 80)
        sys.exit(1)

    print("=" * 80)
    print(f"ANCHOR-EVAL  —  {passed}/{total} Checks bestanden ({100*passed/total:.0f}%)")
    print("=" * 80)
    print("\nNach Check-Art:")
    print(f"  Belegtyp-Match (exact)         : {by_kind['exact'][0]}/{by_kind['exact'][1]}")
    print(f"  Truth-Stringwert-Match (value) : {by_kind['value'][0]}/{by_kind['value'][1]}  ← truth.fields ohne near_label")
    print(f"  Label-Nähe (anchor + value)    : {by_kind['anchor'][0]}/{by_kind['anchor'][1]}  ← Codex' Anker-Kriterium")
    print(f"  Out-of-Scope-Status            : {by_kind['out_of_scope'][0]}/{by_kind['out_of_scope'][1]}")
    print(f"  Zielmodell-Pflichtfelder       : {by_kind['zielmodell_mandatory'][0]}/{by_kind['zielmodell_mandatory'][1]}  ← auto_ok (Wert + Anker, kein manual_review)")
    print(f"  Manual-Review-Felder           : {by_kind['manual_review'][1]} Felder  ← SEPARAT, nie als grün gezählt")
    print(f"  Plausibilitäts-Regeln          : {by_kind['plausibility'][0]}/{by_kind['plausibility'][1]}  ← Codex' #1")
    print(f"  Fehlend (missing)              : {by_kind['missing'][0]}/{by_kind['missing'][1]}")
    if skipped:
        print(f"\nÜbersprungen ({len(skipped)} ohne Truth/JSON):")
        for s in skipped[:10]:
            print(f"  · {s}")
        if len(skipped) > 10:
            print(f"  ... ({len(skipped)-10} weitere)")
    if failures:
        print("\nFailures:")
        for f in failures[:30]:
            print(f)
        if len(failures) > 30:
            print(f"  ... ({len(failures)-30} weitere)")
    if manual_review_lines:
        print(f"\nBraucht Sichtprüfung ({len(manual_review_lines)} Felder — nie als grün gezählt):")
        for m in manual_review_lines[:30]:
            print(m)
        if len(manual_review_lines) > 30:
            print(f"  ... ({len(manual_review_lines)-30} weitere)")


if __name__ == "__main__":
    main()
