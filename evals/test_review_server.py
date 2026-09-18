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
    assert daten == {"gespeichert": 1}


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
    assert "_sichere(zustand.korrekturen)" in quelle
    assert "Wiederherstellungspunkt" in quelle
