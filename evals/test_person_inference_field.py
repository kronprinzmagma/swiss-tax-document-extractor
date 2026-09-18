"""Tests für ``infer_person_field()`` — field-level Person-Match (Plan 03-02).

Verifiziert das Token-basierte Match-Verhalten gemäss Decision D-D2
(03-CONTEXT.md): pro Familienmitglied werden die Tokens des Voll-Namens
(Vorname + Nachname, normalisiert via :func:`extractors.person_inference._name_normalize`)
gebildet und mit den Tokens des person-relevanten Schema-Feldes verglichen.
Match wenn alle Member-Tokens als Teilmenge im Feld-Token-Set vorkommen.

Diese Tests laufen rein in-memory — kein Ollama, kein PDF-Read. Sie tragen
KEINEN ``llm_full``-Marker und gehören damit zur ``pytest -m 'not llm_full'``-
Standard-Suite (Phase-1+2-Kompatibilität).

Edge-Cases (Plan 03-02 §Task 1 Behavior):

* family=None → role=None.
* belegtyp ohne PERSON_RELEVANT_FIELD-Eintrag (z.B. ``lohnausweis``,
  ``unknown``) → role=None.
* Joint-Account-Pattern „Hans und Maria Muster" → role=„gemeinsam".
* KK-Helsana-Pattern: ``versicherte_person_name=Lina Muster`` (Beitrags-
  zahler-Feld nicht relevant) → role=„kind1".
* Initialen (``N. F. Muster``) matchen NICHT — v1-Limit (RESEARCH-Pitfall 5).
* Reihenfolge irrelevant: ``Muster Hans`` matched ebenso wie ``Hans Muster``.
* Case-insensitivität: ``HANS MUSTER`` matched ``Hans Muster``.
* Token-Subset: zusätzliche Tokens im Feld („Herr Hans Peter Muster")
  brechen den Match nicht.
* Nachname-only („Familie Muster") → role=None (kein False-Positive).
"""
from __future__ import annotations

import pytest

from extractors.family import Family, FamilyMember
from extractors.person_inference import infer_person_field
from extractors.schema import (
    BankZinsausweisRaw,
    BerufsauslagenRaw,
    HypothekZinsbestaetigungRaw,
    KinderbetreuungRaw,
    KkPraemienbescheinigungRaw,
    KrankheitskostenRaw,
    LiegenschaftsunterhaltRaw,
    Saeule3aRaw,
    SpendenquittungRaw,
    TaggedField,
    WertschriftenverzeichnisRaw,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def family() -> Family:
    """Synthetische 4er-Familie analog ``evals/family.yaml.test`` (PII-frei)."""
    return Family(
        members=[
            FamilyMember(role="mann", first_name="Hans", last_name="Muster"),
            FamilyMember(role="frau", first_name="Maria", last_name="Muster"),
            FamilyMember(role="kind1", first_name="Lina", last_name="Muster"),
            FamilyMember(role="kind2", first_name="Tim", last_name="Muster"),
        ]
    )


def _bank(name: str, tag_id: int = 11) -> BankZinsausweisRaw:
    """Hilfs-Konstruktor für einen minimalen ``BankZinsausweisRaw``.

    Nur ``kontoinhaber_name`` ist für die Person-Match-Tests relevant; alle
    anderen Pflichtfelder werden mit Dummy-Werten gefüllt, damit Pydantic
    die Klasse validiert.
    """
    return BankZinsausweisRaw(
        institut=TaggedField[str](value="ZKB", tag_refs=[1]),
        kontoinhaber_name=TaggedField[str](value=name, tag_refs=[tag_id]),
        bruttoertrag=TaggedField[str](value="100.00", tag_refs=[2]),
        vermoegensstand_3112=TaggedField[str](value="1000.00", tag_refs=[3]),
        verrechnungssteuer=TaggedField[str](value="35.00", tag_refs=[4]),
        jahr=TaggedField[str](value="2024", tag_refs=[5]),
    )


def _kk(name: str, tag_id: int = 21) -> KkPraemienbescheinigungRaw:
    return KkPraemienbescheinigungRaw(
        kasse=TaggedField[str](value="Helsana", tag_refs=[1]),
        versicherte_person_name=TaggedField[str](value=name, tag_refs=[tag_id]),
        jahr=TaggedField[str](value="2024", tag_refs=[3]),
        praemie_kvg_total=TaggedField[str](value="5000.00", tag_refs=[4]),
    )


def _saeule3a(name: str) -> Saeule3aRaw:
    return Saeule3aRaw(
        stiftung=TaggedField[str](value="VIAC", tag_refs=[1]),
        kontoinhaber_name=TaggedField[str](value=name, tag_refs=[31]),
        jahr=TaggedField[str](value="2024", tag_refs=[3]),
        einzahlung_betrag=TaggedField[str](value="7056.00", tag_refs=[4]),
    )


def _wertschriften(name: str) -> WertschriftenverzeichnisRaw:
    return WertschriftenverzeichnisRaw(
        institut=TaggedField[str](value="UBS", tag_refs=[1]),
        kontoinhaber_name=TaggedField[str](value=name, tag_refs=[41]),
        bestand_3112=TaggedField[str](value="50000.00", tag_refs=[3]),
        bruttoertrag_total=TaggedField[str](value="500.00", tag_refs=[4]),
        verrechnungssteuer_total=TaggedField[str](value="175.00", tag_refs=[5]),
        jahr=TaggedField[str](value="2024", tag_refs=[6]),
    )


def _spenden(name: str) -> SpendenquittungRaw:
    return SpendenquittungRaw(
        empfaenger=TaggedField[str](value="Rotes Kreuz", tag_refs=[1]),
        spender_name=TaggedField[str](value=name, tag_refs=[51]),
        betrag=TaggedField[str](value="200.00", tag_refs=[3]),
        jahr=TaggedField[str](value="2024", tag_refs=[4]),
    )


def _berufsauslagen(name: str) -> BerufsauslagenRaw:
    return BerufsauslagenRaw(
        anbieter=TaggedField[str](value="Klubschule", tag_refs=[1]),
        person_name=TaggedField[str](value=name, tag_refs=[61]),
        betrag=TaggedField[str](value="1200.00", tag_refs=[3]),
        jahr=TaggedField[str](value="2024", tag_refs=[4]),
    )


def _kinderbetreuung(name: str) -> KinderbetreuungRaw:
    return KinderbetreuungRaw(
        anbieter=TaggedField[str](value="Kita Sonnenschein", tag_refs=[1]),
        kind_name=TaggedField[str](value=name, tag_refs=[71]),
        betrag=TaggedField[str](value="12000.00", tag_refs=[3]),
        jahr=TaggedField[str](value="2024", tag_refs=[4]),
    )


def _hypothek(name: str) -> HypothekZinsbestaetigungRaw:
    return HypothekZinsbestaetigungRaw(
        institut=TaggedField[str](value="ZKB", tag_refs=[1]),
        kontoinhaber_name=TaggedField[str](value=name, tag_refs=[81]),
        liegenschaft=TaggedField[str](value="Musterweg 1, 8000 Zürich", tag_refs=[3]),
        schuldzinsen=TaggedField[str](value="8000.00", tag_refs=[4]),
        schuldsaldo_3112=TaggedField[str](value="500000.00", tag_refs=[5]),
        jahr=TaggedField[str](value="2024", tag_refs=[6]),
    )


def _liegenschaft(name: str) -> LiegenschaftsunterhaltRaw:
    return LiegenschaftsunterhaltRaw(
        handwerker_anbieter=TaggedField[str](value="Maler Müller", tag_refs=[1]),
        eigentuemer_name=TaggedField[str](value=name, tag_refs=[91]),
        liegenschaft=TaggedField[str](value="Musterweg 1", tag_refs=[3]),
        betrag=TaggedField[str](value="3000.00", tag_refs=[4]),
        jahr=TaggedField[str](value="2024", tag_refs=[5]),
    )


def _krankheit(name: str) -> KrankheitskostenRaw:
    return KrankheitskostenRaw(
        leistungserbringer=TaggedField[str](value="Dr. Schmid", tag_refs=[1]),
        person_name=TaggedField[str](value=name, tag_refs=[101]),
        betrag=TaggedField[str](value="450.00", tag_refs=[3]),
        jahr=TaggedField[str](value="2024", tag_refs=[4]),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_field_match_family_none_returns_none() -> None:
    """family=None → role=None (D-D2 Schritt 1)."""
    result = infer_person_field("bank_zinsausweis", _bank("Hans Muster"), None)
    assert result.role is None
    assert result.matched_members == []
    assert result.anchor_tag_id is None


def test_field_match_lohnausweis_returns_none(family: Family) -> None:
    """belegtyp ``lohnausweis`` ist nicht in PERSON_RELEVANT_FIELD → role=None (D-D4 Phase 1)."""
    # Wir nutzen einen Bank-raw als Dummy, weil Lohnausweis selbst kein
    # person-relevantes Feld hat — die Implementierung darf den raw gar
    # nicht ansehen, wenn der belegtyp keinen Eintrag hat.
    result = infer_person_field("lohnausweis", _bank("Hans Muster"), family)
    assert result.role is None
    assert result.matched_members == []


def test_field_match_unknown_belegtyp_returns_none(family: Family) -> None:
    """Unbekannter belegtyp → role=None (defensiv, kein Crash)."""
    result = infer_person_field("unknown", _bank("Hans Muster"), family)
    assert result.role is None


def test_field_match_bank_single_person(family: Family) -> None:
    """Bank-Zinsausweis ``Hans Muster`` → role=mann + Anker auf tag_refs[0]."""
    raw = _bank("Hans Muster", tag_id=42)
    result = infer_person_field("bank_zinsausweis", raw, family)
    assert result.role == "mann"
    assert result.matched_members == ["mann"]
    assert result.anchor_tag_id == 42


def test_field_match_bank_joint_account(family: Family) -> None:
    """Bank ``Hans und Maria Muster`` → role=gemeinsam (löst Phase-2-xfail).

    Tokens des Feldes nach Normalize: ``{hans, und, maria, muster}``.
    Hans-Member-Tokens ``{hans, muster}`` ⊆ field_tokens → match.
    Maria-Member-Tokens ``{maria, muster}`` ⊆ field_tokens → match.
    2 Matches → role=„gemeinsam".
    """
    raw = _bank("Hans und Maria Muster")
    result = infer_person_field("bank_zinsausweis", raw, family)
    assert result.role == "gemeinsam"
    assert set(result.matched_members) == {"mann", "frau"}
    # Bei Joint-Match liefern wir keinen Anker (analog ``infer_person``).
    assert result.anchor_tag_id is None


def test_field_match_kk_lina_only(family: Family) -> None:
    """KK-Helsana-Pattern: versicherte_person_name=Lina Muster → kind1.

    Löst die Phase-2-Helsana-xfail: dort steckten ``Hans Muster``
    (Beitragszahler) und ``Lina Muster`` (Versicherte) beide im joined-text
    und der alte Algorithmus matched beide. Mit field-level-Match sehen
    wir NUR das ``versicherte_person_name``-Feld und matchen korrekt kind1.
    """
    raw = _kk("Lina Muster", tag_id=99)
    result = infer_person_field("kk_praemienbescheinigung", raw, family)
    assert result.role == "kind1"
    assert result.matched_members == ["kind1"]
    assert result.anchor_tag_id == 99


def test_field_match_saeule_3a(family: Family) -> None:
    """Säule 3a ``Hans Muster`` → mann."""
    result = infer_person_field("saeule_3a", _saeule3a("Hans Muster"), family)
    assert result.role == "mann"


def test_field_match_initials_not_matched(family: Family) -> None:
    """``N. F. Muster`` mit Initialen matched NICHT (v1-Limit, RESEARCH-Pitfall 5)."""
    raw = _bank("N. F. Muster")
    result = infer_person_field("bank_zinsausweis", raw, family)
    assert result.role is None
    assert result.matched_members == []


def test_field_match_reverse_order(family: Family) -> None:
    """``Muster Hans`` matched ebenso wie ``Hans Muster`` (Reihenfolge-flexibel)."""
    raw = _bank("Muster Hans")
    result = infer_person_field("bank_zinsausweis", raw, family)
    assert result.role == "mann"


def test_field_match_uppercase(family: Family) -> None:
    """``HANS MUSTER`` matched ``Hans Muster`` (Case-insensitivität via normalize)."""
    raw = _bank("HANS MUSTER")
    result = infer_person_field("bank_zinsausweis", raw, family)
    assert result.role == "mann"


def test_field_match_nbsp_whitespace(family: Family) -> None:
    """NBSP (U+00A0) zwischen den Namen toleriert (Whitespace-Klassen-Split).

    Schweizer PDFs benutzen häufig NBSP zwischen Vor- und Nachname.
    :func:`_name_tokens` splittet auf der Whitespace-Klasse inkl. NBSP/NNBSP/
    THINSP, bevor jedes Token einzeln normalisiert wird — der Match-
    Algorithmus erkennt ``"Hans\xa0Muster"`` korrekt als ``{hans, muster}``.
    """
    raw = _bank("Hans Muster")  # NBSP (U+00A0) zwischen den Namen
    result = infer_person_field("bank_zinsausweis", raw, family)
    assert result.role == "mann"


def test_field_match_token_subset(family: Family) -> None:
    """Zusätzliche Tokens im Feld brechen den Match nicht (Subset-Semantik)."""
    raw = _bank("Herr Hans Peter Muster")
    result = infer_person_field("bank_zinsausweis", raw, family)
    # Member-Tokens {hans, muster} ⊆ {herr, hans, peter, muster} → match.
    assert result.role == "mann"


def test_field_match_value_empty(family: Family) -> None:
    """Feld-Wert ist leerer String → role=None."""
    raw = _bank("")
    # Pydantic akzeptiert leeren String (kein min_length-Constraint auf TaggedField[str].value).
    result = infer_person_field("bank_zinsausweis", raw, family)
    assert result.role is None


def test_field_match_familienname_only_no_false_positive(family: Family) -> None:
    """``Familie Muster`` matched NICHT (D-B4-Logik bleibt: Token-Subset benötigt Vorname).

    Member-Tokens für Hans: ``{hans, muster}``. Feld-Tokens: ``{familie, muster}``.
    ``{hans, muster}`` ist NICHT ⊆ ``{familie, muster}`` → kein Match. Damit gibt es
    kein False-Positive auf irgendeinen einzelnen Member, obwohl alle den gleichen
    Nachnamen tragen.
    """
    raw = _bank("Familie Muster")
    result = infer_person_field("bank_zinsausweis", raw, family)
    assert result.role is None
    assert result.matched_members == []


def test_field_match_wertschriften(family: Family) -> None:
    """Wertschriftenverzeichnis mit kontoinhaber_name → analog Bank."""
    result = infer_person_field(
        "wertschriftenverzeichnis", _wertschriften("Hans Muster"), family
    )
    assert result.role == "mann"


def test_field_match_spenden(family: Family) -> None:
    """Spendenquittung mit spender_name → analog."""
    result = infer_person_field("spenden", _spenden("Maria Muster"), family)
    assert result.role == "frau"


def test_field_match_berufsauslagen(family: Family) -> None:
    """Berufsauslagen mit person_name → analog."""
    result = infer_person_field(
        "berufsauslagen", _berufsauslagen("Hans Muster"), family
    )
    assert result.role == "mann"


def test_field_match_kinderbetreuung_kind1(family: Family) -> None:
    """Kinderbetreuung mit kind_name (Lina) → kind1."""
    result = infer_person_field(
        "kinderbetreuung", _kinderbetreuung("Lina Muster"), family
    )
    assert result.role == "kind1"


def test_field_match_kinderbetreuung_kind2(family: Family) -> None:
    """Kinderbetreuung mit kind_name (Tim) → kind2."""
    result = infer_person_field(
        "kinderbetreuung", _kinderbetreuung("Tim Muster"), family
    )
    assert result.role == "kind2"


def test_field_match_hypothek(family: Family) -> None:
    """Hypothek mit kontoinhaber_name → analog."""
    result = infer_person_field(
        "hypothek_zinsbestaetigung", _hypothek("Hans Muster"), family
    )
    assert result.role == "mann"


def test_field_match_liegenschaftsunterhalt(family: Family) -> None:
    """Liegenschaftsunterhalt mit eigentuemer_name → analog."""
    result = infer_person_field(
        "liegenschaftsunterhalt", _liegenschaft("Hans Muster"), family
    )
    assert result.role == "mann"


def test_field_match_krankheitskosten(family: Family) -> None:
    """Krankheitskosten mit person_name → analog."""
    result = infer_person_field(
        "krankheitskosten", _krankheit("Lina Muster"), family
    )
    assert result.role == "kind1"


def test_field_match_field_missing_on_raw(family: Family) -> None:
    """raw hat das person-relevante Feld nicht (defensiv) → role=None.

    Wenn ein anderer (z.B. Bank-)raw fälschlicherweise mit belegtyp=spenden
    aufgerufen wird, hat er kein ``spender_name``-Attribut. getattr-Default
    None fängt das ab und liefert role=None.
    """
    bank_raw = _bank("Hans Muster")
    result = infer_person_field("spenden", bank_raw, family)
    assert result.role is None
