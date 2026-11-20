"""Tests fuer scripts/eval_unsicherheit.py (ohne Ollama, synthetische Werte)."""
from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import eval_unsicherheit as eu

ANTWORT = '{"betrag": {"value": "1\'250.00", "tag_refs": []}, "jahr": {"value": "2024", "tag_refs": []}, "kategorie": null}'


def _tokens(text: str, unsicher: str = "", lp: float = -2.0) -> list[tuple[str, float]]:
    """Ein Token je Zeichen; Zeichen in ``unsicher`` bekommen ``lp``."""
    return [(c, lp if c in unsicher else -0.001) for c in text]


def test_wert_spannen_findet_value_strings():
    sp = eu.wert_spannen(ANTWORT)
    assert ANTWORT[slice(*sp["betrag"])] == "1'250.00"
    assert ANTWORT[slice(*sp["jahr"])] == "2024"
    assert "kategorie" not in sp


def test_wert_spannen_mit_escape_im_wert():
    text = '{"name": {"value": "A \\"B\\" C", "tag_refs": []}}'
    sp = eu.wert_spannen(text)
    assert text[slice(*sp["name"])] == 'A \\"B\\" C'


def test_p_min_nimmt_unsicherstes_token_im_wert():
    lps = _tokens(ANTWORT, unsicher="5")
    p = eu.p_min_je_feld(ANTWORT, lps)
    assert math.isclose(p["betrag"], math.exp(-2.0))
    assert p["jahr"] > 0.99


def test_p_min_none_wenn_tokens_nicht_aufgehen():
    lps = _tokens(ANTWORT)[:-1]
    assert eu.p_min_je_feld(ANTWORT, lps) is None


def test_klasse():
    assert eu.klasse("1'250.00", "1250.00") == "richtig"
    assert eu.klasse("1'250.00", "") == "fehlt"
    assert eu.klasse("437.50", "manual_review:vrs_label_fehlt") == "markiert"
    assert eu.klasse("437.50", "1'250.00") == "falsch"


def test_auroc():
    assert eu.auroc([(0.1, False), (0.99, True)]) == 1.0
    assert eu.auroc([(0.99, False), (0.1, True)]) == 0.0
    assert eu.auroc([(0.5, True)]) is None


def test_zeilen_ohne_werte_und_namensfelder_ausgenommen():
    res = {"belegtyp": "spenden", "fields": [
        {"feld": "betrag", "value": "500.00", "herkunft": "modell", "anchor_valid": True},
        {"feld": "jahr", "value": "2024", "herkunft": "regel", "anchor_valid": True},
    ]}
    truth = {"betrag": {"value": "500.00"}, "jahr": {"value": "2024"},
             "spender_name": {"value": "Hans Muster"}, "_meta": {}}
    zs = eu.zeilen_fuer_beleg(res, truth, {"betrag": 0.8, "jahr": 0.99})
    assert [z["feld"] for z in zs] == ["betrag", "jahr"]
    assert zs[0]["p_min"] == 0.8
    assert zs[1]["p_min"] is None  # Regelwert: keine Modell-Unsicherheit
    assert all("value" not in z for z in zs)


def test_bericht_enthaelt_keine_werte():
    zs = [{"belegtyp": "spenden", "feld": "betrag", "klasse": "falsch",
           "herkunft": "modell", "anchor_valid": True, "p_min": 0.4}]
    text = eu.bericht({"zeilen": zs, "belege": 1, "ausgerichtet": 1})
    assert "Kalibrierung" in text
    assert "500.00" not in text


def test_klasse_kein_teilstring_mehr():
    """Code-Review 2026-11-19: «50.00» in «150.00» war «richtig»."""
    assert eu.klasse("50.00", "150.00") == "falsch"
    assert eu.klasse("2024", "12024") == "falsch"
    assert eu.klasse("1250", "1'250.00") == "richtig"
    assert eu.klasse("Steuerbefreit", " steuerbefreit ") == "richtig"


def _antwort(betrag: str) -> str:
    return '{"betrag": {"value": "' + betrag + '", "tag_refs": []}}'


def test_p_min_vom_aufruf_dessen_wert_im_ergebnis_steht():
    """Zwei Extraktionsaufrufe: zaehlt der, dessen Wert im Ergebnis steht, nicht der letzte."""
    a1, a2 = _antwort("500.00"), _antwort("700.00")
    aufrufe = [(a1, _tokens(a1, unsicher="5", lp=-3.0)), (a2, _tokens(a2))]
    fields = [{"feld": "betrag", "value": "500.00"}]
    p, ausgerichtet = eu.p_min_fuer_ergebnis(aufrufe, fields)
    assert ausgerichtet == 2
    assert math.isclose(p["betrag"], math.exp(-3.0))


def test_p_min_nachbearbeiteter_wert_nimmt_letzten_aufruf():
    a1, a2 = _antwort("500.00"), _antwort("700.00")
    aufrufe = [(a1, _tokens(a1, unsicher="5", lp=-3.0)), (a2, _tokens(a2))]
    fields = [{"feld": "betrag", "value": "manual_review:vrs_label_fehlt"}]
    p, _ = eu.p_min_fuer_ergebnis(aufrufe, fields)
    assert p["betrag"] > 0.99


def test_p_min_ohne_ausrichtung_leer():
    a1 = _antwort("500.00")
    p, ausgerichtet = eu.p_min_fuer_ergebnis([(a1, _tokens(a1)[:-1])], [])
    assert p == {} and ausgerichtet == 0
