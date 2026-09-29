"""Sprechende Dateinamen (260923-dua).

Ein Beleg heisst nach dem Download ``2025_tax_statement4.9.2026155454.pdf``.
Das Schema stammt vom Nutzer::

    BANK-U - Vermögensausweis Zinsausweis - Erika Muster 2025.pdf

Die beiden Eigenschaften, auf die es ankommt: **mehrere Inhalte werden alle
genannt** (ein Bankbeleg mit Saldo und Zinsertrag ist beides), und
**überschrieben wird niemals etwas**.
"""
from __future__ import annotations

import json

import pytest

from extractors.dateiname import (
    aussteller_kurz, baue_dateiname, entschaerfe, freier_name,
    ist_schon_sprechend, typen_aus_zielwerten,
)


# --- Das Schema -------------------------------------------------------------

def test_beispiel_des_nutzers():
    assert baue_dateiname(
        aussteller="BANK-U",
        typen=["Vermögensausweis", "Zinsausweis"],
        person="Erika Muster",
        jahr="2025",
    ) == "BANK-U - Vermögensausweis Zinsausweis - Erika Muster 2025.pdf"


def test_fehlende_teile_lassen_keine_luecke():
    assert baue_dateiname(aussteller="BANK-U", typen=["Zinsausweis"],
                          person="", jahr="2025") \
        == "BANK-U - Zinsausweis - 2025.pdf"
    assert baue_dateiname(aussteller="BANK-U", typen=["Zinsausweis"],
                          person="", jahr="") == "BANK-U - Zinsausweis.pdf"
    assert baue_dateiname(aussteller="", typen=[], person="", jahr="") == ""


def test_umlaute_bleiben():
    """„Vermögensausweis" ist der richtige Name, nicht „Vermgensausweis".

    Der Aussteller ist bewusst **erfunden**: dieser Test prueft ein Fragment
    der Eingabe (``in name``), nicht die ganze Zeichenkette. Stand hier ein
    echter Institutsname, ersetzte ihn der Public-Sync durch seinen Marker —
    die Eingabe aenderte sich, die Assertion nicht, und die Public-CI brach
    (260925-ovq). Der Test gibt nach, nicht der Scrubber.
    """
    name = baue_dateiname(aussteller="Bänkli Genossenschaft",
                          typen=["Vermögensausweis"], person="Jörg Müller",
                          jahr="2025")
    assert "Vermögensausweis" in name
    assert "Bänkli" in name
    assert "Jörg Müller" in name


# --- Mehrere Inhalte --------------------------------------------------------

def test_saldo_und_zinsen_ergeben_beide_typen():
    """Der Fall, den der Nutzer genannt hat."""
    typen = typen_aus_zielwerten([
        "Saldo 31.12.", "Bruttoertrag ohne Verrechnungssteuer"])
    assert typen == ["Vermögensausweis", "Zinsausweis"]


def test_derselbe_typ_steht_nur_einmal():
    """Bruttoertrag mit und ohne Verrechnungssteuer sind ein Zinsausweis."""
    typen = typen_aus_zielwerten([
        "Bruttoertrag ohne Verrechnungssteuer",
        "Bruttoertrag mit Verrechnungssteuer"])
    assert typen == ["Zinsausweis"]


def test_reihenfolge_ist_nicht_alphabetisch():
    """Erst der Bestand, dann der Ertrag — so beschreibt man einen Beleg."""
    typen = typen_aus_zielwerten([
        "Bruttoertrag ohne Verrechnungssteuer", "Saldo 31.12."])
    assert typen == ["Vermögensausweis", "Zinsausweis"]


def test_belegart_springt_ein_wenn_kein_zielwert_bekannt_ist():
    assert typen_aus_zielwerten([], "spenden") == ["Spendenbescheinigung"]
    assert typen_aus_zielwerten(["Unbekanntes"], "lohnausweis") \
        == ["Lohnausweis"]


def test_ohne_alles_bleibt_es_leer():
    assert typen_aus_zielwerten([], None) == []


# --- Aussteller -------------------------------------------------------------

def test_rechtsform_und_zusatz_fallen_weg():
    assert aussteller_kurz("Bank X AG, Zweigniederlassung Zürich") == "Bank X"
    assert aussteller_kurz("Vorsorgestiftung Y") == "Y"


def test_aussteller_ohne_ballast_bleibt_ganz():
    assert aussteller_kurz("BANK-U") == "BANK-U"


def test_nur_ballast_bleibt_lesbar():
    """Sonst hiesse die Datei „ - Zinsausweis - …"."""
    assert aussteller_kurz("Stiftung") == "Stiftung"


# --- Dateisystem ------------------------------------------------------------

def test_pfadzeichen_verschwinden():
    """Sonst liesse sich ueber den Ausstellernamen ein Pfad unterschieben."""
    assert "/" not in entschaerfe("a/b")
    assert ".." not in baue_dateiname(aussteller="../../etc", typen=["X"],
                                      person="", jahr="")
    for zeichen in '/\\:*?"<>|':
        assert zeichen not in entschaerfe(f"a{zeichen}b")


def test_trennzeichen_im_teil_wird_entschaerft():
    """Ein „ - " mitten im Aussteller wuerde das Schema zerlegen."""
    assert " - " not in entschaerfe("Bank - Filiale")


def test_name_wird_nie_ueberschrieben():
    belegt = {"BANK-U - Zinsausweis - A 2025.pdf"}
    assert freier_name("BANK-U - Zinsausweis - A 2025.pdf", belegt) \
        == "BANK-U - Zinsausweis - A 2025-2.pdf"
    belegt.add("BANK-U - Zinsausweis - A 2025-2.pdf")
    assert freier_name("BANK-U - Zinsausweis - A 2025.pdf", belegt) \
        == "BANK-U - Zinsausweis - A 2025-3.pdf"


def test_zweiter_lauf_haengt_keine_nummer_an():
    name = "BANK-U - Zinsausweis - A 2025.pdf"
    assert ist_schon_sprechend(name, name)
    assert ist_schon_sprechend("BANK-U - Zinsausweis - A 2025-2.pdf", name)
    assert not ist_schon_sprechend("2025_tax_statement.pdf", name)


def test_sehr_langer_name_wird_gekuerzt_aber_bleibt_lesbar():
    name = baue_dateiname(aussteller="Bank " + "X" * 100,
                          typen=["Vermögensausweis"], person="A B", jahr="2025")
    assert len(name) <= 160
    assert name.endswith(".pdf")


# --- Der ganze Weg ----------------------------------------------------------

def _korpus(tmp_path):
    samples = tmp_path / "json"
    samples.mkdir()
    pdfs = tmp_path / "pdf"
    pdfs.mkdir()
    return samples, pdfs


def test_plan_nennt_was_fehlt(tmp_path):
    from scripts.umbenennen import plane

    samples, pdfs = _korpus(tmp_path)
    (pdfs / "2025_tax_statement4.9.pdf").write_bytes(b"%PDF-")
    plan = plane(samples, pdfs, zeilen=[])
    assert len(plan) == 1
    assert plan[0]["bereit"] is False
    assert "Aussteller" in plan[0]["grund"]


def test_umbenennen_zieht_die_durchsicht_mit(tmp_path):
    """Der Dateiname ist der Schluessel der ganzen Durchsicht. Ohne Mitziehen
    stuenden alle bestaetigten Werte unter einem Dokument, das es nicht mehr
    gibt."""
    from scripts.umbenennen import fuehre_aus, plane

    samples, pdfs = _korpus(tmp_path)
    alt = "2025_tax_statement4.9.pdf"
    (pdfs / alt).write_bytes(b"%PDF-")
    (samples / "korrekturen.json").write_text(json.dumps({
        f"{alt}|Saldo 31.12.": {"soll": "1000", "bestaetigt_betrag": True},
        f"{alt}|Bruttoertrag ohne Verrechnungssteuer": {"soll": "12"},
        "anderes.pdf|Nettolohn": {"soll": "9"},
    }))
    zeilen = [
        {"pdf_name": alt, "beschreibung": "Saldo 31.12.", "aussteller": "BANK-U",
         "person": "Erika Muster (elternteil_1)", "jahr": "2025",
         "belegtyp": "bank_zinsausweis"},
        {"pdf_name": alt, "beschreibung": "Bruttoertrag ohne Verrechnungssteuer",
         "aussteller": "BANK-U", "person": "Erika Muster (elternteil_1)",
         "jahr": "2025", "belegtyp": "bank_zinsausweis"},
    ]
    plan = plane(samples, pdfs, zeilen=zeilen)
    assert plan[0]["bereit"] is True
    assert plan[0]["neu"].startswith("BANK-U - Vermögensausweis Zinsausweis - ")
    assert plan[0]["neu"].endswith("2025.pdf")

    getan = fuehre_aus(samples, pdfs, plan)
    assert len(getan) == 1
    neu = getan[0]["neu"]
    assert (pdfs / neu).is_file()
    assert not (pdfs / alt).exists()

    korr = json.loads((samples / "korrekturen.json").read_text())
    assert f"{neu}|Saldo 31.12." in korr
    assert korr[f"{neu}|Saldo 31.12."]["bestaetigt_betrag"] is True
    assert not any(k.startswith(alt + "|") for k in korr)
    # Fremde Dokumente bleiben unberuehrt.
    assert korr["anderes.pdf|Nettolohn"]["soll"] == "9"


def test_umbenennen_ist_umkehrbar(tmp_path):
    from scripts.umbenennen import fuehre_aus, plane, zurueck

    samples, pdfs = _korpus(tmp_path)
    alt = "roh.pdf"
    (pdfs / alt).write_bytes(b"%PDF-")
    (samples / "korrekturen.json").write_text(json.dumps(
        {f"{alt}|Nettolohn": {"soll": "90000"}}))
    zeilen = [{"pdf_name": alt, "beschreibung": "Nettolohn",
               "aussteller": "Firma Z", "person": "A B", "jahr": "2025",
               "belegtyp": "lohnausweis"}]
    fuehre_aus(samples, pdfs, plane(samples, pdfs, zeilen=zeilen))
    assert not (pdfs / alt).exists()

    erledigt = zurueck(samples, pdfs)
    assert len(erledigt) == 1
    assert (pdfs / alt).is_file()
    korr = json.loads((samples / "korrekturen.json").read_text())
    assert f"{alt}|Nettolohn" in korr
    assert korr[f"{alt}|Nettolohn"]["soll"] == "90000"


def test_bestehende_datei_wird_nie_ueberschrieben(tmp_path):
    from scripts.umbenennen import fuehre_aus, plane

    samples, pdfs = _korpus(tmp_path)
    (pdfs / "a.pdf").write_bytes(b"%PDF-a")
    (pdfs / "b.pdf").write_bytes(b"%PDF-b")
    gleich = dict(aussteller="BANK-U", person="A B", jahr="2025",
                  belegtyp="lohnausweis", beschreibung="Nettolohn")
    zeilen = [{"pdf_name": "a.pdf", **gleich},
              {"pdf_name": "b.pdf", **gleich}]
    getan = fuehre_aus(samples, pdfs, plane(samples, pdfs, zeilen=zeilen))
    assert len(getan) == 2
    namen = sorted(p.name for p in pdfs.glob("*.pdf"))
    assert len(namen) == 2 and len(set(namen)) == 2
    # Beide Inhalte sind noch da — nichts wurde ueberschrieben.
    inhalte = sorted(p.read_bytes() for p in pdfs.glob("*.pdf"))
    assert inhalte == [b"%PDF-a", b"%PDF-b"]


def test_person_nur_wenn_das_dokument_einer_gehoert(tmp_path):
    """Bei einem Beleg ueber zwei Kinder waere ein Name irrefuehrend."""
    from scripts.umbenennen import plane

    samples, pdfs = _korpus(tmp_path)
    (pdfs / "kita.pdf").write_bytes(b"%PDF-")
    zeilen = [
        {"pdf_name": "kita.pdf", "beschreibung": "Kosten Kinderbetreuung",
         "aussteller": "Kita K", "person": "kind_1", "jahr": "2025",
         "belegtyp": "kinderbetreuung"},
        {"pdf_name": "kita.pdf", "beschreibung": "Kosten Kinderbetreuung",
         "aussteller": "Kita K", "person": "kind_2", "jahr": "2025",
         "belegtyp": "kinderbetreuung"},
    ]
    plan = plane(samples, pdfs, zeilen=zeilen)
    assert plan[0]["person"] == ""
    assert plan[0]["neu"] == "Kita K - Betreuungskosten - 2025.pdf"


def test_gestrichene_zielwerte_erscheinen_nicht_im_namen(tmp_path):
    """Ein faelschlich erkannter Zinsausweis darf den Namen nicht praegen."""
    from scripts.umbenennen import plane

    samples, pdfs = _korpus(tmp_path)
    (pdfs / "k.pdf").write_bytes(b"%PDF-")
    zeilen = [
        {"pdf_name": "k.pdf", "beschreibung": "Saldo 31.12.",
         "aussteller": "BANK-U", "jahr": "2025", "belegtyp": "bank_zinsausweis"},
        {"pdf_name": "k.pdf", "beschreibung": "Bruttoertrag ohne Verrechnungssteuer",
         "aussteller": "BANK-U", "jahr": "2025", "belegtyp": "bank_zinsausweis",
         "gestrichen": True},
    ]
    plan = plane(samples, pdfs, zeilen=zeilen)
    assert plan[0]["typen"] == ["Vermögensausweis"]
    assert "Zinsausweis" not in plan[0]["neu"]


# --- Abgehakte Dokumente aussortieren (260923-dua) --------------------------

def test_nur_abgehakte_werden_aussortiert(tmp_path):
    from scripts.aussortieren import plane

    samples, pdfs = _korpus(tmp_path)
    (pdfs / "weg.pdf").write_bytes(b"%PDF-")
    (pdfs / "bleibt.pdf").write_bytes(b"%PDF-")
    (samples / "korrekturen.json").write_text(json.dumps({
        "weg.pdf|irrelevant": {"neu": True},
        "bleibt.pdf|Nettolohn": {"soll": "9"},
    }))
    assert plane(samples, pdfs) == ["weg.pdf"]


def test_aussortieren_verschiebt_und_ist_umkehrbar(tmp_path):
    from scripts.aussortieren import fuehre_aus, plane, zurueck, ziel_ordner

    samples, pdfs = _korpus(tmp_path)
    (pdfs / "weg.pdf").write_bytes(b"%PDF-inhalt")
    (samples / "korrekturen.json").write_text(json.dumps(
        {"weg.pdf|irrelevant": {"neu": True}}))

    getan = fuehre_aus(samples, pdfs, plane(samples, pdfs))
    assert len(getan) == 1
    assert not (pdfs / "weg.pdf").exists()
    assert (ziel_ordner(pdfs) / "weg.pdf").read_bytes() == b"%PDF-inhalt"
    # Und der naechste Lauf sieht es nicht mehr.
    assert list(pdfs.glob("*.pdf")) == []

    erledigt = zurueck(samples, pdfs)
    assert len(erledigt) == 1
    assert (pdfs / "weg.pdf").read_bytes() == b"%PDF-inhalt"


def test_aussortieren_ueberschreibt_nichts(tmp_path):
    from scripts.aussortieren import fuehre_aus, plane, ziel_ordner

    samples, pdfs = _korpus(tmp_path)
    ziel_ordner(pdfs).mkdir()
    (ziel_ordner(pdfs) / "weg.pdf").write_bytes(b"%PDF-alt")
    (pdfs / "weg.pdf").write_bytes(b"%PDF-neu")
    (samples / "korrekturen.json").write_text(json.dumps(
        {"weg.pdf|irrelevant": {"neu": True}}))

    fuehre_aus(samples, pdfs, plane(samples, pdfs))
    inhalte = sorted(p.read_bytes() for p in ziel_ordner(pdfs).glob("*.pdf"))
    assert inhalte == [b"%PDF-alt", b"%PDF-neu"]


def test_abgehaktes_dokument_faellt_aus_der_tabelle(tmp_path):
    """Der Mensch hat gesagt: gehoert nicht in die Steuererklaerung."""
    from scripts.build_tax_output import wende_korrekturen_an

    samples, _ = _korpus(tmp_path)
    (samples / "korrekturen.json").write_text(json.dumps({
        "weg.pdf|irrelevant": {"neu": True},
    }))
    rows = [{"pdf_name": "weg.pdf", "beschreibung": "Saldo 31.12.",
             "betrag": "1000"},
            {"pdf_name": "bleibt.pdf", "beschreibung": "Saldo 31.12.",
             "betrag": "2000"}]
    wende_korrekturen_an(rows, samples)
    assert [r["pdf_name"] for r in rows] == ["bleibt.pdf"]


def test_abgehaktes_dokument_gilt_nicht_als_fehlend():
    """Sonst meldet die Pruefung genau das als "fehlt", was der Mensch gerade
    ausdruecklich herausgenommen hat."""
    from extractors.korrekturen import vergleiche

    korr = {"weg.pdf|irrelevant": {"neu": True},
            "weg.pdf|Saldo 31.12.": {"soll": "1000"}}
    assert vergleiche(korr, []) == []


# --- Verwaiste Eintraege (260923-dua) ---------------------------------------

def test_umbenennung_wird_nachgezogen(tmp_path):
    """Der Browser haelt seinen eigenen Speicher und schrieb nach einer
    Umbenennung weiter unter dem ALTEN Namen zurueck. Daraus entstand ein
    zweites Dokument, das es im Ordner nicht gibt (vom Nutzer gemeldet)."""
    from scripts.umbenennen import journal_anwenden

    samples, pdfs = _korpus(tmp_path)
    (samples.parent / "umbenennungen.json").write_text(json.dumps(
        [{"zeit": "x", "alt": "roh.pdf", "neu": "Bank - Zinsausweis 2025.pdf"}]))
    (samples / "korrekturen.json").write_text(json.dumps({
        "roh.pdf|Saldo 31.12.": {"soll": "1000"},
        "andere.pdf|Nettolohn": {"soll": "9"},
    }))

    assert journal_anwenden(samples) == 1
    daten = json.loads((samples / "korrekturen.json").read_text())
    assert "Bank - Zinsausweis 2025.pdf|Saldo 31.12." in daten
    assert "roh.pdf|Saldo 31.12." not in daten
    assert daten["andere.pdf|Nettolohn"]["soll"] == "9"
    # Idempotent: ein zweiter Lauf findet nichts mehr.
    assert journal_anwenden(samples) == 0


def test_verwaist_ist_nur_was_nirgends_existiert(tmp_path):
    from scripts.verwaiste import finde_verwaiste

    samples, pdfs = _korpus(tmp_path)
    (pdfs / "da.pdf").write_bytes(b"%PDF-")
    (pdfs / "irrelevant").mkdir()
    (pdfs / "irrelevant" / "aussortiert.pdf").write_bytes(b"%PDF-")
    (samples / "wort.json").write_text(json.dumps(
        {"pdf_name": "nur-json.pdf", "pages": []}))
    (samples / "korrekturen.json").write_text(json.dumps({
        "da.pdf|Saldo 31.12.": {"soll": "1"},
        "aussortiert.pdf|irrelevant": {"neu": True},
        "nur-json.pdf|Nettolohn": {"soll": "2"},
        "gibtsnicht.pdf|Nettolohn": {"soll": "3"},
    }))
    verwaist = finde_verwaiste(samples, pdfs)
    assert list(verwaist) == ["gibtsnicht.pdf"]


def test_entfernen_sichert_vorher(tmp_path):
    from scripts.verwaiste import entferne

    samples, _ = _korpus(tmp_path)
    (samples / "korrekturen.json").write_text(json.dumps({
        "weg.pdf|Nettolohn": {"soll": "3"},
        "bleibt.pdf|Nettolohn": {"soll": "4"},
    }))
    assert entferne(samples, ["weg.pdf|Nettolohn"]) == 1
    daten = json.loads((samples / "korrekturen.json").read_text())
    assert list(daten) == ["bleibt.pdf|Nettolohn"]
    # Der Stand von vorher liegt im Verlauf.
    staende = list((samples / "korrekturen-verlauf").glob("*.json"))
    assert staende, "keine Sicherung angelegt"
    assert "weg.pdf|Nettolohn" in json.loads(staende[0].read_text())
