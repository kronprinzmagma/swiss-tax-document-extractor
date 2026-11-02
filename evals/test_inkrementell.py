"""make productive inkrementell (261024-jga): unveränderte Dokumente werden
weder neu eingelesen noch neu extrahiert; ALLE erzwingt alles; nach einer
Code-Änderung läuft die Extraktion vollständig."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _einlesen(eingang: Path, ausgang: Path, *extra) -> str:
    r = subprocess.run([sys.executable, str(ROOT / "scripts/pdf_to_word_json.py"),
                        "--input", str(eingang), "--output", str(ausgang), *extra],
                       capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 0, r.stderr
    return r.stdout


def test_einlesen_ueberspringt_unveraenderte(tmp_path):
    eingang = tmp_path / "eingang" / "Konten"
    eingang.mkdir(parents=True)
    from reportlab.pdfgen import canvas
    for name in ("Bank A - Zinsausweis.pdf", "Bank B - Zinsausweis.pdf"):
        c = canvas.Canvas(str(eingang / name))
        c.drawString(72, 720, "Zinsausweis 2025  Saldo 31.12.2025  1'234.50")
        c.save()
    ausgang = tmp_path / "json"
    assert "übersprungen" not in _einlesen(tmp_path / "eingang", ausgang)
    zweiter = _einlesen(tmp_path / "eingang", ausgang)
    assert "(2 unverändert übersprungen)" in zweiter
    assert "übersprungen" not in _einlesen(tmp_path / "eingang", ausgang, "--alle")
    doc = json.loads(next(ausgang.glob("*.json")).read_text())
    assert doc["ordner"] == "Konten"


def _extraktion(samples: Path, monkeypatch, *extra) -> list[str]:
    from scripts import process_samples_full as psf

    aufgerufen = []

    def falsch(jp, model=None, **_):
        aufgerufen.append(jp.name)
        return {"pdf_name": json.loads(jp.read_text())["pdf_name"], "status": "ok",
                "belegtyp": "saeule_3a", "fields": []}

    monkeypatch.setattr(psf, "process_json_sample", falsch)
    monkeypatch.setattr(sys, "argv", ["x", "--samples", str(samples), *extra])
    psf.main()
    return aufgerufen


def test_extraktion_uebernimmt_unveraenderte(tmp_path, monkeypatch):
    for name, fp in (("a", "fp-a"), ("b", "fp-b")):
        (tmp_path / f"{name}.json").write_text(json.dumps(
            {"pdf_name": f"{name}.pdf", "pdf_fingerabdruck": fp, "pages": []}))
    assert sorted(_extraktion(tmp_path, monkeypatch)) == ["a.json", "b.json"]
    assert _extraktion(tmp_path, monkeypatch) == []
    # Ein PDF geändert → nur dieses neu.
    (tmp_path / "b.json").write_text(json.dumps(
        {"pdf_name": "b.pdf", "pdf_fingerabdruck": "fp-b2", "pages": []}))
    assert _extraktion(tmp_path, monkeypatch) == ["b.json"]
    assert len(_extraktion(tmp_path, monkeypatch, "--alle")) == 2


def test_extraktion_nach_codeaenderung_vollstaendig(tmp_path, monkeypatch):
    from scripts import process_samples_full as psf

    (tmp_path / "a.json").write_text(json.dumps(
        {"pdf_name": "a.pdf", "pdf_fingerabdruck": "fp-a", "pages": []}))
    _extraktion(tmp_path, monkeypatch)
    monkeypatch.setattr(psf, "code_stand", lambda modell=None: "anderer-stand")
    assert _extraktion(tmp_path, monkeypatch) == ["a.json"]


def test_verschobenes_dokument_wird_neu_extrahiert(tmp_path):
    from scripts.run_productive_local import _verschobene_vergessen

    (tmp_path / "a.json").write_text(json.dumps(
        {"pdf_name": "a.pdf", "ordner": "Wertschriften", "pages": []}))
    (tmp_path / "b.json").write_text(json.dumps(
        {"pdf_name": "b.pdf", "ordner": "Konten", "pages": []}))
    (tmp_path / "_results_full.json").write_text(json.dumps([
        {"pdf_name": "a.pdf", "ordner": "Konten"},
        {"pdf_name": "b.pdf", "ordner": "Konten"}]))
    assert _verschobene_vergessen(tmp_path) == 1
    rest = json.loads((tmp_path / "_results_full.json").read_text())
    assert [e["pdf_name"] for e in rest] == ["b.pdf"]


# --- 261025-hbv: ALLE=1 baut die Kopie voll neu (B8c), reextract mit
# Stempel (B9). Alle Werte erfunden.

def test_korpus_nachziehen_mit_und_ohne_alle(tmp_path, monkeypatch):
    from scripts import run_productive_local as rpl

    aufrufe: list[list[str]] = []
    monkeypatch.setattr(rpl, "_run", lambda cmd: (aufrufe.append(cmd), (0, ""))[1])
    rpl._korpus_nachziehen("py", tmp_path / "eingang", tmp_path / "korpus", True)
    anon, extr = aufrufe
    assert anon[1].endswith("anonymize_belege.py")
    assert "--prune" in anon and "--inkrementell" not in anon
    assert extr[1].endswith("process_samples_full.py") and "--alle" in extr

    aufrufe.clear()
    rpl._korpus_nachziehen("py", tmp_path / "eingang", tmp_path / "korpus", False)
    anon, extr = aufrufe
    assert "--inkrementell" in anon and "--prune" in anon
    assert "--alle" not in extr


def _extraktion_mit_vorgabe(samples: Path, monkeypatch, *extra) -> list[tuple]:
    """Wie _extraktion, aber zeichnet die Belegtyp-Vorgabe mit auf."""
    from scripts import process_samples_full as psf

    aufgerufen = []

    def falsch(jp, model=None, belegtyp_override=None):
        aufgerufen.append((jp.name, belegtyp_override))
        return {"pdf_name": json.loads(jp.read_text())["pdf_name"], "status": "ok",
                "belegtyp": belegtyp_override or "unknown", "fields": []}

    monkeypatch.setattr(psf, "process_json_sample", falsch)
    monkeypatch.setattr(sys, "argv", ["x", "--samples", str(samples), *extra])
    psf.main()
    return aufgerufen


def _ergebnis(samples: Path) -> dict:
    (e,) = json.loads((samples / "_results_full.json").read_text())
    return e


def test_reextract_vorgabe_ueberlebt_den_naechsten_lauf(tmp_path, monkeypatch, capsys):
    from scripts import process_samples_full as psf
    from scripts import reextract

    (tmp_path / "a.json").write_text(json.dumps(
        {"pdf_name": "Muster - Praemien.pdf", "pdf_fingerabdruck": "fp-a", "pages": []}))
    assert _extraktion_mit_vorgabe(tmp_path, monkeypatch) == [("a.json", None)]

    # Der Mensch gibt den Typ vor.
    monkeypatch.setattr(sys, "argv", ["x", "--samples", str(tmp_path),
                                      "--pdf", "Muster - Praemien.pdf",
                                      "--belegtyp", "kk_praemienbescheinigung"])
    gesehen = []

    def falsch(jp, model=None, belegtyp_override=None):
        gesehen.append(belegtyp_override)
        return {"pdf_name": "Muster - Praemien.pdf", "status": "ok",
                "belegtyp": belegtyp_override, "fields": []}

    monkeypatch.setattr(psf, "process_json_sample", falsch)
    assert reextract.main() == 0
    assert gesehen == ["kk_praemienbescheinigung"]
    e = _ergebnis(tmp_path)
    assert e["pdf_fingerabdruck"] == "fp-a"
    assert e["code_stand"] == psf.code_stand(None)
    assert e["belegtyp_override"] == "kk_praemienbescheinigung"

    # Nächster Lauf: unverändert übernommen, Typ bleibt.
    assert _extraktion_mit_vorgabe(tmp_path, monkeypatch) == []
    assert _ergebnis(tmp_path)["belegtyp"] == "kk_praemienbescheinigung"

    # Code geändert: neu extrahiert — mit der Vorgabe.
    monkeypatch.setattr(psf, "code_stand", lambda modell=None: "anderer-stand")
    assert _extraktion_mit_vorgabe(tmp_path, monkeypatch) == [
        ("a.json", "kk_praemienbescheinigung")]
    e = _ergebnis(tmp_path)
    assert e["belegtyp"] == "kk_praemienbescheinigung"
    assert e["belegtyp_override"] == "kk_praemienbescheinigung"

    # ALLE: ebenso.
    assert _extraktion_mit_vorgabe(tmp_path, monkeypatch, "--alle") == [
        ("a.json", "kk_praemienbescheinigung")]
    assert _ergebnis(tmp_path)["belegtyp_override"] == "kk_praemienbescheinigung"

    # PDF geändert: die Vorgabe verfällt — gemeldet, ohne Namen.
    (tmp_path / "a.json").write_text(json.dumps(
        {"pdf_name": "Muster - Praemien.pdf", "pdf_fingerabdruck": "fp-a2", "pages": []}))
    capsys.readouterr()
    assert _extraktion_mit_vorgabe(tmp_path, monkeypatch) == [("a.json", None)]
    aus = capsys.readouterr().out
    assert "1 Belegtyp-Vorgabe verfallen" in aus
    assert "Muster" not in aus
    assert "belegtyp_override" not in _ergebnis(tmp_path)
