"""Der Schlusscheck über der Übertragungstabelle (260923-dua).

Drei Fragen, bevor die Zahlen ins Formular wandern — dieselben, die ein
Mensch stellt, der die Liste zum letzten Mal überfliegt: ist alles da, ist
jeder Eintrag in sich vollständig, sind die Zahlen glaubwürdig.
"""
from __future__ import annotations

import pytest

from extractors.schlusspruefung import (
    SAEULE_3A_MAX, SAEULE_3A_OHNE_PK, betrag_oder_nichts,
    pruefe_plausibel, pruefe_schluss, pruefe_vollstaendig,
)


def _z(**kw) -> dict:
    grund = {"beleg": "a.pdf", "zielwert": "Nettolohn", "betrag": "90000",
             "person": "elternteil_1"}
    grund.update(kw)
    return grund


def _texte(befunde) -> str:
    return " | ".join(b.text for b in befunde)


# --- Ist es ueberhaupt eine Zahl? -------------------------------------------

def test_muellwert_wird_gemeldet_statt_zu_werfen():
    """`parse_swiss_amount` wirft bei Muell eine Ausnahme. Hier ist „keine
    Zahl" gerade das Ergebnis, nach dem gefragt wird."""
    assert betrag_oder_nichts("k.A.") is None
    assert betrag_oder_nichts("") is None
    assert betrag_oder_nichts("1'234.50") == pytest.approx(1234.50)

    b = pruefe_plausibel([_z(betrag="k.A.")])
    assert any(x.schwere == "unstimmig" and "keine Zahl" in x.text for x in b)


def test_fehlender_betrag_faellt_auf():
    b = pruefe_plausibel([_z(betrag="")])
    assert any(x.schwere == "fehlt" for x in b)


def test_negativer_betrag_faellt_auf():
    b = pruefe_plausibel([_z(betrag="-500")])
    assert any(x.schwere == "unstimmig" and "negativ" in x.text for x in b)


# --- Groessenordnung --------------------------------------------------------

def test_praemie_von_45_franken_im_jahr_ist_verdaechtig():
    b = pruefe_plausibel([_z(zielwert="Prämie Grundversicherung KVG",
                             betrag="45", person="kind_1")])
    assert any(x.schwere == "hinweis" and "niedrig" in x.text for x in b)


def test_praemie_von_450000_ist_verdaechtig():
    b = pruefe_plausibel([_z(zielwert="Prämie Grundversicherung KVG",
                             betrag="450000")])
    assert any(x.schwere == "hinweis" and "hoch" in x.text for x in b)


def test_uebliche_werte_schweigen():
    """Ein Hinweis, der bei jedem zweiten Wert anschlaegt, wird ueberlesen."""
    zeilen = [
        _z(zielwert="Nettosohn-tippfehler", betrag="90000"),   # unbekannt
        _z(zielwert="Nettolohn", betrag="92500"),
        _z(zielwert="Prämie Grundversicherung KVG", betrag="4800"),
        _z(zielwert="Einzahlung Säule 3a", betrag="7056"),
        _z(zielwert="Kosten Kinderbetreuung", betrag="12000"),
    ]
    assert pruefe_plausibel(zeilen) == []


# --- Gesetzliche Grenzen (keine Erfahrungswerte) ----------------------------

def test_massgeblich_ist_der_betrag_mit_pensionskasse():
    """7'258 ist die Grenze. Der hoehere Betrag gilt nur OHNE 2. Saeule, also
    faktisch fuer Selbstaendige — in diesem Haushalt sind beide angestellt.

    Die erste Fassung hatte es umgekehrt und haette eine Einzahlung von
    20'000 durchgelassen (vom Nutzer korrigiert).
    """
    b = pruefe_plausibel([_z(zielwert="Einzahlung Säule 3a", betrag="20000")])
    assert [x.schwere for x in b] == ["unstimmig"]
    assert "7'258" in b[0].text


def test_knapp_darueber_faellt_schon_auf():
    b = pruefe_plausibel([_z(zielwert="Einzahlung Säule 3a",
                             betrag=str(SAEULE_3A_MAX + 1))])
    assert [x.schwere for x in b] == ["unstimmig"]


def test_hoechstabzug_selbst_ist_kein_befund():
    assert pruefe_plausibel([_z(zielwert="Einzahlung Säule 3a",
                                betrag=str(SAEULE_3A_MAX))]) == []


def test_meldung_nennt_die_ausnahme():
    """Wer keine Pensionskasse hat, darf mehr — das soll dastehen, sonst
    sucht man den Fehler an der falschen Stelle."""
    b = pruefe_plausibel([_z(zielwert="Einzahlung Säule 3a", betrag="20000")])
    assert "ohne Pensionskasse" in b[0].text
    assert "36'288" in b[0].text


def test_betraege_werden_schweizerisch_geschrieben():
    """`f"{x:,.0f}".replace(",", "'")` traf auch die Kommas im Fliesstext:
    „einen Lohnausweis' also eine Pensionskasse" (260923-dua)."""
    from extractors.schlusspruefung import _chf
    assert _chf(1234567.5) == "1'234'567.50"
    assert _chf(7258) == "7'258"
    b = pruefe_plausibel([_z(zielwert="Einzahlung Säule 3a", betrag="20000")])
    # Kein Apostroph, wo ein Komma hingehoert.
    assert "' " not in b[0].text.replace("'258", "").replace("'288", "")


# --- Gehoert der Gegenwert dazu? --------------------------------------------

def test_hypothek_ohne_schuldzins_faellt_auf():
    """Der Fall, den der Nutzer genannt hat."""
    b = pruefe_vollstaendig([
        _z(beleg="h.pdf", zielwert="Hypothekarschuld 31.12.", betrag="500000"),
    ])
    assert any(x.schwere == "fehlt" and "Schuldzinsen" in x.text for x in b)


def test_schuldzins_ohne_hypothek_faellt_auch_auf():
    b = pruefe_vollstaendig([
        _z(beleg="h.pdf", zielwert="Schuldzinsen Hypothek", betrag="4200"),
    ])
    assert any(x.schwere == "fehlt" and "Hypothekarschuld" in x.text for x in b)


def test_drei_hypotheken_brauchen_drei_zinsen():
    b = pruefe_vollstaendig([
        _z(beleg="h.pdf", zielwert="Hypothekarschuld 31.12.", betrag="500000"),
        _z(beleg="h.pdf", zielwert="Hypothekarschuld 31.12.", betrag="250000"),
        _z(beleg="h.pdf", zielwert="Schuldzinsen Hypothek", betrag="4200"),
    ])
    assert any(x.schwere == "unstimmig" and "2×" in x.text for x in b)


def test_paar_im_gleichgewicht_schweigt():
    assert pruefe_vollstaendig([
        _z(beleg="h.pdf", zielwert="Hypothekarschuld 31.12.", betrag="500000"),
        _z(beleg="h.pdf", zielwert="Schuldzinsen Hypothek", betrag="4200"),
    ]) == []


def test_gestrichene_zeilen_zaehlen_nicht():
    """Eine faelschlich erkannte Position darf keinen Befund ausloesen."""
    assert pruefe_vollstaendig([
        _z(beleg="h.pdf", zielwert="Hypothekarschuld 31.12.", betrag="500000",
           gestrichen=True),
    ]) == []


# --- Zinssatz als Gegenprobe ------------------------------------------------

def test_unglaubwuerdiger_zinssatz_faellt_auf():
    b = pruefe_plausibel([
        _z(beleg="h.pdf", zielwert="Hypothekarschuld 31.12.", betrag="500000"),
        _z(beleg="h.pdf", zielwert="Schuldzinsen Hypothek", betrag="120000"),
    ])
    assert any("% der Hypothekarschuld" in x.text for x in b)


def test_glaubwuerdiger_zinssatz_schweigt():
    b = pruefe_plausibel([
        _z(beleg="h.pdf", zielwert="Hypothekarschuld 31.12.", betrag="500000"),
        _z(beleg="h.pdf", zielwert="Schuldzinsen Hypothek", betrag="7500"),
    ])
    assert not any("Hypothekarschuld" in x.text for x in b)


def test_zinssatz_nur_bei_eindeutiger_zuordnung():
    """Bei mehreren Hypotheken auf einem Beleg ist nicht klar, welcher Zins zu
    welcher gehoert — eine Warnung auf Verdacht waere Rauschen."""
    b = pruefe_plausibel([
        _z(beleg="h.pdf", zielwert="Hypothekarschuld 31.12.", betrag="500000"),
        _z(beleg="h.pdf", zielwert="Hypothekarschuld 31.12.", betrag="250000"),
        _z(beleg="h.pdf", zielwert="Schuldzinsen Hypothek", betrag="120000"),
    ])
    assert not any("% der Hypothekarschuld" in x.text for x in b)


# --- Alles zusammen ---------------------------------------------------------

def test_die_drei_gruppen_sind_getrennt():
    befunde = pruefe_schluss([
        _z(beleg="h.pdf", zielwert="Hypothekarschuld 31.12.", betrag="500000"),
        _z(zielwert="Nettolohn", betrag="k.A."),
    ], ("elternteil_1",))
    gruppen = {b.gruppe for b in befunde}
    assert {"vollstaendig", "plausibel"} <= gruppen


def test_sauberer_lauf_meldet_nichts_zur_plausibilitaet():
    assert pruefe_plausibel([
        _z(zielwert="Nettolohn", betrag="92'500.00"),
    ]) == []
