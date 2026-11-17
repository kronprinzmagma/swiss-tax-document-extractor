"""PDF-Links funktionieren auch hinter dem lokalen Server (260904-rmx).

Browser verweigern jede Navigation von einer ``http://``-Seite zu einer
``file://``-URL. Das ist eine Sicherheitsregel, kein Fehler — aber seit die
Review über localhost läuft statt als Datei, waren damit alle Links auf die
Original-PDFs tot. Ausgerechnet die: der ganze Korrektheits-Constraint
lautet, dass jeder Wert im Original nachprüfbar sein muss.

Der Server liefert die Dokumente deshalb selbst aus — und nur die, die
direkt im Dokumentenordner liegen.
"""
from __future__ import annotations

import socket
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from scripts.build_review_html import _pdf_link
from scripts.review_server import HOST, Handler, Zustand


def freier_port() -> int:
    with socket.socket() as s:
        s.bind((HOST, 0))
        return s.getsockname()[1]


# --- Die Linkform -----------------------------------------------------------

def test_als_datei_bleibt_es_ein_file_link(tmp_path):
    (tmp_path / "a.pdf").write_bytes(b"%PDF-1.4\n")
    assert _pdf_link(tmp_path, "a.pdf", 2).startswith("file://")


def test_hinter_dem_server_geht_der_link_ueber_den_server(tmp_path):
    (tmp_path / "a.pdf").write_bytes(b"%PDF-1.4\n")
    assert _pdf_link(tmp_path, "a.pdf", 2, live=True) == "/pdf/a.pdf#page=2"


def test_sonderzeichen_werden_kodiert(tmp_path):
    (tmp_path / "Kontoauszug 2025.pdf").write_bytes(b"%PDF-1.4\n")
    link = _pdf_link(tmp_path, "Kontoauszug 2025.pdf", 1, live=True)
    assert link.startswith("/pdf/Kontoauszug%202025.pdf")


def test_fehlendes_pdf_ergibt_keinen_link(tmp_path):
    assert _pdf_link(tmp_path, "gibtsnicht.pdf", 1, live=True) == ""


# --- Die Auslieferung -------------------------------------------------------

@pytest.fixture()
def server(tmp_path):
    samples, quelle = tmp_path / "json", tmp_path / "quelle"
    samples.mkdir()
    quelle.mkdir()
    (samples / "_results_full.json").write_text("[]")
    (quelle / "a.pdf").write_bytes(b"%PDF-1.4\ntest\n")
    (tmp_path / "geheim.txt").write_text("nicht ausliefern")

    zustand = Zustand(samples, quelle, "geheim-123")
    zustand.seite = "<html></html>"
    Handler.zustand = zustand
    srv = ThreadingHTTPServer((HOST, freier_port()), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://{HOST}:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def test_pdf_wird_ausgeliefert(server):
    with urllib.request.urlopen(server + "/pdf/a.pdf", timeout=10) as r:
        assert r.headers["Content-Type"] == "application/pdf"
        assert r.read().startswith(b"%PDF")


def test_pdf_wird_nicht_zwischengespeichert(server):
    with urllib.request.urlopen(server + "/pdf/a.pdf", timeout=10) as r:
        assert r.headers["Cache-Control"] == "no-store"


def test_unbekanntes_pdf_gibt_404(server):
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(server + "/pdf/gibtsnicht.pdf", timeout=10)
    assert e.value.code == 404


def test_kein_weg_aus_dem_ordner_heraus(server):
    """Der Server darf kein Dateibrowser werden."""
    for versuch in ("../geheim.txt", "..%2Fgeheim.txt",
                    "%2e%2e%2fgeheim.txt", "/etc/passwd"):
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(f"{server}/pdf/{versuch}", timeout=10)
        assert e.value.code == 404, versuch


def test_ohne_dokumentenordner_kein_pdf(tmp_path):
    samples = tmp_path / "json"
    samples.mkdir()
    (samples / "_results_full.json").write_text("[]")
    zustand = Zustand(samples, None, "t")
    zustand.seite = "<html></html>"
    Handler.zustand = zustand
    srv = ThreadingHTTPServer((HOST, freier_port()), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(
                f"http://{HOST}:{srv.server_address[1]}/pdf/a.pdf", timeout=10)
        assert e.value.code == 404
    finally:
        srv.shutdown()
        srv.server_close()
