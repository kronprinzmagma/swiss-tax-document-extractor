"""Korpus, Stempel, inkrementeller Lauf und Sicherung (261025-kme, B4).
Alle Werte erfunden.

Der inkrementelle Lauf muss Änderungen am Inhalt, am Einlesen und an den
Vorgaben erkennen, ohne Bestand zu verwerfen; der Korpus verliert bei einem
Teilausfall keine Kopie; das Privacy-Gate läuft nach dem Nachziehen über die
Kopie.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parent.parent


def mach_pdf(pfad: Path, text: str = "Zinsausweis 2025 Saldo 1'234.50") -> Path:
    pfad.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(pfad))
    c.drawString(72, 720, text)
    c.save()
    return pfad


def einlesen(eingang: Path, ausgang: Path, *extra) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(ROOT / "scripts/pdf_to_word_json.py"),
                           "--input", str(eingang), "--output", str(ausgang), *extra],
                          capture_output=True, text=True, cwd=ROOT)


# --- B4a: Stempel umfasst Datenmodell, Familie und field_exceptions ---------

def test_code_stand_folgt_dem_datenmodell(tmp_path, monkeypatch):
    from extractors import datenmodell
    from scripts import process_samples_full as psf

    kopie = tmp_path / "DATENMODELL.md"
    shutil.copy(datenmodell.PFAD, kopie)
    monkeypatch.setattr(datenmodell, "PFAD", kopie)
    vorher = psf.code_stand(None)
    kopie.write_text(kopie.read_text(encoding="utf-8") + "\n<!-- geändert -->\n",
                     encoding="utf-8")
    assert psf.code_stand(None) != vorher


def test_code_stand_nur_ueber_benutzte_module(tmp_path):
    from scripts import process_samples_full as psf

    (tmp_path / "scripts").mkdir()
    (tmp_path / "extractors").mkdir()
    start = tmp_path / "scripts" / "process_samples_full.py"
    start.write_text("from extractors.regex_extract import regel\n")
    (tmp_path / "extractors" / "regex_extract.py").write_text("regel = 1\n")
    (tmp_path / "extractors" / "eintraege.py").write_text("leiste = 1\n")
    vorher = psf.code_stand(None, start=start, wurzel=tmp_path)
    (tmp_path / "extractors" / "eintraege.py").write_text("leiste = 2\n")
    assert psf.code_stand(None, start=start, wurzel=tmp_path) == vorher
    (tmp_path / "extractors" / "regex_extract.py").write_text("regel = 2\n")
    assert psf.code_stand(None, start=start, wurzel=tmp_path) != vorher
    echt = {p.name for p in psf._extraktionsmodule()}
    assert "regex_extract.py" in echt and "eintraege.py" not in echt


def test_daten_stand_folgt_field_exceptions(tmp_path):
    from scripts.process_samples_full import daten_stand

    assert daten_stand(tmp_path) == "-"
    (tmp_path / "field_exceptions.json").write_text("{}")
    eins = daten_stand(tmp_path)
    (tmp_path / "field_exceptions.json").write_text('{"a.pdf": {}}')
    assert daten_stand(tmp_path) not in (eins, "-")


# --- B4b/B4c: Einlese-Stand und Inhalt -------------------------------------

def test_einlese_stand_und_grund_stand(tmp_path):
    from scripts.pdf_to_word_json import einlese_stand, neu_einlesen_noetig, pdf_inhalt

    d = tmp_path / "modul.py"
    d.write_text("a = 1\n")
    eins = einlese_stand(dateien=[d])
    d.write_text("a = 2\n")
    assert einlese_stand(dateien=[d]) != eins
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF erfunden 1")
    alt = {"pdf_name": "a.pdf", "pdf_inhalt": pdf_inhalt(pdf),
           "pdf_groesse": pdf.stat().st_size, "einlese_stand": "alt"}
    assert neu_einlesen_noetig(alt, pdf, "neu") == "stand"
    assert neu_einlesen_noetig(alt, pdf, "alt") is None


def test_inhalt_entscheidet_nicht_die_zeit(tmp_path):
    from scripts.pdf_to_word_json import neu_einlesen_noetig, pdf_inhalt

    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF erfunden AAAA")
    alt = {"pdf_name": "a.pdf", "pdf_inhalt": pdf_inhalt(pdf),
           "pdf_groesse": pdf.stat().st_size, "einlese_stand": "s"}
    st = pdf.stat()
    os.utime(pdf, (st.st_atime + 1000, st.st_mtime + 1000))      # cp ohne -p
    assert neu_einlesen_noetig(alt, pdf, "s") is None
    pdf.write_bytes(b"%PDF erfunden BBBB")                        # gleiche Grösse
    os.utime(pdf, (st.st_atime, st.st_mtime))                     # alte Zeit zurück
    assert neu_einlesen_noetig(alt, pdf, "s") == "inhalt"
    assert neu_einlesen_noetig({"pdf_name": "a.pdf", "pdf_fingerabdruck": "1:2"},
                               pdf, "s") == "altes_format"
    assert neu_einlesen_noetig({}, pdf, "s") == "neu"


def test_kopie_ohne_p_wird_nicht_neu_eingelesen(tmp_path):
    eingang, ausgang = tmp_path / "eingang", tmp_path / "json"
    pdf = mach_pdf(eingang / "a.pdf")
    assert einlesen(eingang, ausgang).returncode == 0
    abdruck = json.loads((ausgang / "a.json").read_text())["pdf_fingerabdruck"]
    st = pdf.stat()
    os.utime(pdf, (st.st_atime + 5000, st.st_mtime + 5000))
    zweiter = einlesen(eingang, ausgang)
    assert "(1 unverändert übersprungen)" in zweiter.stdout
    assert json.loads((ausgang / "a.json").read_text())["pdf_fingerabdruck"] == abdruck


# --- B4d: der Ordner zählt --------------------------------------------------

def test_anderer_ordner_wird_neu_extrahiert(tmp_path, monkeypatch):
    from scripts import process_samples_full as psf

    (tmp_path / "a.json").write_text(json.dumps(
        {"pdf_name": "a.pdf", "pdf_fingerabdruck": "fp-a", "pdf_inhalt": "sha256:aa",
         "ordner": "Wertschriften", "pages": []}))
    (tmp_path / "_results_full.json").write_text(json.dumps([
        {"pdf_name": "a.pdf", "status": "ok", "ordner": "Konten", "pdf_fingerabdruck": "fp-a",
         "pdf_inhalt": "sha256:aa", "code_stand": psf.code_stand(None),
         "daten_stand": psf.daten_stand(tmp_path), "fields": []}]))
    aufgerufen = []

    def falsch(jp, model=None, belegtyp_override=None):
        doc = json.loads(jp.read_text())
        aufgerufen.append(jp.name)
        return {"pdf_name": doc["pdf_name"], "ordner": doc["ordner"], "status": "ok",
                "belegtyp": "wertschriftenverzeichnis", "fields": []}

    monkeypatch.setattr(psf, "process_json_sample", falsch)
    monkeypatch.setattr(sys, "argv", ["x", "--samples", str(tmp_path)])
    psf.main()
    assert aufgerufen == ["a.json"]
    (e,) = json.loads((tmp_path / "_results_full.json").read_text())
    assert e["ordner"] == "Wertschriften"
    aufgerufen.clear()
    psf.main()
    assert aufgerufen == []                               # danach übernommen


def _anon_lauf(monkeypatch, eingang, korpus, aufrufe, fehlschlag=(), *extra):
    from scripts import anonymize_belege as ab

    def falsch(pdf, output, family=None, secrets=None):
        aufrufe.append(pdf.name)
        if pdf.name in fehlschlag:
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
    monkeypatch.setattr(ab, "anonymize_one", falsch)
    monkeypatch.setattr(ab, "load_family", lambda p: None)
    monkeypatch.setattr(ab, "load_privacy_secrets", lambda p: {})
    monkeypatch.setattr(sys, "argv", ["x", "--input", str(eingang), "--output", str(korpus),
                                      "--inkrementell", "--prune", *extra])
    return ab.main()


def test_korpus_verschoben_kopie_angepasst(tmp_path, monkeypatch, capsys):
    eingang, korpus = tmp_path / "eingang", tmp_path / "korpus"
    mach_pdf(eingang / "Konten" / "a.pdf", "A")
    aufrufe: list[str] = []
    assert _anon_lauf(monkeypatch, eingang, korpus, aufrufe) == 0
    (eingang / "Wertschriften").mkdir()
    shutil.move(str(eingang / "Konten" / "a.pdf"), str(eingang / "Wertschriften" / "a.pdf"))
    aufrufe.clear()
    capsys.readouterr()
    assert _anon_lauf(monkeypatch, eingang, korpus, aufrufe) == 0
    assert aufrufe == []
    doc = json.loads((korpus / "BANK-A - Ausweis.json").read_text())
    assert doc["ordner"] == "Wertschriften"
    assert "1 Dokument(e) in anderem Ordner" in capsys.readouterr().out


# --- B4e: Teilausfall ---------------------------------------------------------

def test_korpus_teilausfall_kein_prune_exit_1(tmp_path, monkeypatch, capsys):
    eingang, korpus = tmp_path / "eingang", tmp_path / "korpus"
    mach_pdf(eingang / "Konten" / "a.pdf", "A")
    mach_pdf(eingang / "Konten" / "b.pdf", "B")
    korpus.mkdir()
    (korpus / "ALT - Kopie.json").write_text('{"pdf_name": "ALT - Kopie.pdf", "pages": []}')
    aufrufe: list[str] = []
    capsys.readouterr()
    assert _anon_lauf(monkeypatch, eingang, korpus, aufrufe, ("b.pdf",)) == 1
    aus = capsys.readouterr()
    text = aus.out + aus.err
    assert "1 von 2" in text and "Prune ausgesetzt" in text
    assert (korpus / "ALT - Kopie.json").exists()
    assert "b.pdf" not in text


def test_korpus_nachziehen_meldet_fehler(tmp_path, monkeypatch):
    from scripts import run_productive_local as rpl

    aufrufe = []
    monkeypatch.setattr(rpl, "_run", lambda cmd: (aufrufe.append(cmd), (1, ""))[1])
    assert rpl._korpus_nachziehen("py", tmp_path / "eingang", tmp_path / "korpus", False) != 0
    assert len(aufrufe) == 1                       # keine Extraktion nach Fehler


def test_productive_endet_mit_1_bei_unvollstaendiger_kopie(tmp_path, monkeypatch, capsys):
    from scripts import run_productive_local as rpl

    (tmp_path / "eingang").mkdir()
    monkeypatch.setattr(rpl, "_run", lambda cmd: (0, ""))
    monkeypatch.setattr(rpl, "_korpus_nachziehen", lambda *a: 1)
    monkeypatch.setattr(sys, "argv", ["x", "--input", str(tmp_path / "eingang"),
                                      "--output", str(tmp_path / "latest"),
                                      "--korpus", str(tmp_path / "korpus"),
                                      "--aussteller-history", str(tmp_path / "a.json")])
    assert rpl.main() == 1
    assert "Kopie unvollständig" in capsys.readouterr().out


# --- B4f: Personenerkennung und Gate über die Kopie -------------------------

def test_personenerkennung_ausfall_ist_fehler(monkeypatch):
    from scripts import anonymize_belege as ab

    kaputt = types.ModuleType("ollama")

    def chat(**_):
        raise ConnectionError("kein Dienst")

    kaputt.chat = chat
    monkeypatch.setitem(sys.modules, "ollama", kaputt)
    with pytest.raises(ab.PersonenerkennungFehler):
        ab.detect_persons_via_llm("Muster Hans")
    assert ab.detect_persons_via_llm("Muster Hans", pflicht=False) == []
    monkeypatch.setitem(sys.modules, "ollama", None)            # nicht installiert
    with pytest.raises(ab.PersonenerkennungFehler):
        ab.detect_persons_via_llm("Muster Hans")


def test_gate_ueber_die_kopie_nur_zahlen(tmp_path, capsys):
    from scripts.run_productive_local import _korpus_gate

    korpus = tmp_path / "korpus"
    korpus.mkdir()
    (korpus / "geheimdatei.txt").write_text("AHV 756.1234.5678.97\n")
    assert _korpus_gate(sys.executable, korpus) != 0
    aus = capsys.readouterr().out
    assert "Treffer" in aus and "geheimdatei" not in aus


def test_gate_ohne_kopie_ohne_subprozess(tmp_path, monkeypatch, capsys):
    from scripts import run_productive_local as rpl

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("Subprozess"))
    assert rpl._korpus_gate(sys.executable, tmp_path / "fehlt") != 0
    assert "Kopie fehlt" in capsys.readouterr().out


# --- B4g: Stempel nur für ok/out_of_scope ------------------------------------

def test_stempel_hebt_nur_ok(tmp_path, monkeypatch, capsys):
    from scripts import stempel_uebernehmen as su

    (tmp_path / "_results_full.json").write_text(json.dumps([
        {"pdf_name": "a.pdf", "status": "ok", "pdf_fingerabdruck": "fp-a", "code_stand": "x"},
        {"pdf_name": "b.pdf", "status": "pipeline_error", "pdf_fingerabdruck": "fp-b"},
        {"pdf_name": "c.pdf", "status": "unknown_belegtyp", "pdf_fingerabdruck": "fp-c"}]))
    monkeypatch.setattr(sys, "argv", ["x", "--samples", str(tmp_path)])
    assert su.main() == 0
    aus = capsys.readouterr().out
    assert "gehoben: 1" in aus and "übersprungen: 2" in aus
    assert list((tmp_path / "_results_full-verlauf").glob("*.json"))


# --- B4h: Sicherung ------------------------------------------------------------

def test_sicherung_nimmt_entscheide_verlauf_und_defekte(tmp_path):
    from scripts.sicherung import sichere

    (tmp_path / "json" / "entscheide-verlauf").mkdir(parents=True)
    (tmp_path / "json" / "entscheide-verlauf" / "e1.json").write_text("1")
    (tmp_path / "json" / "entscheide-verlauf" / "e2.json").write_text("2")
    (tmp_path / "json" / "entscheide.defekt-20261025-120000-000000.json").write_text("x")
    n, ziel = sichere(tmp_path)
    namen = {p.name for p in ziel.rglob("*") if p.is_file()}
    assert {"e1.json", "e2.json"} <= namen
    assert any("entscheide.defekt-20261025-120000-000000" in x for x in namen)
    assert n == 3


# --- B4i: Ordner der Kopie, Meldungen ohne Namen --------------------------------

def test_korpus_ordner_nur_aus_dem_datenmodell(tmp_path):
    from scripts.anonymize_belege import _korpus_ordner

    eingang = tmp_path / "eingang"
    assert _korpus_ordner(eingang / "Kita Muster" / "a.pdf", eingang) == "sonstiges"
    assert _korpus_ordner(eingang / "Konten" / "a.pdf", eingang) == "Konten"
    assert _korpus_ordner(eingang / "a.pdf", eingang) == ""


def test_einlesefehler_nennt_nummer_und_klasse(tmp_path):
    eingang = tmp_path / "eingang"
    eingang.mkdir()
    (eingang / "Geheimname Muster.pdf").write_bytes(b"kein pdf")
    r = einlesen(eingang, tmp_path / "json")
    text = r.stdout + r.stderr
    assert "Dokument 1/1" in text and "Geheimname" not in text
    assert "nicht eingelesen: 1" in text


def test_oeffnungsfehler_nur_klassenname(tmp_path, monkeypatch, capsys):
    from scripts import anonymize_belege as ab
    from scripts import pdf_to_word_json as ptw

    def kaputt(pdf):
        raise OSError(f"kann {pdf} nicht lesen")

    monkeypatch.setattr(ptw, "pdf_to_json", kaputt)
    pdf = tmp_path / "Geheimname Muster.pdf"
    assert ab.anonymize_one(pdf, tmp_path) is None
    aus = capsys.readouterr()
    assert "OSError" in aus.err and "Geheimname" not in aus.out + aus.err
