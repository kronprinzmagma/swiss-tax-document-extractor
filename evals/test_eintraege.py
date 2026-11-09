"""Einträge wie ZHprivateTax sie abfragt (261024-jga).

Massstab: `FELDLISTE-ZHPRIVATETAX-2025.md`. Alle Werte erfunden.
"""
from __future__ import annotations

from decimal import Decimal

from extractors.eintraege import anzeige, betrag, eingabe, konten, schulden


def zeile(zielwert, betrag_, dok, pos=1, bank="BANK-A", person="elternteil_1",
          u="", typ="bank_zinsausweis"):
    return {"zielwert": zielwert, "betrag": betrag_, "pdf_name": dok,
            "pos": pos, "aussteller": bank, "person": person,
            "unterscheidung": u, "belegtyp": typ}


def feld(eintrag, titel):
    return next(f for f in eintrag.felder if f.titel.startswith(titel))


IBAN = "CH9300762011623852957"


def test_betraege_werden_gelesen_nicht_durchgereicht():
    assert betrag("21.083") == Decimal("21083")      # Punkt als Tausender
    assert betrag("1'365.--") == Decimal("1365")     # ganze Franken
    assert anzeige(Decimal("21083")) == "21'083.00"
    assert eingabe(Decimal("12164.86")) == "12'164.86"


def test_saldo_und_zins_desselben_kontos_in_einem_eintrag():
    """Zwei Dokumente, dieselbe IBAN: ein Eintrag mit Saldo und Ertrag."""
    z = [zeile("Saldo 31.12.", "12'000.50", "saldo.pdf"),
         zeile("Bruttoertrag ohne Verrechnungssteuer", "3.20", "zins.pdf")]
    (e,) = konten(z, {"saldo.pdf": IBAN, "zins.pdf": IBAN})
    assert feld(e, "Konto-/Depot-Nr.").wert == IBAN
    assert feld(e, "Total Guthaben").einsetzen == "12'000.50"
    assert feld(e, "Ertrag ohne").einsetzen == "3.20"


def test_ohne_iban_wird_nicht_zusammengelegt():
    """Eine Ziffernfolge im Dateinamen ist keine Kontoidentität."""
    z = [zeile("Saldo 31.12.", "100", "Kontoauszug_123456.pdf"),
         zeile("Steuerwert 31.12.", "200", "Depot_123456.pdf",
               typ="wertschriftenverzeichnis")]
    eintraege = konten(z, {}, lambda d: "123456")
    assert len(eintraege) == 2
    assert {e.maske for e in eintraege} == {"Bankkonto",
                                            "Steuerauszug/Depot"}
    nr = feld(eintraege[0], "Konto-/Depot-Nr.")
    assert nr.herkunft == "annahme" and nr.warnung


def test_datum_im_dateinamen_ist_keine_kontonummer():
    (e,) = konten([zeile("Saldo 31.12.", "5", "Scan 20240623.pdf")], {},
                  lambda d: "20240623")
    assert feld(e, "Konto-/Depot-Nr.").herkunft == "fehlt"


def test_mehrere_konten_in_einem_dokument():
    z = [zeile("Saldo 31.12.", "1'775.21", "neon.pdf", pos=1),
         zeile("Saldo 31.12.", "506.05", "neon.pdf", pos=2, u="Konto 2")]
    assert len(konten(z, {})) == 2


def test_fehlender_ertrag_ist_keine_warnung():
    (e,) = konten([zeile("Saldo 31.12.", "5", "a.pdf")], {})
    assert not feld(e, "Ertrag ohne").warnung


def test_widerspruechliche_dokumente_bekommen_keinen_knopf():
    z = [zeile("Saldo 31.12.", "100", "a.pdf"),
         zeile("Saldo 31.12.", "110", "b.pdf")]
    (e,) = konten(z, {"a.pdf": IBAN, "b.pdf": IBAN})
    f = feld(e, "Total Guthaben")
    assert f.herkunft == "widerspruch" and f.einsetzen == "" and f.warnung


def test_schuld_und_zins_gepaart_mit_satz_und_luecken():
    z = [zeile("Hypothekarschuld 31.12.", "400'000", "h.pdf", 1, "BANK-H",
               "familie", "Festhypothek A"),
         zeile("Hypothekarschuld 31.12.", "250000", "h.pdf", 2, "BANK-H",
               "familie", "Festhypothek B"),
         zeile("Schuldzinsen Hypothek", "3450", "h.pdf", 1, "BANK-H",
               "familie", "Festhypothek B"),
         zeile("Schuldzinsen Hypothek", "2'880", "h.pdf", 2, "BANK-H",
               "familie", "Festhypothek A")]
    a, b = schulden(z)
    # Über die Unterscheidung gepaart, nicht über die Positionsnummer.
    assert feld(a, "Schuld am").einsetzen == "400'000.00"
    assert feld(a, "Schuldzinsen").einsetzen == "2'880.00"
    assert feld(a, "Zinssatz").wert == "0.72 %"
    assert feld(b, "Schuldzinsen").einsetzen == "3'450.00"
    # Die Adresse fehlt immer — und das wird gesagt.
    assert feld(a, "Name, Vorname und Adresse").warnung


def test_zuordnung_der_reihe_nach_wird_gekennzeichnet():
    z = [zeile("Hypothekarschuld 31.12.", "100000", "h.pdf", 1, "BANK-H"),
         zeile("Hypothekarschuld 31.12.", "200000", "h.pdf", 2, "BANK-H"),
         zeile("Schuldzinsen Hypothek", "900", "h.pdf", 1, "BANK-H"),
         zeile("Schuldzinsen Hypothek", "1800", "h.pdf", 2, "BANK-H")]
    for e in schulden(z):
        assert feld(e, "Schuld am").warnung
        assert "Reihe nach" in feld(e, "Schuldzinsen").hinweis


# ---------------------------------------------------------------------------
# 261025-hbv: Durchsicht geht vor (B3), nichts still gestrichen (B4),
# Anker aus der Kette (B5). Seitenobjekte direkt gebaut, Werte erfunden.

ANKER = {"seite": 1, "bbox": [50, 96, 300, 104], "snippet": "Saldo | 1'000.00"}


def _wert(w, seite=1):
    from extractors.bankobjekte import Wert
    return Wert(Decimal(w) if isinstance(w, str) else w,
                dict(ANKER, seite=seite), "dokument")


def _konto_obj(saldo, dok="k.pdf", seite=1, kennung=IBAN, zins=None):
    from extractors.bankobjekte import Seitenobjekt, Wert
    werte = {"saldo": [_wert(saldo, seite)]}
    if zins is not None:
        werte["zins"] = [_wert(zins, seite)]
    return Seitenobjekt("Konto", ("k", kennung), None,
                        Wert(kennung, dict(ANKER, seite=seite), "dokument"),
                        Wert("CHF", dict(ANKER, seite=seite), "dokument"),
                        werte, [(dok, seite)])


def _hypothek_obj(schuld, zins, dok="h.pdf", seite=1, nr="11 1.111.111.11"):
    from extractors.bankobjekte import Seitenobjekt, Wert
    return Seitenobjekt("Hypothek", ("h", nr), None, None,
                        Wert("CHF", None, "dokument"),
                        {"schuld": [_wert(schuld, seite)], "zins": [_wert(zins, seite)],
                         "nummer": [Wert(nr, None, "dokument")],
                         "zinssatz": [Wert("1.2 %", dict(ANKER, seite=seite), "dokument")]},
                        [(dok, seite)])


def _ablage(zielwert, betrag_, dok, u="", herkunft="ablage"):
    return {"pdf_name": dok, "zielwert": zielwert, "betrag": betrag_,
            "herkunft": herkunft, "unterscheidung": u, "aussteller": "Musterbank",
            "person": "elternteil_1"}


def test_hypothek_gepruefter_wert_geht_dem_beleg_vor():
    from extractors.eintraege import SCHULD, SCHULDZINS, schulden_aus_kette

    o = _hypothek_obj("300000.00", "1500.00")
    zeilen = [_ablage(SCHULD, "250000.00", "h.pdf"),
              _ablage(SCHULDZINS, "1400.00", "h.pdf")]
    (e,), _ = schulden_aus_kette([o], zeilen)
    s = feld(e, "Schuld am")
    assert (s.wert, s.herkunft) == ("250'000.00", "ablage")
    assert s.warnung and "Beleg sagt 300'000.00" in s.hinweis
    z = feld(e, "Schuldzinsen")
    assert (z.wert, z.herkunft) == ("1'400.00", "ablage")
    assert z.warnung and "Beleg sagt 1'500.00" in z.hinweis


def test_zwei_konten_gepruefter_saldo_gehoert_zum_gleichen_betrag():
    from extractors.eintraege import konten_aus_kette

    a = _konto_obj("1000.00", seite=1, kennung="CH0000000000000001234")
    b = _konto_obj("2000.00", seite=2, kennung="CH0000000000000005678")
    (ea, eb), _ = konten_aus_kette([a, b], [_ablage("Saldo 31.12.", "2000.00", "k.pdf")])
    assert feld(ea, "Total Guthaben").herkunft == "beleg"
    assert feld(eb, "Total Guthaben").herkunft == "ablage"
    assert not feld(eb, "Total Guthaben").warnung


def test_zwei_konten_gepruefter_saldo_ueber_die_unterscheidung():
    from extractors.eintraege import konten_aus_kette

    a = _konto_obj("1000.00", seite=1, kennung="CH0000000000000001234")
    b = _konto_obj("2000.00", seite=2, kennung="CH0000000000000005678")
    (ea, eb), _ = konten_aus_kette(
        [a, b], [_ablage("Saldo 31.12.", "2100.00", "k.pdf", u="5678")])
    assert feld(ea, "Total Guthaben").herkunft == "beleg"
    t = feld(eb, "Total Guthaben")
    assert (t.wert, t.herkunft) == ("2'100.00", "ablage")
    assert "Beleg sagt 2'000.00" in t.hinweis


def test_zwei_konten_unzuordenbarer_gepruefter_wert_wird_gemeldet():
    from extractors.eintraege import konten_aus_kette

    a = _konto_obj("1000.00", seite=1, kennung="CH0000000000000001234")
    b = _konto_obj("2000.00", seite=2, kennung="CH0000000000000005678")
    eintraege, _ = konten_aus_kette([a, b], [_ablage("Saldo 31.12.", "3000.00", "k.pdf")])
    for e in eintraege:
        t = feld(e, "Total Guthaben")
        assert t.warnung
        assert "Durchsicht nennt 3'000.00, Zuordnung zum Konto unklar" in t.hinweis


def test_ein_konto_zwei_verschiedene_gepruefte_saldi_ist_widerspruch():
    from extractors.eintraege import konten_aus_kette

    (e,), _ = konten_aus_kette([_konto_obj("1000.00")],
                               [_ablage("Saldo 31.12.", "1000.00", "k.pdf"),
                                _ablage("Saldo 31.12.", "1100.00", "k.pdf")])
    t = feld(e, "Total Guthaben")
    assert t.herkunft == "widerspruch" and t.einsetzen == "" and t.warnung
    assert "1'000.00" in t.wert and "1'100.00" in t.wert


def test_anker_aus_der_kette_am_feld():
    from extractors.eintraege import konten_aus_kette, schulden_aus_kette

    (k,), _ = konten_aus_kette([_konto_obj("1000.00", zins="3.20")], [])
    assert feld(k, "Total Guthaben").anker["seite"] == 1
    assert feld(k, "Total Guthaben").anker["bbox"] == ANKER["bbox"]
    assert feld(k, "Konto-/Depot-Nr.").anker["seite"] == 1
    (h,), _ = schulden_aus_kette([_hypothek_obj("300000.00", "1500.00", seite=2)], [])
    assert feld(h, "Schuld am").anker["seite"] == 2
    assert feld(h, "Schuldzinsen").anker["snippet"] == ANKER["snippet"]
    assert feld(h, "Zinssatz").anker["seite"] == 2
    # Ein abweichender geprüfter Wert zeigt nicht auf die Belegzeile.
    (h2,), _ = schulden_aus_kette([_hypothek_obj("300000.00", "1500.00")],
                                  [_ablage("Hypothekarschuld 31.12.", "250000.00", "h.pdf")])
    assert feld(h2, "Schuld am").anker is None


def _k(dok, saldo, bank="Musterbank", person="elternteil_1"):
    from extractors.eintraege import Eintrag, Feld
    return Eintrag("Bankkonto", "K", person, [dok],
                   [Feld("Bank, Gesellschaft", bank, herkunft="ablage"),
                    Feld("Total Guthaben (Steuerwert 31.12.)", saldo, herkunft="ablage")])


def test_gleicher_saldo_andere_bank_und_person_bleibt():
    from extractors.eintraege import _konten_abgleichen

    belegt = [_k("neu.pdf", "1'000.00", "BANK-A", "elternteil_1")]
    alt = _k("alt.pdf", "1'000.00", "BANK-Z", "kind_1")
    (e,) = _konten_abgleichen(belegt, [alt], {"neu.pdf"})
    assert e is alt and feld(e, "Total").warnung


def test_gleicher_saldo_gleiche_bank_und_person_faellt_mit_spur_weg():
    from extractors.eintraege import _konten_abgleichen

    belegt = [_k("neu.pdf", "1'000.00")]
    assert _konten_abgleichen(belegt, [_k("alt.pdf", "1'000.00")], {"neu.pdf"}) == []
    t = feld(belegt[0], "Total")
    assert "deckt auch Durchsicht-Eintrag aus alt.pdf ab" in t.hinweis
    assert not t.warnung


def test_schuld_ohne_glaeubiger_mit_gleichem_betrag_bleibt():
    from extractors.eintraege import Eintrag, Feld, _abgleichen

    def schuld(dok, g, s, z):
        return Eintrag("Schuld", "B", "elternteil_1", [dok], [
            Feld("Name, Vorname und Adresse des Gläubigers", g),
            Feld("Zinssatz", ""), Feld("Schuld am 31.12.", s), Feld("Schuldzinsen", z)])

    kette = [schuld("abrechnung.pdf", "Musterbank, Weg 1", "100'000.00", "500.00")]
    ohne = schuld("ausweis.pdf", "", "100'000.00", "500.00")
    (e,) = _abgleichen(kette, [ohne])
    assert e is ohne and feld(e, "Schuld am").warnung
    # Gleicher Gläubiger, gleiche Person, gleicher Betrag: dasselbe — mit Spur.
    gleich = schuld("zinsausweis.pdf", "Musterbank, Weg 1", "100'000.00", "500.00")
    assert _abgleichen(kette, [gleich]) == []
    assert "deckt auch Durchsicht-Eintrag aus zinsausweis.pdf ab" in feld(kette[0], "Schuld am").hinweis


# ---------------------------------------------------------------------------
# 261025: Drei Hypotheken in einem Dokument, die Durchsicht nennt einen Zins,
# der zu keiner passt. Vorher hing er als Warnung an allen drei — in der
# Oberfläche sah es aus, als hätten alle denselben Zins. Werte erfunden.

def _drei_hypotheken(nachgerechnet=True):
    from extractors.bankobjekte import Wert
    objekte = [_hypothek_obj("200000.00", "1000.00", nr="11 1.111.111.11"),
               _hypothek_obj("200000.00", "1200.00", nr="22 2.222.222.22"),
               _hypothek_obj("500000.00", "3000.00", nr="33 3.333.333.33")]
    for o in objekte:
        o.werte["_kontrolle"] = [Wert(nachgerechnet)]
        o.werte["_summe_stimmt"] = [Wert(True)]
    return objekte


def _durchsicht_drei_zinsen(zins3="1250.00"):
    from extractors.eintraege import SCHULD, SCHULDZINS
    return [_ablage(SCHULD, "200000.00", "h.pdf", herkunft="mensch"),
            _ablage(SCHULD, "200000.00", "h.pdf", herkunft="mensch"),
            _ablage(SCHULDZINS, "1000.00", "h.pdf", herkunft="mensch"),
            _ablage(SCHULDZINS, "3000.00", "h.pdf", herkunft="mensch"),
            _ablage(SCHULDZINS, zins3, "h.pdf", herkunft="mensch")]


def test_drei_hypotheken_haben_drei_verschiedene_zinsen():
    from extractors.eintraege import schulden_aus_kette

    aus, _ = schulden_aus_kette(_drei_hypotheken(), _durchsicht_drei_zinsen())
    zinsen = [feld(e, "Schuldzinsen").wert for e in aus]
    assert zinsen == ["1'000.00", "1'200.00", "3'000.00"]


def test_uebriger_zins_nur_an_der_hypothek_ohne_eigenen_wert():
    """Nachgerechneter Beleg: er gilt, der übrige Wert wird dort genannt."""
    from extractors.eintraege import schulden_aus_kette

    aus, _ = schulden_aus_kette(_drei_hypotheken(), _durchsicht_drei_zinsen())
    z1, z2, z3 = (feld(e, "Schuldzinsen") for e in aus)
    assert "1'250.00" not in z1.hinweis and "1'250.00" not in z3.hinweis
    assert "1'250.00" in z2.hinweis and "der Beleg gilt" in z2.hinweis
    assert not (z1.warnung or z2.warnung or z3.warnung)


def test_uebriger_zins_ohne_nachrechnung_bleibt_warnung():
    from extractors.eintraege import schulden_aus_kette

    aus, _ = schulden_aus_kette(_drei_hypotheken(nachgerechnet=False),
                                _durchsicht_drei_zinsen())
    z1, z2, z3 = (feld(e, "Schuldzinsen") for e in aus)
    assert z2.warnung and "Zuordnung zum Hypothek unklar" in z2.hinweis
    assert not z1.warnung and not z3.warnung


def test_gleich_grosse_hypotheken_nehmen_beide_gepruefte_schulden():
    """Zwei Hypotheken derselben Höhe sind kein Duplikat (CLAUDE.md)."""
    from extractors.eintraege import schulden_aus_kette

    aus, _ = schulden_aus_kette(_drei_hypotheken(), _durchsicht_drei_zinsen())
    s = [feld(e, "Schuld am") for e in aus]
    assert [x.wert for x in s] == ["200'000.00", "200'000.00", "500'000.00"]
    assert not any(x.warnung for x in s)
