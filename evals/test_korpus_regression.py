"""Leser und Klassifikator gegen den anonymisierten Korpus (261025-kme, Nachbesserung).

Anlass: Die Zeilenbildung der Rohtabelle und ein Klassifikatormuster hatten auf
echten Dokumenten Werte um eine Spalte verschoben, einen ALV-Scan unlesbar
gemacht und ein Säule-3a-Dokument zum Lohnausweis. Die Tests mit erfundenen
Zeilen sahen das nicht, weil ihre Zeilen sauber gesetzt waren. Diese Tests
prüfen Eigenschaften, die für jedes echte Dokument gelten müssen — keine
einzelnen Werte, damit keine echten Zahlen im Code stehen.

Ohne Korpus (gitignoriert) werden sie übersprungen.
"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from extractors import alv, classifier, depot, rohtabelle
from extractors.belegordner import typ_aus_ordner

KORPUS = Path(__file__).resolve().parent / "samples_real_2025"

pytestmark = pytest.mark.skipif(not (KORPUS / "_results_full.json").exists(),
                                reason="anonymisierter Korpus nicht vorhanden")


def _dokumente():
    for p in sorted(KORPUS.glob("*.json")):
        if p.name.startswith("_") or p.name.endswith(".truth.json"):
            continue
        d = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(d, dict) and "pages" in d:
            yield d


def _woerter(d):
    return [dict(w, page=pg.get("page_num", 1)) for pg in d["pages"]
            for w in pg.get("words", [])]


def test_klassifikation_stimmt_mit_den_festgehaltenen_typen_ueberein():
    soll = {e["pdf_name"]: e.get("belegtyp")
            for e in json.loads((KORPUS / "_results_full.json").read_text(encoding="utf-8"))}
    abweichend = []
    for d in _dokumente():
        erkannt = typ_aus_ordner(str(d.get("ordner") or ""),
                                 classifier.classify(_woerter(d))[0])
        if erkannt != soll.get(d.get("pdf_name")):
            abweichend.append((soll.get(d.get("pdf_name")), erkannt))
    assert not abweichend, f"{len(abweichend)} Dokument(e) anders klassifiziert: {abweichend}"


def test_alv_bescheinigung_liefert_alle_spalten_und_geht_auf():
    gefunden = 0
    for d in _dokumente():
        tab = rohtabelle.baue(d)
        if not alv.ALV_RE.search(" ".join(z.text for z in tab.zeilen[:80])):
            continue
        b = alv.lese(tab)
        if b is None:
            continue
        gefunden += 1
        assert {"taggelder", "ahv", "nbuv", "bvg", "steuerbar"} <= set(b.werte), sorted(b.werte)
        assert b.befunde == [], b.befunde
        brutto = b.werte["taggelder"] + b.werte.get("familienzulagen", Decimal(0))
        abzug = sum(b.werte[n] for n in ("ahv", "nbuv", "bvg"))
        assert abs(brutto - abzug - b.werte["steuerbar"]) <= 2
    assert gefunden >= 1


def test_depot_steuerwert_ist_nie_kleiner_als_der_ertrag():
    """Eine um eine Spalte verschobene Zeile machte den Ertrag zum Steuerwert."""
    gelesen = 0
    for d in _dokumente():
        t = depot.lese(rohtabelle.baue(d))
        if t.form not in ("wv", "uebersicht"):
            continue
        gelesen += 1
        assert t.steuerwert is not None
        assert t.ertrag_a is not None and t.ertrag_b is not None
        assert t.steuerwert >= t.ertrag_a, "Steuerwert kleiner als Ertrag A: Spalten verschoben?"
        assert t.steuerwert >= t.ertrag_b
    assert gelesen >= 1


def test_portfolio_steuerwert_ist_gesamt_minus_liquiditaet():
    for d in _dokumente():
        t = depot.lese(rohtabelle.baue(d))
        if t.form != "portfolio":
            continue
        assert t.gesamt is not None and t.steuerwert is not None
        assert t.steuerwert == t.gesamt - (t.liquiditaet or Decimal(0))
