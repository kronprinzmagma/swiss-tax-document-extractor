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
from scripts.build_tax_output import (
    ergaenze_eigene_zeilen, wende_korrekturen_an,
)


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


def _ablage_mit(tmp_path, *zeilen):
    """Eine Ablage mit den angegebenen (Dokument, Zielwert, Betrag)."""
    from extractors.ablage import oeffne, pfad_fuer

    samples = tmp_path / "json"
    samples.mkdir(exist_ok=True)
    db = oeffne(pfad_fuer(samples))
    for i, (dok, ziel, betrag) in enumerate(zeilen, start=1):
        db.execute(
            "INSERT INTO position (id, dokument, zielwert, pos, betrag, "
            "aussteller, jahr, status) VALUES (?,?,?,1,?,'BANK-A','2025',"
            "'geprueft')", (f"p{i:04d}", dok, ziel, betrag))
    db.commit()
    db.close()
    return samples


def test_neue_position_kommt_in_die_ablage(tmp_path):
    """Sonst ist der von Hand nachgetragene Wert doppelt verloren.

    Der Rückweg kannte nur UPDATE: eine über „＋ Position" erfasste Zeile
    blieb in ``korrekturen.json`` liegen. Der Tabellenbau wirft danach alle
    Zeilen eines Dokuments weg, das die Ablage führt — der Wert war weder
    gespeichert noch sichtbar, und gemeldet wurde nichts (260929-dua).
    """
    from extractors.ablage import lade_geprueft, uebernimm_durchsicht

    samples = _ablage_mit(tmp_path,
                          ("a.pdf", "Schuldzinsen Hypothek", "4200"))
    lege(samples / "korrekturen.json", {
        "a.pdf|Saldo 31.12.": {"soll": "1234.55", "neu": True,
                               "person": "elternteil_1", "jahr": "2025"},
    })

    bericht = uebernimm_durchsicht(
        samples, nur={("a.pdf", "Saldo 31.12.", 1)})
    assert bericht.get("neu angelegt") == 1

    gespeichert = {(r["beschreibung"], r["betrag"])
                   for r in lade_geprueft(samples)}
    assert ("Saldo 31.12.", "1234.55") in gespeichert
    # Und in der Tabelle ist er damit auch.
    rows: list[dict] = []
    ergaenze_eigene_zeilen(rows, samples)
    assert any(r["beschreibung"] == "Saldo 31.12." for r in rows)


def test_nur_das_gerade_gespeicherte_wird_angelegt(tmp_path):
    """Ein alter Handeintrag darf nicht bei jedem Speichern zurückkommen.

    Wer einen Wert in ein anderes Dokument überträgt, lässt den früheren
    Eintrag in der Durchsicht stehen. Käme er jedes Mal mit, stünde die
    Position wieder doppelt da.
    """
    from extractors.ablage import lade_geprueft, uebernimm_durchsicht

    samples = _ablage_mit(tmp_path, ("a.pdf", "Saldo 31.12.", "100"))
    lege(samples / "korrekturen.json", {
        "a.pdf|Schuldzinsen Hypothek": {"soll": "7.00", "neu": True},
        "alt.pdf|Hypothekarschuld 31.12.": {"soll": "250000", "neu": True},
    })

    uebernimm_durchsicht(samples,
                         nur={("a.pdf", "Schuldzinsen Hypothek", 1)})
    ziele = {r["beschreibung"] for r in lade_geprueft(samples)}
    assert ziele == {"Saldo 31.12.", "Schuldzinsen Hypothek"}


def test_gestrichene_neue_position_kommt_nicht_zurueck(tmp_path):
    from extractors.ablage import lade_geprueft, uebernimm_durchsicht

    samples = _ablage_mit(tmp_path, ("a.pdf", "Saldo 31.12.", "100"))
    lege(samples / "korrekturen.json", {
        "a.pdf|Schuldzinsen Hypothek": {"soll": "7.00", "neu": True,
                                "entfernt": True},
    })
    uebernimm_durchsicht(samples,
                         nur={("a.pdf", "Schuldzinsen Hypothek", 1)})
    assert {r["beschreibung"] for r in lade_geprueft(samples)} == \
        {"Saldo 31.12."}


def test_zweimal_speichern_legt_nicht_zweimal_an(tmp_path):
    """Sonst wüchse die Ablage bei jedem Klick auf Speichern."""
    from extractors.ablage import lade_geprueft, uebernimm_durchsicht

    samples = _ablage_mit(tmp_path, ("a.pdf", "Saldo 31.12.", "100"))
    lege(samples / "korrekturen.json", {
        "a.pdf|Schuldzinsen Hypothek": {"soll": "7.00", "neu": True},
    })
    nur = {("a.pdf", "Schuldzinsen Hypothek", 1)}
    uebernimm_durchsicht(samples, nur=nur)
    uebernimm_durchsicht(samples, nur=nur)
    assert len(lade_geprueft(samples)) == 2


def test_ohne_ablage_bleibt_die_eigene_zeile_in_der_tabelle(tmp_path):
    """Ohne Ablage gilt der alte Weg unveraendert."""
    samples = tmp_path / "json"
    samples.mkdir()
    lege(samples / "korrekturen.json", {
        "a.pdf|Saldo 31.12.": {"soll": "1234.55", "neu": True},
    })
    rows: list[dict] = []
    ergaenze_eigene_zeilen(rows, samples)
    assert [r["beschreibung"] for r in rows] == ["Saldo 31.12."]


def test_alter_name_wird_zum_vertragsnamen(tmp_path):
    """Wer „Vermögensstand 31.12." erfasst, meint den Steuerwert.

    Der Feldvertrag kennt diesen Namen nicht. Eine so angelegte Position
    erschien deshalb weder in der Tabelle noch im eSteuerauszug — und es
    wurde nichts gemeldet (260929-dua).
    """
    from extractors.ablage import lade_geprueft, uebernimm_durchsicht

    samples = _ablage_mit(
        tmp_path, ("a.pdf", "Bruttoertrag ohne Verrechnungssteuer", "90.80"))
    lege(samples / "korrekturen.json", {
        "a.pdf|Vermögensstand 31.12.": {"soll": "50789.75", "neu": True},
    })
    uebernimm_durchsicht(samples,
                         nur={("a.pdf", "Vermögensstand 31.12.", 1)})
    ziele = {r["beschreibung"]: r["betrag"] for r in lade_geprueft(samples)}
    assert ziele.get("Saldo 31.12.") == "50789.75"


def test_das_dokument_gibt_die_schreibweise_vor(tmp_path):
    """„Saldo" und „Steuerwert" hiessen beide früher „Vermögensstand"."""
    from extractors.ablage import lade_geprueft, uebernimm_durchsicht

    samples = _ablage_mit(tmp_path, ("a.pdf", "Steuerwert 31.12.", "100"))
    lege(samples / "korrekturen.json", {
        "a.pdf|Vermögensstand 31.12.#2": {"soll": "200", "neu": True},
    })
    uebernimm_durchsicht(samples,
                         nur={("a.pdf", "Vermögensstand 31.12.", 2)})
    ziele = [r["beschreibung"] for r in lade_geprueft(samples)]
    assert ziele == ["Steuerwert 31.12.", "Steuerwert 31.12."]
