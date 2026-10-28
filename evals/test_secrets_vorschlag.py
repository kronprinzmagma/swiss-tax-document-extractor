"""Tests des Secrets-Vorschlagsgenerators (260904-rmx).

Die Secrets-Datei von Hand zu pflegen wird vergessen — Adressen waren nie
erfasst und blieben deshalb in den „anonymisierten" Testdaten stehen. Der
Generator schlägt sie aus den Dokumenten vor, bewusst ohne Sprachmodell:
eine Schweizer Adresse ist ein regelmässiges Muster.
"""
from __future__ import annotations

from scripts.secrets_vorschlag import ORT_DENYLIST, finde_adressen, pruefe_bestand


# --- Strassen ---------------------------------------------------------------

def test_strasse_mit_hausnummer():
    assert finde_adressen(["wohnhaft an der Musterstrasse 34 in"]) == ["Musterstrasse 34"]


def test_verschiedene_strassentypen():
    treffer = finde_adressen(["Bahnhofweg 7 Limmatquai 68 Marktgasse 12"])
    assert set(treffer) == {"Bahnhofweg 7", "Limmatquai 68", "Marktgasse 12"}


def test_telefonvorwahl_ist_keine_hausnummer():
    """'Limmatquai 044 000 00 00' — die 044 ist eine Vorwahl."""
    assert finde_adressen(["Limmatquai 044 000 00 00"]) == []


def test_plz_ist_keine_hausnummer():
    """Nach der Strasse folgt oft die PLZ der naechsten Zeile."""
    assert "Musterstrasse 6560" not in finde_adressen(["Musterstrasse 6560 Musterhausen"])


def test_hausnummer_mit_buchstabe():
    assert finde_adressen(["Musterstrasse 12a"]) == ["Musterstrasse 12a"]


# --- PLZ + Ort --------------------------------------------------------------

def test_plz_und_ort():
    assert "8000 Musterhausen" in finde_adressen(["CH-8000 Musterhausen"])


def test_jahreszahl_ist_keine_plz():
    assert finde_adressen(["Steuerjahr 2023 Bern"]) == []
    assert finde_adressen(["2045 Faelligkeit"]) == []


def test_formularvokabular_ist_kein_ort():
    """'2501 Kontostand' ist kein Ort."""
    assert finde_adressen(["2501 Kontostand"]) == []
    assert "kontostand" in ORT_DENYLIST


def test_haeufigste_zuerst():
    texte = ["Musterstrasse 34"] * 3 + ["Bahnhofweg 7"]
    assert finde_adressen(texte)[0] == "Musterstrasse 34"


def test_keine_duplikate():
    assert len(finde_adressen(["Musterstrasse 34 Musterstrasse 34"])) == 1


# --- Bestandspruefung -------------------------------------------------------

def test_fehlende_datei(tmp_path):
    assert pruefe_bestand(tmp_path / "weg.yaml") == "existiert nicht"


def test_syntaxfehler_nennt_die_zeile(tmp_path):
    """Ohne Zeilennummer muss man die ganze Datei durchsuchen."""
    p = tmp_path / "kaputt.yaml"
    p.write_text("institutions:\n  banks:\n    - eins\n   - zwei\n")
    ergebnis = pruefe_bestand(p)
    assert "SYNTAXFEHLER" in ergebnis
    assert "Zeile" in ergebnis


def test_gueltige_datei_nennt_nur_schluessel_und_anzahlen(tmp_path):
    """Der Bericht darf keine Werte zeigen — er soll teilbar sein."""
    p = tmp_path / "ok.yaml"
    p.write_text('addresses:\n  - "Geheimstrasse 1"\n')
    ergebnis = pruefe_bestand(p)
    assert "addresses(1)" in ergebnis
    assert "Geheimstrasse" not in ergebnis
