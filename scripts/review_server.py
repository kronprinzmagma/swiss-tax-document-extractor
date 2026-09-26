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
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from extractors.korrekturen import SchutzVerletzt
from scripts.apply_korrekturen import uebernehme

HOST = "127.0.0.1"
DEFAULT_PORT = 8765

# Solange die Review laeuft, liegt diese Datei im Sample-Ordner. Sie sagt
# anderen Werkzeugen: hier arbeitet gerade jemand.
#
# Ein Rollback waehrend einer offenen Review wird von der Seite beim naechsten
# Tastendruck lautlos rueckgaengig gemacht — sie haelt den alten Stand im
# Browser und schickt ihn vollstaendig zurueck (260923-dua). Die Erkennung
# haengt bewusst am KORPUS und nicht an einer Portnummer: sonst haelt ein
# Server, der einen ganz anderen Ordner bedient, das Zurueckrollen auf.
SPERRDATEI = "review-laeuft.json"


def sperre_setzen(samples: Path) -> Path:
    """Vermerkt, dass fuer diesen Korpus eine Review laeuft."""
    pfad = samples / SPERRDATEI
    try:
        pfad.write_text(json.dumps({"pid": os.getpid()}))
    except OSError:
        pass
    return pfad


def sperre_loesen(samples: Path) -> None:
    try:
        (samples / SPERRDATEI).unlink()
    except OSError:
        pass


def review_laeuft(samples: Path) -> bool:
    """Laeuft fuer diesen Korpus gerade eine Review?

    Eine Sperrdatei ohne lebenden Prozess dahinter ist ein Ueberbleibsel — ein
    abgestuerzter Server darf das Zurueckrollen nicht fuer immer blockieren.
    """
    pfad = samples / SPERRDATEI
    if not pfad.exists():
        return False
    try:
        pid = int(json.loads(pfad.read_text()).get("pid") or 0)
    except (OSError, ValueError, AttributeError):
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        sperre_loesen(samples)
        return False
    except PermissionError:
        return True
    return True


MAX_BODY = 8 * 1024 * 1024   # Grosszuegig fuer viele Zeilen, aber begrenzt.

# Ein laufender Server haelt seinen Code im Speicher.
#
# Wird die Oberflaeche geaendert, waehrend er laeuft, liefert er weiter die
# alte Seite aus — und ein Neuladen im Browser hilft nicht, weil die Seite aus
# dem Prozess kommt und nicht von der Platte. Das hat eine Runde gekostet: die
# Korrektur war da, der Mensch sah sie nicht und meldete den Fehler erneut
# (260923-dua). Deshalb sagt die Seite es jetzt selbst.
_CODE_DATEIEN = ("build_review_html.py", "review_server.py",
                 "build_tax_output.py")

_VERALTET = (
    '<p class="veraltet">Dieser Server läuft mit einer älteren Fassung der '
    'Oberfläche — der Code auf der Platte ist neuer. Die Seite kommt aus dem '
    'laufenden Prozess, ein Neuladen im Browser ändert daran nichts. '
    '<strong>Ctrl-C im Review-Fenster, dann <code>make review</code>.</strong>'
    '</p>')


def _code_ist_neuer(gestartet: float) -> bool:
    """Wurde die Oberflaeche geaendert, seit dieser Server gestartet ist?"""
    ordner = Path(__file__).resolve().parent
    for name in _CODE_DATEIEN:
        try:
            if (ordner / name).stat().st_mtime > gestartet:
                return True
        except OSError:
            continue
    return False


class Zustand:
    """Was der Server zwischen den Anfragen braucht."""

    def __init__(self, samples: Path, pdf_dir: Path | None, token: str):
        self.samples = samples
        self.pdf_dir = pdf_dir
        self.token = token
        self.seite: str = ""
        self.sperre = threading.Lock()
        self.gestartet = time.time()
        # Zaehlt jede gespeicherte Aenderung. Der Uebertragungs-Tab fragt ihn
        # ab und holt den Inhalt nur dann neu — so zieht er mit, ohne die
        # Arbeit nebenan zu stoeren (260923-dua).
        self.stand = 0

    @property
    def korrekturen(self) -> Path:
        return self.samples / "korrekturen.json"

    def baue_seite(self) -> str:
        """Seite neu erzeugen — mit allem, was inzwischen gespeichert ist."""
        from scripts.build_review_html import baue_aus_samples
        self.seite = baue_aus_samples(self.samples, self.pdf_dir,
                                      live_token=self.token)
        if _code_ist_neuer(self.gestartet):
            self.seite = self.seite.replace("<main>", "<main>" + _VERALTET, 1)
        return self.seite


def _tabelle_neu(samples: Path) -> dict:
    """Übertragungstabelle neu bauen — derselbe Weg wie ``make tabelle``."""
    py = sys.executable
    umgebung = dict(os.environ, PYTHONPATH=str(ROOT))
    try:
        ergebnis = subprocess.run(
            [py, str(ROOT / "scripts" / "build_tax_output.py"), "--samples", str(samples)],
            capture_output=True, text=True, env=umgebung, timeout=300,
        )
    except subprocess.TimeoutExpired:
        # Ohne diesen Zweig fliegt die Exception durch den Handler, die
        # Antwort bleibt aus, und der Mensch klickt "Tabelle neu bauen"
        # noch einmal — auf einen Lauf, der ohnehin schon zu lange braucht.
        return {"ok": False,
                "fehler": "Zeitueberschreitung nach 300 s. "
                          "`make tabelle` von Hand laufen lassen."}
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

    def _erlaubte_adressen(self) -> tuple[str, ...]:
        port = self.server.server_address[1]
        return (f"{HOST}:{port}", f"localhost:{port}")

    def _host_stimmt(self) -> bool:
        """Traegt die Anfrage unsere eigene Adresse im Host-Header?

        Die Bindung an 127.0.0.1 haelt fremde Geraete draussen, aber nicht
        fremde Namen. Wer eine Domain nach kurzer TTL auf 127.0.0.1 umbiegt
        (DNS-Rebinding), ist fuer den Browser same-origin mit sich selbst und
        darf die Antwort lesen — die Seite mit den echten Werten ebenso wie
        die PDFs unter /pdf/. Der Browser schickt dabei den aufgerufenen
        Namen im Host-Header mit, nicht unsere Adresse.

        Token und Origin decken nur die Schreibseite ab; diese Pruefung gilt
        darum fuer JEDE Route, auch die lesenden.
        """
        host = (self.headers.get("Host") or "").strip()
        return host in self._erlaubte_adressen()

    def _berechtigt(self) -> bool:
        """Token und Origin pruefen — Localhost allein schuetzt nicht."""
        if self.headers.get("X-Steuer-Token") != self.zustand.token:
            return False
        origin = self.headers.get("Origin")
        if origin and origin not in tuple(f"http://{a}" for a in self._erlaubte_adressen()):
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
            daten = json.loads(self.rfile.read(laenge).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        # Der Rueckgabetyp verspricht ein Objekt, JSON liefert aber auch
        # Listen und Zahlen. Ohne diese Pruefung wirft das .get() des
        # Aufrufers einen AttributeError im Handler-Thread, und der Browser
        # sieht eine abgebrochene Verbindung statt der gemeinten 400.
        if not isinstance(daten, dict):
            return None
        return daten

    # --- Routen ------------------------------------------------------------

    def do_GET(self) -> None:
        if not self._host_stimmt():
            self._antwort(421, b"falscher Host", "text/plain; charset=utf-8")
            return
        pfad = self.path.split("?", 1)[0]
        if pfad in ("/", "/index.html", "/review.html"):
            with self.zustand.sperre:
                seite = self.zustand.seite or self.zustand.baue_seite()
            self._antwort(200, seite.encode("utf-8"), "text/html; charset=utf-8")
            return
        if pfad == "/api/status":
            # Ueber dieselbe Zusammenfuehrung zaehlen, aus der die Seite
            # gebaut wird. Vorher zaehlte der Server nur seine eigene Ablage
            # und meldete damit weniger, als die Seite zeigte (260923-dua).
            from extractors.korrekturen import lade_alle
            try:
                eintraege = len(lade_alle(self.zustand.samples))
            except OSError:
                eintraege = -1
            self._json(200, {"gespeichert": eintraege,
                             "stand": self.zustand.stand})
            return
        if pfad in ("/uebertragung", "/tabelle"):
            # Die reine Lese-Ansicht fuer einen eigenen Tab.
            from scripts.build_review_html import sammle_zeilen, tabelle_seite
            with self.zustand.sperre:
                zeilen = sammle_zeilen(self.zustand.samples, mit_crops=False)
                seite = tabelle_seite(zeilen, self.zustand.stand,
                                      korpus=str(self.zustand.samples.resolve()))
            self._antwort(200, seite.encode("utf-8"),
                          "text/html; charset=utf-8")
            return
        if pfad == "/api/uebertragung":
            # Der Schlusscheck gehoert mit — sonst zeigt er nach einer
            # Korrektur noch den Befund von vorhin.
            from scripts.build_review_html import (
                konten_html, sammle_zeilen, schlusscheck_html, tabelle_html,
            )
            with self.zustand.sperre:
                zeilen = sammle_zeilen(self.zustand.samples, mit_crops=False)
                teile = {"check": schlusscheck_html(zeilen),
                         "ziffer": tabelle_html(zeilen),
                         "konto": konten_html(zeilen)}
            # Alle drei Teile auf einmal — sonst zeigt eine Sicht noch den
            # Stand von vorhin, sobald man umschaltet.
            self._json(200, teile)
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
        if not self._host_stimmt():
            self._json(421, {"fehler": "falscher Host"})
            return
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
                try:
                    bericht = uebernehme(zeilen, self.zustand.korrekturen,
                                         ersetzen=True)
                except SchutzVerletzt as fehler:
                    # Nicht geschrieben — die Datei auf der Platte ist
                    # unveraendert. Der Mensch soll das erfahren, statt es
                    # spaeter in einer kuerzeren Tabelle zu entdecken.
                    namen = [s.rsplit("|", 2)[1] if s.count("|") >= 2 else s
                             for s in fehler.verloren[:5]]
                    self._json(409, {
                        "fehler": "Nicht gespeichert — der Vorgang haette "
                                  f"{len(fehler.verloren)} bestaetigte "
                                  "Position(en) geloescht. Deine Datei ist "
                                  "unveraendert.",
                        "verloren": namen})
                    return
                # Und sofort in die Ablage nachziehen.
                #
                # Sonst landet die Aenderung nur in `korrekturen.json`, und
                # die Tabelle — die inzwischen die Ablage liest — bekommt sie
                # nie zu sehen. Der Mensch aendert etwas, und nichts geschieht
                # (260924-dua).
                from extractors.ablage import uebernimm_durchsicht
                # Nur die Zeilen dieser Speicherung — nicht die ganze alte
                # Durchsicht. Sonst ueberschreibt ein ueberholter Eintrag eine
                # frischere Aenderung in der Ablage (260924-dua).
                betroffen = {(str(z.get("beleg") or ""),
                              str(z.get("zielwert") or ""),
                              int(str(z.get("position") or 1) or 1))
                             for z in zeilen}
                nachgezogen = uebernimm_durchsicht(self.zustand.samples,
                                                   nur=betroffen)
                self.zustand.stand += 1
            self._json(200, {"ok": True, "gespeichert": bericht["gesamt"],
                             "neu": bericht["neu"],
                             "geaendert": bericht["geaendert"],
                             "ablage": nachgezogen})
            return

        if pfad == "/api/umbenennen":
            # Originalbelege sprechend benennen. Ohne PDF-Ordner gibt es
            # nichts umzubenennen — und ohne ihn waere jeder Pfad geraten.
            if self.zustand.pdf_dir is None:
                self._json(400, {"fehler": "Kein Ordner mit den Originalen"})
                return
            beleg = koerper.get("beleg")
            if beleg is not None and not isinstance(beleg, str):
                self._json(400, {"fehler": "beleg muss ein Name sein"})
                return
            from scripts.umbenennen import fuehre_aus, plane
            with self.zustand.sperre:
                try:
                    plan = plane(self.zustand.samples, self.zustand.pdf_dir)
                    getan = fuehre_aus(self.zustand.samples,
                                       self.zustand.pdf_dir, plan, nur=beleg)
                except OSError:
                    self._json(500, {"fehler": "Umbenennen fehlgeschlagen"})
                    return
                if getan:
                    self.zustand.stand += 1
                    self.zustand.baue_seite()
            # Nur die Anzahl zurueck — die Namen stehen auf der Seite, im
            # Netzverkehr haben sie nichts verloren.
            self._json(200, {"ok": True, "anzahl": len(getan)})
            return

        if pfad == "/api/aussortieren":
            if self.zustand.pdf_dir is None:
                self._json(400, {"fehler": "Kein Ordner mit den Originalen"})
                return
            name = koerper.get("beleg")
            if name is not None and not isinstance(name, str):
                self._json(400, {"fehler": "beleg muss ein Name sein"})
                return
            from scripts.aussortieren import fuehre_aus, plane
            with self.zustand.sperre:
                try:
                    plan = plane(self.zustand.samples, self.zustand.pdf_dir)
                    getan = fuehre_aus(self.zustand.samples,
                                       self.zustand.pdf_dir, plan, nur=name)
                except OSError:
                    self._json(500, {"fehler": "Aussortieren fehlgeschlagen"})
                    return
                if getan:
                    self.zustand.stand += 1
                    self.zustand.baue_seite()
            self._json(200, {"ok": True, "anzahl": len(getan)})
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
    # ``dauerhaft``: dieser eine Punkt wird nie weggeraeumt. Vorher drueckten
    # ihn die eigenen Auto-Saves nach zwanzig Tastendruecken aus dem
    # Ringpuffer — die Zusage „zurueck auf den Stand von vorher" war damit
    # genau dann nichts mehr wert, wenn man sie brauchte (260923-dua).
    from scripts.apply_korrekturen import _beide_ablagen, _sichere
    from extractors.korrekturen import lade_alle
    vorher = len(lade_alle(samples))
    for ablage in _beide_ablagen(zustand.korrekturen):
        if ablage.exists():
            _sichere(ablage, dauerhaft=True)

    # Reste frueherer Umbenennungen einsammeln.
    #
    # Der Browser haelt seinen eigenen Speicher und schrieb selbst erfasste
    # Positionen nach einer Umbenennung weiter unter dem ALTEN Dateinamen
    # zurueck — daraus entstand ein zweites Dokument, das es im Ordner nicht
    # gibt. Das Journal weiss, was wohin gehoert (260923-dua).
    from scripts.umbenennen import journal_anwenden
    umgehaengt = journal_anwenden(samples, pdf_dir)
    if umgehaengt:
        print(f"  ↻ {umgehaengt} Eintrag/Eintraege einer frueheren "
              f"Umbenennung nachgezogen")

    print("Baue die Seite …")
    zustand.baue_seite()
    sperre_setzen(samples)

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
        sperre_loesen(samples)
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
