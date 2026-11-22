"""Nachweis, dass die Testsitzung von echten Daten getrennt ist (Phase 5, DS-09).

Alle Proben zielen auf erfundene, nicht vorhandene Pfade unter einem gesperrten
Muster (z. B. ein Belegordner für das Jahr 2099). Die Fehlerart unterscheidet
die Sperre: ``PermissionError`` mit «Testlauf» statt ``FileNotFoundError``.
Fehlte die Sperre, würde nie eine echte Datei geöffnet.

Pfade der echten Orte kommen nur als Text vor.
"""
from __future__ import annotations

import glob
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

import pytest

from extractors import datenpfade
from extractors.datenpfade import ROOT

ERFUNDEN = ROOT / "belege-2099" / "probe.pdf"
MELDUNG = "Testlauf: echte Daten gesperrt"


# --- Die Sitzung ist getrennt -------------------------------------------------

def test_sitzung_ist_getrennt(tmp_path):
    assert os.environ["STEUER_TESTLAUF"] == "1"
    sync = Path(os.environ["SYNC_PRIVATE_DIR"])
    assert (sync / "sync_forbidden.private.txt").is_file()
    assert (sync / "sync_substitutions.private.txt").is_file()
    wurzel = Path(os.environ[datenpfade.ENV])
    assert not wurzel.exists()
    site = Path(os.environ["STEUER_TESTLAUF_SITE"])
    assert (site / "sitecustomize.py").is_file()
    assert os.environ["PYTHONPATH"].split(os.pathsep)[0] == str(site)
    familie = Path(os.environ[datenpfade.ENV_FAMILIE])
    assert familie.is_file() and not datenpfade.testlauf_gesperrt(familie)


def test_open_auf_erfundenen_ort_ist_gesperrt():
    with pytest.raises(PermissionError, match="Testlauf"):
        open(ERFUNDEN, "rb")


def test_meldung_nennt_nur_die_kategorie():
    with pytest.raises(PermissionError) as fehler:
        open(ERFUNDEN, "rb")
    text = str(fehler.value)
    assert text.startswith(MELDUNG)
    assert "belege" not in text.lower().replace("belegordner", "")
    assert "2099" not in text


# --- Welche Orte sind gesperrt? ------------------------------------------------

GESPERRT = [
    ("family.yaml", "Stammdaten"),
    ("family.yaml.bak-1", "Stammdaten"),
    ("privacy_secrets.local.yaml", "Stammdaten"),
    ("privacy_secrets.local.yaml.bak", "Stammdaten"),
    ("privacy_secrets.vorschlag.yaml", "Stammdaten"),
    ("aussteller.json", "Aussteller-Historie"),
    ("aussteller.json.bak", "Aussteller-Historie"),
    ("belege", "Belegordner"),
    ("belege/x/y.pdf", "Belegordner"),
    ("belege-2099/probe.pdf", "Belegordner"),
    ("output", "Ausgabeordner"),
    ("output/latest/json/x.json", "Ausgabeordner"),
    ("output/2099/positionen.db", "Ausgabeordner"),
    ("evals/positionen.db", "Ablage"),
    ("scripts/sync_forbidden.private.txt", "Sync-Liste"),
    ("scripts/sync_substitutions.private.txt", "Sync-Liste"),
    ("evals/samples_real_2023/anon_truth_mapping.json", "Korpus-Zuordnung"),
    ("evals/samples_real/anon_truth_mapping.json", "Korpus-Zuordnung"),
    ("BELEGE/x.pdf", "Belegordner"),  # macOS unterscheidet Gross/Klein nicht
    ("steueraufstellung.numbers", "Steueraufstellung"),
    ("output/latest/x.numbers", "Ausgabeordner"),
    ("probe_review.html", "Review-Probe"),
    ("pruefen_namen.txt", "Kuerzel-Zuordnung"),
    ("entscheide.json", "Entscheide"),
    ("entscheide-verlauf/1.json", "Entscheide"),
]


@pytest.mark.parametrize("rel,kategorie", GESPERRT)
def test_gesperrte_orte(rel, kategorie):
    assert datenpfade.testlauf_kategorie(ROOT / rel) == kategorie
    assert datenpfade.testlauf_gesperrt(ROOT / rel)


def test_alte_originale_und_volume_sind_gesperrt():
    assert datenpfade.testlauf_kategorie(datenpfade.ALT_ORIGINALE / "x" / "y.pdf") == "Originalbelege"
    assert datenpfade.testlauf_kategorie(datenpfade.ALT_ORIGINALE) == "Originalbelege"
    assert datenpfade.testlauf_kategorie(datenpfade.STANDARD / "2025" / "ausgabe") == "Volume"
    assert datenpfade.testlauf_kategorie(datenpfade.STANDARD) == "Volume"


NICHT_GESPERRT = [
    "family.yaml.example",
    "privacy_secrets.local.example.yaml",
    "aussteller.json.example",
    "evals/family.yaml.test",
    "evals/fixtures/lohnausweis.pdf",
    "evals/expected/x.json",
    "evals/samples_real_2023/x.json",
    "evals/test_x.py",
    "belege_notizen.md",
    "outputs/x",
    "extractors/datenpfade.py",
    "extractors/entscheide.py",
    "evals/test_entscheide.py",
    "STATE.md",
]


@pytest.mark.parametrize("rel", NICHT_GESPERRT)
def test_nicht_gesperrte_orte(rel):
    assert datenpfade.testlauf_kategorie(ROOT / rel) is None


def test_tmp_path_ist_nicht_gesperrt(tmp_path):
    assert not datenpfade.testlauf_gesperrt(tmp_path / "x" / "family.yaml")
    (tmp_path / "x.txt").write_text("ok", encoding="utf-8")
    assert (tmp_path / "x.txt").read_text(encoding="utf-8") == "ok"


def test_ohne_testlauf_ist_nichts_gesperrt(monkeypatch):
    monkeypatch.delenv("STEUER_TESTLAUF")
    assert not datenpfade.testlauf_aktiv()
    assert datenpfade.testlauf_kategorie(ERFUNDEN) is None
    assert not datenpfade.testlauf_gesperrt(datenpfade.STANDARD / "x")
    # Der Hook lässt dann alles durch (der Test öffnet nichts Echtes).
    datenpfade.testlauf_audit("open", (str(ERFUNDEN), "rb", 0))


def test_relative_pfade_gelten_ab_arbeitsordner(monkeypatch, tmp_path):
    monkeypatch.chdir(ROOT)
    assert datenpfade.testlauf_gesperrt("family.yaml")
    assert datenpfade.testlauf_gesperrt(Path("belege-2099") / "x.pdf")
    assert datenpfade.testlauf_gesperrt("evals/../output/x")
    monkeypatch.chdir(tmp_path)
    assert not datenpfade.testlauf_gesperrt("family.yaml")
    assert not datenpfade.testlauf_gesperrt(Path("belege-2099") / "x.pdf")


def test_dateikennungen_und_unbrauchbares_werden_ignoriert():
    datenpfade.testlauf_audit("open", (3, "rb", 0))
    datenpfade.testlauf_audit("os.listdir", (None,))
    datenpfade.testlauf_audit("open", ())
    datenpfade.testlauf_audit("os.scandir", (b"\xff\xfe-kein-pfad-in-root",))


# --- Listen, Scannen, Glob, SQLite ---------------------------------------------

def test_auflisten_ist_gesperrt():
    ort = ROOT / "belege-2099"
    with pytest.raises(PermissionError, match="Testlauf"):
        os.listdir(ort)
    with pytest.raises(PermissionError, match="Testlauf"):
        os.scandir(ort)
    with pytest.raises(PermissionError, match="Testlauf"):
        list(ort.iterdir())


def test_glob_praefix_gesperrt():
    with pytest.raises(PermissionError, match="Testlauf"):
        glob.glob(str(ROOT / "belege-2099" / "*.pdf"))
    # Platzhalter erst tiefer im Baum: der feste Teil davor entscheidet.
    with pytest.raises(PermissionError, match="Testlauf"):
        glob.glob(str(ROOT / "belege-2099" / "a" / "**" / "*.pdf"), recursive=True)
    # root_dir und relatives Muster.
    with pytest.raises(PermissionError, match="Testlauf"):
        glob.glob("belege-2099/*.pdf", root_dir=ROOT)
    # Ein Muster ohne festen gesperrten Teil bleibt erlaubt.
    assert glob.glob(str(ROOT / "extractors" / "datenpfade.p*")) != []


def test_sqlite_gesperrt(tmp_path):
    with pytest.raises(PermissionError, match="Testlauf"):
        sqlite3.connect(str(ROOT / "belege-2099" / "x.db"))
    ok = sqlite3.connect(str(tmp_path / "x.db"))
    ok.close()


def test_sqlite_uri_gesperrt():
    ort = ROOT / "belege-2099" / "mit Leerzeichen" / "x.db"
    uri = "file:" + quote(str(ort)) + "?mode=ro"
    with pytest.raises(PermissionError, match="Testlauf"):
        sqlite3.connect(uri, uri=True)
    # Ohne Kodierung und mit Rechnername.
    with pytest.raises(PermissionError, match="Testlauf"):
        sqlite3.connect("file://localhost" + str(ort) + "?mode=ro", uri=True)


def test_sqlite_uri_in_tmp_ist_erlaubt(tmp_path):
    db = sqlite3.connect("file:" + quote(str(tmp_path / "a b.db")) + "?mode=rwc", uri=True)
    db.close()
    sqlite3.connect(":memory:").close()


# --- Kindprozesse ---------------------------------------------------------------

def _kind(code: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-c", code], env=os.environ.copy(),
                          capture_output=True, text=True, timeout=120)


def test_kindprozess_gesperrt():
    lauf = _kind(f"open({str(ERFUNDEN)!r})")
    assert lauf.returncode != 0
    assert MELDUNG in lauf.stderr
    assert "No such file" not in lauf.stderr


def test_kindprozess_erlaubt_tmp(tmp_path):
    ziel = tmp_path / "ok.txt"
    ziel.write_text("ok", encoding="utf-8")
    lauf = _kind(f"print(open({str(ziel)!r}).read())")
    assert lauf.returncode == 0, lauf.stderr
    assert lauf.stdout.strip() == "ok"


def test_make_kind_gesperrt(tmp_path):
    """Auch ein Rezept, das ueber make Python startet, erbt die Sperre."""
    fragment = tmp_path / "probe.mk"
    fragment.write_text(
        "testlauf-probe:\n"
        f"\t$(PY) -c \"open('{ERFUNDEN}')\"\n",
        encoding="utf-8")
    lauf = subprocess.run(["make", "-f", "Makefile", "-f", str(fragment), "testlauf-probe"],
                          cwd=ROOT, env=os.environ.copy(),
                          capture_output=True, text=True, timeout=120)
    assert lauf.returncode != 0
    assert MELDUNG in lauf.stderr
    assert "No such file" not in lauf.stderr


def test_sitecustomize_nur_im_testlauf(tmp_path):
    datei = datenpfade.schreibe_testlauf_site(tmp_path / "site")
    assert datei == tmp_path / "site" / "sitecustomize.py"
    inhalt = datei.read_text(encoding="utf-8")
    assert "STEUER_TESTLAUF" in inhalt
    assert "addaudithook" in inhalt
    # Ohne STEUER_TESTLAUF lädt das Kind den Hook nicht: kein PermissionError.
    umgebung = os.environ.copy()
    umgebung.pop("STEUER_TESTLAUF")
    umgebung["PYTHONPATH"] = str(tmp_path / "site")
    lauf = subprocess.run([sys.executable, "-c", f"open({str(ERFUNDEN)!r})"],
                          env=umgebung, capture_output=True, text=True, timeout=120)
    assert "PermissionError" not in lauf.stderr
    assert "FileNotFoundError" in lauf.stderr


# --- Die Liste ist festgehalten ---------------------------------------------------

def test_echte_orte_festgehalten():
    """Jede Änderung der Liste, auch eine Verkleinerung, lässt diesen Test scheitern."""
    assert datenpfade.ECHTE_ORTE == [
        ("family.yaml", "Stammdaten"),
        ("family.yaml.bak*", "Stammdaten"),
        ("privacy_secrets.local.yaml", "Stammdaten"),
        ("privacy_secrets.local.yaml.bak*", "Stammdaten"),
        ("privacy_secrets.vorschlag.yaml", "Stammdaten"),
        ("privacy_secrets.vorschlag.yaml.bak*", "Stammdaten"),
        ("aussteller.json", "Aussteller-Historie"),
        ("aussteller.json.bak*", "Aussteller-Historie"),
        ("belege", "Belegordner"),
        ("belege-*", "Belegordner"),
        ("output", "Ausgabeordner"),
        ("evals/positionen.db", "Ablage"),
        ("scripts/sync_*.private.txt", "Sync-Liste"),
        ("evals/samples_real*/anon_truth_mapping.json", "Korpus-Zuordnung"),
        ("*.numbers", "Steueraufstellung"),
        ("probe_review.html", "Review-Probe"),
        ("pruefen_namen.txt", "Kuerzel-Zuordnung"),
        ("entscheide*", "Entscheide"),
    ]
    assert datenpfade.ALT_ORIGINALE == Path.home() / "Dokumente" / "Steuern"
    assert datenpfade.STANDARD == Path("/Volumes/Steuern")
    assert datenpfade.ALTE_ORTE == {
        "belege": "belege",
        "belege_muster": "belege-{jahr}",
        "ausgabe": "output",
        "ausgabe_erstes_jahr": "output/latest",
    }


# --- Lader behandeln echte Dateien als nicht vorhanden ------------------------------

def test_load_family_im_testlauf_ohne_zugriff(monkeypatch):
    from extractors.family import load_family

    original = Path.exists

    def wacht(self):
        if self.name == "family.yaml":
            raise AssertionError("Der Lader hat die echte Datei geprüft")
        return original(self)

    monkeypatch.setattr(Path, "exists", wacht)
    assert load_family(ROOT / "family.yaml") is None
    monkeypatch.chdir(ROOT)
    assert load_family(None) is None


def test_load_privacy_secrets_im_testlauf_ohne_zugriff(monkeypatch):
    from extractors.pii_patterns import load_privacy_secrets

    original = Path.exists

    def wacht(self):
        if self.name.startswith("privacy_secrets"):
            raise AssertionError("Der Lader hat die echte Datei geprüft")
        return original(self)

    monkeypatch.setattr(Path, "exists", wacht)
    leer = {"institutions": {}, "persons": [], "iban_account_numbers": [],
            "addresses": [], "birthdates": []}
    assert load_privacy_secrets(ROOT / "privacy_secrets.local.yaml") == leer
    monkeypatch.chdir(ROOT)
    assert load_privacy_secrets(None) == leer


def test_familie_pfad_zeigt_im_testlauf_auf_die_erfundene_familie(monkeypatch, tmp_path):
    from extractors.family import load_family

    erfunden = Path(os.environ[datenpfade.ENV_FAMILIE])
    assert datenpfade.familie_pfad() == erfunden
    assert datenpfade.familie_pfad(ROOT / "family.yaml") == erfunden
    monkeypatch.chdir(ROOT)
    assert datenpfade.familie_pfad(Path("family.yaml")) == erfunden
    # Ein Pfad im Wegwerf-Ordner bleibt, wie er ist.
    eigene = tmp_path / "family.yaml"
    assert datenpfade.familie_pfad(eigene) == eigene
    monkeypatch.chdir(tmp_path)
    assert datenpfade.familie_pfad(Path("family.yaml")) == Path("family.yaml")

    familie = load_family(datenpfade.familie_pfad())
    assert familie is not None
    assert [m.role for m in familie.members] == ["mann", "frau", "kind1", "kind2"]

    # Ohne Testlauf gilt der Pfad an der Wurzel.
    monkeypatch.delenv("STEUER_TESTLAUF")
    assert datenpfade.familie_pfad() == ROOT / "family.yaml"


def test_shutil_ueber_gesperrte_orte_ist_gesperrt(tmp_path):
    import shutil

    quelle = tmp_path / "q.txt"
    quelle.write_text("x", encoding="utf-8")
    with pytest.raises(PermissionError, match="Testlauf"):
        shutil.copyfile(ERFUNDEN, tmp_path / "ziel.txt")
    with pytest.raises(PermissionError, match="Testlauf"):
        shutil.copyfile(quelle, ERFUNDEN)
    with pytest.raises(PermissionError, match="Testlauf"):
        shutil.copytree(ERFUNDEN.parent, tmp_path / "kopie")
    with pytest.raises(PermissionError, match="Testlauf"):
        shutil.move(str(quelle), str(ERFUNDEN))
    assert quelle.exists()
    shutil.copyfile(quelle, tmp_path / "ok.txt")


def test_aussteller_load_im_testlauf_wie_fehlende_datei(monkeypatch, tmp_path):
    from extractors import aussteller

    original = Path.exists

    def wacht(self):
        if self.name == "aussteller.json" and self.parent == ROOT:
            raise AssertionError("Der Lader hat die echte Datei geprüft")
        return original(self)

    monkeypatch.setattr(Path, "exists", wacht)
    assert aussteller.load(ROOT / "aussteller.json") == ({}, None)
    assert aussteller.load(ROOT / "aussteller.json.bak") == ({}, None)
    # Eine Datei im Wegwerf-Ordner wird weiterhin gelesen.
    eigene = tmp_path / "aussteller.json"
    eigene.write_text("{}", encoding="utf-8")
    assert aussteller.load(eigene) == ({}, None)


# --- Die Ablage eines Korpusordners entsteht im Korpusordner ---------------------------

def test_pfad_fuer_ohne_sprung_in_den_elternordner(tmp_path):
    from extractors.ablage import pfad_fuer

    assert pfad_fuer(tmp_path / "x" / "json") == tmp_path / "x" / "positionen.db"
    assert pfad_fuer(tmp_path / "korpus") == tmp_path / "korpus" / "positionen.db"
    korpus = ROOT / "evals" / "samples_real_2023"
    assert pfad_fuer(korpus) == korpus / "positionen.db"
    assert pfad_fuer(korpus).parent != ROOT / "evals"


# --- Abbruch ohne Trennung ----------------------------------------------------------

@pytest.mark.parametrize("variable", ["STEUER_TESTLAUF", "SYNC_PRIVATE_DIR",
                                      datenpfade.ENV, "STEUER_TESTLAUF_SITE"])
def test_sitzungsstart_bricht_ohne_trennung_ab(monkeypatch, variable):
    from evals import conftest

    if variable == datenpfade.ENV:
        monkeypatch.setenv(variable, str(ROOT))  # vorhandener Pfad: keine Trennung
    else:
        monkeypatch.delenv(variable)
    with pytest.raises(pytest.exit.Exception) as ausgang:
        conftest.pytest_sessionstart(None)
    assert ausgang.value.returncode == 3
    assert "nicht von echten Daten getrennt" in str(ausgang.value)


def test_sitzungsstart_laeuft_durch_mit_trennung():
    from evals import conftest

    conftest.pytest_sessionstart(None)


# --- Terminal-Pflicht -----------------------------------------------------------------

def test_nur_im_eigenen_terminal(monkeypatch, capsys):
    monkeypatch.setenv("CLAUDECODE", "1")
    with pytest.raises(SystemExit) as ausgang:
        datenpfade.nur_im_eigenen_terminal("Der Umzug")
    assert ausgang.value.code == 4
    assert "Der Umzug läuft nur in" in capsys.readouterr().err

    monkeypatch.delenv("CLAUDECODE")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)
    with pytest.raises(SystemExit) as ausgang:
        datenpfade.nur_im_eigenen_terminal("Der Umzug")
    assert ausgang.value.code == 4

    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
    datenpfade.nur_im_eigenen_terminal("Der Umzug")
