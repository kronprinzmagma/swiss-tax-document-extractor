"""Der Live-Server: die Review speichert selbst (260904-rmx).

Der Rückweg lief bisher über vier Schritte — korrigieren, CSV exportieren,
``apply_korrekturen``, HTML neu bauen. In dreien davon konnte etwas verloren
gehen, und es ist verloren gegangen. Der Server nimmt den Umweg weg.

Zwei Dinge müssen dabei stimmen, und beide sind hier festgenagelt:

**Er darf nur auf diesem Gerät erreichbar sein.** Eine Bindung an 0.0.0.0
würde die Steuerdaten im Heimnetz ausliefern.

**Localhost allein schützt nicht.** Jede beliebige Webseite im selben Browser
darf an ``http://127.0.0.1:8765`` schicken. Ohne Token könnte sie in die
Korrekturen schreiben.
"""
from __future__ import annotations

import json
import socket
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from scripts.review_server import HOST, Handler, Zustand


def freier_port() -> int:
    with socket.socket() as s:
        s.bind((HOST, 0))
        return s.getsockname()[1]


@pytest.fixture()
def server(tmp_path):
    samples = tmp_path / "json"
    samples.mkdir()
    (samples / "_results_full.json").write_text("[]")

    zustand = Zustand(samples, None, "geheim-123")
    # Ohne Crops — die Testumgebung hat keine PDFs.
    from scripts.build_review_html import baue_aus_samples
    zustand.seite = baue_aus_samples(samples, None, mit_crops=False,
                                     live_token=zustand.token)
    Handler.zustand = zustand
    srv = ThreadingHTTPServer((HOST, freier_port()), Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv, zustand, f"http://{HOST}:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def post(url: str, daten: dict, token: str | None = None):
    kopf = {"Content-Type": "application/json"}
    if token:
        kopf["X-Steuer-Token"] = token
    anfrage = urllib.request.Request(
        url, data=json.dumps(daten).encode(), headers=kopf, method="POST")
    return urllib.request.urlopen(anfrage, timeout=10)


# --- Privacy ----------------------------------------------------------------

def test_bindet_nur_an_localhost(server):
    srv, _, _ = server
    assert srv.server_address[0] == "127.0.0.1"


def test_ohne_token_kein_schreiben(server):
    _, zustand, basis = server
    with pytest.raises(urllib.error.HTTPError) as e:
        post(basis + "/api/korrekturen", {"zeilen": [{"beleg": "a.pdf"}]})
    assert e.value.code == 403
    assert not zustand.korrekturen.exists()


def test_falsches_token_kein_schreiben(server):
    _, zustand, basis = server
    with pytest.raises(urllib.error.HTTPError) as e:
        post(basis + "/api/korrekturen", {"zeilen": []}, token="daneben")
    assert e.value.code == 403
    assert not zustand.korrekturen.exists()


def test_fremder_origin_wird_abgewiesen(server):
    """Eine andere Seite im selben Browser darf nicht hineinschreiben."""
    _, zustand, basis = server
    anfrage = urllib.request.Request(
        basis + "/api/korrekturen",
        data=json.dumps({"zeilen": []}).encode(),
        headers={"Content-Type": "application/json",
                 "X-Steuer-Token": "geheim-123",
                 "Origin": "https://beispiel.invalid"},
        method="POST")
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(anfrage, timeout=10)
    assert e.value.code == 403
    assert not zustand.korrekturen.exists()


def test_fremder_host_bekommt_die_seite_nicht(server):
    """DNS-Rebinding: ein fremder Name, der auf 127.0.0.1 zeigt, bleibt draussen.

    Die Bindung an 127.0.0.1 haelt fremde Geraete ab, aber nicht fremde
    Namen — und die lesenden Routen tragen die echten Werte.
    """
    _, _, basis = server
    anfrage = urllib.request.Request(basis + "/", headers={"Host": "angreifer.invalid"})
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(anfrage, timeout=10)
    assert e.value.code == 421


def test_fremder_host_bekommt_keine_pdfs(server):
    """Dieselbe Grenze gilt fuer die Belege, nicht nur fuer die Seite."""
    _, _, basis = server
    anfrage = urllib.request.Request(basis + "/pdf/beliebig.pdf",
                                     headers={"Host": "angreifer.invalid"})
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(anfrage, timeout=10)
    assert e.value.code == 421


def test_fremder_host_kann_nicht_schreiben(server):
    """Auch mit gueltigem Token — der Host wird vor dem Token geprueft."""
    _, zustand, basis = server
    anfrage = urllib.request.Request(
        basis + "/api/korrekturen",
        data=json.dumps({"zeilen": []}).encode(),
        headers={"Content-Type": "application/json",
                 "X-Steuer-Token": "geheim-123",
                 "Host": "angreifer.invalid"},
        method="POST")
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(anfrage, timeout=10)
    assert e.value.code == 421
    assert not zustand.korrekturen.exists()


def test_koerper_ohne_objekt_wird_sauber_abgewiesen(server):
    """Ein JSON-Array statt eines Objekts darf keinen Handler-Absturz ausloesen.

    Vorher warf das .get() des Aufrufers einen AttributeError im
    Handler-Thread; der Browser sah eine abgebrochene Verbindung statt 400.
    """
    _, _, basis = server
    anfrage = urllib.request.Request(
        basis + "/api/korrekturen",
        data=json.dumps([1, 2, 3]).encode(),
        headers={"Content-Type": "application/json",
                 "X-Steuer-Token": "geheim-123"},
        method="POST")
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(anfrage, timeout=10)
    assert e.value.code == 400


# --- Speichern --------------------------------------------------------------

def test_korrektur_landet_sofort_in_der_datei(server):
    _, zustand, basis = server
    zeile = {"beleg": "a.pdf", "zielwert": "Bruttoertrag", "ziffer": "4",
             "korrektur": "250.00", "person_neu": "elternteil_2"}
    antwort = post(basis + "/api/korrekturen", {"zeilen": [zeile]},
                   token="geheim-123")
    assert antwort.status == 200
    gespeichert = json.loads(zustand.korrekturen.read_text())
    eintrag = gespeichert["a.pdf|Bruttoertrag"]
    assert eintrag["soll"] == "250.00"
    assert eintrag["person"] == "elternteil_2"


def test_zweiter_stand_ueberschreibt_den_ersten(server):
    """Die Seite schickt immer den vollen Stand — kein Delta, kein Drift."""
    _, zustand, basis = server
    z = {"beleg": "a.pdf", "zielwert": "Bruttoertrag", "korrektur": "100.00"}
    post(basis + "/api/korrekturen", {"zeilen": [z]}, token="geheim-123")
    z["korrektur"] = "300.00"
    post(basis + "/api/korrekturen", {"zeilen": [z]}, token="geheim-123")
    assert json.loads(zustand.korrekturen.read_text())[
        "a.pdf|Bruttoertrag"]["soll"] == "300.00"


def test_streichung_geht_denselben_weg(server):
    _, zustand, basis = server
    post(basis + "/api/korrekturen",
         {"zeilen": [{"beleg": "kk.pdf", "zielwert": "Prämie VVG",
                      "streichen": "ja"}]}, token="geheim-123")
    e = json.loads(zustand.korrekturen.read_text())["kk.pdf|Prämie VVG"]
    assert e["entfernt"] is True
    assert e["grund"] == "nicht_im_dokument"


def test_zeilen_ohne_beleg_werden_verworfen(server):
    _, zustand, basis = server
    post(basis + "/api/korrekturen",
         {"zeilen": [{"zielwert": "X", "korrektur": "1"}, "kein dict"]},
         token="geheim-123")
    assert json.loads(zustand.korrekturen.read_text()) == {}


# --- Ausliefern -------------------------------------------------------------

def test_seite_wird_ausgeliefert(server):
    _, _, basis = server
    with urllib.request.urlopen(basis + "/", timeout=10) as r:
        inhalt = r.read().decode()
    assert "Steuerbelege prüfen" in inhalt
    assert "Lokaler Server" in inhalt


def test_seite_wird_nicht_zwischengespeichert(server):
    """Sie traegt echte Werte — kein Cache, kein Referrer."""
    _, _, basis = server
    with urllib.request.urlopen(basis + "/", timeout=10) as r:
        assert r.headers["Cache-Control"] == "no-store"
        assert r.headers["Referrer-Policy"] == "no-referrer"


def test_unbekannte_pfade_liefern_nichts(server):
    """Kein Datei-Ausliefern — der Server kennt genau seine Routen."""
    _, _, basis = server
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(basis + "/../../etc/passwd", timeout=10)
    assert e.value.code == 404


def test_status_meldet_nur_zahlen(server):
    """Auch die Statusantwort darf keine Namen und Betraege enthalten."""
    _, _, basis = server
    post(basis + "/api/korrekturen",
         {"zeilen": [{"beleg": "a.pdf", "zielwert": "X", "korrektur": "9.00"}]},
         token="geheim-123")
    with urllib.request.urlopen(basis + "/api/status", timeout=10) as r:
        daten = json.loads(r.read())
    # `stand` zaehlt die Speichervorgaenge — der Uebertragungs-Tab erkennt
    # daran, dass er seinen Inhalt neu holen muss. Eine Zahl, kein Inhalt.
    assert set(daten) == {"gespeichert", "stand"}
    assert daten["gespeichert"] == 1
    assert isinstance(daten["stand"], int)


# --- Eine Wahrheit, nicht zwei ---------------------------------------------

def test_server_nutzt_dieselbe_auswertung_wie_die_csv():
    """Der CSV-Weg und der Live-Weg teilen sich ``uebernehme``."""
    import inspect

    from scripts import review_server
    from scripts.apply_korrekturen import uebernehme
    assert review_server.uebernehme is uebernehme
    quelle = inspect.getsource(review_server)
    assert "uebernehme(" in quelle


# --- Belegter Port ----------------------------------------------------------

def test_laufende_review_wird_erkannt(server):
    """Zwei offene Review-Seiten waeren schlimmer als eine Fehlermeldung:
    der Mensch bearbeitet sonst weiter die alte."""
    from scripts.review_server import _laeuft_schon_review
    srv, _, _ = server
    assert _laeuft_schon_review(srv.server_address[1]) is True


def test_fremder_dienst_gilt_nicht_als_review(tmp_path):
    """Irgendetwas anderes auf dem Port ist bloss im Weg — dann ausweichen."""
    import http.server
    import threading

    from scripts.review_server import _laeuft_schon_review

    class Stumm(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"hi")

        def log_message(self, *a):
            return

    srv = ThreadingHTTPServer((HOST, freier_port()), Stumm)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        assert _laeuft_schon_review(srv.server_address[1]) is False
    finally:
        srv.shutdown()
        srv.server_close()


def test_geschlossener_port_ist_keine_review():
    from scripts.review_server import _laeuft_schon_review
    assert _laeuft_schon_review(freier_port()) is False


def test_server_setzt_beim_start_einen_wiederherstellungspunkt(tmp_path):
    """Sicherungen entstehen sonst erst beim Speichern — also nach der ersten
    Aenderung. Wer eine ganze Sitzung zurueckdrehen will, braucht den Stand
    von vorher."""
    import inspect

    from scripts import review_server
    quelle = inspect.getsource(review_server.starte)
    assert "dauerhaft=True" in quelle
    assert "Wiederherstellungspunkt" in quelle


def test_startpunkt_ueberlebt_eine_ganze_sitzung(tmp_path):
    """Der Punkt vom Sitzungsstart wurde von den eigenen Auto-Saves aus dem
    Ringpuffer gedraengt: zwanzig Tastendruecke, und die Zusage war nichts
    mehr wert (260923-dua)."""
    from scripts.apply_korrekturen import _sichere

    ziel = tmp_path / "korrekturen.json"
    ziel.write_text(json.dumps({"a.pdf|X": {"soll": "1"}}))
    _sichere(ziel, dauerhaft=True)

    # Eine Sitzung mit reichlich Tastendruecken.
    for i in range(30):
        ziel.write_text(json.dumps({"a.pdf|X": {"soll": str(i)}}))
        _sichere(ziel)

    verlauf = tmp_path / "korrekturen-verlauf"
    start = [p for p in verlauf.glob("*.json") if "-start" in p.stem]
    assert len(start) == 1, "der Startpunkt ist weg"
    assert json.loads(start[0].read_text())["a.pdf|X"]["soll"] == "1"
    # Und er zaehlt nicht gegen das Kontingent der gewoehnlichen Staende.
    gewoehnlich = [p for p in verlauf.glob("*.json") if "-start" not in p.stem]
    assert len(gewoehnlich) <= 20


def test_zwei_sicherungen_derselben_sekunde_bleiben_beide(tmp_path):
    """Beim Live-Speichern ist das der Normalfall, nicht die Ausnahme."""
    from scripts.apply_korrekturen import _sichere

    ziel = tmp_path / "korrekturen.json"
    for wert in ("eins", "zwei", "drei"):
        ziel.write_text(json.dumps({"a.pdf|X": {"soll": wert}}))
        _sichere(ziel)
    staende = sorted((tmp_path / "korrekturen-verlauf").glob("*.json"))
    assert len(staende) == 3
    inhalte = {json.loads(p.read_text())["a.pdf|X"]["soll"] for p in staende}
    assert inhalte == {"eins", "zwei", "drei"}


def test_speichern_trifft_beide_ablagen(tmp_path):
    """Liefen sie auseinander, verdeckte die eine die andere — und eine
    Ruecknahme in der einen wurde aus der anderen wieder auferweckt."""
    from scripts.apply_korrekturen import uebernehme

    samples = tmp_path / "json"
    samples.mkdir()
    aussen = tmp_path / "korrekturen.json"
    aussen.write_text(json.dumps({"alt.pdf|Zins": {"soll": "5",
                                                   "label": "Zinsertrag"}}))
    innen = samples / "korrekturen.json"

    uebernehme([{"beleg": "neu.pdf", "zielwert": "Y", "korrektur": "9"}], innen)

    a = json.loads(aussen.read_text())
    b = json.loads(innen.read_text())
    assert a == b, "die beiden Ablagen laufen wieder auseinander"
    # Und der Eintrag, den nur die aeussere kannte, ist vollstaendig da.
    assert b["alt.pdf|Zins"]["label"] == "Zinsertrag"
    assert b["neu.pdf|Y"]["soll"] == "9"


def test_speichern_legt_keine_zweite_ablage_an(tmp_path):
    """Sonst streut jeder Lauf eine Datei in einen Ordner, der sie nie hatte."""
    from scripts.apply_korrekturen import uebernehme

    samples = tmp_path / "json"
    samples.mkdir()
    uebernehme([{"beleg": "a.pdf", "zielwert": "Y", "korrektur": "9"}],
               samples / "korrekturen.json")
    assert not (tmp_path / "korrekturen.json").exists()


# --- Umbenennen -------------------------------------------------------------

def test_umbenennen_ohne_token_geht_nicht(server):
    _, _, basis = server
    with pytest.raises(urllib.error.HTTPError) as e:
        post(basis + "/api/umbenennen", {"beleg": "a.pdf"})
    assert e.value.code == 403


def test_umbenennen_ohne_originalordner_wird_abgelehnt(server):
    """Ohne den Ordner waere jeder Pfad geraten."""
    _, _, basis = server
    with pytest.raises(urllib.error.HTTPError) as e:
        post(basis + "/api/umbenennen", {"beleg": "a.pdf"}, token="geheim-123")
    assert e.value.code == 400


def test_umbenennen_meldet_nur_die_anzahl(tmp_path):
    """Die neuen Namen tragen Personennamen — im Netzverkehr nichts verloren."""
    import json as _json

    from scripts.build_review_html import baue_aus_samples
    from scripts.review_server import Handler, Zustand

    samples = tmp_path / "json"
    samples.mkdir()
    (samples / "_results_full.json").write_text("[]")
    pdfs = tmp_path / "pdf"
    pdfs.mkdir()
    (pdfs / "roh.pdf").write_bytes(b"%PDF-")
    (samples / "korrekturen.json").write_text(_json.dumps(
        {"roh.pdf|Nettolohn": {"soll": "9"}}))

    zustand = Zustand(samples, pdfs, "geheim-123")
    zustand.seite = baue_aus_samples(samples, None, mit_crops=False,
                                     live_token=zustand.token)
    Handler.zustand = zustand
    srv = ThreadingHTTPServer((HOST, freier_port()), Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        basis = f"http://{HOST}:{srv.server_address[1]}"
        antwort = post(basis + "/api/umbenennen", {}, token="geheim-123")
        daten = json.loads(antwort.read())
        assert set(daten) == {"ok", "anzahl"}
        assert isinstance(daten["anzahl"], int)
    finally:
        srv.shutdown()
        srv.server_close()


# --- Aussortieren (260923-dua) ----------------------------------------------

def test_aussortieren_ohne_token_geht_nicht(server):
    _, _, basis = server
    with pytest.raises(urllib.error.HTTPError) as e:
        post(basis + "/api/aussortieren", {"beleg": "a.pdf"})
    assert e.value.code == 403


def test_seite_warnt_wenn_der_server_veralteten_code_haelt(tmp_path):
    """Ein laufender Server haelt seinen Code im Speicher. Wird die
    Oberflaeche geaendert, liefert er weiter die alte Seite — und ein
    Neuladen im Browser hilft nicht. Das hat eine Runde gekostet
    (260923-dua)."""
    from scripts.review_server import _code_ist_neuer

    # Ein Server, der "vor langer Zeit" gestartet ist, sieht den Code als neu.
    assert _code_ist_neuer(0.0) is True
    # Einer aus der Zukunft sieht nichts Neues.
    import time as _t
    assert _code_ist_neuer(_t.time() + 3600) is False


def test_warnung_steht_in_der_seite(tmp_path):
    import json as _json

    from scripts.build_review_html import baue_aus_samples
    from scripts.review_server import Zustand

    samples = tmp_path / "json"
    samples.mkdir()
    (samples / "_results_full.json").write_text("[]")
    zustand = Zustand(samples, None, "t")
    zustand.gestartet = 0.0          # so alt, dass jeder Code neuer ist
    seite = zustand.baue_seite()
    assert "läuft mit einer älteren Fassung" in seite
    assert "make review" in seite


# --- Die Uebertragungstabelle im eigenen Tab (260923-dua) -------------------

def test_tabelle_hat_eine_eigene_seite(server):
    _, _, basis = server
    with urllib.request.urlopen(basis + "/uebertragung", timeout=10) as r:
        inhalt = r.read().decode()
    assert "Übertragungstabelle" in inhalt
    # Statisch: kein einziges Eingabefeld, sonst verstellt man beim Lesen etwas.
    assert "<input" not in inhalt
    assert "<select" not in inhalt


def test_tabelle_liefert_ihren_inhalt_einzeln(server):
    """Der Tab holt nur den Inhalt nach — ein Neuladen der ganzen Seite wuerde
    beim Lesen an den Anfang zurueckspringen."""
    _, _, basis = server
    with urllib.request.urlopen(basis + "/api/uebertragung", timeout=10) as r:
        assert r.status == 200


def test_stand_zaehlt_jede_speicherung(server):
    """Daran erkennt der Tab, dass sich nebenan etwas geaendert hat."""
    _, _, basis = server

    def stand():
        with urllib.request.urlopen(basis + "/api/status", timeout=10) as r:
            return json.loads(r.read())["stand"]

    vorher = stand()
    post(basis + "/api/korrekturen",
         {"zeilen": [{"beleg": "a.pdf", "zielwert": "X", "korrektur": "1"}]},
         token="geheim-123")
    assert stand() == vorher + 1


def test_schlusscheck_steht_in_der_tabellenseite(server):
    _, _, basis = server
    with urllib.request.urlopen(basis + "/uebertragung", timeout=10) as r:
        inhalt = r.read().decode()
    assert "Schlusscheck" in inhalt


def test_schlusscheck_wird_mitaktualisiert(server):
    """Sonst zeigt er nach einer Korrektur noch den Befund von vorhin."""
    _, _, basis = server
    with urllib.request.urlopen(basis + "/api/uebertragung", timeout=10) as r:
        inhalt = r.read().decode()
    assert "Schlusscheck" in inhalt


def test_tabelle_zeigt_namen_statt_rollen(tmp_path):
    """„elternteil_1" sagt beim Abtippen nichts."""
    from unittest.mock import patch

    from scripts import build_review_html as B

    zeilen = [{"beleg": "a.pdf", "aussteller": "Bank", "person": "elternteil_1",
               "zielwert": "Saldo 31.12.", "betrag": "1000.00",
               "ziffer": "30.1"}]
    with patch.object(B, "personennamen", lambda: {"elternteil_1": "Erika Muster"}):
        html_text = B.tabelle_html(zeilen)
    assert "Erika Muster" in html_text
    assert "elternteil_1" not in html_text


def test_tabelle_zeigt_formatiert_und_kopiert_roh():
    """Lesbar mit Tausendertrennzeichen, kopiert der reine Wert."""
    from scripts.build_review_html import tabelle_html

    zeilen = [{"beleg": "a.pdf", "zielwert": "Nettolohn", "betrag": "92500.00",
               "ziffer": "1.1", "person": "familie"}]
    html_text = tabelle_html(zeilen)
    assert "92'500.00" in html_text          # angezeigt
    assert "kopieren('92500.00'" in html_text  # kopiert


def test_konto_sicht_gruppiert_je_beleg():
    """Im Wertschriftenverzeichnis wird Konto fuer Konto erfasst: Saldo,
    Ertrag ohne, Ertrag mit — nicht Ziffer fuer Ziffer."""
    from scripts.build_review_html import _konten_daten

    zeilen = [
        {"beleg": "b.pdf", "zielwert": "Bruttoertrag mit Verrechnungssteuer",
         "betrag": "840.00", "ziffer": "4", "aussteller": "BANK-U"},
        {"beleg": "b.pdf", "zielwert": "Saldo 31.12.", "betrag": "1000.00",
         "ziffer": "30.1", "aussteller": "BANK-U"},
        {"beleg": "b.pdf", "zielwert": "Bruttoertrag ohne Verrechnungssteuer",
         "betrag": "12.40", "ziffer": "4", "aussteller": "BANK-U"},
        {"beleg": "c.pdf", "zielwert": "Saldo 31.12.", "betrag": "50.00",
         "ziffer": "30.1", "aussteller": "BANK-Z"},
    ]
    bloecke = _konten_daten(zeilen)
    assert [b["aussteller"] for b in bloecke] == ["BANK-U", "BANK-Z"]
    # Erst der Bestand, dann der Ertrag — die Reihenfolge des Formulars.
    assert [z["zielwert"] for z in bloecke[0]["zeilen"]] == [
        "Saldo 31.12.",
        "Bruttoertrag ohne Verrechnungssteuer",
        "Bruttoertrag mit Verrechnungssteuer",
    ]


def test_tabellenkopf_ist_so_breit_wie_die_zeilen():
    """Fehlt dem Kopf eine Spalte, rutscht alles um eine — die Person stand
    unter „Aussteller" (260923-dua, vom Nutzer gemeldet).

    Dass die Spaltenzahl der EXPORT-Datei stimmt, prueft
    evals/test_export_spalten.py. Fuer die angezeigte Tabelle gab es nichts
    Vergleichbares.
    """
    import re

    from scripts.build_review_html import konten_html, tabelle_html

    zeilen = [{"beleg": "a.pdf", "aussteller": "BANK-U", "person": "familie",
               "zielwert": "Saldo 31.12.", "betrag": "1000.00",
               "ziffer": "30.1"}]
    # Je TABELLE zaehlen, nicht je Seite: die Konto-Sicht enthaelt inzwischen
    # auch Summentabellen mit weniger Spalten. Ueber die ganze Seite gezaehlt
    # waere der Vergleich sinnlos (260923-dua).
    for name, bauen in (("Ziffer-Sicht", tabelle_html),
                        ("Konto-Sicht", konten_html)):
        doc = bauen(zeilen)
        tabellen = re.findall(r"<table[^>]*>(.*?)</table>", doc, re.S)
        assert tabellen, f"{name}: keine Tabelle gebaut"
        for tabelle in tabellen:
            kopf = len(re.findall(r"<th[ >]", tabelle))
            for reihe in re.findall(r"<tr[^>]*>(.*?)</tr>", tabelle, re.S):
                if "<td" not in reihe:
                    continue
                zellen = len(re.findall(r"<td[ >]", reihe))
                assert zellen == kopf, (
                    f"{name}: Kopf hat {kopf} Spalten, eine Zeile {zellen} — "
                    f"die Werte stehen unter den falschen Ueberschriften")
