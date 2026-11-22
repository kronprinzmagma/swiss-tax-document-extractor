"""Sagt die Unsicherheit des Modells pro Wert Fehler voraus? (Kalibrierung)

Fuehrt die volle Pipeline (``process_json_sample``) auf synthetischen Fixtures
aus und laesst sich dabei von Ollama pro erzeugtem Token die Log-Wahrscheinlichkeit
geben (``logprobs=True``; die Antwort selbst bleibt gleich, Temperatur 0). Fuer
jeden Wert, den das Sprachmodell geliefert hat, wird daraus eine Sicherheit
berechnet: die kleinste Token-Wahrscheinlichkeit innerhalb des Werts
(``p_min``). Ein einziges unsicheres Token (z. B. eine Ziffer) reicht, damit
der Wert als unsicher gilt.

Danach wird jeder Wert gegen die Wahrheit (``evals/expected``) geprueft:
normalisierte Gleichheit (``_normalisiere_wert`` aus ``eval_quality.py``,
dazu Leerraum/Gross-Klein; Betraege numerisch), kein Teilstring-Vergleich;
Namensfelder ausgenommen. Gezaehlt je Sicherheitsstufe:

* ``richtig``  — Wert stimmt.
* ``falsch``   — Wert steht ohne Marker in der Tabelle, stimmt aber nicht
  (der gefaehrliche Fall: niemand schaut hin).
* ``markiert`` — Pipeline hat den Wert schon auf ``manual_review:*`` gesetzt.
* ``fehlt``    — kein Wert, obwohl einer im Beleg steht.

Zum Vergleich steht dieselbe Auszaehlung fuer die vorhandenen Signale
(``herkunft`` Regel/Modell, ``anchor_valid``) im Bericht.

Nur fuer synthetische Fixtures gedacht. Der Bericht enthaelt wie
``eval_quality.md`` nur Zaehler und Feldnamen, nie Werte.

Aufruf (Ollama noetig; ``make eval-unsicherheit`` erzeugt die Word-JSONs):

    python scripts/eval_unsicherheit.py --samples <word-json-ordner> \\
        --truth-dir evals/expected [--samples ... --truth-dir ...] [--out bericht.md]

``--samples``/``--truth-dir`` duerfen mehrfach stehen (paarweise), etwa fuer
die Betragsvarianten des Modellvergleichs.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Self

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from eval_quality import _NICHT_MATCHBARE_FELDER, _normalisiere_wert

#: Sicherheitsstufen nach kleinster Token-Wahrscheinlichkeit im Wert.
STUFEN: tuple[tuple[str, float, float], ...] = (
    ("p_min ≥ 0.99", 0.99, 1.01),
    ("0.90 ≤ p_min < 0.99", 0.90, 0.99),
    ("0.50 ≤ p_min < 0.90", 0.50, 0.90),
    ("p_min < 0.50", 0.0, 0.50),
)
SCHWELLEN: tuple[float, ...] = (0.5, 0.9, 0.99)
KLASSEN: tuple[str, ...] = ("richtig", "falsch", "markiert", "fehlt")


# ---------------------------------------------------------------------------
# Token-Wahrscheinlichkeiten den Werten zuordnen
# ---------------------------------------------------------------------------


def token_offsets(tokens: list[str], content: str) -> list[tuple[int, int]] | None:
    """Zeichen-Spannen der Tokens in ``content``; None, wenn sie nicht aufgehen.

    Tokens, die ein Mehrbyte-Zeichen zerteilen, kommen von Ollama mitunter
    als Ersatzzeichen zurueck; dann passt die Verkettung nicht zum Inhalt und
    der Beleg wird fuer die Unsicherheit uebersprungen (im Bericht gezaehlt).
    """
    if "".join(tokens) != content:
        return None
    spannen, pos = [], 0
    for tok in tokens:
        spannen.append((pos, pos + len(tok)))
        pos += len(tok)
    return spannen


def _string_ende(content: str, start: int) -> int:
    """Index des schliessenden Anfuehrungszeichens eines JSON-Strings."""
    i = start
    while i < len(content):
        if content[i] == "\\":
            i += 2
            continue
        if content[i] == '"':
            return i
        i += 1
    return -1


def wert_spannen(content: str) -> dict[str, tuple[int, int]]:
    """Zeichen-Spanne des ``value``-Strings je Feld in der Modellantwort.

    Erwartet das TaggedField-Format ``{"feld": {"value": "...", ...}}``.
    Felder mit ``null`` oder ohne String-Wert fehlen im Ergebnis.
    """
    spannen: dict[str, tuple[int, int]] = {}
    for m in re.finditer(r'"(\w+)"\s*:\s*\{', content):
        feld = m.group(1)
        ende_objekt = content.find("}", m.end())
        v = re.compile(r'"value"\s*:\s*"').search(content, m.end(),
                                                  ende_objekt if ende_objekt > 0 else None)
        if not v:
            continue
        ende = _string_ende(content, v.end())
        if ende > v.end():
            spannen[feld] = (v.end(), ende)
    return spannen


def p_min_je_feld(content: str, logprobs: list[tuple[str, float]]) -> dict[str, float] | None:
    """Kleinste Token-Wahrscheinlichkeit je Feldwert; None bei Ausrichtungsfehler."""
    mit_wert = p_min_und_wert_je_feld(content, logprobs)
    if mit_wert is None:
        return None
    return {feld: p for feld, (_, p) in mit_wert.items()}


def p_min_und_wert_je_feld(
    content: str, logprobs: list[tuple[str, float]],
) -> dict[str, tuple[str, float]] | None:
    """Je Feld: (Rohwert in der Antwort, kleinste Token-Wahrscheinlichkeit)."""
    offsets = token_offsets([t for t, _ in logprobs], content)
    if offsets is None:
        return None
    out: dict[str, tuple[str, float]] = {}
    for feld, (a, b) in wert_spannen(content).items():
        lps = [lp for (s, e), (_, lp) in zip(offsets, logprobs) if s < b and e > a]
        if lps:
            out[feld] = (content[a:b], math.exp(min(lps)))
    return out


def p_min_fuer_ergebnis(
    aufrufe: list[tuple[str, list[tuple[str, float]]]],
    fields: list[dict[str, Any]],
) -> tuple[dict[str, float], int]:
    """p_min je Feld aus allen Extraktionsaufrufen eines Belegs.

    Ein Beleg kann mehrere Aufrufe ausloesen (z. B. Wiederholung, mehrere
    Konten). Zaehlt wird der Aufruf, dessen Rohwert dem Endwert entspricht;
    passt keiner (Wert nachbearbeitet), der letzte Aufruf mit diesem Feld.
    Gibt (p_min je Feld, Anzahl ausgerichteter Aufrufe) zurueck.
    """
    kandidaten: dict[str, list[tuple[str, float]]] = {}
    ausgerichtet = 0
    for content, lps in aufrufe:
        je_feld = p_min_und_wert_je_feld(content, lps)
        if je_feld is None:
            continue
        ausgerichtet += 1
        for feld, wert_p in je_feld.items():
            kandidaten.setdefault(feld, []).append(wert_p)
    endwerte = {f.get("feld"): str(f.get("value") or "") for f in fields}
    out: dict[str, float] = {}
    for feld, liste in kandidaten.items():
        ist = endwerte.get(feld, "")
        passend = [p for roh, p in liste if ist and gleicher_wert(roh, ist)]
        out[feld] = passend[-1] if passend else liste[-1][1]
    return out, ausgerichtet


# ---------------------------------------------------------------------------
# Bewertung
# ---------------------------------------------------------------------------


def _vergleichsform(value: str) -> str:
    """Normalisiert wie eval_quality, dazu Leerraum und Gross-/Kleinschreibung."""
    return " ".join(_normalisiere_wert(str(value)).split()).casefold()


def gleicher_wert(soll: str, ist: str) -> bool:
    """Normalisierte Gleichheit; Betraege zusaetzlich numerisch («1250» = «1250.00»).

    Bewusst kein Teilstring-Vergleich wie in eval_quality: dort zaehlte «50.00»
    in «150.00» als richtig (Code-Review 2026-11-19).
    """
    a, b = _vergleichsform(soll), _vergleichsform(ist)
    if a == b:
        return True
    try:
        return Decimal(a) == Decimal(b)
    except InvalidOperation:
        return False


def klasse(soll: str, ist: str) -> str:
    """richtig / falsch / markiert / fehlt — normalisierte Gleichheit."""
    if ist and gleicher_wert(str(soll), str(ist)):
        return "richtig"
    if not ist:
        return "fehlt"
    if str(ist).startswith("manual_review:"):
        return "markiert"
    return "falsch"


def stufe(p: float) -> str:
    for name, lo, hi in STUFEN:
        if lo <= p < hi:
            return name
    return STUFEN[-1][0]


def auroc(werte: list[tuple[float, bool]]) -> float | None:
    """Fläche unter der ROC-Kurve: Unsicherheit (1 - p_min) sagt «nicht richtig» voraus.

    0.5 = Zufall, 1.0 = perfekte Trennung. None, wenn eine Klasse fehlt.
    """
    pos = [1 - p for p, ok in werte if not ok]
    neg = [1 - p for p, ok in werte if ok]
    if not pos or not neg:
        return None
    gewinne = sum((x > y) + 0.5 * (x == y) for x in pos for y in neg)
    return gewinne / (len(pos) * len(neg))


def zeilen_fuer_beleg(res: dict[str, Any], truth: dict[str, Any],
                      p_min: dict[str, float] | None) -> list[dict[str, Any]]:
    """Ein Datensatz je pruefbarem Soll-Wert (ohne Werte, nur Klassen/Signale)."""
    felder = {f.get("feld"): f for f in res.get("fields", [])}
    zeilen = []
    for feld, spec in truth.items():
        if feld.startswith("_") or feld in _NICHT_MATCHBARE_FELDER:
            continue
        soll = spec.get("value") if isinstance(spec, dict) else spec
        if soll in (None, ""):
            continue
        rec = felder.get(feld, {})
        ist = str(rec.get("value") or "")
        zeilen.append({
            "belegtyp": res.get("belegtyp"),
            "feld": feld,
            "klasse": klasse(str(soll), ist),
            "herkunft": rec.get("herkunft") or "–",
            "anchor_valid": bool(rec.get("anchor_valid")),
            "p_min": (p_min or {}).get(feld) if rec.get("herkunft") == "modell" else None,
        })
    return zeilen


# ---------------------------------------------------------------------------
# Lauf
# ---------------------------------------------------------------------------


class _Aufnahme:
    """Faengt ``ollama.chat``-Antworten mit Logprobs ab (nur Extraktion)."""

    def __init__(self) -> None:
        import ollama
        self._ollama = ollama
        self._orig = ollama.chat
        self.aufrufe: list[tuple[str, list[tuple[str, float]]]] = []

    def __enter__(self) -> Self:
        def chat(*args: Any, **kw: Any) -> Any:
            kw["logprobs"] = True
            r = self._orig(*args, **kw)
            fmt = kw.get("format")
            titel = fmt.get("title", "") if isinstance(fmt, dict) else ""
            if titel.endswith("Raw"):
                lps = [(lp.token, lp.logprob) for lp in (r.logprobs or [])]
                self.aufrufe.append((r.message.content or "", lps))
            return r
        self._ollama.chat = chat
        return self

    def __exit__(self, *exc: object) -> None:
        self._ollama.chat = self._orig


def messe(paare: list[tuple[Path, Path]], model: str | None) -> dict[str, Any]:
    from process_samples_full import process_json_sample

    zeilen: list[dict[str, Any]] = []
    belege = ausgerichtet = aufrufe = 0
    with _Aufnahme() as auf:
        for samples, truth_dir in paare:
            for jp in sorted(samples.glob("*.json")):
                if jp.name.startswith("_"):
                    continue
                doc = json.loads(jp.read_text(encoding="utf-8"))
                if "pages" not in doc:
                    continue
                truth_path = truth_dir / f"{Path(doc['pdf_name']).stem}.json"
                if not truth_path.exists():
                    continue
                auf.aufrufe = []
                res = process_json_sample(jp, model=model)
                belege += 1
                p_min, n_ok = p_min_fuer_ergebnis(auf.aufrufe, res.get("fields", []))
                aufrufe += len(auf.aufrufe)
                ausgerichtet += n_ok
                truth = json.loads(truth_path.read_text(encoding="utf-8"))
                zeilen.extend(zeilen_fuer_beleg(res, truth, p_min))
                print(f"  {belege:3d} {res.get('status')} ({res.get('belegtyp')})", flush=True)
    return {"zeilen": zeilen, "belege": belege, "ausgerichtet": ausgerichtet,
            "aufrufe": aufrufe}


def _tabelle(gruppen: dict[str, list[dict[str, Any]]]) -> list[str]:
    out = ["| Gruppe | Werte | richtig | falsch | markiert | fehlt | nicht richtig |",
           "|---|---|---|---|---|---|---|"]
    for name, zs in gruppen.items():
        if not zs:
            continue
        z = {k: sum(r["klasse"] == k for r in zs) for k in KLASSEN}
        nr = len(zs) - z["richtig"]
        out.append(f"| {name} | {len(zs)} | {z['richtig']} | {z['falsch']} | "
                   f"{z['markiert']} | {z['fehlt']} | {nr} ({100 * nr / len(zs):.0f} %) |")
    return out


def bericht(m: dict[str, Any]) -> str:
    zs = m["zeilen"]
    mit_p = [r for r in zs if r["p_min"] is not None]
    out = [
        "# Unsicherheit pro Wert: Kalibrierung",
        "",
        (f"Belege: {m['belege']} · Extraktionsaufrufe: {m.get('aufrufe', '–')} · "
         f"Token-Ausrichtung gelungen: {m['ausgerichtet']} · "
         f"pruefbare Werte: {len(zs)} · davon mit Modell-Unsicherheit: {len(mit_p)}"),
        "",
        "## Modell-Unsicherheit (kleinste Token-Wahrscheinlichkeit im Wert)",
        "",
    ]
    out += _tabelle({name: [r for r in mit_p if stufe(r["p_min"]) == name]
                     for name, _, _ in STUFEN})
    a = auroc([(r["p_min"], r["klasse"] == "richtig") for r in mit_p])
    auroc_text = "–" if a is None else f"{a:.2f}"
    out += ["", (f"AUROC (Unsicherheit sagt «nicht richtig» voraus): {auroc_text} "
                 "(0.5 = Zufall, 1.0 = perfekt)"), ""]
    out += ["### Was eine Anzeige in der Review braeuchte", "",
            ("| Hinweis bei p_min < | angezeigte Werte | davon falsch/markiert/fehlt "
             "| nicht richtig, aber nicht angezeigt | stilles «falsch» erkannt |"),
            "|---|---|---|---|---|"]
    for t in SCHWELLEN:
        an = [r for r in mit_p if r["p_min"] < t]
        treffer = sum(r["klasse"] != "richtig" for r in an)
        verpasst = sum(r["klasse"] != "richtig" for r in mit_p if r["p_min"] >= t)
        still = [r for r in mit_p if r["klasse"] == "falsch"]
        still_an = sum(r["p_min"] < t for r in still)
        out.append(f"| {t} | {len(an)} | {treffer} | {verpasst} | {still_an}/{len(still)} |")
    out += ["", "## Vorhandene Signale zum Vergleich", ""]
    out += _tabelle({
        "Herkunft Regel": [r for r in zs if r["herkunft"] == "regel"],
        "Herkunft Modell, Anker gueltig": [r for r in zs if r["herkunft"] == "modell" and r["anchor_valid"]],
        "Herkunft Modell, kein Anker": [r for r in zs if r["herkunft"] == "modell" and not r["anchor_valid"]],
        "ohne Feld im Ergebnis": [r for r in zs if r["herkunft"] == "–"],
    })
    out += ["", "## Nicht richtige Werte nach Feld", "",
            "| Belegtyp | Feld | Klasse | p_min |", "|---|---|---|---|"]
    for r in sorted(zs, key=lambda r: (str(r["belegtyp"]), r["feld"])):
        if r["klasse"] != "richtig":
            p = "–" if r["p_min"] is None else f"{r['p_min']:.3f}"
            out.append(f"| {r['belegtyp']} | {r['feld']} | {r['klasse']} | {p} |")
    return "\n".join(out) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--samples", type=Path, action="append", required=True,
                    help="Ordner mit Word-JSONs synthetischer Fixtures (mehrfach moeglich)")
    ap.add_argument("--truth-dir", type=Path, action="append", required=True,
                    help="Soll-Werte im Format von evals/expected (paarweise zu --samples)")
    ap.add_argument("--model", default=None, help="Ollama-Modell (Default wie Pipeline)")
    ap.add_argument("--out", type=Path, default=None,
                    help="Bericht (Default: <erstes --samples>/eval_unsicherheit.md)")
    args = ap.parse_args()
    if len(args.samples) != len(args.truth_dir):
        ap.error("--samples und --truth-dir muessen gleich oft angegeben werden")
    m = messe(list(zip(args.samples, args.truth_dir)), args.model)
    text = bericht(m)
    out = args.out or args.samples[0] / "eval_unsicherheit.md"
    out.write_text(text, encoding="utf-8")
    print(text)
    print(f"Bericht geschrieben: {out}")


if __name__ == "__main__":
    main()
