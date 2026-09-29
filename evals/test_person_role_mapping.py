"""Tests fuer map_person_to_role in scripts/build_tax_output.

Deckt beide Stufen ab: Fantasy-Vornamen (Sample-Pfad) und echte
family.yaml-Vornamen (produktiver Pfad). Es werden ausschliesslich
synthetische Namen verwendet; die Real-Namen-Map wird gemonkeypatcht.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import build_tax_output as bto


SYNTH_REAL_MAP = {
    # normalisierte Tokens (lower, diakritika-gefoldet) → Rollen-Sets
    "zora": {bto.ROLE_ELTERNTEIL_2},
    "balthasar": {bto.ROLE_ELTERNTEIL_1},
    "cleo": {bto.ROLE_KIND_1},
}

SYNTH_FANTASY_MAP = {
    "Hans": bto.ROLE_ELTERNTEIL_1,
    "Maria": bto.ROLE_ELTERNTEIL_2,
}


@pytest.fixture(autouse=True)
def _patch_maps(monkeypatch):
    monkeypatch.setattr(bto, "_REAL_FIRSTNAME_TO_ROLE", SYNTH_REAL_MAP)
    monkeypatch.setattr(bto, "_FANTASY_FIRSTNAME_TO_ROLE", SYNTH_FANTASY_MAP)


def test_fantasy_name_hat_vorrang():
    assert bto.map_person_to_role("Hans Muster") == bto.ROLE_ELTERNTEIL_1


def test_echter_vorname_einfach():
    assert bto.map_person_to_role("Zora Beispielmann") == bto.ROLE_ELTERNTEIL_2


def test_echter_vorname_grossschreibung_pdf():
    # PDFs liefern oft Versalien — Tokens werden normalisiert.
    assert bto.map_person_to_role("BALTHASAR BEISPIELMANN") == bto.ROLE_ELTERNTEIL_1


def test_echter_vorname_nachname_zuerst():
    assert bto.map_person_to_role("Beispielmann Cleo") == bto.ROLE_KIND_1


def test_mehrere_echte_namen_ergibt_familie():
    assert bto.map_person_to_role("Zora und Balthasar Beispielmann") == bto.ROLE_FAMILIE


def test_unbekannter_name_bleibt_unknown():
    assert bto.map_person_to_role("Unbekannte Person") == bto.ROLE_UNKNOWN


def test_none_und_marker_bleiben_unknown():
    assert bto.map_person_to_role(None) == bto.ROLE_UNKNOWN
    assert bto.map_person_to_role("") == bto.ROLE_UNKNOWN


def test_substring_matcht_nicht():
    # "Zorath" enthaelt "zora" nur als Substring — Token-Match darf nicht greifen.
    assert bto.map_person_to_role("Zorath Beispielmann") == bto.ROLE_UNKNOWN


# --------------------------------------------------------------------------- #
# Label-Präfix-Cleanup + kanonischer Rollen-Name (Fix nach Re-Anonymisierung)  #
# --------------------------------------------------------------------------- #

def test_clean_display_name_label_praefix_ohne_name():
    # "Kundennummer: 1234567" enthaelt keinen Namen → None, kein Label-Garbage.
    assert bto.clean_display_name("Kundennummer: 1234567") is None
    assert bto.clean_display_name("Vertrags-Nr. 998877") is None


def test_clean_display_name_label_praefix_mit_name():
    assert bto.clean_display_name("Kundennummer: Hans Muster") == "Hans Muster"


def test_format_person_display_faellt_auf_kanonischen_namen_zurueck(monkeypatch):
    monkeypatch.setattr(bto, "_FANTASY_FIRSTNAME_TO_ROLE", {"Lina": bto.ROLE_KIND_1})
    monkeypatch.setattr(bto, "_REAL_FIRSTNAME_TO_ROLE", {})
    # Roher String hat keinen brauchbaren Namen, aber eindeutige Rolle.
    out = bto.format_person_display("Kundennummer: Lina")
    assert out == "Lina (kind_1)"


def test_clean_display_name_klammer_anhang_mit_nummer():
    assert bto.clean_display_name("Muster, Lina (Kundennummer: 954-95-594)") == "Muster, Lina"
