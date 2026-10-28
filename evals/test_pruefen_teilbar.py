"""make pruefen: teilbar in jedem Abschnitt und fail-closed (261025-hbv, B1/B2).

Die teilbare Fassung verlässt den Rechner (Chat). Darin dürfen weder
Beträge noch Dokument- noch Bank-/Gläubigernamen stehen — in keinem
Abschnitt. Und ein Lauf ohne Dokumente, mit ungleicher Inventur oder mit
offenen Entscheiden darf nicht „grün" enden.

Alle Werte erfunden.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

KONTO = "Musterbank - Kontoauszug - Muster 2025.pdf"
KK = "KK-A - Praemien - Muster 2025.pdf"


def _korpus(tmp_path: Path, word: list[tuple[str, str]], ergebnis: list[tuple[str, str]]) -> Path:
    samples = tmp_path / "json"
    samples.mkdir()
    # Der echte Ordner (261025-jm6, A3): je Word-JSON eine leere PDF-Attrappe.
    pdfs = tmp_path / "belege"
    pdfs.mkdir()
    for i, (name, ordner) in enumerate(word):
        (samples / f"doc{i}.json").write_text(
            json.dumps({"pdf_name": name, "ordner": ordner, "pages": []}), encoding="utf-8")
        (pdfs / ordner).mkdir(exist_ok=True)
        (pdfs / ordner / name).write_bytes(b"")
    (samples / "_results_full.json").write_text(
        json.dumps([{"pdf_name": n, "belegtyp": t, "fields": []} for n, t in ergebnis]),
        encoding="utf-8")
    return samples


def _leiste_mit_lecks() -> dict:
    return {"formulare": [
        {"formular": "Wertschriftenverzeichnis (400)", "bloecke": [
            {"titel": "Musterbank · Privatkonto", "maske": "Bankkonto",
             "aussteller": "Musterbank", "person": "elternteil_1",
             "dokumente": ["Musterbank - Altauszug - Muster 2024.pdf"], "verwaist": True,
             "felder": [
                 {"titel": "Bank, Gesellschaft", "wert": "Musterbank", "herkunft": "beleg"},
                 {"titel": "Konto-/Depot-Nr.", "wert": "", "warnung": True,
                  "hinweis": "Musterbank nennt keine Nummer auf Seite 2"},
                 {"titel": "Total Guthaben (Steuerwert 31.12.)", "wert": "1'234.50",
                  "herkunft": "beleg", "warnung": True,
                  "hinweis": "Musterbank Saldo 1'234.50 weicht ab"}]},
            {"titel": "Musterbank · Sparkonto", "maske": "Bankkonto",
             "aussteller": "Musterbank", "person": "elternteil_1",
             "dokumente": [KONTO],
             "felder": [
                 {"titel": "Bank, Gesellschaft", "wert": "Musterbank", "herkunft": "beleg"},
                 {"titel": "Konto-/Depot-Nr.", "wert": "", "warnung": True,
                  "hinweis": "keine Nummer gefunden"},
                 {"titel": "Total Guthaben (Steuerwert 31.12.)", "wert": "1'234.50",
                  "herkunft": "beleg"}]}]},
        {"formular": "Schuldenverzeichnis", "bloecke": [
            {"titel": "Musterbank · Hypothek", "maske": "Schuld", "person": "familie",
             "dokumente": [KONTO],
             "felder": [
                 {"titel": "Name, Vorname und Adresse",
                  "wert": "Musterbank, Bahnhofstrasse 1, 8000 Musterhausen", "herkunft": "beleg"},
                 {"titel": "Schuld am 31.12.", "wert": "250'000.00", "herkunft": "ablage",
                  "warnung": True,
                  "hinweis": "Beleg sagt 260'000.00 — Musterbank, Bahnhofstrasse 1"},
                 {"titel": "Schuldzinsen", "wert": "1'500.00", "herkunft": "beleg"}]}]},
        {"formular": "Versicherungsprämien", "bloecke": [
            {"titel": "KK-A · kind_1", "aussteller": "KK-A", "person": "kind_1",
             "werte": [{"bezeichnung": "Prämie KVG", "betrag": "1'111.10", "quelle": KK,
                        "herkunft": "modell", "warnung": True,
                        "hinweis": "Musterbank? 1'111.10"}]}]},
    ]}


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


def _vorbereiten(monkeypatch, tmp_path, samples, leiste, befunde, offen=()):
    from extractors import eintraege, rohtabelle
    from scripts import entscheiden_seite
    from scripts import pflichtfelder_check as pc

    monkeypatch.setattr(pc, "_leiste", lambda *a, **k: leiste)

    def pruefe(*a, **k):
        return befunde
    pruefe.hinweise = ["Musterbank meldet 1'234.50 doppelt"] if befunde else []
    pruefe.kk = {"kind_1": {"kvg": 1, "vvg": 0, "verbilligung": 0, "dokumente": {KK},
                            "nur_durchsicht": ["Prämie Musterbank 2'222.20"]}} if befunde else {}
    monkeypatch.setattr(pc, "pruefe_leiste", pruefe)
    monkeypatch.setattr(pc, "plausibilitaet",
                        lambda *a, **k: [(KONTO, "Musterbank: Zins 99.50 über 5 %")]
                        if befunde else [])
    monkeypatch.setattr(pc, "pruefe_kette", lambda *a, **k: [])
    monkeypatch.setattr(entscheiden_seite, "offene_punkte",
                        lambda *a, **k: (list(offen), [], []))

    def zeile(y, *texte):
        cs = [rohtabelle.Zelle(t, 50 + 100 * i, 90 + 100 * i) for i, t in enumerate(texte)]
        return rohtabelle.Zeile(1, y, 50, cs, (50, y - 4, 300, y + 4))
    tab = rohtabelle.Rohtabelle(KONTO, [zeile(100, "Musterbank Konto", "1234 5678")])
    monkeypatch.setattr(eintraege, "tabellen_fuer", lambda s, t: {KONTO: tab})


def _lauf(monkeypatch, samples, *extra) -> int:
    from scripts import pflichtfelder_check as pc

    monkeypatch.setattr(pc._ordner_von, "_cache", None, raising=False)
    monkeypatch.setattr(sys, "argv", ["x", "--samples", str(samples),
                                      "--pdf-dir", str(samples.parent / "belege"), *extra])
    return pc.main()


BETRAG = re.compile(r"\d[.,]\d{2}")


def test_teilbar_ohne_betrag_dokument_und_bankname(tmp_path, monkeypatch, capsys):
    samples = _korpus(tmp_path, [(KONTO, "Konten"), (KK, "Krankenkasse")],
                      [(KONTO, "bank_zinsausweis"), (KK, "kk_praemienbescheinigung")])
    befunde = [{"dokument": KONTO, "typ": "bank_zinsausweis",
                "fehlt": ["Saldo 1234.50 aus dem Beleg fehlt in der Leiste",
                          "Schuld/Zins 250000.00/1500.00 nicht als Paar in der Leiste"]},
               {"dokument": KK, "typ": "kk_praemienbescheinigung", "fehlt": []}]
    _vorbereiten(monkeypatch, tmp_path, samples, _leiste_mit_lecks(), befunde)

    _lauf(monkeypatch, samples)
    aus = capsys.readouterr().out
    assert not BETRAG.findall(aus), [z for z in aus.splitlines() if BETRAG.search(z)]
    for verboten in ("Musterbank", "Bahnhofstrasse", "Musterhausen", "Kontoauszug",
                     "Altauszug", ".pdf"):
        assert verboten not in aus, [z for z in aus.splitlines() if verboten in z]
    # Kürzel- und Namensliste liegt nur lokal.
    assert (tmp_path / "pruefen_namen.txt").exists()

    # Die volle Fassung bleibt unmaskiert — die Maske ist selektiv.
    _lauf(monkeypatch, samples, "--voll")
    voll = capsys.readouterr().out
    assert "1234.50" in voll
    assert "Musterbank" in voll


def test_leerer_lauf_ist_nicht_gruen(tmp_path, monkeypatch, capsys):
    samples = _korpus(tmp_path, [], [])
    _vorbereiten(monkeypatch, tmp_path, samples, {"formulare": []}, [])
    assert _lauf(monkeypatch, samples) == 1
    assert "0 Dokumente" in capsys.readouterr().out


def test_word_json_ohne_ergebnis_ist_nicht_gruen(tmp_path, monkeypatch, capsys):
    samples = _korpus(tmp_path, [(KONTO, "Konten"), (KK, "Krankenkasse")],
                      [(KONTO, "bank_zinsausweis")])
    _vorbereiten(monkeypatch, tmp_path, samples, _leiste_gruen(),
                 [])
    from scripts import pflichtfelder_check as pc
    monkeypatch.setattr(pc, "pruefe_leiste",
                        lambda *a, **k: [{"dokument": KONTO, "typ": "bank_zinsausweis",
                                          "fehlt": []}])
    assert _lauf(monkeypatch, samples) == 1
    aus = capsys.readouterr().out
    assert "ungleich" in aus
    assert "ohne Extraktionsergebnis: 1" in aus
    zeile = next(z for z in aus.splitlines() if "ohne Extraktionsergebnis" in z)
    assert re.search(r"D\d\d", zeile) and "KK-A" not in zeile and "nicht im Ordner" not in zeile


@pytest.mark.parametrize("offen, erwartet", [([{"punkt": "x"}], 1), ([], 0)])
def test_offene_entscheide_halten_den_lauf_rot(tmp_path, monkeypatch, capsys, offen, erwartet):
    samples = _korpus(tmp_path, [(KONTO, "Konten")], [(KONTO, "bank_zinsausweis")])
    _vorbereiten(monkeypatch, tmp_path, samples, _leiste_gruen(), [], offen=offen)
    from scripts import pflichtfelder_check as pc
    monkeypatch.setattr(pc, "pruefe_leiste",
                        lambda *a, **k: [{"dokument": KONTO, "typ": "bank_zinsausweis",
                                          "fehlt": []}])
    assert _lauf(monkeypatch, samples) == erwartet
    assert f"Zu entscheiden in der Review: {len(offen)} offen" in capsys.readouterr().out
