"""Tests der Prämien-Label-Pflicht (260904-rmx).

Beobachtet an einem echten Auszug: das Sprachmodell gab die Postleitzahl der
Absenderadresse als KVG-Prämie aus. Verankert und grün — dieselbe Fehlerklasse
wie die AHV-Nummer als AHV-Abzug.
"""
from __future__ import annotations

from scripts.process_samples_full import harden_kk_praemien


def wort(text, x0, top):
    return {"text": text, "x0": x0, "top": top, "x1": x0 + 30,
            "bottom": top + 9, "page": 1}


def feld(value, bbox=(500.0, 100.0, 530.0, 109.0), herkunft="modell",
         name="praemie_kvg_total"):
    return {"feld": name, "value": value, "bbox": list(bbox), "page": 1,
            "anchor_valid": True, "herkunft": herkunft}


def test_praemie_ohne_label_in_der_zeile_wird_demotiert():
    """Die Postleitzahl steht in der Adresszeile, nicht bei einer Prämie."""
    words = [wort("8000", 500.0, 100.0), wort("Musterhausen", 535.0, 100.0)]
    f = [feld("8000.00")]
    harden_kk_praemien(f, words)
    assert f[0]["value"].startswith("manual_review:praemie_label_fehlt")
    assert f[0]["anchor_valid"] is False


def test_praemie_mit_label_in_der_zeile_bleibt():
    words = [wort("Grundversicherung", 300.0, 100.0), wort("3120.40", 500.0, 100.0)]
    f = [feld("3'120.40")]
    harden_kk_praemien(f, words)
    assert f[0]["value"] == "3'120.40"


def test_kurzes_label_genuegt():
    """Manche Kassen schreiben schlicht 'Praemien'."""
    words = [wort("Prämien", 300.0, 100.0), wort("3081.98", 500.0, 100.0)]
    f = [feld("3'081.98")]
    harden_kk_praemien(f, words)
    assert f[0]["value"] == "3'081.98"


def test_label_im_fliesstext_genuegt_nicht():
    """Die Rechtsbelehrung am Seitenende enthaelt 'Grundversicherung'."""
    words = [
        wort("8000", 500.0, 100.0), wort("Musterhausen", 535.0, 100.0),
        wort("Police(Grundversicherung", 100.0, 700.0),   # andere Zeile
    ]
    f = [feld("8000.00")]
    harden_kk_praemien(f, words)
    assert f[0]["value"].startswith("manual_review:")


def test_regelwert_wird_nicht_angetastet():
    """Kam der Wert von einer beschrifteten Regel, ist das Label Teil des Musters."""
    words = [wort("8000", 500.0, 100.0)]
    f = [feld("8000.00", herkunft="regel")]
    harden_kk_praemien(f, words)
    assert f[0]["value"] == "8000.00"


def test_marker_bleibt_marker():
    words = [wort("x", 500.0, 100.0)]
    f = [feld("manual_review:nicht_parsebar")]
    harden_kk_praemien(f, words)
    assert f[0]["value"] == "manual_review:nicht_parsebar"


def test_andere_felder_unberuehrt():
    words = [wort("8000", 500.0, 100.0)]
    f = [feld("8000.00", name="selbstgetragene_kosten")]
    harden_kk_praemien(f, words)
    assert f[0]["value"] == "8000.00"


def test_vvg_wird_ebenfalls_geprueft():
    words = [wort("8000", 500.0, 100.0), wort("Musterhausen", 535.0, 100.0)]
    f = [feld("8000.00", name="praemie_vvg_total")]
    harden_kk_praemien(f, words)
    assert f[0]["value"].startswith("manual_review:")
