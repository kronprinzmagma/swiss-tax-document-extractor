"""extractors-Package: flache Modul-Struktur für die Lohnausweis-Pipeline (Phase 1+2).

Öffentliche API. Phase 2 erweitert um drei zusätzliche Belegtypen
(Bank-Zinsausweis, KK-Prämienbescheinigung, Säule 3a), das ``Belegtyp``-Literal
und ``MANDATORY_FIELDS_FOR_BELEGTYP`` als Single Source of Truth für
Pflichtfeld-Listen.
"""
from extractors.anchor_resolver import resolve_field
from extractors.classifier import classify
from extractors.confidence import (
    classify_werterhaltend,
    compose_confidence,
    run_plausibility,
)
from extractors.family import Family, FamilyMember, Role, load_family
from extractors.llm_classifier import CLASSIFIER_FALLBACK_THRESHOLD, classify_via_llm
from extractors.llm_extract import DEFAULT_MODEL, extract, get_model_digest
from extractors.numbers import normalize, parse_swiss_amount, parse_swiss_date
from extractors.pdf_reader import read_pdf
from extractors.person_inference import (
    PERSON_RELEVANT_FIELD,
    PersonResult,
    PersonRole,
    infer_person,
    infer_person_field,
)
from extractors.schema import (
    MANDATORY_FIELDS_FOR_BELEGTYP,
    BankZinsausweisRaw,
    Belegtyp,
    BerufsauslagenRaw,
    ClassifierFallbackResult,
    HypothekZinsbestaetigungRaw,
    KinderbetreuungRaw,
    KkPraemienbescheinigungRaw,
    KrankheitskostenRaw,
    LiegenschaftsunterhaltRaw,
    LohnausweisRaw,
    ReasonCode,
    Saeule3aRaw,
    SpendenquittungRaw,
    TaggedField,
    WertschriftenverzeichnisRaw,
)
from extractors.tokenize import TaggedWord, tokenize

__all__ = [
    "CLASSIFIER_FALLBACK_THRESHOLD",
    "DEFAULT_MODEL",
    "MANDATORY_FIELDS_FOR_BELEGTYP",
    "PERSON_RELEVANT_FIELD",
    "BankZinsausweisRaw",
    "Belegtyp",
    "BerufsauslagenRaw",
    "ClassifierFallbackResult",
    "Family",
    "FamilyMember",
    "HypothekZinsbestaetigungRaw",
    "KinderbetreuungRaw",
    "KkPraemienbescheinigungRaw",
    "KrankheitskostenRaw",
    "LiegenschaftsunterhaltRaw",
    "LohnausweisRaw",
    "PersonResult",
    "PersonRole",
    "ReasonCode",
    "Role",
    "Saeule3aRaw",
    "SpendenquittungRaw",
    "TaggedField",
    "TaggedWord",
    "WertschriftenverzeichnisRaw",
    "classify",
    "classify_via_llm",
    "classify_werterhaltend",
    "compose_confidence",
    "extract",
    "get_model_digest",
    "infer_person",
    "infer_person_field",
    "load_family",
    "normalize",
    "parse_swiss_amount",
    "parse_swiss_date",
    "read_pdf",
    "resolve_field",
    "run_plausibility",
    "tokenize",
]
