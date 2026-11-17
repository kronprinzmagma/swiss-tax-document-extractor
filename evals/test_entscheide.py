"""Entscheide: Grabstein, defekte Datei laut, Position im Schlüssel,
Entscheidungen ohne Wert sichtbar, Server prüft den Schlüssel (261025-jm6, A2).

* A2a — Eine Rücknahme löschte den Eintrag (``pop``). Abwesenheit überlebt
  keine Zusammenführung; jetzt ist sie ein Grabstein.
* A2b — Eine defekte ``entscheide.json`` galt als leer; das nächste
  Speichern überschrieb sie, alle Entscheidungen waren still weg.
* A2c — Zwei gleiche Beträge im selben Dokument hatten denselben Schlüssel;
  „weglassen" auf einen nahm beide weg.
* A2d — Eine Entscheidung, deren Wert es nicht mehr gibt, war unsichtbar.
* A2e — Der Server nahm jeden Schlüssel an.

Alle Werte erfunden.
"""
from __future__ import annotations

import json
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from extractors import entscheide

HYPO = "Musterbank - Hypothek - Muster 2025.pdf"
KK = "KK-A - Praemien - Muster 2025.pdf"


def _wert(bez="Schuldzinsen", betrag="1'500.00", quelle=HYPO, pos=None, **mehr):
    w = {"bezeichnung": bez, "betrag": betrag, "quelle": quelle, **mehr}
    if pos is not None:
        w["pos"] = pos
    return w


def _leiste_werte(*werte) -> list[dict]:
    return [{"formular": "Schuldenverzeichnis", "bloecke": [
        {"titel": "Musterbank", "person": "familie", "werte": list(werte)}]}]


def _schuldbloecke() -> list[dict]:
    def block():
        return {"titel": "Musterbank · Hypothek", "maske": "Schuld", "person": "familie",
                "dokumente": [HYPO], "werte": [],
                "felder": [{"titel": "Schuld am 31.12.", "wert": "250'000.00"},
                           {"titel": "Schuldzinsen", "wert": "1'500.00"}]}
    return [{"formular": "Schuldenverzeichnis", "bloecke": [block(), block()]}]


def _anwenden(samples, formulare, laufdoks=frozenset({HYPO})):
    from scripts.export_extension import _entscheide_anwenden
    return _entscheide_anwenden(samples, formulare, set(laufdoks))


# --- A2a: Grabstein ----------------------------------------------------------

def test_ruecknahme_ist_ein_grabstein(tmp_path):
    k = entscheide.schluessel_wert(KK, "Prämie Grundversicherung KVG", "1'000.00")
    entscheide.speichere(tmp_path, k, "weglassen")
    entscheide.speichere(tmp_path, k, None)

    assert entscheide.lade(tmp_path) == {}
    grab = entscheide.lade_roh(tmp_path)[k]
    assert grab["aktion"] is None and grab["zurueckgenommen"] is True
    assert grab["vorher"] == "weglassen" and grab["am"]

    f = [{"formular": "Versicherungsprämien", "bloecke": [{"titel": "KK-A", "werte": [
        _wert("Prämie Grundversicherung KVG", "1'000.00", KK)]}]}]
    _anwenden(tmp_path, f, {KK})
    assert len(f[0]["bloecke"][0]["werte"]) == 1

    # Die jüngste Verlaufskopie kennt die Entscheidung noch.
    juengste = sorted((tmp_path / "entscheide-verlauf").glob("*.json"))[-1]
    assert json.loads(juengste.read_text(encoding="utf-8"))[k]["aktion"] == "weglassen"


def test_ruecknahme_ohne_eintrag_ist_ein_fehler(tmp_path):
    k = entscheide.schluessel_wert(KK, "Prämie", "1.00")
    andere = entscheide.schluessel_wert(KK, "Prämie", "2.00")
    entscheide.speichere(tmp_path, andere, "behalten")
    vorher = entscheide.pfad(tmp_path).read_bytes()
    with pytest.raises(ValueError, match="nichts zurückzunehmen"):
        entscheide.speichere(tmp_path, k, None)
    assert entscheide.pfad(tmp_path).read_bytes() == vorher


# --- A2b: defekte Datei ist laut ---------------------------------------------

@pytest.mark.parametrize("inhalt", ["{kaputt", "[]", json.dumps({"k": "weglassen"})])
def test_defekte_datei_ist_laut_und_bleibt(tmp_path, inhalt):
    ziel = entscheide.pfad(tmp_path)
    ziel.write_text(inhalt, encoding="utf-8")
    roh = ziel.read_bytes()
    assert not issubclass(entscheide.EntscheideDefekt, ValueError)
    with pytest.raises(entscheide.EntscheideDefekt) as e:
        entscheide.lade(tmp_path)
    assert str(tmp_path) not in str(e.value)
    with pytest.raises(entscheide.EntscheideDefekt):
        entscheide.lade_roh(tmp_path)
    with pytest.raises(entscheide.EntscheideDefekt):
        entscheide.speichere(tmp_path, "x|y|1.00", "behalten")
    with pytest.raises(entscheide.EntscheideDefekt):
        entscheide.lade(tmp_path)
    assert ziel.read_bytes() == roh
    kopien = list(tmp_path.glob("entscheide.defekt-*.json"))
    assert len(kopien) == 1 and kopien[0].read_bytes() == roh


def test_speichern_schreibt_atomar(tmp_path):
    entscheide.speichere(tmp_path, "a|b|1.00", "behalten")
    entscheide.speichere(tmp_path, "a|b|2.00", "weglassen")
    assert set(entscheide.lade(tmp_path)) == {"a|b|1.00", "a|b|2.00"}
    # Keine liegengebliebene Zwischendatei.
    assert sorted(p.name for p in tmp_path.iterdir()) == ["entscheide-verlauf",
                                                          "entscheide.json"]


# --- A2c: Position im Schlüssel ----------------------------------------------

def test_gleiche_betraege_haben_eigene_schluessel(tmp_path):
    f = _leiste_werte(_wert(pos=1), _wert(pos=2))
    _anwenden(tmp_path, f)
    k1, k2 = (w["schluessel"] for w in f[0]["bloecke"][0]["werte"])
    assert k1 != k2 and k2.endswith("#2") and not k1.endswith("#2")

    entscheide.speichere(tmp_path, k2, "weglassen")
    f = _leiste_werte(_wert(pos=1, info="erste"), _wert(pos=2, info="zweite"))
    _anwenden(tmp_path, f)
    assert [w["info"] for w in f[0]["bloecke"][0]["werte"]] == ["erste"]


def test_gleiche_schuldbloecke_haben_eigene_schluessel(tmp_path):
    f = _schuldbloecke()
    _anwenden(tmp_path, f)
    k1, k2 = (b["schluessel"] for b in f[0]["bloecke"])
    assert k1 != k2 and k2.endswith("#2")
    entscheide.speichere(tmp_path, k2, "weglassen")
    f = _schuldbloecke()
    _anwenden(tmp_path, f)
    assert [b["schluessel"] for b in f[0]["bloecke"]] == [k1]


def test_alter_schluessel_gilt_fuer_die_erste_position(tmp_path):
    alt = entscheide.schluessel_wert(HYPO, "Schuldzinsen", "1'500.00")
    assert "#" not in alt
    entscheide.speichere(tmp_path, alt, "weglassen")
    f = _leiste_werte(_wert(pos=1, info="erste"), _wert(pos=2, info="zweite"))
    _anwenden(tmp_path, f)
    assert [w["info"] for w in f[0]["bloecke"][0]["werte"]] == ["zweite"]


def test_alter_schluessel_wirkt_bei_einziger_position(tmp_path):
    alt = entscheide.schluessel_wert(HYPO, "Schuldzinsen", "1'500.00")
    entscheide.speichere(tmp_path, alt, "behalten")
    f = _leiste_werte(_wert(pos=3, warnung=True, hinweis="prüfen"))
    ohne = _anwenden(tmp_path, f)
    (w,) = f[0]["bloecke"][0]["werte"]
    assert w["entschieden"] == "behalten" and not w["warnung"]
    assert w["schluessel"] == alt
    assert ohne == []


def test_zerlege_schluessel():
    k = entscheide.schluessel_wert(HYPO, "Schuldzinsen", "1'500.00", pos=2)
    assert k.endswith("#2")
    assert entscheide.zerlege_schluessel(k) == (HYPO, "Schuldzinsen", "1500.00", 2)
    assert entscheide.zerlege_schluessel(
        entscheide.schluessel_wert(HYPO, "Konto #12", "3.00")) == (HYPO, "Konto #12", "3.00", 1)


# --- A2d: Entscheidungen ohne Wert --------------------------------------------

def _daten_mit(samples, *werte) -> dict:
    formulare = _leiste_werte(*werte)
    ohne = _anwenden(samples, formulare)
    return {"formulare": formulare, "entscheide_ohne_wert": ohne}


def test_entscheidung_ohne_wert_ist_sichtbar(tmp_path, monkeypatch):
    from scripts.entscheiden_seite import offene_punkte, seite

    verloren = entscheide.schluessel_wert("Musterbank - Alt - Muster 2024.pdf",
                                          "Schuldzinsen", "999.00")
    weg = entscheide.schluessel_wert(HYPO, "Schuldzinsen", "1'500.00")
    entscheide.speichere(tmp_path, verloren, "behalten")
    entscheide.speichere(tmp_path, weg, "weglassen")
    daten = _daten_mit(tmp_path, _wert(pos=1), _wert("Schuld", "2.00", pos=1))
    assert daten["entscheide_ohne_wert"] == [verloren]

    offen, entschieden, ohne_wert = offene_punkte(tmp_path, daten=daten)
    assert [p["schluessel"] for p in ohne_wert] == [verloren]
    assert ohne_wert[0]["aktion"] == "behalten" and ohne_wert[0]["titel"] == "Schuldzinsen"
    assert verloren not in [p["schluessel"] for p in entschieden]
    assert [p["schluessel"] for p in entschieden] == [weg]

    import scripts.export_extension as ex
    monkeypatch.setattr(ex, "baue", lambda s, **k: daten)
    html = seite(tmp_path, "t")
    teil = html.split("Entscheidungen ohne Wert")[1]
    assert "zurücknehmen" in teil and "999.00" in teil


def test_make_pruefen_nennt_entscheidungen_ohne_wert(tmp_path, monkeypatch, capsys):
    from extractors import eintraege
    from scripts import entscheiden_seite
    from scripts import pflichtfelder_check as pc

    samples = tmp_path / "json"
    samples.mkdir()
    konto = "Musterbank - Kontoauszug - Muster 2025.pdf"
    (samples / "doc0.json").write_text(
        json.dumps({"pdf_name": konto, "ordner": "Konten", "pages": []}), encoding="utf-8")
    (samples / "_results_full.json").write_text(
        json.dumps([{"pdf_name": konto, "belegtyp": "bank_zinsausweis", "fields": []}]),
        encoding="utf-8")
    ordner = tmp_path / "pdfs"
    (ordner / "Konten").mkdir(parents=True)
    (ordner / "Konten" / konto).write_bytes(b"")

    def pruefe(*a, **k):
        return [{"dokument": konto, "typ": "bank_zinsausweis", "fehlt": []}]
    pruefe.hinweise, pruefe.kk = [], {}
    monkeypatch.setattr(pc, "_leiste", lambda *a, **k: {"formulare": []})
    monkeypatch.setattr(pc, "pruefe_leiste", pruefe)
    monkeypatch.setattr(pc, "plausibilitaet", lambda *a, **k: [])
    monkeypatch.setattr(pc, "pruefe_kette", lambda *a, **k: [])
    monkeypatch.setattr(eintraege, "tabellen_fuer", lambda *a, **k: {})
    monkeypatch.setattr(entscheiden_seite, "offene_punkte",
                        lambda *a, **k: ([], [], [{"schluessel": "x|y|1.00"}]))
    monkeypatch.setattr(sys, "argv", ["x", "--samples", str(samples), "--pdf-dir", str(ordner)])
    assert pc.main() == 0
    aus = capsys.readouterr().out
    assert "Entscheidungen ohne passenden Wert: 1" in aus
    assert "x|y" not in aus


def test_make_pruefen_meldet_defekte_entscheide(tmp_path, monkeypatch, capsys):
    from scripts import pflichtfelder_check as pc

    samples = tmp_path / "json"
    samples.mkdir()
    (samples / "_results_full.json").write_text("[]", encoding="utf-8")
    entscheide.pfad(samples).write_text("{kaputt", encoding="utf-8")

    def kaputt(*a, **k):
        entscheide.lade(samples)
    monkeypatch.setattr(pc, "_leiste", kaputt)
    monkeypatch.setattr(sys, "argv", ["x", "--samples", str(samples), "--ohne-ordner"])
    assert pc.main() == 1
    assert "entscheide.json" in capsys.readouterr().out


# --- A2e: Server prüft den Schlüssel -----------------------------------------

@pytest.fixture()
def server(tmp_path, monkeypatch):
    from scripts import export_extension
    from scripts.review_server import HOST, Handler, Zustand

    samples = tmp_path / "json"
    samples.mkdir()
    (samples / "_results_full.json").write_text("[]")
    gueltig = entscheide.schluessel_wert(KK, "Prämie Grundversicherung KVG", "1'000.00")
    aufrufe = []

    def baue(s, **k):
        aufrufe.append(k)
        return {"formulare": [{"formular": "Versicherungsprämien", "bloecke": [
            {"titel": "KK-A", "werte": [dict(_wert("Prämie Grundversicherung KVG",
                                                   "1'000.00", KK), schluessel=gueltig)]}]}],
                "entscheide_ohne_wert": []}
    monkeypatch.setattr(export_extension, "baue", baue)

    zustand = Zustand(samples, None, "geheim-123")
    Handler.zustand = zustand
    with __import__("socket").socket() as so:
        so.bind((HOST, 0))
        port = so.getsockname()[1]
    srv = ThreadingHTTPServer((HOST, port), Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield samples, f"http://{HOST}:{port}", gueltig, aufrufe
    srv.shutdown()
    srv.server_close()


def _post(url, daten, token="geheim-123"):
    kopf = {"Content-Type": "application/json"}
    if token:
        kopf["X-Steuer-Token"] = token
    anfrage = urllib.request.Request(url, data=json.dumps(daten).encode(),
                                     headers=kopf, method="POST")
    try:
        with urllib.request.urlopen(anfrage, timeout=10) as r:
            return r.status, r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8")


def test_server_nimmt_nur_passende_schluessel(server):
    samples, basis, gueltig, aufrufe = server
    url = basis + "/api/entscheiden"

    # Token zuerst: ohne Token wird nicht einmal die Leiste gerechnet.
    code, _ = _post(url, {"schluessel": gueltig, "aktion": "behalten"}, token=None)
    assert code == 403 and aufrufe == []

    code, _ = _post(url, {"schluessel": "erfunden|x|1.00", "aktion": "behalten"})
    assert code == 400 and not entscheide.pfad(samples).exists()

    code, _ = _post(url, {"schluessel": gueltig, "aktion": "behalten"})
    assert code == 200 and entscheide.lade(samples)[gueltig]["aktion"] == "behalten"
    assert aufrufe and "pdf_dir" in aufrufe[0]

    code, _ = _post(url, {"schluessel": gueltig, "aktion": None})
    assert code == 200 and entscheide.lade_roh(samples)[gueltig]["zurueckgenommen"] is True

    code, _ = _post(url, {"schluessel": "erfunden|x|1.00", "aktion": None})
    assert code == 400


def test_server_defekte_datei(server):
    samples, basis, gueltig, _ = server
    ziel = entscheide.pfad(samples)
    ziel.write_text("{kaputt", encoding="utf-8")
    code, text = _post(basis + "/api/entscheiden", {"schluessel": gueltig, "aktion": "behalten"})
    assert code == 409 and "defekt" in text
    assert ziel.read_text(encoding="utf-8") == "{kaputt"

    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(basis + "/entscheiden", timeout=10)
    assert e.value.code == 500
    assert "defekt" in e.value.read().decode("utf-8")
