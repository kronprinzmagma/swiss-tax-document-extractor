"""Unit-Tests für Phase-2-Schemas, Belegtyp-Literal und MANDATORY_FIELDS_FOR_BELEGTYP.

Plan 02-01 Task 2 (Schema) + Task 3 (Prompt-Dispatch). Testet ausschliesslich
strukturelle Eigenschaften — keine echten Ollama-Calls (Test d in Task 3 mockt
``ollama.chat`` via ``monkeypatch``).
"""
from __future__ import annotations

import pytest
from pydantic import BaseModel, ValidationError

from extractors import (
    Belegtyp,
    BankZinsausweisRaw,
    KkPraemienbescheinigungRaw,
    LohnausweisRaw,
    MANDATORY_FIELDS_FOR_BELEGTYP,
    Saeule3aRaw,
    TaggedField,
)


# ---------------------------------------------------------------------------
# Belegtyp-Literal (D-D6)
# ---------------------------------------------------------------------------


def test_belegtyp_literal_hat_5_werte() -> None:
    """Belegtyp-Literal enthält die 5 Phase-2-Werte als Teilmenge (D-D6).

    Phase 3 (Plan 03-01) erweitert das Literal um 7 neue Belegtypen
    (Wertschriften, Spenden, Berufsauslagen, Kinderbetreuung, Hypothek,
    Liegenschaftsunterhalt, Krankheitskosten). Die Phase-2-Invariante
    ist hier auf „Teilmenge" relaxiert; die Vollständigkeits-Prüfung
    erfolgt in ``evals/test_phase3_schemas_stub.py``.
    """
    from typing import get_args

    werte = set(get_args(Belegtyp))
    phase2_minimum = {
        "lohnausweis",
        "bank_zinsausweis",
        "kk_praemienbescheinigung",
        "saeule_3a",
        "unknown",
    }
    assert phase2_minimum <= werte, f"Phase-2-Belegtypen fehlen: {phase2_minimum - werte}"


# ---------------------------------------------------------------------------
# MANDATORY_FIELDS_FOR_BELEGTYP — Single Source of Truth (D-C1..C6)
# ---------------------------------------------------------------------------


def test_mandatory_fields_map_hat_4_belegtypen() -> None:
    """MANDATORY_FIELDS_FOR_BELEGTYP deckt die 4 Phase-2-Belegtypen ab.

    Phase 3 (Plan 03-01) erweitert die Map um 7 Einträge. Die Phase-2-
    Invariante ist hier auf „Teilmenge" relaxiert; die Vollständigkeits-
    Prüfung erfolgt in ``evals/test_phase3_schemas_stub.py``.
    """
    phase2_minimum = {
        "lohnausweis",
        "bank_zinsausweis",
        "kk_praemienbescheinigung",
        "saeule_3a",
    }
    assert phase2_minimum <= set(MANDATORY_FIELDS_FOR_BELEGTYP.keys()), (
        f"Phase-2-Einträge fehlen: {phase2_minimum - set(MANDATORY_FIELDS_FOR_BELEGTYP.keys())}"
    )


def test_mandatory_fields_lohnausweis_phase1_kompatibel() -> None:
    """Lohnausweis-Pflichtfelder spiegeln steuer_extraktor.REQUIRED_FIELDS (Phase 1)."""
    assert MANDATORY_FIELDS_FOR_BELEGTYP["lohnausweis"] == [
        "arbeitgeber",
        "periode_von",
        "periode_bis",
        "bruttolohn_pos8",
        "ahv_alv_nbu_abzug_pos9",
        "bvg_abzug_pos10a",
        "nettolohn_pos11",
    ]


def test_mandatory_fields_bank_zinsausweis_d_c1() -> None:
    """Bank-Zinsausweis-Pflichtfelder gemäss D-C1 (6 Felder, Reihenfolge wie Schema)."""
    assert MANDATORY_FIELDS_FOR_BELEGTYP["bank_zinsausweis"] == [
        "institut",
        "kontoinhaber_name",
        "bruttoertrag",
        "vermoegensstand_3112",
        "verrechnungssteuer",
        "jahr",
    ]


def test_mandatory_fields_kk_praemienbescheinigung_d_c3() -> None:
    """KK-Pflichtfelder gemäss D-C3 (4 Felder)."""
    assert MANDATORY_FIELDS_FOR_BELEGTYP["kk_praemienbescheinigung"] == [
        "kasse",
        "versicherte_person_name",
        "jahr",
        "praemie_kvg_total",
    ]


def test_mandatory_fields_saeule_3a_d_c5() -> None:
    """Säule-3a-Pflichtfelder gemäss D-C5 (4 Felder)."""
    assert MANDATORY_FIELDS_FOR_BELEGTYP["saeule_3a"] == [
        "stiftung",
        "kontoinhaber_name",
        "jahr",
        "einzahlung_betrag",
    ]


# ---------------------------------------------------------------------------
# Helper für Tagged-Felder
# ---------------------------------------------------------------------------


def _tf(value: str, ids: list[int] | None = None) -> dict:
    return {"value": value, "tag_refs": ids or [1]}


# ---------------------------------------------------------------------------
# BankZinsausweisRaw (D-C1, D-C2)
# ---------------------------------------------------------------------------


def _bank_payload_minimal() -> dict:
    return {
        "institut": _tf("ZKB Zürcher Kantonalbank"),
        "kontoinhaber_name": _tf("Max Beispiel"),
        "bruttoertrag": _tf("125.50"),
        "vermoegensstand_3112": _tf("12'500.00"),
        "verrechnungssteuer": _tf("43.93"),
        "jahr": _tf("2024"),
    }


def test_bank_zinsausweis_construct_minimal() -> None:
    obj = BankZinsausweisRaw(**_bank_payload_minimal())
    assert obj.institut.value == "ZKB Zürcher Kantonalbank"
    assert obj.konto_nr_redacted is None and obj.kontotyp is None


def test_bank_zinsausweis_fehlt_pflichtfeld() -> None:
    payload = _bank_payload_minimal()
    del payload["bruttoertrag"]
    with pytest.raises(ValidationError):
        BankZinsausweisRaw(**payload)


def test_bank_zinsausweis_extra_field_forbidden() -> None:
    payload = _bank_payload_minimal()
    payload["unbekannt"] = _tf("foo")
    with pytest.raises(ValidationError):
        BankZinsausweisRaw(**payload)


def test_bank_zinsausweis_optional_felder() -> None:
    payload = _bank_payload_minimal()
    payload["konto_nr_redacted"] = _tf("XXXX-1234")
    payload["kontotyp"] = _tf("Sparkonto")
    obj = BankZinsausweisRaw(**payload)
    assert obj.konto_nr_redacted is not None and obj.konto_nr_redacted.value == "XXXX-1234"
    assert obj.kontotyp is not None and obj.kontotyp.value == "Sparkonto"


# ---------------------------------------------------------------------------
# KkPraemienbescheinigungRaw (D-C3, D-C4)
# ---------------------------------------------------------------------------


def _kk_payload_minimal() -> dict:
    return {
        "kasse": _tf("Helsana"),
        "versicherte_person_name": _tf("Anna Beispiel"),
        "jahr": _tf("2024"),
        "praemie_kvg_total": _tf("4'200.00"),
    }


def test_kk_construct_minimal() -> None:
    obj = KkPraemienbescheinigungRaw(**_kk_payload_minimal())
    assert obj.kasse.value == "Helsana"
    assert obj.praemie_vvg_total is None
    assert obj.mitversicherte_kinder_namen == []


def test_kk_fehlt_pflichtfeld_kasse() -> None:
    payload = _kk_payload_minimal()
    del payload["kasse"]
    with pytest.raises(ValidationError):
        KkPraemienbescheinigungRaw(**payload)


def test_kk_extra_field_forbidden() -> None:
    payload = _kk_payload_minimal()
    payload["zusatz"] = _tf("xx")
    with pytest.raises(ValidationError):
        KkPraemienbescheinigungRaw(**payload)


def test_kk_mitversicherte_kinder_liste() -> None:
    payload = _kk_payload_minimal()
    payload["mitversicherte_kinder_namen"] = [_tf("Lina Beispiel"), _tf("Tim Beispiel")]
    obj = KkPraemienbescheinigungRaw(**payload)
    assert len(obj.mitversicherte_kinder_namen) == 2
    assert obj.mitversicherte_kinder_namen[0].value == "Lina Beispiel"


# ---------------------------------------------------------------------------
# Saeule3aRaw (D-C5, D-C6)
# ---------------------------------------------------------------------------


def _saeule3a_payload_minimal() -> dict:
    return {
        "stiftung": _tf("VIAC"),
        "kontoinhaber_name": _tf("Max Beispiel"),
        "jahr": _tf("2024"),
        "einzahlung_betrag": _tf("7'056.00"),
    }


def test_saeule3a_construct_minimal() -> None:
    obj = Saeule3aRaw(**_saeule3a_payload_minimal())
    assert obj.stiftung.value == "VIAC"
    assert obj.kontonummer_redacted is None
    assert obj.valutadatum is None


def test_saeule3a_fehlt_pflichtfeld() -> None:
    payload = _saeule3a_payload_minimal()
    del payload["einzahlung_betrag"]
    with pytest.raises(ValidationError):
        Saeule3aRaw(**payload)


def test_saeule3a_extra_field_forbidden() -> None:
    payload = _saeule3a_payload_minimal()
    payload["foo"] = _tf("bar")
    with pytest.raises(ValidationError):
        Saeule3aRaw(**payload)


def test_saeule3a_optional() -> None:
    payload = _saeule3a_payload_minimal()
    payload["kontonummer_redacted"] = _tf("XXXX-9876")
    payload["valutadatum"] = _tf("2024-12-20")
    obj = Saeule3aRaw(**payload)
    assert obj.kontonummer_redacted is not None
    assert obj.valutadatum is not None and obj.valutadatum.value == "2024-12-20"


# ---------------------------------------------------------------------------
# Schema-Konfiguration: alle 3 neuen Schemas haben extra="forbid" + schema_version=1
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "schema_class",
    [BankZinsausweisRaw, KkPraemienbescheinigungRaw, Saeule3aRaw],
)
def test_schemas_haben_extra_forbid(schema_class: type[BaseModel]) -> None:
    assert schema_class.model_config.get("extra") == "forbid"


@pytest.mark.parametrize(
    "schema_class,minimal",
    [
        (BankZinsausweisRaw, _bank_payload_minimal),
        (KkPraemienbescheinigungRaw, _kk_payload_minimal),
        (Saeule3aRaw, _saeule3a_payload_minimal),
    ],
)
def test_schemas_haben_schema_version_1(schema_class, minimal) -> None:
    obj = schema_class(**minimal())
    assert obj.schema_version == 1


# ---------------------------------------------------------------------------
# TaggedField-Generic für list[TaggedField[str]] in KK
# ---------------------------------------------------------------------------


def test_taggedfield_str_envelope() -> None:
    tf = TaggedField[str](value="abc", tag_refs=[1, 2])
    assert tf.value == "abc"
    assert tf.tag_refs == [1, 2]


# ---------------------------------------------------------------------------
# Task 3 — PROMPT_TEMPLATES (Schema-Dispatch)
# ---------------------------------------------------------------------------


def test_prompt_templates_hat_4_schemas() -> None:
    """Phase 2: 4 Schemas; Phase 3 Wave 3 (Plan 03-04): + 3 (Wertschriften/Spenden/Berufsauslagen);
    Phase 3 Wave 4 (Plan 03-05): + 4 (Kinderbetreuung/Hypothek/Liegenschaftsunterhalt/Krankheitskosten).

    Test-Name bleibt aus historischen Gründen — der Assert deckt jetzt 11 Schemas ab.
    """
    from extractors.llm_extract import PROMPT_TEMPLATES
    from extractors.schema import (
        BerufsauslagenRaw,
        HypothekZinsbestaetigungRaw,
        KinderbetreuungRaw,
        KrankheitskostenRaw,
        LiegenschaftsunterhaltRaw,
        SpendenquittungRaw,
        WertschriftenverzeichnisRaw,
    )

    assert set(PROMPT_TEMPLATES.keys()) == {
        LohnausweisRaw,
        BankZinsausweisRaw,
        KkPraemienbescheinigungRaw,
        Saeule3aRaw,
        WertschriftenverzeichnisRaw,
        SpendenquittungRaw,
        BerufsauslagenRaw,
        KinderbetreuungRaw,
        HypothekZinsbestaetigungRaw,
        LiegenschaftsunterhaltRaw,
        KrankheitskostenRaw,
    }


@pytest.mark.parametrize(
    "schema_class",
    [LohnausweisRaw, BankZinsausweisRaw, KkPraemienbescheinigungRaw, Saeule3aRaw],
)
def test_prompt_templates_enthalten_tag_refs_disziplin(schema_class) -> None:
    from extractors.llm_extract import PROMPT_TEMPLATES

    assert "tag_refs" in PROMPT_TEMPLATES[schema_class]


def test_prompt_templates_enthalten_belegtyp_kopfsatz() -> None:
    from extractors.llm_extract import PROMPT_TEMPLATES

    assert "Lohnausweis" in PROMPT_TEMPLATES[LohnausweisRaw]
    assert "Bank-Zinsausweis" in PROMPT_TEMPLATES[BankZinsausweisRaw]
    assert "Krankenkasse" in PROMPT_TEMPLATES[KkPraemienbescheinigungRaw] or \
        "Prämienbescheinigung" in PROMPT_TEMPLATES[KkPraemienbescheinigungRaw]
    assert "Säule" in PROMPT_TEMPLATES[Saeule3aRaw] or "3a" in PROMPT_TEMPLATES[Saeule3aRaw]


def test_prompt_templates_enthalten_pflichtfelder_listing() -> None:
    """Jeder Prompt nennt mindestens ein eindeutiges Pflichtfeld seines Schemas."""
    from extractors.llm_extract import PROMPT_TEMPLATES

    assert "bruttolohn_pos8" in PROMPT_TEMPLATES[LohnausweisRaw]
    assert "bruttoertrag" in PROMPT_TEMPLATES[BankZinsausweisRaw]
    assert "praemie_kvg_total" in PROMPT_TEMPLATES[KkPraemienbescheinigungRaw]
    assert "einzahlung_betrag" in PROMPT_TEMPLATES[Saeule3aRaw]


# ---------------------------------------------------------------------------
# extract() Schema-Dispatch (Test d & e mit gemocktem ollama.chat)
# ---------------------------------------------------------------------------


class _FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeResponse:
    def __init__(self, content: str) -> None:
        self.message = _FakeMessage(content)


def _fake_chat_factory(json_payload: str):
    def _fake_chat(**kwargs):
        return _FakeResponse(json_payload)

    return _fake_chat


def test_extract_dispatcht_bankzinsausweis(monkeypatch) -> None:
    """extract(schema_class=BankZinsausweisRaw) liefert BankZinsausweisRaw-Instanz."""
    import json
    import ollama

    payload = {
        "schema_version": 1,
        "institut": _tf("ZKB"),
        "kontoinhaber_name": _tf("Max Beispiel"),
        "bruttoertrag": _tf("100.00"),
        "vermoegensstand_3112": _tf("10000.00"),
        "verrechnungssteuer": _tf("35.00"),
        "jahr": _tf("2024"),
    }
    monkeypatch.setattr(ollama, "chat", _fake_chat_factory(json.dumps(payload)))

    from extractors.llm_extract import extract

    result = extract("[T0] dummy", schema_class=BankZinsausweisRaw)
    assert isinstance(result, BankZinsausweisRaw)
    assert result.institut.value == "ZKB"


def test_extract_default_lohnausweis_rueckwaertskompatibel(monkeypatch) -> None:
    """extract(tagged_text) ohne schema_class liefert weiterhin LohnausweisRaw."""
    import json
    import ollama

    payload = {
        "schema_version": 1,
        "arbeitgeber": _tf("ACME AG"),
        "periode_von": _tf("01.01.2024"),
        "periode_bis": _tf("31.12.2024"),
        "bruttolohn_pos8": _tf("95'400.00"),
        "ahv_alv_nbu_abzug_pos9": _tf("5'000.00"),
        "nettolohn_pos11": _tf("85'000.00"),
    }
    monkeypatch.setattr(ollama, "chat", _fake_chat_factory(json.dumps(payload)))

    from extractors.llm_extract import extract

    result = extract("[T0] dummy")
    assert isinstance(result, LohnausweisRaw)
    assert result.arbeitgeber.value == "ACME AG"


def test_extract_unbekanntes_schema_raises(monkeypatch) -> None:
    """extract() mit nicht-registrierter Schema-Klasse → KeyError."""
    from extractors.llm_extract import extract

    class FremdSchema(BaseModel):
        x: str = "y"

    with pytest.raises(KeyError):
        extract("[T0] dummy", schema_class=FremdSchema)
