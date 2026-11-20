"""eSteuerauszüge der Banken kopieren statt nachbauen (261120).

der Anwender: „Wenn ein Beleg schon als eSteuerauszug vorliegt, möchte ich, dass du
ihn von den Belegen rüberkopierst und nicht selber erstellst." Werte erfunden;
als „Bankauszug" dient ein mit unserem eigenen Erzeuger gebautes PDF — der
Barcode ist derselbe Standard.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.etax_vorhanden import decke_ab, finde, lies, norm

ROOT = Path(__file__).resolve().parent.parent
PYETAX = ROOT / ".venv-etax" / "bin" / "python"

KONTO = "LI00 9999 8888 7777 666A A"


def _konto(nummer, saldo="1000"):
    return {"nummer": nummer, "bezeichnung": "Konto", "jahr": "2025", "saldo": saldo,
            "ertrag_mit_vst": "", "ertrag_ohne_vst": "", "dokumente": []}


def _schuld():
    return {"bezeichnung": "Festhypothek", "jahr": "2025", "schuld": "100000", "zins": "900"}


def test_konten_der_bank_fallen_aus_dem_bau():
    institute = [
        {"institut": "Bank A", "person": "elternteil_1",
         "konten": [_konto(norm(KONTO)), _konto("277605")], "schulden": []},
        {"institut": "Bank B", "person": "elternteil_2",
         "konten": [_konto("998877")], "schulden": [_schuld()]},
    ]
    bank = [{"pdf": "a.pdf", "jahr": "2025", "konten": [norm(KONTO)], "schulden": 0},
            {"pdf": "b.pdf", "jahr": "2025", "konten": ["998877"], "schulden": 1},
            {"pdf": "c.pdf", "jahr": "2025", "konten": ["111"], "schulden": 0}]
    bleiben, vorhanden = decke_ab(institute, bank)
    # Bank A: nur das Depot bleibt zu bauen; Bank B ist ganz abgedeckt.
    assert [(h["institut"], [k["nummer"] for k in h["konten"]]) for h in bleiben] == [
        ("Bank A", ["277605"])]
    assert [(v["abgedeckt_konten"], v["abgedeckt_schulden"]) for v in vorhanden] == [
        (1, 0), (1, 1), (0, 0)]   # c.pdf ohne Gegenstück — wird trotzdem kopiert


def test_schulden_bleiben_wenn_der_bankauszug_sie_nicht_fuehrt():
    institute = [{"institut": "Bank B", "person": "elternteil_1",
                  "konten": [_konto("998877")], "schulden": [_schuld(), _schuld()]}]
    bank = [{"pdf": "b.pdf", "jahr": "2025", "konten": ["998877"], "schulden": 1}]
    bleiben, _ = decke_ab(institute, bank)
    assert len(bleiben) == 1 and bleiben[0]["konten"] == [] and len(bleiben[0]["schulden"]) == 2


def test_lies_nummern_jahr_schulden():
    xml = (b'<taxStatement periodTo="2025-12-31"><listOfBankAccounts>'
           b'<bankAccount iban="LI00999988887777666AA" bankAccountName="x"/>'
           b'<bankAccount bankAccountNumber="16-123456-7" bankAccountName="y"/>'
           b'</listOfBankAccounts><depot depotNumber="277 605"/>'
           b'<liabilityAccount bankAccountName="z"/></taxStatement>')
    assert lies(xml) == {"jahr": "2025", "konten": ["161234567", "277605",
                                                    "LI00999988887777666AA"], "schulden": 1}


@pytest.fixture(scope="module")
def bank_pdf(tmp_path_factory):
    if not PYETAX.exists():
        pytest.skip(".venv-etax fehlt")
    sys.path.insert(0, str(ROOT / "evals"))
    import test_etax_bauen as t
    ziel = tmp_path_factory.mktemp("bank")
    haus = [{"institut": "Musterbank AG", "person": "elternteil_1", "schulden": [],
             "konten": [_konto(norm(KONTO))]}]
    lauf = subprocess.run([str(PYETAX), "-c", t._BAU, str(ROOT / "scripts"), str(ziel),
                           json.dumps(haus)], capture_output=True, text=True, timeout=300)
    assert lauf.returncode == 0, lauf.stderr[-1000:]
    return Path(json.loads(lauf.stdout.strip().splitlines()[-1])[0]["pdf"])


def test_finde_liest_den_barcode_und_ueberspringt_irrelevant(bank_pdf, tmp_path):
    belege = tmp_path / "belege"
    (belege / "Konten").mkdir(parents=True)
    (belege / "irrelevant").mkdir()
    shutil.copy2(bank_pdf, belege / "Konten" / "bank.pdf")
    shutil.copy2(bank_pdf, belege / "irrelevant" / "alt.pdf")
    speicher = tmp_path / "speicher.json"
    gefunden = finde(belege, speicher)
    assert [(Path(g["pdf"]).name, g["jahr"], g["konten"]) for g in gefunden] == [
        ("bank.pdf", "2025", [norm(KONTO)])]
    # Zweiter Lauf aus dem Zwischenspeicher — gleiches Ergebnis.
    assert finde(belege, speicher) == gefunden


def test_bauen_kopiert_den_bankauszug(bank_pdf, tmp_path):
    eingabe = tmp_path / "etax_eingabe.json"
    eingabe.write_text(json.dumps({
        "institute": [], "kunden": {}, "kunde": {"vorname": "Hans", "nachname": "Muster"},
        "vorhanden": [{"pdf": str(bank_pdf), "jahr": "2025", "konten": [norm(KONTO)],
                       "schulden": 0, "abgedeckt_konten": 1, "abgedeckt_schulden": 0}]}),
        encoding="utf-8")
    ziel = tmp_path / "ziel"
    r = subprocess.run([str(PYETAX), str(ROOT / "scripts" / "etax_bauen.py"), "--eingabe",
                        str(eingabe), "--ziel", str(ziel), "--jahr", "2025"],
                       capture_output=True, text=True, cwd=ROOT,
                       env={"PYTHONPATH": str(ROOT), "PATH": "/usr/bin:/bin"})
    assert r.returncode == 0, r.stdout + r.stderr
    assert "1  eSteuerauszug/-züge der Bank kopiert" in r.stdout
    assert (ziel / bank_pdf.name).read_bytes() == bank_pdf.read_bytes()
    assert bank_pdf.exists()   # das Original bleibt


def test_viele_teile_in_falscher_reihenfolge():
    """Mehr als sechs Barcode-Teile, durcheinander: schrittweise zusammengesetzt."""
    import random
    import zlib

    from scripts.etax_vorhanden import _schrittweise

    xml = b"<taxStatement>" + b"".join(b"<bankAccount iban='X%d'/>" % i for i in range(400)) \
        + b"</taxStatement>"
    roh = zlib.compress(xml)
    teile = [roh[i:i + len(roh) // 9 + 1] for i in range(0, len(roh), len(roh) // 9 + 1)]
    assert len(teile) > 6
    random.Random(7).shuffle(teile)
    assert _schrittweise(teile) == xml
