"""pydantic-Modelle, ``TaggedField[T]``-Envelope und ``ReasonCode``-Enum.

Definiert das Output-Schema, das Ollama im JSON-Schema-Mode (XGrammar) füllt:
``LohnausweisRaw`` mit den positionsbenannten Pflicht- und Optional-Feldern
gemäss ESTV-Wegleitung 2024 (Pos 8 Bruttolohn, Pos 9 AHV/ALV/NBU,
Pos 10a BVG, Pos 11 Nettolohn, Pos 12 Quellensteuer — siehe 01-CONTEXT.md
§canonical_refs und 01-RESEARCH.md §Pitfall 1).

Wichtige Design-Entscheidungen:

* Beträge sind als ``str`` typisiert, nicht als ``Decimal`` — XGrammar/JSON-
  Schema kann den Schweizer Apostroph-Präfix nicht über das ``number``-Type
  durchlassen. Decimal-Konvertierung erfolgt POST-LLM via
  :func:`extractors.numbers.parse_swiss_amount` (RESEARCH §Pattern 2).
* :class:`TaggedField` ist generisch (``TaggedField[T]``) und erzwingt
  ``min_length=1`` auf ``tag_refs`` — kein Wert ohne Tag-Beleg (D-B5).
* :attr:`LohnausweisRaw.model_config` setzt ``extra="forbid"``, damit
  XGrammar im resultierenden JSON-Schema keine zusätzlichen Felder
  zulässt.

Reines Modul — kein I/O.
"""
from __future__ import annotations

from enum import Enum
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Belegtyp-Literal (D-D6, 02-CONTEXT.md)
# ---------------------------------------------------------------------------

#: Geschlossener Belegtyp-Katalog. Wird von ``extractors.classifier.classify()``
#: zurückgegeben und steuert den Schema-Dispatch in
#: :func:`extractors.llm_extract.extract` und in
#: :data:`MANDATORY_FIELDS_FOR_BELEGTYP`. Erweiterungen (z.B. Wertschriften,
#: Spende, Hypothek) erfolgen explizit pro Phase — kein „free text type".
Belegtyp = Literal[
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
    # Freizügigkeitskonto (2. Säule): Guthaben erst bei Bezug steuerbar →
    # immer out-of-scope. Eigener Typ, damit es NICHT als bank_zinsausweis
    # ins Wertschriftenverzeichnis wandert (Finding E5).
    "freizuegigkeitskonto",
    "unknown",
]

T = TypeVar("T")


class ReasonCode(str, Enum):
    """Geschlossener Reason-Code-Katalog gemäss D-D3 (01-CONTEXT.md).

    String-Enum, damit die Werte direkt JSON-serialisierbar sind und in
    den Bucket-CSVs (``unprocessed.csv`` Spalte ``reason_code``) ohne
    weiteren Adapter geschrieben werden können.
    """

    UNSUPPORTED_TYPE = "unsupported_type"
    TEXT_EXTRACT_FAILED = "text_extract_failed"
    OCR_FAILED = "ocr_failed"
    LLM_EXTRACT_FAILED = "llm_extract_failed"
    SCHEMA_VALIDATION_FAILED = "schema_validation_failed"
    ANCHOR_RESOLUTION_FAILED = "anchor_resolution_failed"
    MULTIPLE_BELEGTYP_MATCH = "multiple_belegtyp_match"


class TaggedField(BaseModel, Generic[T]):
    """Generischer Wert-Envelope mit optionalen Tag-Referenzen.

    Reale Semantik (siehe Modul-Docstring von :mod:`extractors.llm_extract`):
    ``tag_refs`` ist ein **optionales** Feld mit ``default_factory=list``. Der
    LLM bekommt sauberen Klartext OHNE ``[T<id>]``-Tags und muss daher KEINE
    Tag-IDs liefern — das war historisch die häufigste Fehlerquelle. Die
    Anker-Suche (Bounding-Box im Original-PDF) übernimmt POST-LLM der
    Verbatim-Fallback in :mod:`extractors.anchor_resolver`; ``tag_refs`` bleibt
    in der Regel leer und ist KEIN Pflichtfeld.

    Felder ``confidence`` und ``anchor`` werden vom LLM **nicht** befüllt
    (LLM-Self-Confidence wird per D-B5 ignoriert) — sie sind Optional
    und werden nach der Anker-Resolution gesetzt. Im JSON-Schema, das
    Ollama als ``format=`` bekommt, sind sie nullable.
    """

    value: T
    tag_refs: list[int] = Field(
        default_factory=list,
        description="Tag-IDs (optional, bleibt i.d.R. leer — Anker liefert der Verbatim-Fallback im Anchor-Resolver).",
    )


class LohnausweisRaw(BaseModel):
    """LLM-Output-Schema für den Schweizer Lohnausweis (Form 11).

    Pflichtfelder (Header + ESTV-Pflichtpositionen):

    * ``arbeitgeber`` — Header.
    * ``periode_von`` / ``periode_bis`` — Lohnperiode (Header).
    * ``bruttolohn_pos8`` — Pos 8 Bruttolohn total.
    * ``ahv_alv_nbu_abzug_pos9`` — Pos 9 AHV/IV/EO/ALV/NBUV-Abzug.
    * ``nettolohn_pos11`` — Pos 11 Nettolohn (= Pos 8 − Pos 9 − Pos 10).

    Optional:

    * ``bvg_abzug_pos10a`` — Pos 10.1 BVG-Beitrag (Default ``None``,
      kann bei Niedriglohn unter BVG-Eintrittsschwelle fehlen).
    * ``quellensteuer_pos12`` — Pos 12 Quellensteuer (nur bei
      quellensteuer­pflichtigen Personen).

    Beträge sind ``str`` — Decimal-Konvertierung POST-LLM via
    :func:`extractors.numbers.parse_swiss_amount` (RESEARCH §Pattern 2).
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    arbeitgeber: TaggedField[str] = Field(
        ...,
        description="Voll-Name + Adresse des Arbeitgebers aus dem Kopfteil (z.B. 'ACME AG, 8000 Zürich'). tag_refs MUSS ALLE Wörter des Namens samt Adresse umfassen.",
    )
    arbeitnehmer_name: TaggedField[str] | None = Field(
        default=None,
        description="Name des Arbeitnehmers / der versicherten Person (z.B. 'Hans Muster'). Steht meist im Adressfeld nach 'Herr'/'Frau'.",
    )
    periode_von: TaggedField[str] = Field(
        ..., description="Beginn der Lohnperiode (TT.MM.JJJJ, z.B. '01.01.2024')."
    )
    periode_bis: TaggedField[str] = Field(
        ..., description="Ende der Lohnperiode (TT.MM.JJJJ, z.B. '31.12.2024')."
    )
    bruttolohn_pos8: TaggedField[str] = Field(
        ...,
        description="Pos 8 'Bruttolohn total' — der CHF-Betrag rechts neben der Pos-8-Zeile. tag_refs MUSS auf den Zahlenwert zeigen, NICHT auf die Label-Wörter.",
    )
    ahv_alv_nbu_abzug_pos9: TaggedField[str] = Field(
        ...,
        description="Pos 9 'AHV/IV/EO/ALV/NBUV-Abzug' — der CHF-Betrag rechts neben Pos 9. tag_refs MUSS auf den Zahlenwert zeigen.",
    )
    nettolohn_pos11: TaggedField[str] = Field(
        ...,
        description="Pos 11 'Nettolohn' — der CHF-Betrag rechts neben Pos 11. tag_refs MUSS auf den Zahlenwert zeigen.",
    )
    bvg_abzug_pos10a: TaggedField[str] | None = Field(
        default=None,
        description="Pos 10.1 'Berufliche Vorsorge (BVG)' — der CHF-Betrag rechts neben dieser Zeile. PFLICHTFELD bei Bruttolohn ≥ 22'050 CHF (BVG-Eintrittsschwelle). Darf NUR null sein, wenn Pos 10.1 im Beleg gar nicht erscheint. tag_refs MUSS auf den Zahlenwert zeigen, NICHT auf das Label 'Berufliche Vorsorge (BVG)'.",
    )
    quellensteuer_pos12: TaggedField[str] | None = Field(
        default=None,
        description="Pos 12 'Quellensteuer-Abzug' — nur bei quellensteuerpflichtigen Personen vorhanden. CHF-Betrag rechts neben Pos 12. tag_refs auf den Zahlenwert.",
    )


class BankZinsausweisRaw(BaseModel):
    """LLM-Output-Schema für den Schweizer Bank-Zinsausweis (Form 340 / Steuerausweis).

    Pflichtfelder (D-C1, 02-CONTEXT.md):

    * ``institut`` — Name der Bank (z.B. „ZKB Zürcher Kantonalbank").
    * ``kontoinhaber_name`` — für Person-Inferenz (D-B1..B5).
    * ``bruttoertrag`` — Zinserträge im Steuerjahr, CHF.
    * ``vermoegensstand_3112`` — Saldo zum Stichtag 31.12., CHF.
    * ``verrechnungssteuer`` — abgezogene VRS, CHF (Plausibility ≈ 35% × Brutto).
    * ``jahr`` — 4-stellig.

    Optional (D-C2):

    * ``konto_nr_redacted`` — last-4-Pattern (z.B. „XXXX-1234"); nicht alle
      Banken drucken das Konto sichtbar — daher optional.
    * ``kontotyp`` — z.B. „Sparkonto", „Privatkonto"; deklarativ und nicht
      durchgängig auf den Auszügen vorhanden.

    Beträge sind ``str`` — Decimal-Konvertierung POST-LLM via
    :func:`extractors.numbers.parse_swiss_amount` (XGrammar-Constraint).
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    institut: TaggedField[str] = Field(
        ...,
        description="Name der Bank (z.B. 'ZKB Zürcher Kantonalbank', 'UBS', 'Raiffeisen Schweiz').",
    )
    kontoinhaber_name: TaggedField[str] = Field(
        ...,
        description="Voll-Name der kontoinhabenden Person(en) (für Person-Match). Bei Joint-Accounts beide Namen.",
    )
    bruttoertrag: TaggedField[str] = Field(
        ...,
        description="Bruttoertrag der Zinsen im Jahr in CHF. tag_refs MUSS auf den CHF-Zahlenwert zeigen, NICHT auf das Label 'Bruttoertrag' oder 'Zinsen'.",
    )
    vermoegensstand_3112: TaggedField[str] = Field(
        ...,
        description="Vermögensstand am Stichtag 31.12. in CHF. tag_refs MUSS auf den CHF-Zahlenwert zeigen, NICHT auf das Label 'Vermögensstand per 31.12.'.",
    )
    verrechnungssteuer: TaggedField[str] = Field(
        ...,
        description="Abgezogene Verrechnungssteuer in CHF (typisch 35% des Bruttoertrags). tag_refs MUSS auf den CHF-Zahlenwert zeigen.",
    )
    jahr: TaggedField[str] = Field(..., description="4-stelliges Steuerjahr (z.B. '2024').")
    konto_nr_redacted: TaggedField[str] | None = Field(
        default=None, description="Optionale Konto-Nr im last-4-Pattern (z.B. 'XXXX-1234')."
    )
    kontotyp: TaggedField[str] | None = Field(
        default=None, description="Optionaler Kontotyp (z.B. 'Sparkonto', 'Privatkonto')."
    )


class KkPraemienbescheinigungRaw(BaseModel):
    """LLM-Output-Schema für die Schweizer Krankenkassen-Prämienbescheinigung (Ziff. 16).

    Pflichtfelder (D-C3, 02-CONTEXT.md):

    * ``kasse`` — Krankenversicherer (z.B. „Helsana", „Sanitas", „CSS").
    * ``versicherte_person_name`` — für Person-Inferenz (D-B1..B5).
    * ``jahr`` — 4-stellig.
    * ``praemie_kvg_total`` — Total Grundversicherung KVG im Jahr, CHF
      (Pflichtfeld; KVG-Prämie ist immer abziehbar).

    Optional (D-C4):

    * ``praemie_vvg_total`` — Total Zusatzversicherung VVG im Jahr, CHF;
      nur abziehbar wenn obligatorische Erwerbsausfallversicherung —
      VVG-Plausibilitätsregel kommt in Phase 3.
    * ``mitversicherte_kinder_namen`` — Liste der mitversicherten Kinder
      (für Phase-3-Mehrfach-Person-Mapping). Default: leere Liste.

    Beträge sind ``str`` — Decimal-Konvertierung POST-LLM.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    kasse: TaggedField[str]
    versicherte_person_name: TaggedField[str]
    jahr: TaggedField[str]
    praemie_kvg_total: TaggedField[str]
    praemie_vvg_total: TaggedField[str] | None = None
    # Prämie Total — nur wenn das Dokument KVG/VVG NICHT getrennt ausweist.
    praemie_total: TaggedField[str] | None = None
    # Selbstgetragene Krankheits- und Unfallkosten (260904-rmx).
    #
    # Ein KK-Jahresauszug enthält zwei steuerlich VERSCHIEDENE Dinge: die
    # Prämien (Versicherungsabzug) und die selbst getragenen Behandlungskosten
    # — Franchise, Selbstbehalt, Spitalbeitrag und nicht versicherte
    # Behandlungen. Letztere gehören in den Abzug für Krankheits- und
    # Unfallkosten (Steuerbuch ZH §32.1, Formular StA 370), der erst greift,
    # soweit er 5 % des Reineinkommens übersteigt.
    #
    # regex_extract.py erkennt die Labels bereits (KK_CSS_SELBST_KOSTEN,
    # KK_SANITAS_NICHTVERSICHERT, KK_VISANA_NICHT_GETRAGEN) und
    # build_tax_output.TRANSFER_MAP erwartet das Feld — es fehlte nur hier,
    # weshalb der extrahierte Wert bei der Zuweisung lautlos verfiel.
    selbstgetragene_kosten: TaggedField[str] | None = None
    mitversicherte_kinder_namen: list[TaggedField[str]] = Field(
        default_factory=list,
        max_length=10,  # hartes Cap gegen LLM-Hallucination-Loops, in denen
                        # leere {"value":"","tag_refs":[]}-Einträge endlos
                        # angehängt werden → JSON-Output >12k Zeichen, EOF-Fehler
    )


class Saeule3aRaw(BaseModel):
    """LLM-Output-Schema für die Schweizer Säule-3a-Bescheinigung (Ziff. 14).

    Pflichtfelder (D-C5, 02-CONTEXT.md):

    * ``stiftung`` — 3a-Stiftung (z.B. „VIAC", „Frankly", „PostFinance
      Vorsorge 3a").
    * ``kontoinhaber_name`` — für Person-Inferenz.
    * ``jahr`` — 4-stellig.
    * ``einzahlung_betrag`` — Total der Einzahlungen im Jahr, CHF.
      Plausibility-Limits via Jahr-Map: 2024 = CHF 7'056 (Erwerbstätige
      mit BVG), 2025 = CHF 7'258. Selbständige ohne BVG: 2024 = 35'280,
      2025 = 36'288. Implementierung in :mod:`extractors.confidence`
      via ``LIMITS_BY_YEAR``-Dict.

    Optional (D-C6):

    * ``kontonummer_redacted`` — last-4-Pattern.
    * ``valutadatum`` — Datum der Einzahlung; nur wenn auf Bescheinigung
      sichtbar.

    Beträge sind ``str`` — Decimal-Konvertierung POST-LLM.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    stiftung: TaggedField[str]
    kontoinhaber_name: TaggedField[str]
    jahr: TaggedField[str]
    einzahlung_betrag: TaggedField[str]
    kontonummer_redacted: TaggedField[str] | None = None
    valutadatum: TaggedField[str] | None = None


# ---------------------------------------------------------------------------
# Phase-3-Stubs (Plan 03-01, Wave 1) — Skelette für die 7 neuen Belegtypen.
#
# Diese Klassen definieren nur die Pflicht-/Optional-Feld-Struktur mit
# ``TaggedField[str]``-Envelope und ``extra="forbid"``. Die ausführlichen
# ``Field(description=...)``-Hinweise nach dem PLAN-06-Pattern (Kernregel:
# "tag_refs MUSS auf den CHF-Zahlenwert zeigen, NICHT auf das Label") werden
# in Wave 3 + 4 (Plan 03-04 + 03-05) ergänzt — analog zu Phase 1+2.
# ---------------------------------------------------------------------------


class WertschriftenverzeichnisRaw(BaseModel):
    """LLM-Output-Schema für das Schweizer Wertschriftenverzeichnis / Depot-Auszug (D-A1).

    Total-only in v1 — Einzelpositionen pro Aktie/Obligation/Fund sind v2
    deferred (D-A2, 03-CONTEXT.md). eCH-0196-XML-Parser ist v1.x deferred
    (D-A3). Pflichtfelder (D-A1):

    * ``institut`` — Name der depotführenden Bank.
    * ``kontoinhaber_name`` — für Person-Inferenz (D-D1).
    * ``bestand_3112`` — Depot-Bestand zum Stichtag 31.12. in CHF.
    * ``bruttoertrag_total`` — Total der Bruttoerträge (Zinsen + Dividenden) im Jahr.
    * ``verrechnungssteuer_total`` — abgezogene VRS in CHF.
    * ``jahr`` — 4-stellig.

    Optional:

    * ``depot_nr_redacted`` — last-4-Pattern.
    * ``waehrung`` — Default CHF.

    Field-Descriptions per PLAN-06-Pattern (Wave 3, Plan 03-04):
    "tag_refs MUSS auf den CHF-Zahlenwert zeigen, NICHT auf das Label".

    Beträge sind ``str`` — Decimal-Konvertierung POST-LLM via
    :func:`extractors.numbers.parse_swiss_amount`.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    institut: TaggedField[str] = Field(
        ...,
        description="Name Bank/Broker (z.B. 'UBS Switzerland AG', 'ZKB Zürcher Kantonalbank', 'VIAC'). tag_refs MUSS ALLE Wörter des Namens umfassen.",
    )
    kontoinhaber_name: TaggedField[str] = Field(
        ...,
        description="Voll-Name der depot-haltenden Person(en) für Person-Match. Bei Joint-Depots beide Namen (z.B. 'Hans und Maria Muster').",
    )
    bestand_3112: TaggedField[str] = Field(
        ...,
        description="Depot-Total / Bestand per 31.12. in CHF (Summe aller Positionen — v1 Total-only, D-A2). tag_refs MUSS auf den CHF-Zahlenwert zeigen, NICHT auf das Label 'Bestand per 31.12.' oder 'Depot-Total'.",
    )
    bruttoertrag_total: TaggedField[str] = Field(
        ...,
        description="Summe aller Bruttoerträge (Dividenden + Zinsen + Ausschüttungen) im Jahr in CHF. tag_refs MUSS auf den CHF-Zahlenwert zeigen, NICHT auf das Label 'Bruttoertrag total' oder 'Total Erträge'.",
    )
    verrechnungssteuer_total: TaggedField[str] = Field(
        ...,
        description="Summe abgezogener Verrechnungssteuer (VRS) in CHF (typisch ≈35% des Brutto auf CH-Quellen). tag_refs MUSS auf den CHF-Zahlenwert zeigen, NICHT auf das Label 'Verrechnungssteuer'.",
    )
    jahr: TaggedField[str] = Field(..., description="4-stelliges Steuerjahr (z.B. '2024').")
    depot_nr_redacted: TaggedField[str] | None = Field(
        default=None,
        description="Optionale Depot-Nr im last-4-Pattern (z.B. 'XXXX-9876').",
    )
    waehrung: TaggedField[str] | None = Field(
        default=None,
        description="Optionale Währung (Default CHF wenn nicht angegeben).",
    )


class SpendenquittungRaw(BaseModel):
    """LLM-Output-Schema für Schweizer Spendenquittungen (D-E1).

    Pflichtfelder:

    * ``empfaenger`` — gemeinnützige Organisation, Stiftung, Partei etc.
    * ``spender_name`` — für Person-Inferenz (D-D1).
    * ``betrag`` — Spendenbetrag in CHF.
    * ``jahr`` — 4-stellig.

    Optional:

    * ``steuerbefreiungs_status`` — Hinweis im Beleg ob die Organisation
      steuerbefreit / gemeinnützig anerkannt ist (str — "gemeinnützig" /
      "nicht gemeinnützig" / null wenn nicht angegeben).

    Field-Descriptions per PLAN-06-Pattern (Wave 3, Plan 03-04).
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    empfaenger: TaggedField[str] = Field(
        ...,
        description="Name der gemeinnützigen Organisation / Stiftung (z.B. 'WWF Schweiz', 'Schweizerisches Rotes Kreuz'). tag_refs MUSS ALLE Wörter des Namens umfassen.",
    )
    spender_name: TaggedField[str] = Field(
        ...,
        description="Voll-Name der spendenden Person für Person-Match (z.B. 'Hans Muster').",
    )
    betrag: TaggedField[str] = Field(
        ...,
        description="Spendenbetrag in CHF. tag_refs MUSS auf den CHF-Zahlenwert zeigen, NICHT auf das Label 'Spende' / 'Spendenbetrag' / 'Betrag'. KEINE Berechnung, kein Erfinden — Wert wortwörtlich aus dem Beleg.",
    )
    jahr: TaggedField[str] = Field(..., description="4-stelliges Steuerjahr (z.B. '2024').")
    steuerbefreiungs_status: TaggedField[str] | None = Field(
        default=None,
        description="Optionaler Hinweis im Beleg auf Gemeinnützigkeit / Steuerbefreiung (z.B. 'Die Organisation ist als gemeinnützig anerkannt').",
    )


class BerufsauslagenRaw(BaseModel):
    """LLM-Output-Schema für Berufsauslagen / Weiterbildungs-Bestätigung (D-E2).

    Pflichtfelder:

    * ``anbieter`` — Kurs-/Weiterbildungs-Anbieter (Schule, Akademie, Institut).
    * ``person_name`` — für Person-Inferenz (D-D1).
    * ``betrag`` — Kurskosten in CHF.
    * ``jahr`` — 4-stellig.

    Optional:

    * ``kursart`` — z.B. "Sprachkurs", "Zertifikat", "Diplomstudiengang".

    Field-Descriptions per PLAN-06-Pattern (Wave 3, Plan 03-04).
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    anbieter: TaggedField[str] = Field(
        ...,
        description="Kurs-/Weiterbildungs-Anbieter (Schule, Akademie, Hochschule — z.B. 'EB Zürich', 'Universität St. Gallen', 'Klubschule Migros'). tag_refs MUSS ALLE Wörter des Namens umfassen.",
    )
    person_name: TaggedField[str] = Field(
        ...,
        description="Voll-Name der teilnehmenden Person für Person-Match (z.B. 'Hans Muster').",
    )
    betrag: TaggedField[str] = Field(
        ...,
        description="Kurskosten gesamt in CHF. tag_refs MUSS auf den CHF-Zahlenwert zeigen, NICHT auf das Label 'Kurskosten' / 'Kursgebühr' / 'Total'. KEINE Berechnung — Wert wortwörtlich aus dem Beleg.",
    )
    jahr: TaggedField[str] = Field(..., description="4-stelliges Steuerjahr (z.B. '2024').")
    kursart: TaggedField[str] | None = Field(
        default=None,
        description="Optionale Kursart / Studiengang-Bezeichnung (z.B. 'MAS', 'CAS', 'Sprachkurs', 'Weiterbildung').",
    )


class KinderbetreuungRaw(BaseModel):
    """LLM-Output-Schema für Kinderbetreuungs-Bestätigung (Kita/Hort/Tagesfamilie, D-E3).

    Pflichtfelder:

    * ``anbieter`` — Kita, Hort, Tagesfamilie (Trägerschaft/Institution).
    * ``kind_name`` — für Person-Inferenz (D-D1, Match auf Kind-Rolle).
    * ``betrag`` — Betreuungskosten im Jahr in CHF. Plausibility-Cap
      ZH 2024 = CHF 25'000 pro Kind (D-E3, Plan 03-05).
    * ``jahr`` — 4-stellig.

    Optional:

    * ``betreuungs_typ`` — z.B. "Kita", "Hort", "Tagesfamilie".

    Field-Descriptions per PLAN-06-Pattern (Wave 4, Plan 03-05):
    "tag_refs MUSS auf den CHF-Zahlenwert zeigen, NICHT auf das Label".
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    anbieter: TaggedField[str] = Field(
        ...,
        description="Name der Betreuungs-Institution (z.B. 'Kita Sonnenschein', 'Hort am Berg', 'Tagesfamilie Müller'). tag_refs MUSS ALLE Wörter des Namens umfassen.",
    )
    kind_name: TaggedField[str] = Field(
        ...,
        description="Voll-Name des betreuten Kindes für Person-Match auf kind1/kind2 (z.B. 'Lina Muster').",
    )
    betrag: TaggedField[str] = Field(
        ...,
        description="Jahresbetrag Betreuungskosten in CHF. tag_refs MUSS auf den CHF-Zahlenwert zeigen, NICHT auf das Label 'Total' / 'Betreuungskosten' / 'Kurskosten'. ZH 2024 Plausibility-Cap: CHF 25'000 pro Kind.",
    )
    jahr: TaggedField[str] = Field(..., description="4-stelliges Steuerjahr (z.B. '2024').")
    betreuungs_typ: TaggedField[str] | None = Field(
        default=None,
        description="Optionale Art der Betreuung (z.B. 'Kita', 'Hort', 'Tagesfamilie', 'Mittagstisch').",
    )


class HypothekZinsbestaetigungRaw(BaseModel):
    """LLM-Output-Schema für Hypothek-Zinsbestätigung (D-E4).

    Pflichtfelder:

    * ``institut`` — Hypothek-gebende Bank (z.B. UBS, ZKB, Migros Bank).
    * ``kontoinhaber_name`` — für Person-Inferenz (D-D1).
    * ``liegenschaft`` — Adresse oder Kurzbezeichnung der hypothezierten
      Liegenschaft.
    * ``schuldzinsen`` — Jahres-Schuldzinsen in CHF.
    * ``schuldsaldo_3112`` — Hypothek-Saldo per 31.12. in CHF.
    * ``jahr`` — 4-stellig.

    Plausibility (Plan 03-05, Pitfall 8 RESEARCH): schuldzinsen /
    schuldsaldo_3112 ∈ [0.005, 0.10] (weiter Range für Mischhypothek-
    Edge-Cases). Ausserhalb → PENALTY_MILD.

    Field-Descriptions per PLAN-06-Pattern (Wave 4, Plan 03-05).
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    institut: TaggedField[str] = Field(
        ...,
        description="Name der Hypothek-gebenden Bank (z.B. 'UBS', 'ZKB Zürcher Kantonalbank', 'Raiffeisen Schweiz', 'Migros Bank'). tag_refs MUSS ALLE Wörter des Namens umfassen.",
    )
    kontoinhaber_name: TaggedField[str] = Field(
        ...,
        description="Voll-Name der hypothekarisch verschuldeten Person(en) für Person-Match. Bei Joint-Hypothek beide Namen samt Bindewort (z.B. 'Hans und Maria Muster').",
    )
    liegenschaft: TaggedField[str] = Field(
        ...,
        description="Adresse oder Kurzbezeichnung der Liegenschaft (z.B. 'EFH Bahnhofstr. 10, 8001 Zürich', 'ETW Seefeldstr. 22'). tag_refs MUSS alle Wörter umfassen.",
    )
    schuldzinsen: TaggedField[str] = Field(
        ...,
        description="Jahresbetrag Hypothekarzinsen in CHF. tag_refs MUSS auf den CHF-Zahlenwert zeigen, NICHT auf das Label 'Schuldzinsen' / 'Hypothekarzinsen'. Plausibility-Range: Zinsen ≈ 0.5%–10% × Saldo (Mischhypothek-tolerant).",
    )
    schuldsaldo_3112: TaggedField[str] = Field(
        ...,
        description="Schuldsaldo / Hypothekarsaldo per 31.12. in CHF. tag_refs MUSS auf den CHF-Zahlenwert zeigen, NICHT auf das Label 'Bestand per 31.12.' / 'Schuldsaldo' / 'Saldo'.",
    )
    jahr: TaggedField[str] = Field(..., description="4-stelliges Steuerjahr (z.B. '2024').")


class LiegenschaftsunterhaltRaw(BaseModel):
    """LLM-Output-Schema für Liegenschaftsunterhalt / Handwerker-Rechnungen (D-E5).

    Wahlrecht Pauschale 20% vs. effektiv ist USER-ENTSCHEIDUNG —
    Tool extrahiert nur die Einzelpositionen + werterhaltend-Flag.
    Pflichtfelder:

    * ``handwerker_anbieter`` — ausstellender Handwerker / Dienstleister.
    * ``eigentuemer_name`` — für Person-Inferenz (D-D1).
    * ``liegenschaft`` — Adresse oder Kurzbezeichnung.
    * ``betrag`` — Rechnungsbetrag in CHF.
    * ``jahr`` — 4-stellig.

    Optional:

    * ``werterhaltend`` — bool | null. Wird POST-LLM via
      :func:`extractors.confidence.classify_werterhaltend` auf
      ``handwerker_anbieter`` + ``page1_text`` aufgelöst (Regex-
      Heuristik, kein LLM-Call): "Reparatur/Erhaltung/Renovation/
      Service/Wartung" → True, "Anbau/Neubau/Umbau/Erweiterung/Ausbau"
      → False, sonst None + Report-Warnung. Pipeline-Integration in
      Plan 03-06. LLM lässt das Feld null.

    Field-Descriptions per PLAN-06-Pattern (Wave 4, Plan 03-05).
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    handwerker_anbieter: TaggedField[str] = Field(
        ...,
        description="Name des Handwerkers / der Firma (z.B. 'Maler Müller AG, Zürich', 'BauPlan Schweiz GmbH'). tag_refs MUSS ALLE Wörter des Namens umfassen.",
    )
    eigentuemer_name: TaggedField[str] = Field(
        ...,
        description="Voll-Name der Eigentümer-Person(en) für Person-Match (z.B. 'Hans Muster' oder 'Hans und Maria Muster' bei gemeinsamem Eigentum).",
    )
    liegenschaft: TaggedField[str] = Field(
        ...,
        description="Adresse oder Kurzbezeichnung der Liegenschaft (z.B. 'EFH Bahnhofstr. 10, 8001 Zürich'). tag_refs MUSS alle Wörter umfassen.",
    )
    betrag: TaggedField[str] = Field(
        ...,
        description="Rechnungsbetrag in CHF. tag_refs MUSS auf den CHF-Zahlenwert zeigen, NICHT auf das Label 'Total brutto' / 'Rechnungsbetrag' / 'Total'. KEINE Berechnung — Wert wortwörtlich aus dem Beleg.",
    )
    jahr: TaggedField[str] = Field(..., description="4-stelliges Steuerjahr (z.B. '2024').")
    werterhaltend: TaggedField[str] | None = Field(
        default=None,
        description="LASS DIESES FELD WEG (null). werterhaltend (Reparatur/Erhaltung) vs. wertvermehrend (Anbau/Neubau) wird POST-LLM via Regex-Heuristik (classify_werterhaltend) im Tool-Pipeline gesetzt — NICHT durch das LLM.",
    )


class KrankheitskostenRaw(BaseModel):
    """LLM-Output-Schema für Krankheitskosten / Arzt-/Apotheken-/Klinik-Rechnung (D-E6).

    Selbstbehalt 5% Nettoeinkommen (ZH Ziff. 22.1 / Form 370) ist
    Cross-Beleg — wird in v2 berechnet. Phase 3 extrahiert nur die
    Einzelpositionen ohne Plausibility-Check. Pflichtfelder:

    * ``leistungserbringer`` — Arzt, Apotheke, Klinik, Therapeut.
    * ``person_name`` — für Person-Inferenz (D-D1).
    * ``betrag`` — Rechnungsbetrag (nach Kassenanteil, falls auf dem
      Beleg vorhanden) in CHF.
    * ``jahr`` — 4-stellig.

    Optional:

    * ``kategorie`` — z.B. "Arzt", "Medikament", "Therapie".

    Field-Descriptions per PLAN-06-Pattern (Wave 4, Plan 03-05).
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    leistungserbringer: TaggedField[str] = Field(
        ...,
        description="Name des Leistungserbringers (Arzt/Apotheke/Klinik/Therapeut, z.B. 'Dr. med. Hans Meier, Zürich', 'Apotheke zur Krone', 'Spital Triemli'). tag_refs MUSS ALLE Wörter umfassen.",
    )
    person_name: TaggedField[str] = Field(
        ...,
        description="Voll-Name der behandelten Person für Person-Match (z.B. 'Maria Muster', 'Tim Muster').",
    )
    betrag: TaggedField[str] = Field(
        ...,
        description="Restbetrag nach Kassenanteil (Selbstbehalt + Franchise + nicht-gedeckt) in CHF. tag_refs MUSS auf den CHF-Zahlenwert zeigen, NICHT auf das Label 'Rechnungsbetrag' / 'Total' / 'Patientenanteil'. KEINE Berechnung — Wert wortwörtlich aus dem Beleg.",
    )
    jahr: TaggedField[str] = Field(..., description="4-stelliges Steuerjahr (z.B. '2024').")
    kategorie: TaggedField[str] | None = Field(
        default=None,
        description="Optionale Kategorie (z.B. 'Arzt', 'Medikament', 'Therapie', 'Spital', 'Zahnarzt').",
    )


# ---------------------------------------------------------------------------
# ClassifierFallbackResult — Output-Schema für LLM-Fallback-Klassifikator
# (D-C1..C6, CLS-02 step 3 — Plan 03-06 Wave 5).
# ---------------------------------------------------------------------------


class ClassifierFallbackResult(BaseModel):
    """LLM-Fallback-Klassifikator-Output (D-C1..C6, CLS-02 step 3).

    Wird via :mod:`extractors.llm_classifier` produziert, wenn alle 11
    Header-Regex-Scores == 0 sind und der regelbasierte Klassifikator
    keinen Belegtyp identifizieren konnte. XGrammar erzwingt über das
    ``Belegtyp``-Literal, dass das LLM nur einen der 12 zulässigen
    Werte (11 Belegtypen + ``"unknown"``) ausgibt.

    ``confidence`` dient NUR der Threshold-Entscheidung (< 0.6 ⇒ Caller
    setzt das Resultat auf ``("unknown", UNSUPPORTED_TYPE)`` — D-C4).
    Der Wert fliesst NICHT in :func:`extractors.confidence.compose_confidence`
    der Feld-Konfidenzen ein (Pitfall 7) — LLM-Self-Confidence bleibt
    ignoriert (D-B5).
    """

    model_config = ConfigDict(extra="forbid")

    belegtyp: Belegtyp = Field(
        ...,
        description="Einer der 11 Schweizer Steuer-Belegtypen oder 'unknown' bei Unsicherheit.",
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Modell-Selbsteinschätzung 0..1 — nur für Threshold-Entscheidung (D-C4).",
    )
    reasoning: str = Field(
        ...,
        description="Kurzer Hinweis-Text (1-2 Sätze) — für Debugging im Run-Report.",
    )


# ---------------------------------------------------------------------------
# MANDATORY_FIELDS_FOR_BELEGTYP — Single Source of Truth für Recall-Berechnung
# ---------------------------------------------------------------------------

#: Map ``Belegtyp → Pflichtfeld-Liste``. Konsumiert von
#: :mod:`evals.test_extraction` (Recall) und Phase-3-Validation. Reihenfolge
#: entspricht der Schema-Klasse-Reihenfolge.
#:
#: HINWEIS — kontrollierte Duplizierung: ``steuer_extraktor.REQUIRED_FIELDS``
#: (Phase-1-CLI) hält die Lohnausweis-Liste eigenständig, damit ``schema.py``
#: keine Aufwärts-Abhängigkeit zur CLI aufbaut. Plan 02-05 löst das auf,
#: indem ``REQUIRED_FIELDS`` durch einen Lookup auf diese Map ersetzt wird.
MANDATORY_FIELDS_FOR_BELEGTYP: dict[str, list[str]] = {
    "lohnausweis": [
        "arbeitgeber",
        "periode_von",
        "periode_bis",
        "bruttolohn_pos8",
        "ahv_alv_nbu_abzug_pos9",
        "bvg_abzug_pos10a",
        "nettolohn_pos11",
    ],
    "bank_zinsausweis": [
        "institut",
        "kontoinhaber_name",
        "bruttoertrag",
        "vermoegensstand_3112",
        "verrechnungssteuer",
        "jahr",
    ],
    "kk_praemienbescheinigung": [
        "kasse",
        "versicherte_person_name",
        "jahr",
        "praemie_kvg_total",
    ],
    "saeule_3a": [
        "stiftung",
        "kontoinhaber_name",
        "jahr",
        "einzahlung_betrag",
    ],
    "wertschriftenverzeichnis": [
        "institut",
        "kontoinhaber_name",
        "bestand_3112",
        "bruttoertrag_total",
        "verrechnungssteuer_total",
        "jahr",
    ],
    "spenden": [
        "empfaenger",
        "spender_name",
        "betrag",
        "jahr",
    ],
    "berufsauslagen": [
        "anbieter",
        "person_name",
        "betrag",
        "jahr",
    ],
    "kinderbetreuung": [
        "anbieter",
        "kind_name",
        "betrag",
        "jahr",
    ],
    "hypothek_zinsbestaetigung": [
        "institut",
        "kontoinhaber_name",
        "liegenschaft",
        "schuldzinsen",
        "schuldsaldo_3112",
        "jahr",
    ],
    "liegenschaftsunterhalt": [
        "handwerker_anbieter",
        "eigentuemer_name",
        "liegenschaft",
        "betrag",
        "jahr",
    ],
    "krankheitskosten": [
        "leistungserbringer",
        "person_name",
        "betrag",
        "jahr",
    ],
}
