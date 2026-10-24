"""Hypotheken und Scan-Seiten (261024-jga). Alle Werte erfunden.

Nachgebildet nach dem Aufbau echter Zinsabrechnungen einer Regionalbank:
Festhypothek mit Nummer und IBAN im Kopf, „Saldo am …" negativ geführt,
Zinsrechnung als Tabelle (Kapital, Tage, Satz, Betrag), „Rechnungsbetrag"
als Zins. Und ein Schuldenausweis mit mehreren Hypotheken auf einer Seite.
"""
from __future__ import annotations

from decimal import Decimal

from extractors import bankobjekte, rohtabelle
from scripts.pdf_to_word_json import bessere_seiten


def _iban(bban: str, land: str = "CH") -> str:
    zahl = int("".join(str(int(c, 36)) for c in bban + land + "00"))
    return f"{land}{98 - zahl % 97:02d}{bban}"


def zeile(y, *zellen, seite=1):
    x, cs = 50.0, []
    for text in zellen:
        cs.append(rohtabelle.Zelle(text, x, x + 60))
        x += 100
    return rohtabelle.Zeile(seite, y, 50, cs, (50, y - 4, x, y + 4))


def zinsabrechnung(nr, iban, kapital, zeilen_, total, seite=1):
    z = [zeile(100, "Herr", "Festhypothek", f"{nr} / CHF", seite=seite),
         zeile(130, "8000 Musterhausen", "IBAN", iban, seite=seite),
         zeile(200, "Saldo am 24.12.2025", "CHF", f"-{kapital}", seite=seite),
         zeile(260, "Zinsabrechnung vom 01.07.2025 bis 31.12.2025", seite=seite)]
    y = 300
    for tage, satz, betrag in zeilen_:
        z.append(zeile(y, "27.06.2025", f"-{kapital}", str(tage), satz,
                       f"-{kapital}", f"-{betrag}", seite=seite))
        y += 12
    z.append(zeile(y + 10, "Rechnungsbetrag", "31.12.2025", f"-{total}", seite=seite))
    return z


def test_festhypothek_schuld_zins_satz_und_nachrechnung():
    iban = _iban("00000111122223333")
    tab = rohtabelle.Rohtabelle("h.pdf", zinsabrechnung(
        "11 1.111.111.11", iban, "120'000.00",
        [(30, "1.2000", "120.00"), (150, "1.3000", "650.00")], "770.00"))
    (o,) = bankobjekte.objekte([("h.pdf", tab)])
    assert o.art == "Hypothek"
    assert bankobjekte.einwert(o.werte["schuld"])[0] == Decimal("120000.00")
    assert bankobjekte.einwert(o.werte["zins"])[0] == Decimal("770.00")
    assert [w.wert for w in o.werte["zinssatz"]] == ["1.2 %", "1.3 %"]
    assert all(w.wert for w in o.werte["_kontrolle"])
    assert o.werte["_summe_stimmt"][0].wert is True
    assert bankobjekte.einwert(o.werte["periode"])[0] == "01.07.2025 – 31.12.2025"


def test_falsch_gelesener_zins_faellt_bei_der_nachrechnung_auf():
    """Ein Apostroph als 7 gelesen: 650.00 wird zu 7650.00."""
    tab = rohtabelle.Rohtabelle("h.pdf", zinsabrechnung(
        "11 1.111.111.11", _iban("00000111122223333"), "120'000.00",
        [(150, "1.3000", "7650.00")], "7650.00"))
    (o,) = bankobjekte.objekte([("h.pdf", tab)])
    assert not all(w.wert for w in o.werte["_kontrolle"])


def test_jede_seite_eine_hypothek_ueber_ihre_nummer():
    z = (zinsabrechnung("11 1.111.111.11", _iban("00000111122223333"),
                        "120'000.00", [(180, "1.2000", "720.00")], "720.00", seite=1)
         + zinsabrechnung("22 2.222.222.22", _iban("00000444455556666"),
                          "80'000.00", [(180, "2.0000", "800.00")], "800.00", seite=2))
    obs = bankobjekte.objekte([("h.pdf", rohtabelle.Rohtabelle("h.pdf", z))])
    assert sorted(bankobjekte.einwert(o.werte["schuld"])[0] for o in obs) == [
        Decimal("80000.00"), Decimal("120000.00")]


def test_schuldenausweis_mehrere_hypotheken_auf_einer_seite():
    z = []
    y = 100
    for nr, betrag, satz in (("11 1.111.111.11", "120'000.00", "1.200"),
                             ("22 2.222.222.22", "80'000.00", "2.000")):
        z += [zeile(y, f"Hypothek Nr. {nr}"),
              zeile(y + 12, "CHF", betrag, f"Festhypothek Nr. {nr} / Laufzeit"),
              zeile(y + 24, "Schuld per 24.05.2025"),
              zeile(y + 36, f"Zinssatz {satz} %"),
              zeile(y + 48, "CHF", "500.00", "Marchzins 01.01.2025 - 24.05.2025")]
        y += 100
    obs = bankobjekte.objekte([("s.pdf", rohtabelle.Rohtabelle("s.pdf", z))])
    assert len(obs) == 2
    assert {bankobjekte.einwert(o.werte["schuld"])[0] for o in obs} == {
        Decimal("120000.00"), Decimal("80000.00")}


def test_schuldenausweis_und_abrechnung_finden_sich_ueber_die_nummer():
    """Ohne IBAN (Ausweis) und mit IBAN (Abrechnung): dasselbe Objekt; der
    jüngere Stichtag gilt."""
    ausweis = [zeile(100, "Hypothek Nr. 11 1.111.111.11"),
               zeile(112, "CHF", "125'000.00", "Festhypothek Nr. 11 1.111.111.11"),
               zeile(124, "Schuld per 24.05.2025"),
               zeile(200, "Hypothek Nr. 22 2.222.222.22"),
               zeile(212, "CHF", "80'000.00", "Festhypothek Nr. 22 2.222.222.22"),
               zeile(224, "Schuld per 24.05.2025")]
    abrechnung = zinsabrechnung("11 1.111.111.11", _iban("00000111122223333"),
                                "120'000.00", [(180, "1.2000", "720.00")], "720.00")
    obs = bankobjekte.objekte([("s.pdf", rohtabelle.Rohtabelle("s.pdf", ausweis)),
                               ("h.pdf", rohtabelle.Rohtabelle("h.pdf", abrechnung))])
    eins = next(o for o in obs if "1.111" in str(o.werte["nummer"][0].wert))
    assert len(eins.quellen) == 2
    assert bankobjekte.einwert(eins.werte["schuld"])[0] == Decimal("120000.00")


def test_ocr_apostroph_als_gradzeichen():
    assert rohtabelle.betrag_in("-100°000.00") == Decimal("-100000.00")


def test_scanseite_zeilenweise_textebene_plus_ocr():
    """Briefkopf aus der Textebene, die graue Zeile aus dem OCR."""
    def w(text, x, y):
        return {"text": text, "x0": x, "top": y, "x1": x + 40, "bottom": y + 8}
    original = [{"page_num": 1, "words": [
        w("Hauptstrasse", 340, 16), w("12,", 390, 16),
        w("8000", 340, 25), w("Musterhausen", 370, 25),
        w("Habenzins", 100, 396)]}]
    ocr = [{"page_num": 1, "words": [
        w("Fo", 345, 18), w("se", 360, 18), w("&", 75, 27),
        w("Habenzins", 100, 396), w("12.30", 500, 396),
        w("Buchsaldo", 100, 418), w("1'234.50", 500, 418)]}]
    seiten, ersetzt = bessere_seiten(original, ocr, [True])
    texte = {x["text"] for x in seiten[0]["words"]}
    assert {"Hauptstrasse", "Musterhausen", "12.30", "1'234.50"} <= texte
    assert "Fo" not in texte and ersetzt == [1]
