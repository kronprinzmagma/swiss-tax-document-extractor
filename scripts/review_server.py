#!/usr/bin/env python3
"""Live-Oberfläche: die Review speichert selbst, statt CSVs zu exportieren.

Bisher war die Durchsicht ein Umweg: im HTML korrigieren → CSV exportieren →
``apply_korrekturen`` → ``build_tax_output`` → HTML neu bauen. Vier Schritte
für jede Runde, und in drei davon konnte etwas verloren gehen — es ist auch
verloren gegangen.

Dieser Server nimmt der Seite den Umweg ab. Er liefert genau dieselbe Seite
aus, die ``build_review_html.py`` erzeugt; weil sie jetzt über ``http://``
kommt statt als Datei, kann sie zurückschreiben. Jede Änderung landet sofort
in ``korrekturen.json``, und ein Knopf baut die Übertragungstabelle neu.

**Privacy.** Der Server bindet ausschliesslich an 127.0.0.1 und weigert sich,
etwas anderes zu tun — eine Bindung an 0.0.0.0 würde die Steuerdaten im
Heimnetz ausliefern. Er spricht mit nichts ausser dem eigenen Browser.

**Warum ein Token.** Localhost ist nicht privat: jede beliebige Webseite im
selben Browser darf an ``http://127.0.0.1:8765`` schicken. Ohne Schutz könnte
sie in die Korrekturen schreiben. Die Seite bekommt deshalb beim Start ein
zufälliges Token mit, das jeder Schreibzugriff vorweisen muss; fremde Seiten
kennen es nicht. Zusätzlich wird der ``Origin``-Header geprüft.

Aufruf::

    PYTHONPATH=. .venv/bin/python scripts/review_server.py \\
        --samples output/latest/json --pdf-dir belege
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.apply_korrekturen import uebernehme

HOST = "127.0.0.1"
DEFAULT_PORT = 8765
MAX_BODY = 8 * 1024 * 1024   # Grosszuegig fuer viele Zeilen, aber begrenzt.


class Zustand:
    """Was der Server zwischen den Anfragen braucht."""

    def __init__(self, samples: Path, pdf_dir: Path | None, token: str):
        self.samples = samples
        self.pdf_dir = pdf_dir
        self.token = token
        self.seite: str = ""
        self.sperre = threading.Lock()

    @property
    def korrekturen(self) -> Path:
        return self.samples / "korrekturen.json"

    def baue_seite(self) -> str:
        """Seite neu erzeugen — mit allem, was inzwischen gespeichert ist."""
        from scripts.build_review_html import baue_aus_samples
        self.seite = baue_aus_samples(self.samples, self.pdf_dir,
                                      live_token=self.token)
        return self.seite


def _tabelle_neu(samples: Path) -> dict:
    """Übertragungstabelle neu bauen — derselbe Weg wie ``make tabelle``."""
    py = sys.executable
    umgebung = dict(os.environ, PYTHONPATH=str(ROOT))
    ergebnis = subprocess.run(
        [py, str(ROOT / "scripts" / "build_tax_output.py"), "--samples", str(samples)],
        capture_output=True, text=True, env=umgebung, timeout=300,
    )
    if ergebnis.returncode != 0:
        return {"ok": False, "fehler": ergebnis.stderr[-2000:]}

    # Die MDs liegen neben den JSONs; der Mensch sucht sie eine Ebene hoeher.
    ziel = samples.parent
    kopiert = []
    for name in ("steuer_uebertragung.md", "nachweis_anhang.md"):
        quelle = samples / name
        if quelle.exists():
            (ziel / name).write_text(quelle.read_text(encoding="utf-8"),
                                     encoding="utf-8")
            kopiert.append(name)
    return {"ok": True, "dateien": kopiert, "ordner": str(ziel)}


class Handler(BaseHTTPRequestHandler):
    server_version = "steuer-extraktor-review"
    zustand: Zustand   # wird beim Start gesetzt

    # --- Hilfen ------------------------------------------------------------

    def _antwort(self, code: int, inhalt: bytes, typ: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", typ)
        self.send_header("Content-Length", str(len(inhalt)))
        # Die Seite traegt echte Werte — kein Cache, kein Verweis nach aussen.
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(inhalt)

    def _json(self, code: int, daten: dict) -> None:
        self._antwort(code, json.dumps(daten, ensure_ascii=False).encode(),
                      "application/json; charset=utf-8")

    def _berechtigt(self) -> bool:
        """Token und Origin pruefen — Localhost allein schuetzt nicht."""
        if self.headers.get("X-Steuer-Token") != self.zustand.token:
            return False
        origin = self.headers.get("Origin")
        if origin and origin not in (f"http://{HOST}:{self.server.server_address[1]}",
                                     f"http://localhost:{self.server.server_address[1]}"):
            return False
        return True

    def _koerper(self) -> dict | None:
        try:
            laenge = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return None
        if laenge <= 0 or laenge > MAX_BODY:
            return None
        try:
            return json.loads(self.rfile.read(laenge).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None

    # --- Routen ------------------------------------------------------------

    def do_GET(self) -> None:
        pfad = self.path.split("?", 1)[0]
        if pfad in ("/", "/index.html", "/review.html"):
            with self.zustand.sperre:
                seite = self.zustand.seite or self.zustand.baue_seite()
            self._antwort(200, seite.encode("utf-8"), "text/html; charset=utf-8")
            return
        if pfad == "/api/status":
            eintraege = 0
            if self.zustand.korrekturen.exists():
                try:
                    eintraege = len(json.loads(self.zustand.korrekturen.read_text()))
                except (OSError, json.JSONDecodeError):
                    eintraege = -1
            self._json(200, {"gespeichert": eintraege})
            return
        if pfad.startswith("/pdf/"):
            self._pdf(pfad[len("/pdf/"):])
            return
        self._antwort(404, b"nicht gefunden", "text/plain; charset=utf-8")

    def _pdf(self, roh: str) -> None:
        """Ein Original-PDF ausliefern — nur aus dem Beleg-Ordner.

        Der Browser laesst von einer http-Seite aus keinen file://-Link zu,
        also muss der Server das Dokument selbst herausgeben. Ausgeliefert
        wird ausschliesslich eine Datei, die direkt im uebergebenen Ordner
        liegt: der Name wird auf seinen Basisnamen reduziert und der
        aufgeloeste Pfad muss im Ordner liegen. Damit laeuft "../.." ins
        Leere, und der Server wird nicht zum Dateibrowser.
        """
        from urllib.parse import unquote

        if self.zustand.pdf_dir is None:
            self._antwort(404, b"kein Beleg-Ordner", "text/plain; charset=utf-8")
            return
        name = Path(unquote(roh)).name
        ziel = (self.zustand.pdf_dir / name).resolve()
        ordner = self.zustand.pdf_dir.resolve()
        if ordner not in ziel.parents or not ziel.is_file():
            self._antwort(404, b"nicht gefunden", "text/plain; charset=utf-8")
            return
        try:
            inhalt = ziel.read_bytes()
        except OSError:
            self._antwort(404, b"nicht lesbar", "text/plain; charset=utf-8")
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/pdf")
        self.send_header("Content-Length", str(len(inhalt)))
        self.send_header("Content-Disposition", "inline")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(inhalt)

    def do_POST(self) -> None:
        pfad = self.path.split("?", 1)[0]
        if not self._berechtigt():
            self._json(403, {"fehler": "Token fehlt oder stimmt nicht"})
            return
        koerper = self._koerper()
        if koerper is None:
            self._json(400, {"fehler": "Ungueltiger Inhalt"})
            return

        if pfad == "/api/korrekturen":
            zeilen = koerper.get("zeilen")
            if not isinstance(zeilen, list):
                self._json(400, {"fehler": "zeilen fehlt"})
                return
            # Dieselbe Auswertung wie beim CSV-Weg — eine Wahrheit, nicht zwei.
            # ``zielwert`` wird hart indiziert — ohne ihn gaebe es eine
            # KeyError und der Browser saehe nur „nicht gespeichert".
            zeilen = [z for z in zeilen if isinstance(z, dict)
                      and z.get("beleg") and z.get("zielwert") is not None]
            with self.zustand.sperre:
                bericht = uebernehme(zeilen, self.zustand.korrekturen,
                                     ersetzen=True)
            self._json(200, {"ok": True, "gespeichert": bericht["gesamt"],
                             "neu": bericht["neu"],
                             "geaendert": bericht["geaendert"]})
            return

        if pfad == "/api/tabelle":
            with self.zustand.sperre:
                ergebnis = _tabelle_neu(self.zustand.samples)
                if ergebnis.get("ok"):
                    self.zustand.baue_seite()   # Seite zieht die Korrekturen nach
            self._json(200 if ergebnis.get("ok") else 500, ergebnis)
            return

        self._json(404, {"fehler": "unbekannte Route"})

    def log_message(self, format: str, *args) -> None:
        """Keine Pfade und keine Dateinamen ins Log — sie tragen Namen."""
        return


def _laeuft_schon_review(port: int) -> bool:
    """Ist der Port von einer aelteren Review belegt — oder von etwas anderem?

    Gefragt wird die Statusroute, die nur dieser Server kennt. Sie gibt
    ausschliesslich eine Zaehlung zurueck, also nichts, was hier stoeren
    koennte.
    """
    import urllib.error
    import urllib.request
    try:
        with urllib.request.urlopen(
                f"http://{HOST}:{port}/api/status", timeout=2) as antwort:
            return "gespeichert" in json.loads(antwort.read())
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
        return False


def starte(samples: Path, pdf_dir: Path | None, port: int,
           oeffnen: bool = True) -> int:
    if not samples.is_dir():
        print(f"Nicht gefunden: {samples}", file=sys.stderr)
        return 1

    token = secrets.token_urlsafe(24)
    zustand = Zustand(samples, pdf_dir, token)

    # Wiederherstellungspunkt VOR der ersten Eingabe.
    #
    # Die Sicherungen entstehen sonst erst beim Speichern — also nach der
    # ersten Aenderung. Wer eine Sitzung ganz zurueckdrehen will, braucht den
    # Stand von vorher. Er kostet ein paar Kilobyte (260904-rmx).
    from scripts.apply_korrekturen import _sichere
    vorher = 0
    if zustand.korrekturen.exists():
        try:
            vorher = len(json.loads(zustand.korrekturen.read_text()))
        except (OSError, json.JSONDecodeError):
            vorher = -1
        _sichere(zustand.korrekturen)

    print("Baue die Seite …")
    zustand.baue_seite()

    Handler.zustand = zustand
    try:
        server = ThreadingHTTPServer((HOST, port), Handler)
    except OSError:
        # Belegt. Zwei sehr verschiedene Faelle, und die Unterscheidung ist
        # wichtig: laeuft dort schon eine Review, wuerde ein zweiter Server
        # auf einem anderen Port bedeuten, dass zwei Seiten offen sind — und
        # der Mensch bearbeitet womoeglich die alte weiter. Etwas Fremdes
        # dagegen ist einfach im Weg; dann weichen wir aus.
        if _laeuft_schon_review(port):
            print(f"\nAuf http://{HOST}:{port}/ laeuft bereits eine Review.\n"
                  f"Entweder dort weiterarbeiten — oder die alte beenden:\n"
                  f"    kill $(lsof -t -iTCP:{port} -sTCP:LISTEN)\n"
                  f"und `make review` erneut starten.", file=sys.stderr)
            return 1
        try:
            server = ThreadingHTTPServer((HOST, 0), Handler)
        except OSError as fehler:
            print(f"Kein freier Port ({fehler}).", file=sys.stderr)
            return 1
        print(f"Port {port} ist belegt — weiche auf "
              f"{server.server_address[1]} aus.")

    adresse = f"http://{HOST}:{server.server_address[1]}/"
    print(f"\nReview laeuft: {adresse}")
    print("Nur auf diesem Geraet erreichbar. Aenderungen werden sofort "
          "gespeichert.")
    if vorher:
        print(f"{vorher} gespeicherte Eintraege geladen; ein "
              f"Wiederherstellungspunkt liegt im Verlauf.")
        print("Zurueck auf diesen Stand:  make staende  →  "
              "make zurueckrollen AUF=<Zeitpunkt>")
    print("Beenden mit Ctrl-C.\n")
    if oeffnen:
        webbrowser.open(adresse)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nBeendet.")
    finally:
        server.server_close()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--samples", type=Path, required=True,
                    help="Ordner mit den Word-JSONs und _results_full.json")
    ap.add_argument("--pdf-dir", type=Path, default=None,
                    help="Ordner mit den PDFs (fuer Ausschnitte und Seitenbild)")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--kein-browser", action="store_true",
                    help="Seite nicht automatisch oeffnen")
    args = ap.parse_args()
    return starte(args.samples, args.pdf_dir, args.port,
                  oeffnen=not args.kein_browser)


if __name__ == "__main__":
    raise SystemExit(main())
