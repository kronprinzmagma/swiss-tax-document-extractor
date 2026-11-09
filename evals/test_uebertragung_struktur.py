"""Die Übertragungstabelle ist nach dem Ausfüllen gegliedert, nicht nach Status.

Vorher standen die Zeilen unter „🟢 automatisch übertragbar", „🟡 manuell
prüfen" und „⚪ out of scope". Das beschreibt, wie weit das Programm ist — nicht,
wie der Mensch die Steuererklärung ausfüllt. Sobald alles geprüft war, stand
alles in einem Abschnitt und die Einteilung half nicht mehr.

Jetzt: Formular in der Reihenfolge des Ausfüllens → je Konto bzw. Person ein
Block → darin die Werte mit ihrer Ziffer → Summen je Ziffer. Alles für einen
Eingabeschritt an einer Stelle (260923-dua).
"""
from __future__ import annotations

from pathlib import Path

from scripts.build_tax_output import render_uebertragung


def zeile(**kwargs) -> dict:
    grund = {
        "pdf_name": "a.pdf", "steuerbereich": "Vermögen & Erträge",
        "ziffer": "30.1", "person": "elternteil_1", "aussteller": "BANK-A",
        "beschreibung": "Saldo 31.12.", "betrag": "1000.00", "jahr": "2025",
        "page": 1, "bbox": None, "snippet": "", "status": "auto",
        "field_name": "saldo", "plaus_errs": [], "plaus_hinweis": None,
        "manual_review_marker": None, "derived": False, "anchor_valid": True,
        "herkunft": "mensch", "inference_source": "ablage",
        "unterscheidung": "", "pos": 1,
    }
    grund.update(kwargs)
    return grund


def baue(rows: list[dict]) -> str:
    return render_uebertragung(rows, [], [], len(rows), len(rows),
                               Path("output/latest/json"))


def test_gliederung_folgt_den_formularen():
    text = baue([
        zeile(),
        zeile(beschreibung="Nettolohn", ziffer="1", aussteller="AG-A",
              pdf_name="lohn.pdf", betrag="80000.00"),
    ])
    # Das Lohnformular kommt vor dem Wertschriftenverzeichnis — so wird
    # ausgefuellt.
    assert text.index("## Lohnausweis (100)") \
        < text.index("## Wertschriftenverzeichnis (400)")


def test_kein_status_abschnitt_mehr():
    text = baue([zeile()])
    assert "Automatisch übertragbar" not in text
    assert "Manuell prüfen" not in text


def test_ein_block_je_konto_mit_allen_werten_darin():
    """Saldo und Ertrag desselben Kontos stehen zusammen."""
    text = baue([
        zeile(),
        zeile(beschreibung="Bruttoertrag ohne Verrechnungssteuer",
              ziffer="4", betrag="12.40"),
    ])
    block = text.split("### ")[1]
    assert "Saldo 31.12." in block
    assert "Bruttoertrag ohne Verrechnungssteuer" in block


def test_zwei_dokumente_desselben_kontos_geben_einen_block():
    """Zinsabrechnung und Steuerauszug — ein Konto, eine Eingabe.

    Sonst sucht man den Ertrag an einer anderen Stelle als den Saldo.
    """
    text = baue([
        zeile(pdf_name="Zinsabrechnung_123456_2025.pdf"),
        zeile(pdf_name="TAX_P_123456_2025.pdf",
              beschreibung="Bruttoertrag ohne Verrechnungssteuer",
              ziffer="4", betrag="12.40"),
    ])
    assert text.count("### ") == 1, "beide Dokumente gehoeren in einen Block"


def test_verschiedene_konten_bleiben_getrennt():
    text = baue([
        zeile(pdf_name="Zinsabrechnung_111111_2025.pdf"),
        zeile(pdf_name="Zinsabrechnung_222222_2025.pdf",
              aussteller="BANK-B"),
    ])
    assert text.count("### ") == 2


def test_die_kontonummer_wird_nicht_zur_ueberschrift():
    """Sie gruppiert nur; in der Überschrift stehen Aussteller und Person.

    In der Quelle-Spalte steht weiterhin der Dokumentname — den braucht man,
    um den Wert im Original nachzuschlagen. Die Gruppierungsnummer selbst
    taucht nirgends als eigene Angabe auf.
    """
    text = baue([zeile(pdf_name="Zinsabrechnung_987654_2025.pdf")])
    ueberschrift = text.split("### ")[1].splitlines()[0]
    assert "987654" not in ueberschrift
    assert "BANK-A" in ueberschrift


def test_summen_je_ziffer_am_ende_jedes_formulars():
    text = baue([
        zeile(betrag="1000.00"),
        zeile(pdf_name="b.pdf", aussteller="BANK-B", betrag="500.50"),
    ])
    assert "**Summen für Wertschriftenverzeichnis (400):**" in text
    # Dieselbe Schreibweise wie in den Zeilen darueber — `fmt_amount`.
    assert "1500.50" in text


def test_alle_ziffern_auf_einen_blick():
    """Die Gesamtsumme je Ziffer — zum Abhaken beim Ausfüllen."""
    text = baue([
        zeile(betrag="1000.00"),
        zeile(beschreibung="Nettolohn", ziffer="1", pdf_name="lohn.pdf",
              aussteller="AG-A", betrag="80000.00"),
    ])
    assert "## Alle Ziffern auf einen Blick" in text
    assert "80000.00" in text


def test_der_grader_findet_seine_spalten_weiterhin():
    """`is_transfer_table` prüft auf bestimmte Begriffe im Text."""
    text = baue([zeile()])
    for begriff in ("Steuerbereich", "Ziffer", "Beschreibung", "Betrag CHF",
                    "Quelle", "Status"):
        assert begriff in text, begriff
    for verboten in ("BBox", "Snippet", "Anker (x0,top)"):
        assert verboten not in text, verboten
