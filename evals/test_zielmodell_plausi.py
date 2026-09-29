"""Regressionstests für Lohnausweis-Plausi + VSt-Label-Pflicht (Quick 260612-l2g).

Rein synthetisch — eigene values-Dicts, keine echten Sample-Werte. Deterministisch,
kein Ollama.
"""
import pytest

from extractors.steuer_zielmodell import (
    rule_lohnausweis_vertauschung,
    rule_verrechnungssteuer_label_pflicht,
)


# --------------------------------------------------------------------------- #
# E2/E4 — VSt-Label-Pflicht                                                    #
# --------------------------------------------------------------------------- #


def test_vrs_label_fehlt_loest_befund_aus():
    """VRS-Wert aus Ertragsspalte (Snippet ohne VSt-Label) → Befund."""
    befunde = rule_verrechnungssteuer_label_pflicht({
        "verrechnungssteuer": "48.90",
        "verrechnungssteuer_snippet": "Ertrag Rubrik B 48.90",
    })
    assert befunde
    assert any("Label fehlt" in b for b in befunde)


def test_vrs_mit_label_kein_befund():
    """VRS-Wert mit Verrechnungssteuer-Label in der Nähe → akzeptiert."""
    befunde = rule_verrechnungssteuer_label_pflicht({
        "verrechnungssteuer": "48.90",
        "verrechnungssteuer_snippet": "Verrechnungssteuer 48.90",
    })
    assert befunde == []


def test_vrs_total_mit_rubrik_a_kein_befund():
    befunde = rule_verrechnungssteuer_label_pflicht({
        "verrechnungssteuer_total": "120.00",
        "verrechnungssteuer_total_snippet": "Rubrik A Total 120.00",
    })
    assert befunde == []


def test_vrs_manual_review_kein_befund():
    """Bereits als manual_review markiert → kein zusätzlicher Befund."""
    befunde = rule_verrechnungssteuer_label_pflicht({
        "verrechnungssteuer": "manual_review:vrs_not_reported",
        "verrechnungssteuer_snippet": "",
    })
    assert befunde == []


def test_vrs_leer_kein_befund():
    assert rule_verrechnungssteuer_label_pflicht({"verrechnungssteuer": ""}) == []


# --------------------------------------------------------------------------- #
# E3 — Lohnausweis-Vertauschung (Checks a-c+e)                                 #
# --------------------------------------------------------------------------- #


def test_lohnausweis_bvg_gleich_netto():
    """(a) BVG == Nettolohn → Befund."""
    befunde = rule_lohnausweis_vertauschung({
        "bruttolohn_pos8": "100000",
        "ahv_alv_nbu_abzug_pos9": "6000",
        "bvg_abzug_pos10a": "90000",
        "nettolohn_pos11": "90000",
    })
    assert any("BVG (Pos 10a) == Nettolohn" in b for b in befunde)


def test_lohnausweis_bvg_ueber_25_prozent():
    """(b) BVG > 25% Brutto → Befund."""
    befunde = rule_lohnausweis_vertauschung({
        "bruttolohn_pos8": "100000",
        "ahv_alv_nbu_abzug_pos9": "6000",
        "bvg_abzug_pos10a": "30000",   # 30% > 25%
        "nettolohn_pos11": "64000",
    })
    assert any("> 25%" in b for b in befunde)


def test_lohnausweis_ahv_ueber_15_prozent():
    """(c) AHV > 15% Brutto → Befund."""
    befunde = rule_lohnausweis_vertauschung({
        "bruttolohn_pos8": "100000",
        "ahv_alv_nbu_abzug_pos9": "20000",  # 20% > 15%
        "bvg_abzug_pos10a": "5000",
        "nettolohn_pos11": "75000",
    })
    assert any("> 15%" in b for b in befunde)


def test_lohnausweis_quersumme_abweichung():
    """(e) Brutto−AHV−BVG weicht >5% von Netto ab → Befund."""
    befunde = rule_lohnausweis_vertauschung({
        "bruttolohn_pos8": "100000",
        "ahv_alv_nbu_abzug_pos9": "6000",
        "bvg_abzug_pos10a": "5000",
        "nettolohn_pos11": "50000",   # erwartet 89000, weicht stark ab
    })
    assert any("Quersumme" in b for b in befunde)


def test_lohnausweis_plausibel_kein_befund():
    """Konsistenter Lohnausweis → keine Befunde."""
    befunde = rule_lohnausweis_vertauschung({
        "bruttolohn_pos8": "100000",
        "ahv_alv_nbu_abzug_pos9": "6000",   # 6%
        "bvg_abzug_pos10a": "5000",         # 5%
        "nettolohn_pos11": "89000",         # = 100000 - 6000 - 5000
    })
    assert befunde == []


def test_lohnausweis_fehlende_felder_kein_crash():
    """Unvollständige Felder → keine Befunde, kein Crash."""
    assert rule_lohnausweis_vertauschung({"bruttolohn_pos8": "100000"}) == []


# --------------------------------------------------------------------------- #
# E3 (d) — Arbeitgeber enthält Familienname (synthetische family-Fixture)      #
# --------------------------------------------------------------------------- #


class _FakeMember:
    def __init__(self, first, last):
        self.first_name = first
        self.last_name = last


class _FakeFamily:
    def __init__(self, members):
        self.members = members


def test_arbeitgeber_ist_person_erkannt(monkeypatch):
    import scripts.process_samples_full as psf

    fam = _FakeFamily([_FakeMember("Felix", "Beispiel")])
    monkeypatch.setattr("extractors.family.load_family", lambda *a, **k: fam)

    # Beide Tokens vorhanden (auch in umgekehrter Reihenfolge / mit Anrede).
    assert psf._arbeitgeber_enthaelt_familienname("Felix Beispiel") is True
    assert psf._arbeitgeber_enthaelt_familienname("Beispiel Felix") is True
    assert psf._arbeitgeber_enthaelt_familienname("Herr Felix Beispiel") is True


def test_arbeitgeber_firma_nicht_als_person(monkeypatch):
    import scripts.process_samples_full as psf

    fam = _FakeFamily([_FakeMember("Felix", "Beispiel")])
    monkeypatch.setattr("extractors.family.load_family", lambda *a, **k: fam)

    # Nur Firma, kein vollständiges Namens-Paar → kein Treffer.
    assert psf._arbeitgeber_enthaelt_familienname("BeispielFirma AG") is False
    assert psf._arbeitgeber_enthaelt_familienname("INST-A") is False


def test_arbeitgeber_ohne_family_yaml(monkeypatch):
    import scripts.process_samples_full as psf

    monkeypatch.setattr("extractors.family.load_family", lambda *a, **k: None)
    assert psf._arbeitgeber_enthaelt_familienname("Felix Beispiel") is False
