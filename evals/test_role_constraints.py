"""Tests fuer die Familien-Constraints in scripts/build_tax_output.

Pro Belegtyp ist nur eine geschlossene Menge an Rollen fachlich erlaubt
(ALLOWED_ROLES_FOR_BELEGTYP). resolve_role_constrained loest mehrdeutige
Personen-Strings korrekt auf und gibt nie eine unerlaubte Rolle aus.
resolve_kinderbetreuung_kind findet das Kind ueber ein Zweitfeld, wenn das
primaere Feld unbestimmt bleibt.

Es werden ausschliesslich synthetische Namen verwendet; beide Maps
(_FANTASY_FIRSTNAME_TO_ROLE und _REAL_FIRSTNAME_TO_ROLE) werden gemonkeypatcht.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import build_tax_output as bto


# Synthetische Vornamen → Rollen. Eltern und Kinder bewusst getrennt, damit
# die Constraint-Logik (Kind auf Lohnausweis verboten etc.) testbar ist.
SYNTH_REAL_MAP = {
    "balthasar": {bto.ROLE_ELTERNTEIL_1},
    "zora": {bto.ROLE_ELTERNTEIL_2},
    "cleo": {bto.ROLE_KIND_1},
    "nuri": {bto.ROLE_KIND_2},
}

SYNTH_FANTASY_MAP = {
    "Hans": bto.ROLE_ELTERNTEIL_1,
    "Maria": bto.ROLE_ELTERNTEIL_2,
    "Lina": bto.ROLE_KIND_1,
    "Tim": bto.ROLE_KIND_2,
}


@pytest.fixture(autouse=True)
def _patch_maps(monkeypatch):
    monkeypatch.setattr(bto, "_REAL_FIRSTNAME_TO_ROLE", SYNTH_REAL_MAP)
    monkeypatch.setattr(bto, "_FANTASY_FIRSTNAME_TO_ROLE", SYNTH_FANTASY_MAP)


# --- resolve_role_constrained -------------------------------------------------

def test_elternteil_auf_kinderbetreuung_ist_unknown():
    # Kinderbetreuung erlaubt nur Kinder → ein Elternteil-Name ist verboten.
    assert (
        bto.resolve_role_constrained("Balthasar Beispielmann", "kinderbetreuung")
        == bto.ROLE_UNKNOWN
    )


def test_kind_auf_lohnausweis_ist_unknown():
    # Lohnausweis erlaubt nur Eltern → ein Kind-Name ist verboten.
    assert (
        bto.resolve_role_constrained("Cleo Beispielmann", "lohnausweis")
        == bto.ROLE_UNKNOWN
    )


def test_elternteil_plus_kind_auf_lohnausweis_loest_auf_elternteil():
    # Roher String mehrdeutig, aber nur der Elternteil ist auf einem
    # Lohnausweis erlaubt → genau eine erlaubte Rolle uebrig.
    assert (
        bto.resolve_role_constrained("Balthasar und Cleo Beispielmann", "lohnausweis")
        == bto.ROLE_ELTERNTEIL_1
    )


def test_zwei_kinder_auf_kk_ist_familie():
    # KK-Kontext erlaubt familie → zwei Kinder ergeben familie.
    assert (
        bto.resolve_role_constrained(
            "Cleo und Nuri Beispielmann", "kk_praemienbescheinigung"
        )
        == bto.ROLE_FAMILIE
    )


def test_kind_auf_kinderbetreuung_ist_kind():
    # Kind ist im Kinderbetreuungs-Kontext erlaubt und eindeutig.
    assert (
        bto.resolve_role_constrained("Cleo Beispielmann", "kinderbetreuung")
        == bto.ROLE_KIND_1
    )


def test_unbekannter_belegtyp_keine_einschraenkung():
    # Spendenquittung nicht in ALLOWED_ROLES → Verhalten wie map_person_to_role.
    person = "Cleo Beispielmann"
    assert (
        bto.resolve_role_constrained(person, "spendenquittung")
        == bto.map_person_to_role(person)
    )
    # Auch ein mehrdeutiger String verhaelt sich wie ohne Constraint (familie).
    multi = "Cleo und Nuri Beispielmann"
    assert (
        bto.resolve_role_constrained(multi, "spendenquittung")
        == bto.map_person_to_role(multi)
        == bto.ROLE_FAMILIE
    )


def test_zwei_eltern_auf_lohnausweis_ist_unknown():
    # >1 erlaubte Rolle, aber familie ist auf Lohnausweis NICHT erlaubt →
    # nie eine unerlaubte Rolle, also unknown.
    assert (
        bto.resolve_role_constrained("Balthasar und Zora Beispielmann", "lohnausweis")
        == bto.ROLE_UNKNOWN
    )


def test_none_und_marker_bleiben_unknown():
    assert bto.resolve_role_constrained(None, "lohnausweis") == bto.ROLE_UNKNOWN
    assert bto.resolve_role_constrained("", "kinderbetreuung") == bto.ROLE_UNKNOWN


def test_map_person_to_role_unveraendert():
    # Rueckwaertskompatibilitaet: map_person_to_role-Verhalten unveraendert.
    assert bto.map_person_to_role("Cleo Beispielmann") == bto.ROLE_KIND_1
    assert bto.map_person_to_role("Cleo und Nuri Beispielmann") == bto.ROLE_FAMILIE
    assert bto.map_person_to_role("Unbekannt") == bto.ROLE_UNKNOWN


# --- _collect_candidate_roles -------------------------------------------------

def test_collect_candidate_roles_liefert_einzelrollen():
    # Sammelt die rohen Einzel-Treffer (keine Reduktion auf familie/unknown).
    assert bto._collect_candidate_roles("Cleo und Nuri Beispielmann") == {
        bto.ROLE_KIND_1,
        bto.ROLE_KIND_2,
    }
    assert bto._collect_candidate_roles("Unbekannt") == set()
    assert bto._collect_candidate_roles(None) == set()


# --- resolve_kinderbetreuung_kind --------------------------------------------

def _entry(belegtyp: str, fields: dict[str, str]) -> dict:
    return {
        "belegtyp": belegtyp,
        "fields": [{"feld": k, "value": v} for k, v in fields.items()],
    }


def test_kinderbetreuung_primaerfeld_kind():
    entry = _entry("kinderbetreuung", {"kind_name": "Cleo Beispielmann"})
    assert bto.resolve_kinderbetreuung_kind(entry) == bto.ROLE_KIND_1


def test_kinderbetreuung_fallback_ueber_zweitfeld():
    # Primaeres kind_name unbestimmt, aber ein anderes Feld nennt genau ein Kind.
    entry = _entry(
        "kinderbetreuung",
        {"kind_name": "Unbekannt", "person": "Cleo Beispielmann"},
    )
    assert bto.resolve_kinderbetreuung_kind(entry) == bto.ROLE_KIND_1


def test_kinderbetreuung_zwei_verschiedene_kinder_ist_unknown():
    # Zwei verschiedene Kinder ueber Felder verteilt → nicht eindeutig.
    entry = _entry(
        "kinderbetreuung",
        {"kind_name": "Cleo Beispielmann", "kind": "Nuri Beispielmann"},
    )
    assert bto.resolve_kinderbetreuung_kind(entry) == bto.ROLE_UNKNOWN


def test_kinderbetreuung_kein_kind_ist_unknown():
    entry = _entry("kinderbetreuung", {"kind_name": "Unbekannt"})
    assert bto.resolve_kinderbetreuung_kind(entry) == bto.ROLE_UNKNOWN


# --- Finding 3: Rollen-Konsistenz MD (build_rows) ↔ xlsx ----------------------

def _lohn_entry(person: str) -> dict:
    """Synthetischer Lohnausweis-Entry mit verankertem Nettolohn."""
    def _f(feld: str, value: str) -> dict:
        return {
            "feld": feld, "value": value, "bbox": [0, 0, 10, 10],
            "page": 1, "snippet": f"{feld} {value}", "anchor_valid": True,
            "inference_source": None,
        }
    return {
        "pdf_name": "lohn_test.pdf", "status": "ok", "belegtyp": "lohnausweis",
        "fields": [
            _f("arbeitnehmer_name", person),
            _f("arbeitgeber", "ARBEITGEBER-A"),
            _f("periode_von", "2022-01-01"),
            _f("periode_bis", "2022-12-31"),
            _f("bruttolohn_pos8", "100'000.00"),
            _f("ahv_alv_nbu_abzug_pos9", "6'000.00"),
            _f("nettolohn_pos11", "94'000.00"),
            _f("jahr", "2022"),
        ],
    }


def test_lohn_kind_person_md_und_xlsx_beide_manuell():
    # Kind-Name auf Lohnausweis ist constrained verboten → in BEIDEN Outputs manuell.
    import scripts.build_steueraufstellung as bsa
    bsa._ROLE_TO_CANONICAL = bsa._build_role_to_canonical()

    entry = _lohn_entry("Cleo Beispielmann")
    rows = bto.build_rows(entry)
    netto_rows = [r for r in rows if r["field_name"] == "nettolohn_pos11"]
    assert netto_rows, "Nettolohn-Zeile erwartet"
    assert netto_rows[0]["status"] == "manual_review"

    wb = bsa.build_workbook([entry])
    ws = wb["Lohnausweise"]
    data_rows = [tuple(c.value for c in r) for r in ws.iter_rows()][2:]
    data_rows = [r for r in data_rows if r[0] and r[0] != "Total"]
    assert data_rows, "Lohn-Datenzeile erwartet"
    assert data_rows[0][-1] == bsa.STATUS_MANUELL


def test_lohn_mehrdeutige_person_md_und_xlsx_beide_manuell():
    # "Hans und Maria" → zwei Eltern, familie auf Lohnausweis NICHT erlaubt → UNKNOWN.
    import scripts.build_steueraufstellung as bsa
    bsa._ROLE_TO_CANONICAL = bsa._build_role_to_canonical()

    entry = _lohn_entry("Hans und Maria Muster")
    rows = bto.build_rows(entry)
    netto_rows = [r for r in rows if r["field_name"] == "nettolohn_pos11"]
    assert netto_rows[0]["status"] == "manual_review"

    wb = bsa.build_workbook([entry])
    ws = wb["Lohnausweise"]
    data_rows = [tuple(c.value for c in r) for r in ws.iter_rows()][2:]
    data_rows = [r for r in data_rows if r[0] and r[0] != "Total"]
    assert data_rows[0][-1] == bsa.STATUS_MANUELL
