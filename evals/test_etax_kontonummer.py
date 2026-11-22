"""Die Konto-/Depot-Nr. kommt im eSteuerauszug an (261120).

Sie lief durch die Betragsumwandlung: eine IBAN ist keine Zahl und wurde
leer, eine rein numerische Depotnummer überstand es. der Anwender: „wieso ist bei
den eSteuerauszügen nicht immer eine Kontonummer angegeben?" — Formular 340
verlangt sie („bei Konto inkl. Nummer"). Werte erfunden.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from scripts.etax_daten import baue, kennung

ROOT = Path(__file__).resolve().parent.parent
KORPUS = ROOT / "evals" / "samples_real_2025"


@pytest.mark.parametrize("roh, erwartet", [
    # Ländercode XX: erfunden, keine echte IBAN im Quelltext (Leak-Prüfung).
    ("XX00 1111 2222 3333 4444 5", "XX0011112222333344445"),
    ("xx00 1111 2222 3333 4444 5", "XX0011112222333344445"),
    ("LI00 9999 8888 7777 666A A", "LI00999988887777666AA"),
    ("277605", "277605"),
    ("16-123456-7", "16-123456-7"),
    ("0.123.456.78", "0.123.456.78"),   # als Betrag geparst wäre es „0.12345678"
    ("", ""),
])
def test_kennung_bleibt_text(roh, erwartet):
    assert kennung(roh) == erwartet


def test_korpus_jedes_konto_mit_nummer_der_leiste():
    """Was die Leiste als Konto-/Depot-Nr. zeigt, steht auch in der Eingabe
    für den Auszug — auf dem anonymisierten Korpus."""
    if not (KORPUS / "_results_full.json").exists():
        pytest.skip("Korpus 2025 fehlt")
    daten = baue(KORPUS)
    konten = [k for h in daten["institute"] for k in h["konten"]]
    assert konten
    assert all(k["nummer"] for k in konten), \
        f"{sum(1 for k in konten if not k['nummer'])} Konto/Konten ohne Nummer"


PYETAX = ROOT / ".venv-etax" / "bin" / "python"

_PRUEFE_XML = r'''
import json, re, sys
from scripts.etax_bauen import baue_auszug, setze_kennung
nummern = json.loads(sys.argv[1])
haus = {"institut": "Musterbank AG", "person": "elternteil_1", "schulden": [],
        "konten": [{"nummer": n, "bezeichnung": f"Konto {i}", "jahr": "2025", "saldo": "1000",
                    "ertrag_mit_vst": "10", "ertrag_ohne_vst": "", "dokumente": []}
                   for i, n in enumerate(nummern)]}
a = baue_auszug(haus, 2025, {"vorname": "Hans", "nachname": "Muster"})
setze_kennung(a, 2025)
x = a.to_xml_bytes().decode()
print(json.dumps({"tags": re.findall(r"<[^>]*bankAccount [^>]*>", x)}))
'''


@pytest.mark.skipif(not PYETAX.exists(), reason=".venv-etax fehlt")
def test_nummer_steht_im_feld_das_zhprivatetax_liest():
    """Im PDF stand die IBAN, nach dem Import in ZHprivateTax war die
    Konto-Nr. leer (der Anwender, 261120): sie stand nur im Feld `iban`. ZHprivateTax
    liest `bankAccountNumber`. Jetzt steht sie in beiden — XSD-gültig."""
    import json
    import subprocess

    nummern = ["LI00999988887777666AA", "277605"]
    aus = subprocess.run([str(PYETAX), "-c", _PRUEFE_XML, json.dumps(nummern)], cwd=ROOT,
                         capture_output=True, text=True, env={"PYTHONPATH": str(ROOT)})
    assert aus.returncode == 0, aus.stderr[-500:]
    # Schemagültig mit beiden Feldern: der echte Bau mit IBAN in
    # test_etax_failclosed.test_zwei_personen_zwei_namen_im_auszug (fail-closed).
    tags = json.loads(aus.stdout.strip().splitlines()[-1])["tags"]
    assert 'bankAccountNumber="LI00999988887777666AA"' in tags[0]
    assert 'iban="LI00999988887777666AA"' in tags[0]
    assert 'bankAccountNumber="277605"' in tags[1]


def test_korpus_mit_pdf_ordner_laeuft_durch(tmp_path):
    """Mit PDF-Ordner läuft die Suche nach Bankauszügen mit (261120: eine
    Variable `bank` überdeckte die Funktion `bank()` — nur mit --pdf-dir, und
    genau diesen Weg ging kein Test)."""
    import shutil

    if not (KORPUS / "_results_full.json").exists():
        pytest.skip("Korpus 2025 fehlt")
    kopie = tmp_path / "json"
    # Ohne die Zuordnung Kürzel → Original: sie gehört nicht zu den Dokumenten
    # und ist in Testläufen gesperrt (Phase 5); `baue` liest sie nicht.
    shutil.copytree(KORPUS, kopie, ignore=shutil.ignore_patterns("anon_truth_mapping.json"))
    ordner = tmp_path / "pdfs"
    ordner.mkdir()
    daten = baue(kopie, pdf_dir=ordner)
    assert daten["vorhanden"] == [] and daten["institute"]
    assert all(k["nummer"] for h in daten["institute"] for k in h["konten"])
