"""steuer-extraktor — CLI-Einstieg.

Phase 2 (Plan 02-05): End-to-End-Pipeline für 4 Belegtypen
(Lohnausweis, Bank-Zinsausweis, KK-Prämienbescheinigung, Säule 3a) plus
Person-Inferenz aus optionaler ``family.yaml``. Sequenzielle Verarbeitung,
lokales Ollama, drei-Buckets-Output mit Pre-/Post-Count-Assert (D-D2).
Cloud-Engine bleibt hart blockiert (PRV-01) — ``--engine cloud`` löst
``UsageError`` aus, ohne irgendeinen Cloud-Pfad zu importieren.

Pipeline pro PDF (siehe :func:`process_one`):
read_pdf → classify (Tuple) → tokenize → SCHEMA_DISPATCH-Lookup →
extract → resolve_field + run_plausibility(belegtyp, year) →
infer_person(words, family) → ProcessResult → drei-Buckets-CSV.
"""
from __future__ import annotations

import hashlib
import json
import sys
import traceback
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import click
from pydantic import BaseModel, ValidationError
from rich.console import Console
from rich.progress import Progress

from extractors import aussteller as aussteller_mod
from extractors.anchor_resolver import resolve_field
from extractors.label_priors import get_priors
from extractors.classifier import classify
from extractors.confidence import classify_werterhaltend, compose_confidence, run_plausibility
from extractors.family import Family, load_family
from extractors.llm_extract import DEFAULT_MODEL, extract, get_model_digest
from extractors.numbers import parse_swiss_amount, parse_swiss_date
from extractors.output import (
    FieldRow,
    ProcessResult,
    print_markdown_table,
    write_csvs,
    write_report,
)
from extractors.pdf_reader import read_pdf
from extractors.person_inference import (
    PERSON_RELEVANT_FIELD,
    infer_person,
    infer_person_field,
)
from extractors.schema import (
    BankZinsausweisRaw,
    Belegtyp,
    BerufsauslagenRaw,
    HypothekZinsbestaetigungRaw,
    KinderbetreuungRaw,
    KkPraemienbescheinigungRaw,
    KrankheitskostenRaw,
    LiegenschaftsunterhaltRaw,
    LohnausweisRaw,
    MANDATORY_FIELDS_FOR_BELEGTYP,
    ReasonCode,
    Saeule3aRaw,
    SpendenquittungRaw,
    WertschriftenverzeichnisRaw,
)
from extractors.tokenize import tokenize

console = Console()

# ---------------------------------------------------------------------------
# Schema-Dispatch (Plan 02-05) — Single-Source-of-Truth für Belegtyp → Klasse.
# ---------------------------------------------------------------------------

#: Map ``Belegtyp-String → pydantic-Raw-Klasse``. Konsumiert von
#: :func:`process_one` für den schema-aware Aufruf von
#: :func:`extractors.llm_extract.extract`.
SCHEMA_DISPATCH: dict[str, type[BaseModel]] = {
    "lohnausweis": LohnausweisRaw,
    "bank_zinsausweis": BankZinsausweisRaw,
    "kk_praemienbescheinigung": KkPraemienbescheinigungRaw,
    "saeule_3a": Saeule3aRaw,
    # Phase-3 (Plan 03-06): 7 neue Belegtypen.
    "wertschriftenverzeichnis": WertschriftenverzeichnisRaw,
    "spenden": SpendenquittungRaw,
    "berufsauslagen": BerufsauslagenRaw,
    "kinderbetreuung": KinderbetreuungRaw,
    "hypothek_zinsbestaetigung": HypothekZinsbestaetigungRaw,
    "liegenschaftsunterhalt": LiegenschaftsunterhaltRaw,
    "krankheitskosten": KrankheitskostenRaw,
}

#: Pro Belegtyp: die String-Felder, die als Schweizer Geldbetrag geparst
#: werden müssen. ``parse_swiss_amount`` läuft POST-LLM und füllt das
#: ``decimals``-Dict, das wiederum :func:`run_plausibility` füttert.
AMOUNT_FIELDS_FOR_BELEGTYP: dict[str, set[str]] = {
    "lohnausweis": {
        "bruttolohn_pos8",
        "nettolohn_pos11",
        "ahv_alv_nbu_abzug_pos9",
        "bvg_abzug_pos10a",
        "quellensteuer_pos12",
    },
    "bank_zinsausweis": {
        "bruttoertrag",
        "vermoegensstand_3112",
        "verrechnungssteuer",
    },
    "kk_praemienbescheinigung": {
        "praemie_kvg_total",
        "praemie_vvg_total",
    },
    "saeule_3a": {
        "einzahlung_betrag",
    },
    # Phase-3 (Plan 03-06): 7 neue Belegtypen.
    "wertschriftenverzeichnis": {
        "bestand_3112",
        "bruttoertrag_total",
        "verrechnungssteuer_total",
    },
    "spenden": {"betrag"},
    "berufsauslagen": {"betrag"},
    "kinderbetreuung": {"betrag"},
    "hypothek_zinsbestaetigung": {
        "schuldzinsen",
        "schuldsaldo_3112",
    },
    "liegenschaftsunterhalt": {"betrag"},
    "krankheitskosten": {"betrag"},
}

#: Pro Belegtyp: das Schema-Feld, das in die CSV-Spalte ``aussteller`` fliesst.
#: Lohnausweis → arbeitgeber, Bank → institut, KK → kasse, 3a → stiftung.
_AUSSTELLER_FIELD_FOR_BELEGTYP: dict[str, str] = {
    "lohnausweis": "arbeitgeber",
    "bank_zinsausweis": "institut",
    "kk_praemienbescheinigung": "kasse",
    "saeule_3a": "stiftung",
    # Phase-3 (Plan 03-06, D-B7): 7 neue Belegtypen.
    "wertschriftenverzeichnis": "institut",
    "spenden": "empfaenger",
    "berufsauslagen": "anbieter",
    "kinderbetreuung": "anbieter",
    "hypothek_zinsbestaetigung": "institut",
    "liegenschaftsunterhalt": "handwerker_anbieter",
    "krankheitskosten": "leistungserbringer",
}


def _log(log_path: Path, event: dict) -> None:
    """Schreibt JSONL-Event. PRV-04: keine PII — nur Counts/Hashes/Codes."""
    event["timestamp"] = datetime.now(timezone.utc).isoformat()
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, ensure_ascii=False) + "\n")


def _pdf_hash(pdf_path: Path) -> str:
    """SHA256-Kurzform des PDF-Inhalts — kein Klartext, kein Dateiname."""
    return hashlib.sha256(pdf_path.read_bytes()).hexdigest()[:12]


def _derive_datum_iso(belegtyp: str, raw: BaseModel) -> str | None:
    """Leitet das ISO-Datum für die CSV-Spalte ``datum`` aus dem Schema ab.

    * Lohnausweis → ``periode_bis`` (kalendarische Periodengrenze).
    * Bank/KK/3a → ``jahr`` (4-stellig) → ``"<jahr>-12-31"`` als Stichtag.

    Bei Parser-Fehler liefert die Funktion ``None``.
    """
    if belegtyp == "lohnausweis":
        try:
            tf = getattr(raw, "periode_bis", None)
            if tf is not None:
                return parse_swiss_date(tf.value)
        except (ValueError, AttributeError):
            return None
        return None

    # Bank / KK / 3a — alle haben ein ``jahr``-Feld.
    try:
        tf = getattr(raw, "jahr", None)
        if tf is None:
            return None
        jahr_str = tf.value.strip()
        if len(jahr_str) == 4 and jahr_str.isdigit():
            return f"{jahr_str}-12-31"
    except AttributeError:
        return None
    return None


def _extract_year_from_result(res: ProcessResult) -> int | None:
    """Liefert das Steuerjahr eines :class:`ProcessResult` für das Aussteller-
    Lifecycle-Update (Plan 03-06, main-Loop).

    Quelle: ``res.datum_iso[:4]`` — bei Bank/KK/3a ist das ``"<jahr>-12-31"``,
    bei Lohnausweis ``periode_bis`` (kalendarisches Periodenende). Liefert
    ``None`` wenn ``datum_iso`` fehlt oder das Präfix nicht 4-stellig-numerisch
    ist.
    """
    if res.datum_iso is None:
        return None
    head = res.datum_iso[:4]
    if len(head) == 4 and head.isdigit():
        try:
            return int(head)
        except ValueError:
            return None
    return None


def _current_year_from_buckets(buckets: dict[str, list[ProcessResult]]) -> int:
    """Leitet ``current_year`` für ``find_missing`` aus den extracted-Buckets ab.

    Strategie: Median der nicht-None ``datum_iso[:4]``-Werte aus
    ``extracted`` UND ``unverified`` (auch unverified-Belege haben ein
    korrekt extrahiertes ``datum_iso``). Fix WR-02: würde der ``unverified``-
    Bucket ignoriert, fiele die Funktion bei einem Lauf, in dem alle PDFs
    aus Anker-Gründen unverified bleiben, auf ``now().year`` zurück — und
    ``find_missing`` würde dann genau die Vorjahres-Aussteller stillschweigend
    aus dem Report ausschliessen. Wenn der Median nicht ableitbar ist
    (z. B. leerer Lauf), fällt die Funktion auf ``datetime.now().year`` zurück.
    """
    years: list[int] = []
    for bucket_name in ("extracted", "unverified"):
        for res in buckets.get(bucket_name, []):
            y = _extract_year_from_result(res)
            if y is not None:
                years.append(y)
    if not years:
        return datetime.now(timezone.utc).year
    years.sort()
    return years[len(years) // 2]


def _extract_year(raw: BaseModel) -> int | None:
    """Extrahiert ``raw.jahr.value`` als ``int`` (für 3a-Limit-Plausibility)."""
    tf = getattr(raw, "jahr", None)
    if tf is None:
        return None
    try:
        return int(tf.value)
    except (ValueError, AttributeError):
        return None


def process_one(
    pdf_path: Path,
    family: Family | None = None,
    year: int | None = None,
) -> ProcessResult:
    """Verarbeitet ein einzelnes PDF und liefert ein ``ProcessResult``.

    Phase-2-Pipeline (Plan 02-05):

    1. ``read_pdf`` (read-Failure → ``unprocessed``).
    2. ``classify`` (Tuple-Return) — bei ``"unknown"``: ``unprocessed`` mit
       ``reason_code`` aus dem :class:`ReasonCode`-Enum (D-D3/D-D4).
    3. ``tokenize`` → ``tagged_text`` + ``tag_map`` + ``tagged_words``.
    4. Schema-Dispatch via :data:`SCHEMA_DISPATCH` →
       ``extract(tagged_text, schema_class=…)``.
    5. Pflicht-/Optional-Felder aus :data:`MANDATORY_FIELDS_FOR_BELEGTYP`
       und ``schema.model_fields``.
    6. Belegtyp-aware ``run_plausibility(belegtyp, decimals, year=…)``.
    7. ``infer_person(tagged_words, family)`` → :attr:`ProcessResult.person`.

    Routing-Regel: jede Exception in Stufen 1–4 → ``unprocessed`` mit
    passendem ``reason_code`` aus :class:`ReasonCode`. Erst nach
    erfolgreichem ``extract()`` wird in ``extracted`` vs. ``unverified``
    aufgeteilt — basierend auf ``anchor_valid`` aller Pflichtfelder.

    Args:
        pdf_path: Pfad zum PDF.
        family: optionale Familien-Konfig für Person-Inferenz (None →
            disabled, person bleibt None).
        year: Steuerjahr für Plausibility-Limits. Wenn ``None``, versucht
            die Funktion aus dem extrahierten ``jahr``-Feld abzuleiten
            (Bank/KK/3a) bzw. wird ignoriert (Lohnausweis).
    """
    # 1. Ingestion
    try:
        words = read_pdf(pdf_path)
    except NotImplementedError as e:
        return ProcessResult(
            bucket="unprocessed",
            pdf_path=pdf_path,
            reason_code=ReasonCode.OCR_FAILED.value,
            error=str(e),
        )
    except Exception as e:
        return ProcessResult(
            bucket="unprocessed",
            pdf_path=pdf_path,
            reason_code=ReasonCode.TEXT_EXTRACT_FAILED.value,
            error=str(e),
        )

    # 2. Out-of-Scope ZUERST prüfen — Filename-Regeln sind zuverlässiger als der
    # Klassifikator (Corporate-Action wird sonst fälschlicherweise als
    # bank_zinsausweis eingestuft).
    from extractors.out_of_scope import classify_out_of_scope
    plain_text_for_oos = " ".join(w["text"] for w in words)
    oos = classify_out_of_scope(plain_text_for_oos, pdf_path.name)
    if oos is not None:
        belegtyp_hinweis, oos_reason = oos
        return ProcessResult(
            bucket="unprocessed",
            pdf_path=pdf_path,
            belegtyp=belegtyp_hinweis,
            reason_code="out_of_scope",
            error=oos_reason,
        )

    # 3. Classify (Tuple-Return aus Plan 02-04).
    belegtyp, classify_reason = classify(words)
    if belegtyp == "unknown":
        return ProcessResult(
            bucket="unprocessed",
            pdf_path=pdf_path,
            belegtyp="unknown",
            reason_code=(
                classify_reason.value
                if classify_reason
                else ReasonCode.UNSUPPORTED_TYPE.value
            ),
            error=f"belegtyp=unknown",
        )

    if belegtyp not in SCHEMA_DISPATCH:
        # Defensive: Belegtyp-Literal, das in SCHEMA_DISPATCH fehlt — sollte
        # via Belegtyp-Literal-Closed-Set unmöglich sein, aber wir routen
        # sauber statt zu crashen.
        return ProcessResult(
            bucket="unprocessed",
            pdf_path=pdf_path,
            belegtyp=belegtyp,
            reason_code=ReasonCode.UNSUPPORTED_TYPE.value,
            error=f"Kein Schema-Dispatch für belegtyp={belegtyp!r}",
        )

    # 3. Tokenize
    tagged_text, tag_map = tokenize(words)
    tagged_words = list(tag_map.values())

    # 4. LLM-Extract — schema-aware.
    schema_class = SCHEMA_DISPATCH[belegtyp]
    try:
        raw = extract(tagged_text, schema_class=schema_class)
    except ValidationError as e:
        return ProcessResult(
            bucket="unprocessed",
            pdf_path=pdf_path,
            belegtyp=belegtyp,
            reason_code=ReasonCode.SCHEMA_VALIDATION_FAILED.value,
            error=str(e),
        )
    except Exception as e:
        return ProcessResult(
            bucket="unprocessed",
            pdf_path=pdf_path,
            belegtyp=belegtyp,
            reason_code=ReasonCode.LLM_EXTRACT_FAILED.value,
            error=str(e),
        )

    # 4-pre. Name-Cleanup: trailing-Geburtsdatum / Komma-Listen entfernen
    from extractors.name_cleanup import clean_person_name as _clean
    from extractors.steuer_zielmodell import get_spec as _zm_get
    from extractors.schema import TaggedField as __TF
    _spec_zm = _zm_get(belegtyp)
    if _spec_zm and _spec_zm.person_field and hasattr(raw, _spec_zm.person_field):
        _tf = getattr(raw, _spec_zm.person_field)
        if _tf and _tf.value:
            _cleaned = _clean(_tf.value)
            if _cleaned != _tf.value:
                setattr(raw, _spec_zm.person_field,
                        __TF(value=_cleaned, tag_refs=_tf.tag_refs))

    # 4a. periode_von/bis Halluzinations-Check (CLI-Pendant zu process_samples_full).
    # Wenn LLM einen Wert liefert, der im Doc-Text gar nicht vorkommt, ist es
    # eine Halluzination — entfernen.
    plain_text_for_check = " ".join(w["text"] for w in words)
    for periode_field in ("periode_von", "periode_bis"):
        if hasattr(raw, periode_field):
            from extractors.schema import TaggedField as _TF
            tf = getattr(raw, periode_field, None)
            if tf and tf.value:
                if (tf.value not in plain_text_for_check
                    and tf.value.replace(".", "") not in plain_text_for_check.replace(".", "")):
                    setattr(raw, periode_field, _TF(value="", tag_refs=[]))

    # 4b. Jahr-Inferenz aus Dateiname/Header — Codex' Punkt 5 in CLI-Pfad.
    # Override nur bei fehlendem oder implausiblem LLM-Wert (außerhalb [2018, 2030]).
    if hasattr(raw, "jahr"):
        from extractors.date_inference import infer_jahr
        from extractors.schema import TaggedField
        current_jahr = getattr(raw, "jahr", None)
        current_value = (
            current_jahr.value if current_jahr and current_jahr.value else None
        )
        plausible = (
            current_value is not None
            and current_value.isdigit()
            and 2018 <= int(current_value) <= 2030
        )
        if not plausible:
            plain_text = " ".join(w["text"] for w in words)
            jahr_inferred, _infer_src = infer_jahr(pdf_path.name, plain_text)
            if jahr_inferred:
                setattr(raw, "jahr", TaggedField(value=jahr_inferred, tag_refs=[]))
            else:
                # Implausibler LLM-Wert ohne Inferenz-Quelle → entfernen statt
                # falsch anzeigen (Codex' #3).
                setattr(raw, "jahr", TaggedField(value="", tag_refs=[]))

    # 5. Pflicht- + Optional-Feldlisten — aus Zielmodell statt Schema
    # (Codex' #1: echte Single-Source-of-Truth). Fallback auf Schema-Liste
    # nur für Belegtypen, die das Zielmodell nicht abdeckt (Hypothek,
    # Liegenschaft, Spenden, Berufsauslagen — alle Out-of-Scope für die
    # User-Familie, aber Schema existiert für künftige Erweiterungen).
    from extractors.steuer_zielmodell import mandatory_fields as _zm_mandatory
    _zm_mand = _zm_mandatory(belegtyp)
    pflichtfelder = _zm_mand if _zm_mand else MANDATORY_FIELDS_FOR_BELEGTYP[belegtyp]
    # WR-05 (Code-Review 03-06): ``werterhaltend`` ist Schema-Stub (TaggedField[str]
    # mit "LASS DIESES FELD WEG"-Description), wird POST-LLM via Regex-Heuristik
    # befüllt und landet als ``ProcessResult.werterhaltend_hint``. Falls das LLM die
    # Description ignoriert und doch einen String liefert, würde er ungeprüft als
    # FieldRow in die CSV laufen — explizit aus alle_felder ausschliessen.
    alle_felder = [
        f
        for f in schema_class.model_fields
        if f != "schema_version"
        and not (belegtyp == "liegenschaftsunterhalt" and f == "werterhaltend")
    ]
    optional_felder = [f for f in alle_felder if f not in pflichtfelder]

    # 5a. Geldbeträge POST-LLM in Decimal konvertieren (für Plausibility).
    amount_fields = AMOUNT_FIELDS_FOR_BELEGTYP.get(belegtyp, set())
    decimals: dict[str, Decimal | None] = {}
    for fname in amount_fields:
        tf = getattr(raw, fname, None)
        if tf is None:
            decimals[fname] = None
            continue
        try:
            decimals[fname] = parse_swiss_amount(tf.value)
        except ValueError:
            decimals[fname] = None

    # 5b. Year ableiten falls nicht via CLI gesetzt — nur für Bank/KK/3a relevant.
    effective_year = year if year is not None else _extract_year(raw)

    # 5c. Belegtyp-aware Plausibility (Plan 02-04).
    penalties = run_plausibility(belegtyp, decimals, year=effective_year)

    # 5d. Pro Feld: anchor + amount + confidence.
    fields: list[FieldRow] = []
    all_required_valid = True
    for fname in alle_felder:
        tf = getattr(raw, fname, None)
        if tf is None:
            # Optional + nicht gefunden = OK; Pflicht + nicht gefunden = unverified.
            if fname in pflichtfelder:
                all_required_valid = False
            continue

        # KK-Sonderfall: ``mitversicherte_kinder_namen`` ist eine Liste
        # von TaggedField — pro Eintrag eine FieldRow emittieren (flacher
        # CSV-Output, Phase-3 macht das richtige Mehr-Person-Mapping).
        if isinstance(tf, list):
            for idx, child_tf in enumerate(tf):
                valid, snippet, bboxes = resolve_field(
                    child_tf.value, child_tf.tag_refs, tag_map,
                    label_priors=get_priors(belegtyp, fname),
                )
                page = (
                    tag_map[child_tf.tag_refs[0]].page
                    if child_tf.tag_refs and child_tf.tag_refs[0] in tag_map
                    else None
                )
                snippet_match = 1.0 if valid else 0.0
                confidence = compose_confidence(valid, snippet_match, 1.0)
                fields.append(
                    FieldRow(
                        feld=f"{fname}[{idx}]",
                        wert_raw=child_tf.value,
                        wert_decimal=None,
                        konfidenz=confidence,
                        anchor_valid=valid,
                        anchor_page=page,
                        anchor_bbox=list(bboxes[0]) if bboxes else None,
                        snippet=snippet,
                    )
                )
            continue

        valid, snippet, bboxes = resolve_field(
            tf.value, tf.tag_refs, tag_map,
            label_priors=get_priors(belegtyp, fname),
        )
        if not valid and fname in pflichtfelder:
            all_required_valid = False
        wert_decimal = decimals.get(fname)
        # Primärer Pfad: page aus tag_refs (LLM hat Tags geliefert).
        # Verbatim-Fallback-Pfad: tag_refs leer, aber bboxes vom Resolver
        # befüllt — page über bbox-Lookup in tag_map herleiten.
        page: int | None = None
        if tf.tag_refs and tf.tag_refs[0] in tag_map:
            page = tag_map[tf.tag_refs[0]].page
        elif bboxes:
            for tw in tag_map.values():
                if tw.bbox == bboxes[0]:
                    page = tw.page
                    break
        snippet_match = 1.0 if valid else 0.0
        penalty = penalties.get(fname, 1.0)
        confidence = compose_confidence(valid, snippet_match, penalty)
        fields.append(
            FieldRow(
                feld=fname,
                wert_raw=tf.value,
                wert_decimal=str(wert_decimal) if wert_decimal is not None else None,
                konfidenz=confidence,
                anchor_valid=valid,
                anchor_page=page,
                anchor_bbox=list(bboxes[0]) if bboxes else None,
                snippet=snippet,
            )
        )

    # Optional-Pflicht-Konsistenz: optional_felder werden nur erwähnt,
    # damit zukünftige Erweiterungen sie sehen — aktuell keine zusätzliche
    # Logik nötig (alle Felder werden ohnehin oben durchlaufen).
    _ = optional_felder

    # 6. Datum-ISO ableiten (belegtyp-spezifisch).
    datum_iso = _derive_datum_iso(belegtyp, raw)

    # 7. Aussteller-Mapping pro Belegtyp.
    aussteller: str | None = None
    aussteller_field = _AUSSTELLER_FIELD_FOR_BELEGTYP.get(belegtyp)
    if aussteller_field is not None:
        tf_aussteller = getattr(raw, aussteller_field, None)
        if tf_aussteller is not None:
            aussteller = tf_aussteller.value

    # 8. Person-Inferenz: Lohnausweis nutzt joined-text (D-D3); sonst
    #    field-level (D-D2, Plan 03-06). Field-level löst die zwei
    #    Phase-2-xfail-Limits (bank_zinsausweis_zkb / kk_praemien_helsana).
    if belegtyp == "lohnausweis":
        person_result = infer_person(tagged_words, family)
    else:
        person_result = infer_person_field(belegtyp, raw, family)
    person: str | None = person_result.role
    person_anchor_page: int | None = None
    if person_result.anchor_tag_id is not None:
        tw = tag_map.get(person_result.anchor_tag_id)
        if tw is not None:
            person_anchor_page = tw.page

    # 9. Liegenschaftsunterhalt — werterhaltend-Heuristik POST-LLM.
    #    Haystack: handwerker_anbieter + kompletter joined-text Seite 1
    #    (Beschreibungs-Paragraphen wie "Reparatur Fassade" stehen oft als
    #    eigene Blöcke im Layout). Plan 03-06 Task 2.
    werterhaltend_hint: bool | None = None
    if belegtyp == "liegenschaftsunterhalt":
        handwerker_tf = getattr(raw, "handwerker_anbieter", None)
        handwerker_text = handwerker_tf.value if handwerker_tf is not None else ""
        page1_text = " ".join(w.get("text", "") for w in words if w.get("page") == 1)
        haystack = f"{handwerker_text} {page1_text}"
        werterhaltend_hint = classify_werterhaltend(haystack)

    bucket = "extracted" if all_required_valid else "unverified"
    return ProcessResult(
        bucket=bucket,
        pdf_path=pdf_path,
        belegtyp=belegtyp,
        aussteller=aussteller,
        datum_iso=datum_iso,
        fields=fields,
        person=person,
        person_anchor_page=person_anchor_page,
        werterhaltend_hint=werterhaltend_hint,
    )


@click.command()
@click.option(
    "--input",
    "input_dir",
    type=click.Path(exists=False, file_okay=False, path_type=Path),
    default=Path("belege"),
    help="Ordner mit PDFs (Default: ./belege/)",
)
@click.option(
    "--engine",
    type=click.Choice(["local", "cloud"]),
    default="local",
    help="local = Ollama (Default). cloud = Phase 4 (Phase 1/2 löst UsageError aus).",
)
@click.option(
    "--aussteller-history",
    type=click.Path(path_type=Path),
    default=Path("aussteller.json"),
    help="Vorjahres-Aussteller (Phase 3: aussteller_history wird hier ausgewertet).",
)
@click.option(
    "--output",
    "output_dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=Path("output"),
    help="Ordner für Tabellen + Reports.",
)
@click.option(
    "--year",
    type=int,
    default=None,
    help="Steuerjahr für Limits/Plausibility (Default: aus Periode/jahr-Feld ableiten).",
)
@click.option(
    "--family",
    "family_path",
    type=click.Path(path_type=Path),
    default=Path("family.yaml"),
    help="Pfad zu family.yaml für Person-Inferenz (Default: ./family.yaml; "
    "fehlende Datei → Person-Inferenz disabled).",
)
def main(
    input_dir: Path,
    engine: str,
    aussteller_history: Path,
    output_dir: Path,
    year: int | None,
    family_path: Path,
) -> None:
    """PDFs aus ``input_dir`` extrahieren und steuerrelevante Tabelle ausgeben."""
    if engine == "cloud":
        raise click.UsageError(
            "Cloud-Engine ist Phase 4 — siehe PRV-02. "
            "In Phase 1/2 ist nur --engine local (Ollama) verfügbar."
        )

    if not input_dir.exists():
        console.print(f"[red]Fehler:[/] Ordner {input_dir} existiert nicht.")
        sys.exit(1)

    # Family-Konfig laden (Plan 02-05). D-A2: graceful bei fehlender Datei.
    # Wird VOR dem PDF-Scan geladen, damit die Privacy-/Hinweis-Meldungen auch
    # bei leerem Input-Ordner erscheinen (Plan-02-05 Smoke-Test).
    family: Family | None = None
    family_warnings: list[str] = []
    default_family_path = Path("family.yaml")
    try:
        family = load_family(family_path)
    except ValueError as e:
        console.print(
            f"[red]family.yaml ungültig:[/] {e}. Lauf wird abgebrochen."
        )
        sys.exit(1)
    if family is None:
        if family_path != default_family_path:
            # Nutzer hat explizit einen Pfad gesetzt — auf fehlende Datei hinweisen.
            console.print(
                f"[yellow]Hinweis:[/] {family_path} nicht gefunden — "
                "Person-Inferenz deaktiviert."
            )
        # Default-Pfad fehlt: silent (häufiger Fall, kein Lärm).
    else:
        family_warnings = list(family.warnings)
        if family_warnings:
            console.print(
                f"[yellow]Privacy-Warnungen aus family.yaml:[/] "
                f"{len(family_warnings)} Eintrag/Einträge — Details in report.md."
            )

    pdfs = sorted(input_dir.glob("*.pdf"))
    if not pdfs:
        console.print(f"[yellow]Keine PDFs in {input_dir}.[/]")
        sys.exit(0)

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
    run_dir = output_dir / timestamp
    run_dir.mkdir(parents=True)
    log_path = run_dir / "run.jsonl"

    # Drift-Log: Modell-Tag + Digest in jedem Run festhalten (D-B4 / EVL-05).
    try:
        model_info = get_model_digest(DEFAULT_MODEL)
    except Exception as e:
        console.print(f"[red]Ollama nicht erreichbar:[/] {e}")
        sys.exit(2)
    _log(
        log_path,
        {
            "event": "run_start",
            "model": model_info["model"],
            "digest": model_info["digest"],
            "n_pdfs": len(pdfs),
            "family_loaded": family is not None,
            "family_warning_count": len(family_warnings),
        },
    )
    # Family-Warnungen einzeln loggen (PII-frei, T-02-16) — Klartext-Strings,
    # die Family.warnings selbst formatiert hat (kein AHV-Wert enthalten).
    for warning in family_warnings:
        _log(log_path, {"event": "family_warning", "warning": warning})

    # Aussteller-Historie laden (Plan 03-06, D-B1..B7). Korruption-tolerant:
    # load() crasht nie, liefert (map, warning_or_None).
    aussteller_map, aussteller_warning = aussteller_mod.load(aussteller_history)
    if aussteller_warning:
        console.print(f"[yellow]Hinweis:[/] {aussteller_warning}")
    seen_aussteller_keys: set[str] = set()

    buckets: dict[str, list[ProcessResult]] = {
        "extracted": [],
        "unverified": [],
        "unprocessed": [],
    }
    with Progress() as progress:
        task = progress.add_task("Extrahiere", total=len(pdfs))
        for pdf in pdfs:
            t0 = datetime.now(timezone.utc)
            try:
                res = process_one(pdf, family=family, year=year)
            except Exception as e:
                res = ProcessResult(
                    bucket="unprocessed",
                    pdf_path=pdf,
                    reason_code=ReasonCode.LLM_EXTRACT_FAILED.value,
                    error=f"unhandled: {e!r}\n{traceback.format_exc()[:500]}",
                )
            buckets[res.bucket].append(res)

            # Aussteller-Update — nur für extracted-Bucket mit valider
            # Aussteller- und Belegtyp-Info (D-B5 idempotent: max-update).
            if (
                res.bucket == "extracted"
                and res.aussteller
                and res.belegtyp
                and res.belegtyp != "unknown"
            ):
                beleg_year = year if year is not None else _extract_year_from_result(res)
                if beleg_year is not None:
                    key = aussteller_mod.make_key(res.aussteller, res.belegtyp)
                    seen_aussteller_keys.add(key)
                    aussteller_mod.update(
                        aussteller_map, res.aussteller, res.belegtyp, beleg_year
                    )

            duration = (datetime.now(timezone.utc) - t0).total_seconds()
            _log(
                log_path,
                {
                    "event": "pdf_processed",
                    "pdf_hash": _pdf_hash(pdf),
                    "bucket": res.bucket,
                    "belegtyp": res.belegtyp,
                    "person": res.person,  # Role-String oder None — KEINE Klartextnamen.
                    "reason_code": res.reason_code,
                    "duration_s": round(duration, 3),
                    "model": model_info["model"],
                    "digest": model_info["digest"],
                },
            )
            progress.update(task, advance=1)

    # Pre-/Post-Count-Assert (CLI-05, D-D2) — Verletzung crasht den Lauf.
    total_buckets = sum(len(b) for b in buckets.values())
    assert len(pdfs) == total_buckets, (
        f"Pre-/Post-Count-Mismatch: input={len(pdfs)}, buckets={total_buckets}"
    )

    # Aussteller-Vollständigkeitsprüfung (D-B6) + atomarer Write (Pitfall 6).
    # WR-01 (Code-Review 03-06): Bei aussteller_warning (Corrupt-Load) wäre
    # ``aussteller_map`` leer — ein Write würde die historische Datei
    # überschreiben und Vorjahres-Aussteller stillschweigend löschen
    # (Constraint 3 "Vollständigkeit"). Deshalb: bei Warning skip-write
    # und kein find_missing (was wir nicht geladen haben, können wir nicht
    # gegen "fehlend" vergleichen).
    if aussteller_warning:
        console.print(
            "[red]Aussteller-Historie nicht geladen — kein Write in diesem Lauf "
            "(Original-Datei + ggf. .bak-Snapshot bleiben unverändert).[/]"
        )
        aussteller_missing: list = []
    else:
        current_year_for_missing = (
            year if year is not None else _current_year_from_buckets(buckets)
        )
        aussteller_missing = aussteller_mod.find_missing(
            aussteller_map, seen_aussteller_keys, current_year_for_missing
        )
        aussteller_mod.write_json(aussteller_history, aussteller_map)

    write_csvs(run_dir, buckets)
    write_report(
        run_dir,
        buckets,
        model_info,
        family_warnings=family_warnings,
        aussteller_missing=aussteller_missing,
        aussteller_load_warning=aussteller_warning,
    )
    _log(
        log_path,
        {
            "event": "run_end",
            "n_extracted": len(buckets["extracted"]),
            "n_unverified": len(buckets["unverified"]),
            "n_unprocessed": len(buckets["unprocessed"]),
        },
    )
    print_markdown_table(buckets["extracted"], console=console)
    console.print(f"\n[green]Output:[/] {run_dir}")


if __name__ == "__main__":
    main()
