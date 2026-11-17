"""Der selbst gebaute eSteuerauszug muss importierbar sein (261024-jga).

ZHprivateTax wies alle zehn Auszuege ab: „Der Barcode enthält keine
gültigen Daten." Der Barcode war in Ordnung — das XML darin verletzte das
Schema eCH-0196 an fuenf Stellen. Kein Test hatte das XML je gegen das
Schema gehalten; geprueft wurde nur, ob ein Bild auf der Seite ist.

Hier wird der ganze Weg nachgestellt, mit erfundenen Werten: Auszug bauen,
gegen das Schema pruefen, PDF rendern, Barcode wieder auslesen, entpacken —
und das muss genau das geprueft XML ergeben.

Laeuft nur, wo `.venv-etax` existiert (die Bibliothek bringt Git-Forks mit
und lebt deshalb in einem eigenen venv).
"""
from __future__ import annotations

import itertools
import json
import subprocess
import zlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PYETAX = ROOT / ".venv-etax" / "bin" / "python"

pytestmark = pytest.mark.skipif(not PYETAX.exists(),
                                reason=".venv-etax fehlt")

# Erfunden. Deckt ab: Kolonne A und B, Konto ohne Ertrag, Konto ohne
# IBAN, drei Schulden, Umlaut im Institut, dieselbe Bank fuer zwei Personen.
INSTITUTE = [
    {"institut": "Zürcher Musterbank", "person": "elternteil_1",
     "konten": [
         {"nummer": "CH9300762011623852957", "bezeichnung": "Privatkonto",
          "jahr": "2025", "saldo": "12'345.60", "ertrag_mit_vst": "12.30",
          "ertrag_ohne_vst": ""},
         {"nummer": "", "bezeichnung": "Sparkonto", "jahr": "2025",
          "saldo": "500", "ertrag_mit_vst": "", "ertrag_ohne_vst": ""}],
     "schulden": [
         {"bezeichnung": f"Festhypothek {i}", "jahr": "2025",
          "schuld": str(100000 * i), "zins": str(900 * i)}
         for i in (1, 2, 3)]},
    {"institut": "Zürcher Musterbank", "person": "elternteil_2",
     "konten": [{"nummer": "CH9300762011623852957", "bezeichnung": "Konto",
                 "jahr": "2025", "saldo": "800", "ertrag_mit_vst": "",
                 "ertrag_ohne_vst": "3.10"}],
     "schulden": []},
]

_BAU = r"""
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import etax_bauen as eb
from opensteuerauszug.calculate.total import TotalCalculator
from opensteuerauszug.render.render import render_tax_statement
ziel = Path(sys.argv[2])
aus = []
for i, haus in enumerate(json.loads(sys.argv[3])):
    a = eb.baue_auszug(haus, 2025, {"vorname": "Max", "nachname": "Muster"})
    eb.setze_kennung(a, 2025)
    TotalCalculator().calculate(a)
    xml = ziel / f"{i}.xml"
    xml.write_bytes(a.to_xml_bytes())
    pdf = render_tax_statement(a, ziel / f"{i}.pdf")
    aus.append({"id": a.id, "fehler": eb.schemafehler(a),
                "xml": str(xml), "pdf": str(pdf)})
print(json.dumps(aus))
"""


@pytest.fixture(scope="module")
def gebaut(tmp_path_factory):
    ziel = tmp_path_factory.mktemp("etax")
    lauf = subprocess.run(
        [str(PYETAX), "-c", _BAU, str(ROOT / "scripts"), str(ziel),
         json.dumps(INSTITUTE)],
        capture_output=True, text=True, timeout=300)
    assert lauf.returncode == 0, lauf.stderr[-2000:]
    return json.loads(lauf.stdout.strip().splitlines()[-1])


def test_xml_besteht_das_schema(gebaut):
    for auszug in gebaut:
        assert auszug["fehler"] == [], auszug["fehler"]


def test_kennung_traegt_stichtag_und_ist_eindeutig(gebaut):
    ids = [a["id"] for a in gebaut]
    assert all(len(i) == 31 and i.endswith("2025123101") for i in ids), ids
    # Dieselbe Bank, zwei Personen: zwei Auszuege, nicht zweimal derselbe.
    assert len(set(ids)) == len(ids), ids


def test_pflichtangaben_stehen_im_xml(gebaut):
    xml = Path(gebaut[0]["xml"]).read_text(encoding="utf-8")
    assert 'canton="ZH"' in xml
    assert 'minorVersion="22"' in xml
    assert 'clientNumber="E1"' in xml
    # 35 % von 12.30 — der Rueckerstattungsanspruch aus Kolonne A.
    assert 'withHoldingTaxClaim="4.31"' in xml


def test_barcode_ergibt_genau_das_xml(gebaut):
    pdfplumber = pytest.importorskip("pdfplumber")
    zxingcpp = pytest.importorskip("zxingcpp")
    for auszug in gebaut:
        teile = []
        with pdfplumber.open(auszug["pdf"]) as pdf:
            for seite in pdf.pages:
                bild = seite.to_image(resolution=300).original
                teile += [t.bytes for t in zxingcpp.read_barcodes(
                    bild, formats=zxingcpp.BarcodeFormat.PDF417)]
        assert teile, "kein Barcode im PDF"
        # zxing nennt die Segmentnummer nicht; bei wenigen Teilen ist das
        # Durchprobieren der Reihenfolge eindeutig (zlib prueft die Summe).
        erwartet = Path(auszug["xml"]).read_bytes()
        gefunden = None
        for folge in itertools.permutations(teile):
            try:
                gefunden = zlib.decompress(b"".join(folge))
                break
            except zlib.error:
                continue
        assert gefunden == erwartet


def test_die_pruefung_erkennt_den_auszug_als_esteuerauszug(gebaut):
    """Gegenprobe zu `make esteuerauszug`: ein echter eCH-0196-Barcode."""
    from scripts.esteuerauszug_check import barcode_art

    art = barcode_art(Path(gebaut[0]["pdf"]))
    assert art["art"] == "eSteuerauszug (eCH-0196), vollständig gelesen"
    assert art["konten"] == 2 and art["schulden"] == 3
