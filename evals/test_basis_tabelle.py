"""Eval-Test für ``scripts/build_basis_tabelle.py``.

Deterministisch (kein ``llm_full``-Marker), läuft im Default-Lauf
``pytest -m "not llm_full"``. Baut ein synthetisches ``_results_full.json``-
Fixture (NUR Fantasy-Namen Hans/Maria/Lina/Tim — Privacy) und prüft die
Basis-Tabelle: pro Belegtyp eine Zeile, korrekte Lücken-Codes, ehrliche
Vollständigkeitsquote, ``--stats`` ohne Dateinamen.

Das Personen-Rollen-Mapping wird wie in ``test_steueraufstellung`` über die
PII-freie ``family.yaml.test`` aktiviert (Hans→elternteil_1, Maria→elternteil_2,
Lina→kind_1, Tim→kind_2).
"""
from __future__ import annotations

from pathlib import Path

import pytest

import scripts.build_tax_output as bto
import scripts.build_basis_tabelle as bbt
from scripts.build_basis_tabelle import (
    LUECKE_BELEG_FEHLGESCHLAGEN,
    LUECKE_FELD_FEHLT,
    LUECKE_MR_MARKER,
    LUECKE_NICHT_PARSEBAR,
    LUECKE_PERSON_UNBESTIMMT,
    STATUS_FEHLGESCHLAGEN,
    STATUS_LUECKE,
    STATUS_NA_BY_DESIGN,
    STATUS_OOS,
    STATUS_VOLLSTAENDIG,
    _resolve_value,
    build_row,
    build_rows,
    completeness,
    gap_counts,
    header_line,
    render_stats,
)

_FAMILY_TEST = Path(__file__).parent / "family.yaml.test"


@pytest.fixture(autouse=True)
def _family_maps(monkeypatch):
    """Aktiviert das Rollen-Mapping über die synthetische ``family.yaml.test``.

    Die Vornamen→Rolle-Maps in ``build_tax_output`` werden zur Importzeit aus
    ``ROOT/family.yaml`` gebaut — die existiert im Test-/CI-Lauf nicht. Wir
    bauen sie deterministisch aus dem PII-freien ``family.yaml.test`` neu.
    ``build_basis_tabelle`` importiert die Mapper aus ``build_tax_output``, der
    Monkeypatch dort genügt.
    """
    from extractors.family import load_family
    from extractors.person_inference import _name_tokens

    fmly = load_family(_FAMILY_TEST)
    assert fmly is not None, "family.yaml.test muss ladbar sein"

    pool = ["Hans", "Maria", "Lina", "Tim", "Anna", "Peter"]
    fantasy: dict[str, str] = {}
    real: dict[str, set[str]] = {}
    for idx, m in enumerate(fmly.members):
        role = bto._FAMILY_ROLE_MAP.get(m.role, bto.ROLE_UNKNOWN)
        if idx < len(pool):
            fantasy[pool[idx]] = role
        for tok in _name_tokens(m.first_name):
            real.setdefault(tok, set()).add(role)

    monkeypatch.setattr(bto, "_FANTASY_FIRSTNAME_TO_ROLE", fantasy)
    monkeypatch.setattr(bto, "_REAL_FIRSTNAME_TO_ROLE", real)


def _field(feld: str, value: str, anchor_valid: bool = True) -> dict:
    return {
        "feld": feld,
        "value": value,
        "bbox": [0, 0, 10, 10],
        "page": 1,
        "snippet": f"{feld} {value}",
        "anchor_valid": anchor_valid,
        "inference_source": None,
    }


@pytest.fixture
def entries() -> list[dict]:
    """Synthetisches _results_full.json-Fixture (nur Fantasy-Namen).

    Pro Belegtyp eine Zeile; dazu gezielte Lücken-Fälle (MR-Marker, nicht
    parsebar, Feld fehlt, Person unbestimmt) und ein OOS-/ein fehlgeschlagener
    Beleg.
    """
    return [
        # Lohnausweis Hans → vollständig (Person + nettolohn).
        {
            "pdf_name": "lohn_hans.pdf", "status": "ok", "belegtyp": "lohnausweis",
            "fields": [
                _field("arbeitnehmer_name", "Hans Muster"),
                _field("jahr", "2022"),
                _field("nettolohn_pos11", "85'000.00"),
            ],
        },
        # Bank → vollständig (Institut + vermoegensstand_3112).
        {
            "pdf_name": "bank.pdf", "status": "ok", "belegtyp": "bank_zinsausweis",
            "fields": [
                _field("institut", "BANK-A"),
                _field("jahr", "2022"),
                _field("vermoegensstand_3112", "12'500.00"),
            ],
        },
        # Wertschriften → vollständig (Institut + bestand_3112).
        {
            "pdf_name": "wsv.pdf", "status": "ok",
            "belegtyp": "wertschriftenverzeichnis",
            "fields": [
                _field("institut", "BROKER-A"),
                _field("jahr", "2022"),
                _field("bestand_3112", "40'000.00"),
            ],
        },
        # KK Maria → vollständig (Person + praemie_kvg_total).
        {
            "pdf_name": "kk_maria.pdf", "status": "ok",
            "belegtyp": "kk_praemienbescheinigung",
            "fields": [
                _field("versicherte_person_name", "Maria Muster"),
                _field("jahr", "2022"),
                _field("praemie_kvg_total", "4'200.00"),
            ],
        },
        # Krankheitskosten Tim → vollständig via selbstgetragene_kosten (betrag
        # fehlt, Fallback greift).
        {
            "pdf_name": "krankheit_tim.pdf", "status": "ok",
            "belegtyp": "krankheitskosten",
            "fields": [
                _field("person_name", "Tim Muster"),
                _field("jahr", "2022"),
                _field("selbstgetragene_kosten", "320.50"),
            ],
        },
        # Säule 3a Hans → vollständig.
        {
            "pdf_name": "saeule3a_hans.pdf", "status": "ok",
            "belegtyp": "saeule_3a",
            "fields": [
                _field("kontoinhaber_name", "Hans Muster"),
                _field("jahr", "2022"),
                _field("einzahlung_betrag", "7'056.00"),
            ],
        },
        # Spenden → vollständig (Empfänger als Name, betrag).
        {
            "pdf_name": "spende.pdf", "status": "ok", "belegtyp": "spenden",
            "fields": [
                _field("empfaenger", "ORG-X"),
                _field("betrag", "500.00"),
            ],
        },
        # Kinderbetreuung Lina → vollständig.
        {
            "pdf_name": "kita_lina.pdf", "status": "ok",
            "belegtyp": "kinderbetreuung",
            "fields": [
                _field("kind_name", "Lina Muster"),
                _field("jahr", "2022"),
                _field("betrag", "6'000.00"),
            ],
        },
        # — Lücken-Fälle —
        # MR-Marker im Wert → manual_review_marker.
        {
            "pdf_name": "bank_mr.pdf", "status": "ok", "belegtyp": "bank_zinsausweis",
            "fields": [
                _field("institut", "BANK-B"),
                _field("vermoegensstand_3112", "manual_review:unklar"),
            ],
        },
        # Wert nicht parsebar (Dot-Tausender "7.793" wird seit dem
        # Punkt-Tausender-Fix korrekt als 7793 geparst — hier deshalb ein
        # echt unparsebarer Wert mit 4 Nachkommastellen).
        {
            "pdf_name": "bank_unparse.pdf", "status": "ok",
            "belegtyp": "bank_zinsausweis",
            "fields": [
                _field("institut", "BANK-C"),
                _field("vermoegensstand_3112", "7.7935"),
            ],
        },
        # Wert-Feld fehlt komplett.
        {
            "pdf_name": "bank_leer.pdf", "status": "ok",
            "belegtyp": "bank_zinsausweis",
            "fields": [
                _field("institut", "BANK-D"),
            ],
        },
        # Person unbestimmt (unbekannter Name) bei personensensitivem Lohn.
        {
            "pdf_name": "lohn_unbekannt.pdf", "status": "ok", "belegtyp": "lohnausweis",
            "fields": [
                _field("arbeitnehmer_name", "Xaver Unbekannt"),
                _field("nettolohn_pos11", "50'000.00"),
            ],
        },
        # Out-of-Scope.
        {
            "pdf_name": "vvk.pdf", "status": "out_of_scope",
            "belegtyp": "vermoegensverwaltungskosten",
            "out_of_scope_reason": "Jahresgebühr — nicht abzugsfähig",
            "fields": [],
        },
        # Fehlgeschlagen.
        {
            "pdf_name": "kaputt.pdf", "status": "extract_failed",
            "belegtyp": "lohnausweis", "error": "LLM-Timeout", "fields": [],
        },
    ]


def test_eine_zeile_pro_dokument(entries):
    rows = build_rows(entries)
    assert len(rows) == len(entries)


def test_vollstaendige_zeilen_haben_typ_name_wert(entries):
    rows = build_rows(entries)
    by_file = {r["datei"]: r for r in rows}

    lohn = by_file["lohn_hans.pdf"]
    assert lohn["status"] == STATUS_VOLLSTAENDIG
    assert lohn["dokumenttyp"] == "lohnausweis"
    assert "elternteil_1" in lohn["name"]  # Hans → elternteil_1
    assert lohn["wert"] == "85000.00"
    assert lohn["luecke"] == ""

    bank = by_file["bank.pdf"]
    assert bank["status"] == STATUS_VOLLSTAENDIG
    assert bank["name"] == "BANK-A"
    assert bank["wert"] == "12500.00"


def test_krankheitskosten_fallback_auf_selbstgetragene_kosten(entries):
    rows = build_rows(entries)
    krankheit = next(r for r in rows if r["datei"] == "krankheit_tim.pdf")
    assert krankheit["status"] == STATUS_VOLLSTAENDIG
    assert krankheit["wert"] == "320.50"


def test_spenden_nutzt_empfaenger_als_name(entries):
    rows = build_rows(entries)
    spende = next(r for r in rows if r["datei"] == "spende.pdf")
    assert spende["name"] == "ORG-X"
    assert spende["wert"] == "500.00"


def test_luecken_codes(entries):
    rows = build_rows(entries)
    by_file = {r["datei"]: r for r in rows}

    assert by_file["bank_mr.pdf"]["luecke"] == LUECKE_MR_MARKER
    assert by_file["bank_mr.pdf"]["wert"] == ""

    assert by_file["bank_unparse.pdf"]["luecke"] == LUECKE_NICHT_PARSEBAR
    assert by_file["bank_unparse.pdf"]["wert"] == ""

    assert by_file["bank_leer.pdf"]["luecke"] == LUECKE_FELD_FEHLT

    assert by_file["lohn_unbekannt.pdf"]["luecke"] == LUECKE_PERSON_UNBESTIMMT
    # Wert ist parsebar, aber Person fehlt → trotzdem nicht vollständig.
    assert by_file["lohn_unbekannt.pdf"]["status"] == STATUS_LUECKE


def test_out_of_scope_zeile(entries):
    rows = build_rows(entries)
    oos = next(r for r in rows if r["datei"] == "vvk.pdf")
    assert oos["status"] == STATUS_OOS
    assert oos["wert"] == ""
    assert "Jahresgebühr" in oos["luecke_detail"]


def test_fehlgeschlagene_zeile(entries):
    rows = build_rows(entries)
    kaputt = next(r for r in rows if r["datei"] == "kaputt.pdf")
    assert kaputt["status"] == STATUS_FEHLGESCHLAGEN
    assert kaputt["luecke"] == LUECKE_BELEG_FEHLGESCHLAGEN
    assert "LLM-Timeout" in kaputt["luecke_detail"]


def test_vollstaendigkeitsquote(entries):
    rows = build_rows(entries)
    full, total = completeness(rows)
    # 8 vollständige (lohn_hans, bank, wsv, kk_maria, krankheit_tim,
    # saeule3a_hans, spende, kita_lina); total = alle 14 Dokumente.
    assert total == 14
    assert full == 8
    header = header_line(rows, "2022")
    assert "8/14" in header
    assert "Steuerjahr 2022" in header


def test_stats_ohne_dateinamen(entries):
    rows = build_rows(entries)
    stats = render_stats(rows, "2022")
    # Kopfzeile + Lücken-Zähler, KEINE Dateinamen.
    for r in entries:
        assert r["pdf_name"] not in stats
    # Lücken-Zähler aggregiert pro (belegtyp, code).
    counts = gap_counts(rows)
    assert counts[("bank_zinsausweis", LUECKE_MR_MARKER)] == 1
    assert counts[("bank_zinsausweis", LUECKE_NICHT_PARSEBAR)] == 1
    assert counts[("bank_zinsausweis", LUECKE_FELD_FEHLT)] == 1
    assert counts[("lohnausweis", LUECKE_PERSON_UNBESTIMMT)] == 1
    assert counts[("lohnausweis", LUECKE_BELEG_FEHLGESCHLAGEN)] == 1
    assert "bank_zinsausweis" in stats
    assert "8/14" in stats


def test_resolve_value_by_design_keine_luecke():
    # not_in_beleg_by_design → leere Zelle, KEIN Lücken-Code.
    wert, luecke = _resolve_value(
        {"feld": "vermoegensstand_3112", "value": "not_in_beleg_by_design"}
    )
    assert wert == ""
    assert luecke is None


def test_build_row_status_na_by_design():
    # Ein Bank-Beleg, dessen Leitwert (vermoegensstand_3112) by-design fehlt:
    # Status "n/a by design", keine Lücke, nicht als vollständig gezählt.
    entry = {
        "pdf_name": "by_design.pdf", "status": "ok",
        "belegtyp": "bank_zinsausweis",
        "fields": [
            _field("institut", "BANK-A"),
            _field("jahr", "2022"),
            {"feld": "vermoegensstand_3112", "value": "not_in_beleg_by_design",
             "bbox": None, "page": None, "snippet": "", "anchor_valid": False,
             "inference_source": "user_verified:nur ertragsausweis"},
        ],
    }
    row = build_row(entry)
    assert row["status"] == STATUS_NA_BY_DESIGN
    assert row["luecke"] == ""
    assert row["wert"] == ""
    # Nicht als vollständig gezählt.
    full, total = completeness([row])
    assert full == 0
    assert total == 1
    # Keine Lücke im gap_counts.
    assert gap_counts([row]) == {}


def test_by_design_kvg_verdeckt_echtes_vvg_nicht():
    # Finding 4c: KVG ist by-design abwesend, VVG hat einen echten parsebaren
    # Wert. Das by-design-Erstfeld darf das echte VVG NICHT verdecken — die
    # Zeile ist vollständig MIT dem VVG-Wert.
    entry = {
        "pdf_name": "kk_vvg.pdf", "status": "ok",
        "belegtyp": "kk_praemienbescheinigung",
        "fields": [
            _field("versicherte_person_name", "Maria Muster"),
            {"feld": "praemie_kvg_total", "value": "not_in_beleg_by_design",
             "bbox": None, "page": None, "snippet": "", "anchor_valid": False,
             "inference_source": "user_verified:nur zusatzversicherung"},
            _field("praemie_vvg_total", "1200.00"),
        ],
    }
    row = build_row(entry)
    assert row["status"] == STATUS_VOLLSTAENDIG
    assert row["luecke"] == ""
    # VVG-Wert ist in der Zelle, nicht leer (by-design verdeckt ihn nicht).
    assert "1200" in row["wert"]
