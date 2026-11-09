"""Datenmodell laut, Pflichtwerte aus dem Datenmodell, Ordner statt Lauf
(261025-jm6, A1, A3, A5, A6).

* A1 — Ein fehlender oder umbenannter Schlüssel in `extractors/datenmodell.yaml`
  schaltete eine Regel still ab (Accessoren lieferten {}, () oder 0). Jetzt
  ist er ein lauter Fehler; die Pflichtwerte je Dokumenttyp kommen aus dem
  Datenmodell, nicht aus hart kodierten Titeln.
* A3 — „im Ordner" hiess „im letzten Lauf". Jetzt vergleicht make pruefen
  mit dem echten PDF-Ordner.
* A5 — Mehrere Befunde zu einem Dokument: in der Inventur gewann der letzte.
* A6 — Freitext-Personen standen in der teilbaren Fassung.

Alle Werte erfunden.
"""
from __future__ import annotations

import json
import re
import sys
import unicodedata
from pathlib import Path

import pytest
import yaml

from extractors import datenmodell

HYPO = "Musterbank - Zinsabrechnung - Muster 2025.pdf"
LOHN = "Musterfirma - Lohnausweis - Muster 2025.pdf"
KONTO = "Musterbank - Kontoauszug - Muster 2025.pdf"
KK = "KK-A - Praemien - Muster 2025.pdf"
KK2 = "KK-B - Praemien - Kind 2025.pdf"


# --- Hilfen -----------------------------------------------------------------

def _yaml_block() -> tuple[str, dict]:
    text = datenmodell.PFAD.read_text(encoding="utf-8")
    return text, yaml.safe_load(text)


def _schreibe_modell(ziel: Path, aendern) -> Path:
    _, daten = _yaml_block()
    aendern(daten)
    ziel.write_text(yaml.safe_dump(daten, allow_unicode=True, sort_keys=False),
                    encoding="utf-8")
    return ziel


def _loesche(daten: dict, pfad: str) -> None:
    *vorne, letzt = pfad.split(".")
    knoten = daten
    for teil in vorne:
        knoten = knoten[teil]
    del knoten[letzt]


@pytest.fixture
def modell(tmp_path, monkeypatch):
    """Ein verändertes Datenmodell für einen Test — Cache vor und nach leer."""
    datenmodell.lade.cache_clear()

    def setze(aendern) -> Path:
        pfad = _schreibe_modell(tmp_path / "datenmodell.yaml", aendern)
        monkeypatch.setattr(datenmodell, "PFAD", pfad)
        datenmodell.lade.cache_clear()
        return pfad

    yield setze
    monkeypatch.undo()
    datenmodell.lade.cache_clear()


def _samples(tmp_path: Path, ergebnis: list[tuple[str, str]],
             word: list[tuple[str, str]] = ()) -> Path:
    samples = tmp_path / "json"
    samples.mkdir(exist_ok=True)
    for i, (name, ordner) in enumerate(word):
        (samples / f"doc{i}.json").write_text(
            json.dumps({"pdf_name": name, "ordner": ordner, "pages": []}), encoding="utf-8")
    (samples / "_results_full.json").write_text(
        json.dumps([{"pdf_name": n, "belegtyp": t, "fields": []} for n, t in ergebnis]),
        encoding="utf-8")
    return samples


def _belege(tmp_path: Path, pdfs: list[tuple[str, str]]) -> Path:
    ordner = tmp_path / "belege"
    ordner.mkdir(exist_ok=True)
    for name, unter in pdfs:
        (ordner / unter).mkdir(parents=True, exist_ok=True)
        (ordner / unter / name).write_bytes(b"")
    return ordner


def _leiste_hypothek(mit_zinssatz: bool = False) -> dict:
    felder = [
        {"titel": "Name, Vorname und Adresse",
         "wert": "Musterbank, Bahnhofstrasse 1, 8000 Musterhausen", "herkunft": "beleg"},
        {"titel": "Schuld am 31.12.", "wert": "250'000.00", "herkunft": "beleg"},
        {"titel": "Schuldzinsen", "wert": "1'500.00", "herkunft": "beleg"}]
    if mit_zinssatz:
        felder.append({"titel": "Zinssatz", "wert": "1.25 %", "herkunft": "beleg"})
    return {"formulare": [{"formular": "Schuldenverzeichnis", "bloecke": [
        {"titel": "Musterbank · Hypothek", "maske": "Schuld", "person": "familie",
         "dokumente": [HYPO], "felder": felder, "werte": []}]}]}


def _leiste_lohn() -> dict:
    return {"formulare": [{"formular": "Lohnausweis (100)", "bloecke": [
        {"titel": "Musterfirma · elternteil_1", "aussteller": "Musterfirma",
         "person": "elternteil_1",
         "werte": [{"bezeichnung": "Nettolohn (Ziffer 11)", "betrag": "80'000.00",
                    "quelle": LOHN}],
         "angaben": [{"quelle": LOHN, "werte": [
             {"titel": "Arbeitgeber", "wert": "Musterfirma", "herkunft": "tabelle"},
             {"titel": "Anstellung von", "wert": "01.01.2025", "herkunft": "belegt"},
             {"titel": "Anstellung bis", "wert": "", "herkunft": "offen"}]}]}]}]}


def _fehlt_von(befunde: list[dict], dok: str) -> list[str]:
    return [x for b in befunde if b["dokument"] == dok for x in b["fehlt"]]


def _ohne_kk(monkeypatch):
    """Keine Word-Tabellen — die Prämienverbilligung bleibt still."""
    from extractors import eintraege
    monkeypatch.setattr(eintraege, "tabellen_fuer", lambda *a, **k: {})


# --- A1: lade() prüft die Pflichtschlüssel -----------------------------------

PFLICHTPFADE = ("ordner_typen", "pflicht_je_typ", "personen.rollen",
                "personen.krankenkasse_dokumente", "personen.kvg_fast_leer.erwachsene",
                "personen.kvg_fast_leer.kinder", "personen.saeule_3a_maximum",
                "personen.kita_maximum_je_kind")


@pytest.mark.parametrize("pfad", PFLICHTPFADE)
def test_fehlender_pflichtschluessel_ist_laut(tmp_path, pfad):
    ziel = _schreibe_modell(tmp_path / "datenmodell.yaml", lambda d: _loesche(d, pfad))
    with pytest.raises(datenmodell.DatenmodellFehler) as e:
        datenmodell.lade_aus(ziel)
    assert isinstance(e.value, ValueError)
    assert pfad in str(e.value)


def test_umbenannter_schluessel_ist_laut(tmp_path):
    def kk_umbenennen(d):
        d["personen"]["kk_dokumente"] = d["personen"].pop("krankenkasse_dokumente")

    def ordner_umbenennen(d):
        d["ordnertypen"] = d.pop("ordner_typen")

    for aendern, erwartet in ((kk_umbenennen, "krankenkasse_dokumente"),
                              (ordner_umbenennen, "ordner_typen")):
        ziel = _schreibe_modell(tmp_path / "datenmodell.yaml", aendern)
        with pytest.raises(datenmodell.DatenmodellFehler, match=erwartet):
            datenmodell.lade_aus(ziel)

    ziel = _schreibe_modell(tmp_path / "datenmodell.yaml",
                            lambda d: d["personen"].__setitem__("rollen", []))
    with pytest.raises(datenmodell.DatenmodellFehler, match="personen.rollen"):
        datenmodell.lade_aus(ziel)


def test_kk_rolle_ohne_eintrag_in_rollen_ist_laut(tmp_path):
    ziel = _schreibe_modell(
        tmp_path / "datenmodell.yaml",
        lambda d: d["personen"]["krankenkasse_dokumente"].__setitem__("kind_3", 1))
    with pytest.raises(datenmodell.DatenmodellFehler, match="kind_3"):
        datenmodell.lade_aus(ziel)


def test_echtes_datenmodell_ist_gueltig():
    daten = datenmodell.lade_aus(datenmodell.PFAD)
    assert daten["pflicht_je_typ"]
    assert datenmodell.saeule_3a_maximum() > 0
    assert datenmodell.kita_maximum_je_kind() > 0


# --- A1: Pflichtwerte je Typ aus dem Datenmodell -----------------------------

def test_hypothek_ohne_zinssatz_meldet_zinssatz(tmp_path, monkeypatch):
    from scripts import pflichtfelder_check as pc

    _ohne_kk(monkeypatch)
    samples = _samples(tmp_path, [(HYPO, "hypothek_zinsbestaetigung")])
    befunde = pc.pruefe_leiste(samples, daten=_leiste_hypothek())
    assert "Zinssatz" in _fehlt_von(befunde, HYPO)
    befunde = pc.pruefe_leiste(samples, daten=_leiste_hypothek(mit_zinssatz=True))
    assert "Zinssatz" not in _fehlt_von(befunde, HYPO)


def test_gestrichener_pflichtwert_meldet_nichts(tmp_path, monkeypatch, modell):
    from scripts import pflichtfelder_check as pc

    _ohne_kk(monkeypatch)
    modell(lambda d: d["pflicht_je_typ"]["hypothek_zinsbestaetigung"].remove("Zinssatz"))
    samples = _samples(tmp_path, [(HYPO, "hypothek_zinsbestaetigung")])
    befunde = pc.pruefe_leiste(samples, daten=_leiste_hypothek())
    assert "Zinssatz" not in _fehlt_von(befunde, HYPO)


def test_lohnausweis_ohne_anstellung_bis(tmp_path, monkeypatch):
    from scripts import pflichtfelder_check as pc

    _ohne_kk(monkeypatch)
    samples = _samples(tmp_path, [(LOHN, "lohnausweis")])
    befunde = pc.pruefe_leiste(samples, daten=_leiste_lohn())
    fehlt = _fehlt_von(befunde, LOHN)
    assert "Anstellung bis" in fehlt
    assert "Nettolohn" not in fehlt and "Anstellung von" not in fehlt


def test_lohnausweis_anstellung_bis_gestrichen(tmp_path, monkeypatch, modell):
    from scripts import pflichtfelder_check as pc

    _ohne_kk(monkeypatch)
    modell(lambda d: d["pflicht_je_typ"]["lohnausweis"].remove("Anstellung bis"))
    samples = _samples(tmp_path, [(LOHN, "lohnausweis")])
    befunde = pc.pruefe_leiste(samples, daten=_leiste_lohn())
    assert "Anstellung bis" not in _fehlt_von(befunde, LOHN)


@pytest.mark.parametrize("aendern, erwartet", [
    (lambda d: d["pflicht_je_typ"]["bank_zinsausweis"].append("Steuerwert in Fremdwährung"),
     ("bank_zinsausweis", "Steuerwert in Fremdwährung")),
    (lambda d: d["pflicht_je_typ"].__setitem__("spendenquittung", ["Betrag"]),
     ("spendenquittung", "Betrag")),
])
def test_pflichtwert_ohne_zuordnung_ist_laut(tmp_path, monkeypatch, modell, aendern, erwartet):
    from scripts import pflichtfelder_check as pc

    _ohne_kk(monkeypatch)
    modell(aendern)
    samples = _samples(tmp_path, [(LOHN, "lohnausweis")])
    with pytest.raises(datenmodell.DatenmodellFehler) as e:
        pc.pruefe_leiste(samples, daten=_leiste_lohn())
    for teil in erwartet:
        assert teil in str(e.value)


def test_saeule_3a_maximum_aus_dem_datenmodell(tmp_path, monkeypatch, modell):
    from scripts import pflichtfelder_check as pc

    felder = {"einzahlung_betrag": {"value": "6'000.00"}}
    assert pc.realismus("saeule_3a", felder) == []
    leiste = {"formulare": [{"formular": "Bescheinigung der Vorsorgeeinrichtung", "bloecke": [
        {"werte": [{"bezeichnung": "Einzahlung Säule 3a", "betrag": "6'000.00",
                    "quelle": "3a.pdf"}]}]}]}
    monkeypatch.setattr(pc, "pruefe_extraktion", lambda s: [])
    assert pc.plausibilitaet(tmp_path, daten=leiste) == []

    modell(lambda d: d["personen"].__setitem__("saeule_3a_maximum", 5000))
    (befund,) = pc.realismus("saeule_3a", felder)
    assert "Maximum" in befund and "5'000" in befund
    assert {b for _, b in pc.plausibilitaet(tmp_path, daten=leiste)} == {
        "Säule 3a über dem Maximum"}


# --- A3: Ordner statt Lauf ----------------------------------------------------

def test_laufdokumente_aus_dem_ordner(tmp_path):
    from extractors.eintraege import _laufdokumente

    samples = _samples(tmp_path, [("b.pdf", "unknown")])
    nfd = unicodedata.normalize("NFD", "Löhne.pdf")
    ordner = _belege(tmp_path, [("a.pdf", "Konten"), ("x.pdf", "irrelevant"),
                                (nfd, "Einkuenfte")])
    assert _laufdokumente(samples, ordner) == {"a.pdf", unicodedata.normalize("NFC", nfd)}
    assert _laufdokumente(samples) == {"b.pdf"}
    with pytest.raises(FileNotFoundError):
        _laufdokumente(samples, tmp_path / "gibtsnicht")


def test_baue_verwaist_aus_dem_ordner(tmp_path, monkeypatch):
    from extractors import eintraege
    from scripts import build_review_html, etax_daten
    from scripts import export_extension as ex

    samples = _samples(tmp_path, [("weg.pdf", "kk_praemienbescheinigung")])
    ordner = _belege(tmp_path, [("neu.pdf", "Krankenkassen")])
    basis = {"zielwert": "Prämie Grundversicherung KVG", "ziffer": "15",
             "aussteller": "KK-A", "person": "kind_1", "herkunft": "regel"}
    zeilen = [dict(basis, pdf_name="neu.pdf", betrag="1'000.00"),
              dict(basis, pdf_name="weg.pdf", betrag="1'100.00")]
    monkeypatch.setattr(etax_daten, "_zeilen_der_tabelle", lambda s: zeilen)
    monkeypatch.setattr(build_review_html, "personennamen", lambda: {})
    monkeypatch.setattr(eintraege, "alle_eintraege", lambda *a, **k: ([], []))
    monkeypatch.setattr(eintraege, "tabellen_fuer", lambda *a, **k: {})

    def werte(daten):
        (form,) = [f for f in daten["formulare"] if f["formular"] == "Versicherungsprämien"]
        return {w["quelle"]: w for b in form["bloecke"] for w in b["werte"]}

    mit = werte(ex.baue(samples, pdf_dir=ordner))
    assert mit["neu.pdf"]["verwaist"] is False
    assert mit["weg.pdf"]["verwaist"] is True and mit["weg.pdf"]["warnung"]
    ohne = werte(ex.baue(samples))
    assert ohne["neu.pdf"]["verwaist"] is True
    assert ohne["weg.pdf"]["verwaist"] is False


def _leiste_gruen() -> dict:
    return {"formulare": [
        {"formular": "Wertschriftenverzeichnis (400)", "bloecke": [
            {"titel": "Musterbank · Sparkonto", "maske": "Bankkonto",
             "aussteller": "Musterbank", "person": "elternteil_1",
             "dokumente": [KONTO],
             "felder": [
                 {"titel": "Bank, Gesellschaft", "wert": "Musterbank", "herkunft": "beleg"},
                 {"titel": "Total Guthaben (Steuerwert 31.12.)", "wert": "1'234.50",
                  "herkunft": "beleg"}]}]}]}


def _vorbereiten(monkeypatch, leiste, befunde=()):
    from extractors import eintraege
    from scripts import entscheiden_seite
    from scripts import pflichtfelder_check as pc

    monkeypatch.setattr(pc, "_leiste", lambda *a, **k: leiste)
    if befunde is not None:
        def pruefe(*a, **k):
            return list(befunde)
        pruefe.hinweise = []
        pruefe.kk = {}
        monkeypatch.setattr(pc, "pruefe_leiste", pruefe)
    monkeypatch.setattr(pc, "plausibilitaet", lambda *a, **k: [])
    monkeypatch.setattr(pc, "pruefe_kette", lambda *a, **k: [])
    monkeypatch.setattr(entscheiden_seite, "offene_punkte", lambda *a, **k: ([], [], []))
    monkeypatch.setattr(eintraege, "tabellen_fuer", lambda *a, **k: {})


def _lauf(monkeypatch, samples, *extra) -> int:
    from scripts import pflichtfelder_check as pc

    monkeypatch.setattr(sys, "argv", ["x", "--samples", str(samples), *extra])
    return pc.main()


def test_inventur_gegen_den_ordner(tmp_path, monkeypatch, capsys):
    a, b, c = (f"Musterbank - Konto {x} - Muster 2025.pdf" for x in "ABC")
    samples = _samples(tmp_path, [(a, "bank_zinsausweis"), (b, "bank_zinsausweis")],
                       word=[(a, "Konten"), (b, "Konten")])
    ordner = _belege(tmp_path, [(a, "Konten"), (c, "Konten")])
    _vorbereiten(monkeypatch, _leiste_gruen())
    assert _lauf(monkeypatch, samples, "--pdf-dir", str(ordner)) == 1
    aus = capsys.readouterr().out
    assert ".pdf" not in aus
    for text in ("im Ordner, nicht eingelesen: 1", "eingelesen, nicht mehr im Ordner: 1"):
        (zeile,) = [z for z in aus.splitlines() if text in z]
        assert re.search(r"D\d\d", zeile), zeile


def test_ohne_ordner_ist_rot(tmp_path, monkeypatch, capsys):
    samples = _samples(tmp_path, [(KONTO, "bank_zinsausweis")], word=[(KONTO, "Konten")])
    _vorbereiten(monkeypatch, _leiste_gruen())
    assert _lauf(monkeypatch, samples) == 1
    assert "Kein PDF-Ordner" in capsys.readouterr().out

    assert _lauf(monkeypatch, samples, "--pdf-dir", str(tmp_path / "gibtsnicht")) == 1
    aus = capsys.readouterr().out
    assert "Kein PDF-Ordner" in aus and "gibtsnicht" not in aus

    assert _lauf(monkeypatch, samples, "--ohne-ordner") == 0
    assert "ohne Ordnervergleich" in capsys.readouterr().out


def test_ordner_gleich_ist_gruen(tmp_path, monkeypatch, capsys):
    samples = _samples(tmp_path, [(KONTO, "bank_zinsausweis")], word=[(KONTO, "Konten")])
    ordner = _belege(tmp_path, [(KONTO, "Konten")])
    _vorbereiten(monkeypatch, _leiste_gruen())
    assert _lauf(monkeypatch, samples, "--pdf-dir", str(ordner)) == 0
    assert "Dokumente im Ordner: 1" in capsys.readouterr().out


def test_datenmodell_fehler_in_main(tmp_path, monkeypatch, capsys):
    from scripts import pflichtfelder_check as pc

    samples = _samples(tmp_path, [(KONTO, "bank_zinsausweis")], word=[(KONTO, "Konten")])
    _vorbereiten(monkeypatch, _leiste_gruen(), befunde=None)

    def kaputt(*a, **k):
        raise datenmodell.DatenmodellFehler("pflicht_je_typ.x: ohne Zuordnung")
    monkeypatch.setattr(pc, "pruefe_leiste", kaputt)
    assert _lauf(monkeypatch, samples, "--ohne-ordner") == 1
    assert "Datenmodell: pflicht_je_typ.x" in capsys.readouterr().out


# --- A5: alle Befunde eines Dokuments in der Inventur ------------------------

def test_inventur_zeigt_alle_befunde_eines_dokuments(tmp_path, monkeypatch, capsys):
    samples = _samples(tmp_path, [(KONTO, "lohnausweis")], word=[(KONTO, "Konten")])
    ordner = _belege(tmp_path, [(KONTO, "Konten")])
    _vorbereiten(monkeypatch, _leiste_gruen(), befunde=[
        {"dokument": KONTO, "typ": "lohnausweis", "fehlt": ["Nettolohn"]},
        {"dokument": KONTO, "typ": "kk_person", "fehlt": ["Person"]}])
    _lauf(monkeypatch, samples, "--pdf-dir", str(ordner))
    aus = capsys.readouterr().out
    inventur = aus.split("Je Dokument (Typ")[1].split("Dokumente, die fehlen")[0]
    (zeile,) = [z for z in inventur.splitlines() if "D01" in z]
    assert "Nettolohn" in zeile and "Person" in zeile


# --- A6: Personen teilbar ----------------------------------------------------

def _leiste_personen() -> dict:
    return {"formulare": [{"formular": "Versicherungsprämien", "bloecke": [
        {"titel": "KK-A · Muster Hans", "aussteller": "KK-A", "person": "Muster Hans",
         "werte": [{"bezeichnung": "Prämie Grundversicherung KVG", "betrag": "1'000.00",
                    "quelle": KK, "herkunft": "regel"}]},
        {"titel": "KK-B · kind_1", "aussteller": "KK-B", "person": "kind_1",
         "werte": [{"bezeichnung": "Prämie Grundversicherung KVG", "betrag": "900.00",
                    "quelle": KK2, "herkunft": "regel"}]}]}]}


def test_praemie_ohne_person_nennt_keinen_namen(tmp_path, monkeypatch):
    from scripts import pflichtfelder_check as pc

    _ohne_kk(monkeypatch)
    samples = _samples(tmp_path, [(KK, "kk_praemienbescheinigung"),
                                  (KK2, "kk_praemienbescheinigung")])
    befunde = pc.pruefe_leiste(samples, daten=_leiste_personen())
    fehlt = _fehlt_von(befunde, KK)
    assert "Prämie ohne Person (‹Person›)" in fehlt
    assert not any("Hans" in x for b in befunde for x in b["fehlt"])


def test_main_teilbar_ohne_personennamen(tmp_path, monkeypatch, capsys):
    samples = _samples(tmp_path, [(KK, "kk_praemienbescheinigung"),
                                  (KK2, "kk_praemienbescheinigung")],
                       word=[(KK, "Krankenkassen"), (KK2, "Krankenkassen")])
    ordner = _belege(tmp_path, [(KK, "Krankenkassen"), (KK2, "Krankenkassen")])
    _vorbereiten(monkeypatch, _leiste_personen(), befunde=None)
    _lauf(monkeypatch, samples, "--pdf-dir", str(ordner))
    aus = capsys.readouterr().out
    assert "Hans" not in aus, [z for z in aus.splitlines() if "Hans" in z]
    je_person = next(z for z in aus.splitlines() if "Je Person" in z)
    assert "‹Person›" in je_person and "kind_1" in je_person


def test_person_marke():
    from scripts.pflichtfelder_check import _person_marke

    assert _person_marke("kind_1") == "kind_1"
    assert _person_marke("") == "leer"
    assert _person_marke("Familie") == "Familie"
    assert _person_marke("unbekannt/manuell") == "unbekannt/manuell"
    assert _person_marke("Muster Hans") == "‹Person›"
