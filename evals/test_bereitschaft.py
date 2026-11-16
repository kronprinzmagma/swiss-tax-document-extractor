"""Die eine Frage: kann die Steuererklaerung ausgefuellt werden? (260904-rmx)

Fuer den Sample-Korpus gibt es den Grader. Fuer den produktiven Lauf gab es
nichts — man musste die Oberflaeche durchscrollen und uebersah dabei genau
die Dokumente, die gar nicht angezeigt wurden.
"""
from __future__ import annotations

import json
from pathlib import Path

from scripts.bereitschaft import pruefe


def f(feld, wert):
    return {"feld": feld, "value": wert, "bbox": [1, 2, 3, 4], "page": 1,
            "anchor_valid": True}


def baue(tmp_path, pdfs, jsons, ergebnisse, korrekturen=None):
    quelle, ziel = tmp_path / "quelle", tmp_path / "json"
    quelle.mkdir()
    ziel.mkdir()
    for n in pdfs:
        (quelle / n).write_bytes(b"%PDF-1.4\n")
    for n, typ in jsons.items():
        (ziel / (n.removesuffix(".pdf") + ".json")).write_text(
            json.dumps({"pdf_name": n, "belegtyp": typ, "pages": []}))
    (ziel / "_results_full.json").write_text(json.dumps(ergebnisse))
    if korrekturen is not None:
        (ziel / "korrekturen.json").write_text(json.dumps(korrekturen))
    return quelle, ziel


def punkt(punkte, teil):
    return next(p for p in punkte if teil in p.titel)


def test_nicht_eingelesenes_pdf_ist_offen(tmp_path):
    quelle, ziel = baue(tmp_path, ["scan.pdf"], {}, [])
    assert not punkt(pruefe(quelle, ziel), "eingelesen").ok


def test_dokument_ohne_position_ist_offen(tmp_path):
    quelle, ziel = baue(tmp_path, ["a.pdf"], {"a.pdf": "bank_zinsausweis"},
                        [{"pdf_name": "a.pdf", "status": "ok",
                          "belegtyp": "bank_zinsausweis",
                          "fields": [f("vermoegensstand_3112", "0.00")]}])
    p = punkt(pruefe(quelle, ziel), "liefert eine Position")
    assert not p.ok
    assert "a.pdf" in p.betroffen


def test_abgehaktes_dokument_ist_erledigt(tmp_path):
    """„irrelevant" ist eine gueltige Antwort — sonst haekelt man ewig."""
    quelle, ziel = baue(tmp_path, ["a.pdf"], {"a.pdf": None},
                        [{"pdf_name": "a.pdf", "status": "unknown_belegtyp",
                          "fields": []}],
                        korrekturen={"a.pdf|irrelevant": {"zielwert": "irrelevant"}})
    punkte = pruefe(quelle, ziel)
    assert punkt(punkte, "liefert eine Position").ok
    assert punkt(punkte, "Belegart").ok


def test_unbekannt_manuell_gilt_nicht_als_zuordnung(tmp_path):
    """Die Rolle heisst gerade „hier fehlt sie noch"."""
    quelle, ziel = baue(
        tmp_path, ["k.pdf"], {"k.pdf": "kk_praemienbescheinigung"},
        [{"pdf_name": "k.pdf", "status": "ok",
          "belegtyp": "kk_praemienbescheinigung",
          "fields": [f("praemie_kvg_total", "3120.40")]}])
    p = punkt(pruefe(quelle, ziel), "zugeordnet")
    assert not p.ok


def test_vollstaendiger_lauf_ist_bereit(tmp_path):
    eintraege = []
    for rolle, name in (("Vorname A", "e1"), ("Vorname B", "e2")):
        eintraege += [
            {"pdf_name": f"lohn-{name}.pdf", "status": "ok",
             "belegtyp": "lohnausweis",
             "fields": [f("nettolohn_pos11", "85420.00"),
                        f("arbeitnehmer", rolle)]},
        ]
    quelle, ziel = baue(
        tmp_path, [e["pdf_name"] for e in eintraege],
        {e["pdf_name"]: e["belegtyp"] for e in eintraege}, eintraege)
    punkte = pruefe(quelle, ziel)
    # Nicht alles kann gruen sein (die KVG fehlt in diesem Mini-Korpus),
    # aber die dokumentbezogenen Punkte muessen es sein.
    assert punkt(punkte, "eingelesen").ok
    assert punkt(punkte, "liefert eine Position").ok
    assert punkt(punkte, "Belegart").ok
    assert punkt(punkte, "Betrag").ok


def test_jeder_punkt_sagt_was_zu_tun_ist(tmp_path):
    quelle, ziel = baue(tmp_path, ["scan.pdf"], {}, [])
    for p in pruefe(quelle, ziel):
        if not p.ok:
            assert p.was_tun, p.titel


# --- Aufschluesselung nach Belegart -----------------------------------------

def test_offene_punkte_sagen_wo_das_problem_sitzt(tmp_path):
    """„6 ohne Betrag" hilft niemandem — „6× Hypotheken" schon, und es nennt
    keinen Dateinamen."""
    quelle, ziel = baue(
        tmp_path, ["h.pdf", "k.pdf"],
        {"h.pdf": "hypothek_zinsbestaetigung",
         "k.pdf": "kk_praemienbescheinigung"},
        [{"pdf_name": "h.pdf", "status": "ok",
          "belegtyp": "hypothek_zinsbestaetigung",
          "fields": [f("institut", "BANK-A"), f("schuldzinsen", ""),
                     f("schuldsaldo_3112", "")]},
         {"pdf_name": "k.pdf", "status": "ok",
          "belegtyp": "kk_praemienbescheinigung",
          "fields": [f("praemie_kvg_total", "3120.40")]}])
    p = punkt(pruefe(quelle, ziel), "Betrag")
    assert not p.ok
    assert p.nach_art.get("hypothek_zinsbestaetigung")
    assert "kk_praemienbescheinigung" not in p.nach_art


def test_aufschluesselung_nennt_keine_dateinamen(tmp_path):
    quelle, ziel = baue(
        tmp_path, ["Vorname Nachname 2025.pdf"],
        {"Vorname Nachname 2025.pdf": "hypothek_zinsbestaetigung"},
        [{"pdf_name": "Vorname Nachname 2025.pdf", "status": "ok",
          "belegtyp": "hypothek_zinsbestaetigung",
          "fields": [f("institut", "BANK-A"), f("schuldzinsen", "")]}])
    for p in pruefe(quelle, ziel):
        for art in p.nach_art:
            assert "Nachname" not in art


# --- Zwei verschiedene Fragen (260923-dua) ---------------------------------
#
# Punkt 8: kommt die Durchsicht in der Tabelle an? (Bezug abgerissen?)
# Punkt 9: liest der Lauf die bestaetigten Werte weiterhin richtig? (Drift?)
#
# Die erste Fassung warf beides zusammen und verglich gegen den rohen Lauf.
# Damit war jede KORREKTUR eine Abweichung — sie weicht ja gerade ab, das ist
# ihr Zweck — und der Punkt dauerhaft rot.


def _korpus(tmp_path, korrekturen: dict, eintraege: list[dict]):
    import json as _json
    samples = tmp_path / "json"
    samples.mkdir()
    (samples / "_results_full.json").write_text(_json.dumps(eintraege))
    (samples / "korrekturen.json").write_text(_json.dumps(korrekturen))
    return samples


def _lohn(wert: str) -> list[dict]:
    return [{
        "pdf_name": "l.pdf", "status": "ok", "belegtyp": "lohnausweis",
        "fields": [{"feld": "nettolohn_pos11", "value": wert},
                   {"feld": "jahr", "value": "2025"}],
    }]


def _punkte(tmp_path, samples):
    from scripts.bereitschaft import pruefe
    return {p.titel: p for p in pruefe(tmp_path / "leer", samples)}


def test_korrektur_ist_keine_abweichung(tmp_path):
    """Eine Korrektur weicht vom rohen Lauf ab — das ist ihr Zweck. Sie als
    Rueckschritt zu melden macht den Punkt dauerhaft rot und damit wertlos."""
    samples = _korpus(tmp_path, {"l.pdf|Nettolohn": {"soll": "91234"}},
                      _lohn("88000"))
    p = _punkte(tmp_path, samples)
    assert p["Der Lauf liest die bestätigten Werte weiterhin richtig"].ok
    # Und in der Tabelle steht der korrigierte Wert — Bezug intakt.
    assert p["Die Durchsicht steht in der Tabelle"].ok


def test_abgerissener_bezug_faellt_auf(tmp_path):
    """Ein Eintrag, dessen Zeile es nicht mehr gibt — nach einer Umbenennung
    oder einem verlorenen Schluessel. Genau das ist mehrfach passiert."""
    samples = _korpus(tmp_path, {"weg.pdf|Nettolohn": {"soll": "91234"}},
                      _lohn("88000"))
    p = _punkte(tmp_path, samples)
    assert p["Die Durchsicht steht in der Tabelle"].ok is False


def test_drift_des_modells_faellt_auf(tmp_path):
    """Der Mensch hat den Wert bestaetigt, ohne ihn zu korrigieren — das
    Modell hat ihn also richtig gelesen. Liest es ihn morgen anders, hat sich
    die Extraktion verschlechtert."""
    samples = _korpus(tmp_path, {
        "l.pdf|Nettolohn": {"bestaetigt_betrag": True, "wert_betrag": "88000"},
    }, _lohn("77777"))
    p = _punkte(tmp_path, samples)
    assert p["Der Lauf liest die bestätigten Werte weiterhin richtig"].ok is False


def test_kein_drift_wenn_der_lauf_trifft(tmp_path):
    samples = _korpus(tmp_path, {
        "l.pdf|Nettolohn": {"bestaetigt_betrag": True, "wert_betrag": "88000"},
    }, _lohn("88000"))
    p = _punkte(tmp_path, samples)
    assert p["Der Lauf liest die bestätigten Werte weiterhin richtig"].ok


def test_selbst_erfasste_position_zaehlt_bei_keinem_von_beiden(tmp_path):
    """Sie stammt aus der Durchsicht — im rohen Lauf kann es sie nicht geben,
    und in der Tabelle steht sie ueber ergaenze_eigene_zeilen."""
    eintraege = [{"pdf_name": "h.pdf", "status": "ok",
                  "belegtyp": "hypothek_zinsbestaetigung", "fields": []}]
    samples = _korpus(tmp_path, {
        "h.pdf|Hypothekarschuld 31.12.#2": {"soll": "250000", "neu": True},
    }, eintraege)
    p = _punkte(tmp_path, samples)
    assert p["Der Lauf liest die bestätigten Werte weiterhin richtig"].ok
    assert p["Die Durchsicht steht in der Tabelle"].ok
