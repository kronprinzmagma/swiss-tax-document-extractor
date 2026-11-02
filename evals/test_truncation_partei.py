"""E6 (Truncation-Summenfenster) + E7 (Parteibeitrag) — Quick 260612-l2g.

Rein synthetisch, deterministisch, kein Ollama.
"""
from extractors.llm_extract import _MAX_PLAIN_TEXT_LIMIT_FOR_TEST, _extract_summen_fenster
from scripts.build_tax_output import build_rows, is_parteibeitrag


# --------------------------------------------------------------------------- #
# E6 — Truncation-Limit 12000 + WSV-Summenfenster                             #
# --------------------------------------------------------------------------- #


def test_max_plain_text_limit_ist_12000():
    assert _MAX_PLAIN_TEXT_LIMIT_FOR_TEST == 12_000


def test_summen_fenster_findet_steuerwert():
    text = (
        "Position 1 ... viele Zeilen ... "
        + "X" * 500
        + " Steuerwert der A- und B-Werte 123456.00 Bruttoertrag 789.00"
    )
    fenster = _extract_summen_fenster(text)
    assert "Steuerwert der A- und B-Werte" in fenster
    assert "123456.00" in fenster


def test_summen_fenster_leer_ohne_label():
    assert _extract_summen_fenster("Nur Fliesstext ohne Summenlabel") == ""


# --------------------------------------------------------------------------- #
# E7 — Parteibeitrag-Kategorie                                                #
# --------------------------------------------------------------------------- #


def _spenden_entry(empfaenger: str, betrag: str, snippet: str = "") -> dict:
    return {
        "pdf_name": "Spende_Beleg",
        "belegtyp": "spenden",
        "status": "ok",
        "fields": [
            {"feld": "empfaenger", "value": empfaenger, "snippet": snippet,
             "page": 1, "bbox": None, "anchor_valid": False},
            {"feld": "betrag", "value": betrag, "snippet": "",
             "page": 1, "bbox": None, "anchor_valid": False},
            {"feld": "jahr", "value": "2023", "snippet": "",
             "page": 1, "bbox": None, "anchor_valid": False},
        ],
    }


def test_is_parteibeitrag_erkennt_mitgliederbeitrag_partei():
    entry = _spenden_entry(
        "INST-A", "300.00", snippet="Mitgliederbeitrag GLP Sektion"
    )
    assert is_parteibeitrag(entry) is True


def test_is_parteibeitrag_generisches_partei_keyword():
    entry = _spenden_entry(
        "Beispiel Partei", "200.00", snippet="Mitgliederbeitrag 2023"
    )
    assert is_parteibeitrag(entry) is True


def test_is_parteibeitrag_gewoehnliche_spende_nein():
    entry = _spenden_entry("INST-B", "500.00", snippet="Spende WWF gemeinnützig")
    assert is_parteibeitrag(entry) is False


def test_is_parteibeitrag_mitgliederbeitrag_ohne_partei_nein():
    """Mitgliederbeitrag an einen Verein ohne Partei-Kontext → keine Partei."""
    entry = _spenden_entry("INST-C", "80.00", snippet="Mitgliederbeitrag Turnverein")
    assert is_parteibeitrag(entry) is False


def test_build_rows_parteibeitrag_eigene_zeile():
    entry = _spenden_entry("INST-A", "300.00", snippet="Mitgliederbeitrag SP")
    rows = build_rows(entry)
    assert len(rows) == 1
    row = rows[0]
    assert row["beschreibung"] == "Parteimitgliederbeitrag"
    assert row["betrag"] == "300.00"
    assert "Spende" not in row["beschreibung"]
