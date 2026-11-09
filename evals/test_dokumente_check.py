"""Kein Dokument darf lautlos verschwinden (260904-rmx).

Ein Scan ohne Textebene, fuer den kein OCR verfuegbar ist, erzeugt eine
Warnung auf stderr und ist danach weg: kein Word-JSON, keine Zeile, kein
Eintrag in irgendeiner Liste. Wer die Warnung im Scrollback uebersieht, hat
einen Beleg weniger in der Steuererklaerung — und der Kern des Projekts ist
gerade, dass nichts vergessen geht.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def baue(tmp_path, pdfs, jsons, ergebnisse):
    quelle, ziel = tmp_path / "quelle", tmp_path / "json"
    quelle.mkdir()
    ziel.mkdir()
    for n in pdfs:
        (quelle / n).write_bytes(b"%PDF-1.4\n")
    for n, typ in jsons.items():
        (ziel / (n.removesuffix(".pdf") + ".json")).write_text(
            json.dumps({"pdf_name": n, "belegtyp": typ, "pages": []}))
    (ziel / "_results_full.json").write_text(json.dumps(ergebnisse))
    return quelle, ziel


def lauf(quelle, ziel, *extra) -> str:
    r = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "dokumente_check.py"),
         "--input", str(quelle), "--samples", str(ziel), *extra],
        capture_output=True, text=True,
        env={"PYTHONPATH": str(ROOT), "PATH": "/usr/bin:/bin"})
    return r.stdout


def test_nicht_eingelesenes_pdf_wird_gemeldet(tmp_path):
    quelle, ziel = baue(tmp_path, ["scan.pdf"], {}, [])
    aus = lauf(quelle, ziel)
    assert "PDF nicht eingelesen" in aus
    assert "scan.pdf" in aus


def test_eingelesen_aber_nicht_extrahiert(tmp_path):
    quelle, ziel = baue(tmp_path, ["a.pdf"], {"a.pdf": "saeule_3a"}, [])
    assert "nicht extrahiert" in lauf(quelle, ziel)


def test_extrahiert_aber_ohne_wert():
    """Ein Dokument ohne uebertragbaren Wert faellt in eine eigene Gruppe.

    Direkt gegen die Einordnung geprueft: ein Ergebnis mit leeren Feldern
    liefert trotzdem eine Platzhalterzeile, laesst sich also nicht ueber den
    Weg durch die Pipeline herstellen."""
    from scripts.dokumente_check import _einordnen
    gruppen = _einordnen({"a.pdf": {"im_ordner": True, "json": True,
                                    "ergebnis": "ok", "zeilen": 0,
                                    "belegtyp": "saeule_3a"}})
    assert gruppen["ohne_zeile"] == ["a.pdf"]


def test_ergebnis_ohne_pdf_ist_verwaist(tmp_path):
    """Nicht zu verwechseln mit 'PDF nicht eingelesen' — erste Fassung
    beschriftete beide Faelle vertauscht."""
    quelle, ziel = baue(tmp_path, [], {},
                        [{"pdf_name": "weg.pdf", "status": "ok",
                          "belegtyp": "saeule_3a", "fields": []}])
    aus = lauf(quelle, ziel)
    assert "Ergebnis ohne PDF" in aus
    zeile = [z for z in aus.splitlines() if "weg.pdf" in z]
    assert zeile, aus
    assert "Ergebnis ohne PDF" in aus.split("weg.pdf")[0].split("\n\n")[-1]


def test_vollstaendiger_lauf_meldet_nichts(tmp_path):
    quelle, ziel = baue(
        tmp_path, ["a.pdf"], {"a.pdf": "saeule_3a"},
        [{"pdf_name": "a.pdf", "status": "ok", "belegtyp": "saeule_3a",
          "fields": [{"feld": "einzahlung_betrag", "value": "6883.00",
                      "bbox": [1, 2, 3, 4], "page": 1, "anchor_valid": True}]}])
    assert "Jedes Dokument ist angekommen" in lauf(quelle, ziel)


def test_nur_zahlen_nennt_keine_dateinamen(tmp_path):
    """Steuerbelege heissen nach den Menschen, um die es geht."""
    quelle, ziel = baue(tmp_path, ["Auszug Vorname Nachname.pdf"], {}, [])
    aus = lauf(quelle, ziel, "--nur-zahlen")
    assert "Nachname" not in aus
    assert "PDF nicht eingelesen" in aus


# --- Einzelabfrage ----------------------------------------------------------

def test_einzelnes_dokument_erklaert_sein_fehlen(tmp_path):
    """"Das Dokument finde ich nicht" braucht eine Antwort, keine Vermutung."""
    quelle, ziel = baue(
        tmp_path, ["Kontoauszug - 2025 - Bank.pdf"],
        {"Kontoauszug - 2025 - Bank.pdf": "bank_zinsausweis"},
        [{"pdf_name": "Kontoauszug - 2025 - Bank.pdf", "status": "ok",
          "belegtyp": "bank_zinsausweis",
          "fields": [{"feld": "vermoegensstand_3112", "value": "0.00",
                      "bbox": [1, 2, 3, 4], "page": 1, "anchor_valid": True}]}])
    aus = lauf(quelle, ziel, "--dokument", "Kontoauszug - 2025")
    assert "Betrag null" in aus
    assert "nicht zugeordneten" in aus


def test_einzelabfrage_versteht_prozentkodierung(tmp_path):
    quelle, ziel = baue(tmp_path, ["Kontoauszug - 2025 - Bank.pdf"], {}, [])
    assert "Kontoauszug" in lauf(quelle, ziel, "--dokument",
                                 "Kontoauszug%20-%202025")


def test_einzelabfrage_ohne_treffer_sagt_das(tmp_path):
    quelle, ziel = baue(tmp_path, ["a.pdf"], {}, [])
    aus = lauf(quelle, ziel, "--dokument", "gibtesnicht")
    assert "Kein Dokument passt" in aus
