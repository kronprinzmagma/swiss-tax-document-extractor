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
    assert eingabe(Decimal("12164.86")) == "12164.86"


def test_saldo_und_zins_desselben_kontos_in_einem_eintrag():
    """Zwei Dokumente, dieselbe IBAN: ein Eintrag mit Saldo und Ertrag."""
    z = [zeile("Saldo 31.12.", "12'000.50", "saldo.pdf"),
         zeile("Bruttoertrag ohne Verrechnungssteuer", "3.20", "zins.pdf")]
    (e,) = konten(z, {"saldo.pdf": IBAN, "zins.pdf": IBAN})
    assert feld(e, "Konto-/Depot-Nr.").wert == IBAN
    assert feld(e, "Total Guthaben").einsetzen == "12000.50"
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
    assert feld(a, "Schuld am").einsetzen == "400000.00"
    assert feld(a, "Schuldzinsen").einsetzen == "2880.00"
    assert feld(a, "Zinssatz").wert == "0.72 %"
    assert feld(b, "Schuldzinsen").einsetzen == "3450.00"
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
