"""Steht der Betrag wirklich im Dokument?

Das Kernversprechen: jeder Wert muss bis aufs Wort im Original nachweisbar
sein. Für von Hand eingetragene Werte galt das nie — niemand hat je geprüft,
ob die Zahl im Beleg überhaupt vorkommt.

Die Prüfung ist nur so gut wie ihre Suche. Drei eigene Fehler haben sie
zunächst Werte als fehlend melden lassen, die sehr wohl dastanden — jeder
davon hat hier einen Test (260924-dua).
"""
from __future__ import annotations

from scripts.ablage_nachweis import brauchbar, schreibweisen, suche


def wort(text, seite=1, top=100.0, x0=10.0):
    return {"text": text, "seite": seite, "top": top, "x0": x0,
            "x1": x0 + 30, "bottom": top + 9}


def test_schweizer_schreibweisen():
    formen = schreibweisen("4756.50")
    assert "4756.50" in formen
    assert "4'756.50" in formen
    assert "4 756.50" in formen
    assert "4756,50" in formen


def test_runde_betraege_auch_ohne_rappen():
    """Das Hauptformular rechnet „CHF ohne Rappen"."""
    formen = schreibweisen("80000.00")
    assert "80'000" in formen and "80000" in formen


def test_typografischer_apostroph_wird_gefunden():
    """Schweizer PDFs setzen ’ (U+2019), nicht den geraden Apostroph.

    Die erste Fassung kannte nur `'` und fand damit keinen einzigen
    vierstelligen Betrag.
    """
    worte = [wort("4’756.50")]
    assert suche(worte, schreibweisen("4756.50"))


def test_ueber_zwei_woerter_hinweg():
    """pdfplumber trennt Beträge — „1'234" und „.55" sind zwei Wörter."""
    worte = [wort("1'234", x0=10), wort(".55", x0=40)]
    assert suche(worte, schreibweisen("1234.55"))


def test_nicht_ueber_zeilen_hinweg():
    """Zwei Zahlen untereinander ergeben keinen Betrag."""
    worte = [wort("1'234", top=100), wort(".55", top=140)]
    assert not suche(worte, schreibweisen("1234.55"))


def test_waehrung_und_sternchen_stoeren_nicht():
    assert suche([wort("CHF4'756.50*")], schreibweisen("4756.50"))


def test_ein_betrag_der_fehlt_wird_nicht_erfunden():
    assert not suche([wort("999.00")], schreibweisen("4756.50"))


def test_unbrauchbarer_anker_gilt_als_fehlend():
    """`S.None` sieht belegt aus und sagt nichts.

    Die Übernahme schrieb das, weil sie die Seite unter dem falschen
    Schlüssel suchte — und die Prüfung hielt sich für fertig.
    """
    assert brauchbar("S.2 (71.0, 305.4, 120.0, 314.9)")
    assert not brauchbar("S.None")
    assert not brauchbar("")
    assert not brauchbar("S.3")


def test_der_teilbare_teil_traegt_keine_dateinamen(tmp_path, capsys):
    """Der Grund muss teilbar bleiben — der Dateiname reist getrennt.

    Die erste Fassung schrieb ihn in den Grund und damit mitten in den Block,
    der ausdrücklich als teilbar überschrieben ist (260924-dua).
    """
    import sqlite3

    from extractors.ablage import oeffne, pfad_fuer
    from scripts.ablage_nachweis import pruefe

    samples = tmp_path / "json"
    samples.mkdir()
    db = oeffne(pfad_fuer(samples))
    db.execute("INSERT INTO position (id, dokument, zielwert, betrag, status) "
               "VALUES ('p1','geheim.pdf','Saldo 31.12.','4756.50','geprueft')")
    db.commit()
    # Ein anderes Dokument, das den Betrag traegt.
    (samples / "anderes.json").write_text(
        '{"pages": [{"page_num": 1, "words": [{"text": "4756.50", "x0": 1,'
        ' "top": 1, "x1": 2, "bottom": 2}]}]}', encoding="utf-8")
    (samples / "geheim.json").write_text(
        '{"pages": [{"page_num": 1, "words": [{"text": "1.00", "x0": 1,'
        ' "top": 1, "x1": 2, "bottom": 2}]}]}', encoding="utf-8")

    bericht = pruefe(samples)
    assert len(bericht["fehlt"]) == 1
    _zeile, grund, woanders = bericht["fehlt"][0]
    assert "ANDEREN" in grund
    assert ".pdf" not in grund and "anderes" not in grund, (
        "der Grund darf keinen Dateinamen tragen")
    assert woanders == "anderes"


def test_drei_nachkommastellen_werden_gefunden():
    """Der Wert, wie er dasteht, gehört immer zu den Schreibweisen.

    Alle Umformungen rechnen auf zwei Nachkommastellen. Bei einem Betrag mit
    dreien wurde daraus eine Zahl, die im Beleg nicht vorkommt — obwohl der
    Betrag klar auf Seite 1 stand (260924-dua, vom Nutzer gemeldet).
    """
    assert "44.271" in schreibweisen("44.271")
    assert suche([wort("44.271")], schreibweisen("44.271"))


def test_schweizer_strich_fuer_null_rappen():
    """Auf Bankbelegen üblich: 1'234.— statt 1'234.00."""
    for strich in ("—", "–", "-"):
        assert suche([wort(f"1'234.{strich}")], schreibweisen("1234.00")), strich


def test_eine_nachkommastelle_statt_zwei():
    assert suche([wort("1'234.5")], schreibweisen("1234.50"))
