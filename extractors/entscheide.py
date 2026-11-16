"""Entscheidungen des Menschen zu Werten der Leiste — „behalten" oder „weglassen".

Manches lässt sich nicht berechnen: ob ein bestätigter Wert, dessen PDF nicht
mehr im Ordner liegt, noch gilt; ob eine Warnung zur Kenntnis genommen ist.
Das entscheidet der Anwender in der Review (Seite „Zu entscheiden"), nicht im
Terminal (261024-jga: „wenn du etwas willst, dann eher einen Review im Webui").

* **behalten** — der Wert gilt, die Warnung ist erledigt.
* **weglassen** — der Wert kommt nicht in die Leiste. In der Ablage bleibt er
  unverändert; nichts wird gelöscht. Die Entscheidung lässt sich zurücknehmen.

Datei: ``<samples>/entscheide.json``. Vor jedem Schreiben geht eine Kopie
nach ``entscheide-verlauf/`` (Versionieren, nie still löschen).

Regeln seit 261025-jm6 (A2):

* **Eine Rücknahme ist ein Grabstein**, nie Abwesenheit (CLAUDE.md
  „Schweigen heisst nie löschen"): ``{"aktion": null, "zurueckgenommen":
  true, "vorher": …, "am": …}``. ``lade()`` übergeht Grabsteine,
  ``lade_roh()`` zeigt sie.
* **Eine defekte Datei ist laut** (``EntscheideDefekt``). Sie bleibt
  unverändert liegen, daneben eine Kopie ``entscheide.defekt-<Zeit>.json``;
  vorher galt sie als leer, und das nächste Speichern überschrieb sie.
* **Ein Schlüssel, eine Position.** Zwei gleiche Beträge im selben Dokument
  tragen ``#2``, ``#3`` — gebaut über ``korrekturen.baue_schluessel``. Für die
  erste Position ist der Schlüssel wörtlich der alte.
"""
from __future__ import annotations

import json
import os
import shutil
from datetime import datetime
from pathlib import Path

from extractors.eintraege import betrag, dok_name

AKTIONEN = ("behalten", "weglassen")


class EntscheideDefekt(RuntimeError):
    """``entscheide.json`` ist nicht lesbar oder hat eine falsche Form.

    Bewusst keine ValueError-Unterklasse: viele Aufrufer fangen
    ``(OSError, ValueError)`` und machen still weiter — genau das darf hier
    nicht passieren.
    """


def pfad(samples: Path) -> Path:
    return samples / "entscheide.json"


def _stempel() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S-%f")


def _defekt(samples: Path, roh: bytes | None, grund: str) -> EntscheideDefekt:
    """Kopie danebenlegen (nie verschieben) und den Fehler bauen.

    Verschieben hiesse: die nächste Datei ist leer, alle Entscheidungen
    still weg. Die Meldung nennt nur Dateinamen, keinen Pfad und keinen
    Inhalt.
    """
    kopie = ""
    if roh is not None:
        for k in sorted(samples.glob("entscheide.defekt-*.json")):
            try:
                if k.read_bytes() == roh:
                    kopie = k.name
                    break
            except OSError:
                continue
        if not kopie:
            ziel = samples / f"entscheide.defekt-{_stempel()}.json"
            ziel.write_bytes(roh)
            kopie = ziel.name
    return EntscheideDefekt(
        f"entscheide.json ist defekt ({grund}) — sie bleibt unverändert liegen"
        + (f"; Kopie: {kopie}" if kopie else "")
        + ". Frühere Stände liegen in entscheide-verlauf/. Datei reparieren oder "
        "einen Stand von dort zurückholen; bis dahin wird nichts gespeichert.")


def lade_roh(samples: Path) -> dict[str, dict]:
    """Alle Einträge, auch Grabsteine. Fehlt die Datei: ``{}``; ist sie
    defekt: ``EntscheideDefekt``."""
    ziel = pfad(samples)
    try:
        roh = ziel.read_bytes()
    except FileNotFoundError:
        return {}
    except OSError as e:
        raise _defekt(samples, None, f"nicht lesbar: {e.__class__.__name__}") from e
    try:
        daten = json.loads(roh.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise _defekt(samples, roh, "kein gültiges JSON") from e
    if not isinstance(daten, dict):
        raise _defekt(samples, roh, "Wurzel ist kein Objekt")
    if any(not isinstance(v, dict) for v in daten.values()):
        raise _defekt(samples, roh, "ein Eintrag ist kein Objekt")
    return daten


def lade(samples: Path) -> dict[str, dict]:
    """Die gültigen Entscheidungen — ohne Grabsteine."""
    return {k: v for k, v in lade_roh(samples).items() if v.get("aktion") in AKTIONEN}


def speichere(samples: Path, schluessel: str, aktion: str | None) -> dict:
    """Eine Entscheidung setzen (oder mit ``None`` zurücknehmen — Grabstein)."""
    if aktion is not None and aktion not in AKTIONEN:
        raise ValueError(f"unbekannte Aktion: {aktion}")
    daten = lade_roh(samples)          # wirft bei defekter Datei, vor jeder Änderung
    jetzt = datetime.now().isoformat(timespec="seconds")
    if aktion is None:
        vorher = (daten.get(schluessel) or {}).get("aktion")
        if vorher not in AKTIONEN:
            raise ValueError("nichts zurückzunehmen — dieser Schlüssel ist nicht gesetzt")
        neu = {"aktion": None, "zurueckgenommen": True, "vorher": vorher, "am": jetzt}
    else:
        neu = {"aktion": aktion, "am": jetzt}
    ziel = pfad(samples)
    if ziel.exists():
        verlauf = samples / "entscheide-verlauf"
        verlauf.mkdir(exist_ok=True)
        shutil.copy2(ziel, verlauf / f"entscheide-{_stempel()}.json")
    daten[schluessel] = neu
    # Atomar: erst ganz schreiben, dann ersetzen — ein Abbruch hinterlässt
    # nie eine halbe Datei.
    tmp = ziel.with_name(f".entscheide-{os.getpid()}-{_stempel()}.tmp")
    try:
        tmp.write_text(json.dumps(daten, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
                       encoding="utf-8")
        os.replace(tmp, ziel)
    finally:
        if tmp.exists():
            tmp.unlink()
    return daten


def _schluessel(dok: str, titel: str, wert, pos: int) -> str:
    from extractors.korrekturen import baue_schluessel

    b = betrag(wert)
    return baue_schluessel(dok_name(dok), f"{titel}|{b if b is not None else ''}", pos)


def schluessel_wert(quelle: str, bezeichnung: str, wert, pos: int = 1) -> str:
    """Ein Betrag der Leiste: Dokument | Bezeichnung | Betrag [#Position]."""
    return _schluessel(quelle, bezeichnung, wert, pos)


def schluessel_block(dokumente: list[str], titel: str, felder: list[dict],
                     pos: int = 1) -> str:
    """Ein Eintrag mit Feldern (Konto, Depot, Schuld): Dokument | Titel | Total [#Position]."""
    total = next((f.get("wert") for f in felder
                  if str(f.get("titel", "")).startswith(("Total", "Schuld am"))), "")
    return _schluessel(dokumente[0] if dokumente else "", titel, total, pos)


def zerlege_schluessel(schluessel: str) -> tuple[str, str, str, int]:
    """``"a.pdf|Zins|12.00#2"`` → ``("a.pdf", "Zins", "12.00", 2)``.

    Die Position liest ``korrekturen.zerlege`` — keine eigene #-Logik.
    """
    from extractors.korrekturen import zerlege

    dok, rest, pos = zerlege(schluessel)
    if "|" in rest:
        titel, _, wert = rest.rpartition("|")
    else:
        titel, wert = rest, ""
    return dok, titel, wert, pos


def gueltige_schluessel(daten: dict) -> set[str]:
    """Alle Schlüssel, die die Leiste gerade trägt (Blöcke und Werte)."""
    aus: set[str] = set()
    for f in daten.get("formulare") or []:
        for b in f.get("bloecke") or []:
            if b.get("schluessel"):
                aus.add(b["schluessel"])
            for w in b.get("werte") or []:
                if w.get("schluessel"):
                    aus.add(w["schluessel"])
    return aus
