"""Unit-Tests für extractors/confidence.py.

Deterministisch (keine I/O, kein LLM). Deckt compose_confidence-Formel
und die drei Phase-1-Plausibility-Regeln (D-B5).
"""
from decimal import Decimal

from extractors.confidence import compose_confidence, run_plausibility


def test_compose_anchor_valid_full():
    assert compose_confidence(True, 1.0, 1.0) == 1.0


def test_compose_anchor_invalid_zero():
    assert compose_confidence(False, 1.0, 1.0) == 0.0


def test_compose_partial_match():
    assert abs(compose_confidence(True, 0.5, 1.0) - 0.5) < 1e-9


def test_compose_penalty():
    assert abs(compose_confidence(True, 1.0, 0.7) - 0.7) < 1e-9


def test_compose_clamped_to_unit_interval():
    # Defensive: keine negativen oder >1 Werte selbst bei pathologischen Inputs.
    assert compose_confidence(True, 1.0, 1.5) == 1.0
    assert compose_confidence(True, 1.0, -0.5) == 0.0


def test_plausibility_all_ok():
    penalties = run_plausibility(
        "lohnausweis",
        {
            "bruttolohn_pos8": Decimal("95400.00"),
            "nettolohn_pos11": Decimal("79500.00"),
            "ahv_alv_nbu_abzug_pos9": Decimal("5056.20"),  # ≈ 95400 * 0.053
            "bvg_abzug_pos10a": Decimal("10843.80"),
        }
    )
    for f, p in penalties.items():
        assert p == 1.0, f"{f}: {p}"


def test_plausibility_brutto_lt_netto():
    penalties = run_plausibility(
        "lohnausweis",
        {
            "bruttolohn_pos8": Decimal("50000"),
            "nettolohn_pos11": Decimal("70000"),  # netto > brutto, Verletzung
            "ahv_alv_nbu_abzug_pos9": Decimal("2650"),
            "bvg_abzug_pos10a": Decimal("5000"),
        }
    )
    assert penalties["bruttolohn_pos8"] == 0.7
    assert penalties["nettolohn_pos11"] == 0.7


def test_plausibility_ahv_off():
    penalties = run_plausibility(
        "lohnausweis",
        {
            "bruttolohn_pos8": Decimal("100000"),
            "nettolohn_pos11": Decimal("80000"),
            "ahv_alv_nbu_abzug_pos9": Decimal("10000"),  # 10% statt 5.3%
            "bvg_abzug_pos10a": Decimal("10000"),
        }
    )
    assert penalties["ahv_alv_nbu_abzug_pos9"] == 0.85


def test_plausibility_bvg_zero_above_threshold():
    penalties = run_plausibility(
        "lohnausweis",
        {
            "bruttolohn_pos8": Decimal("95400"),
            "nettolohn_pos11": Decimal("80000"),
            "ahv_alv_nbu_abzug_pos9": Decimal("5056.20"),
            "bvg_abzug_pos10a": Decimal("0"),
        }
    )
    assert penalties["bvg_abzug_pos10a"] == 0.85


def test_plausibility_bvg_none_below_threshold():
    penalties = run_plausibility(
        "lohnausweis",
        {
            "bruttolohn_pos8": Decimal("18000"),  # unter BVG-Schwelle
            "nettolohn_pos11": Decimal("16000"),
            "ahv_alv_nbu_abzug_pos9": Decimal("954"),
            "bvg_abzug_pos10a": None,
        }
    )
    assert penalties.get("bvg_abzug_pos10a", 1.0) == 1.0


def test_plausibility_with_none_values():
    # None-Werte (z.B. quellensteuer fehlt) dürfen keine Penalties auslösen.
    penalties = run_plausibility(
        "lohnausweis",
        {
            "bruttolohn_pos8": Decimal("95400"),
            "nettolohn_pos11": Decimal("79500"),
            "ahv_alv_nbu_abzug_pos9": Decimal("5056.20"),
            "bvg_abzug_pos10a": Decimal("10843.80"),
            "quellensteuer_pos12": None,
        }
    )
    assert penalties["quellensteuer_pos12"] == 1.0
