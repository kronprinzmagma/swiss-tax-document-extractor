"""Ersatztexte in re.sub müssen buchstäblich sein (260904-rmx).

``re.sub`` deutet den Ersatz als Template. Enthält ein Instituts- oder
Personenname einen Backslash oder eine Folge wie ``\\1``, stürzt der Lauf mit
``bad escape`` ab — oder, schlimmer, es wird still ein falscher Text
eingesetzt und die Anonymisierung bleibt unvollständig.

Aufgefallen, als die Secrets-Datei zum ersten Mal gefüllt war: der ganze
Anonymisierungslauf brach ab.
"""
from __future__ import annotations

import re

import pytest

from scripts.anonymize_belege import _literal, _scrub_filename_stem


def test_literal_gibt_den_text_unveraendert_zurueck():
    ersatz = _literal("BANK-A")
    assert re.sub("x", ersatz, "axb") == "aBANK-Ab"


def test_backslash_im_ersatz_stuerzt_nicht_ab():
    """Genau der Absturz: 'bad escape (end of pattern)'."""
    assert re.sub("x", _literal("Firma\\"), "x") == "Firma\\"


def test_rueckverweis_wird_nicht_ausgewertet():
    """'\\1' im Namen darf keine Gruppe einsetzen — sonst still falsch."""
    assert re.sub("(a)x", _literal("Marke\\1"), "ax") == "Marke\\1"


def test_dollar_und_klammern_bleiben_stehen():
    assert re.sub("x", _literal("A&B (AG)"), "x") == "A&B (AG)"


# --- Zusammenspiel mit dem Datei-Stem-Scrub ---------------------------------

def test_stem_scrub_mit_backslash_im_marker():
    stem = _scrub_filename_stem("Auszug Firma 2025",
                                {"Firma": "INST-A\\"}, None)
    assert stem == "Auszug INST-A\\ 2025"


def test_stem_scrub_gewoehnlicher_fall():
    assert _scrub_filename_stem("Auszug Beispielbank 2025",
                                {"Beispielbank": "BANK-A"}, None) \
        == "Auszug BANK-A 2025"


def test_stem_scrub_case_insensitiv():
    assert _scrub_filename_stem("auszug BEISPIELBANK", {"Beispielbank": "BANK-A"},
                                None) == "auszug BANK-A"


def test_stem_scrub_laengste_zuerst():
    """Teiltreffer duerfen nicht zu frueh greifen."""
    karte = {"Bank": "KURZ", "Beispielbank": "LANG"}
    assert _scrub_filename_stem("Beispielbank", karte, None) == "LANG"


def test_stem_scrub_family_pairs_mit_sonderzeichen():
    assert _scrub_filename_stem("Lohn Muster\\Test",
                                None, [("Muster\\Test", "Fantasy")]) \
        == "Lohn Fantasy"


@pytest.mark.parametrize("marker", ["A\\", "\\g<1>", "\\\\", "x\\ny"])
def test_verschiedene_gefaehrliche_marker(marker):
    assert _scrub_filename_stem("Firma", {"Firma": marker}, None) == marker
