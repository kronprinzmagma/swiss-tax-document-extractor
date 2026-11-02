"""Fälschlich erkannte Felder müssen verschwinden können (260904-rmx).

Bis hierher kannte der Zirkel nur zwei Aussagen: „der Betrag stimmt" und
„der Betrag ist ein anderer". Für den häufigsten Befund gab es keine — die
PLZ als Prämie, die AHV-Nummer als Abzug, eine Zusatzversicherung in der
Abrechnung der Grundversicherung. Wer solche Zeilen überschrieb, machte aus
einem falschen Wert einen anderen falschen Wert; die Zeile blieb.

Streichen ist deshalb kein Sonderfall von Korrigieren, sondern eine eigene
Aussage — und eine engere, als sie zuerst hiess: **dieses Dokument weist den
Wert nicht aus.** Nicht „der Wert gehört nicht in die Steuererklärung". Die
Zusatzversicherung aus dem Beispiel existiert sehr wohl, sie liegt bei einer
anderen Kasse und in einem anderen Dokument. Also muss die
Vollständigkeitsprüfung sie weiterhin einfordern — sonst verschwiegen wir
mit der falschen Zeile auch den echten Abzug.
"""
from __future__ import annotations

import json
import sys

from scripts.apply_korrekturen import lies_korrekturen, main
from scripts.build_tax_output import wende_korrekturen_an

KOPF = ("nr,beleg,ziffer,zielwert,person,aussteller,betrag,konfidenz,grund,"
        "herkunft,korrektur,person_neu,aussteller_neu,zielwert_neu,ziffer_neu,"
        "bestaetigt,streichen,pos_x,pos_y,pos_seite,zeilenkontext,notiz\n")


def zeile(**kw) -> dict:
    basis = {"pdf_name": "a.pdf", "beschreibung": "Prämie Grundversicherung KVG",
             "betrag": "8000.00", "person": "elternteil_1", "aussteller": "KK",
             "ziffer": "15", "herkunft": "modell", "anchor_valid": True,
             "plaus_errs": [], "plaus_hinweis": None,
             "manual_review_marker": None, "status": "auto",
             "field_name": "praemie_kvg", "derived": False}
    basis.update(kw)
    return basis


def schreibe(tmp_path, inhalt: dict):
    (tmp_path / "korrekturen.json").write_text(json.dumps(inhalt, ensure_ascii=False))
    return tmp_path


# --- Wirkung auf die Tabelle ------------------------------------------------

def test_gestrichene_zeile_verschwindet(tmp_path):
    schluessel = "a.pdf|Prämie Grundversicherung KVG"
    schreibe(tmp_path, {schluessel: {"entfernt": True}})
    rows = [zeile()]
    assert wende_korrekturen_an(rows, tmp_path) == 1
    assert rows == []


def test_nur_die_gestrichene_verschwindet(tmp_path):
    schreibe(tmp_path, {"a.pdf|Prämie Grundversicherung KVG": {"entfernt": True}})
    rows = [zeile(), zeile(pdf_name="b.pdf")]
    wende_korrekturen_an(rows, tmp_path)
    assert [r["pdf_name"] for r in rows] == ["b.pdf"]


def test_streichen_schlaegt_bestaetigung(tmp_path):
    """Wer eine Zeile streicht, hat sie damit nicht auch bestaetigt."""
    schreibe(tmp_path, {"a.pdf|Prämie Grundversicherung KVG": {
        "entfernt": True, "soll": "8000.00"}})
    rows = [zeile()]
    wende_korrekturen_an(rows, tmp_path)
    assert rows == []


def test_ohne_streichung_bleibt_alles(tmp_path):
    schreibe(tmp_path, {"a.pdf|Prämie Grundversicherung KVG": {"entfernt": False}})
    rows = [zeile()]
    wende_korrekturen_an(rows, tmp_path)
    assert len(rows) == 1


# --- Weg durch die CSV ------------------------------------------------------

def test_spalte_streichen_wird_gelesen(tmp_path):
    p = tmp_path / "k.csv"
    p.write_text(KOPF + "1,a.pdf,15,Prämie Grundversicherung KVG,e1,KK,8000.00,"
                        "unsicher,g,modell,,,,,,,ja,,,,,\n")
    assert len(lies_korrekturen(p)) == 1


def test_streichung_landet_in_korrekturen_json(tmp_path):
    p = tmp_path / "k.csv"
    p.write_text(KOPF + "1,a.pdf,15,Prämie Grundversicherung KVG,e1,KK,8000.00,"
                        "unsicher,g,modell,,,,,,,ja,,,,,\n")
    ziel = tmp_path / "korrekturen.json"
    argv = sys.argv
    sys.argv = ["x", "--file", str(p), "--out", str(ziel)]
    try:
        main()
    finally:
        sys.argv = argv
    e = json.loads(ziel.read_text())["a.pdf|Prämie Grundversicherung KVG"]
    assert e["entfernt"] is True
    assert "soll" not in e


def test_ganzer_weg_von_der_csv_bis_zur_tabelle(tmp_path):
    """Der Zirkel als Ganzes — hier faellt auf, wenn ein Glied fehlt."""
    p = tmp_path / "k.csv"
    p.write_text(KOPF + "1,a.pdf,15,Prämie Grundversicherung KVG,e1,KK,8000.00,"
                        "unsicher,g,modell,,,,,,,ja,,,,,\n")
    argv = sys.argv
    sys.argv = ["x", "--file", str(p)]
    try:
        main()
    finally:
        sys.argv = argv
    rows = [zeile()]
    wende_korrekturen_an(rows, tmp_path)
    assert rows == []


# --- Der Zeilentrenner ------------------------------------------------------

def test_csv_mit_literalen_backslash_n_wird_gerettet(tmp_path):
    """Der Export schrieb den Trenner eine Zeit lang als Backslash-n.

    Die Datei sah heil aus — eine Zeile, alles drin — und apply_korrekturen
    meldete "nichts zu tun". Wer nicht nachsah, verlor die ganze Durchsicht.
    """
    p = tmp_path / "kaputt.csv"
    p.write_text(KOPF.rstrip("\n") + "\\n"
                 + "1,a.pdf,15,Prämie Grundversicherung KVG,e1,KK,8000.00,"
                   "unsicher,g,modell,,,,,,,ja,,,,,\\n")
    zeilen = lies_korrekturen(p)
    assert len(zeilen) == 1
    assert zeilen[0]["streichen"] == "ja"


def test_heile_csv_wird_nicht_angefasst(tmp_path):
    """Ein echter Zeilenumbruch darf nicht als Reparaturfall gelten."""
    p = tmp_path / "heil.csv"
    p.write_text(KOPF + "1,a.pdf,15,Prämie Grundversicherung KVG,e1,KK,8000.00,"
                        "unsicher,g,modell,,,,,,,ja,,,,,\n")
    assert len(lies_korrekturen(p)) == 1


def test_notiz_mit_backslash_n_bleibt_unversehrt(tmp_path):
    """Eine mehrzeilige Datei wird nie repariert — auch wenn irgendwo ein
    literales Backslash-n in einer Notiz steht."""
    p = tmp_path / "ok.csv"
    p.write_text(KOPF
                 + "1,a.pdf,15,Prämie Grundversicherung KVG,e1,KK,8000.00,"
                   "unsicher,g,modell,,,,,,,,,,,,Pfad C:\\neu\n"
                 + "2,b.pdf,15,Prämie Grundversicherung KVG,e1,KK,10.00,"
                   "unsicher,g,modell,,,,,,,ja,,,,,\n")
    zeilen = lies_korrekturen(p)
    assert len(zeilen) == 2
    assert zeilen[0]["notiz"] == "Pfad C:\\neu"


# --- Was Streichen NICHT heisst ---------------------------------------------

def test_gestrichene_vvg_wird_weiter_eingefordert(tmp_path):
    """Der Fall aus der Praxis, wortwoertlich.

    In der Abrechnung der Grundversicherung stand eine Zusatzversicherung,
    die dort nicht hingehoert — sie laeuft bei einer anderen Kasse. Wird die
    Zeile gestrichen, darf daraus nicht "es gibt keine VVG" werden: dann
    fiele ein echter Abzug lautlos unter den Tisch.
    """
    from extractors.vollstaendigkeit import pruefe_erwartetes_optionales

    schreibe(tmp_path, {"kk.pdf|Prämie Zusatzversicherung VVG": {
        "entfernt": True, "grund": "nicht_im_dokument"}})
    rows = [
        zeile(pdf_name="kk.pdf", beschreibung="Prämie Grundversicherung KVG"),
        zeile(pdf_name="kk.pdf", beschreibung="Prämie Zusatzversicherung VVG"),
    ]
    wende_korrekturen_an(rows, tmp_path)
    assert [r["beschreibung"] for r in rows] == ["Prämie Grundversicherung KVG"]

    tabelle = [{"person": r["person"], "beschreibung": r["beschreibung"],
                "pdf_name": r["pdf_name"]} for r in rows]
    codes = [b.code for b in pruefe_erwartetes_optionales(tabelle, ("elternteil_1",))]
    assert "vvg_fehlt" in codes


def test_grund_steht_im_eintrag(tmp_path):
    """Warum die Zeile weg ist, gehoert festgehalten — daraus wird die Regel,
    dass dieser Belegtyp dieses Feld nicht ausweist."""
    p = tmp_path / "k.csv"
    p.write_text(KOPF + "1,kk.pdf,15,Prämie Zusatzversicherung VVG,e1,KK,120.00,"
                        "unsicher,g,modell,,,,,,,ja,,,,,\n")
    ziel = tmp_path / "korrekturen.json"
    argv = sys.argv
    sys.argv = ["x", "--file", str(p), "--out", str(ziel)]
    try:
        main()
    finally:
        sys.argv = argv
    e = json.loads(ziel.read_text())["kk.pdf|Prämie Zusatzversicherung VVG"]
    assert e["grund"] == "nicht_im_dokument"
