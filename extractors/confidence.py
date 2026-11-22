"""Konfidenz-Komposition und belegtyp-spezifische Plausibility-Checks (Phase 1 + 2).

Per D-B5: LLM-Self-Confidence wird IGNORIERT. Konfidenz wird komponiert aus:
- ``anchor_valid`` (boolean: tag_ref auflösbar zu bbox)
- ``snippet_match`` (Wert-Snippet-Match-Qualität, normalisiert via numbers.py, 0..1)
- ``plausibility_penalty`` (Cross-Field-Plausibility, 0.7..1.0)

Formel: ``confidence = float(anchor_valid) * snippet_match * plausibility_penalty``,
geclampt auf [0, 1].

Phase 2 (Plan 02-04) erweitert ``run_plausibility`` um typ-spezifischen
Dispatch (D-D6, D-C1, D-C5):

* ``lohnausweis``: Phase-1-Logik unverändert (Brutto>Netto, AHV≈5.3%,
  BVG≥0 wenn Brutto≥22'050).
* ``bank_zinsausweis``: VRS ≈ 35 % × Bruttoertrag (±5 %).
* ``kk_praemienbescheinigung``: KVG-Prämie ≥ CHF 1'000 (typische Mindest-
  prämie Erwachsene; CONTEXT §specifics).
* ``saeule_3a``: Einzahlung ≤ Jahr-spezifisches Limit aus
  ``SAEULE_3A_MAX_BY_YEAR`` (2024: 7'056 mit BVG / 35'280 ohne BVG;
  2025: 7'258 / 36'288 — RESEARCH-Korrektur #1).

Phase 3 Wave 3 (Plan 03-04) erweitert ``run_plausibility`` um drei weitere
Belegtypen (D-A1, D-E1, D-E2):

* ``wertschriftenverzeichnis``: ``verrechnungssteuer_total ≈ 35 % ×
  bruttoertrag_total`` (±5 %) — gleiche VRS-Quote wie Bank-Zinsausweis.
  Verletzung → ``PENALTY_MILD`` auf ``verrechnungssteuer_total``.
* ``spenden``: keine Plausibility (Spendenbeträge sind frei, jede Höhe
  möglich — CONTEXT §specifics). Explizites No-Op statt Default-Pfad,
  damit die Absicht im Code dokumentiert ist.
* ``berufsauslagen``: keine Plausibility (Kurs-/Weiterbildungskosten
  sind frei). Explizites No-Op analog Spenden.

Phase 3 Wave 4 (Plan 03-05) erweitert ``run_plausibility`` um vier weitere
Belegtypen (D-E3, D-E4, D-E5, D-E6):

* ``kinderbetreuung``: ``betrag ≤ KINDERBETREUUNG_MAX_ZH`` (CHF 25'000
  ZH 2024 Cap pro Kind). Überschreitung → ``PENALTY_MILD`` auf ``betrag``.
* ``hypothek_zinsbestaetigung``: ``schuldzinsen / schuldsaldo_3112 ∈
  [HYPOTHEK_ZINS_RATIO_MIN, HYPOTHEK_ZINS_RATIO_MAX]`` (0.5%–10% — weiter
  Range für Mischhypothek-Edge-Cases, Pitfall 8 RESEARCH). Ausserhalb →
  ``PENALTY_MILD`` auf ``schuldzinsen``.
* ``liegenschaftsunterhalt``: keine Plausibility (jeder Betrag möglich) —
  No-Op.
* ``krankheitskosten``: keine Plausibility in v1 (Selbstbehalt 5%
  Nettoeinkommen ist Cross-Beleg → v2 deferred) — No-Op.

Zusätzlich exportiert das Modul ``classify_werterhaltend(text) -> bool | None``
als Regex-Heuristik (Claude's Discretion, CONTEXT §D-E5) für die
Pipeline-Integration in Plan 03-06: "Reparatur/Erhaltung/Renovation/Service/
Wartung/Instandhaltung" → True, "Anbau/Neubau/Umbau/Erweiterung/Ausbau" →
False, beide oder keiner → None.

3a-Limit-Penalty bei Überschreitung — KEIN Block (D-C5).
``year=None`` ⇒ kein Limit-Check (lieber unterspezifiziert als falsch).
"""
from __future__ import annotations

import re
from decimal import Decimal

from extractors.schema import Belegtyp

# ---------------------------------------------------------------------------
# Phase-1-Konstanten (Lohnausweis).
# ---------------------------------------------------------------------------
AHV_RATE = Decimal("0.053")          # AHV/IV/EO 2025 (Arbeitnehmer-Anteil)
AHV_TOLERANCE = Decimal("0.05")       # ±5 % Toleranz
BVG_THRESHOLD = Decimal("22050")      # BVG-Eintrittsschwelle 2024

# ---------------------------------------------------------------------------
# Phase-2-Konstanten.
# ---------------------------------------------------------------------------

#: Bank-Zinsausweis: schweizerische Verrechnungssteuer (VRS) auf Zinserträge.
BANK_VRS_RATE = Decimal("0.35")
BANK_VRS_TOLERANCE = Decimal("0.05")

#: KK: typische Mindestjahresprämie Grundversicherung für Erwachsene
#: (CONTEXT §specifics — 1'000 CHF als untere Plausibility-Grenze).
KK_KVG_MIN_THRESHOLD = Decimal("1000")

#: Säule-3a-Maximaleinzahlungen (RESEARCH-Korrektur #1; ESTV-verifiziert).
#: ``mit_bvg``: Erwerbstätige mit 2. Säule. ``ohne_bvg``: Selbständige
#: ohne BVG (max. 20 % vom Erwerbseinkommen, gedeckelt).
SAEULE_3A_MAX_BY_YEAR: dict[int, dict[str, int]] = {
    2024: {"mit_bvg": 7056, "ohne_bvg": 35280},
    2025: {"mit_bvg": 7258, "ohne_bvg": 36288},
}

# ---------------------------------------------------------------------------
# Phase-3-Wave-4-Konstanten (Plan 03-05).
# ---------------------------------------------------------------------------

#: Kinderbetreuung-Cap (ZH 2024, D-E3). Pro Kind im Jahr maximal CHF 25'000
#: abziehbar; Überschreitung → PENALTY_MILD (kein Block).
KINDERBETREUUNG_MAX_ZH = Decimal("25000")

#: Hypothekarzins-Plausibility-Range (Pitfall 8 RESEARCH — weiter Range,
#: deckt Mischhypothek- und Anteils-Edge-Cases ab). Verletzung → PENALTY_MILD.
HYPOTHEK_ZINS_RATIO_MIN = Decimal("0.005")  # 0.5 %
HYPOTHEK_ZINS_RATIO_MAX = Decimal("0.10")   # 10 %

#: Regex-Heuristik für werterhaltend (Liegenschaftsunterhalt, D-E5).
#: "Werterhaltend" = Reparatur, Erhaltung, Renovation, Service, Wartung,
#: Instandhaltung — wirkt steuerlich abziehbar (Pauschale 20% vs. effektiv).
#: WR-04 (Code-Review 03-06): Wort-Grenzen ``\b`` erforderlich, weil der
#: Haystack der kompletten Seite-1-Text ist und Substring-Matches sonst in
#: Footer/Datenschutz/Adressen kippen ("Aus**bau**stufe", "**Service**-Hotline",
#: "Daten**erhaltung**"). Falsche True/False-Klassifikation ist steuerlich
#: problematischer als ein ehrliches ``None``.
WERTERHALTEND_REGEX = re.compile(
    r"\b(reparatur|erhaltung|renovation|service|wartung|instandhaltung)\b",
    re.IGNORECASE,
)
#: "Wertvermehrend" = Anbau, Neubau, Umbau, Erweiterung, Ausbau — NICHT
#: abziehbar als Unterhalt (führt zu Anlagekosten).
WERTVERMEHREND_REGEX = re.compile(
    r"\b(anbau|neubau|umbau|erweiterung|ausbau)\b",
    re.IGNORECASE,
)

# Penalty-Stufen.
PENALTY_OK = 1.0
PENALTY_MILD = 0.85
PENALTY_HARD = 0.7


def compose_confidence(
    anchor_valid: bool,
    snippet_match: float,
    plausibility_penalty: float,
) -> float:
    """Komponiert die finale Feld-Konfidenz.

    Formel: ``float(anchor_valid) * snippet_match * plausibility_penalty``,
    geclampt auf [0, 1].
    """
    raw = float(anchor_valid) * snippet_match * plausibility_penalty
    return max(0.0, min(1.0, raw))


# ---------------------------------------------------------------------------
# Belegtyp-spezifische Plausibility-Sub-Funktionen.
# ---------------------------------------------------------------------------


def _run_plausibility_lohnausweis(
    extracted: dict[str, Decimal | None],
) -> dict[str, float]:
    """Phase-1-Lohnausweis-Plausibility (UNVERÄNDERT seit Plan 01).

    Regeln:
    1. ``bruttolohn_pos8 > nettolohn_pos11`` — Verletzung → 0.7 für beide.
    2. ``ahv_alv_nbu_abzug_pos9 ≈ bruttolohn_pos8 × 0.053`` (±5 %) → 0.85.
    3. ``bvg_abzug_pos10a > 0`` falls Brutto ≥ Schwelle → 0.85.
    """
    penalties: dict[str, float] = {k: PENALTY_OK for k in extracted}
    b = extracted.get("bruttolohn_pos8")
    n = extracted.get("nettolohn_pos11")
    a = extracted.get("ahv_alv_nbu_abzug_pos9")
    v = extracted.get("bvg_abzug_pos10a")

    if b is not None and n is not None and b <= n:
        penalties["bruttolohn_pos8"] = PENALTY_HARD
        penalties["nettolohn_pos11"] = PENALTY_HARD

    if b is not None and a is not None and b > 0:
        expected_ahv = b * AHV_RATE
        ratio_diff = abs(a - expected_ahv) / expected_ahv
        if ratio_diff > AHV_TOLERANCE:
            penalties["ahv_alv_nbu_abzug_pos9"] = min(
                penalties["ahv_alv_nbu_abzug_pos9"], PENALTY_MILD
            )

    if b is not None and b >= BVG_THRESHOLD and v is not None and v <= 0:
        penalties["bvg_abzug_pos10a"] = min(
            penalties["bvg_abzug_pos10a"], PENALTY_MILD
        )

    return penalties


def _run_plausibility_bank(
    extracted: dict[str, Decimal | None],
) -> dict[str, float]:
    """Bank-Zinsausweis: VRS ≈ 35 % × Bruttoertrag (±5 %).

    Verletzung → ``PENALTY_MILD`` für ``verrechnungssteuer``.
    """
    penalties: dict[str, float] = {k: PENALTY_OK for k in extracted}
    brutto = extracted.get("bruttoertrag")
    vrs = extracted.get("verrechnungssteuer")

    if brutto is not None and vrs is not None and brutto > 0:
        expected = brutto * BANK_VRS_RATE
        ratio_diff = abs(vrs - expected) / expected
        if ratio_diff > BANK_VRS_TOLERANCE:
            penalties["verrechnungssteuer"] = PENALTY_MILD

    return penalties


def _run_plausibility_kk(
    extracted: dict[str, Decimal | None],
) -> dict[str, float]:
    """KK-Prämienbescheinigung: KVG-Prämie ≥ ``KK_KVG_MIN_THRESHOLD``.

    Schutz gegen offensichtliche Fehl-Extraktionen (z.B. Monats- statt
    Jahres-Prämie). Verletzung → ``PENALTY_MILD``.
    """
    penalties: dict[str, float] = {k: PENALTY_OK for k in extracted}
    kvg = extracted.get("praemie_kvg_total")
    if kvg is not None and kvg < KK_KVG_MIN_THRESHOLD:
        penalties["praemie_kvg_total"] = PENALTY_MILD
    return penalties


def _run_plausibility_saeule_3a(
    extracted: dict[str, Decimal | None],
    year: int | None,
) -> dict[str, float]:
    """Säule 3a: Einzahlung ≤ Jahr-Limit (mit BVG; D-C5).

    Penalty bei Überschreitung — kein Block. ``year=None`` oder
    Jahr ausserhalb der Map → kein Penalty (bewusst keine Annahme).
    """
    penalties: dict[str, float] = {k: PENALTY_OK for k in extracted}
    einzahlung = extracted.get("einzahlung_betrag")
    if einzahlung is None or year is None or year not in SAEULE_3A_MAX_BY_YEAR:
        return penalties

    limit_mit_bvg = Decimal(SAEULE_3A_MAX_BY_YEAR[year]["mit_bvg"])
    if einzahlung > limit_mit_bvg:
        penalties["einzahlung_betrag"] = PENALTY_MILD
    return penalties


# ---------------------------------------------------------------------------
# Phase-3-Wave-3-Sub-Funktionen (Plan 03-04).
# ---------------------------------------------------------------------------


def _run_plausibility_wertschriften(
    extracted: dict[str, Decimal | None],
) -> dict[str, float]:
    """Wertschriftenverzeichnis: VRS ≈ 35 % × Bruttoertrag (±5 %).

    Analog Bank-Zinsausweis — die Schweizer Verrechnungssteuer beträgt
    35 % auf inländische Kapitalerträge. Verletzung → ``PENALTY_MILD`` auf
    ``verrechnungssteuer_total``. Plan 03-04 (D-A1).
    """
    penalties: dict[str, float] = {k: PENALTY_OK for k in extracted}
    brutto = extracted.get("bruttoertrag_total")
    vrs = extracted.get("verrechnungssteuer_total")

    if brutto is not None and vrs is not None and brutto > 0:
        expected = brutto * BANK_VRS_RATE
        ratio_diff = abs(vrs - expected) / expected
        if ratio_diff > BANK_VRS_TOLERANCE:
            penalties["verrechnungssteuer_total"] = PENALTY_MILD

    return penalties


def _run_plausibility_no_op(
    extracted: dict[str, Decimal | None],
) -> dict[str, float]:
    """No-Op-Plausibility für Spenden und Berufsauslagen (Plan 03-04).

    Spendenbeträge und Kurs-/Weiterbildungskosten sind frei — keine
    Cross-Field-Regel ableitbar (CONTEXT §specifics). Liefert für alle
    Felder ``PENALTY_OK``. Explizit ausgeschrieben, damit der Default-
    Pfad (unbekannter Belegtyp) klar vom bewussten No-Op unterscheidbar
    bleibt.
    """
    return {k: PENALTY_OK for k in extracted}


# ---------------------------------------------------------------------------
# Phase-3-Wave-4-Sub-Funktionen (Plan 03-05).
# ---------------------------------------------------------------------------


def _run_plausibility_kinderbetreuung(
    extracted: dict[str, Decimal | None],
) -> dict[str, float]:
    """Kinderbetreuung: ``betrag ≤ KINDERBETREUUNG_MAX_ZH`` (ZH 2024 Cap).

    Überschreitung → ``PENALTY_MILD`` auf ``betrag``. Plan 03-05 (D-E3).
    """
    penalties: dict[str, float] = {k: PENALTY_OK for k in extracted}
    b = extracted.get("betrag")
    if b is not None and b > KINDERBETREUUNG_MAX_ZH:
        penalties["betrag"] = PENALTY_MILD
    return penalties


def _run_plausibility_hypothek(
    extracted: dict[str, Decimal | None],
) -> dict[str, float]:
    """Hypothek: ``schuldzinsen / schuldsaldo_3112 ∈ [0.005, 0.10]``.

    Pitfall 8 RESEARCH: weiter Range für Mischhypothek-Edge-Cases.
    Verletzung → ``PENALTY_MILD`` auf ``schuldzinsen``. Plan 03-05 (D-E4).
    """
    penalties: dict[str, float] = {k: PENALTY_OK for k in extracted}
    z = extracted.get("schuldzinsen")
    s = extracted.get("schuldsaldo_3112")
    if z is not None and s is not None and s > 0:
        ratio = z / s
        if ratio < HYPOTHEK_ZINS_RATIO_MIN or ratio > HYPOTHEK_ZINS_RATIO_MAX:
            penalties["schuldzinsen"] = PENALTY_MILD
    return penalties


def classify_werterhaltend(text: str) -> bool | None:
    """Regex-Heuristik: Liegenschaftsunterhalt-Werterhaltend-Klassifikation.

    Plan 03-05 (D-E5, CONTEXT §Claude's Discretion). Wird in Plan 03-06
    nachgelagert vom Pipeline auf ``handwerker_anbieter`` + Seite-1-Text
    angewendet, um :attr:`LiegenschaftsunterhaltRaw.werterhaltend` zu
    befüllen — LLM lässt das Feld null.

    Entscheidungs-Logik (3-Branch):

    * Nur ``WERTERHALTEND_REGEX`` matched → ``True``
      (Reparatur/Erhaltung/Renovation/Service/Wartung/Instandhaltung).
    * Nur ``WERTVERMEHREND_REGEX`` matched → ``False``
      (Anbau/Neubau/Umbau/Erweiterung/Ausbau).
    * Beide oder keiner matched → ``None``
      (Heuristik-Unsicherheit; Pipeline schreibt Report-Warnung).

    Args:
        text: zu klassifizierender Beschreibungstext.

    Returns:
        ``True`` / ``False`` / ``None`` gemäss obiger Logik.
    """
    we = bool(WERTERHALTEND_REGEX.search(text))
    wv = bool(WERTVERMEHREND_REGEX.search(text))
    if we and not wv:
        return True
    if wv and not we:
        return False
    return None


# ---------------------------------------------------------------------------
# Öffentlicher Dispatcher.
# ---------------------------------------------------------------------------


def run_plausibility(
    belegtyp: Belegtyp | str,
    extracted: dict[str, Decimal | None],
    year: int | None = None,
) -> dict[str, float]:
    """Berechnet pro Feld einen ``plausibility_penalty`` in [0.7, 1.0].

    Dispatcht nach ``belegtyp``:

    * ``lohnausweis`` → Phase-1-Logik (Brutto/Netto, AHV, BVG).
    * ``bank_zinsausweis`` → VRS ≈ 35 % × Brutto.
    * ``kk_praemienbescheinigung`` → KVG ≥ 1'000.
    * ``saeule_3a`` → Einzahlung ≤ Jahr-Limit aus ``SAEULE_3A_MAX_BY_YEAR``.
    * Unbekannt → alle Felder ``PENALTY_OK``.

    None-Werte und fehlende Felder überspringen die jeweilige Regel ohne
    Penalty.

    Args:
        belegtyp: Belegtyp aus ``extractors.classifier.classify``.
        extracted: Dict ``feldname → Decimal | None``.
        year: Steuerjahr (nur für 3a-Limit-Map relevant).
    Returns:
        Dict mit demselben Key-Set, Wert = Penalty (Default 1.0).
    """
    if belegtyp == "lohnausweis":
        return _run_plausibility_lohnausweis(extracted)
    if belegtyp == "bank_zinsausweis":
        return _run_plausibility_bank(extracted)
    if belegtyp == "kk_praemienbescheinigung":
        return _run_plausibility_kk(extracted)
    if belegtyp == "saeule_3a":
        return _run_plausibility_saeule_3a(extracted, year)
    if belegtyp == "wertschriftenverzeichnis":
        return _run_plausibility_wertschriften(extracted)
    if belegtyp in ("spenden", "berufsauslagen"):
        return _run_plausibility_no_op(extracted)
    if belegtyp == "kinderbetreuung":
        return _run_plausibility_kinderbetreuung(extracted)
    if belegtyp == "hypothek_zinsbestaetigung":
        return _run_plausibility_hypothek(extracted)
    if belegtyp in ("liegenschaftsunterhalt", "krankheitskosten"):
        return _run_plausibility_no_op(extracted)
    return {k: PENALTY_OK for k in extracted}
