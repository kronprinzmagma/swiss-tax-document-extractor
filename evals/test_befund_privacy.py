"""Befunde auf der Konsole duerfen keine Dokumentnamen tragen (260904-rmx).

Steuerbelege heissen nach den Menschen, um die es geht: „Steuerauszug 2025
<Vorname Nachname>.pdf" ist ein Personendatum. Die Konsolenausgabe von
``build_review_html`` wurde als teilbar beschrieben und enthielt genau das —
ein Nutzer hat sie daraufhin weitergegeben.

Die Oberflaeche selbst zeigt den Namen weiterhin: sie laeuft lokal, und ohne
ihn weiss niemand, welches Dokument gemeint ist.
"""
from __future__ import annotations

from extractors.vollstaendigkeit import (
    Befund,
    pruefe_dokument_konsistenz,
)


def test_rolle_bleibt_lesbar():
    b = Befund("kvg_fehlt", "fehlt", "…", "elternteil_2")
    assert b.betrifft_neutral == "elternteil_2"


def test_dokumentname_wird_nicht_ausgegeben():
    b = Befund("selbstkosten_ohne_praemie", "unstimmig", "…",
               "Steuerauszug - 2025 - Vorname Nachname.pdf")
    assert "Nachname" not in b.betrifft_neutral
    assert ".pdf" not in b.betrifft_neutral
    assert b.betrifft_neutral


def test_leeres_betrifft_bleibt_leer():
    assert Befund("person_fehlt", "fehlt", "…").betrifft_neutral == ""


def test_die_praemienregel_nennt_eine_rolle_kein_dokument():
    """Seit die Regel je Person gilt, traegt sie gar keinen Dateinamen mehr.

    Das ist die bessere Loesung als Maskieren: was nicht drinsteht, kann auch
    nicht hinausrutschen. Der Befund ist damit unveraendert teilbar
    (260923-dua).
    """
    zeilen = [{"person": "elternteil_1", "pdf_name": "Ausweis Person X.pdf",
               "belegtyp": "krankheitskosten",
               "beschreibung": "Selbst getragene Krankheits- und Unfallkosten"}]
    b = pruefe_dokument_konsistenz(zeilen)[0]
    assert b.betrifft == "elternteil_1"
    assert b.betrifft_neutral == "elternteil_1"
    assert "Person X" not in b.text and ".pdf" not in b.text


def test_konsolenausgabe_maskiert():
    """Die Stelle, die es falsch machte."""
    import inspect

    from scripts import build_review_html
    quelle = inspect.getsource(build_review_html.main)
    assert "b.betrifft_neutral" in quelle
    assert "{b.betrifft}" not in quelle
