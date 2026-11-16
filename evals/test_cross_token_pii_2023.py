"""Regressions-Tests für den dritten 2023er-Verifikationslauf (260630-dsn).

Vier eng umrissene Lücken:
A1: Telefonnummern mit langem numerischem Endblock (z.B. "+41 11 1111111")
    rutschen durch ``_scrub_cross_token_pii`` (nur 2–4-stellige Body-Chunks).
A2: Drittpersonen-Namen über Token-Grenzen werden nie gescrubbt — neuer Pass
    ``_scrub_cross_token_thirdparty_names``.
A3: Privacy-Gate flaggt "NNNN Abrechnungsperiode" fälschlich als plz_ort.
A4: ``harden_dividende_bruttoertrag`` sieht nur den engen Snippet, nicht den
    Zeilen-Kontext (in dem "Dividende" steht).

PRIVACY: ausschliesslich synthetische Daten — KEINE echten Namen/Nummern.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anonymize_belege as anon
import process_samples_full as psf
from scripts import privacy_gate


def _words(*texts: str) -> list[dict]:
    """Synthetische Wortliste — nur ``text`` ist für die Scrubber relevant."""
    return [{"text": t} for t in texts]


def _texts(words: list[dict]) -> list[str]:
    return [w["text"] for w in words]


# --------------------------------------------------------------------------- #
# A1: Telefon-Langendblock                                                     #
# --------------------------------------------------------------------------- #
def test_a1_intl_prefix_langer_endblock_gescrubbt():
    # +41 (Intl-Marker) + 2-stellige Vorwahl + 7-stelliger Endblock → 11 Ziffern.
    w = _words("+41", "11", "1111111")
    anon._scrub_cross_token_pii(w)
    assert _texts(w)[0] == "044"  # Intl-Prefix ersetzt
    # Body-Tokens (Vorwahl + Endblock) sind unkenntlich (kein Originalwert mehr).
    assert "11" not in _texts(w)[1:]
    assert "1111111" not in _texts(w)


def test_a1_label_marker_langer_endblock_gescrubbt():
    # "Tel." (Wort-Marker) + 3-stellig + 7-stellig.
    w = _words("Tel.", "044", "1234567")
    anon._scrub_cross_token_pii(w)
    assert _texts(w)[0] == "Tel."  # Wort-Marker bleibt
    assert "1234567" not in _texts(w)
    assert "044" not in _texts(w)[1:]  # Vorwahl-Body ersetzt


def test_a1_ohne_marker_unveraendert():
    # Konservativ-Erhalt: kein Marker-Token davor → Valoren-/Betragsschutz.
    w = _words("43", "1234567")
    anon._scrub_cross_token_pii(w)
    assert _texts(w) == ["43", "1234567"]


def test_a1_bestehende_kurzform_regression():
    # Bestehende Variante muss weiterhin gescrubbt werden.
    w = _words("+41", "62", "885", "11", "11")
    anon._scrub_cross_token_pii(w)
    assert _texts(w)[0] == "044"
    assert "62" not in _texts(w)[1:]
    assert "885" not in _texts(w)


# --------------------------------------------------------------------------- #
# A2: Cross-Token-Drittnamen                                                   #
# --------------------------------------------------------------------------- #
def test_a2_name_datum_betrag_gescrubbt():
    w = _words("Beispiel-Muster", "Erika", "09.06.2023", "64.71")
    anon._scrub_cross_token_thirdparty_names(w)
    txt = _texts(w)
    assert txt[0] == "PERSON-X"
    assert txt[1] == ""  # Folge-Namens-Token geleert
    assert txt[2] == "09.06.2023"  # Datum erhalten
    assert txt[3] == "64.71"  # Betrag erhalten


def test_a2_datum_name_ort_betrag_gescrubbt():
    w = _words("09.06.2023", "Beispiel-Muster", "Erika,", "Teststadt", "64.71")
    anon._scrub_cross_token_thirdparty_names(w)
    txt = _texts(w)
    assert txt[0] == "09.06.2023"  # Datum erhalten
    # Name + Ort-Span ist unkenntlich: weder "Erika," noch "Teststadt" bleiben.
    assert "Erika," not in txt
    assert "Teststadt" not in txt
    assert "PERSON-X" in txt
    assert txt[-1] == "64.71"  # Betrag erhalten


def test_a2_label_whitelist_unveraendert():
    w = _words("Selbstbehalt", "22.12.2023", "50.00")
    anon._scrub_cross_token_thirdparty_names(w)
    assert _texts(w) == ["Selbstbehalt", "22.12.2023", "50.00"]


def test_a2_family_name_geschuetzt():
    # Family-Name (synthetisch über das Set mitgegeben, kein Hartcoding) darf
    # nicht als Drittperson gescrubbt werden.
    family = {"Beispiel-Muster", "Erika"}
    w = _words("Beispiel-Muster", "Erika", "09.06.2023", "64.71")
    anon._scrub_cross_token_thirdparty_names(w, family_first_last=family)
    assert _texts(w)[0] == "Beispiel-Muster"
    assert _texts(w)[1] == "Erika"


# --------------------------------------------------------------------------- #
# A3: Gate plz_ort Label-Denylist                                             #
# --------------------------------------------------------------------------- #
def test_a3_label_wort_als_ort_ist_placeholder():
    assert privacy_gate._is_placeholder_match("1267 Abrechnungsperiode", "plz_ort_real") is True


def test_a3_echter_ort_bleibt_treffer():
    assert privacy_gate._is_placeholder_match("8001 Zürich", "plz_ort_real") is False


def test_a3_weitere_denylist_woerter():
    for label in ("Police", "Prämien", "Jahrgang", "Versicherte"):
        assert privacy_gate._is_placeholder_match(f"1267 {label}", "plz_ort_real") is True


# --------------------------------------------------------------------------- #
# A4: Dividenden-Zeilen-Kontext                                                #
# --------------------------------------------------------------------------- #
def test_a4_dividende_im_zeilenkontext_demotiert():
    # Snippet enthält KEIN "Dividende", aber die Zeile (words) tut es.
    words = [
        {"text": "Dividende", "x0": 10, "top": 100, "x1": 60, "bottom": 110, "page": 1},
        {"text": "ROCHE", "x0": 65, "top": 100, "x1": 95, "bottom": 110, "page": 1},
        {"text": "GS", "x0": 100, "top": 100, "x1": 115, "bottom": 110, "page": 1},
        {"text": "116.70", "x0": 120, "top": 100, "x1": 160, "bottom": 110, "page": 1},
    ]
    fields = [{
        "feld": "bruttoertrag", "value": "116.70",
        "snippet": "116.70",  # enger Snippet ohne "Dividende"
        "anchor_valid": True, "bbox": [120, 100, 160, 110], "page": 1,
    }]
    psf.harden_dividende_bruttoertrag(fields, words=words)
    assert fields[0]["value"] == "manual_review:dividende_im_kontoauszug"
    assert fields[0]["anchor_valid"] is False


def test_a4_snippet_fallback_ohne_words():
    # Reine Snippet-Variante funktioniert weiter (Default words=None).
    fields = [{
        "feld": "bruttoertrag", "value": "116.70",
        "snippet": "Dividende ROCHE GS 116.70",
        "anchor_valid": True, "bbox": [0, 0, 10, 10], "page": 1,
    }]
    psf.harden_dividende_bruttoertrag(fields)
    assert fields[0]["value"] == "manual_review:dividende_im_kontoauszug"


def test_a4_kein_dividende_in_snippet_noch_kontext():
    words = [
        {"text": "Bruttozins", "x0": 10, "top": 100, "x1": 60, "bottom": 110, "page": 1},
        {"text": "116.70", "x0": 120, "top": 100, "x1": 160, "bottom": 110, "page": 1},
    ]
    fields = [{
        "feld": "bruttoertrag", "value": "116.70",
        "snippet": "116.70",
        "anchor_valid": True, "bbox": [120, 100, 160, 110], "page": 1,
    }]
    psf.harden_dividende_bruttoertrag(fields, words=words)
    assert fields[0]["value"] == "116.70"
