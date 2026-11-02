"""Herkunft und Anker in der Leiste (261025-hbv, B4–B6).

* Ein Wert aus einem Dokument, das nicht mehr im Ordner liegt, fällt nur
  weg, wenn derselbe Betrag bei derselben Kasse für dieselbe Person aus dem
  Lauf da ist — und der behaltene Wert sagt es (B4).
* Ein Modellwert wird durch eine Prämienzeile höchstens „modell_bestaetigt",
  nie „regel"; eine Monatsprämie bestätigt nichts (B5).
* Jeder Wert eines festen Lesers trägt einen Anker (B5); Berechnetes heisst
  berechnet, Kontrollzahlen haben keinen Knopf (B6).

Alle Werte erfunden.
"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from extractors import rohtabelle

ANKER = {"seite": 1, "bbox": [50, 96, 300, 104], "snippet": "Total Prämie | 3'852.00"}


def zeile(y, *texte, seite=1):
    cs = [rohtabelle.Zelle(t, 50 + 100 * i, 90 + 100 * i) for i, t in enumerate(texte)]
    return rohtabelle.Zeile(seite, y, 50, cs, (50, y - 4, 300, y + 4))


def tab(dok, *zeilen):
    return rohtabelle.Rohtabelle(dok, list(zeilen))


# --- B4: kein stilles Streichen bei gleichem Betrag ------------------------

def _praemie(betrag, quelle, herkunft="ablage"):
    return {"bezeichnung": "Prämie Grundversicherung KVG", "betrag": betrag,
            "quelle": quelle, "herkunft": herkunft}


def test_zwei_kinder_gleiche_praemie_verwaister_wert_bleibt():
    from scripts.export_extension import _verwaiste_markieren

    formulare = [{"formular": "Versicherungsprämien", "bloecke": [
        {"titel": "KK-A · kind_1", "aussteller": "KK-A", "person": "kind_1",
         "werte": [_praemie("1'234.50", "kk-a kind1.pdf")]},
        {"titel": "KK-A · kind_2", "aussteller": "KK-A", "person": "kind_2",
         "werte": [_praemie("1'234.50", "kk-a kind2 alt.pdf")]}]}]
    _verwaiste_markieren(formulare, {"kk-a kind1.pdf"})
    (b1, b2) = formulare[0]["bloecke"]
    assert len(b2["werte"]) == 1
    assert b2["werte"][0]["warnung"] and "einziger Beleg" in b2["werte"][0]["hinweis"]
    assert not b1["werte"][0].get("info")


def test_gleiche_kasse_person_betrag_faellt_weg_mit_spur():
    from scripts.export_extension import _verwaiste_markieren

    formulare = [{"formular": "Versicherungsprämien", "bloecke": [
        {"titel": "KK-A · kind_1", "aussteller": "KK-A", "person": "kind_1",
         "werte": [_praemie("1'234.50", "kk-a neu.pdf"),
                   _praemie("1'234.50", "kk-a alt.pdf")]}]}]
    _verwaiste_markieren(formulare, {"kk-a neu.pdf"})
    (b,) = formulare[0]["bloecke"]
    assert [w["quelle"] for w in b["werte"]] == ["kk-a neu.pdf"]
    assert "deckt auch Durchsicht-Wert aus kk-a alt.pdf ab" in b["werte"][0]["info"]


def test_ohne_aussteller_faellt_nichts_weg():
    from scripts.export_extension import _verwaiste_markieren

    formulare = [{"formular": "Versicherungsprämien", "bloecke": [
        {"titel": "x", "aussteller": "", "person": "kind_1",
         "werte": [_praemie("1'234.50", "neu.pdf"), _praemie("1'234.50", "alt.pdf")]}]}]
    _verwaiste_markieren(formulare, {"neu.pdf"})
    assert len(formulare[0]["bloecke"][0]["werte"]) == 2


# --- B5: Prämien bestätigen, nie „regel" -----------------------------------

def _formulare_kk(betrag, herkunft="modell", quelle="k.pdf"):
    return [{"formular": "Versicherungsprämien", "bloecke": [{"werte": [
        {"bezeichnung": "Prämie Total", "betrag": betrag, "quelle": quelle,
         "herkunft": herkunft}]}]}]


def test_monatspraemie_bestaetigt_keinen_modellwert(monkeypatch):
    from extractors import eintraege
    from scripts import export_extension as ex

    t = tab("k.pdf", zeile(100, "Prämie pro Monat", "321.00"))
    monkeypatch.setattr(eintraege, "tabellen_fuer", lambda s, typen: {"k.pdf": t})
    f = _formulare_kk("321.00")
    ex._bestaetigen(Path("."), f)
    assert f[0]["bloecke"][0]["werte"][0]["herkunft"] == "modell"


def test_praemienzeile_macht_modellwert_nur_modell_bestaetigt(monkeypatch):
    from extractors import eintraege
    from scripts import export_extension as ex

    z = zeile(100, "Total Prämie", "3'852.00")
    monkeypatch.setattr(eintraege, "tabellen_fuer",
                        lambda s, typen: {"k.pdf": tab("k.pdf", z)})
    f = _formulare_kk("3'852.00")
    ex._bestaetigen(Path("."), f)
    w = f[0]["bloecke"][0]["werte"][0]
    assert w["herkunft"] == "modell_bestaetigt"
    assert w["anker"] == z.anker()
    # Ein Wert der Durchsicht bleibt, was er ist.
    d = _formulare_kk("3'852.00", herkunft="ablage")
    ex._bestaetigen(Path("."), d)
    assert d[0]["bloecke"][0]["werte"][0]["herkunft"] == "ablage"
    assert not d[0]["bloecke"][0]["werte"][0].get("warnung")


def test_saeule3a_regel_und_berechnet_mit_anker(monkeypatch):
    from extractors import eintraege
    from scripts import export_extension as ex

    def f21(total):
        return tab("3a.pdf",
                   zeile(10, "Form. 21 EDP"),
                   zeile(20, "2.Beiträge für anerkannte Formen der gebundenen Selbstvorsorge (Säule 3a)"),
                   zeile(30, "111.222.33", "2019", "2050", "3'000.00"),
                   zeile(40, "q Total Beiträge an die Säule 3a", *([total] if total else [])))

    def formulare():
        return [{"formular": "Bescheinigung der Vorsorgeeinrichtung", "bloecke": [{"werte": [
            {"bezeichnung": "Einzahlung Säule 3a", "betrag": "3'000.00", "quelle": "3a.pdf",
             "herkunft": "modell", "jahr": "2025"}]}]}]

    for total, herkunft in (("3'000.00", "regel"), (None, "berechnet")):
        monkeypatch.setattr(eintraege, "tabellen_fuer",
                            lambda s, typen, t=f21(total): {"3a.pdf": t})
        f = formulare()
        ex._bestaetigen(Path("."), f)
        w = f[0]["bloecke"][0]["werte"][0]
        assert w["herkunft"] == herkunft, total
        assert w["anker"] and w["anker"]["seite"] == 1


# --- B5/B6: Anker und Herkunft der festen Leser im Export ------------------

def test_alv_berechnet_und_kontrollzeilen_ohne_knopf(monkeypatch):
    from extractors import alv, eintraege
    from scripts import export_extension as ex

    dok = "Arbeitslosenkasse - Bescheinigung - Muster 2025.pdf"
    monkeypatch.setattr(eintraege, "tabellen_fuer",
                        lambda s, typen: {dok: tab(dok, zeile(10, "Arbeitslosenkasse"))})
    ohne = alv.Bescheinigung("2025", {"taggelder": Decimal("10000"),
                                      "familienzulagen": Decimal("500"),
                                      "ahv": Decimal("600")}, anker=ANKER)
    monkeypatch.setattr(alv, "lese", lambda t, jahr=None: ohne)
    (b,) = ex._alv_bloecke(Path("."), lambda r: r)
    steuerbar, *kontrolle = b["werte"]
    assert steuerbar["herkunft"] == "berechnet" and steuerbar["anker"] == ANKER
    assert steuerbar["betrag"] == "9'900.00"
    assert kontrolle and all(
        k["herkunft"] == "beleg" and k["gerechnet"] is False and k["kontrolle"] is True
        and k["einsetzen"] == "" and k["anker"] == ANKER for k in kontrolle)

    mit = alv.Bescheinigung("2025", {"taggelder": Decimal("10000"),
                                     "steuerbar": Decimal("9400")}, anker=ANKER)
    monkeypatch.setattr(alv, "lese", lambda t, jahr=None: mit)
    (b,) = ex._alv_bloecke(Path("."), lambda r: r)
    assert b["werte"][0]["herkunft"] == "regel" and b["werte"][0]["anker"] == ANKER


def test_kinder_und_verbilligung_tragen_anker(monkeypatch):
    from extractors import eintraege
    from scripts import export_extension as ex

    kz = zeile(100, "Kind Eins", "01.01.2019", "1'000.50")
    kita = "Kita-A - Ausweis - Muster 2025.pdf"
    monkeypatch.setattr(eintraege, "tabellen_fuer", lambda s, typen: {kita: tab(kita, kz)})
    (b,) = ex._kinder_bloecke(Path("."), lambda r: r)
    assert b["werte"][0]["anker"] == kz.anker()

    vz = zeile(110, "Prämienverbilligung", "-111.10")
    kk = "KK-A - Praemien - Muster 2025.pdf"
    monkeypatch.setattr(eintraege, "tabellen_fuer",
                        lambda s, typen: {kk: tab(kk, zeile(100, "Prämie KVG", "1'000.00"), vz)})
    bloecke = [{"werte": [{"bezeichnung": "Prämie KVG", "betrag": "1'000.00", "quelle": kk}]}]
    ex._verbilligung_ergaenzen(Path("."), bloecke)
    v = bloecke[0]["werte"][-1]
    assert v["betrag"] == "111.10" and v["anker"] == vz.anker()


def test_tabellenzeilen_tragen_ihren_anker(monkeypatch, tmp_path):
    from extractors import eintraege
    from scripts import build_review_html, etax_daten
    from scripts import export_extension as ex

    dok = "KK-A - Praemien - Muster 2025.pdf"
    (tmp_path / "_results_full.json").write_text(
        json.dumps([{"pdf_name": dok, "belegtyp": "kk_praemienbescheinigung", "fields": []}]),
        encoding="utf-8")
    basis = {"pdf_name": dok, "zielwert": "Prämie Grundversicherung KVG", "ziffer": "15",
             "aussteller": "KK-A", "person": "kind_1", "herkunft": "regel"}
    zeilen = [dict(basis, betrag="1'000.00", seite=2, snippet="Prämie KVG | 1'000.00"),
              dict(basis, betrag="1'100.00", seite=2, snippet="x", ohne_nachweis=True),
              dict(basis, betrag="1'200.00", anker={"seite": 3, "snippet": "y"})]
    monkeypatch.setattr(etax_daten, "_zeilen_der_tabelle", lambda s: zeilen)
    monkeypatch.setattr(build_review_html, "personennamen", lambda: {})
    monkeypatch.setattr(eintraege, "alle_eintraege", lambda *a, **k: ([], []))
    monkeypatch.setattr(eintraege, "tabellen_fuer", lambda s, typen: {})
    daten = ex.baue(tmp_path)
    (form,) = [f for f in daten["formulare"] if f["formular"] == "Versicherungsprämien"]
    anker = {w["betrag"]: w["anker"] for b in form["bloecke"] for w in b["werte"]}
    assert anker["1'000.00"] == {"seite": 2, "snippet": "Prämie KVG | 1'000.00"}
    assert anker["1'100.00"] is None
    assert anker["1'200.00"] == {"seite": 3, "snippet": "y"}


# --- bilanz: „regel" ohne Anker ist nicht gesichert -------------------------

def test_bilanz_zaehlt_regel_nur_mit_anker(monkeypatch):
    from scripts import pflichtfelder_check as pc

    leiste = {"formulare": [{"formular": "Versicherungsprämien", "bloecke": [{"werte": [
        {"bezeichnung": "ohne Anker", "betrag": "1.00", "quelle": "a.pdf", "herkunft": "regel"},
        {"bezeichnung": "mit Anker", "betrag": "2.00", "quelle": "a.pdf", "herkunft": "regel",
         "anker": ANKER},
        {"bezeichnung": "bestätigt", "betrag": "3.00", "quelle": "a.pdf",
         "herkunft": "modell_bestaetigt", "anker": ANKER},
        {"bezeichnung": "Kontrolle", "betrag": "4.00", "quelle": "a.pdf", "herkunft": "beleg",
         "kontrolle": True, "einsetzen": ""}]}]}]}
    monkeypatch.setattr(pc, "_leiste", lambda s: leiste)
    (b,) = pc.bilanz(Path("."))
    assert [x.split(": ")[1] for x in b["gesichert"]] == ["mit Anker"]
    assert [x.split(": ")[1] for x in b["modell"]] == ["ohne Anker", "bestätigt"]
    assert "warnung" not in b and "fehlt" not in b
