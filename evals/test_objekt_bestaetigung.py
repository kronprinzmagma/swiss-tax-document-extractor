"""Bestätigen je Hypothek/Konto statt loser Beträge je Dokument (261025).

der Anwender: „ich kann ja nur pro Dokument Felder ergänzen und eintragen, nicht
referenzieren". Drei Hypotheken auf einem Beleg, drei lose Zinsen — welcher
zu welcher gehört, stand nirgends. Ein Zins passte zu keiner, ein anderer
war der von Seite 3 statt Seite 2.

Jetzt trägt jede bestätigte Position die Marke ihres Objekts
(„Hypothek Nr. …") in ``unterscheidung``. Sind alle Objekte eines Dokuments
bestätigt, treten die losen Einträge zurück — sie bleiben in der Ablage.
Werte erfunden.
"""
from __future__ import annotations

import json
import sqlite3
import urllib.error
from decimal import Decimal
from pathlib import Path

import pytest

from extractors import ablage
from extractors.eintraege import SCHULD, SCHULDZINS

M1, M2, M3 = "Hypothek Nr. 11 1.111.111.11", "Hypothek Nr. 22 2.222.222.22", \
    "Hypothek Nr. 33 3.333.333.33"
MARKEN = {SCHULD: [M1, M2, M3], SCHULDZINS: [M1, M2, M3]}


@pytest.fixture()
def samples(tmp_path):
    ordner = tmp_path / "json"
    ordner.mkdir()
    db = ablage.oeffne(ablage.pfad_fuer(ordner))
    # So hat der Anwender erfasst: zwei Schulden, drei Zinsen, ohne Bezug.
    for i, (ziel, betrag) in enumerate([(SCHULD, "200000.00"), (SCHULD, "200000.00"),
                                        (SCHULDZINS, "1000.00"), (SCHULDZINS, "3000.00"),
                                        (SCHULDZINS, "1250.00")], start=1):
        pos = i if ziel == SCHULD else i - 2
        db.execute("INSERT INTO position (id, dokument, zielwert, pos, betrag, person, "
                   "aussteller, jahr, herkunft, status) "
                   "VALUES (?,?,?,?,?,?,?,?, 'mensch', 'geprueft')",
                   (f"p{i:04d}", "h.pdf", ziel, pos, betrag, "familie", "Musterbank", "2025"))
    db.commit()
    db.close()
    return ordner


def _zinsen(samples) -> list[tuple[str, str]]:
    return sorted((z["unterscheidung"], z["betrag"]) for z in ablage.lade_geprueft(samples)
                  if z["zielwert"] == SCHULDZINS)


def test_erste_bestaetigung_legt_neue_position_mit_marke_an(samples):
    n = ablage.bestaetige_objekt(samples, "h.pdf", M1,
                                 {SCHULD: "200000.00", SCHULDZINS: "1000.00"}, MARKEN)
    assert n == {"neu": 2, "geaendert": 0, "gleich": 0, "ersetzt": 0}
    zeilen = [z for z in ablage.lade_geprueft(samples) if z["unterscheidung"] == M1]
    assert {z["zielwert"] for z in zeilen} == {SCHULD, SCHULDZINS}
    # Nächste freie Nummer, nichts rückt nach; Person/Aussteller vom Dokument.
    zins = next(z for z in zeilen if z["zielwert"] == SCHULDZINS)
    assert (zins["pos"], zins["person"], zins["aussteller"]) == (4, "familie", "Musterbank")


def test_lose_eintraege_bleiben_bis_alle_objekte_bestaetigt_sind(samples):
    for m, zins in ((M1, "1000.00"), (M2, "1200.00")):
        ablage.bestaetige_objekt(samples, "h.pdf", m, {SCHULDZINS: zins}, MARKEN)
    assert len(_zinsen(samples)) == 5          # 3 lose + 2 je Objekt


def test_alle_bestaetigt_lose_treten_zurueck_ohne_zu_verschwinden(samples):
    for m, zins in ((M1, "1000.00"), (M2, "1200.00"), (M3, "3000.00")):
        n = ablage.bestaetige_objekt(samples, "h.pdf", m, {SCHULDZINS: zins}, MARKEN)
    assert n["ersetzt"] == 3
    assert _zinsen(samples) == [(M1, "1000.00"), (M2, "1200.00"), (M3, "3000.00")]
    # Die Schulden-Gruppe ist noch nicht je Objekt bestätigt — sie bleibt.
    assert sum(z["zielwert"] == SCHULD for z in ablage.lade_geprueft(samples)) == 2
    # Nichts gelöscht: die alten Zeilen stehen unverändert in der Tabelle.
    db = sqlite3.connect(ablage.pfad_fuer(samples))
    alt = db.execute("SELECT betrag FROM position WHERE id IN ('p0003','p0004','p0005') "
                     "ORDER BY id").fetchall()
    assert [a[0] for a in alt] == ["1000.00", "3000.00", "1250.00"]
    assert {e["betrag"] for e in ablage.lade_ersetzt(samples)} == {"1000.00", "3000.00",
                                                                   "1250.00"}
    with pytest.raises(sqlite3.DatabaseError):
        db.execute("DELETE FROM position_ersetzt")


def test_gleicher_betrag_aendert_nichts_anderer_wird_protokolliert(samples):
    ablage.bestaetige_objekt(samples, "h.pdf", M2, {SCHULDZINS: "1200.00"}, MARKEN)
    assert ablage.bestaetige_objekt(samples, "h.pdf", M2, {SCHULDZINS: "1200.00"},
                                    MARKEN)["gleich"] == 1
    assert ablage.bestaetige_objekt(samples, "h.pdf", M2, {SCHULDZINS: "1210.00"},
                                    MARKEN)["geaendert"] == 1
    assert (M2, "1210.00") in _zinsen(samples)
    db = sqlite3.connect(ablage.pfad_fuer(samples))
    was = [r[0] for r in db.execute("SELECT was FROM protokoll ORDER BY lfd")]
    assert was == ["je Objekt bestätigt", "je Objekt bestätigt"]


def test_geschlossene_gruppe_wird_laut_abgewiesen(samples):
    db = sqlite3.connect(ablage.pfad_fuer(samples))
    db.execute("INSERT INTO gruppe_geschlossen (dokument, zielwert) VALUES ('h.pdf', ?)",
               (SCHULDZINS,))
    db.commit()
    with pytest.raises(ablage.Gesperrt):
        ablage.bestaetige_objekt(samples, "h.pdf", M1, {SCHULDZINS: "1000.00"}, MARKEN)


# --- Leiste: die Marke entscheidet ------------------------------------------

def _hyp(schuld, zins, nr):
    from extractors.bankobjekte import Seitenobjekt, Wert
    anker = {"seite": 1, "bbox": [1, 2, 3, 4], "snippet": "x"}
    return Seitenobjekt("Hypothek", ("h", nr), None, None, Wert("CHF", None, "dokument"),
                        {"schuld": [Wert(Decimal(schuld), anker, "dokument")],
                         "zins": [Wert(Decimal(zins), anker, "dokument")],
                         "nummer": [Wert(nr, None, "dokument")]},
                        [("h.pdf", int(nr[0]))])


def _zeile(ziel, betrag, u=""):
    return {"pdf_name": "h.pdf", "zielwert": ziel, "betrag": betrag, "herkunft": "mensch",
            "unterscheidung": u, "aussteller": "Musterbank", "person": "familie"}


def test_marke_geht_vor_gleichem_betrag():
    """Zwei gleich grosse Hypotheken: die für H2 bestätigte Schuld gehört
    nicht zu H1, nur weil der Betrag passt."""
    from extractors.eintraege import schulden_aus_kette

    objekte = [_hyp("200000.00", "1000.00", "11 1.111.111.11"),
               _hyp("200000.00", "1200.00", "22 2.222.222.22"),
               _hyp("500000.00", "3000.00", "33 3.333.333.33")]
    zeilen = [_zeile(SCHULD, "199000.00", M2), _zeile(SCHULDZINS, "1190.00", M2)]
    aus, _ = schulden_aus_kette(objekte, zeilen)
    h1, h2, h3 = ([f for f in e.felder if f.titel.startswith(t)][0].wert
                  for e in aus for t in ("Schuldzinsen",))
    assert (h1, h2, h3) == ("1'000.00", "1'190.00", "3'000.00")
    s = [[f for f in e.felder if f.titel.startswith("Schuld am")][0] for e in aus]
    assert [x.wert for x in s] == ["200'000.00", "199'000.00", "500'000.00"]
    assert not s[0].warnung and s[1].warnung     # H2 weicht vom Beleg ab: gesagt


def test_objekt_marke():
    from extractors.eintraege import objekt_marke
    assert objekt_marke(_hyp("1.00", "1.00", "22 2.222.222.22")) == M2


# --- Eingaben werden gegen die Kette geprüft ---------------------------------

@pytest.fixture()
def kette(monkeypatch, samples):
    from extractors import eintraege
    objekte = [_hyp("200000.00", "1000.00", "11 1.111.111.11"),
               _hyp("200000.00", "1200.00", "22 2.222.222.22"),
               _hyp("500000.00", "3000.00", "33 3.333.333.33")]
    monkeypatch.setattr(eintraege, "kette_laden", lambda s: (objekte, {}))
    return samples


def test_fremde_marke_wird_abgewiesen(kette):
    from extractors.eintraege import bestaetige_objekt
    with pytest.raises(ValueError, match="gehört nicht zu diesem Dokument"):
        bestaetige_objekt(kette, "h.pdf", "Hypothek Nr. 99", {SCHULDZINS: "1.00"})


def test_fremdes_feld_und_kein_betrag_werden_abgewiesen(kette):
    from extractors.eintraege import bestaetige_objekt
    with pytest.raises(ValueError, match="gehört nicht zu"):
        bestaetige_objekt(kette, "h.pdf", M1, {"Saldo 31.12.": "1.00"})
    with pytest.raises(ValueError, match="kein Betrag"):
        bestaetige_objekt(kette, "h.pdf", M1, {SCHULDZINS: "abc"})


def test_betrag_in_schweizer_schreibweise_wird_gelesen(kette):
    from extractors.eintraege import bestaetige_objekt, objekte_des_dokuments
    bestaetige_objekt(kette, "h.pdf", M2, {SCHULDZINS: "1'200.00"})
    d = objekte_des_dokuments(kette, "h.pdf")
    h2 = next(o for o in d["objekte"] if o["marke"] == M2)
    zins = next(f for f in h2["felder"] if f["zielwert"] == SCHULDZINS)
    assert (zins["beleg"], zins["bestaetigt"]) == ("1'200.00", "1'200.00")
    assert [o["marke"] for o in d["objekte"]] == [M1, M2, M3]


def test_alle_drei_bestaetigt_ersetzt_steht_in_der_antwort(kette):
    from extractors.eintraege import bestaetige_objekt, objekte_des_dokuments
    for m, s, z in ((M1, "200000.00", "1000.00"), (M2, "200000.00", "1200.00"),
                    (M3, "500000.00", "3000.00")):
        bestaetige_objekt(kette, "h.pdf", m, {SCHULD: s, SCHULDZINS: z})
    d = objekte_des_dokuments(kette, "h.pdf")
    assert len(d["ersetzt"]) == 5
    assert all(f["bestaetigt"] for o in d["objekte"] for f in o["felder"])


# --- Server -----------------------------------------------------------------

def test_server_routen(kette):
    import threading
    from http.server import ThreadingHTTPServer

    from evals.test_review_server import freier_port, post
    from scripts.review_server import HOST, Handler, Zustand

    (kette / "_results_full.json").write_text("[]")
    zustand = Zustand(kette, None, "geheim-123")
    zustand.seite = "<main></main>"
    zustand.baue_seite = lambda: zustand.seite
    Handler.zustand = zustand
    srv = ThreadingHTTPServer((HOST, freier_port()), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://{HOST}:{srv.server_address[1]}"
    try:
        with pytest.raises(urllib.error.HTTPError) as e:
            post(url + "/api/objekt-bestaetigen",
                 {"beleg": "h.pdf", "marke": M1, "werte": {SCHULDZINS: "1.00"}})
        assert e.value.code == 403
        with pytest.raises(urllib.error.HTTPError) as e:
            post(url + "/api/objekt-bestaetigen",
                 {"beleg": "h.pdf", "marke": "x", "werte": {SCHULDZINS: "1.00"}}, "geheim-123")
        assert e.value.code == 400
        r = post(url + "/api/objekt-bestaetigen",
                 {"beleg": "h.pdf", "marke": M1, "werte": {SCHULDZINS: "1000.00"}},
                 "geheim-123")
        assert json.loads(r.read())["neu"] == 1
        assert zustand.stand == 1
        d = json.loads(post(url + "/api/objekte", {"beleg": "h.pdf"}, "geheim-123").read())
        assert len(d["objekte"]) == 3
    finally:
        srv.shutdown()
        srv.server_close()


def test_seite_hat_den_abschnitt_je_objekt():
    quelle = (Path(__file__).parent.parent / "scripts" / "build_review_html.py").read_text()
    assert "objekteHtml(gewaehlt)" in quelle and "/api/objekt-bestaetigen" in quelle


def test_durchsicht_erreicht_ersetzte_zeilen_nicht(samples):
    """Ein Speichern aus der Oberfläche unter #1 darf die ersetzte alte
    Position #1 nicht ändern — sie bleibt, wie der Anwender sie erfasst hat."""
    for m, zins in ((M1, "1000.00"), (M2, "1200.00"), (M3, "3000.00")):
        ablage.bestaetige_objekt(samples, "h.pdf", m, {SCHULDZINS: zins}, MARKEN)
    (samples / "korrekturen.json").write_text(json.dumps({
        f"h.pdf|{SCHULDZINS}": {"soll": "9999.00", "zielwert": SCHULDZINS,
                                "bestaetigt": "Betrag"}}))
    ablage.uebernimm_durchsicht(samples, nur={("h.pdf", SCHULDZINS, 1)})
    db = sqlite3.connect(ablage.pfad_fuer(samples))
    assert db.execute("SELECT betrag FROM position WHERE id='p0003'").fetchone()[0] == "1000.00"


def test_oberflaeche_nimmt_die_gespeicherte_nummer():
    quelle = (Path(__file__).parent.parent / "scripts" / "build_review_html.py").read_text()
    kopf = quelle[quelle.index("function positionVon(r){"):][:600]
    assert kopf.index("if (r.pos) return r.pos;") < kopf.index("if (s.posNr) return s.posNr;")


def test_eingefrorener_stand_fuehrt_ersetzte_nicht(samples, monkeypatch):
    """`make einfrieren` nahm die Zeilen aus der Durchsicht-Sicht und führte
    die losen, längst ersetzten Hypotheken-Einträge zusätzlich (261028)."""
    import scripts.build_review_html as review
    from scripts import einfrieren

    for marke, schuld, zins in [(M1, "200000.00", "1000.00"), (M2, "200000.00", "1250.00"),
                                (M3, "150000.00", "3000.00")]:
        ablage.bestaetige_objekt(samples, "h.pdf", marke,
                                 {SCHULD: schuld, SCHULDZINS: zins}, MARKEN)
    # Die Durchsicht-Sicht kennt noch die losen Einträge — und ein zweites
    # Dokument, das die Ablage nicht führt.
    lose = [{"beleg": "h.pdf", "zielwert": SCHULDZINS, "betrag": b}
            for b in ("1000.00", "3000.00", "1250.00")]
    fremd = [{"beleg": "k.pdf", "zielwert": "Saldo 31.12.", "betrag": "10.00"}]
    monkeypatch.setattr(review, "sammle_zeilen", lambda *a, **k: lose + fremd)
    monkeypatch.setattr(review, "personennamen", lambda: {})

    zeilen = einfrieren.sammle(samples)
    hypothek = [z for z in zeilen if z["dokument"] == "h.pdf"]
    assert len(hypothek) == 6
    assert {z["unterscheidung"] for z in hypothek} == {M1, M2, M3}
    assert [z["dokument"] for z in zeilen if z["dokument"] != "h.pdf"] == ["k.pdf"]


def test_einfrieren_schluesselt_die_herkunft_auf():
    from scripts.einfrieren import aufschluesselung

    zeilen = ([{"formular": "Schuldenverzeichnis", "_quelle": "Ablage", "_im_ordner": True}] * 6
              + [{"formular": "Schuldenverzeichnis", "_quelle": "Ablage", "_im_ordner": False,
                  "_gedeckt": True}] * 2
              + [{"formular": "Schuldenverzeichnis", "_quelle": "Ablage", "_im_ordner": False}])
    assert aufschluesselung(zeilen) == [(
        "Schuldenverzeichnis", 7,
        "9 Ablage · 2 ausgelassen (gleicher Wert eines Dokuments im Ordner)"
        " · 1 ⚠ Dokument nicht mehr im Ordner")]


def test_einfrieren_laesst_gedeckte_verwaiste_aus_und_markiert_den_rest():
    """9 Schulden-Zeilen bei drei Hypotheken: drei geprüfte Werte hingen an
    einem Dokument, das nicht mehr im Ordner liegt (261028). Gleicher Wert,
    gleiche Bank wie im Ordner → derselbe; sonst bleibt er, mit Hinweis."""
    from scripts.einfrieren import NICHT_IM_ORDNER, _verwaiste_abgleichen

    def z(betrag, bank, im_ordner, ziel=SCHULD):
        return {"beschreibung": ziel, "betrag": betrag, "aussteller": bank,
                "_im_ordner": im_ordner}

    da = [z("200000.00", "Musterbank AG", True), z("1000.00", "Musterbank AG", True, SCHULDZINS)]
    alt = [z("200'000.00", "Musterbank", False),          # gleicher Wert → gedeckt
           z("999.00", "Musterbank", False, SCHULDZINS),  # anderer Zins → bleibt
           z("200000.00", "Andere Bank", False)]           # andere Bank → bleibt
    _verwaiste_abgleichen(da + alt)
    assert alt[0].get("_gedeckt") is True
    assert [x.get("hinweis") for x in alt[1:]] == [NICHT_IM_ORDNER] * 2
    assert not any(x.get("_gedeckt") or x.get("hinweis") for x in da)
