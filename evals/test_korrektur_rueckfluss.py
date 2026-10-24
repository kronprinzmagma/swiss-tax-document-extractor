"""Tests des Korrektur-Rückflusses (260904-rmx).

Ohne diesen Schritt bleibt der Zirkel offen: Korrekturen dienen dem Grader als
Sollwert, aber die Tabelle, die tatsächlich in die Steuererklärung wandert,
zeigt weiter den alten Wert. Der Mensch müsste zweimal korrigieren.
"""
from __future__ import annotations

import json

from scripts.build_tax_output import (
    KONFIDENZ_SICHER, konfidenz_fuer, konfidenz_grund, wende_korrekturen_an,
)


def zeile(pdf="a.pdf", beschreibung="Selbstkosten", betrag="3980.25", **kw):
    basis = {"pdf_name": pdf, "beschreibung": beschreibung, "betrag": betrag,
             "herkunft": "modell", "anchor_valid": False, "plaus_errs": ["alt"],
             "plaus_hinweis": "alt", "manual_review_marker": None,
             "status": "manual_review", "field_name": "selbstgetragene_kosten",
             "derived": False}
    basis.update(kw)
    return basis


def schreibe(tmp_path, inhalt: dict, name="korrekturen.json"):
    (tmp_path / name).write_text(json.dumps(inhalt, ensure_ascii=False))
    return tmp_path


def test_korrigierter_wert_ersetzt_den_extrahierten(tmp_path):
    """Der reale Fall: Bruttorechnung durch den Selbstanteil ersetzen."""
    schreibe(tmp_path, {"a.pdf|Selbstkosten": {"soll": "732.75"}})
    rows = [zeile()]
    assert wende_korrekturen_an(rows, tmp_path) == 1
    assert rows[0]["betrag"] == "732.75"


def test_korrigierter_wert_ist_die_hoechste_konfidenz(tmp_path):
    """Ein Mensch hat im Original nachgesehen — verlaesslicher geht es nicht."""
    schreibe(tmp_path, {"a.pdf|Selbstkosten": {"soll": "732.75"}})
    rows = [zeile()]
    wende_korrekturen_an(rows, tmp_path)
    assert rows[0]["herkunft"] == "mensch"
    assert konfidenz_fuer(rows[0]) == KONFIDENZ_SICHER
    assert "geprüft" in konfidenz_grund(rows[0])


def test_alte_befunde_verschwinden(tmp_path):
    """Der Befund bezog sich auf den ersetzten Wert."""
    schreibe(tmp_path, {"a.pdf|Selbstkosten": {"soll": "732.75"}})
    rows = [zeile()]
    wende_korrekturen_an(rows, tmp_path)
    assert rows[0]["plaus_errs"] == []
    assert rows[0]["plaus_hinweis"] is None
    assert rows[0]["status"] == "auto"


def test_marker_wird_durch_korrektur_geloest(tmp_path):
    schreibe(tmp_path, {"a.pdf|Selbstkosten": {"soll": "732.75"}})
    rows = [zeile(betrag=None, manual_review_marker="manual_review:kein_anker")]
    wende_korrekturen_an(rows, tmp_path)
    assert rows[0]["betrag"] == "732.75"
    assert rows[0]["manual_review_marker"] is None


def test_unveraenderter_wert_zaehlt_nicht_als_korrektur(tmp_path):
    """Bestaetigen ohne Aenderung ist keine Korrektur."""
    schreibe(tmp_path, {"a.pdf|Selbstkosten": {"soll": "3980.25"}})
    assert wende_korrekturen_an([zeile()], tmp_path) == 0


def test_eintrag_ohne_sollwert_aendert_nichts(tmp_path):
    """Nur eine notierte Beschriftung ist ein Regel-Hinweis."""
    schreibe(tmp_path, {"a.pdf|Selbstkosten": {"label": "Ihr Anteil"}})
    rows = [zeile()]
    assert wende_korrekturen_an(rows, tmp_path) == 0
    assert rows[0]["betrag"] == "3980.25"


def test_andere_zeile_bleibt_unberuehrt(tmp_path):
    schreibe(tmp_path, {"a.pdf|Selbstkosten": {"soll": "732.75"}})
    rows = [zeile(pdf="b.pdf")]
    assert wende_korrekturen_an(rows, tmp_path) == 0


def test_ohne_datei_passiert_nichts(tmp_path):
    assert wende_korrekturen_an([zeile()], tmp_path) == 0


def test_datei_eine_ebene_hoeher_wird_gefunden(tmp_path):
    """Produktiver Lauf: Ergebnisse in json/, korrekturen.json darueber."""
    schreibe(tmp_path, {"a.pdf|Selbstkosten": {"soll": "732.75"}})
    unter = tmp_path / "json"
    unter.mkdir()
    rows = [zeile()]
    assert wende_korrekturen_an(rows, unter) == 1


def test_review_oberflaeche_wendet_korrekturen_an(tmp_path):
    """Der Rueckfluss war nur in build_tax_output verdrahtet, nicht in der
    Oberflaeche — nach jedem Lauf zeigte sie wieder die alten Werte und die
    ganze Durchsicht waere erneut zu machen gewesen."""
    import inspect
    from scripts import build_review_html
    quelle = inspect.getsource(build_review_html.sammle_zeilen)
    assert "wende_korrekturen_an" in quelle
    assert "ergaenze_eigene_zeilen" in quelle


def test_bestaetigungen_werden_gelesen(tmp_path):
    """33 von 47 Eintraegen waren Bestaetigungen — sie gingen komplett
    verloren, die Durchsicht waere jedes Mal zu wiederholen gewesen."""
    from scripts.apply_korrekturen import lies_korrekturen
    p = tmp_path / "k.csv"
    p.write_text("nr,beleg,ziffer,zielwert,person,aussteller,betrag,konfidenz,"
                 "grund,herkunft,korrektur,notiz\n"
                 "1,a.pdf,4,Bruttoertrag,e1,B,10.00,sicher,g,regel,,"
                 "Betrag bestätigt | Person bestätigt\n")
    z = lies_korrekturen(p)
    assert len(z) == 1
    assert "Betrag bestätigt" in z[0]["notiz"]


def test_review_merkt_bestaetigungen_vor():
    """Die Haken muessen gesetzt erscheinen, sonst hakt man alles neu ab.

    Geprueft wird das Verhalten, nicht der Quelltext: welches Feld die
    Bestaetigung traegt, entscheidet `gilt_als_bestaetigt`.
    """
    from extractors.korrekturen import gilt_als_bestaetigt

    assert gilt_als_bestaetigt({"bestaetigt_betrag": True})
    assert not gilt_als_bestaetigt({})
    assert not gilt_als_bestaetigt({"soll": "100"})


def test_selbst_eingetippter_betrag_gilt_als_geprueft():
    """Wer einen Betrag von Hand erfasst, hat ihn im Beleg gelesen. Ihn danach
    noch einmal abhaken zu lassen ist Buerokratie — und liess drei Dokumente
    dauerhaft als „ungeprueft" dastehen (260923-dua, vom Nutzer gemeldet)."""
    from extractors.korrekturen import gilt_als_bestaetigt

    assert gilt_als_bestaetigt({"neu": True, "soll": "250000"})
    # Ohne Betrag ist nichts geprueft.
    assert not gilt_als_bestaetigt({"neu": True})
    # Gestrichen oder zurueckgenommen zaehlt nicht.
    assert not gilt_als_bestaetigt({"neu": True, "soll": "1", "entfernt": True})
    assert not gilt_als_bestaetigt({"neu": True, "soll": "1",
                                    "zurueckgenommen": True})
