"""Pfadquelle Teil 2 (Plan 05-01): Volume-Schema, Marke, Guard, Tracer ``make pruefen``.

Alle Werte sind erfunden (Jahr 2099, Wegwerf-Ordner). Kein Test berührt einen
echten Datenort: ``STEUER_DATEN_WURZEL`` zeigt immer auf ``tmp_path``.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

import pytest

from extractors import datenpfade
from extractors.datenpfade import HINWEIS, MELDUNG, MARKE

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def wurzel(tmp_path, monkeypatch) -> Path:
    """Wegwerf-Wurzel ohne Marke (Volume nicht eingehängt)."""
    w = tmp_path / "daten"
    w.mkdir()
    monkeypatch.setenv(datenpfade.ENV, str(w))
    return w


@pytest.fixture
def eingehaengt(wurzel) -> Path:
    """Wegwerf-Wurzel mit Marke (Volume eingehängt)."""
    (wurzel / MARKE).write_text("", encoding="utf-8")
    return wurzel


# --- Schema ----------------------------------------------------------------------

def test_schema_gleich_fuer_jedes_jahr(wurzel):
    for jahr in (2025, 2026, 2099):
        assert datenpfade.jahr_ordner(jahr) == wurzel / str(jahr)
        assert datenpfade.belege(jahr) == wurzel / str(jahr) / "belege"
        assert datenpfade.ausgabe(jahr) == wurzel / str(jahr) / "ausgabe"
        assert datenpfade.eingereicht(jahr) == wurzel / str(jahr) / "eingereicht"
        assert datenpfade.arbeit(jahr) == wurzel / str(jahr) / "arbeit"
        assert datenpfade.sicherungen(jahr) == wurzel / "sicherungen" / str(jahr)
    assert datenpfade.stammdaten() == wurzel / "stammdaten"
    assert datenpfade.originale() == wurzel / "originale"
    assert datenpfade.protokoll() == wurzel / "stammdaten" / "umzug-protokoll.jsonl"


@pytest.mark.parametrize("jahr", [99, 12345, "abc", None])
def test_jahr_muss_vierstellig_sein(wurzel, jahr):
    with pytest.raises((ValueError, TypeError)):
        datenpfade.ausgabe(jahr)


def test_wurzel_standard_ohne_umgebungsvariable(monkeypatch):
    monkeypatch.delenv(datenpfade.ENV, raising=False)
    assert datenpfade.wurzel() == datenpfade.STANDARD
    monkeypatch.setenv(datenpfade.ENV, "")
    assert datenpfade.wurzel() == datenpfade.STANDARD


@pytest.mark.parametrize("name", ["mit leer/zeichen", "mit$dollar", "mit#raute"])
def test_wurzel_mit_leerraum_ist_wertfehler(monkeypatch, tmp_path, name):
    monkeypatch.setenv(datenpfade.ENV, str(tmp_path / name))
    with pytest.raises(ValueError):
        datenpfade.wurzel()
    with pytest.raises(ValueError):
        datenpfade.make_variablen(2099)


# --- Marke und Guard ---------------------------------------------------------------

def test_marke_ja_nein(wurzel):
    assert datenpfade.ist_eingehaengt() is False
    (wurzel / MARKE).write_text("", encoding="utf-8")
    assert datenpfade.ist_eingehaengt() is True


def test_standard_braucht_einhaengepunkt_und_marke(monkeypatch):
    monkeypatch.delenv(datenpfade.ENV, raising=False)
    monkeypatch.setattr(os.path, "ismount", lambda p: False)
    assert datenpfade.ist_eingehaengt() is False  # ohne Einhaengepunkt: keine Marke noetig

    monkeypatch.setattr(os.path, "ismount", lambda p: True)
    echt = os.path.isfile
    monkeypatch.setattr(os.path, "isfile", lambda p: False if str(p).endswith(MARKE) else echt(p))
    assert datenpfade.ist_eingehaengt() is False  # fremdes Volume gleichen Namens
    monkeypatch.setattr(os.path, "isfile", lambda p: True if str(p).endswith(MARKE) else echt(p))
    assert datenpfade.ist_eingehaengt() is True


def test_pruefe_eingehaengt_ohne_marke_meldet_und_exit_3(wurzel, capsys):
    with pytest.raises(datenpfade.VolumeFehlt) as fehler:
        datenpfade.pruefe_eingehaengt()
    assert fehler.value.code == 3
    assert MELDUNG in capsys.readouterr().err
    # Nichts angelegt (nur die Wurzel selbst aus dem Fixture).
    assert list(wurzel.iterdir()) == []


def test_pruefe_eingehaengt_mit_marke(eingehaengt):
    assert datenpfade.pruefe_eingehaengt() == eingehaengt


def test_stammdatei(wurzel):
    assert datenpfade.stammdatei("familie.yaml") is None  # ohne Marke
    (wurzel / MARKE).write_text("", encoding="utf-8")
    assert datenpfade.stammdatei("familie.yaml") == wurzel / "stammdaten" / "familie.yaml"
    assert datenpfade.stammdatei("unter/ordner.txt") == wurzel / "stammdaten" / "unter" / "ordner.txt"
    for schlecht in ("../aussen.txt", "a/../../b", "/abs/olut", ""):
        with pytest.raises(ValueError):
            datenpfade.stammdatei(schlecht)
    # Auch ohne Marke wird der Name geprüft, bevor None zurückkommt.
    (wurzel / MARKE).unlink()
    with pytest.raises(ValueError):
        datenpfade.stammdatei("../aussen.txt")


# --- Jahr, Arbeit, Sicherungen -----------------------------------------------------

def test_jahr_von(wurzel, tmp_path):
    assert datenpfade.jahr_von(wurzel / "2099" / "ausgabe" / "json") == 2099
    assert datenpfade.jahr_von(wurzel / "2099") == 2099
    assert datenpfade.jahr_von(wurzel / "2099" / ".." / "2098" / "ausgabe") == 2098
    assert datenpfade.jahr_von(wurzel / "stammdaten") is None
    assert datenpfade.jahr_von(wurzel / "20999") is None
    assert datenpfade.jahr_von(wurzel) is None
    assert datenpfade.jahr_von(tmp_path / "2099" / "ausgabe") is None  # ausserhalb
    assert datenpfade.jahr_von(Path("evals") / "samples_real_2099") is None


def test_arbeit_fuer(wurzel, tmp_path):
    assert datenpfade.arbeit_fuer(wurzel / "2099" / "ausgabe" / "json") == wurzel / "2099" / "arbeit"
    assert datenpfade.arbeit_fuer(wurzel / "2099" / "ausgabe") == wurzel / "2099" / "arbeit"
    korpus = tmp_path / "korpus_2099"
    assert datenpfade.arbeit_fuer(korpus / "json") == korpus
    assert datenpfade.arbeit_fuer(korpus) == korpus


def test_sicherungen_fuer(wurzel, tmp_path):
    assert datenpfade.sicherungen_fuer(wurzel / "2099" / "ausgabe") == wurzel / "sicherungen" / "2099"
    ausgabe = tmp_path / "ausserhalb"
    assert datenpfade.sicherungen_fuer(ausgabe) == ausgabe / "sicherungen"


# --- Zuordnung des Korpus ------------------------------------------------------------

def _datei(pfad: Path) -> Path:
    pfad.parent.mkdir(parents=True, exist_ok=True)
    pfad.write_text("{}", encoding="utf-8")
    return pfad


def test_korpus_zuordnung_ohne_volume_ist_korpus_ort(wurzel, tmp_path):
    korpus = tmp_path / "korpus_2099"
    assert datenpfade.korpus_zuordnung(korpus) == korpus / "anon_truth_mapping.json"
    assert datenpfade.korpus_zuordnung_ziel(korpus) == korpus / "anon_truth_mapping.json"


def test_korpus_zuordnung_mit_volume(eingehaengt, tmp_path):
    korpus = tmp_path / "korpus_2099"
    alt = korpus / "anon_truth_mapping.json"
    neu = eingehaengt / "stammdaten" / "korpus" / "korpus_2099" / "anon_truth_mapping.json"
    # Fall 1: nirgends vorhanden -> Lesen am Korpus-Ort, Schreiben aufs Volume.
    assert datenpfade.korpus_zuordnung(korpus) == alt
    assert datenpfade.korpus_zuordnung_ziel(korpus) == neu
    # Fall 2: nur im Korpus -> beides bleibt dort (der Anwender zieht um, nicht der Code).
    _datei(alt)
    assert datenpfade.korpus_zuordnung(korpus) == alt
    assert datenpfade.korpus_zuordnung_ziel(korpus) == alt
    # Fall 3: auf dem Volume -> Volume (auch wenn sie im Korpus noch liegt).
    _datei(neu)
    assert datenpfade.korpus_zuordnung(korpus) == neu
    assert datenpfade.korpus_zuordnung_ziel(korpus) == neu


# --- Make-Variablen und Kommandozeile --------------------------------------------------

def test_make_variablen(wurzel):
    zeile = datenpfade.make_variablen(2099)
    assert f"LATEST?={wurzel}/2099/ausgabe" in zeile.split()
    assert f"VORJAHR_AUSGABE?={wurzel}/2098/ausgabe" in zeile.split()
    assert f"PDFDIR?={wurzel}/2099/belege" in zeile.split()
    assert f"ARBEIT?={wurzel}/2099/arbeit" in zeile.split()
    assert f"EINGEREICHT?={wurzel}/2099/eingereicht" in zeile.split()
    assert f"DATEN?={wurzel}" in zeile.split()
    assert "\n" not in zeile


def test_cli(eingehaengt, capsys):
    assert datenpfade.main(["wurzel"]) == 0
    assert capsys.readouterr().out.strip() == str(eingehaengt)
    assert datenpfade.main(["ausgabe", "2099"]) == 0
    assert capsys.readouterr().out.strip() == f"{eingehaengt}/2099/ausgabe"
    assert datenpfade.main(["pruefen"]) == 0
    aus = capsys.readouterr()
    assert aus.out == "" and aus.err == ""  # still
    assert datenpfade.main(["hinweis"]) == 0
    assert HINWEIS in capsys.readouterr().err
    assert datenpfade.main(["ausgabe", "99"]) == 2
    assert datenpfade.main(["unbekannt"]) == 2


def test_cli_hinweis_ohne_volume_schweigt(wurzel, capsys):
    assert datenpfade.main(["hinweis"]) == 0
    aus = capsys.readouterr()
    assert aus.out == "" and aus.err == ""
    with pytest.raises(datenpfade.VolumeFehlt):
        datenpfade.main(["pruefen"])


# --- Makefile -------------------------------------------------------------------------

def test_makefile_holt_pfade_aus_der_pfadquelle():
    text = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert text.count("extractors.datenpfade") >= 3
    assert text.count("MIT_HINWEIS") >= 2
    assert text.count("mk/*.mk") == 1
    # Keine eigene Pfadregel mehr.
    for alt in ("output/latest", "ERSTES_JAHR", "$(call ausgabe"):
        assert alt not in text, alt
    koerper = text.split("\npruefen:\n", 1)[1].split("\n\n", 1)[0].splitlines()
    assert koerper[0].strip() == "@$(DATEN_DA)"
    assert "$(MIT_HINWEIS)" in koerper[-1]


# --- Tracer: make pruefen durch alle Schichten ------------------------------------------

def _status_hash() -> str:
    aus = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT,
                         capture_output=True, text=True, check=True).stdout
    return hashlib.sha256(aus.encode()).hexdigest()


def _make_pruefen(wurzel: Path):
    umgebung = os.environ.copy()
    umgebung[datenpfade.ENV] = str(wurzel)
    vorher = _status_hash()
    lauf = subprocess.run(["make", "pruefen", "STEUERJAHR=2099"], cwd=ROOT, env=umgebung,
                          capture_output=True, text=True, timeout=300)
    assert _status_hash() == vorher, "der Arbeitsbaum hat sich verändert"
    return lauf


def test_tracer_a_ohne_marke_bricht_mit_meldung_ab(wurzel):
    lauf = _make_pruefen(wurzel)
    assert lauf.returncode != 0
    assert MELDUNG in lauf.stderr
    assert HINWEIS not in lauf.stderr
    assert "Kein Extraktionsergebnis" not in lauf.stdout
    assert [p.name for p in wurzel.iterdir()] == []  # nichts angelegt


def test_tracer_b_ohne_ergebnis_exit_2_und_erinnerung(eingehaengt):
    (eingehaengt / "2099" / "ausgabe" / "json").mkdir(parents=True)
    (eingehaengt / "2099" / "belege").mkdir()
    lauf = _make_pruefen(eingehaengt)
    assert "Kein Extraktionsergebnis gefunden" in lauf.stdout
    assert HINWEIS in lauf.stderr
    assert "Error 2" in lauf.stderr
    assert MELDUNG not in lauf.stderr


def test_tracer_c_ohne_pdf_ordner_exit_1_und_erinnerung(eingehaengt):
    json_ordner = eingehaengt / "2099" / "ausgabe" / "json"
    json_ordner.mkdir(parents=True)
    (json_ordner / "_results_full.json").write_text("[]", encoding="utf-8")
    lauf = _make_pruefen(eingehaengt)
    assert HINWEIS in lauf.stderr
    assert "Error 1" in lauf.stderr  # der Exit-Code des Skripts bleibt erhalten
    assert "Kein PDF-Ordner" in lauf.stdout


def test_tracer_wo_braucht_kein_volume(wurzel):
    umgebung = os.environ.copy()
    umgebung[datenpfade.ENV] = str(wurzel)
    lauf = subprocess.run(["make", "-s", "wo", "STEUERJAHR=2099"], cwd=ROOT, env=umgebung,
                          capture_output=True, text=True, timeout=120)
    assert lauf.returncode == 0, lauf.stderr
    assert f"{wurzel}/2099/ausgabe" in lauf.stdout
    assert f"{wurzel}/2099/belege" in lauf.stdout
    assert f"Arbeit {wurzel}/2099/arbeit" in lauf.stdout
