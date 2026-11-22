"""Feste Leser lesen Spalte, Jahr und Ziffer statt Lage und Reihenfolge (261025-kme).
Alle Werte erfunden.

Ein Leser, der von seinem Layout abweicht, meldet einen Befund — er gibt
weder still einen falschen Wert mit gültigem Anker noch still nichts, wenn
der Beleg einen Wert hat, den er nur nicht lesen konnte.
"""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from pathlib import Path

from extractors import (alv, bankobjekte, depot, eintraege, kinderbetreuung,
                        rohtabelle, saeule3a)
from extractors.layout_lohnausweis import extract_lohnausweis_layout


def zeile(y, *zellen, seite=1, x=50.0, abstand=100.0, xs=None):
    cs = []
    for i, text in enumerate(zellen):
        x0 = xs[i] if xs else x + i * abstand
        cs.append(rohtabelle.Zelle(text, x0, x0 + 40))
    return rohtabelle.Zeile(seite, y, cs[0].x0, cs, (cs[0].x0, y - 4, cs[-1].x1, y + 4))


def tab(*zeilen, dok="t.pdf"):
    return rohtabelle.Rohtabelle(dok, list(zeilen))


ALV_DOK = "Arbeitslosenkasse - Bescheinigung - Muster 2025.pdf"


# --- ALV (B1a) ----------------------------------------------------------------

def _alv_verschmolzen():
    kopf_x = [42, 75, 135, 188, 255, 316]
    return tab(
        zeile(100, "Jahr", "Taggelder", "Familienzulage", "Beiträge AHV/IV/EO",
              "Prämien NBUV", "Beitrag berufl. Vorsorge Steuerbarer Betrag", xs=kopf_x),
        zeile(110, "Année", "Indemnités journalières", "Allocation familiale",
              "Cotisations AVS", "Primes AANP", "prévoyance prof Montant imposable", xs=kopf_x),
        zeile(120, "anno", "Indennità giornaliere", "Assegno familiare",
              "Contributi AVS", "Premi AINP", "Previdenza prof Importo imponibile", xs=kopf_x),
        zeile(130, "2025", "10,000", "500", "200", "10", "300", "9,990",
              xs=[36, 84, 140, 214, 276, 330, 391]), dok=ALV_DOK)


def test_alv_verschmolzener_kopf_ist_befund():
    b = alv.lese(_alv_verschmolzen())
    assert any("Spalte steuerbar nicht lesbar" in x for x in b.befunde)
    assert "steuerbar" not in b.werte
    assert b.werte["bvg"] == Decimal("300")          # der linke Wert
    assert any("zwei Werte unter Spalte bvg" in x for x in b.befunde)


def test_alv_verschmolzen_im_export_berechnet_mit_hinweis(monkeypatch):
    from scripts import export_extension as ex

    monkeypatch.setattr(eintraege, "tabellen_fuer",
                        lambda s, typen: {ALV_DOK: _alv_verschmolzen()})
    (blk,) = ex._alv_bloecke(Path("."), lambda r: r)
    w = blk["werte"][0]
    assert w["herkunft"] == "berechnet"
    assert "Spalte steuerbar nicht lesbar" in w["hinweis"]
    assert w["warnung"] is True


def _alv_zwei_jahre():
    xs = [42, 84, 160]
    return tab(zeile(100, "Jahr", "Taggelder", "Steuerbarer Betrag", xs=xs),
               zeile(110, "2024", "8,000", "8,000", xs=xs),
               zeile(120, "2025", "10,000", "10,000", xs=xs), dok=ALV_DOK)


def test_alv_waehlt_die_zeile_des_steuerjahrs():
    b = alv.lese(_alv_zwei_jahre(), jahr="2025")
    assert b.jahr == "2025"
    assert b.werte == {"taggelder": Decimal("10000"), "steuerbar": Decimal("10000")}
    assert b.befunde == []


def test_alv_ohne_steuerjahr_bei_zwei_zeilen_kein_wert():
    b = alv.lese(_alv_zwei_jahre())
    assert b.werte == {}
    assert any("mehrere Jahreszeilen" in x and "2024" in x for x in b.befunde)
    assert b.anker is not None
    b = alv.lese(_alv_zwei_jahre(), jahr="2023")
    assert b.werte == {}
    assert any("keine Zeile für das Steuerjahr 2023" in x for x in b.befunde)


def test_alv_ohne_wert_im_export_ein_leerer_wert_mit_warnung(monkeypatch):
    from scripts import export_extension as ex

    monkeypatch.setattr(eintraege, "tabellen_fuer",
                        lambda s, typen: {ALV_DOK: _alv_zwei_jahre()})
    (blk,) = ex._alv_bloecke(Path("."), lambda r: r)
    (w,) = blk["werte"]                      # keine Kontrollzeilen
    assert w["betrag"] == "" and w["einsetzen"] == ""
    assert w["warnung"] is True and "mehrere Jahreszeilen" in w["hinweis"]
    (blk,) = ex._alv_bloecke(Path("."), lambda r: r, jahr="2025")
    assert blk["werte"][0]["betrag"] == "10'000.00"


def test_alv_doppelt_belegte_spalte_ist_befund():
    t = tab(zeile(100, "Jahr", "Taggelder", xs=[42, 75]),
            zeile(110, "2025", "10,000", "500", xs=[36, 84, 140]))
    b = alv.lese(t)
    assert b.werte["taggelder"] == Decimal("10000")
    assert any("zwei Werte unter Spalte taggelder" in x for x in b.befunde)


# --- Steuerjahr des Laufs (B1a/B1b, BS-10) ----------------------------------

def _ergebnis(tmp_path, *jahre):
    daten = [{"pdf_name": f"d{i}.pdf", "status": "ok",
              "fields": [{"feld": "jahr", "value": j}]} for i, j in enumerate(jahre)]
    daten.append({"pdf_name": "x.pdf", "status": "extract_failed",
                  "fields": [{"feld": "jahr", "value": "2019"}] * 5})
    p = tmp_path / "_results_full.json"
    p.write_text(json.dumps(daten), encoding="utf-8")
    return p


def test_steuerjahr_mehrheit_gleichstand_fehlt(tmp_path):
    _ergebnis(tmp_path, "2025", "2025", "2024")
    assert eintraege.steuerjahr_des_laufs(tmp_path) == "2025"
    _ergebnis(tmp_path, "2025", "2024")
    assert eintraege.steuerjahr_des_laufs(tmp_path) is None
    assert eintraege.steuerjahr_des_laufs(tmp_path / "fehlt") is None


def test_steuerjahr_liest_bestand_ohne_aenderung(tmp_path):
    """BS-10: das Steuerjahr wird gelesen, die Datei bleibt byte-gleich."""
    p = _ergebnis(tmp_path, "2025", "2025")
    vorher = hashlib.sha256(p.read_bytes()).hexdigest()
    assert eintraege.steuerjahr_des_laufs(tmp_path) == "2025"
    assert hashlib.sha256(p.read_bytes()).hexdigest() == vorher
    # Aufrufe ohne Jahr bleiben möglich.
    assert alv.lese(_alv_zwei_jahre()) is not None
    assert saeule3a.lese(tab(zeile(10, "Brief"))).wert is None


# --- Säule 3a (B1b) -----------------------------------------------------------

def test_saeule3a_ganze_beitragstabelle():
    zeilen = [zeile(10, "Jahr", "Beiträge in CHF")]
    for k, jahr in enumerate(range(2018, 2026)):
        zeilen.append(zeile(20 + 10 * k, str(jahr), f"{1000 + k}.00"))
    e = saeule3a.lese(tab(*zeilen), "2025")
    assert e.wert == Decimal("1007.00") and e.herkunft == "dokument"


def test_saeule3a_beitragsspalte_ueber_den_kopf():
    t = tab(zeile(10, "Jahr", "Beiträge in CHF", "Guthaben 31.12."),
            zeile(20, "2025", "6'000.00", "6'500.00"))
    assert saeule3a.lese(t, "2025").wert == Decimal("6000.00")


def test_saeule3a_ohne_zeile_des_jahres_ist_befund():
    kopf = zeile(10, "Jahr", "Beiträge in CHF")
    e = saeule3a.lese(tab(kopf, zeile(20, "2024", "6'000.00")), "2025")
    assert e.wert is None
    assert any("keine Zeile für das Steuerjahr 2025" in b for b in e.befunde)
    assert e.anker == kopf.anker()


def test_saeule3a_zwei_jahreszeilen_ohne_jahr_ist_befund():
    t = tab(zeile(10, "Jahr", "Beiträge in CHF"),
            zeile(20, "2024", "6'000.00"), zeile(30, "2025", "6'100.00"))
    e = saeule3a.lese(t)
    assert e.wert is None and any("mehrere Jahreszeilen" in b for b in e.befunde)


def test_saeule3a_hoechstbetrag_aus_dem_datenmodell(monkeypatch):
    from extractors import datenmodell

    monkeypatch.setattr(datenmodell, "saeule_3a_maximum", lambda: Decimal("5000"))
    t = tab(zeile(10, "Jahr", "Beiträge in CHF"), zeile(20, "2025", "6'000.00"))
    e = saeule3a.lese(t, "2025")
    assert e.wert == Decimal("6000.00")
    assert any("über dem Höchstbetrag" in b and "5000" in b for b in e.befunde)


def test_saeule3a_form21_betrag_vor_chf():
    f21 = tab(zeile(10, "Form. 21 EDP"),
              zeile(20, "2.Beiträge für anerkannte Formen der gebundenen Selbstvorsorge (Säule 3a)"),
              zeile(30, "111.222.33", "2019", "2050", "3'000.00", "CHF"),
              zeile(40, "q Total Beiträge an die Säule 3a", "3'000.00", "CHF"))
    e = saeule3a.lese(f21)
    assert (e.wert, e.herkunft) == (Decimal("3000.00"), "dokument")


def test_bestaetigen_markiert_modellwert_bei_unlesbarem_formular(monkeypatch):
    from scripts import export_extension as ex

    dok = "Stiftung-A - 3a - Muster 2025.pdf"
    t = tab(zeile(10, "Jahr", "Beiträge in CHF"),
            zeile(20, "2024", "6'000.00"), zeile(30, "2025", "6'100.00"), dok=dok)
    monkeypatch.setattr(eintraege, "tabellen_fuer", lambda s, typen: {dok: t})
    w = {"herkunft": "modell", "betrag": "6'100.00", "quelle": dok, "jahr": ""}
    f = [{"formular": "Bescheinigung der Vorsorgeeinrichtung (3a)", "bloecke": [{"werte": [w]}]}]
    ex._bestaetigen(Path("."), f)
    assert w["warnung"] is True and "Formular nicht lesbar" in w["hinweis"]
    assert "mehrere Jahreszeilen" in w["hinweis"]
    assert w["herkunft"] == "modell"
    # Mit Steuerjahr liest die Regel die Zeile und bestätigt.
    w2 = {"herkunft": "modell", "betrag": "6'100.00", "quelle": dok, "jahr": ""}
    f2 = [{"formular": "Bescheinigung der Vorsorgeeinrichtung (3a)", "bloecke": [{"werte": [w2]}]}]
    ex._bestaetigen(Path("."), f2, jahr="2025")
    assert w2["herkunft"] == "regel" and not w2.get("warnung")


# --- Depot (B1c) --------------------------------------------------------------

def test_depot_stichtagsspalte_ueber_den_kopf():
    t = tab(zeile(100, "Vermögen", "31.12.2024", "31.12.2025", "Veränderung"),
            zeile(110, "Gesamtvermögen", "8'000.00", "9'000.00", "1'000.00"))
    d = depot.lese(t)
    assert d.steuerwert == Decimal("9000.00") and d.warnungen == []
    assert "9'000.00" in d.anker["steuerwert"]["snippet"]


def test_depot_ohne_kopf_kein_wert_mit_warnung():
    t = tab(zeile(110, "Gesamtvermögen", "8'000.00", "9'000.00", "1'000.00"))
    d = depot.lese(t)
    assert d.steuerwert is None and d.gesamt is None
    assert any("Spalte des Stichtags nicht eindeutig" in w for w in d.warnungen)
    assert all(w in d.befunde for w in d.warnungen)


def test_depot_gegenprobe_liquiditaet_groesser():
    t = tab(zeile(150, "Total Liquidität", "12'000.00"),
            zeile(200, "Gesamtvermögen in CHF", "9'000.00"))
    d = depot.lese(t)
    assert d.steuerwert is None
    assert any("Gegenprobe" in w for w in d.warnungen)


def test_depot_ohne_form_meldet_es():
    d = depot.lese(tab(zeile(100, "Depotauszug"), zeile(110, "Irgendwas", "1'000.00")))
    assert d.form == ""
    assert any("keine bekannte Form" in b for b in d.befunde)
    assert any("keine bekannte Form" in w for w in d.warnungen)


def test_depot_warnung_steht_am_total_steuerwert():
    t = tab(zeile(150, "Total Liquidität", "12'000.00"),
            zeile(200, "Gesamtvermögen in CHF", "9'000.00"))
    (e,) = eintraege.depots_aus_dokumenten({"p.pdf": t}, [], {})
    sw = next(f for f in e.felder if f.titel == "Total Steuerwert")
    assert "Gegenprobe" in sw.hinweis and sw.warnung is True
    (e,) = eintraege.depots_aus_dokumenten(
        {"q.pdf": tab(zeile(100, "Depotauszug"))}, [], {})
    sw = next(f for f in e.felder if f.titel == "Total Steuerwert")
    assert "keine bekannte Form" in sw.hinweis and sw.warnung is True


# --- Kinderbetreuung (B1d) ----------------------------------------------------

def test_kita_rechnungszeile_ist_kein_kind():
    a = kinderbetreuung.lese(tab(zeile(100, "Rechnung Nr. 17", "15.01.2026", "1'000.50"),
                                 zeile(110, "Total", "1'000.50")))
    assert a.kinder == []
    assert any("1 Zeile mit Datum und Betrag ist kein Kind" in b for b in a.befunde)
    assert not any("Rechnung" in b for b in a.befunde)


def test_kita_ohne_total_ist_befund():
    a = kinderbetreuung.lese(tab(zeile(100, "Kind Eins", "01.01.2019", "1'000.50"),
                                 zeile(110, "Kind Zwei", "01.01.2021", "500.25")))
    assert len(a.kinder) == 2
    assert "Total fehlt, Summe nicht gegengeprüft" in a.befunde


def test_kita_anonymisierter_name_ist_ein_kind():
    a = kinderbetreuung.lese(tab(zeile(100, "Person3 Muster", "01.01.2020", "500.00"),
                                 zeile(110, "Total", "500.00")))
    assert [(k.name, k.betrag) for k in a.kinder] == [("Person3 Muster", Decimal("500.00"))]
    assert a.befunde == []


def test_kita_befund_setzt_warnung_im_export(monkeypatch):
    from scripts import export_extension as ex

    kita = "Kita-A - Ausweis - Muster 2025.pdf"
    t = tab(zeile(100, "Kind Eins", "01.01.2019", "1'000.50"), dok=kita)
    monkeypatch.setattr(eintraege, "tabellen_fuer", lambda s, typen: {kita: t})
    (b,) = ex._kinder_bloecke(Path("."), lambda r: r)
    assert b["werte"][0]["warnung"] is True


# --- Lohnausweis (B1f) --------------------------------------------------------

BREITE, HOEHE = 595.0, 842.0


def _w(text, top, x0):
    return {"text": text, "x0": x0, "top": top, "x1": x0 + 6 * len(text),
            "bottom": top + 10.0, "page": 1}


def _lohnzeilen(*zeilen):
    """(Beschriftung, Betrag, top) → Wortboxen; Beschriftung links, Betrag rechts."""
    words = []
    for beschriftung, betrag, top in zeilen:
        x = 60.0
        for teil in beschriftung.split():
            words.append(_w(teil, top, x))
            x += 6 * len(teil) + 4
        words.append(_w(betrag, top, 541.0))
    return words


def test_lohnausweis_10_2_ohne_10_1_ist_kein_bvg_beitrag():
    r = extract_lohnausweis_layout(_lohnzeilen(
        ("8. Bruttolohn total", "75600", 470.0),
        ("9. Beiträge AHV/IV/EO/ALV/NBUV", "4008", 484.0),
        ("10.2 Beiträge für den Einkauf", "3100", 502.0),
        ("11. Nettolohn", "68492", 533.0)), BREITE, HOEHE)
    assert r["ahv_alv_nbu_abzug_pos9"]["value"] == "4008"
    assert r["bvg_abzug_pos10a"] is None
    assert r["bvg_einkauf_pos10b"]["value"] == "3100"
    assert r["plausibel"] is True and r["abzuege_zuordnung"] == "ziffer"


def test_lohnausweis_10_1_und_10_2_je_nach_ziffer():
    r = extract_lohnausweis_layout(_lohnzeilen(
        ("8. Bruttolohn total", "75600", 470.0),
        ("9. Beiträge AHV/IV/EO/ALV/NBUV", "4008", 484.0),
        ("10.1 Ordentliche Beiträge", "2100", 496.0),
        ("10.2 Beiträge für den Einkauf", "1000", 508.0),
        ("11. Nettolohn", "68492", 533.0)), BREITE, HOEHE)
    assert r["ahv_alv_nbu_abzug_pos9"]["value"] == "4008"
    assert r["bvg_abzug_pos10a"]["value"] == "2100"
    assert r["bvg_einkauf_pos10b"]["value"] == "1000"
    assert r["plausibel"] is True and r["befunde"] == []


def test_lohnausweis_unleserliche_9_nach_formregel():
    r = extract_lohnausweis_layout(_lohnzeilen(
        ("8. Bruttolohn total", "75600", 470.0),
        ("°. BE:", "4008", 484.0),
        ("10.1 Ordentliche Beiträge", "3100", 502.0),
        ("11. Nettolohn", "68492", 533.0)), BREITE, HOEHE)
    assert r["ahv_alv_nbu_abzug_pos9"]["value"] == "4008"
    assert r["bvg_abzug_pos10a"]["value"] == "3100"
    assert r["abzuege_zuordnung"] == "ziffer"


def test_lohnausweis_keine_ziffer_lesbar_ist_unklar():
    r = extract_lohnausweis_layout(_lohnzeilen(
        ("8. Bruttolohn total", "75600", 470.0),
        ("°. BE:", "4008", 484.0),
        ("xx yy", "3100", 502.0),
        ("11. Nettolohn", "68492", 533.0)), BREITE, HOEHE)
    assert r["ahv_alv_nbu_abzug_pos9"] is None and r["bvg_abzug_pos10a"] is None
    assert r["abzuege_zuordnung"] == "unklar"
    assert any("Abzug ohne lesbare Ziffer" in b for b in r["befunde"])


def test_abzuege_aus_layout():
    from scripts.process_samples_full import _abzuege_aus_layout

    feld9 = {"value": "4008"}
    feld10 = {"value": "3100"}
    ziffer = {"plausibel": True, "abzuege_zuordnung": "ziffer",
              "ahv_alv_nbu_abzug_pos9": feld9, "bvg_abzug_pos10a": None,
              "bvg_einkauf_pos10b": feld10}
    assert _abzuege_aus_layout(ziffer) == {"ahv_alv_nbu_abzug_pos9": feld9,
                                           "bvg_abzug_pos10a": None}
    reihenfolge = {"plausibel": True, "abzuege_zuordnung": "reihenfolge",
                   "ahv_alv_nbu_abzug_pos9": feld9, "bvg_abzug_pos10a": feld10}
    assert _abzuege_aus_layout(reihenfolge) == {"ahv_alv_nbu_abzug_pos9": feld9,
                                                "bvg_abzug_pos10a": feld10}
    assert _abzuege_aus_layout({**reihenfolge, "bvg_abzug_pos10a": None}) == {
        "ahv_alv_nbu_abzug_pos9": feld9}
    assert _abzuege_aus_layout({**ziffer, "plausibel": False}) == {}
    assert _abzuege_aus_layout({**ziffer, "abzuege_zuordnung": "unklar"}) == {}


# --- Bankobjekte (B3) ---------------------------------------------------------

def _iban_ch() -> str:
    """Gültige CH-IBAN aus einer erfundenen BBAN, zur Laufzeit gebildet."""
    from evals.test_hypotheken import _iban
    return _iban("00000123412341234")


IBAN_CH = _iban_ch()


def test_buchung_hypothekarzins_macht_keine_hypothek():
    (o,) = bankobjekte.objekte([("k.pdf", tab(
        zeile(100, "Privatkonto"),
        zeile(110, f"IBAN {IBAN_CH}"),
        zeile(120, "Datum", "Buchungstext", "Belastung", "Saldo"),
        zeile(130, "15.06.2025", "Hypothekarzins", "500.00", "9'500.00"),
        zeile(140, "Saldo per 31.12.2025", "9'500.00")))])
    assert o.art == "Konto"
    assert bankobjekte.einwert(o.werte["saldo"])[0] == Decimal("9500.00")


def test_hypothekenseite_mit_nummer_bleibt_hypothek():
    (o,) = bankobjekte.objekte([("h.pdf", tab(
        zeile(100, "Festhypothek Nr. 12 3.456.789.01"),
        zeile(110, "Saldo am 31.12.2025", "-100'000.00")))])
    assert o.art == "Hypothek"
    assert bankobjekte.einwert(o.werte["schuld"])[0] == Decimal("100000.00")


def test_kontoseite_mit_werbung_fuer_hypothek_bleibt_konto():
    (o,) = bankobjekte.objekte([("k.pdf", tab(
        zeile(100, "Ihre Hypothek bei uns"),
        zeile(110, "Privatkonto"),
        zeile(120, f"IBAN {IBAN_CH}"),
        zeile(130, "Saldo per 31.12.2025", "1'000.00")))])
    assert o.art == "Konto"


# --- Staat (B6) ---------------------------------------------------------------

def _staat_aus_konten(iban):
    zeilen = [{"zielwert": eintraege.SALDO[0], "pdf_name": "k.pdf",
               "aussteller": "Musterbank", "person": "elternteil_1", "betrag": "1'000.00"}]
    (e,) = eintraege.konten(zeilen, {"k.pdf": iban})
    return next(f for f in e.felder if f.titel == "Staat")


def test_staat_folgt_dem_iban_praefix():
    f = _staat_aus_konten("DE89370400440532013000")
    assert (f.wert, f.herkunft, f.warnung) == ("DE", "berechnet", True)
    assert "Ausland — Maske prüfen" in f.hinweis
    f = _staat_aus_konten(IBAN_CH.replace(" ", ""))
    assert (f.wert, f.herkunft, f.warnung) == ("CH", "berechnet", False)


def test_staat_ohne_iban_ist_annahme():
    t = tab(zeile(100, "Privatkonto"), zeile(110, "Konto-Nr. 123-456"),
            zeile(120, "Saldo per 31.12.2025", "1'000.00"), dok="k.pdf")
    objekte = bankobjekte.objekte([("k.pdf", t)])
    (e,), _ = eintraege.konten_aus_kette(objekte, [], {"k.pdf": t})
    f = next(f for f in e.felder if f.titel == "Staat")
    assert e.felder[1].wert == "123-456"
    assert (f.wert, f.herkunft) == ("CH", "annahme")


# --- ALV auf einem OCR-Scan (Nachbesserung 261025-kme) ----------------------------
#
# Auf einem verrauschten Scan hängen Rauschwörter in den Zellen der Jahreszeile
# („48,393 Eu", „1,195 DEE 39", „44 EIER , 595"). Die Zeilenbildung hält sie
# jetzt in der Zeile; der Leser darf deshalb nicht an ihnen scheitern. Gelesen
# wird mit Rauschwörtern entfernt; eine Zelle mit zwei Zahlen gehört den Spalten,
# die in ihr beginnen, und die Gegenprobe entscheidet, ob die Zuordnung gilt.
# Werte erfunden.

def _alv_scan(jahreszeile):
    kopf_x = [42, 75, 135, 188, 255, 316, 376]
    return tab(
        zeile(100, "Jahr", "Taggelder", "Familienzulage", "Beiträge AHV/IV/EO",
              "Prämien NBUV", "Beitrag berufl. Vorsorge", "Steuerbarer Betrag", xs=kopf_x),
        jahreszeile, dok=ALV_DOK)


def _zelle(text, x0, x1):
    return rohtabelle.Zelle(text, x0, x1)


def _jahreszeile(*zellen):
    return rohtabelle.Zeile(1, 110, zellen[0].x0, list(zellen),
                            (zellen[0].x0, 106, zellen[-1].x1, 114))


def test_alv_scan_mit_rauschwoertern_in_den_zellen():
    z = _jahreszeile(_zelle("2025", 36, 60), _zelle("20 , 000 Eu", 84, 185),
                     _zelle("1 , 000", 214, 246), _zelle("500 DEE 40", 276, 365),
                     _zelle("18 EIER , 460", 391, 431))
    b = alv.lese(_alv_scan(z))
    assert b.werte == {"taggelder": 20000, "ahv": 1000, "nbuv": 500, "bvg": 40,
                       "steuerbar": 18460}
    assert b.befunde == []


def test_alv_scan_zelle_mit_zwei_zahlen_nur_bei_stimmender_gegenprobe():
    # Die Zuordnung 500 → NBUV, 40 → BVG geht auf. Stimmt der steuerbare Betrag
    # nicht, bleibt der Befund stehen, statt einen falschen Wert zu verschweigen.
    z = _jahreszeile(_zelle("2025", 36, 60), _zelle("20 , 000 Eu", 84, 185),
                     _zelle("1 , 000", 214, 246), _zelle("500 DEE 40", 276, 365),
                     _zelle("17 EIER , 000", 391, 431))
    b = alv.lese(_alv_scan(z))
    assert any("≠ steuerbar" in x for x in b.befunde)


def test_alv_zelle_mit_unklarer_zahlenzahl_ist_befund_nicht_wert():
    # Drei Zahlen in einer Zelle, aber nur eine Spalte beginnt darin.
    z = _jahreszeile(_zelle("2025", 36, 60), _zelle("20,000", 84, 124),
                     _zelle("1,000", 214, 246), _zelle("500 DEE 7 EIER 40", 276, 300),
                     _zelle("18,460", 391, 431))
    b = alv.lese(_alv_scan(z))
    assert "nbuv" not in b.werte
    assert any("nicht eindeutig" in x for x in b.befunde)
