"""Die Brücke zum eSteuerauszug darf nichts verlieren.

Der Barcode-Weg spart das Abtippen: ZHprivateTax importiert ein PDF nach
eCH-0196 und füllt das Wertschriftenverzeichnis selbst. Was dabei nicht in die
Eingabe kommt, fehlt am Ende in der Steuererklärung — ohne dass es jemand
sieht.

Zweimal ist in dieser einen Funktion dasselbe passiert: drei Hypotheken wurden
zu zwei. Erst, weil nach der *Unterscheidung* gruppiert wurde und zwei keinen
Zusatz trugen. Dann, weil nach der *Positionsnummer* gruppiert wurde und zwei
dieselbe hatten. Beide Male lautlos (260923-dua).
"""
from __future__ import annotations

import pytest

from extractors.ablage import oeffne, pfad_fuer
from scripts.etax_daten import baue


@pytest.fixture()
def samples(tmp_path):
    ordner = tmp_path / "json"
    ordner.mkdir()
    return ordner


def lege(db, kennung, dokument, zielwert, betrag, aussteller,
         pos=1, unterscheidung="", jahr="2025"):
    db.execute(
        "INSERT INTO position (id, dokument, zielwert, pos, betrag, "
        "aussteller, unterscheidung, jahr, status) "
        "VALUES (?,?,?,?,?,?,?,?, 'geprueft')",
        (kennung, dokument, zielwert, pos, betrag, aussteller,
         unterscheidung, jahr))


def test_drei_hypotheken_bleiben_drei(samples):
    """Auch wenn zwei dieselbe Positionsnummer tragen und keinen Zusatz."""
    db = oeffne(pfad_fuer(samples))
    # Schuld und Zins stehen in ZWEI Dokumenten — so kommen sie von der Bank.
    for i, (pos, betrag) in enumerate([(1, "100000"), (2, "200000"),
                                       (2, "300000")], start=1):
        lege(db, f"h{i}", "saldo.pdf", "Hypothekarschuld 31.12.", betrag,
             "BANK-A", pos=pos)
    for i, (pos, betrag) in enumerate([(1, "1000"), (2, "2000"),
                                       (3, "3000")], start=1):
        lege(db, f"z{i}", "zins.pdf", "Schuldzinsen Hypothek", betrag,
             "BANK-A", pos=pos)
    db.commit()

    daten = baue(samples)
    schulden = [s for h in daten["institute"] for s in h["schulden"]]
    assert len(schulden) == 3, "eine Hypothek ist verschwunden"
    assert sorted(s["schuld"] for s in schulden) == \
        ["100000", "200000", "300000"]
    assert sorted(s["zins"] for s in schulden) == ["1000", "2000", "3000"]


def test_schuld_und_zins_aus_zwei_dokumenten_paaren(samples):
    """Sie stehen selten im selben Dokument — trotzdem gehören sie zusammen."""
    db = oeffne(pfad_fuer(samples))
    lege(db, "h1", "saldo.pdf", "Hypothekarschuld 31.12.", "500000", "BANK-A")
    lege(db, "z1", "zins.pdf", "Schuldzinsen Hypothek", "4200", "BANK-A")
    db.commit()

    schulden = [s for h in baue(samples)["institute"] for s in h["schulden"]]
    assert len(schulden) == 1
    assert schulden[0]["schuld"] == "500000"
    assert schulden[0]["zins"] == "4200"


def test_ein_ueberhang_bleibt_sichtbar(samples):
    """Mehr Zinsen als Hypotheken: der Rest verschwindet nicht, er fällt auf."""
    db = oeffne(pfad_fuer(samples))
    lege(db, "h1", "saldo.pdf", "Hypothekarschuld 31.12.", "500000", "BANK-A")
    lege(db, "z1", "zins.pdf", "Schuldzinsen Hypothek", "4200", "BANK-A")
    lege(db, "z2", "zins.pdf", "Schuldzinsen Hypothek", "900", "BANK-A", pos=2)
    db.commit()

    daten = baue(samples)
    schulden = [s for h in daten["institute"] for s in h["schulden"]]
    assert len(schulden) == 2
    assert daten["unvollstaendige_schulden"] == 1


def test_ohne_iban_ist_ein_dokument_ein_konto(samples):
    """Zwei Dokumente ohne IBAN bleiben zwei Konten — und das ist richtig so.

    Frueher wurden sie ueber eine Ziffernfolge im Dateinamen zusammengefasst.
    Die kann eine Kontonummer sein, genauso gut ein Datum: dann verschmolzen
    zwei fremde Konten zu einem, und die Ertraege stimmten nicht mehr. Ein
    Konto zu viel sieht man; ein verschmolzenes nicht (260924-dua).

    Zusammengefasst wird nur ueber die IBAN — sie steht im Beleg und ist
    eindeutig.
    """
    db = oeffne(pfad_fuer(samples))
    lege(db, "k1", "Zinsabrechnung_123456_2025.pdf", "Saldo 31.12.",
         "4756.50", "BANK-A")
    lege(db, "k2", "TAX_P_123456_2025.pdf",
         "Bruttoertrag ohne Verrechnungssteuer", "12.40", "BANK-A")
    db.commit()

    konten = [k for h in baue(samples)["institute"] for k in h["konten"]]
    assert len(konten) == 2, "ohne IBAN wird nichts zusammengefasst"
    assert {k["saldo"] for k in konten} == {"4756.50", ""}


def test_zwei_schreibweisen_desselben_hauses_fallen_auf(samples):
    """Sonst entstehen zwei Auszüge für eine Bank — und nichts paart."""
    db = oeffne(pfad_fuer(samples))
    lege(db, "k1", "a.pdf", "Saldo 31.12.", "100", "Muster Bank")
    lege(db, "k2", "b.pdf", "Saldo 31.12.", "200", "Muster Bank AG")
    db.commit()

    daten = baue(samples)
    assert daten["gleiche_institute"], "die Ähnlichkeit wurde nicht bemerkt"


def test_ohne_aussteller_kein_auszug(samples):
    """Ein eSteuerauszug ohne Absender gibt es nicht — das wird gezählt."""
    db = oeffne(pfad_fuer(samples))
    lege(db, "k1", "a.pdf", "Saldo 31.12.", "100", "")
    db.commit()

    daten = baue(samples)
    assert daten["ohne_institut"] == 1
    assert daten["institute"] == []


def test_der_zinssatz_verraet_eine_falsche_paarung(samples):
    """Welcher Zins zu welcher Hypothek gehört, steht nicht in den Daten.

    Gepaart wird der Reihe nach — eine falsche Zuordnung fällt aber sofort
    auf, weil sie einen unmöglichen Zinssatz ergibt. Die Wegleitung verlangt
    den Satz ohnehin im Schuldenverzeichnis (S. 21).
    """
    db = oeffne(pfad_fuer(samples))
    lege(db, "h1", "beleg.pdf", "Hypothekarschuld 31.12.", "400000", "BANK-A")
    lege(db, "z1", "beleg.pdf", "Schuldzinsen Hypothek", "40000", "BANK-A")
    db.commit()

    daten = baue(samples)
    assert daten["unplausible_zinssaetze"] == 1, "10 % waere zu melden"


def test_plausible_saetze_geben_keinen_befund(samples):
    db = oeffne(pfad_fuer(samples))
    lege(db, "h1", "beleg.pdf", "Hypothekarschuld 31.12.", "400000", "BANK-A")
    lege(db, "z1", "beleg.pdf", "Schuldzinsen Hypothek", "3600", "BANK-A")
    db.commit()

    daten = baue(samples)
    assert daten["unplausible_zinssaetze"] == 0
    schuld = daten["institute"][0]["schulden"][0]
    assert 0.8 < schuld["zinssatz"] < 1.0
