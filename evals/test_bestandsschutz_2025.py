"""Bestandsschutz 2025: keine Umstellung verliert etwas vom Bestand (261025-kme).
Alle Werte erfunden.

der Anwender' Vorgabe (2026-10-25): die Daten des Steuerjahrs 2025 bleiben bestehen.
Je Umstellung ein Test mit einem tmp-Bestand im alten Format; zugesichert
wird jedes Mal „nichts geht verloren" — die Datei ist noch da, im Verlauf
liegt eine byte-gleiche Kopie, die Dokumente und ihre Kennung bleiben.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parent.parent


def mach_pdf(pfad: Path, text: str = "Zinsausweis 2025 Saldo 31.12.2025 1'234.50") -> Path:
    pfad.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(pfad))
    c.drawString(72, 720, text)
    c.save()
    return pfad


def sha(pfad: Path) -> str:
    return hashlib.sha256(pfad.read_bytes()).hexdigest()


def gleiche_kopie(ordner: Path, bytes_: bytes) -> bool:
    return ordner.is_dir() and any(p.read_bytes() == bytes_ for p in ordner.iterdir()
                                   if p.is_file())


def einlesen(eingang: Path, ausgang: Path, *extra) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(ROOT / "scripts/pdf_to_word_json.py"),
                           "--input", str(eingang), "--output", str(ausgang), *extra],
                          capture_output=True, text=True, cwd=ROOT)


def altes_word_json(pdf: Path, ordner: str = "Konten") -> dict:
    """Word-JSON wie vor 261025-kme: nur pdf_fingerabdruck (size:mtime)."""
    from scripts.pdf_to_word_json import pdf_fingerabdruck
    return {"pdf_name": pdf.name, "pdf_fingerabdruck": pdf_fingerabdruck(pdf),
            "belegtyp": None, "ordner": ordner,
            "pages": [{"page_num": 1, "width": 595.0, "height": 842.0, "words": []}]}


# --- BS-1 -------------------------------------------------------------------

def test_word_json_altes_format_einmal_neu_kennung_bleibt(tmp_path):
    eingang, ausgang = tmp_path / "eingang", tmp_path / "json"
    pdf = mach_pdf(eingang / "Konten" / "a.pdf")
    ausgang.mkdir()
    alt = altes_word_json(pdf)
    (ausgang / "a.json").write_text(json.dumps(alt))
    alt_bytes = (ausgang / "a.json").read_bytes()

    r = einlesen(eingang, ausgang)
    assert r.returncode == 0, r.stderr
    assert "einmalig neu eingelesen (altes Format): 1" in r.stdout
    neu = json.loads((ausgang / "a.json").read_text())
    assert neu["pdf_inhalt"].startswith("sha256:") and neu["einlese_stand"]
    assert neu["pdf_fingerabdruck"] == alt["pdf_fingerabdruck"]     # Ablage-Kennung
    assert gleiche_kopie(ausgang / "_einlese-verlauf", alt_bytes)
    # Danach gilt es als unverändert — einmalig heisst einmalig.
    zweiter = einlesen(eingang, ausgang)
    assert "(1 unverändert übersprungen)" in zweiter.stdout
    assert "altes Format" not in zweiter.stdout


# --- BS-2 -------------------------------------------------------------------

def test_abgleich_verschiebt_statt_loeschen(tmp_path):
    from scripts.run_productive_local import _json_abgleich

    quelle, ziel = tmp_path / "quelle", tmp_path / "json"
    pdf = mach_pdf(quelle / "a.pdf")
    ziel.mkdir()
    (ziel / "a.json").write_text(json.dumps(altes_word_json(pdf, "")))
    (ziel / "weg.json").write_text(json.dumps(
        {"pdf_name": "weg.pdf", "pdf_fingerabdruck": "1:2", "pages": []}))
    (ziel / "kaputt.json").write_text("{ nicht lesbar")
    weg, kaputt = (ziel / "weg.json").read_bytes(), (ziel / "kaputt.json").read_bytes()

    assert _json_abgleich(quelle, ziel) == (2, 1)
    assert (ziel / "a.json").exists()                  # altes Format, gleiches size:mtime
    assert not (ziel / "weg.json").exists()
    assert gleiche_kopie(ziel / "_json-verlauf", weg)
    assert gleiche_kopie(ziel / "_json-verlauf", kaputt)


# --- BS-3 -------------------------------------------------------------------

def test_altes_ergebnis_kopie_vorgabe_bleibt(tmp_path, monkeypatch):
    from scripts import process_samples_full as psf

    for name, fp in (("a", "fp-a"), ("b", "fp-b"), ("c", "fp-c")):
        (tmp_path / f"{name}.json").write_text(json.dumps(
            {"pdf_name": f"{name}.pdf", "pdf_fingerabdruck": fp, "pages": []}))
    alt = [
        {"pdf_name": "a.pdf", "status": "ok", "belegtyp": "kk_praemienbescheinigung",
         "pdf_fingerabdruck": "fp-a", "code_stand": "alt",
         "belegtyp_override": "kk_praemienbescheinigung", "fields": []},
        {"pdf_name": "b.pdf", "status": "ok", "belegtyp": "saeule_3a",
         "pdf_fingerabdruck": "fp-b", "code_stand": "alt", "fields": []},
        {"pdf_name": "c.pdf", "status": "extract_failed", "fields": []},
    ]
    (tmp_path / "_results_full.json").write_text(json.dumps(alt))
    alt_bytes = (tmp_path / "_results_full.json").read_bytes()
    gesehen = []

    def falsch(jp, model=None, belegtyp_override=None):
        gesehen.append((jp.name, belegtyp_override))
        return {"pdf_name": json.loads(jp.read_text())["pdf_name"], "status": "ok",
                "belegtyp": belegtyp_override or "unknown", "fields": []}

    monkeypatch.setattr(psf, "process_json_sample", falsch)
    monkeypatch.setattr(sys, "argv", ["x", "--samples", str(tmp_path)])
    psf.main()
    assert sorted(gesehen) == [("a.json", "kk_praemienbescheinigung"),
                               ("b.json", None), ("c.json", None)]
    assert gleiche_kopie(tmp_path / "_results_full-verlauf", alt_bytes)
    neu = json.loads((tmp_path / "_results_full.json").read_text())
    assert sorted(e["pdf_name"] for e in neu) == ["a.pdf", "b.pdf", "c.pdf"]
    a = next(e for e in neu if e["pdf_name"] == "a.pdf")
    assert a["belegtyp_override"] == "kk_praemienbescheinigung"
    assert a["pdf_fingerabdruck"] == "fp-a"


# --- BS-4 -------------------------------------------------------------------

def test_korpus_teilausfall_verliert_keine_kopie(tmp_path, monkeypatch, capsys):
    from scripts import anonymize_belege as ab
    from scripts.pdf_to_word_json import pdf_fingerabdruck

    eingang, korpus = tmp_path / "eingang", tmp_path / "korpus"
    a = mach_pdf(eingang / "Konten" / "a.pdf", "A")
    b = mach_pdf(eingang / "Konten" / "b.pdf", "B")
    korpus.mkdir()

    def alter_schluessel(pdf):
        return hashlib.sha256(f"{pdf.name}|{pdf_fingerabdruck(pdf)}".encode()).hexdigest()[:20]

    kopien = {"BANK-A - Ausweis": b'{"pdf_name": "BANK-A - Ausweis.pdf", "pages": []}',
              "BANK-A - Ausweis-2": b'{"pdf_name": "BANK-A - Ausweis-2.pdf", "pages": []}'}
    for stamm, inhalt in kopien.items():
        (korpus / f"{stamm}.json").write_bytes(inhalt)
    (korpus / "_korpus_index.json").write_text(json.dumps({
        alter_schluessel(a): "BANK-A - Ausweis", alter_schluessel(b): "BANK-A - Ausweis-2",
        "anonymisierer_stand": "s1"}))

    def falsch(pdf, output, family=None, secrets=None):
        if pdf.name == "b.pdf":
            return None
        stamm, n = "BANK-A - Ausweis", 2
        while stamm in ab._GESCHRIEBEN:
            stamm = f"BANK-A - Ausweis-{n}"
            n += 1
        ab._GESCHRIEBEN.add(stamm)
        ziel = output / f"{stamm}.json"
        ziel.write_text(json.dumps({"pdf_name": f"{stamm}.pdf", "pages": []}))
        return ziel

    ab._GESCHRIEBEN.clear()
    monkeypatch.setattr(ab, "_anonymisierer_stand", lambda: "s1")
    monkeypatch.setattr(ab, "anonymize_one", falsch)
    monkeypatch.setattr(ab, "load_family", lambda p: None)
    monkeypatch.setattr(ab, "load_privacy_secrets", lambda p: {})
    monkeypatch.setattr(sys, "argv", ["x", "--input", str(eingang), "--output", str(korpus),
                                      "--inkrementell", "--prune"])
    assert ab.main() == 1
    aus = capsys.readouterr()
    assert "Prune ausgesetzt" in aus.out + aus.err
    for stamm, inhalt in kopien.items():
        assert (korpus / f"{stamm}.json").read_bytes() == inhalt


# --- BS-5 -------------------------------------------------------------------

def test_stempel_kopie_und_fehler_bleiben(tmp_path, monkeypatch, capsys):
    from scripts import stempel_uebernehmen as su

    (tmp_path / "a.json").write_text(json.dumps(
        {"pdf_name": "a.pdf", "pdf_fingerabdruck": "fp-a", "pdf_inhalt": "sha256:aa",
         "einlese_stand": "e1", "pages": []}))
    fehler = {"pdf_name": "b.pdf", "status": "pipeline_error", "pdf_fingerabdruck": "fp-b",
              "code_stand": "alt", "error": "x", "fields": []}
    unbekannt = {"pdf_name": "c.pdf", "status": "unknown_belegtyp",
                 "pdf_fingerabdruck": "fp-c", "code_stand": "alt", "fields": []}
    alt = [{"pdf_name": "a.pdf", "status": "ok", "pdf_fingerabdruck": "fp-a",
            "code_stand": "alt", "fields": []}, fehler, unbekannt]
    (tmp_path / "_results_full.json").write_text(json.dumps(alt))
    alt_bytes = (tmp_path / "_results_full.json").read_bytes()
    monkeypatch.setattr(sys, "argv", ["x", "--samples", str(tmp_path)])
    assert su.main() == 0
    assert gleiche_kopie(tmp_path / "_results_full-verlauf", alt_bytes)
    neu = json.loads((tmp_path / "_results_full.json").read_text())
    assert neu[1] == fehler and neu[2] == unbekannt
    assert neu[0]["code_stand"] != "alt" and neu[0]["pdf_inhalt"] == "sha256:aa"


# --- BS-6 -------------------------------------------------------------------

def test_ablage_bleibt_byte_gleich(tmp_path, monkeypatch):
    from extractors.ablage import oeffne, pfad_fuer
    from scripts import process_samples_full as psf
    from scripts import reextract, sicherung, stempel_uebernehmen
    from scripts.run_productive_local import _json_abgleich

    eingang = tmp_path / "eingang"
    samples = tmp_path / "latest" / "json"
    samples.mkdir(parents=True)
    mach_pdf(eingang / "Konten" / "a.pdf")
    db = oeffne(pfad_fuer(samples))
    db.execute("INSERT INTO position (id, dokument, zielwert, pos, betrag, aussteller, "
               "unterscheidung, jahr, status) VALUES "
               "('k1', 'a.pdf', 'Saldo 31.12.', 1, '1234.50', 'Musterbank', '', '2025', "
               "'geprueft')")
    db.commit()
    db.close()
    vorher = sha(pfad_fuer(samples))

    assert einlesen(eingang, samples).returncode == 0

    def falsch(jp, model=None, belegtyp_override=None):
        return {"pdf_name": json.loads(jp.read_text())["pdf_name"], "status": "ok",
                "belegtyp": "bank_zinsausweis", "fields": []}

    monkeypatch.setattr(psf, "process_json_sample", falsch)
    monkeypatch.setattr(sys, "argv", ["x", "--samples", str(samples)])
    psf.main()
    monkeypatch.setattr(sys, "argv", ["x", "--samples", str(samples), "--pdf", "a.pdf"])
    assert reextract.main() == 0
    monkeypatch.setattr(sys, "argv", ["x", "--samples", str(samples)])
    assert stempel_uebernehmen.main() == 0
    sicherung.sichere(samples.parent)
    _json_abgleich(eingang, samples)
    assert sha(pfad_fuer(samples)) == vorher


# --- BS-7 -------------------------------------------------------------------

def _bestand(wurzel: Path) -> dict[str, bytes]:
    dateien = {
        "positionen.db": b"db", "korrekturen.json": b"{}", "json/korrekturen.json": b"{}",
        "json/entscheide.json": b'{"a": 1}',
        "json/entscheide-verlauf/entscheide-1.json": b"1",
        "json/entscheide-verlauf/entscheide-2.json": b"2",
        "json/entscheide.defekt-20261025-120000-000000.json": b"defekt",
        "json/_results_full.json": b"[]", "steuer_uebertragung.md": b"# T",
        "nachweis_anhang.md": b"# N", "steuerwerte.json": b"{}",
        "etax_eingabe.json": b"{}", "steueraufstellung.xlsx": b"xlsx",
        "steuer_uebertragung.csv": b"a;b", "esteuerauszug/I01.pdf": b"%PDF-1.4",
    }
    for name, inhalt in dateien.items():
        (wurzel / name).parent.mkdir(parents=True, exist_ok=True)
        (wurzel / name).write_bytes(inhalt)
    return dateien


def test_sicherung_erweitert_ueberschreibt_nie(tmp_path):
    from scripts.sicherung import sichere

    dateien = _bestand(tmp_path)
    n1, ziel1 = sichere(tmp_path)
    erste = {p.relative_to(ziel1): p.read_bytes() for p in ziel1.rglob("*") if p.is_file()}
    assert n1 == len(dateien)
    inhalte = sorted(erste.values())
    assert inhalte == sorted(dateien.values())
    n2, ziel2 = sichere(tmp_path)
    assert ziel2 != ziel1 and n2 == n1
    assert {p.relative_to(ziel1): p.read_bytes()
            for p in ziel1.rglob("*") if p.is_file()} == erste


# --- BS-8 -------------------------------------------------------------------

def test_productive_sichert_vor_dem_lauf(tmp_path, monkeypatch, capsys):
    from scripts import run_productive_local as rpl

    eingang, ausgang = tmp_path / "eingang", tmp_path / "latest"
    eingang.mkdir()
    (ausgang / "json").mkdir(parents=True)
    (ausgang / "positionen.db").write_bytes(b"db")
    beim_ersten_schritt = []

    def lauf(cmd):
        beim_ersten_schritt.append(sorted(p.name for p in (ausgang / "sicherungen").rglob("*")
                                          if p.is_file()))
        return 1, ""

    monkeypatch.setattr(rpl, "_run", lauf)
    monkeypatch.setattr(sys, "argv", ["x", "--input", str(eingang), "--output", str(ausgang)])
    assert rpl.main() == 1
    assert beim_ersten_schritt and "positionen.db" in beim_ersten_schritt[0]
    assert "Sicherung vor dem Lauf: 1 Datei(en)" in capsys.readouterr().out


def test_productive_ohne_sicherung_kein_lauf(tmp_path, monkeypatch, capsys):
    from scripts import run_productive_local as rpl
    from scripts import sicherung

    def kaputt(wurzel):
        raise OSError("voll")

    monkeypatch.setattr(sicherung, "sichere", kaputt)
    monkeypatch.setattr(rpl, "_run", lambda cmd: (_ for _ in ()).throw(AssertionError("lief")))
    (tmp_path / "eingang").mkdir()
    monkeypatch.setattr(sys, "argv", ["x", "--input", str(tmp_path / "eingang"),
                                      "--output", str(tmp_path / "latest")])
    assert rpl.main() == 1
    assert "ABBRUCH: ohne Sicherung kein Lauf" in capsys.readouterr().out
    assert os.path.isdir(tmp_path / "latest")
