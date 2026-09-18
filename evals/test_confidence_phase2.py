"""Phase-2-Tests für extractors/confidence.py — typ-spezifischer Dispatch.

Deckt D-C1, D-C5 (02-CONTEXT.md) sowie RESEARCH-Korrektur #1 (3a-Limit
via Jahr-Map: 2024=7056, 2025=7258).
"""
from __future__ import annotations

from decimal import Decimal

from extractors.confidence import (
    BANK_VRS_RATE,
    BANK_VRS_TOLERANCE,
    KK_KVG_MIN_THRESHOLD,
    PENALTY_MILD,
    PENALTY_OK,
    SAEULE_3A_MAX_BY_YEAR,
    run_plausibility,
)


# ---------------------------------------------------------------------------
# (a) Phase-1-Lohnausweis-Verhalten unverändert (Spiegel-Tests).
# ---------------------------------------------------------------------------


def test_plausibility_lohn_all_ok():
    p = run_plausibility(
        "lohnausweis",
        {
            "bruttolohn_pos8": Decimal("95400.00"),
            "nettolohn_pos11": Decimal("79500.00"),
            "ahv_alv_nbu_abzug_pos9": Decimal("5056.20"),
            "bvg_abzug_pos10a": Decimal("10843.80"),
        },
    )
    for f, v in p.items():
        assert v == PENALTY_OK, f"{f}: {v}"


def test_plausibility_lohn_brutto_lt_netto():
    p = run_plausibility(
        "lohnausweis",
        {
            "bruttolohn_pos8": Decimal("50000"),
            "nettolohn_pos11": Decimal("70000"),
            "ahv_alv_nbu_abzug_pos9": Decimal("2650"),
            "bvg_abzug_pos10a": Decimal("5000"),
        },
    )
    assert p["bruttolohn_pos8"] == 0.7
    assert p["nettolohn_pos11"] == 0.7


# ---------------------------------------------------------------------------
# (b)+(c) Bank-Zinsausweis: VRS ≈ 35% × bruttoertrag ±5 %.
# ---------------------------------------------------------------------------


def test_plausibility_bank_vrs_exact():
    p = run_plausibility(
        "bank_zinsausweis",
        {
            "bruttoertrag": Decimal("1250"),
            "verrechnungssteuer": Decimal("437.50"),
        },
    )
    for f, v in p.items():
        assert v == PENALTY_OK, f"{f}: {v}"


def test_plausibility_bank_vrs_off():
    p = run_plausibility(
        "bank_zinsausweis",
        {
            "bruttoertrag": Decimal("1250"),
            "verrechnungssteuer": Decimal("400"),
        },
    )
    assert p["verrechnungssteuer"] == PENALTY_MILD


# ---------------------------------------------------------------------------
# (d)+(e) KK-Prämienbescheinigung: praemie_kvg_total ≥ 1000.
# ---------------------------------------------------------------------------


def test_plausibility_kk_above_threshold():
    p = run_plausibility(
        "kk_praemienbescheinigung",
        {"praemie_kvg_total": Decimal("4200")},
    )
    assert p["praemie_kvg_total"] == PENALTY_OK


def test_plausibility_kk_below_threshold():
    p = run_plausibility(
        "kk_praemienbescheinigung",
        {"praemie_kvg_total": Decimal("500")},
    )
    assert p["praemie_kvg_total"] == PENALTY_MILD


# ---------------------------------------------------------------------------
# (f)–(i) Säule 3a: Jahr-Map 2024=7056, 2025=7258.
# ---------------------------------------------------------------------------


def test_plausibility_3a_2024_at_limit():
    p = run_plausibility(
        "saeule_3a",
        {"einzahlung_betrag": Decimal("7056")},
        year=2024,
    )
    assert p["einzahlung_betrag"] == PENALTY_OK


def test_plausibility_3a_2024_over_limit():
    p = run_plausibility(
        "saeule_3a",
        {"einzahlung_betrag": Decimal("8000")},
        year=2024,
    )
    assert p["einzahlung_betrag"] == PENALTY_MILD


def test_plausibility_3a_2025_at_limit():
    p = run_plausibility(
        "saeule_3a",
        {"einzahlung_betrag": Decimal("7258")},
        year=2025,
    )
    assert p["einzahlung_betrag"] == PENALTY_OK


def test_plausibility_3a_2025_over_limit():
    p = run_plausibility(
        "saeule_3a",
        {"einzahlung_betrag": Decimal("7300")},
        year=2025,
    )
    assert p["einzahlung_betrag"] == PENALTY_MILD


# (j) year=None → kein Limit-Penalty.
def test_plausibility_3a_year_none_no_penalty():
    p = run_plausibility(
        "saeule_3a",
        {"einzahlung_betrag": Decimal("99999")},
        year=None,
    )
    assert p["einzahlung_betrag"] == PENALTY_OK


# (k) Unbekannter Belegtyp → alle Felder PENALTY_OK.
def test_plausibility_unknown_belegtyp():
    p = run_plausibility("unknown", {"egal": Decimal("1")})
    for f, v in p.items():
        assert v == PENALTY_OK, f"{f}: {v}"


# ---------------------------------------------------------------------------
# Konstanten-Verifikation (RESEARCH-Korrektur #1).
# ---------------------------------------------------------------------------


def test_saeule_3a_max_by_year_constants():
    assert SAEULE_3A_MAX_BY_YEAR[2024]["mit_bvg"] == 7056
    assert SAEULE_3A_MAX_BY_YEAR[2024]["ohne_bvg"] == 35280
    assert SAEULE_3A_MAX_BY_YEAR[2025]["mit_bvg"] == 7258
    assert SAEULE_3A_MAX_BY_YEAR[2025]["ohne_bvg"] == 36288


def test_bank_constants():
    assert BANK_VRS_RATE == Decimal("0.35")
    assert BANK_VRS_TOLERANCE == Decimal("0.05")


def test_kk_constants():
    assert KK_KVG_MIN_THRESHOLD == Decimal("1000")
