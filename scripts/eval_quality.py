"""Eval-Qualitaets-Harness — truth-freies Mess-Tool fuer Extraktions-Qualitaet.

Schliesst die im Projekt als UNKONTROLLIERT dokumentierte Eval-Luecke
(Wert-Korrektheit faktisch ungeprueft) auf eine Art, die die Privacy-Rotlinie
kompromisslos respektiert: das Tool berechnet pro Belegtyp und gesamt
STRUKTURELLE Qualitaetsmetriken aus ``_results_full.json`` und schreibt sie
streng aggregiert nach ``eval_quality.md``.

Designvertrag (Privacy-Disziplin, Vorbild ``scripts/grade_run.py`` /
``scripts/check_aufstellung.py``):

* Das ``metrics``-Dict, das ``compute_metrics`` zurueckgibt, enthaelt PER
  KONSTRUKTION nur Zaehler, Raten, Feldnamen, Marker-Namen und Regel-Namen —
  NIEMALS einen Feldwert, Befund-Text mit Wert oder Truth-String.
* ``render_report`` rendert AUSSCHLIESSLICH aus ``metrics`` → es kann
  strukturell keine Werte leaken. Das ist die Privacy-Garantie, nicht eine
  nachgelagerte Filterung.
* Plausibilitaets-Befund-Strings (die Werte enthalten) werden verworfen —
  nur ``len(befunde)`` fliesst in die Metriken.

Das Tool ist ein REPORT-Tool, kein Gate: Default-Exit 0. Optional setzt
``--min-pflicht-coverage`` einen fail-closed-Schwellwert fuer spaeteres CI.

Ein ``--compare``-Modus (siehe ``compute_compare`` / ``render_compare_report``)
stellt zwei Laeufe gegeneinander — das Werkzeug fuer den 7B-vs-14B/32B-
Vergleich. Ein optionaler Truth-Match-Layer (``compute_truth_match``) ergaenzt
eine Wert-Match-Rate, wenn Truth-Files vorhanden sind, mit ehrlichem
Honesty-Report.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from extractors.steuer_zielmodell import (
    SPECS, get_spec, is_placeholder, run_plausibility,
)

DEFAULT_SAMPLES = Path("evals/samples_real_2022")

# Sentinel-Werte, die kein erfuelltes Pflichtfeld sind.
_BY_DESIGN = "not_in_beleg_by_design"
_MANUAL_REVIEW_PREFIX = "manual_review:"


# ---------------------------------------------------------------------------
# Feld-Klassifikation (rein aus Wert/Anker, nie der Wert selbst verlaesst sie)
# ---------------------------------------------------------------------------


def _ist_erfuelltes_pflichtfeld(field_record: dict | None) -> bool:
    """True, wenn das Feld als echtes, verankertes Pflichtfeld zaehlt.

    Nicht erfuellt sind: fehlendes Feld, Platzhalter, ``manual_review:*``,
    ``not_in_beleg_by_design``, leerer Wert ODER fehlender Anker
    (anchor_valid != True).
    """
    if field_record is None:
        return False
    value = field_record.get("value")
    if value is None:
        return False
    value = str(value)
    if is_placeholder(value):
        return False
    if value.startswith(_MANUAL_REVIEW_PREFIX):
        return False
    if value == _BY_DESIGN:
        return False
    return bool(field_record.get("anchor_valid", False))


def _ist_echter_wert(value) -> bool:
    """True, wenn der Wert eine echte Extraktion ist (kein Marker/Platzhalter)."""
    if value is None:
        return False
    value = str(value)
    if is_placeholder(value):
        return False
    if value.startswith(_MANUAL_REVIEW_PREFIX):
        return False
    if value == _BY_DESIGN:
        return False
    return True


# ---------------------------------------------------------------------------
# Kern: aggregierte Metriken (NUR Zaehler/Raten/Feldnamen/Marker-Namen)
# ---------------------------------------------------------------------------


def compute_metrics(results: list[dict]) -> dict:
    """Berechnet strukturelle Qualitaetsmetriken aus den Result-Entries.

    Gibt ein Dict zurueck, das per Konstruktion KEINE Feldwerte enthaelt —
    nur Aggregat-Zaehler, Raten, Feldnamen, Marker-Namen.
    """
    # (a) Pflichtfeld-Abdeckung pro Belegtyp.
    pflicht_erfuellt: Counter = Counter()
    pflicht_erwartet: Counter = Counter()
    # (a') Conditional-Felder nur als Info (vorhanden/abwesend), nie Pflicht.
    conditional_vorhanden: Counter = Counter()
    conditional_abwesend: Counter = Counter()

    # (b) Anker-Validity.
    anker_mit: Counter = Counter()
    anker_total: Counter = Counter()

    # (c) Marker.
    marker_manual_review: Counter = Counter()
    placeholder_total = 0
    by_design_total = 0

    # (d) Plausibilitaet.
    plaus_befunde: Counter = Counter()

    # (e) Verteilungen.
    belegtyp_verteilung: Counter = Counter()
    status_verteilung: Counter = Counter()

    for entry in results:
        status = entry.get("status") or "unknown_belegtyp"
        belegtyp = entry.get("belegtyp")
        fields = entry.get("fields", []) or []

        status_verteilung[status] += 1
        belegtyp_verteilung[str(belegtyp) if belegtyp else "None"] += 1

        field_by_name = {f.get("feld"): f for f in fields}

        # (a) Pflichtfeld-Abdeckung — nur fuer status==ok mit bekanntem Spec.
        spec = get_spec(belegtyp) if belegtyp else None
        if status == "ok" and spec is not None:
            for fname in spec.mandatory_field_names():
                pflicht_erwartet[belegtyp] += 1
                if _ist_erfuelltes_pflichtfeld(field_by_name.get(fname)):
                    pflicht_erfuellt[belegtyp] += 1
            # Conditional nur als Info — NIE in die Pflicht-Rate einrechnen.
            for fname in spec.conditional_field_names():
                rec = field_by_name.get(fname)
                if rec is not None and _ist_echter_wert(rec.get("value")):
                    conditional_vorhanden[belegtyp] += 1
                else:
                    conditional_abwesend[belegtyp] += 1

        # (b)+(c) Felder durchgehen.
        for f in fields:
            value = f.get("value")
            sval = "" if value is None else str(value)
            bt_key = str(belegtyp) if belegtyp else "None"

            if sval.startswith(_MANUAL_REVIEW_PREFIX):
                suffix = sval[len(_MANUAL_REVIEW_PREFIX):].strip() or "(leer)"
                marker_manual_review[suffix] += 1
            elif sval == _BY_DESIGN:
                by_design_total += 1
            elif is_placeholder(sval):
                placeholder_total += 1

            # Anker-Validity-Nenner: nicht-leere, nicht-manual_review Felder.
            if sval and not sval.startswith(_MANUAL_REVIEW_PREFIX):
                anker_total[bt_key] += 1
                anker_total["gesamt"] += 1
                if f.get("anchor_valid", False):
                    anker_mit[bt_key] += 1
                    anker_mit["gesamt"] += 1

        # (d) Plausibilitaet — nur Anzahl, Befund-Strings werden verworfen.
        if spec is not None:
            values = {f.get("feld"): (f.get("value") or "") for f in fields}
            befunde = run_plausibility(belegtyp, values, skip_arithmetic=True)
            if befunde:
                plaus_befunde[belegtyp] += len(befunde)

    # Pflicht-Coverage-Raten zusammenstellen.
    pflicht_coverage: dict[str, dict] = {}
    gesamt_erfuellt = 0
    gesamt_erwartet = 0
    for bt in sorted(pflicht_erwartet):
        erf = pflicht_erfuellt[bt]
        erw = pflicht_erwartet[bt]
        gesamt_erfuellt += erf
        gesamt_erwartet += erw
        pflicht_coverage[bt] = {
            "erfuellt": erf,
            "erwartet": erw,
            "rate": (erf / erw) if erw else 0.0,
        }
    pflicht_coverage["gesamt"] = {
        "erfuellt": gesamt_erfuellt,
        "erwartet": gesamt_erwartet,
        "rate": (gesamt_erfuellt / gesamt_erwartet) if gesamt_erwartet else 0.0,
    }

    # Anker-Validity-Raten.
    anker_validity: dict[str, dict] = {}
    for bt in sorted(set(anker_total) | {"gesamt"}):
        tot = anker_total[bt]
        mit = anker_mit[bt]
        anker_validity[bt] = {
            "mit_anker": mit,
            "gesamt": tot,
            "rate": (mit / tot) if tot else 0.0,
        }

    # Plausibilitaet pro Belegtyp + gesamt.
    plaus_out: dict[str, int] = {bt: plaus_befunde[bt] for bt in sorted(plaus_befunde)}
    plaus_out["gesamt"] = sum(plaus_befunde.values())

    return {
        "anzahl_belege": len(results),
        "pflicht_coverage": pflicht_coverage,
        "conditional_info": {
            "vorhanden": dict(conditional_vorhanden),
            "abwesend": dict(conditional_abwesend),
        },
        "anker_validity": anker_validity,
        "marker": {
            "manual_review": dict(marker_manual_review),
            "placeholder_total": placeholder_total,
            "not_in_beleg_by_design_total": by_design_total,
        },
        "plausibility": plaus_out,
        "belegtyp_verteilung": dict(belegtyp_verteilung),
        "status_verteilung": dict(status_verteilung),
    }


# ---------------------------------------------------------------------------
# Report — rein aus den Aggregat-Zahlen (strukturell wertfrei)
# ---------------------------------------------------------------------------


def _rate_pct(d: dict) -> str:
    return f"{100 * d['rate']:.0f}%"


def render_report(metrics: dict, *, truth_match: dict | None = None) -> str:
    """Baut den Markdown-Report ausschliesslich aus den Aggregat-Zahlen.

    Da ``metrics`` per Konstruktion keine Werte enthaelt, kann der Report
    keine Werte leaken — das ist die strukturelle Privacy-Garantie.
    """
    lines: list[str] = []
    lines.append("# Eval-Qualitaet — strukturelle Metriken")
    lines.append("")
    lines.append(f"Belege gesamt: **{metrics['anzahl_belege']}**")
    lines.append("")
    lines.append("> Privacy: dieser Report enthaelt ausschliesslich Zahlen, "
                 "Raten, Feldnamen, Marker-Namen und Regel-Namen — niemals "
                 "Feldwerte.")
    lines.append("")

    # Pflichtfeld-Abdeckung + Anker-Validity + Plausi pro Belegtyp.
    lines.append("## Pflichtfeld-Abdeckung, Anker-Validity, Plausibilitaet")
    lines.append("")
    lines.append("| Belegtyp | Pflicht-Abdeckung | Anker-Validity | Plausi-Befunde |")
    lines.append("|---|---|---|---|")
    pcov = metrics["pflicht_coverage"]
    av = metrics["anker_validity"]
    plaus = metrics["plausibility"]
    belegtypen = sorted(bt for bt in pcov if bt != "gesamt")
    for bt in belegtypen:
        cov = pcov[bt]
        cov_str = f"{cov['erfuellt']}/{cov['erwartet']} ({_rate_pct(cov)})"
        av_bt = av.get(bt)
        av_str = (f"{av_bt['mit_anker']}/{av_bt['gesamt']} ({_rate_pct(av_bt)})"
                  if av_bt else "—")
        lines.append(f"| {bt} | {cov_str} | {av_str} | {plaus.get(bt, 0)} |")
    # Gesamt-Zeile.
    g = pcov["gesamt"]
    gav = av.get("gesamt", {"mit_anker": 0, "gesamt": 0, "rate": 0.0})
    lines.append(
        f"| **gesamt** | **{g['erfuellt']}/{g['erwartet']} ({_rate_pct(g)})** "
        f"| **{gav['mit_anker']}/{gav['gesamt']} ({_rate_pct(gav)})** "
        f"| **{plaus.get('gesamt', 0)}** |"
    )
    lines.append("")

    # Marker-Tabelle.
    lines.append("## Marker (manual_review-Suffixe + by-design/Platzhalter)")
    lines.append("")
    lines.append("| Marker | Count |")
    lines.append("|---|---|")
    mk = metrics["marker"]
    for suffix in sorted(mk["manual_review"]):
        lines.append(f"| manual_review:{suffix} | {mk['manual_review'][suffix]} |")
    lines.append(f"| (Platzhalter total) | {mk['placeholder_total']} |")
    lines.append(f"| (not_in_beleg_by_design total) | {mk['not_in_beleg_by_design_total']} |")
    lines.append("")

    # Status-Verteilung.
    lines.append("## Status-Verteilung")
    lines.append("")
    lines.append("| Status | Count |")
    lines.append("|---|---|")
    for st in sorted(metrics["status_verteilung"]):
        lines.append(f"| {st} | {metrics['status_verteilung'][st]} |")
    lines.append("")

    # Belegtyp-Verteilung.
    lines.append("## Belegtyp-Verteilung")
    lines.append("")
    lines.append("| Belegtyp | Count |")
    lines.append("|---|---|")
    for bt in sorted(metrics["belegtyp_verteilung"]):
        lines.append(f"| {bt} | {metrics['belegtyp_verteilung'][bt]} |")
    lines.append("")

    # Wert-Korrektheit (Truth-Layer) — optional, ehrlicher Honesty-Hinweis.
    if truth_match is not None:
        lines.append("## Wert-Korrektheit (Truth-Match)")
        lines.append("")
        if truth_match.get("wert_korrektheit_ungeprueft"):
            lines.append(
                "> **Wert-Korrektheit ungeprueft** — keine Truth-Files oder "
                "keine matchbaren Felder vorhanden. Dieser Lauf misst NUR "
                "strukturelle Qualitaet, NICHT die inhaltliche Richtigkeit der "
                "extrahierten Werte. Kein stiller PASS."
            )
            lines.append("")
            lines.append(
                f"Truth-Files gefunden: {truth_match.get('truth_files_gefunden', 0)} · "
                f"matchbare Felder: {truth_match.get('match_total', 0)}"
            )
        else:
            g = truth_match["gesamt"]
            rate = (100 * g["match_korrekt"] / g["match_total"]) if g["match_total"] else 0.0
            lines.append(
                f"Wert-Match-Rate gesamt: **{g['match_korrekt']}/{g['match_total']} "
                f"({rate:.0f}%)** · uebersprungen (anonymisiert-nicht-matchbar): "
                f"{g['uebersprungen_nicht_matchbar']} · Truth-Files: "
                f"{truth_match.get('truth_files_gefunden', 0)}"
            )
            lines.append("")
            lines.append("| Belegtyp | Wert-Match | uebersprungen |")
            lines.append("|---|---|---|")
            for bt in sorted(truth_match["pro_belegtyp"]):
                d = truth_match["pro_belegtyp"][bt]
                r = (100 * d["match_korrekt"] / d["match_total"]) if d["match_total"] else 0.0
                lines.append(
                    f"| {bt} | {d['match_korrekt']}/{d['match_total']} ({r:.0f}%) "
                    f"| {d['uebersprungen_nicht_matchbar']} |"
                )
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Compare-Modus — zwei Laeufe gegeneinander (Modellvergleich)
# ---------------------------------------------------------------------------


def _delta_row(a: float | int, b: float | int) -> dict:
    return {"a": a, "b": b, "delta": b - a}


def compute_compare(metrics_a: dict, metrics_b: dict,
                    results_a: list[dict], results_b: list[dict]) -> dict:
    """Stellt zwei Laeufe gegeneinander.

    Liefert eine Delta-Tabelle (B − A) fuer Pflicht-Abdeckung (gesamt + pro
    Belegtyp), Anker-Validity, Marker-Total und Plausi-Befund-Total sowie einen
    Wert-Unterschieds-Zaehler (matche Belege per pdf_name, zaehle Felder mit
    value_a != value_b). NIEMALS werden Werte selbst ausgegeben — nur Zaehler.
    """
    delta: dict[str, dict] = {}

    ga = metrics_a["pflicht_coverage"]["gesamt"]
    gb = metrics_b["pflicht_coverage"]["gesamt"]
    delta["pflicht_coverage_gesamt"] = _delta_row(ga["rate"], gb["rate"])

    # Pflicht-Abdeckung pro Belegtyp (Vereinigung der Belegtypen).
    delta["pflicht_coverage_pro_belegtyp"] = {}
    bts = set(metrics_a["pflicht_coverage"]) | set(metrics_b["pflicht_coverage"])
    for bt in sorted(bts - {"gesamt"}):
        ra = metrics_a["pflicht_coverage"].get(bt, {}).get("rate", 0.0)
        rb = metrics_b["pflicht_coverage"].get(bt, {}).get("rate", 0.0)
        delta["pflicht_coverage_pro_belegtyp"][bt] = _delta_row(ra, rb)

    ava = metrics_a["anker_validity"].get("gesamt", {}).get("rate", 0.0)
    avb = metrics_b["anker_validity"].get("gesamt", {}).get("rate", 0.0)
    delta["anker_validity_gesamt"] = _delta_row(ava, avb)

    mta = sum(metrics_a["marker"]["manual_review"].values())
    mtb = sum(metrics_b["marker"]["manual_review"].values())
    delta["marker_total"] = _delta_row(mta, mtb)

    # Marker-Detail besonders fuer hallucination_* / value_not_in_document_*.
    delta["marker_detail"] = {}
    suffixes = (set(metrics_a["marker"]["manual_review"])
                | set(metrics_b["marker"]["manual_review"]))
    for suf in sorted(suffixes):
        ca = metrics_a["marker"]["manual_review"].get(suf, 0)
        cb = metrics_b["marker"]["manual_review"].get(suf, 0)
        delta["marker_detail"][suf] = _delta_row(ca, cb)

    pta = metrics_a["plausibility"].get("gesamt", 0)
    ptb = metrics_b["plausibility"].get("gesamt", 0)
    delta["plausibility_total"] = _delta_row(pta, ptb)

    # Wert-Unterschieds-Zaehler: matche Belege per pdf_name (Schnittmenge).
    by_pdf_a = {e.get("pdf_name"): e for e in results_a}
    by_pdf_b = {e.get("pdf_name"): e for e in results_b}
    gemeinsame = set(by_pdf_a) & set(by_pdf_b)
    wert_unterschiede = 0
    for pdf in gemeinsame:
        fa = {f.get("feld"): f.get("value") for f in by_pdf_a[pdf].get("fields", [])}
        fb = {f.get("feld"): f.get("value") for f in by_pdf_b[pdf].get("fields", [])}
        for feld in set(fa) & set(fb):
            if fa[feld] != fb[feld]:
                wert_unterschiede += 1  # NUR zaehlen, nie den Wert ausgeben.

    return {
        "delta": delta,
        "wert_unterschiede": wert_unterschiede,
        "gematchte_belege": len(gemeinsame),
    }


def _fmt_delta(d: dict, *, pct: bool = False) -> str:
    if pct:
        return (f"{100*d['a']:.0f}% → {100*d['b']:.0f}% "
                f"(Δ {100*d['delta']:+.0f} pp)")
    return f"{d['a']} → {d['b']} (Δ {d['delta']:+})"


def render_compare_report(compare: dict) -> str:
    """Baut den Compare-Report rein aus Aggregat-Zahlen (Delta + Zaehler)."""
    lines: list[str] = []
    d = compare["delta"]
    lines.append("# Eval-Vergleich — Lauf A vs. Lauf B")
    lines.append("")
    lines.append("> Privacy: nur Raten/Zahlen/Marker-Namen — keine Feldwerte. "
                 "Konvention: A = primaerer Lauf (--samples), B = Vergleich "
                 "(--compare). Δ = B − A.")
    lines.append("")
    lines.append("## Delta (B − A)")
    lines.append("")
    lines.append("| Metrik | A → B (Δ) |")
    lines.append("|---|---|")
    lines.append(f"| Pflicht-Abdeckung gesamt | {_fmt_delta(d['pflicht_coverage_gesamt'], pct=True)} |")
    lines.append(f"| Anker-Validity gesamt | {_fmt_delta(d['anker_validity_gesamt'], pct=True)} |")
    lines.append(f"| Marker-Total (manual_review) | {_fmt_delta(d['marker_total'])} |")
    lines.append(f"| Plausi-Befunde total | {_fmt_delta(d['plausibility_total'])} |")
    lines.append("")

    if d["pflicht_coverage_pro_belegtyp"]:
        lines.append("## Pflicht-Abdeckung pro Belegtyp (Δ)")
        lines.append("")
        lines.append("| Belegtyp | A → B (Δ) |")
        lines.append("|---|---|")
        for bt in sorted(d["pflicht_coverage_pro_belegtyp"]):
            lines.append(f"| {bt} | {_fmt_delta(d['pflicht_coverage_pro_belegtyp'][bt], pct=True)} |")
        lines.append("")

    if d["marker_detail"]:
        lines.append("## Marker-Detail (weniger = besser bei hallucination_*/value_not_in_document_*)")
        lines.append("")
        lines.append("| Marker | A → B (Δ) |")
        lines.append("|---|---|")
        for suf in sorted(d["marker_detail"]):
            lines.append(f"| manual_review:{suf} | {_fmt_delta(d['marker_detail'][suf])} |")
        lines.append("")

    lines.append("## Wert-Unterschiede (nur Zaehler)")
    lines.append("")
    lines.append(f"Gematchte Belege (per pdf_name): **{compare['gematchte_belege']}**")
    lines.append("")
    lines.append(f"Felder mit unterschiedlichem extrahiertem Wert: "
                 f"**{compare['wert_unterschiede']}** (nur Anzahl — die Werte "
                 f"selbst verlassen das Tool nie).")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Truth-Match-Layer (optional, truth-ABHAENGIG) — nur Zaehler verlassen ihn
# ---------------------------------------------------------------------------

# Anonymisierte Felder: der Extrakt traegt stabile Marker (BANK-A, INST-C),
# die echte Truth den echten Namen → ein Wert-Match ist hier strukturell
# unmoeglich und KEIN Fail. Wir zaehlen sie als "uebersprungen".
_NICHT_MATCHBARE_FELDER: frozenset[str] = frozenset({
    "institut", "kasse", "stiftung", "anbieter", "leistungserbringer",
    "arbeitgeber", "versicherte_person_name", "person_name",
    "kontoinhaber_name", "arbeitnehmer_name", "kind_name", "spender_name",
})


def _normalisiere_wert(value: str) -> str:
    """CH-Apostroph/Komma entfernen — analog eval_anchors Substring-Konvention."""
    return value.replace("'", "").replace(",", "")


def compute_truth_match(results: list[dict], samples_dir: Path) -> dict:
    """Optionaler Wert-Match gegen *.truth.json — gibt NUR Zaehler zurueck.

    Reuse der Stem-Mapping-Logik aus ``scripts/eval_anchors.py`` (``_safe_stem``)
    und der dortigen Substring-Vergleichs-Konvention (Truth-Wert normalisiert
    via ``.replace("'","").replace(",","")`` als Substring im Result-Wert).

    Anonymisierte Felder (``_NICHT_MATCHBARE_FELDER``) werden uebersprungen und
    separat gezaehlt — NICHT als Fail. Truth- und Result-Werte werden NIE in
    den Rueckgabewert geschrieben, nur Zaehler.

    Honesty: bei 0 Truth-Files ODER 0 matchbaren Feldern wird das Flag
    ``wert_korrektheit_ungeprueft=True`` gesetzt — kein stiller PASS.
    """
    from eval_anchors import _safe_stem  # Reuse, nicht duplizieren.

    pro_belegtyp: dict[str, dict] = {}
    gesamt = {
        "match_korrekt": 0,
        "match_total": 0,
        "uebersprungen_nicht_matchbar": 0,
    }
    truth_files_gefunden = 0

    for res in results:
        stem = _safe_stem(res.get("pdf_name", ""))
        truth_path = samples_dir / f"{stem}.truth.json"
        if not truth_path.exists():
            continue
        truth_files_gefunden += 1
        truth = json.loads(truth_path.read_text())
        truth_fields = truth.get("fields", {})
        if not truth_fields:
            continue

        belegtyp = str(res.get("belegtyp") or "None")
        bt = pro_belegtyp.setdefault(belegtyp, {
            "match_korrekt": 0, "match_total": 0,
            "uebersprungen_nicht_matchbar": 0,
        })
        result_values = {f.get("feld"): (f.get("value") or "")
                         for f in res.get("fields", [])}

        for feld, spec in truth_fields.items():
            truth_value = spec.get("value") if isinstance(spec, dict) else spec
            if truth_value is None or truth_value == "":
                continue
            if feld in _NICHT_MATCHBARE_FELDER:
                bt["uebersprungen_nicht_matchbar"] += 1
                gesamt["uebersprungen_nicht_matchbar"] += 1
                continue
            bt["match_total"] += 1
            gesamt["match_total"] += 1
            got = result_values.get(feld, "")
            if _normalisiere_wert(str(truth_value)) in _normalisiere_wert(str(got)):
                bt["match_korrekt"] += 1
                gesamt["match_korrekt"] += 1

    ungeprueft = truth_files_gefunden == 0 or gesamt["match_total"] == 0
    return {
        "pro_belegtyp": pro_belegtyp,
        "gesamt": gesamt,
        "truth_files_gefunden": truth_files_gefunden,
        "match_total": gesamt["match_total"],
        "wert_korrektheit_ungeprueft": ungeprueft,
    }


def run_compare(samples_dir: Path, other_results: Path,
                metrics_a: dict, results_a: list[dict]) -> None:
    """CLI-Helfer: laedt den Vergleichslauf, schreibt eval_compare.md."""
    if not other_results.exists():
        print(f"FEHLT: {other_results} — Vergleichslauf nicht gefunden.")
        return
    results_b = _load_results(other_results)
    metrics_b = compute_metrics(results_b)
    compare = compute_compare(metrics_a, metrics_b, results_a, results_b)
    out_path = samples_dir / "eval_compare.md"
    out_path.write_text(render_compare_report(compare))
    print(f"Compare geschrieben     : {out_path}")
    print(f"  gematchte Belege      : {compare['gematchte_belege']}")
    print(f"  Wert-Unterschiede     : {compare['wert_unterschiede']} (nur Zaehler)")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _load_results(path: Path) -> list[dict]:
    return json.loads(path.read_text())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Eval-Qualitaets-Harness (truth-frei, nur Aggregat-Zahlen).",
    )
    parser.add_argument("--samples", type=Path, default=DEFAULT_SAMPLES)
    parser.add_argument("--compare", type=Path, default=None,
                        help="Pfad zu einem zweiten _results_full.json (Modellvergleich).")
    parser.add_argument("--min-pflicht-coverage", type=float, default=None,
                        help="Optionaler fail-closed-Schwellwert (Exit 1 wenn unterschritten).")
    args = parser.parse_args(argv)

    results_file = args.samples / "_results_full.json"
    if not results_file.exists():
        print(f"FEHLT: {results_file} — erst `scripts/process_samples_full.py` ausfuehren.")
        return 1

    results = _load_results(results_file)
    metrics = compute_metrics(results)

    # Truth-Match-Layer automatisch, wenn mindestens ein *.truth.json existiert.
    # (compute_truth_match wird in Task 2 in diesem Modul ergaenzt.)
    truth_match = None
    if any(args.samples.glob("*.truth.json")) and "compute_truth_match" in globals():
        truth_match = compute_truth_match(results, args.samples)

    report = render_report(metrics, truth_match=truth_match)
    out_path = args.samples / "eval_quality.md"
    out_path.write_text(report)

    g = metrics["pflicht_coverage"]["gesamt"]
    gav = metrics["anker_validity"].get("gesamt", {"mit_anker": 0, "gesamt": 0, "rate": 0.0})
    mk = metrics["marker"]
    marker_total = sum(mk["manual_review"].values())
    print("=" * 72)
    print(f"EVAL-QUALITAET — {results_file}")
    print("=" * 72)
    print(f"Pflicht-Abdeckung gesamt : {g['erfuellt']}/{g['erwartet']} "
          f"({100*g['rate']:.0f}%)")
    print(f"Anker-Validity gesamt    : {gav['mit_anker']}/{gav['gesamt']} "
          f"({100*gav['rate']:.0f}%)")
    print(f"Marker (manual_review)   : {marker_total} total")
    print(f"Plausi-Befunde           : {metrics['plausibility'].get('gesamt', 0)} total")
    print(f"Report geschrieben       : {out_path}")

    # Compare-Modus (in Task 2 in diesem Modul ergaenzt).
    if args.compare is not None and "run_compare" in globals():
        run_compare(args.samples, args.compare, metrics, results)

    # Fail-closed-Option.
    if args.min_pflicht_coverage is not None and g["rate"] < args.min_pflicht_coverage:
        print(f"FAIL: Pflicht-Abdeckung {100*g['rate']:.0f}% < "
              f"Schwelle {100*args.min_pflicht_coverage:.0f}%")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
