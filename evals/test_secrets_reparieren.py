"""Tests der Secrets-Reparatur (260904-rmx).

Ein einziger Einrückungsfehler machte die Datei für YAML unlesbar — und damit
den Anonymizer wirkungslos, ohne dass es im Alltag auffiel. Der tolerante
Zeilenleser holt heraus, was herauszuholen ist.
"""
from __future__ import annotations

import yaml

from scripts.secrets_reparieren import lies_tolerant, schreibe_sauber


def datei(tmp_path, inhalt: str, name: str = "s.yaml"):
    p = tmp_path / name
    p.write_text(inhalt, encoding="utf-8")
    return p


def test_gueltige_datei_wird_vollstaendig_gelesen(tmp_path):
    p = datei(tmp_path, 'addresses:\n  - "Musterstrasse 1"\n  - "8000 Ort"\n')
    daten, unlesbar = lies_tolerant(p)
    assert daten["addresses"] == ["Musterstrasse 1", "8000 Ort"]
    assert unlesbar == 0


def test_kaputte_einrueckung_verliert_nichts(tmp_path):
    """Genau der Fehler, der die echte Datei blockierte."""
    p = datei(tmp_path, 'addresses:\n    - "Eins"\n   - "Zwei"\n     - "Drei"\n')
    daten, _ = lies_tolerant(p)
    assert daten["addresses"] == ["Eins", "Zwei", "Drei"]


def test_institutions_untergliederung(tmp_path):
    p = datei(tmp_path,
              'institutions:\n  banks:\n    - "Bank A"\n  kk:\n    - "Kasse B"\n')
    daten, _ = lies_tolerant(p)
    assert daten["institutions.banks"] == ["Bank A"]
    assert daten["institutions.kk"] == ["Kasse B"]


def test_kommentare_und_leerzeilen_stoeren_nicht(tmp_path):
    p = datei(tmp_path, '# Kommentar\n\naddresses:\n  - "Eins"   # Notiz\n')
    daten, unlesbar = lies_tolerant(p)
    assert daten["addresses"] == ["Eins"]
    assert unlesbar == 0


def test_leere_liste_wird_ignoriert(tmp_path):
    p = datei(tmp_path, "addresses:\n    []\n")
    daten, _ = lies_tolerant(p)
    assert daten.get("addresses", []) == []


def test_duplikate_werden_entfernt(tmp_path):
    p = datei(tmp_path, 'addresses:\n  - "Eins"\n  - "Eins"\n')
    daten, _ = lies_tolerant(p)
    assert daten["addresses"] == ["Eins"]


def test_unlesbare_zeilen_werden_gezaehlt(tmp_path):
    p = datei(tmp_path, "addresses:\n  - Eins\nkaputt ohne doppelpunkt\n")
    _, unlesbar = lies_tolerant(p)
    assert unlesbar == 1


def test_fehlende_datei(tmp_path):
    assert lies_tolerant(tmp_path / "weg.yaml") == ({}, 0)


# --- Ausgabe ----------------------------------------------------------------

def test_ergebnis_ist_gueltiges_yaml():
    inhalt = schreibe_sauber({
        "institutions.banks": ["Bank A"],
        "addresses": ["Musterstrasse 1"],
        "persons": ["Dritte Person"],
    })
    daten = yaml.safe_load(inhalt)
    assert daten["institutions"]["banks"] == ["Bank A"]
    assert daten["addresses"] == ["Musterstrasse 1"]
    assert daten["persons"] == ["Dritte Person"]


def test_leere_abschnitte_bleiben_gueltig():
    daten = yaml.safe_load(schreibe_sauber({}))
    assert daten["addresses"] == []
    assert daten["institutions"]["weitere"] == []


def test_rundlauf_verliert_nichts(tmp_path):
    """Lesen, schreiben, wieder lesen — gleiche Menge."""
    original = {"institutions.kk": ["Kasse B"], "addresses": ["Eins", "Zwei"]}
    p = datei(tmp_path, schreibe_sauber(original))
    wieder, unlesbar = lies_tolerant(p)
    assert unlesbar == 0
    assert wieder["addresses"] == ["Eins", "Zwei"]
    assert wieder["institutions.kk"] == ["Kasse B"]


def test_eintraege_ohne_unterschluessel_gehen_nicht_verloren():
    """Standen sie direkt unter institutions:, wuerden sie sonst verschwinden —
    und der Anonymizer liesse diese Namen ab dann stehen."""
    inhalt = schreibe_sauber({"institutions": ["Ohne Unterschluessel"]})
    daten = yaml.safe_load(inhalt)
    assert "Ohne Unterschluessel" in daten["institutions"]["weitere"]


def test_unbekannter_unterschluessel_wird_gerettet():
    inhalt = schreibe_sauber({"institutions.exotisch": ["Etwas"]})
    assert "Etwas" in yaml.safe_load(inhalt)["institutions"]["weitere"]


def test_bekannte_unterschluessel_bleiben_wo_sie_sind():
    daten = yaml.safe_load(schreibe_sauber({"institutions.banks": ["Bank A"]}))
    assert daten["institutions"]["banks"] == ["Bank A"]
    assert daten["institutions"].get("weitere", []) == []
