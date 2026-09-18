"""Korrekturen liegen an zwei Orten — beide müssen gelten (260904-rmx).

``apply_korrekturen`` schreibt neben die CSV, der Live-Server in den
Sample-Ordner. Die Aufrufer nahmen ``lade(a) or lade(b)``: das ist kein
„beide", sondern „die erste, die es gibt". Sobald der Server einmal
gespeichert hatte, war die über die CSV eingespielte Arbeit unsichtbar —
ohne Fehlermeldung, die Tabelle sah einfach wieder aus wie vor der
Durchsicht.
"""
from __future__ import annotations

import json

from extractors.korrekturen import lade_alle
from scripts.build_tax_output import wende_korrekturen_an


def lege(pfad, inhalt: dict):
    pfad.parent.mkdir(parents=True, exist_ok=True)
    pfad.write_text(json.dumps(inhalt, ensure_ascii=False))


def zeile(**kw) -> dict:
    basis = {"pdf_name": "a.pdf", "beschreibung": "Bruttoertrag",
             "betrag": "1.00", "person": "", "aussteller": "B", "ziffer": "4",
             "herkunft": "modell", "anchor_valid": False, "plaus_errs": [],
             "plaus_hinweis": None, "manual_review_marker": None,
             "status": "manual_review", "field_name": "bruttoertrag",
             "derived": False}
    basis.update(kw)
    return basis


def test_beide_ablagen_werden_gelesen(tmp_path):
    latest, samples = tmp_path, tmp_path / "json"
    lege(latest / "korrekturen.json", {"a.pdf|Bruttoertrag": {"soll": "100.00"}})
    lege(samples / "korrekturen.json", {"b.pdf|Saldo": {"soll": "200.00"}})
    alle = lade_alle(samples)
    assert set(alle) == {"a.pdf|Bruttoertrag", "b.pdf|Saldo"}


def test_der_sample_ordner_gewinnt_bei_gleichem_schluessel(tmp_path):
    """Dort schreibt die laufende Sitzung — also der jüngere Stand."""
    latest, samples = tmp_path, tmp_path / "json"
    lege(latest / "korrekturen.json", {"a.pdf|Bruttoertrag": {"soll": "100.00"}})
    lege(samples / "korrekturen.json", {"a.pdf|Bruttoertrag": {"soll": "300.00"}})
    assert lade_alle(samples)["a.pdf|Bruttoertrag"]["soll"] == "300.00"


def test_nur_eine_ablage_reicht(tmp_path):
    samples = tmp_path / "json"
    samples.mkdir()
    lege(tmp_path / "korrekturen.json", {"a.pdf|Bruttoertrag": {"soll": "5.00"}})
    assert lade_alle(samples)["a.pdf|Bruttoertrag"]["soll"] == "5.00"


def test_gar_keine_ablage(tmp_path):
    samples = tmp_path / "json"
    samples.mkdir()
    assert lade_alle(samples) == {}


def test_csv_arbeit_ueberlebt_den_ersten_serverstart(tmp_path):
    """Der Fall, der es in die Praxis geschafft hat.

    Die Einträge aus der CSV lagen eine Ebene höher. Der Server speicherte
    eine einzige Änderung in den Sample-Ordner — und ab da galt nur noch
    diese eine.
    """
    latest, samples = tmp_path, tmp_path / "json"
    lege(latest / "korrekturen.json",
         {"a.pdf|Bruttoertrag": {"soll": "100.00", "person": "elternteil_1"}})
    lege(samples / "korrekturen.json", {"c.pdf|Nettolohn": {"soll": "9.00"}})

    rows = [zeile()]
    assert wende_korrekturen_an(rows, samples) == 1
    assert rows[0]["betrag"] == "100.00"
    assert rows[0]["person"] == "elternteil_1"
