"""Smoke-Tests für Plan 03-01 (Phase 3 Wave 1 Setup-Stubs).

Verifiziert die Setup-Invarianten ohne LLM-Call:

* ``Belegtyp``-Literal hat 11 Belegtypen + ``"unknown"`` = 12 Werte.
* Alle 7 neuen Schema-Stub-Klassen importierbar (Wertschriften, Spenden,
  Berufsauslagen, Kinderbetreuung, Hypothek, Liegenschaftsunterhalt,
  Krankheitskosten).
* ``MANDATORY_FIELDS_FOR_BELEGTYP`` hat exakt 11 Einträge und Pflichtfelder
  matchen die Schema-Klassen (Schema-Class als Single Source of Truth).
* ``PERSON_RELEVANT_FIELD`` hat 10 Einträge (alle ausser Lohnausweis).

Folgepläne 03-02 ff. erweitern die Stubs (Field-Descriptions, Plausibility,
Generators, Person-Match). Hier nur Skelett-Invarianten.
"""
from __future__ import annotations

import typing

import pytest

from extractors.person_inference import PERSON_RELEVANT_FIELD
from extractors.schema import (
    MANDATORY_FIELDS_FOR_BELEGTYP,
    BerufsauslagenRaw,
    Belegtyp,
    HypothekZinsbestaetigungRaw,
    KinderbetreuungRaw,
    KrankheitskostenRaw,
    LiegenschaftsunterhaltRaw,
    SpendenquittungRaw,
    WertschriftenverzeichnisRaw,
)

EXPECTED_BELEGTYPEN = {
    "lohnausweis",
    "bank_zinsausweis",
    "kk_praemienbescheinigung",
    "saeule_3a",
    "wertschriftenverzeichnis",
    "spenden",
    "berufsauslagen",
    "kinderbetreuung",
    "hypothek_zinsbestaetigung",
    "liegenschaftsunterhalt",
    "krankheitskosten",
}


# OOS-/Klassifikations-only-Typen ohne eigenes Extraktions-Schema. Sie sind
# Teil des Belegtyp-Literals (Klassifikator gibt sie zurück), tauchen aber NICHT
# in MANDATORY_FIELDS_FOR_BELEGTYP auf, weil sie nie feld-extrahiert werden.
# freizuegigkeitskonto (2. Säule) → immer out-of-scope (Quick 260612-l2g, E5).
CLASSIFICATION_ONLY_BELEGTYPEN = {
    "freizuegigkeitskonto",
}


def test_belegtyp_literal_values() -> None:
    """``Belegtyp`` enthält 11 Schema-Belegtypen + Klassifikations-only + ``"unknown"``."""
    args = set(typing.get_args(Belegtyp))
    expected = EXPECTED_BELEGTYPEN | CLASSIFICATION_ONLY_BELEGTYPEN | {"unknown"}
    assert args == expected, f"args={sorted(args)}"


def test_mandatory_fields_has_11_belegtypen() -> None:
    """``MANDATORY_FIELDS_FOR_BELEGTYP`` deckt alle 11 Belegtypen ab."""
    keys = set(MANDATORY_FIELDS_FOR_BELEGTYP.keys())
    assert keys == EXPECTED_BELEGTYPEN, f"keys={sorted(keys)}"


@pytest.mark.parametrize(
    "belegtyp, schema_cls",
    [
        ("wertschriftenverzeichnis", WertschriftenverzeichnisRaw),
        ("spenden", SpendenquittungRaw),
        ("berufsauslagen", BerufsauslagenRaw),
        ("kinderbetreuung", KinderbetreuungRaw),
        ("hypothek_zinsbestaetigung", HypothekZinsbestaetigungRaw),
        ("liegenschaftsunterhalt", LiegenschaftsunterhaltRaw),
        ("krankheitskosten", KrankheitskostenRaw),
    ],
)
def test_mandatory_fields_are_subset_of_schema(belegtyp: str, schema_cls: type) -> None:
    """Pflichtfeld-Liste pro Belegtyp muss Teilmenge der Schema-Felder sein.

    Schützt vor Drift zwischen ``MANDATORY_FIELDS_FOR_BELEGTYP`` und den
    Schema-Klassen (z.B. Tippfehler in Feld-Namen).
    """
    mandatory = set(MANDATORY_FIELDS_FOR_BELEGTYP[belegtyp])
    schema_fields = set(schema_cls.model_fields.keys())
    missing = mandatory - schema_fields
    assert not missing, f"{belegtyp}: mandatory fields {missing} not in schema"


def test_person_relevant_field_has_10_entries() -> None:
    """``PERSON_RELEVANT_FIELD`` deckt 10 Belegtypen ab (Lohnausweis ausgenommen, D-D4)."""
    assert len(PERSON_RELEVANT_FIELD) == 10
    assert "lohnausweis" not in PERSON_RELEVANT_FIELD
    # Alle Keys müssen gültige Belegtypen sein.
    assert set(PERSON_RELEVANT_FIELD.keys()) <= EXPECTED_BELEGTYPEN


@pytest.mark.parametrize("belegtyp,field_name", list(PERSON_RELEVANT_FIELD.items()))
def test_person_relevant_field_points_to_schema_field(belegtyp: str, field_name: str) -> None:
    """Das in ``PERSON_RELEVANT_FIELD`` referenzierte Feld muss im Schema existieren.

    Schützt vor Drift in Plan 03-02 (``infer_person_field`` würde sonst auf
    nicht-existente Attribute zugreifen).
    """
    from extractors.schema import (
        BankZinsausweisRaw,
        KkPraemienbescheinigungRaw,
        Saeule3aRaw,
    )

    schema_dispatch: dict[str, type] = {
        "bank_zinsausweis": BankZinsausweisRaw,
        "kk_praemienbescheinigung": KkPraemienbescheinigungRaw,
        "saeule_3a": Saeule3aRaw,
        "wertschriftenverzeichnis": WertschriftenverzeichnisRaw,
        "spenden": SpendenquittungRaw,
        "berufsauslagen": BerufsauslagenRaw,
        "kinderbetreuung": KinderbetreuungRaw,
        "hypothek_zinsbestaetigung": HypothekZinsbestaetigungRaw,
        "liegenschaftsunterhalt": LiegenschaftsunterhaltRaw,
        "krankheitskosten": KrankheitskostenRaw,
    }
    schema_cls = schema_dispatch[belegtyp]
    assert field_name in schema_cls.model_fields, (
        f"{belegtyp}: PERSON_RELEVANT_FIELD points to '{field_name}' "
        f"but {schema_cls.__name__} has no such field"
    )
