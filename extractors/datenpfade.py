"""Die einzige Pfadquelle der Datenorte (Phase 5, Datenschutz).

Teil 1 (Plan 05-14) kennt die alten Orte der echten Daten (im Repo und unter
``~/Dokumente``) und die **Testlauf-Sperre**: In einer pytest-Sitzung
(``STEUER_TESTLAUF=1``) weist ein Audit-Hook jedes Öffnen, Auflisten und
SQLite-Verbinden dieser Orte ab, und die Lader für Stammdaten behandeln die
echten Dateien als nicht vorhanden (wie in CI).

Teil 2 (Plan 05-01, D-05, 2026-11-22) kennt die Pfade des verschlüsselten
Volumes. Für jedes Jahr gilt dasselbe Schema, ohne Sonderfall 2025::

    <Wurzel>/<Jahr>/<Belegordner> | ausgabe | eingereicht | arbeit
    <Wurzel>/stammdaten
    <Wurzel>/sicherungen/<Jahr>

Das Makefile und die Skripte holen ihre Pfade nur noch von hier
(``python -m extractors.datenpfade make-variablen <Jahr>``). Teil 2 legt nie
einen Ordner an und schreibt nichts; ohne eingehängtes Volume bricht
:func:`pruefe_eingehaengt` mit :data:`MELDUNG` und Exit 3 ab.

Grenzen der Sperre (offen benannt):

* Der Hook vergleicht nur Pfadtexte (``os.path.abspath`` und ``fnmatch``),
  nie ``stat``, ``exists`` oder ``realpath`` auf den gesperrten Orten. Ein
  Symlink, der aus einem erlaubten Ort auf einen gesperrten zeigt, wird nicht
  aufgelöst.
* Python-Kindprozesse laden den Hook über ``sitecustomize.py``
  (:func:`schreibe_testlauf_site`). Nicht abgedeckt sind Kindprozesse ohne
  Python (Shell-Befehle wie ``cat``, ``git``, ``make`` selbst) sowie Python mit
  ``-S`` oder ``-I``; sie laden kein ``sitecustomize``. Dort schützen nur die
  Prüfungen in den Ladern und, ab Plan 05-09, die Sandbox.

Nur Standardbibliothek, keine Importe aus ``extractors.*`` (die Datei wird auch
von ``sitecustomize.py`` über ihren Pfad geladen).
"""
from __future__ import annotations

import fnmatch
import os
import re
import sys
from pathlib import Path
from urllib.parse import unquote

#: Repo-Wurzel.
ROOT = Path(__file__).resolve().parent.parent

#: Umgebungsvariable für die Wurzel der Daten (Tests zeigen auf einen
#: Wegwerf-Pfad).
ENV = "STEUER_DATEN_WURZEL"

#: Einhängepunkt des verschlüsselten Volumes (D-01).
STANDARD = Path("/Volumes/Steuern")

#: Marke im Volume, an der sich ein echtes Volume erkennen lässt.
MARKE = ".steuer-volume"

#: Originalbelege ausserhalb des Repos (D-10).
ALT_ORIGINALE = Path.home() / "Dokumente" / "Steuern"

#: Die alten Orte im Repo, relativ zur Wurzel (Quelle der Umzugsliste).
ALTE_ORTE: dict[str, str] = {
    "belege": "belege",
    "belege_muster": "belege-{jahr}",
    "ausgabe": "output",
    "ausgabe_erstes_jahr": "output/latest",
}

#: Gesperrte Orte in Testläufen: (Glob-Muster relativ zu ``ROOT``, Kategorie).
#: Ein Muster sperrt den Ort selbst und alles darunter. Die ``.example``-
#: Dateien, ``evals/fixtures``, ``evals/expected`` und der übrige Korpus sind
#: nicht gesperrt. Dazu kommen ``ALT_ORIGINALE`` und ``STANDARD`` samt allem
#: darunter (absolut, siehe :func:`testlauf_kategorie`).
ECHTE_ORTE: list[tuple[str, str]] = [
    ("family.yaml", "Stammdaten"),
    ("family.yaml.bak*", "Stammdaten"),
    ("privacy_secrets.local.yaml", "Stammdaten"),
    ("privacy_secrets.local.yaml.bak*", "Stammdaten"),
    ("privacy_secrets.vorschlag.yaml", "Stammdaten"),
    ("privacy_secrets.vorschlag.yaml.bak*", "Stammdaten"),
    ("aussteller.json", "Aussteller-Historie"),
    ("aussteller.json.bak*", "Aussteller-Historie"),
    ("belege", "Belegordner"),
    ("belege-*", "Belegordner"),
    ("output", "Ausgabeordner"),
    ("evals/positionen.db", "Ablage"),
    ("scripts/sync_*.private.txt", "Sync-Liste"),
    ("evals/samples_real*/anon_truth_mapping.json", "Korpus-Zuordnung"),
    ("*.numbers", "Steueraufstellung"),
    ("probe_review.html", "Review-Probe"),
    ("pruefen_namen.txt", "Kuerzel-Zuordnung"),
    ("entscheide*", "Entscheide"),
]

#: Umgebungsvariable der erfundenen Familie in Testläufen (Plan 05-14).
ENV_FAMILIE = "STEUER_TESTLAUF_FAMILIE"

#: Ereignisse des Audit-Hooks, die ein Ziel tragen.
_EREIGNISSE = {
    "open", "os.listdir", "os.scandir", "glob.glob", "glob.glob/2",
    "sqlite3.connect", "shutil.copyfile", "shutil.copytree", "shutil.move",
}

# Kleinschreibung: das Dateisystem von macOS unterscheidet Gross und Klein
# nicht, ein Zugriff über abweichende Schreibung wäre sonst ein Umweg.
_ROOT_KLEIN = os.fspath(ROOT).lower()
_ECHTE_KLEIN = [(m.lower(), k) for m, k in ECHTE_ORTE]
_ABSOLUT_KLEIN = [
    (os.path.abspath(os.fspath(ALT_ORIGINALE)).lower(), "Originalbelege"),
    (os.path.abspath(os.fspath(STANDARD)).lower(), "Volume"),
]


def testlauf_aktiv() -> bool:
    """Ist die Testlauf-Sperre eingeschaltet (``STEUER_TESTLAUF=1``)?"""
    return os.environ.get("STEUER_TESTLAUF") == "1"


def _text(pfad) -> str | None:
    """Pfadtext, oder None bei Dateikennungen und Unbrauchbarem."""
    if isinstance(pfad, int) or pfad is None:
        return None
    try:
        return os.fsdecode(os.fspath(pfad))
    except (TypeError, ValueError):
        return None


def _kategorie(text: str) -> str | None:
    """Kategorie des Ortes zu einem Pfadtext, ohne Prüfung der Sitzung."""
    try:
        absolut = os.path.abspath(text).lower()
    except (TypeError, ValueError):
        return None
    for wurzel, kategorie in _ABSOLUT_KLEIN:
        if absolut == wurzel or absolut.startswith(wurzel + "/"):
            return kategorie
    if not absolut.startswith(_ROOT_KLEIN + "/"):
        return None
    teile = absolut[len(_ROOT_KLEIN) + 1:].split("/")
    # Jeder Vorfahre zählt: wer unter einem gesperrten Ordner liegt, ist gesperrt.
    for i in range(1, len(teile) + 1):
        teil = "/".join(teile[:i])
        for muster, kategorie in _ECHTE_KLEIN:
            if fnmatch.fnmatchcase(teil, muster):
                return kategorie
    return None


def testlauf_kategorie(pfad) -> str | None:
    """Kategorie des gesperrten Ortes, oder None (auch ausserhalb von Testläufen).

    Relative Pfade gelten relativ zum aktuellen Arbeitsordner. Es wird nie auf
    das Dateisystem zugegriffen.
    """
    if not testlauf_aktiv():
        return None
    text = _text(pfad)
    if text is None:
        return None
    return _kategorie(text)


def testlauf_gesperrt(pfad) -> bool:
    """Ist ``pfad`` in einem Testlauf ein echter Ort, der als nicht vorhanden gilt?"""
    return testlauf_kategorie(pfad) is not None


def familie_pfad(pfad=None) -> Path:
    """Pfad der Familien-Datei, aus der ein Programm sie lädt.

    Ohne Argument die Datei an der Repo-Wurzel, mit Argument genau dieser Pfad
    (so bleibt ein Aufruf relativ zum Arbeitsordner, was er war). In einem
    Testlauf zeigt ein gesperrter Pfad auf die erfundene Familie im
    Wegwerf-Ordner der Sitzung (``STEUER_TESTLAUF_FAMILIE``, aus
    ``evals/conftest.py``); die echte Datei wird nie angefasst.
    """
    ziel = Path(pfad) if pfad is not None else ROOT / "family.yaml"
    if testlauf_aktiv():
        erfunden = os.environ.get(ENV_FAMILIE)
        if erfunden and testlauf_gesperrt(ziel):
            return Path(erfunden)
    return ziel


def _sqlite_ziele(text: str) -> list[str]:
    """Pfadtexte zu einem ``sqlite3.connect``-Ziel (auch als URI)."""
    ziele = [text]
    if text[:5].lower() == "file:":
        rest = text[5:]
        rest = rest.split("?", 1)[0].split("#", 1)[0]
        if rest.startswith("//"):
            # file://<Rechner>/<Pfad>: den Rechner abschneiden.
            rest = rest[2:]
            rest = rest[rest.find("/"):] if "/" in rest else ""
        ziele.append(unquote(rest))
    return ziele


def _glob_praefix(muster: str, root_dir) -> str:
    """Festes Verzeichnis-Präfix eines Glob-Musters (vor dem ersten Platzhalter)."""
    positionen = [muster.find(z) for z in "*?[" if z in muster]
    if positionen:
        fest = muster[:min(positionen)]
        # Auf den letzten vollständigen Pfadteil kürzen.
        fest = fest[:fest.rfind("/") + 1]
    else:
        fest = muster
    basis = _text(root_dir) or os.getcwd()
    return os.path.join(basis, fest) if fest else basis


def _ziele(event: str, args: tuple) -> list[str]:
    if not args:
        return []
    if event in ("open", "os.listdir", "os.scandir"):
        text = _text(args[0])
        return [text] if text is not None else []
    if event in ("glob.glob", "glob.glob/2"):
        muster = _text(args[0])
        if muster is None:
            return []
        root_dir = args[2] if event == "glob.glob/2" and len(args) > 2 else None
        return [_glob_praefix(muster, root_dir)]
    if event == "sqlite3.connect":
        text = _text(args[0])
        return _sqlite_ziele(text) if text else []
    if event in ("shutil.copyfile", "shutil.copytree", "shutil.move"):
        # Quelle und Ziel: auch ein Kopieren NACH einem echten Ort ist gesperrt.
        return [t for t in (_text(a) for a in args[:2]) if t is not None]
    return []


def testlauf_audit(event: str, args: tuple) -> None:
    """Audit-Hook: weist Zugriffe auf echte Orte in Testläufen ab.

    Die Meldung nennt nur die Kategorie, nie den Pfad: Fehlermeldungen
    erscheinen in Ausgaben, die ein Agent liest.
    """
    if event not in _EREIGNISSE or not testlauf_aktiv():
        return
    try:
        ziele = _ziele(event, args)
    except Exception:  # noqa: BLE001 — ein Hook darf nie selbst scheitern
        return
    for ziel in ziele:
        kategorie = _kategorie(ziel)
        if kategorie is not None:
            raise PermissionError(f"Testlauf: echte Daten gesperrt ({kategorie})")


_SITE_VORLAGE = '''"""Von der Testsitzung erzeugt (Phase 5): Testlauf-Sperre in Kindprozessen."""
import importlib.util
import os
import sys

if os.environ.get("STEUER_TESTLAUF") == "1":
    _spec = importlib.util.spec_from_file_location("_steuer_datenpfade", {pfad!r})
    _modul = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_modul)
    sys.addaudithook(_modul.testlauf_audit)
'''


def schreibe_testlauf_site(ordner: Path) -> Path:
    """Legt in ``ordner`` ein ``sitecustomize.py`` an, das den Hook registriert.

    Der Ordner gehört vorne auf ``PYTHONPATH``; jeder Python-Kindprozess mit
    ``STEUER_TESTLAUF=1`` lädt dann diese Datei und registriert denselben Hook
    wie die Testsitzung. Verbleibende Lücke: Kindprozesse ohne Python (Shell,
    ``git``, ``make`` selbst) sowie Python mit ``-S`` oder ``-I`` laden kein
    ``sitecustomize``; dort schützen nur die Lader-Prüfungen und, ab Plan 05-09,
    die Sandbox. Liefert den Pfad der geschriebenen Datei.
    """
    ordner = Path(ordner)
    ordner.mkdir(parents=True, exist_ok=True)
    datei = ordner / "sitecustomize.py"
    datei.write_text(
        _SITE_VORLAGE.format(pfad=str(Path(__file__).resolve())),
        encoding="utf-8")
    return datei


def nur_im_eigenen_terminal(was: str) -> None:
    """Bricht ab (Exit 4), wenn ``was`` nicht in der Anwender' eigenem Terminal läuft.

    Als Claude-Sitzung (``CLAUDECODE``) oder ohne Terminal als Stdin gilt der
    Lauf als nicht eigenhändig.
    """
    try:
        tty = sys.stdin is not None and sys.stdin.isatty()
    except (ValueError, OSError):
        tty = False
    if os.environ.get("CLAUDECODE") or not tty:
        print(f"{was} läuft nur in der Anwender' eigenem Terminal (Terminal.app).",
              file=sys.stderr)
        raise SystemExit(4)


# --- Teil 2: Pfade des Volumes (Plan 05-01) --------------------------------------

#: Feste Meldung, wenn das Volume fehlt (teilbar: nennt keinen Benutzerpfad).
MELDUNG = ("Das Steuer-Volume ist nicht eingehängt (/Volumes/Steuern). "
           "Im eigenen Terminal: make volume-an")

#: Erinnerung am Ende eines echten Make-Ziels (D-03).
HINWEIS = ("Hinweis: Das Steuer-Volume ist noch eingehängt. "
           "Nach der Arbeit auswerfen: make volume-aus")

#: Zeichen, die als Make-Wort oder in Make-Variablen nicht heil bleiben.
_UNZULAESSIG = re.compile(r"[\s$#]")


class VolumeFehlt(SystemExit):
    """Das Volume ist nicht eingehängt (Exit-Code 3)."""


def wurzel() -> Path:
    """Wurzel der Daten: ``STEUER_DATEN_WURZEL``, sonst das Standard-Volume.

    Leerraum (und ``$``, ``#``) im Pfad ist nicht zulässig: das Makefile
    übernimmt die Pfade als einzelne Wörter.
    """
    roh = os.environ.get(ENV)
    pfad = Path(roh) if roh else STANDARD
    if _UNZULAESSIG.search(os.fspath(pfad)):
        raise ValueError("Die Wurzel der Daten darf keinen Leerraum, kein $ und "
                         "kein # enthalten.")
    return pfad


def ist_eingehaengt() -> bool:
    """Ist das Volume da? Mit gesetzter Umgebungsvariable genügt die Marke.

    Ohne Variable braucht es den Einhängepunkt UND die Marke: ein fremdes
    Volume gleichen Namens gilt nicht (T-05-03). Nur lesende Prüfungen.
    """
    basis = wurzel()
    if not os.environ.get(ENV) and not os.path.ismount(STANDARD):
        return False
    return os.path.isfile(basis / MARKE)


def pruefe_eingehaengt() -> Path:
    """Gibt die Wurzel zurück oder bricht mit :data:`MELDUNG` und Exit 3 ab."""
    if not ist_eingehaengt():
        print(MELDUNG, file=sys.stderr)
        raise VolumeFehlt(3)
    return wurzel()


def _jahr(jahr) -> int:
    """Vierstelliges Jahr als ``int``, sonst ``ValueError``."""
    wert = int(jahr)
    if not 1000 <= wert <= 9999:
        raise ValueError("Das Steuerjahr muss vierstellig sein.")
    return wert


def jahr_ordner(jahr) -> Path:
    return wurzel() / str(_jahr(jahr))


def belege(jahr) -> Path:
    return jahr_ordner(jahr) / "belege"


def ausgabe(jahr) -> Path:
    return jahr_ordner(jahr) / "ausgabe"


def eingereicht(jahr) -> Path:
    return jahr_ordner(jahr) / "eingereicht"


def arbeit(jahr) -> Path:
    """Nebenprodukte eines Jahres (nie gesperrt, nie abgeschlossen)."""
    return jahr_ordner(jahr) / "arbeit"


def stammdaten() -> Path:
    return wurzel() / "stammdaten"


def stammdatei(name) -> Path | None:
    """Pfad einer Stammdatei, oder ``None`` ohne eingehängtes Volume.

    ``name`` ist relativ und ohne ``..``, sonst ``ValueError``.
    """
    roh = Path(os.fspath(name))
    if not roh.parts or roh.is_absolute() or ".." in roh.parts:
        raise ValueError("Der Name einer Stammdatei ist relativ und ohne ..")
    if not ist_eingehaengt():
        return None
    return stammdaten() / roh


def sicherungen(jahr) -> Path:
    return wurzel() / "sicherungen" / str(_jahr(jahr))


def originale() -> Path:
    """Ort der umgezogenen Originalbelege (früher ``~/Dokumente/Steuern``, D-10)."""
    return wurzel() / "originale"


def protokoll() -> Path:
    """Protokoll des Umzugs (angehängt, nur Zahlen)."""
    return stammdaten() / "umzug-protokoll.jsonl"


def jahr_von(pfad) -> int | None:
    """Steuerjahr eines Pfades unter der Wurzel, sonst ``None``.

    Nur Pfadtext (``os.path.abspath``); Symlinks werden nicht aufgelöst.
    """
    text = os.path.abspath(os.fspath(pfad))
    basis = os.path.abspath(os.fspath(wurzel())).rstrip("/")
    if not text.startswith(basis + "/"):
        return None
    erster = text[len(basis) + 1:].split("/", 1)[0]
    if len(erster) == 4 and erster.isascii() and erster.isdigit():
        return int(erster)
    return None


def arbeit_fuer(samples) -> Path:
    """Arbeitsordner zu einem Ausgabe- oder ``json``-Ordner.

    Unter einem Jahr des Volumes ``arbeit(jahr)``, sonst der bisherige Ort:
    der Elternordner bei einem Ordner namens ``json``, sonst der Ordner selbst.
    """
    samples = Path(samples)
    jahr = jahr_von(samples)
    if jahr is not None:
        return arbeit(jahr)
    return samples.parent if samples.name == "json" else samples


def sicherungen_fuer(ausgabe_ordner) -> Path:
    """Sicherungsordner zu einem Ausgabeordner (mit Jahr: auf dem Volume)."""
    ausgabe_ordner = Path(ausgabe_ordner)
    jahr = jahr_von(ausgabe_ordner)
    if jahr is not None:
        return sicherungen(jahr)
    return ausgabe_ordner / "sicherungen"


_ZUORDNUNG = "anon_truth_mapping.json"


def _zuordnung_volume(korpus: Path) -> Path:
    return stammdaten() / "korpus" / korpus.name / _ZUORDNUNG


def _vorhanden(pfad: Path) -> bool:
    """Existiert die Datei? Gesperrte echte Orte gelten im Testlauf als nicht da."""
    return not testlauf_gesperrt(pfad) and os.path.isfile(pfad)


def korpus_zuordnung(korpus) -> Path:
    """Ort der Zuordnungsdatei zum Lesen (D-21).

    Das Volume, wenn es eingehängt ist und die Datei dort liegt, sonst der Ort
    im Korpus (die Datei muss dort nicht existieren).
    """
    korpus = Path(korpus)
    if ist_eingehaengt() and os.path.isfile(_zuordnung_volume(korpus)):
        return _zuordnung_volume(korpus)
    return korpus / _ZUORDNUNG


def korpus_zuordnung_ziel(korpus) -> Path:
    """Ort der Zuordnungsdatei zum Schreiben (D-21).

    Auf dem Volume, wenn es eingehängt ist und die Datei dort liegt oder noch
    nirgends; liegt sie nur im Korpus (oder ist das Volume nicht da), bleibt
    der Korpus-Ort.
    """
    korpus = Path(korpus)
    alt = korpus / _ZUORDNUNG
    if not ist_eingehaengt():
        return alt
    neu = _zuordnung_volume(korpus)
    if os.path.isfile(neu) or not _vorhanden(alt):
        return neu
    return alt


def make_variablen(jahr) -> str:
    """Eine Zeile ``NAME?=wert`` je Make-Variable (mit ``?=``: Aufrufer gewinnt)."""
    felder = [
        ("DATEN", wurzel()),
        ("LATEST", ausgabe(jahr)),
        ("PDFDIR", belege(jahr)),
        ("VORJAHR_AUSGABE", ausgabe(_jahr(jahr) - 1)),
        ("ARBEIT", arbeit(jahr)),
        ("EINGEREICHT", eingereicht(jahr)),
    ]
    return " ".join(f"{name}?={wert}" for name, wert in felder)


def main(argv: list[str] | None = None) -> int:
    """Kommandozeile für das Makefile (siehe Modul-Docstring)."""
    args = list(sys.argv[1:] if argv is None else argv)
    ziele = {"ausgabe": ausgabe, "belege": belege, "arbeit": arbeit,
             "eingereicht": eingereicht}
    try:
        if args == ["wurzel"]:
            print(wurzel())
        elif len(args) == 2 and args[0] == "make-variablen":
            print(make_variablen(args[1]))
        elif args == ["pruefen"]:
            pruefe_eingehaengt()
        elif args == ["hinweis"]:
            if ist_eingehaengt():
                print(HINWEIS, file=sys.stderr)
        elif len(args) == 2 and args[0] in ziele:
            print(ziele[args[0]](args[1]))
        else:
            print("Aufruf: python -m extractors.datenpfade wurzel | "
                  "make-variablen <Jahr> | pruefen | hinweis | "
                  "ausgabe|belege|arbeit|eingereicht <Jahr>", file=sys.stderr)
            return 2
    except ValueError as fehler:
        print(f"Datenpfade: {fehler}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
