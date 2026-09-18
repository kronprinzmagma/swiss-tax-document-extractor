"""Tests für ``extractors.aussteller`` (Plan 03-03, Wave 2).

Deckt den Lifecycle der ``aussteller.json``-Map ab: load (tolerant gegen
fehlende/korrupte Dateien), update (idempotent, ``last_seen_year =
max(prev, current)``, D-B5), find_missing (Vorjahres-Filter D-B6) und
atomarer Write via ``.bak``-Pattern (Pitfall 6, 03-RESEARCH.md).

Reine Unit-Tests, KEIN llm_full-Marker — laufen im Default-Suite-Lauf.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from extractors.aussteller import (
    AusstellerEntry,
    find_missing,
    load,
    make_fingerprint,
    make_key,
    update,
    write_json,
)


# ---------------------------------------------------------------------------
# make_key (D-B3)
# ---------------------------------------------------------------------------


def test_make_key_normalizes() -> None:
    """Apostroph + NBSP + Whitespace + Case werden via ``normalize`` entfernt.

    ``"ZKB Zürcher Kantonalbank"`` (mit regulären Spaces) muss nach
    Normalisierung lower-case ohne Whitespace sein und mit dem Belegtyp
    via Doppel-Underscore verbunden werden.
    """
    key = make_key("ZKB Zürcher Kantonalbank", "bank_zinsausweis")
    assert "__" in key
    assert key == key.lower()
    assert " " not in key
    # Belegtyp-Suffix bleibt erhalten
    assert key.endswith("__bank_zinsausweis")


def test_make_key_different_belegtyp_different_key() -> None:
    """Selber Aussteller-Name + zwei Belegtypen → zwei verschiedene Keys."""
    k1 = make_key("Helsana", "kk_praemienbescheinigung")
    k2 = make_key("Helsana", "spenden")
    assert k1 != k2


def test_make_key_apostroph_variants_collapse() -> None:
    """U+0027 vs. U+2019 vs. U+2018 → identischer Key (Pitfall 2 normalize)."""
    k1 = make_key("L'Oréal", "spenden")
    k2 = make_key("L’Oréal", "spenden")
    k3 = make_key("L‘Oréal", "spenden")
    assert k1 == k2 == k3


# ---------------------------------------------------------------------------
# make_fingerprint (D-B4)
# ---------------------------------------------------------------------------


def test_fingerprint_stable() -> None:
    """Selbe Inputs → selber Fingerprint (deterministisch via sha256)."""
    fp1 = make_fingerprint("UBS", "bank_zinsausweis", 2024)
    fp2 = make_fingerprint("UBS", "bank_zinsausweis", 2024)
    assert fp1 == fp2
    assert len(fp1) == 12


def test_fingerprint_different_year() -> None:
    """Unterschiedliches Jahr → unterschiedlicher Fingerprint."""
    fp_2024 = make_fingerprint("UBS", "bank_zinsausweis", 2024)
    fp_2025 = make_fingerprint("UBS", "bank_zinsausweis", 2025)
    assert fp_2024 != fp_2025


# ---------------------------------------------------------------------------
# load — Korruption-Tolerance (D-B-Pitfall 6)
# ---------------------------------------------------------------------------


def test_load_missing_file(tmp_path: Path) -> None:
    """Fehlende Datei → leere Map + kein Warning."""
    history, warning = load(tmp_path / "aussteller.json")
    assert history == {}
    assert warning is None


def test_load_corrupt_file(tmp_path: Path) -> None:
    """Invalides JSON → leere Map + Warning-String (kein Crash)."""
    path = tmp_path / "aussteller.json"
    path.write_text("{ this is not valid JSON ", encoding="utf-8")
    history, warning = load(path)
    assert history == {}
    assert warning is not None
    assert "aussteller.json" in warning.lower() or "korrupt" in warning.lower()


def test_load_valid_roundtrip(tmp_path: Path) -> None:
    """Roundtrip via ``write_json`` → ``load`` liefert dieselben Daten."""
    path = tmp_path / "aussteller.json"
    original: dict[str, AusstellerEntry] = {
        "ubs__bank_zinsausweis": {
            "aussteller_name": "UBS",
            "belegtyp": "bank_zinsausweis",
            "last_seen_year": 2024,
            "fingerprint": make_fingerprint("UBS", "bank_zinsausweis", 2024),
        }
    }
    write_json(path, original)
    loaded, warning = load(path)
    assert warning is None
    assert loaded == original


# ---------------------------------------------------------------------------
# update (D-B5)
# ---------------------------------------------------------------------------


def test_update_new_entry() -> None:
    """Leere History → Update legt 1 Eintrag mit korrektem Key/Fingerprint an."""
    history: dict[str, AusstellerEntry] = {}
    result = update(history, "UBS", "bank_zinsausweis", 2024)
    expected_key = make_key("UBS", "bank_zinsausweis")
    assert expected_key in result
    entry = result[expected_key]
    assert entry["aussteller_name"] == "UBS"
    assert entry["belegtyp"] == "bank_zinsausweis"
    assert entry["last_seen_year"] == 2024
    assert entry["fingerprint"] == make_fingerprint("UBS", "bank_zinsausweis", 2024)


def test_update_idempotent_year_max_ascending() -> None:
    """Zweimal updaten mit 2023 dann 2024 → ``last_seen_year=2024``."""
    history: dict[str, AusstellerEntry] = {}
    update(history, "UBS", "bank_zinsausweis", 2023)
    update(history, "UBS", "bank_zinsausweis", 2024)
    key = make_key("UBS", "bank_zinsausweis")
    assert history[key]["last_seen_year"] == 2024


def test_update_year_max_descending() -> None:
    """Zweimal updaten mit 2024 dann 2023 → ``last_seen_year=2024`` (max-Regel D-B5)."""
    history: dict[str, AusstellerEntry] = {}
    update(history, "UBS", "bank_zinsausweis", 2024)
    update(history, "UBS", "bank_zinsausweis", 2023)
    key = make_key("UBS", "bank_zinsausweis")
    assert history[key]["last_seen_year"] == 2024


# ---------------------------------------------------------------------------
# find_missing (D-B6)
# ---------------------------------------------------------------------------


def test_find_missing_excludes_seen() -> None:
    """Aussteller im current-Lauf gesehen → NICHT in missing."""
    history: dict[str, AusstellerEntry] = {}
    update(history, "UBS", "bank_zinsausweis", 2023)
    seen = {make_key("UBS", "bank_zinsausweis")}
    missing = find_missing(history, seen, current_year=2024)
    assert missing == []


def test_find_missing_includes_last_year() -> None:
    """Aussteller mit ``last_seen_year = current_year - 1`` nicht gesehen → IN missing."""
    history: dict[str, AusstellerEntry] = {}
    update(history, "Helsana", "kk_praemienbescheinigung", 2023)
    missing = find_missing(history, seen_keys=set(), current_year=2024)
    assert len(missing) == 1
    assert missing[0]["aussteller_name"] == "Helsana"
    assert missing[0]["belegtyp"] == "kk_praemienbescheinigung"


def test_find_missing_excludes_too_old() -> None:
    """Aussteller mit ``last_seen_year = current_year - 2`` (verjährt) → NICHT in missing."""
    history: dict[str, AusstellerEntry] = {}
    update(history, "AltBank", "bank_zinsausweis", 2022)
    missing = find_missing(history, seen_keys=set(), current_year=2024)
    assert missing == []


def test_find_missing_sorted_output() -> None:
    """Output ist sortiert nach (belegtyp, aussteller_name) für stabilen Report."""
    history: dict[str, AusstellerEntry] = {}
    update(history, "ZKB", "bank_zinsausweis", 2023)
    update(history, "UBS", "bank_zinsausweis", 2023)
    update(history, "Helsana", "kk_praemienbescheinigung", 2023)
    missing = find_missing(history, seen_keys=set(), current_year=2024)
    # bank_zinsausweis < kk_praemienbescheinigung; innerhalb bank: UBS < ZKB
    assert [m["aussteller_name"] for m in missing] == ["UBS", "ZKB", "Helsana"]


# ---------------------------------------------------------------------------
# write_json — atomarer Write mit .bak (Pitfall 6)
# ---------------------------------------------------------------------------


def test_write_atomic_creates_bak(tmp_path: Path) -> None:
    """Wenn Original existiert, wird vor dem Schreiben eine ``.bak``-Kopie angelegt."""
    path = tmp_path / "aussteller.json"
    first: dict[str, AusstellerEntry] = {
        "ubs__bank_zinsausweis": {
            "aussteller_name": "UBS",
            "belegtyp": "bank_zinsausweis",
            "last_seen_year": 2023,
            "fingerprint": make_fingerprint("UBS", "bank_zinsausweis", 2023),
        }
    }
    write_json(path, first)
    # Zweiter Write mit anderem Inhalt → .bak muss alten Inhalt enthalten
    second: dict[str, AusstellerEntry] = {
        "ubs__bank_zinsausweis": {
            "aussteller_name": "UBS",
            "belegtyp": "bank_zinsausweis",
            "last_seen_year": 2024,
            "fingerprint": make_fingerprint("UBS", "bank_zinsausweis", 2024),
        }
    }
    write_json(path, second)
    bak_path = path.with_suffix(path.suffix + ".bak")
    assert bak_path.exists()
    bak_content = json.loads(bak_path.read_text(encoding="utf-8"))
    assert bak_content["ubs__bank_zinsausweis"]["last_seen_year"] == 2023
    # path enthält den neuen Inhalt
    current_content = json.loads(path.read_text(encoding="utf-8"))
    assert current_content["ubs__bank_zinsausweis"]["last_seen_year"] == 2024


def test_write_no_bak_on_first_write(tmp_path: Path) -> None:
    """Kein ``.bak`` beim ersten Write (nichts zu sichern)."""
    path = tmp_path / "aussteller.json"
    history: dict[str, AusstellerEntry] = {}
    write_json(path, history)
    assert path.exists()
    bak_path = path.with_suffix(path.suffix + ".bak")
    assert not bak_path.exists()


def test_write_sort_keys_deterministic(tmp_path: Path) -> None:
    """``sort_keys=True`` ergibt deterministischen Output für Git-Diffs."""
    path = tmp_path / "aussteller.json"
    history: dict[str, AusstellerEntry] = {
        "zkb__bank_zinsausweis": {
            "aussteller_name": "ZKB",
            "belegtyp": "bank_zinsausweis",
            "last_seen_year": 2024,
            "fingerprint": make_fingerprint("ZKB", "bank_zinsausweis", 2024),
        },
        "helsana__kk_praemienbescheinigung": {
            "aussteller_name": "Helsana",
            "belegtyp": "kk_praemienbescheinigung",
            "last_seen_year": 2024,
            "fingerprint": make_fingerprint("Helsana", "kk_praemienbescheinigung", 2024),
        },
    }
    write_json(path, history)
    text = path.read_text(encoding="utf-8")
    # helsana vor zkb (alphabetisch sortiert)
    assert text.index("helsana") < text.index("zkb")
