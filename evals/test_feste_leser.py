"""Feste Leser für Depot, Fremdbetreuung, Prämienverbilligung, ALV und
Konten (261024-jga). Alle Werte erfunden; Aufbau nach den Formularen.
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from extractors import (alv, bankobjekte, depot, kinderbetreuung,
                        praemienverbilligung, rohtabelle)
from extractors.belegordner import erlaubte_typen, ordner_von, typ_aus_ordner


def zeile(y, *zellen, seite=1, x=50.0, abstand=100.0, xs=None):
    cs = []
    for i, text in enumerate(zellen):
        x0 = xs[i] if xs else x + i * abstand
        cs.append(rohtabelle.Zelle(text, x0, x0 + 40))
    return rohtabelle.Zeile(seite, y, cs[0].x0, cs, (cs[0].x0, y - 4, cs[-1].x1, y + 4))


def tab(*zeilen):
    return rohtabelle.Rohtabelle("t.pdf", list(zeilen))


# --- Depot -----------------------------------------------------------------

def test_depot_konsolidierte_uebersicht_trennt_a_und_b():
    t = tab(zeile(100, "Total CHF", "A* CHF", "B* CHF", x=200),
            zeile(110, "Bank, Kunde nr. 12345", "Steuerwert", "10.500", "2.000", "8.500"),
            zeile(120, "Ertrag", "300", "100", "200"))
    d = depot.lese(t)
    assert (d.steuerwert, d.ertrag_a, d.ertrag_b) == (10500, 100, 200)
    assert d.kennung_art == "kunde"  # keine IBAN im Dokument


def test_depot_wv_uebersicht_mit_vst_gegenprobe():
    t = tab(zeile(100, "Steuerwert der", "Bruttoertrag", "Bruttoertrag"),
            zeile(110, "12'000(1)", "40", "300", "14"),
            zeile(120, "(1) Davon A 2'000 und B 10'000"),
            zeile(130, "Depot 123456789"))
    d = depot.lese(t)
    assert (d.steuerwert, d.ertrag_a, d.ertrag_b, d.vst) == (12000, 40, 300, 14)
    assert d.kennung == "123456789" and d.kennung_art == "depot"
    assert d.befunde == []


def test_depot_vst_passt_nicht_wird_befund():
    t = tab(zeile(100, "Steuerwert der", "Bruttoertrag"),
            zeile(110, "12'000", "40", "300", "30"))
    assert any("35 %" in b for b in depot.lese(t).befunde)


def test_portfolio_uebersicht_sagt_kein_ertrag():
    t = tab(zeile(100, "Kontonummer", "555666"),
            zeile(200, "Gesamtvermögen in CHF", "9'999.90"))
    d = depot.lese(t)
    assert d.form == "portfolio" and d.steuerwert == Decimal("9999.90")
    assert d.ertrag_a is None and d.befunde


# --- Fremdbetreuung ---------------------------------------------------------

def test_kita_je_kind_und_total():
    t = tab(zeile(100, "Kind Eins", "01.01.2019", "1'000.50"),
            zeile(110, "Kind Zwei", "01.01.2021", "500.25"),
            zeile(130, "Total", "1'500, 75"))
    a = kinderbetreuung.lese(t)
    assert [(k.name, k.betrag) for k in a.kinder] == [
        ("Kind Eins", Decimal("1000.50")), ("Kind Zwei", Decimal("500.25"))]
    assert a.total == Decimal("1500.75") and a.befunde == []


# --- Prämienverbilligung ----------------------------------------------------

def test_praemienverbilligung_und_rechnungstotal():
    t = tab(zeile(100, "Prämien KVG", "1'000.00"),
            zeile(110, "Prämienverbilligung", "-200.00"),
            zeile(120, "Totalin Rechnunggestellte Prämien", "800.00"))
    v = praemienverbilligung.lese(t)
    assert v.betrag == Decimal("200.00") and v.in_rechnung == Decimal("800.00")
    assert praemienverbilligung.lese(tab(zeile(100, "Prämien", "1.00"))) is None


# --- ALV -------------------------------------------------------------------

def test_alv_spalten_ueber_lage_und_gegenprobe():
    kopf_x = [42, 75, 135, 188, 255, 316]
    t = tab(zeile(100, "Jahr", "Taggelder", "Familienzulage", "Beiträge AHV/IV/EO",
                  "Prämien NBUV", "Beitrag berufl. Vorsorge Steuerbarer Betrag", xs=kopf_x),
            zeile(110, "Année", "Indemnités journalières | Allocation familiale",
                  "Cotisations AVS", "Primes AANP", "prévoyance prof", "Montant imposable",
                  xs=[34, 75, 188, 255, 316, 376]),
            zeile(120, "anno", "Indennità giornaliere", "Assegno familiare",
                  "Contributi AVS", "Premi AINP", "Previdenza prof Importo imponibile",
                  xs=kopf_x),
            zeile(130, "2025", "10,000", "500", "200", "10", "9,290",
                  xs=[36, 84, 214, 276, 357, 391]))
    b = alv.lese(t)
    assert b.werte == {"taggelder": 10000, "ahv": 500, "nbuv": 200, "bvg": 10,
                       "steuerbar": 9290}
    assert b.befunde == []


# --- Konten: Spalte, VSt-Zeile, kein Zins ----------------------------------

def _konto(*zeilen):
    (o,) = bankobjekte.objekte([("k.pdf", tab(*zeilen))])
    return o


def test_zins_aus_spalte_habenzins():
    o = _konto(zeile(100, "Zinsabrechnung vom 01.01.2025 bis 31.12.2025"),
               zeile(110, "Datum", "Habenzins", "Sollzins", "Total CHF"),
               zeile(120, "Total", "3.30", "0.00", "3.30"),
               zeile(130, "Kontostand", "1'234.00"))
    assert bankobjekte.einwert(o.werte["zins"])[0] == Decimal("3.30")


def test_zins_aus_zeile_ohne_verrechnungssteuer():
    o = _konto(zeile(100, "Zinsausweis 2025"),
               zeile(110, "ohne Verrechnungssteuer", "12.34"),
               zeile(120, "Buchsaldo 31.12.2025", "5'000.00"))
    assert bankobjekte.einwert(o.werte["zins"])[0] == Decimal("12.34")


def test_bescheinigung_ohne_zinszeile_heisst_null():
    o = _konto(zeile(100, "Zins- und Kapitalbescheinigung 01.01.2025 - 31.12.2025"),
               zeile(110, "Guthabensaldo am 31.12.2025", "700.00"))
    zins = o.werte["zins"][0]
    assert zins.wert == 0 and zins.herkunft == bankobjekte.KEIN_ZINS_AUSGEWIESEN


# --- Ordner bestimmt den Typ -----------------------------------------------

def test_ordner_sagt_den_typ(tmp_path):
    assert ordner_von(tmp_path / "Konten/a.pdf", tmp_path) == "Konten"
    assert ordner_von(tmp_path / "a.pdf", tmp_path) == ""
    assert erlaubte_typen("Einkünfte") == ("lohnausweis", "unknown")
    assert typ_aus_ordner("Konten", "wertschriftenverzeichnis") == "bank_zinsausweis"
    assert typ_aus_ordner("Konten", "hypothek_zinsbestaetigung") == "hypothek_zinsbestaetigung"
    assert typ_aus_ordner("", "saeule_3a") == "saeule_3a"


def test_leiste_drei_hypotheken_je_mit_eigenem_zins():
    """Vorher: eine Schuld und der Zins einer anderen Hypothek."""
    from evals.test_hypotheken import _iban, zinsabrechnung
    from extractors.eintraege import schulden_aus_kette

    z = []
    for seite, (nr, bban, kapital, zins) in enumerate(
            [("11 1.111.111.11", "00000111122223333", "120'000.00", "720.00"),
             ("22 2.222.222.22", "00000444455556666", "120'000.00", "750.00"),
             ("33 3.333.333.33", "00000777788889999", "80'000.00", "900.00")], start=1):
        satz = "1.2000" if seite == 1 else ("1.2500" if seite == 2 else "2.2500")
        z += zinsabrechnung(nr, _iban(bban), kapital, [(180, satz, zins)], zins, seite=seite)
    tabelle = rohtabelle.Rohtabelle("h.pdf", z)
    objekte = bankobjekte.objekte([("h.pdf", tabelle)])
    eintraege, gedeckt = schulden_aus_kette(objekte, [], {"h.pdf": tabelle})
    paare = sorted((e.felder[2].einsetzen, e.felder[3].einsetzen) for e in eintraege)
    assert paare == [("120'000.00", "720.00"), ("120'000.00", "750.00"),
                     ("80'000.00", "900.00")]
    assert gedeckt == {"h.pdf"}


def test_saeule3a_formular21_hinter_begleitbrief_und_tabelle():
    from extractors import saeule3a

    f21 = tab(zeile(10, "Sehr geehrter Herr", seite=1),
              zeile(10, "Form. 21 EDP", seite=2),
              zeile(20, "2.Beiträge für anerkannte Formen der gebundenen Selbstvorsorge (Säule 3a)", seite=2),
              zeile(30, "111.222.33", "2019", "2050", "3'000", seite=2),
              zeile(40, "q Total Beiträge an die Säule 3a", seite=2))
    assert saeule3a.lese(f21) == (Decimal("3000"), [])
    tabelle = tab(zeile(10, "Geburtsdatum", "Eröffnungsjahr", "Jahr", "Beiträge in CHF"),
                  zeile(20, "01.01.1900", "2020", "2025", "1'234.50"))
    assert saeule3a.lese(tabelle, "2025") == (Decimal("1234.50"), [])
    zu_viel = tab(zeile(10, "Jahr", "Beiträge in CHF"), zeile(20, "2025", "8'000.00"))
    assert saeule3a.lese(zu_viel)[1]


def test_schulden_aus_durchsicht_werden_abgeglichen_nicht_verdoppelt():
    from extractors.eintraege import Eintrag, Feld, _abgleichen

    def schuld(dok, s, z):
        return Eintrag("Schuld", "B", "elternteil_1", [dok], [
            Feld("Name, Vorname und Adresse des Gläubigers", "Musterbank, Weg 1"),
            Feld("Zinssatz", ""), Feld("Schuld am 31.12.", s), Feld("Schuldzinsen", z)])

    kette = [schuld("abrechnung.pdf", "100'000.00", "500.00")]
    zeilen = [schuld("ausweis.pdf", "100'000.00", "500.00"),      # gleich → weg
              schuld("zinsausweis.pdf", "100'000.00", "450.00"),  # anderer Zins → Hinweis
              schuld("alt.pdf", "50'000.00", "300.00")]           # unbekannt → bleibt
    bleiben = _abgleichen(kette, zeilen)
    assert [e.dokumente for e in bleiben] == [["alt.pdf"]]
    assert bleiben[0].felder[2].warnung
    zins = kette[0].felder[3]
    assert "450.00" in zins.hinweis and zins.warnung


def test_dokumentnamen_zerlegt_und_zusammengesetzt_sind_gleich():
    import unicodedata

    from extractors.eintraege import _zeilen_von, dok_name

    zerlegt = unicodedata.normalize("NFD", "Bank - Vermögensausweis - X.pdf")
    zeilen = [{"pdf_name": zerlegt, "betrag": "1.00"}]
    assert _zeilen_von(zeilen, dok_name("Bank - Vermögensausweis - X.pdf")) == zeilen


def test_portfolio_ohne_liquiditaet_wenn_konto_da():
    from extractors.eintraege import Eintrag, Feld, _liquiditaet_gegenprobe

    t = tab(zeile(100, "Kontonummer", "555666"),
            zeile(150, "Total Liquidität", "1'000.00 10.00"),
            zeile(200, "Gesamtvermögen in CHF", "9'000.00"))
    d = depot.lese(t)
    assert (d.gesamt, d.liquiditaet, d.steuerwert) == (9000, 1000, 8000)

    def depot_eintrag():
        e = Eintrag("Steuerauszug/Depot", "D", "elternteil_1", ["p.pdf"],
                    [Feld("Total Steuerwert", "8'000.00", "8'000.00", "beleg", "")])
        e.liquiditaet, e.text = Decimal("1000"), "bankdienstleistungen von musterbank ag"
        return e

    konto = Eintrag("Bankkonto", "K", "elternteil_1", ["k.pdf"],
                    [Feld("Bank, Gesellschaft", "Musterbank AG"),
                     Feld("Total Guthaben (Steuerwert 31.12.)", "1'000.00")])
    mit = depot_eintrag()
    _liquiditaet_gegenprobe([mit], [konto])
    assert mit.felder[0].wert == "8'000.00" and not mit.felder[0].warnung
    ohne = depot_eintrag()
    _liquiditaet_gegenprobe([ohne], [])
    assert ohne.felder[0].wert == "9'000.00" and "inkl. Kontokorrent" in ohne.felder[0].hinweis
    # Konto derselben Bank, aber anderer Kontostand: kein 1:1 → bleibt im Depot.
    anders = depot_eintrag()
    konto2 = Eintrag("Bankkonto", "K", "elternteil_1", ["k.pdf"],
                     [Feld("Bank, Gesellschaft", "Musterbank AG"),
                      Feld("Total Guthaben (Steuerwert 31.12.)", "500.00")])
    _liquiditaet_gegenprobe([anders], [konto2])
    assert anders.felder[0].wert == "9'000.00"


def test_alter_zielwert_auch_mit_zerlegtem_umlaut(monkeypatch, tmp_path):
    import unicodedata

    from scripts import build_review_html, etax_daten

    zeile_ = {"beleg": "k.pdf", "belegtyp": "bank_zinsausweis",
              "zielwert": unicodedata.normalize("NFD", "Vermögensstand 31.12.")}
    monkeypatch.setattr(build_review_html, "sammle_zeilen", lambda *a, **k: [zeile_])
    (z,) = etax_daten._zeilen_der_tabelle(tmp_path)
    assert z["zielwert"] == "Saldo 31.12."


def test_depot_kennung_faellt_auf_gueltige_iban_zurueck():
    from evals.test_hypotheken import _iban

    iban = _iban("00000123412341234")
    t = tab(zeile(100, "Kunde nr. 4711"),
            zeile(110, "Anlagekonto", iban),
            zeile(200, "Gesamtvermögen in CHF", "1'000.00"))
    d = depot.lese(t)
    assert (d.kennung, d.kennung_art) == (iban, "iban")
