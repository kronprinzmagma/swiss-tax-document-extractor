"""Datenmodell als Vorgabe, inkrementelle Kopie, Diagnose „Form statt Wert"
(261024-jga). Alle Werte erfunden."""
from __future__ import annotations

import json
import sys
from decimal import Decimal
from pathlib import Path


def test_datenmodell_liefert_die_vorgaben():
    from extractors import datenmodell

    assert datenmodell.kk_dokumente_je_rolle() == {
        "elternteil_1": 2, "elternteil_2": 2, "kind_1": 1, "kind_2": 1}
    assert datenmodell.ordner_typen()["krankenkasse"] == datenmodell.ordner_typen()["krankenkassen"]
    for typ in ("lohnausweis", "bank_zinsausweis", "wertschriftenverzeichnis",
                "hypothek_zinsbestaetigung", "kk_praemienbescheinigung", "saeule_3a",
                "kinderbetreuung"):
        assert datenmodell.pflicht_je_typ()[typ]


def test_belegordner_liest_das_datenmodell():
    from extractors.belegordner import erlaubte_typen

    assert erlaubte_typen("Konten") == ("bank_zinsausweis", "hypothek_zinsbestaetigung")
    assert erlaubte_typen("irgendwas") is None


def _pdf(pfad: Path, text: str) -> Path:
    from reportlab.pdfgen import canvas
    pfad.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(pfad))
    c.drawString(72, 720, text)
    c.save()
    return pfad


def test_kopie_inkrementell_ohne_ueberschreiben(tmp_path, monkeypatch):
    from scripts import anonymize_belege as ab

    eingang, korpus = tmp_path / "eingang", tmp_path / "korpus"
    _pdf(eingang / "Konten" / "a.pdf", "A")
    _pdf(eingang / "Konten" / "b.pdf", "B")
    aufrufe = []

    def falsch(pdf, output, family=None, secrets=None):
        # Beide heissen nach dem Anonymisieren gleich — wie im echten Lauf.
        aufrufe.append(pdf.name)
        stamm, n = "BANK-A - Ausweis", 2
        while stamm in ab._GESCHRIEBEN:
            stamm = f"BANK-A - Ausweis-{n}"
            n += 1
        ab._GESCHRIEBEN.add(stamm)
        ziel = output / f"{stamm}.json"
        ziel.write_text(json.dumps({"pdf_name": f"{stamm}.pdf", "pages": []}))
        return ziel

    def lauf():
        ab._GESCHRIEBEN.clear()
        monkeypatch.setattr(ab, "anonymize_one", falsch)
        monkeypatch.setattr(ab, "load_family", lambda p: None)
        monkeypatch.setattr(ab, "load_privacy_secrets", lambda p: {})
        monkeypatch.setattr(sys, "argv", ["x", "--input", str(eingang), "--output", str(korpus),
                                          "--inkrementell", "--prune"])
        ab.main()

    lauf()
    assert sorted(aufrufe) == ["a.pdf", "b.pdf"]
    kopien = sorted(p.name for p in korpus.glob("BANK-A*.json"))
    assert kopien == ["BANK-A - Ausweis-2.json", "BANK-A - Ausweis.json"]
    index = json.loads((korpus / "_korpus_index.json").read_text())
    assert all("a.pdf" not in k and "b.pdf" not in k for k in index)  # keine Namen

    aufrufe.clear()
    lauf()
    assert aufrufe == []                       # nichts Unverändertes neu

    _pdf(eingang / "Konten" / "c.pdf", "C")
    (eingang / "Konten" / "a.pdf").unlink()
    aufrufe.clear()
    lauf()
    assert aufrufe == ["c.pdf"]                # nur das neue
    namen = sorted(p.name for p in korpus.glob("BANK-A*.json"))
    assert len(namen) == 2                     # a ist weg, b und c bleiben
    doc = json.loads((korpus / f"{json.loads((korpus / '_korpus_index.json').read_text())[ab._korpus_schluessel(eingang / 'Konten' / 'c.pdf')]}.json").read_text())
    assert doc["ordner"] == "Konten" and doc["pdf_fingerabdruck"]


def test_diagnose_zeigt_form_und_gleiche_betraege(monkeypatch, capsys):
    from scripts import pflichtfelder_check as pc

    leiste = {"formulare": [{"formular": "Wertschriftenverzeichnis (400)", "bloecke": [
        {"titel": "K", "dokumente": ["a.pdf"], "felder": [
            {"titel": "Total Guthaben (Steuerwert 31.12.)", "wert": "1'234.50", "herkunft": "beleg"},
            {"titel": "Konto-/Depot-Nr.", "wert": "CH9300762011623852957", "herkunft": "beleg"}]},
        {"titel": "D", "dokumente": ["b.pdf"], "felder": [
            {"titel": "Total Steuerwert", "wert": "1'234.50", "herkunft": "beleg"}]}]}]}
    monkeypatch.setattr(pc, "_leiste", lambda s: leiste)
    pc._diagnose(Path("."), lambda d: {"a.pdf": "D01 x", "b.pdf": "D02 y"}[d])
    aus = capsys.readouterr().out
    assert "9'999.99" in aus and "1'234" not in aus
    assert "IBAN gültig" in aus and "CH93" not in aus
    assert "gleicher Betrag wie D02" in aus
    assert Decimal("1234.50")  # nur Form, nie der Betrag


def test_plausibilitaet_schlaegt_an(monkeypatch):
    from scripts import pflichtfelder_check as pc

    leiste = {"formulare": [
        {"formular": "Wertschriftenverzeichnis (400)", "bloecke": [
            {"maske": "Bankkonto", "dokumente": ["k.pdf"], "felder": [
                {"titel": "Total Guthaben (Steuerwert 31.12.)", "wert": "1'000.00"},
                {"titel": "Ertrag ohne Verrechnungssteuer", "wert": "80.00"}]},
            {"maske": "Steuerauszug/Depot", "dokumente": ["d.pdf"], "felder": [
                {"titel": "Total Steuerwert", "wert": "1'000.00"},
                {"titel": "Ertrag mit Verrechnungssteuer", "wert": "200.00"}]}]},
        {"formular": "Bescheinigung der Vorsorgeeinrichtung", "bloecke": [
            {"werte": [{"bezeichnung": "Einzahlung Säule 3a", "betrag": "9'000.00",
                        "quelle": "3a.pdf"}]}]}]}
    monkeypatch.setattr(pc, "_leiste", lambda s: leiste)
    monkeypatch.setattr(pc, "pruefe_extraktion", lambda s: [])
    befunde = {b for _, b in pc.plausibilitaet(Path("."))}
    assert befunde == {"Konto: Zins über 5 % des Saldos",
                       "Depot: Ertrag über 15 % des Steuerwerts",
                       "Säule 3a über dem Maximum"}


def test_durchsicht_praemie_wird_gegen_das_dokument_gehalten(monkeypatch):
    from extractors import eintraege, rohtabelle
    from scripts import export_extension as ex

    def zeile(y, *texte):
        cs = [rohtabelle.Zelle(t, 50 + 100 * i, 90 + 100 * i) for i, t in enumerate(texte)]
        return rohtabelle.Zeile(1, y, 50, cs, (50, y - 4, 300, y + 4))

    tab = rohtabelle.Rohtabelle("k.pdf", [zeile(100, "Prämie"), zeile(115, "Total CHF", "321.00")])
    monkeypatch.setattr(eintraege, "tabellen_fuer", lambda s, t: {"k.pdf": tab})

    def formulare(betrag):
        return [{"formular": "Versicherungsprämien", "bloecke": [{"werte": [
            {"bezeichnung": "Prämie Total", "betrag": betrag, "quelle": "k.pdf",
             "herkunft": "mensch"}]}]}]

    gut = formulare("321.00")
    ex._bestaetigen(Path("."), gut)
    assert not gut[0]["bloecke"][0]["werte"][0].get("warnung")
    falsch = formulare("312.00")
    ex._bestaetigen(Path("."), falsch)
    assert falsch[0]["bloecke"][0]["werte"][0]["warnung"]


# --- 261025-hbv: kein Namens-Mapping ohne Truth-Dateien (B8a), Stand des
# Anonymisierers im Index (B8b). Alle Werte erfunden.

def _anon_lauf(monkeypatch, eingang, korpus, aufrufe, *extra):
    from scripts import anonymize_belege as ab

    def falsch(pdf, output, family=None, secrets=None):
        aufrufe.append(pdf.name)
        stamm, n = "BANK-A - Ausweis", 2
        while stamm in ab._GESCHRIEBEN:
            stamm = f"BANK-A - Ausweis-{n}"
            n += 1
        ab._GESCHRIEBEN.add(stamm)
        ziel = output / f"{stamm}.json"
        ziel.write_text(json.dumps({"pdf_name": f"{stamm}.pdf", "pages": []}))
        return ziel

    ab._GESCHRIEBEN.clear()
    monkeypatch.setattr(ab, "anonymize_one", falsch)
    monkeypatch.setattr(ab, "load_family", lambda p: None)
    monkeypatch.setattr(ab, "load_privacy_secrets", lambda p: {})
    monkeypatch.setattr(sys, "argv", ["x", "--input", str(eingang), "--output", str(korpus),
                                      "--inkrementell", "--prune", *extra])
    ab.main()


def test_ohne_truth_dateien_kein_namens_mapping(tmp_path, monkeypatch, capsys):
    eingang, korpus = tmp_path / "eingang", tmp_path / "korpus"
    _pdf(eingang / "Konten" / "a.pdf", "A")
    korpus.mkdir()
    (korpus / "anon_truth_mapping.json").write_text(
        json.dumps({"BANK-A - Ausweis": "geheim_original"}))
    _anon_lauf(monkeypatch, eingang, korpus, [])
    aus = capsys.readouterr()
    assert not (korpus / "anon_truth_mapping.json").exists()
    assert "anon_truth_mapping.json entfernt" in aus.out
    assert "geheim_original" not in aus.out + aus.err
    assert not list(korpus.glob("anon_truth_mapping*"))      # keine Sicherungskopie

    # Mit einer Truth-Datei wird das Mapping weiter geschrieben.
    (korpus / "BANK-A - Ausweis.truth.json").write_text(json.dumps({"x": "1.00"}))
    _anon_lauf(monkeypatch, eingang, korpus, [])
    (korpus / "_korpus_index.json").unlink()                 # erzwingt Neu-Anonymisierung
    _anon_lauf(monkeypatch, eingang, korpus, [])
    assert json.loads((korpus / "anon_truth_mapping.json").read_text()) == {
        "BANK-A - Ausweis": "a"}


def test_geaenderter_anonymisierer_anonymisiert_alles_neu(tmp_path, monkeypatch, capsys):
    from scripts import anonymize_belege as ab

    eingang, korpus = tmp_path / "eingang", tmp_path / "korpus"
    _pdf(eingang / "Konten" / "a.pdf", "A")
    _pdf(eingang / "Konten" / "b.pdf", "B")
    aufrufe: list[str] = []

    monkeypatch.setattr(ab, "_anonymisierer_stand", lambda: "s1")
    _anon_lauf(monkeypatch, eingang, korpus, aufrufe)
    assert sorted(aufrufe) == ["a.pdf", "b.pdf"]
    assert json.loads((korpus / "_korpus_index.json").read_text())["anonymisierer_stand"] == "s1"
    aufrufe.clear()
    _anon_lauf(monkeypatch, eingang, korpus, aufrufe)
    assert aufrufe == []

    monkeypatch.setattr(ab, "_anonymisierer_stand", lambda: "s2")
    capsys.readouterr()
    _anon_lauf(monkeypatch, eingang, korpus, aufrufe)
    assert sorted(aufrufe) == ["a.pdf", "b.pdf"]
    assert "Anonymisierer-Stand unbekannt oder geändert" in capsys.readouterr().out
    # Dieselben Namen überschrieben, keine „-3"-Varianten daneben.
    assert sorted(p.name for p in korpus.glob("BANK-A*.json")) == [
        "BANK-A - Ausweis-2.json", "BANK-A - Ausweis.json"]

    # Index im alten Format (ohne Stand): gilt als abweichend.
    index = json.loads((korpus / "_korpus_index.json").read_text())
    index.pop("anonymisierer_stand")
    (korpus / "_korpus_index.json").write_text(json.dumps(index))
    aufrufe.clear()
    _anon_lauf(monkeypatch, eingang, korpus, aufrufe)
    assert sorted(aufrufe) == ["a.pdf", "b.pdf"]


def test_voller_lauf_schreibt_den_index_mit_stand(tmp_path, monkeypatch):
    from scripts import anonymize_belege as ab

    eingang, korpus = tmp_path / "eingang", tmp_path / "korpus"
    _pdf(eingang / "Konten" / "a.pdf", "A")
    monkeypatch.setattr(ab, "_anonymisierer_stand", lambda: "s1")
    monkeypatch.setattr(ab, "anonymize_one", lambda pdf, output, family=None, secrets=None: (
        (output / "BANK-A - Ausweis.json").write_text(json.dumps({"pdf_name": "x", "pages": []}))
        and output / "BANK-A - Ausweis.json"))
    monkeypatch.setattr(ab, "load_family", lambda p: None)
    monkeypatch.setattr(ab, "load_privacy_secrets", lambda p: {})
    monkeypatch.setattr(sys, "argv", ["x", "--input", str(eingang), "--output", str(korpus),
                                      "--prune"])
    ab.main()
    index = json.loads((korpus / "_korpus_index.json").read_text())
    assert index["anonymisierer_stand"] == "s1" and "BANK-A - Ausweis" in index.values()
