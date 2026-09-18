"""End-to-End-Eval-Suite: parametrisiert über alle ``expected/*.json``.

Plan 02-05: belegtyp-aware. Die Suite konsumiert
:data:`extractors.schema.MANDATORY_FIELDS_FOR_BELEGTYP` und
:data:`steuer_extraktor.SCHEMA_DISPATCH` — Phase-1-Fixtures bekommen
``"lohnausweis"`` als Default für ``_meta.belegtyp`` (rückwärtskompatibel).

Misst pro Fixture:

* **Recall** ≥ 95 % auf den belegtyp-spezifischen Pflichtfeldern.
* **Precision** ≥ 95 % — alle extrahierten Felder gehören zum
  jeweiligen Schema (keine Phantom-Keys).
* **Anchor-Validity** = 100 % auf den extrahierten Pflichtfeldern.
* **Brier-Score** ≤ 0.20 — Kalibrierung gegen ``anchor_valid``.

Zusätzlich:

* :func:`test_person_inference_e2e` — Family-Attribution-Recall ≥ 90 %
  auf den Phase-2-Fixtures mit ``_meta.expected_person`` (ROADMAP-
  Erfolgskriterium 4).
* :func:`test_pos17_gate` und :func:`test_privacy_gate` — Smoke-Tests
  für die zwei Phase-2-Lehre-Gates.

Ruft echtes Ollama auf — falls nicht erreichbar oder Digest-Drift, schlägt
der ``model_check``-Fixture aus ``conftest.py`` an: skip oder loud-fail.
"""
from __future__ import annotations

import json
import subprocess
from collections import Counter
from pathlib import Path

import pytest

from extractors.family import load_family
from extractors.schema import MANDATORY_FIELDS_FOR_BELEGTYP
from steuer_extraktor import SCHEMA_DISPATCH, process_one

# Pitfall 4 (03-RESEARCH.md): diese Suite ruft fuer jede Fixture echtes Ollama
# auf (~52 Tests × ~30s ≈ 10-15 min). Modul-weiter Marker, damit der
# Default-Lauf (`pytest -m "not llm_full"`) sie deselektiert und nur die
# deterministische Eval-Suite laeuft. Vollen Lauf via `pytest -m ""` oder
# `pytest -m llm_full` starten.
pytestmark = pytest.mark.llm_full

FIXTURES = Path(__file__).parent / "fixtures"
EXPECTED = Path(__file__).parent / "expected"
FAMILY_TEST_YAML = Path(__file__).parent / "family.yaml.test"
REPO_ROOT = Path(__file__).parent.parent


@pytest.fixture(scope="module", autouse=True)
def _generate_fixtures() -> None:
    """Stellt sicher, dass die Fixtures generiert sind (idempotent)."""
    if not (FIXTURES / "lohnausweis_standard.pdf").exists():
        from evals.generate import main as gen_main

        gen_main()


def _belegtyp_for_expected(expected: dict, fallback_stem: str) -> str:
    """Liest ``_meta.belegtyp`` oder fällt auf 'lohnausweis' zurück.

    Phase-1-Fixtures (``lohnausweis_standard``, ``lohnausweis_quellensteuer``)
    haben den ``_meta``-Block teilweise nicht — fallback auf
    'lohnausweis' hält sie rückwärtskompatibel.
    """
    meta = expected.get("_meta", {})
    return meta.get("belegtyp", "lohnausweis")


# Phase 3 (Plan 03-06): leer. ``process_one`` dispatcht nun pro Belegtyp auf
# das jeweilige Schema in ``SCHEMA_DISPATCH`` (11 Einträge), und alle Wave-3+4-
# Fixtures laufen End-to-End durch die Pipeline.
_PHASE3_WAVE3_PIPELINE_PENDING: set[str] = set()


# Plan 03-07 (Prompt-Tuning Phase-3-Belegtypen): Die ehemaligen Anker-Quality-
# xfails wurden mit angepassten Few-Shot-Beispielen für spenden/berufsauslagen/
# hypothek_zinsbestaetigung behoben. Die alten Few-Shots zeigten ein Layout
# mit "CHF"-Token zwischen Label und Wert, das in den echten Fixtures NICHT
# vorkommt — das LLM generalisierte falsch und gab Label-Tag-IDs als tag_refs
# zurück. Neue Few-Shots spiegeln das echte Layout (Label direkt gefolgt vom
# Wert-Token) plus eine zusätzliche Merkregel "nächste numerische Position
# rechts vom Label". Set bleibt als Marker für künftige Regression-Spotting.
_PHASE3_ANKER_QUALITY_KNOWN_LIMITS: set[str] = set()


@pytest.mark.parametrize(
    "expected_file",
    sorted(EXPECTED.glob("*.json")),
    ids=lambda p: p.stem,
)
def test_extraction_full_evl03(expected_file: Path, model_check: dict) -> None:
    """Pro Fixture: Recall ≥ 95 %, Precision ≥ 95 %, Anchor 100 %, Brier ≤ 0.20."""
    if expected_file.stem == "lohnausweis_no_header":
        # Diese Fixture testet den LLM-Fallback-Klassifikator (Plan 03-06 Task 1)
        # über ``test_classifier_fallback.py`` (mit ``llm_full``-Marker). Sie ist
        # bewusst minimal gestaltet (keine Pos-Header) — LLM-Extraktions-Qualität
        # auf Anker-Ebene ist nicht ihr Test-Ziel.
        pytest.skip(
            "lohnausweis_no_header: LLM-Fallback-Klassifikator-Fixture, getestet "
            "in test_classifier_fallback.py (@llm_full)."
        )
    if expected_file.stem in _PHASE3_ANKER_QUALITY_KNOWN_LIMITS:
        pytest.xfail(
            f"{expected_file.stem}: LLM liefert Label-Snippet statt Wert-Tag-Refs "
            "auf 'betrag' (Prompt-Tuning Phase-3-Backlog). Strict-xfail: bei "
            "verbessertem Prompt schlägt der Test als XPASS an."
        )
    expected = json.loads(expected_file.read_text())
    belegtyp = _belegtyp_for_expected(expected, expected_file.stem)
    pflichtfelder = MANDATORY_FIELDS_FOR_BELEGTYP[belegtyp]
    schema_class = SCHEMA_DISPATCH[belegtyp]
    allowed_fields = set(schema_class.model_fields.keys())

    # Soll-Felder (ohne _meta-Keys, None-Werte ignorieren).
    soll = {
        k: v for k, v in expected.items()
        if not k.startswith("_") and v is not None
    }
    pflichtfelder_in_expected = [k for k in pflichtfelder if k in soll]

    pdf_path = FIXTURES / f"{expected_file.stem}.pdf"
    family = load_family(FAMILY_TEST_YAML)
    result = process_one(pdf_path, family=family, year=2024)

    assert result.bucket in {"extracted", "unverified"}, (
        f"{expected_file.stem}: bucket={result.bucket}, "
        f"reason={result.reason_code}, error={result.error}"
    )

    # KK-Sonderfall: mitversicherte_kinder_namen flacht in mitversicherte_kinder_namen[0..]
    # auf — für Recall/Precision normalisieren wir dieses Prefix zurück.
    extracted_fields: dict[str, object] = {}
    for fr in result.fields:
        # FieldRow für Listen-Items hat feld="<name>[idx]"; reduziere auf den
        # Schema-Feldnamen (Recall/Precision-Gates arbeiten auf Schema-Ebene).
        feld_key = fr.feld.split("[", 1)[0]
        extracted_fields.setdefault(feld_key, fr)

    # --- Recall: Pflichtfelder vorhanden? ---
    present_required = [f for f in pflichtfelder_in_expected if f in extracted_fields]
    recall = len(present_required) / max(len(pflichtfelder_in_expected), 1)
    assert recall >= 0.95, (
        f"{expected_file.stem} ({belegtyp}): Recall {recall:.0%} < 95%. "
        f"Fehlend: {set(pflichtfelder_in_expected) - set(extracted_fields)}"
    )

    # --- Precision: alle extrahierten Felder gehören zum Schema? ---
    valid_extracted = [f for f in extracted_fields if f in allowed_fields]
    precision = len(valid_extracted) / max(len(extracted_fields), 1)
    assert precision >= 0.95, (
        f"{expected_file.stem} ({belegtyp}): Precision {precision:.0%} < 95%. "
        f"Phantom-Felder: {set(extracted_fields) - allowed_fields}"
    )

    # --- Anchor-Validity = 100 % auf den Pflichtfeldern ---
    for f in present_required:
        fr = extracted_fields[f]
        assert fr.anchor_valid, (
            f"{expected_file.stem} ({belegtyp}): Anker für {f}={fr.wert_raw!r} "
            f"(snippet={fr.snippet!r}) invalid"
        )

    # --- Konfidenz-Kalibrierung: Brier-Score ---
    if extracted_fields:
        brier = sum(
            (fr.konfidenz - (1.0 if fr.anchor_valid else 0.0)) ** 2
            for fr in extracted_fields.values()
        ) / len(extracted_fields)
        assert brier <= 0.20, (
            f"{expected_file.stem} ({belegtyp}): Brier-Score {brier:.3f} > 0.20 "
            "— Konfidenz schlecht kalibriert"
        )


# ---------------------------------------------------------------------------
# Werterhaltend-Heuristik (Liegenschaftsunterhalt, Plan 03-06 Task 2).
# ---------------------------------------------------------------------------

_WERTERHALTEND_FIXTURES = sorted(
    p
    for p in EXPECTED.glob("liegenschaftsunterhalt_*.json")
    if "expected_werterhaltend" in json.loads(p.read_text()).get("_meta", {})
)


@pytest.mark.parametrize(
    "expected_file",
    _WERTERHALTEND_FIXTURES,
    ids=lambda p: p.stem,
)
def test_werterhaltend_hint_matches_expected(
    expected_file: Path, model_check: dict
) -> None:
    """``classify_werterhaltend`` POST-LLM liefert den erwarteten True/False-Hint.

    Reparatur-Fixtures → True (werterhaltend, steuerlich abzugsfähig).
    Anbau/Neubau-Fixtures → False (wertvermehrend, NICHT abzugsfähig).
    """
    expected = json.loads(expected_file.read_text())
    expected_werterhaltend = expected["_meta"]["expected_werterhaltend"]
    pdf_path = FIXTURES / f"{expected_file.stem}.pdf"
    family = load_family(FAMILY_TEST_YAML)
    result = process_one(pdf_path, family=family, year=2024)
    assert result.werterhaltend_hint == expected_werterhaltend, (
        f"{expected_file.stem}: erwartet werterhaltend_hint="
        f"{expected_werterhaltend!r}, erhalten {result.werterhaltend_hint!r}"
    )


# ---------------------------------------------------------------------------
# Person-Inferenz End-to-End (PER-01..03, ROADMAP-Erfolgskriterium 4).
# ---------------------------------------------------------------------------

# Phase-2-Fixtures mit erwarteten Person-Roles aus _meta.expected_person.
_PERSON_FIXTURES = sorted(
    p
    for p in EXPECTED.glob("*.json")
    if "expected_person" in json.loads(p.read_text()).get("_meta", {})
)

# Bekannte strukturelle Limitierungen des D-B1-Exact-Match-Algorithmus
# (Plan-02-05-Lehre, dokumentiert für Phase-3-Backlog):
#
# * ``bank_zinsausweis_zkb``: Joint-Account druckt
#   ``"Hans und Maria Muster"``. Der Substring ``"hans muster"`` ist NICHT
#   im normalisierten Text enthalten (durch das Bindewort ``und``),
#   ``"maria muster"`` schon — der Algorithmus liefert 1 Match → ``frau``
#   statt ``gemeinsam``. Phase-3-Lösung: field-level Match auf
#   ``kontoinhaber_name`` (Multi-Person-Pattern) statt joined-text Substring.
# * ``kk_praemienbescheinigung_helsana``: Helsana-Cert listet sowohl
#   ``Versicherter (Beitragszahler): Hans Muster`` als auch
#   ``Versicherte Person: Lina Muster`` — 2 Matches → ``gemeinsam`` statt
#   ``kind1``. Phase-3-Lösung: field-level Match auf
#   ``versicherte_person_name`` ausschliesslich.
# TODO Plan 03-06: nach ``process_one``-Dispatch auf
# :func:`extractors.person_inference.infer_person_field` entfernen.
# Plan 03-02 hat die field-level-Match-Funktion bereits implementiert und
# in evals/test_person_inference_field.py mit 23 in-memory-Tests verifiziert
# (inkl. Joint-Account ``Hans und Maria Muster`` → ``gemeinsam`` und
# Helsana-Versicherte ``Lina Muster`` → ``kind1``). Die End-to-End-Auflösung
# der xfail-Markierungen hier passiert in Plan 03-06, sobald ``process_one``
# pro Belegtyp die richtige Inferenz-Variante aufruft.
#: Phase 3 (Plan 03-06): leer. ``process_one`` dispatcht nun pro Belegtyp
#: auf :func:`infer_person_field` (field-level, robust) bzw.
#: :func:`infer_person` (joined-text, nur Lohnausweis). Die zwei früheren
#: xfail-Einträge ``bank_zinsausweis_zkb`` (Joint-Account) und
#: ``kk_praemienbescheinigung_helsana`` (Beitragszahler-vs-Versicherte)
#: laufen jetzt korrekt durch.
_PERSON_INFERENCE_KNOWN_LIMITS: set[str] = set()


@pytest.mark.parametrize(
    "expected_file",
    _PERSON_FIXTURES,
    ids=lambda p: p.stem,
)
def test_person_inference_per_fixture(
    expected_file: Path, model_check: dict
) -> None:
    """Pro Fixture mit ``_meta.expected_person``: Person-Inferenz korrekt.

    Fixtures in :data:`_PERSON_INFERENCE_KNOWN_LIMITS` werden ge-xfail-t —
    diese strukturellen Limits des D-B1-Exact-Match sind dokumentiert und
    landen auf dem Phase-3-Backlog (field-level Person-Match).
    """
    if expected_file.stem in _PERSON_INFERENCE_KNOWN_LIMITS:
        pytest.xfail(
            f"{expected_file.stem}: bekannte Limitierung des D-B1-Exact-Match "
            "(Joint-Account 'X und Y' bzw. Beitragszahler-vs-Versicherte-Person). "
            "Phase-3-Backlog: field-level Person-Match."
        )

    expected = json.loads(expected_file.read_text())
    expected_person = expected["_meta"]["expected_person"]
    pdf_path = FIXTURES / f"{expected_file.stem}.pdf"
    family = load_family(FAMILY_TEST_YAML)
    assert family is not None, "evals/family.yaml.test muss existieren"

    result = process_one(pdf_path, family=family, year=2024)
    # Wir tolerieren ``unverified`` (LLM-Quality-Phänomen aus Phase 1) —
    # Person-Inferenz arbeitet auf den tagged_words, unabhängig von der
    # Anker-Qualität der Geldfelder.
    assert result.bucket != "unprocessed", (
        f"{expected_file.stem}: Pipeline failed mit "
        f"reason={result.reason_code}, error={result.error}"
    )
    assert result.person == expected_person, (
        f"{expected_file.stem}: erwartet person={expected_person!r}, "
        f"erhalten {result.person!r}"
    )


def test_family_attribution_recall_aggregate(model_check: dict) -> None:
    """Aggregierter Recall-Check: ≥ 90 % der Fixtures korrekt klassifiziert.

    ROADMAP-Erfolgskriterium 4 (Phase 2). Bekannte strukturelle Limits
    (D-B1-Exact-Match — siehe :data:`_PERSON_INFERENCE_KNOWN_LIMITS`) werden
    aus der Aggregation ausgenommen und sind im Phase-3-Backlog dokumentiert.
    """
    family = load_family(FAMILY_TEST_YAML)
    assert family is not None
    correct = 0
    total = 0
    misses: list[str] = []
    skipped: list[str] = []
    for expected_file in _PERSON_FIXTURES:
        if expected_file.stem in _PERSON_INFERENCE_KNOWN_LIMITS:
            skipped.append(expected_file.stem)
            continue
        expected = json.loads(expected_file.read_text())
        expected_person = expected["_meta"]["expected_person"]
        pdf_path = FIXTURES / f"{expected_file.stem}.pdf"
        result = process_one(pdf_path, family=family, year=2024)
        total += 1
        if result.bucket == "unprocessed":
            misses.append(f"{expected_file.stem}: bucket=unprocessed")
            continue
        if result.person == expected_person:
            correct += 1
        else:
            misses.append(
                f"{expected_file.stem}: erwartet={expected_person} "
                f"erhalten={result.person}"
            )
    assert total > 0, "Keine Phase-2-Person-Fixtures gefunden"
    recall = correct / total
    assert recall >= 0.90, (
        f"Family-Attribution-Recall {recall:.0%} < 90% "
        f"({correct}/{total} korrekt; übersprungen: {skipped}). Misses: {misses}"
    )


# ---------------------------------------------------------------------------
# Pos-17-Gate + Privacy-Gate (Phase-1-Lehre + PRV-01).
# ---------------------------------------------------------------------------


def test_pos17_gate() -> None:
    """grep -r 'pos17' im Quellcode darf 0 Treffer ergeben (Phase-1-Lehre).

    ``evals/test_fixtures.py`` und diese Datei selbst sind ausgenommen, da
    sie bewusst die literale Zeichenkette als Guard-Pattern referenzieren.
    """
    targets = [
        REPO_ROOT / "extractors",
        REPO_ROOT / "steuer_extraktor.py",
        REPO_ROOT / "evals",
    ]
    target_strs = [str(t) for t in targets if t.exists()]
    proc = subprocess.run(
        [
            "grep",
            "-rE",
            "pos17",
            "--include=*.py",
            "--exclude=test_fixtures.py",
            "--exclude=test_extraction.py",
            *target_strs,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 1, (
        f"Pos-17-Gate verletzt — Treffer:\n{proc.stdout}"
    )


def test_privacy_gate() -> None:
    """Cloud-Imports (anthropic/openai/requests.) im extractor-Pfad verboten (PRV-01)."""
    proc = subprocess.run(
        [
            "grep",
            "-rE",
            r"(anthropic|openai|requests\.)",
            "--include=*.py",
            str(REPO_ROOT / "extractors"),
            str(REPO_ROOT / "steuer_extraktor.py"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 1, (
        f"Privacy-Gate verletzt — Cloud-Imports gefunden:\n{proc.stdout}"
    )


def test_belegtyp_distribution_in_fixtures() -> None:
    """Sanity: alle 4 Phase-2-Belegtypen kommen in der Eval-Suite vor."""
    counter: Counter[str] = Counter()
    for expected_file in EXPECTED.glob("*.json"):
        expected = json.loads(expected_file.read_text())
        belegtyp = _belegtyp_for_expected(expected, expected_file.stem)
        counter[belegtyp] += 1
    # Erwartung Plan 02-05: mind. 1 Lohnausweis + 1 Bank + 1 KK + 1 3a.
    for belegtyp in (
        "lohnausweis",
        "bank_zinsausweis",
        "kk_praemienbescheinigung",
        "saeule_3a",
    ):
        assert counter[belegtyp] >= 1, (
            f"Mind. 1 Fixture für {belegtyp} erwartet — counter={dict(counter)}"
        )
