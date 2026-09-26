#!/usr/bin/env python3
"""Die Tabelle als Ablage — geprüfte Werte sind gespeichert, nicht hergeleitet.

Bisher war die Übertragungstabelle ein **Nebenprodukt jedes Laufs**: die
Extraktion lieferte Zeilen, die Durchsicht lag getrennt daneben, und beim
Tabellenbau wurden beide über einen Schlüssel aus Dokumentname, Zielwert und
Positionsnummer zusammengefügt. Alle drei können sich verschieben — durch eine
Umbenennung, eine neue Position, ein Dokument, das aus dem Ordner wandert.
Verschiebt sich einer, findet die geprüfte Zahl ihre Zeile nicht mehr: sie
fehlt, oder die Extraktionszeile steht zusätzlich daneben. Im Code stehen 244
markierte Vorfälle dieser Art.

Hier ist es umgedreht. Jede Position bekommt **einmal** eine Kennung, die nie
neu berechnet wird. Dokumentname und Zielwert sind dann nur noch Inhalt, keine
Identität mehr. Eine geprüfte Position wird wörtlich gelesen — die Extraktion
wird für sie gar nicht mehr befragt.

Und die beiden Regeln, um die es geht, erzwingt die Datenbank selbst, nicht die
Sorgfalt des Programms darüber:

* **Geprüft ist fix.** Auf eine Zeile mit ``status='geprueft'`` sind Änderungen
  an den Wertfeldern und das Löschen verboten. Ein Lauf kann sie nicht
  anfassen, auch wenn sein Code fehlerhaft ist.
* **Vollständig ist geschlossen.** Ist eine Gruppe (Dokument + Zielwert) als
  vollständig markiert, werden weitere Positionen darin abgewiesen. Zwei
  Kinder, zwei Werte — und nichts kommt dazu.

Wer etwas ändern will, muss die Zeile ausdrücklich freigeben. Das ist ein
eigener Schritt, er steht im Protokoll, und er kommt vom Menschen. Dasselbe
Vorgehen wie bei einer abgeschlossenen Buchungsperiode.
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

# Felder, die den eigentlichen Wert tragen. Nur sie sind gesperrt — an
# Anzeigedetails darf sich etwas ändern, ohne dass eine Freigabe nötig wird.
WERTFELDER = ("zielwert", "pos", "ziffer", "person",
              "aussteller", "betrag", "jahr", "unterscheidung")

SCHEMA = """
CREATE TABLE IF NOT EXISTS position (
    id            TEXT PRIMARY KEY,
    dokument      TEXT NOT NULL,
    -- Der Fingerabdruck des PDF-Inhalts: die Identitaet des Dokuments.
    --
    -- Der Dateiname taugt dafuer nicht. Er aendert sich bei jeder sprechenden
    -- Umbenennung, und dann zeigt die Ablage auf den alten, die Extraktion
    -- liefert den neuen — dasselbe Dokument gilt als zwei, und jeder Wert
    -- steht doppelt in der Tabelle. Genau das ist passiert (260924-dua).
    --
    -- Dieselbe Lehre wie bei den Positionen, nur eine Ebene hoeher: Identitaet
    -- gehoert nicht an etwas, das sich aendern kann.
    dokument_id   TEXT NOT NULL DEFAULT '',
    zielwert      TEXT NOT NULL,
    pos           INTEGER NOT NULL DEFAULT 1,
    ziffer        TEXT NOT NULL DEFAULT '',
    person        TEXT NOT NULL DEFAULT '',
    aussteller    TEXT NOT NULL DEFAULT '',
    betrag        TEXT NOT NULL DEFAULT '',
    jahr          TEXT NOT NULL DEFAULT '',
    unterscheidung TEXT NOT NULL DEFAULT '',
    anker         TEXT NOT NULL DEFAULT '',
    herkunft      TEXT NOT NULL DEFAULT 'modell',
    status        TEXT NOT NULL DEFAULT 'vorschlag'
                  CHECK (status IN ('vorschlag', 'geprueft')),
    geaendert_am  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- KEIN Eindeutigkeits-Index auf (dokument, zielwert, pos).
--
-- Er waere die naheliegende Absicherung gegen Vervielfachung — und wurde
-- versuchsweise gesetzt. Folge: die bestehende Ablage liess sich nicht mehr
-- OEFFNEN, weil sie solche Paare bereits enthaelt. Ein Schutz, der die Daten
-- unzugaenglich macht, ist kein Schutz (260923-dua, Code-Review).
--
-- Die Doppelten sind ein echter Befund: zwei Positionen desselben Dokuments
-- tragen dieselbe Nummer, weshalb sich eine Hypothek nicht mehr von der
-- anderen unterscheiden liess. Gemeldet wird das von `ablage_stand.py`;
-- bereinigt gehoert es mit Bedacht, nicht durch einen Index, der die Tuer
-- zuschlaegt.

CREATE TABLE IF NOT EXISTS gruppe_geschlossen (
    dokument  TEXT NOT NULL,
    zielwert  TEXT NOT NULL,
    seit      TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (dokument, zielwert)
);

CREATE TABLE IF NOT EXISTS protokoll (
    lfd       INTEGER PRIMARY KEY AUTOINCREMENT,
    zeitpunkt TEXT NOT NULL DEFAULT (datetime('now')),
    id        TEXT,
    was       TEXT NOT NULL,
    feld      TEXT NOT NULL DEFAULT '',
    vorher    TEXT NOT NULL DEFAULT '',
    nachher   TEXT NOT NULL DEFAULT ''
);

-- Geprueft ist fix: kein Aendern der Wertfelder.
--
-- Der Statuswechsel selbst bleibt erlaubt — so laesst sich eine Zeile
-- freigeben. Beides zugleich geht nicht: erst freigeben, dann aendern. Damit
-- ist jede Aenderung an einem geprueften Wert ein ausdruecklicher, eigener
-- Schritt.
DROP TRIGGER IF EXISTS geprueft_ist_fix;
DROP TRIGGER IF EXISTS geprueft_bleibt;
DROP TRIGGER IF EXISTS gruppe_ist_zu;

-- Der Dokumentname fehlt in dieser Liste mit Absicht.
--
-- Er ist ein Verweis auf die Quelle, kein Wert. Wird ein Beleg sprechend
-- umbenannt, muss der Verweis nachziehen koennen — sonst waere die Ablage nach
-- der ersten Umbenennung von ihren Dokumenten getrennt, und genau diese
-- Trennung ist der Fehler, den sie beheben soll. Der Betrag, die Person, die
-- Ziffer: die sind gesperrt.
CREATE TRIGGER geprueft_ist_fix
BEFORE UPDATE ON position
WHEN old.status = 'geprueft' AND new.status = 'geprueft'
 AND (new.zielwert   IS NOT old.zielwert
   OR new.pos        IS NOT old.pos
   OR new.ziffer     IS NOT old.ziffer
   OR new.person     IS NOT old.person
   OR new.aussteller IS NOT old.aussteller
   OR new.betrag     IS NOT old.betrag
   OR new.jahr       IS NOT old.jahr
   OR new.unterscheidung IS NOT old.unterscheidung)
BEGIN
    SELECT RAISE(ABORT, 'geprueft: erst freigeben, dann aendern');
END;

CREATE TRIGGER geprueft_bleibt
BEFORE DELETE ON position
WHEN old.status = 'geprueft'
BEGIN
    SELECT RAISE(ABORT, 'geprueft: wird nicht geloescht');
END;

-- Vollstaendig ist geschlossen: keine weitere Position in dieser Gruppe.
CREATE TRIGGER gruppe_ist_zu
BEFORE INSERT ON position
WHEN EXISTS (SELECT 1 FROM gruppe_geschlossen g
             WHERE g.dokument = new.dokument AND g.zielwert = new.zielwert)
BEGIN
    SELECT RAISE(ABORT, 'Gruppe ist vollstaendig: keine weitere Position');
END;
"""


class Gesperrt(Exception):
    """Die Ablage hat einen Schreibvorgang abgewiesen."""


def oeffne(pfad: Path) -> sqlite3.Connection:
    """Verbindung zur Ablage; legt sie an, wenn es sie noch nicht gibt."""
    pfad.parent.mkdir(parents=True, exist_ok=True)
    verbindung = sqlite3.connect(pfad)
    verbindung.row_factory = sqlite3.Row
    verbindung.executescript(SCHEMA)
    # Eine Ablage, die vor dieser Spalte entstand, bekommt sie nachtraeglich.
    # `CREATE TABLE IF NOT EXISTS` ergaenzt keine Spalten.
    spalten = {r["name"] for r in verbindung.execute(
        "PRAGMA table_info(position)")}
    if "dokument_id" not in spalten:
        verbindung.execute(
            "ALTER TABLE position ADD COLUMN dokument_id TEXT NOT NULL "
            "DEFAULT ''")
    verbindung.commit()
    return verbindung


def naechste_id(verbindung: sqlite3.Connection) -> str:
    """Die nächste freie Kennung — fortlaufend, nie wiederverwendet.

    Sortiert wird über die ZAHL, nicht über den Text: mit vierstelliger
    Auffüllung stimmt beides bis 9999, danach stünde ``p10000`` in der
    Textsortierung vor ``p9999`` und die nächste Kennung wäre bereits
    vergeben (260923-dua, Code-Review).
    """
    zeile = verbindung.execute(
        "SELECT MAX(CAST(substr(id, 2) AS INTEGER)) AS n FROM position"
    ).fetchone()
    n = (zeile["n"] or 0) + 1
    return f"p{n:04d}"


def pfad_fuer(samples_dir: Path) -> Path:
    """Wo die Ablage zu einem Sample-/Output-Ordner liegt."""
    return samples_dir.parent / "positionen.db"


_ANKER = re.compile(
    r"^S\.(\d+)\s*\(([\d.]+),\s*([\d.]+),\s*([\d.]+),\s*([\d.]+)\)")


def anker_zerlegen(anker) -> tuple[int, tuple | None]:
    """``"S.2 (71.0, 305.4, 120.0, 314.9)"`` → ``(2, (71.0, 305.4, …))``.

    Ohne brauchbaren Anker: Seite 1 und keine Box. Seite 1 ist kein Rateversuch,
    sondern die Voraussetzung dafür, dass die Oberfläche überhaupt ein
    Seitenbild zeigen kann — nur dann lässt sich der richtige Wert anklicken.
    Ohne Seite blieb der Knopf „im Dokument wählen" für jede geprüfte Position
    unsichtbar, obwohl es ihn längst gibt (260924-dua).
    """
    treffer = _ANKER.match(str(anker or "").strip())
    if not treffer:
        return 1, None
    return (int(treffer.group(1)),
            tuple(float(treffer.group(i)) for i in (2, 3, 4, 5)))


def namen_je_fingerabdruck(samples_dir: Path) -> dict[str, str]:
    """Fingerabdruck → heutiger Dateiname, aus den Word-JSONs."""
    import json as _json

    aus: dict[str, str] = {}
    for pfad in sorted(samples_dir.glob("*.json")):
        if pfad.name.startswith("_") or pfad.name == "korrekturen.json":
            continue
        try:
            doc = _json.loads(pfad.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        finger = str(doc.get("pdf_fingerabdruck") or "")
        name = str(doc.get("pdf_name") or "")
        if finger and name:
            aus[finger] = name
    return aus


def lade_geprueft(samples_dir: Path) -> list[dict]:
    """Die geprüften Positionen — in der Form, die der Tabellenbau erwartet.

    Sie werden **wörtlich** gelesen. Keine Extraktion, kein Zusammenfügen über
    einen Schlüssel, keine Aliasnamen für umbenannte Zielwerte. Was hier steht,
    steht so in der Tabelle.
    """
    pfad = pfad_fuer(samples_dir)
    if not pfad.exists():
        return []
    verbindung = oeffne(pfad)
    # Der Name von heute, nicht der von damals.
    #
    # Wurde ein Beleg seit der Uebernahme umbenannt, zeigt `dokument` auf den
    # alten Namen. Ueber den Fingerabdruck findet die Zeile ihr Dokument
    # trotzdem — und die Tabelle fuehrt es nicht mehr als zweites (260924-dua).
    heutige = namen_je_fingerabdruck(samples_dir)
    aus = []
    for r in verbindung.execute(
            "SELECT * FROM position WHERE status='geprueft' "
            "ORDER BY dokument, zielwert, pos"):
        seite, kasten = anker_zerlegen(r["anker"])
        dokument = heutige.get(str(r["dokument_id"] or ""), r["dokument"])
        aus.append({
            "ablage_id": r["id"],
            "pdf_name": dokument, "beleg": dokument,
            "beschreibung": r["zielwert"], "zielwert": r["zielwert"],
            "pos": r["pos"], "ziffer": r["ziffer"], "person": r["person"],
            "aussteller": r["aussteller"], "betrag": r["betrag"],
            "jahr": r["jahr"], "unterscheidung": r["unterscheidung"],
            "herkunft": r["herkunft"] or "mensch",
            "status": "auto", "field_name": "ablage",
            "page": seite, "bbox": kasten, "snippet": r["anker"],
            "plaus_errs": [], "plaus_hinweis": None,
            "manual_review_marker": None, "derived": False,
            "anchor_valid": True, "inference_source": "ablage",
        })
    verbindung.close()
    return aus


def gruppen_in_ablage(samples_dir: Path) -> set[tuple[str, str]]:
    """Welche (Dokument, Zielwert) die Ablage führt.

    Für diese Paare liefert die Ablage die Wahrheit; die Extraktion wird für
    sie gar nicht erst befragt. Das ist gröber als ein Abgleich Zeile für Zeile
    — und genau deshalb verlässlich: es braucht keine Wiedererkennung einzelner
    Positionen, die mehrfach schiefgegangen ist.
    """
    pfad = pfad_fuer(samples_dir)
    if not pfad.exists():
        return set()
    verbindung = oeffne(pfad)
    heutige = namen_je_fingerabdruck(samples_dir)
    aus = {(heutige.get(str(r["dokument_id"] or ""), r["dokument"]),
            r["zielwert"])
           for r in verbindung.execute(
               "SELECT DISTINCT dokument, dokument_id, zielwert FROM position "
               "WHERE status='geprueft'")}
    verbindung.close()
    return aus


# Felder, die der Mensch in der Oberflaeche aendern kann, und wie sie im
# Korrektur-Eintrag heissen.
# Der Aussteller steht bewusst NICHT in dieser Liste.
#
# Die Oberflaeche schickt bei jedem Speichern *alle* Zeilen, nicht nur die
# geaenderten. Die Eingrenzung auf „nur diese Zeilen" greift damit ins Leere,
# und `korrekturen.json` — wo die alte Schreibweise weiterlebt — ueberschrieb
# die vereinheitlichte in der Ablage. Dreimal hintereinander; jedes Mal fielen
# danach Hypothek und Zins auseinander, weil sie bei zwei Instituten lagen
# (260924-dua).
#
# Betrag und Person aendert der Mensch in der Oberflaeche — die gehoeren
# nachgezogen. Der Aussteller wird ausdruecklich geaendert, mit
# `make ablage-aussteller`. Dort ist er auch richtig aufgehoben: er betrifft
# alle Zeilen eines Hauses, nicht eine einzelne.
AUS_DURCHSICHT: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("betrag", ("soll", "wert_betrag")),
    ("person", ("person", "wert_person")),
)


def uebernimm_durchsicht(samples_dir: Path, nur: set | None = None) -> dict:
    """Änderungen aus der Durchsicht in die Ablage nachziehen.

    Seit die Tabelle geprüfte Werte **aus der Ablage** liest, hatte eine
    Korrektur in der Oberfläche keine Wirkung mehr: sie landete in
    ``korrekturen.json``, und die Tabelle sah dorthin nicht mehr hin. Der
    Mensch änderte etwas, und nichts geschah (260924-dua, vom Nutzer
    bemerkt).

    Das ist der Rückweg. Er gilt **nur** für das, was der Mensch ausdrücklich
    geändert hat — kein Lauf kommt hier durch, weil nur Korrektur-Einträge
    gelesen werden, die er selbst erzeugt hat. Geändert wird auf dem
    vorgesehenen Weg: freigeben, ändern, wieder sperren, in einer
    Transaktion, jeder Schritt im Protokoll.

    ``nur`` grenzt auf die Positionen ein, die gerade gespeichert wurden —
    als Menge von ``(dokument, zielwert, pos)``. **Ohne diese Grenze richtet
    der Rückweg Schaden an:** er vergliche die Ablage mit der *ganzen* alten
    Durchsicht, und dort stehen auch längst überholte Werte. Eine
    Vereinheitlichung der Schreibweise wurde so prompt wieder zurückgedreht,
    weil `korrekturen.json` noch den alten Namen führte (260924-dua, vom
    Nutzer bemerkt).

    Ohne ``nur`` passiert deshalb nichts. Wer alles nachziehen will, sagt es
    ausdrücklich mit ``nur=set()`` — dann gilt die alte Durchsicht wieder als
    Quelle, mit allem, was daran hängt.

    Zurück kommt, was sich geändert hat — nach Feld gezählt.
    """
    if nur is None:
        return {}
    from extractors.korrekturen import finde, lade_alle

    pfad = pfad_fuer(samples_dir)
    if not pfad.exists():
        return {}
    korrekturen = lade_alle(samples_dir)
    if not korrekturen:
        return {}

    verbindung = oeffne(pfad)
    zeilen = list(verbindung.execute(
        "SELECT * FROM position WHERE status='geprueft'"))

    aenderungen: list[tuple[str, str, str, str]] = []
    for r in zeilen:
        if nur and (r["dokument"], r["zielwert"], r["pos"]) not in nur:
            continue
        eintrag = finde(korrekturen, r["dokument"], r["zielwert"], r["pos"])
        if not isinstance(eintrag, dict):
            continue
        for feld, quellen in AUS_DURCHSICHT:
            neu = next((str(eintrag[q]).strip() for q in quellen
                        if str(eintrag.get(q) or "").strip()), "")
            if not neu:
                continue
            alt = str(r[feld] or "").strip()
            if neu != alt:
                aenderungen.append((r["id"], feld, alt, neu))

    if not aenderungen:
        return {}

    try:
        with verbindung:
            for kennung, feld, alt, neu in aenderungen:
                verbindung.execute(
                    "UPDATE position SET status='vorschlag' WHERE id=?",
                    (kennung,))
                verbindung.execute(
                    f"UPDATE position SET {feld}=?, "
                    f"geaendert_am=datetime('now') WHERE id=?",
                    (neu, kennung))
                verbindung.execute(
                    "UPDATE position SET status='geprueft' WHERE id=?",
                    (kennung,))
                verbindung.execute(
                    "INSERT INTO protokoll (id, was, feld, vorher, nachher) "
                    "VALUES (?,?,?,?,?)",
                    (kennung, "aus der Durchsicht", feld, alt, neu))
    except Exception:
        verbindung.rollback()
        raise

    offen = verbindung.execute(
        "SELECT count(*) AS n FROM position WHERE status='vorschlag'"
    ).fetchone()["n"]
    if offen:
        raise RuntimeError(f"{offen} Position(en) sind freigegeben geblieben")

    zaehler: dict[str, int] = {}
    for _, feld, _, _ in aenderungen:
        zaehler[feld] = zaehler.get(feld, 0) + 1
    return zaehler
