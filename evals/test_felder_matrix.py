"""Die feine Auflösung muss stimmen (260904-rmx).

Zwei Fallen, in die die erste Fassung beide getappt ist:

**Verbinden über die Beschriftung.** Feldvertrag und Übertragungstabelle
benannten dieselben Felder verschieden — die Matrix meldete „12 erwartet, 0
mit Wert", obwohl alle zwölf da waren. Verbunden wird über den Feldnamen.

**0.00 als Wert zählen.** Ein Nullbetrag ist etwas anderes als ein fehlender:
die Zeile existiert, wird aber vom Tabellenbau verworfen. Wer beides gleich
zählt, produziert Widersprüche zwischen den Berichten.
"""
from __future__ import annotations

import json

from scripts.felder_matrix import _ist_null, sammle


def f(feld, wert):
    return {"feld": feld, "value": wert, "bbox": [1, 2, 3, 4], "page": 1,
            "anchor_valid": True}


def baue(tmp_path, belegtyp, felder):
    quelle, ziel = tmp_path / "quelle", tmp_path / "json"
    quelle.mkdir()
    ziel.mkdir()
    (quelle / "a.pdf").write_bytes(b"%PDF-1.4\n")
    (ziel / "a.json").write_text(json.dumps(
        {"pdf_name": "a.pdf", "belegtyp": belegtyp, "pages": []}))
    (ziel / "_results_full.json").write_text(json.dumps(
        [{"pdf_name": "a.pdf", "status": "ok", "belegtyp": belegtyp,
          "fields": felder}]))
    return quelle, ziel


def feld(alle, bezeichnung):
    return next(f for f in alle[0]["felder"] if f["zielwert"] == bezeichnung)


# --- Verbinden über den Feldnamen -------------------------------------------

def test_wert_wird_gefunden_auch_wenn_die_beschriftung_abweicht(tmp_path):
    quelle, ziel = baue(tmp_path, "bank_zinsausweis",
                        [f("vermoegensstand_3112", "12500.55")])
    assert feld(sammle(quelle, ziel), "Saldo 31.12.")["wert"] is True


def test_sollfeld_ohne_wert_wird_gemeldet(tmp_path):
    quelle, ziel = baue(tmp_path, "kk_praemienbescheinigung",
                        [f("praemie_kvg_total", "3120.40")])
    alle = sammle(quelle, ziel)
    assert feld(alle, "Prämie Grundversicherung KVG")["wert"] is True
    # Die anderen Sollfelder des Vertrags erscheinen als fehlend.
    assert feld(alle, "Prämie Zusatzversicherung VVG")["wert"] is False


def test_jedes_sollfeld_des_vertrags_taucht_auf(tmp_path):
    from extractors.zielwerte import ZIELWERTE
    quelle, ziel = baue(tmp_path, "kk_praemienbescheinigung", [])
    bezeichnungen = {f["zielwert"] for f in sammle(quelle, ziel)[0]["felder"]}
    for z in ZIELWERTE["kk_praemienbescheinigung"]:
        assert z.bezeichnung in bezeichnungen, z.bezeichnung


# --- Nullbeträge ------------------------------------------------------------

def test_nullbetrag_ist_kein_wert(tmp_path):
    quelle, ziel = baue(tmp_path, "saeule_3a",
                        [f("einzahlung_betrag", "0.00")])
    e = feld(sammle(quelle, ziel), "Einzahlung Säule 3a")
    assert e["wert"] is False
    assert e["null"] is True


def test_nullbetrag_ist_auch_kein_loch(tmp_path):
    """Er wird eigens ausgewiesen — 'es gab nichts' und 'nicht gefunden'
    sind verschiedene Aussagen."""
    quelle, ziel = baue(tmp_path, "saeule_3a", [])
    e = feld(sammle(quelle, ziel), "Einzahlung Säule 3a")
    assert e["wert"] is False
    assert e["null"] is False


def test_nullerkennung_versteht_schweizer_schreibweisen():
    for t in ("0", "0.00", "0,00", "0.-", "0 . 0 0".replace(" ", "")):
        assert _ist_null(t), t
    for t in ("3120.40", "0.01", "-5.00", "keine Angabe"):
        assert not _ist_null(t), t
