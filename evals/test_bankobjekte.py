"""Kette für Bankdokumente: Rohtabelle → Konten → Maske (261024-jga).

Gegen den anonymisierten Korpus; erwartete Werte von Hand abgelesen.
"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from extractors import bankobjekte, rohtabelle

KORPUS = Path(__file__).resolve().parent / "samples_real_2023"


def lade(teil: str):
    pfad = next(KORPUS.glob(f"*{teil}*.json"), None)
    if pfad is None:
        pytest.skip("Korpus fehlt")
    return bankobjekte.zerlege(rohtabelle.baue(json.loads(pfad.read_text())), "BANK")


def feld(eintrag, titel):
    return next(f for f in eintrag["felder"] if f["titel"].startswith(titel))


def test_betraege_aus_zerteilten_zellen():
    assert rohtabelle.betrag_in("1'775 .21") == Decimal("1775.21")
    assert rohtabelle.betrag_in("2 485.01") == Decimal("2485.01")
    assert rohtabelle.betrag_in("01.01.2023") is None
    assert rohtabelle.betrag_in("1.00%") is None


def test_zwei_konten_zwei_eintraege_und_der_zins_geht_nicht_verloren():
    """Zwei Seiten, zwei Konten — der Zins 6.05 auf Seite 2 fehlte bisher."""
    ue = bankobjekte.uebertragung(lade("neonbank"))
    assert [feld(e, "Total Guthaben")["wert"] for e in ue] == [
        Decimal("1775.21"), Decimal("506.05")]
    assert feld(ue[1], "Ertrag ohne")["wert"] == Decimal("6.05")
    assert feld(ue[0], "Bezeichnung")["wert"] == "BANK-N Konto"
    assert all(e["pflichtluecken"] == 0 for e in ue)


def test_buchungstext_ist_kein_kontokopf():
    """„Sparkonto Plus CH00" in der Buchungstabelle startet kein Konto."""
    pfad = next((Path(__file__).resolve().parent / "samples_real_2022")
                .glob("Kontoauszug 01.12.2022*.json"), None) or next(
        (Path(__file__).resolve().parent / "samples_real_2022")
        .glob("Kontoauszug_01.12.2022*CH00*.json"), None)
    if pfad is None:
        pytest.skip("Korpus fehlt")
    dok = bankobjekte.zerlege(rohtabelle.baue(json.loads(pfad.read_text())), "BANK")
    (e,) = bankobjekte.uebertragung(dok)
    assert feld(e, "Währung")["herkunft"] == "dokument"


def test_tabellenspalte_und_fremdwaehrung_ohne_saldo_bleibt_luecke():
    ue = bankobjekte.uebertragung(lade("Zinsabrechnung"))
    assert [feld(e, "Währung")["wert"] for e in ue] == ["CHF", "USD", "EUR"]
    assert feld(ue[0], "Total Guthaben")["wert"] == Decimal("3162.55")
    assert feld(ue[1], "Total Guthaben")["herkunft"] == "fehlt"


def _iban(bban: str, land: str = "CH") -> str:
    """Gültige IBAN zu einer erfundenen Kontonummer — keine echte im Code."""
    zahl = int("".join(str(int(c, 36)) for c in bban + land + "00"))
    return f"{land}{98 - zahl % 97:02d}{bban}"


def test_iban_pruefsumme():
    gut = _iban("00000123456789012")
    assert bankobjekte.iban_gueltig(gut) is None
    falsch = gut[:-1] + str((int(gut[-1]) + 1) % 10)
    assert "Prüfsumme" in bankobjekte.iban_gueltig(falsch)
    assert "21 Stellen" in bankobjekte.iban_gueltig(gut[:-2])


def test_unrealistischer_zins_faellt_auf():
    """23.79 Zins auf 371.88 Saldo = 6.4 % — Saldo vermutlich falsch gelesen."""
    eintrag = {"felder": [
        {"titel": "Konto-/Depot-Nr.", "wert": "12345678", "herkunft": "dokument"},
        {"titel": "Währung", "wert": "CHF", "herkunft": "dokument"},
        {"titel": "Total Guthaben (Steuerwert 31.12.)",
         "wert": Decimal("371.88"), "herkunft": "dokument"},
        {"titel": "Ertrag ohne Verrechnungssteuer",
         "wert": Decimal("23.79"), "herkunft": "dokument"},
        {"titel": "Ertrag mit Verrechnungssteuer", "wert": None, "herkunft": "leer"},
    ]}
    befunde = bankobjekte.pruefe(eintrag)
    assert any("unrealistisch" in b["grund"] for b in befunde)


def test_kontoart_mit_seitenangabe_ist_ein_titel():
    ue = bankobjekte.uebertragung(lade("TAX_P_CH00_1109872050"))
    assert feld(ue[0], "Bezeichnung")["wert"] == "E-Jugendsparkonto"


def test_bankkonten_seite_baut():
    from scripts.bankkonten_seite import bankkonten_seite

    if not KORPUS.exists():
        pytest.skip("Korpus fehlt")
    seite = bankkonten_seite(KORPUS, mit_pdf=False)
    assert "Bankkonten und Hypotheken — aus den Dokumenten gelesen" in seite
    assert "Widersprüche" in seite


def _objekte(*teile):
    dokumente = []
    for teil in teile:
        pfad = next(KORPUS.glob(f"*{teil}*.json"), None)
        if pfad is None:
            pytest.skip("Korpus fehlt")
        d = json.loads(pfad.read_text())
        dokumente.append((d.get("pdf_name", pfad.name), rohtabelle.baue(d)))
    return bankobjekte.objekte(dokumente)


def test_jede_seite_ein_objekt_maskierte_kennung_verschmilzt_nicht():
    """Zwei Seiten, zwei Konten — maskierte Nummern legen nichts zusammen."""
    obs = _objekte("neonbank")
    salden = sorted(bankobjekte.einwert(o.werte["saldo"])[0] for o in obs)
    assert salden == [Decimal("506.05"), Decimal("1775.21")]


def test_seiten_mit_gleicher_gueltiger_iban_sind_ein_objekt():
    def seite(iban, saldo):
        z = rohtabelle.Zeile(1, 100, 50, [rohtabelle.Zelle(f"IBAN {iban}", 50, 200)],
                             (50, 95, 200, 105))
        s = rohtabelle.Zeile(1, 140, 50, [rohtabelle.Zelle("Saldo per 31.12.2025", 50, 200),
                                          rohtabelle.Zelle(saldo, 400, 450)],
                             (50, 135, 450, 145))
        return rohtabelle.Rohtabelle("x", [z, s])
    iban = _iban("00000123456789012")
    obs = bankobjekte.objekte([("a.pdf", seite(iban, "100.00")),
                               ("b.pdf", seite(iban, "100.00"))])
    assert len(obs) == 1 and len(obs[0].quellen) == 2
    obs = bankobjekte.objekte([("a.pdf", seite(iban, "100.00")),
                               ("b.pdf", seite(iban, "110.00"))])
    assert bankobjekte.einwert(obs[0].werte["saldo"])[1] == "widerspruch"
