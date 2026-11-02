"""Wertschriften: Ertrag mit und ohne Verrechnungssteuer (260904-rmx).

Der Anwender hat es dreimal gesagt: es gibt nicht „Bruttoertrag" und
„Verrechnungssteuer 35 %", sondern **einen Betrag mit** und **einen ohne**
Verrechnungssteuer. Das Wertschriftenverzeichnis hat dafür zwei Abteilungen.
Der Feldvertrag hatte zwei Schubladen für drei Dinge, und keine davon war
die, die man ausfüllt.

Die Verrechnungssteuer selbst ist kein Übertragungswert: sie beträgt 35 % der
Erträge in Abteilung A und wird gerechnet, nicht abgeschrieben.
"""
from __future__ import annotations

from extractors.zielwerte import ZIELWERTE
from scripts.build_review_html import ZIELWERT_WAHL, ZIELWERT_ZIFFER
from scripts.build_tax_output import ALTE_BEZEICHNUNGEN, TRANSFER_MAP

MIT = "Bruttoertrag mit Verrechnungssteuer"
OHNE = "Bruttoertrag ohne Verrechnungssteuer"


def bezeichnungen(belegtyp: str) -> set[str]:
    return {z.bezeichnung for z in ZIELWERTE[belegtyp]}


def test_beide_abteilungen_existieren():
    for typ in ("bank_zinsausweis", "wertschriftenverzeichnis"):
        assert MIT in bezeichnungen(typ), typ
        assert OHNE in bezeichnungen(typ), typ


def test_keine_zeile_heisst_mehr_verrechnungssteuer():
    """Der Steuerbetrag ist keine Uebertragungszeile — die Abteilung ist es."""
    for typ in ("bank_zinsausweis", "wertschriftenverzeichnis"):
        assert not any(b.startswith("Verrechnungssteuer")
                       for b in bezeichnungen(typ))


def test_zuordnung_folgt_der_erfassung_des_nutzers():
    """Der Anwender hat die zwei vorhandenen Felder fuer seine zwei Betraege benutzt:
    ``bruttoertrag`` ist der Betrag OHNE, ``verrechnungssteuer`` der MIT
    Verrechnungssteuer. Dreht das jemand um, stehen beide Betraege in der
    falschen Abteilung des Wertschriftenverzeichnisses."""
    zu = {z.feld: z.bezeichnung for z in ZIELWERTE["bank_zinsausweis"]}
    assert zu["bruttoertrag"] == OHNE
    assert zu["verrechnungssteuer"] == MIT
    zu2 = {z.feld: z.bezeichnung for z in ZIELWERTE["wertschriftenverzeichnis"]}
    assert zu2["bruttoertrag_total"] == OHNE
    assert zu2["verrechnungssteuer_total"] == MIT


def test_die_tabelle_fuehrt_beide():
    for typ, felder in (("bank_zinsausweis",
                         {"bruttoertrag", "verrechnungssteuer"}),
                        ("wertschriftenverzeichnis",
                         {"bruttoertrag_total", "verrechnungssteuer_total"})):
        vorhanden = {f for f, _, _ in TRANSFER_MAP[typ]}
        assert felder <= vorhanden, typ


def test_beide_sind_in_der_review_waehlbar():
    assert OHNE in ZIELWERT_WAHL
    assert MIT in ZIELWERT_WAHL
    assert ZIELWERT_ZIFFER[OHNE] == "4"
    assert ZIELWERT_ZIFFER[MIT] == "4"


def test_beide_abteilungen_gehen_in_ziffer_4():
    for typ in ("bank_zinsausweis", "wertschriftenverzeichnis"):
        for z in ZIELWERTE[typ]:
            if "Bruttoertrag" in z.bezeichnung:
                assert z.ziffer == "4"


def test_alte_bestaetigungen_behalten_ihren_bezug():
    """Beim Umbenennen darf die Durchsicht nicht ungueltig werden."""
    assert "Bruttozins / Bruttoertrag" in ALTE_BEZEICHNUNGEN[OHNE]
    assert "Bruttoertrag Wertschriften" in ALTE_BEZEICHNUNGEN[OHNE]
    assert "Verrechnungssteuer 35%" in ALTE_BEZEICHNUNGEN[MIT]


def test_umbenannte_zeile_findet_ihre_korrektur(tmp_path):
    import json

    from scripts.build_tax_output import wende_korrekturen_an
    (tmp_path / "korrekturen.json").write_text(json.dumps(
        {"a.pdf|Bruttozins / Bruttoertrag": {"person": "familie",
                                             "bestaetigt_betrag": True}}))
    rows = [{"pdf_name": "a.pdf", "beschreibung": OHNE, "betrag": "10.00",
             "person": "", "aussteller": "B", "ziffer": "4",
             "herkunft": "modell", "anchor_valid": True, "plaus_errs": [],
             "plaus_hinweis": None, "manual_review_marker": None,
             "status": "auto", "field_name": "bruttoertrag", "derived": False}]
    assert wende_korrekturen_an(rows, tmp_path) == 1
    assert rows[0]["person"] == "familie"
