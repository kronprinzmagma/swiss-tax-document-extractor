"""Stand-Seite und Terminal: eine Regel, eine Rechnung, kein Ordner-Cache
(261025-jm6, A4).

* A4a — Die Stand-Seite zählte ein fehlendes Dokument über den Hinweistext
  („nicht mehr im Ordner"), make pruefen über das blosse verwaist-Flag. Ein
  „behalten" zählte im Terminal weiter als fehlend, auf der Seite nicht.
* A4b — /stand rechnete die Leiste dreimal (baue, pruefe_leiste,
  offene_punkte).
* A4c — _ordner_von hielt einen prozessweiten Cache: ein zweiter Ordner oder
  ein neu eingelesenes Dokument sah den alten Stand.

Alle Werte erfunden.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

KK = "KK-A - Praemien - Muster 2024.pdf"


def _samples(tmp_path: Path, ergebnis: list[tuple[str, str]] = ()) -> Path:
    samples = tmp_path / "json"
    samples.mkdir()
    (samples / "_results_full.json").write_text(
        json.dumps([{"pdf_name": n, "belegtyp": t, "fields": []} for n, t in ergebnis]),
        encoding="utf-8")
    return samples


def _leiste(entschieden: str | None, hinweis: str) -> dict:
    w = {"bezeichnung": "Prämie Grundversicherung KVG", "betrag": "1'000.00",
         "quelle": KK, "herkunft": "ablage", "verwaist": True, "hinweis": hinweis,
         "warnung": entschieden is None}
    if entschieden:
        w["entschieden"] = entschieden
    return {"formulare": [{"formular": "Versicherungsprämien", "bloecke": [
        {"titel": "KK-A · kind_1", "aussteller": "KK-A", "person": "kind_1",
         "werte": [w]}]}], "entscheide_ohne_wert": []}


def _patchen(monkeypatch, leiste):
    from extractors import eintraege
    from scripts import build_review_html
    from scripts import pflichtfelder_check as pc

    monkeypatch.setattr(pc, "_leiste", lambda *a, **k: leiste)

    def pruefe(*a, **k):
        return []
    pruefe.hinweise, pruefe.kk = [], {}
    monkeypatch.setattr(pc, "pruefe_leiste", pruefe)
    monkeypatch.setattr(pc, "plausibilitaet", lambda *a, **k: [])
    monkeypatch.setattr(pc, "pruefe_kette", lambda *a, **k: [])
    monkeypatch.setattr(eintraege, "tabellen_fuer", lambda *a, **k: {})
    monkeypatch.setattr(build_review_html, "personennamen", lambda: {})


def _terminal(monkeypatch, capsys, tmp_path, samples) -> str:
    from scripts import pflichtfelder_check as pc

    ordner = tmp_path / "pdfs"
    (ordner / "Krankenkassen").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(sys, "argv", ["x", "--samples", str(samples),
                                      "--pdf-dir", str(ordner)])
    pc.main()
    return capsys.readouterr().out


def test_behalten_zaehlt_nirgends_als_fehlend(tmp_path, monkeypatch, capsys):
    from scripts import stand_seite

    samples = _samples(tmp_path, [(KK, "kk_praemienbescheinigung")])
    _patchen(monkeypatch, _leiste("behalten", "einziger Beleg: Dokument nicht mehr im "
                                               "Ordner — PDF wieder ablegen — von dir behalten"))
    assert "alle Dokumente im Ordner" in stand_seite.seite(samples)
    aus = _terminal(monkeypatch, capsys, tmp_path, samples)
    assert "Dokumente, die fehlen (Werte nur aus der Durchsicht): 0" in aus


def test_nicht_entschieden_zaehlt_ueberall_ohne_textvergleich(tmp_path, monkeypatch, capsys):
    from scripts import stand_seite

    samples = _samples(tmp_path, [(KK, "kk_praemienbescheinigung")])
    _patchen(monkeypatch, _leiste(None, "bitte prüfen"))
    assert "1 Dokument(e) fehlen im Ordner" in stand_seite.seite(samples)
    aus = _terminal(monkeypatch, capsys, tmp_path, samples)
    assert "Dokumente, die fehlen (Werte nur aus der Durchsicht): 1" in aus


def test_dokument_fehlt():
    from scripts.export_extension import dokument_fehlt

    assert dokument_fehlt({"verwaist": True})
    assert not dokument_fehlt({"verwaist": True, "entschieden": "behalten"})
    assert not dokument_fehlt({"verwaist": False})
    assert not dokument_fehlt({})


def test_stand_rechnet_die_leiste_einmal(tmp_path, monkeypatch):
    from scripts import build_review_html, export_extension, stand_seite
    from scripts import pflichtfelder_check as pc

    samples = _samples(tmp_path)
    aufrufe = []

    def leiste(*a, **k):
        aufrufe.append(k)
        return {"formulare": [{"formular": "Versicherungsprämien", "bloecke": [
            {"titel": "KK-A · kind_1", "aussteller": "KK-A", "person": "kind_1",
             "werte": [{"bezeichnung": "Prämie Grundversicherung KVG",
                        "betrag": "1'000.00", "quelle": KK, "herkunft": "regel"}]}]}],
                "entscheide_ohne_wert": []}

    def nie(*a, **k):
        raise AssertionError("baue darf hier nicht laufen — die Leiste kommt aus _leiste")
    monkeypatch.setattr(pc, "_leiste", leiste)
    monkeypatch.setattr(export_extension, "baue", nie)
    monkeypatch.setattr(build_review_html, "personennamen", lambda: {})
    html = stand_seite.seite(samples, pdf_dir=None)
    assert "Stand der Steuerwerte" in html
    assert len(aufrufe) == 1 and "pdf_dir" in aufrufe[0]


def test_ordner_von_ohne_prozessweiten_cache(tmp_path):
    from scripts.pflichtfelder_check import _ordner_von

    def samples(name: str, ordner: str) -> Path:
        s = tmp_path / name
        s.mkdir(exist_ok=True)
        (s / "doc0.json").write_text(
            json.dumps({"pdf_name": "x.pdf", "ordner": ordner, "pages": []}), encoding="utf-8")
        return s

    a = samples("a", "Konten")
    b = samples("b", "Krankenkassen")
    assert _ordner_von(a, "x.pdf") == "Konten"
    assert _ordner_von(b, "x.pdf") == "Krankenkassen"
    samples("a", "Wertschriften")
    assert _ordner_von(a, "x.pdf") == "Wertschriften"
