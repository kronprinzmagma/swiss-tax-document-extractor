"""Vorjahresvergleich je Person und Objekt — was fehlt, was ist neu, was anders.

der Anwender (2026-10-28): „Jetzt ist klar, was jede Person für Arbeitgeber,
Krankenkassen, Konten etc. hat, und bei künftigen Läufen sollte das beachtet
werden und ggf. auch Hinweise geben, dass etwas fehlt oder anders ist."

Bis dahin gab es nur ``aussteller.json``: Aussteller + Belegart + letztes
Jahr — ohne Person, ohne Konto, ohne Hypothek. Ein fehlender Lohnausweis von
Person 2 fiel nicht auf, solange irgendein Lohnausweis da war.

**Profil** = die wiederkehrenden Posten eines abgeschlossenen Jahres, gelesen
aus der Leiste (``export_extension.baue``) — also aus dem, was tatsächlich in
die Erklärung ging, nicht aus einem Zwischenschritt. Ein Posten ist:

* Lohnausweis, Säule 3a, Krankenkasse, Kita: (Formular, Person, Aussteller)
* Konto, Depot, Schuld: (Formular, Person, Kennung) — Kennung ist die
  Konto-/Depot- bzw. Hypothekennummer; ohne Kennung der Aussteller.

Krankheitskosten (Hilfsblatt 320) und die Familien-Totale wechseln jedes Jahr
und sind kein Posten.

**Abgleich** des laufenden Jahres gegen das Profil des Vorjahres:

* ``fehlt`` — im Vorjahr da, jetzt nicht. Blockiert ``make pruefen``, bis
  der Beleg da ist oder ``make vorjahr-quittieren`` sagt, warum nicht
  (Stelle gewechselt, Konto saldiert). Quittungen werden angehängt, nie
  gelöscht.
* ``neu`` — jetzt da, im Vorjahr nicht. Hinweis.
* ``anders`` — derselbe Posten, aber ein Betrag weicht stark ab oder der
  Zinssatz hat sich geändert. Hinweis.

Rein: I/O nur in :func:`lade_profil` / :func:`schreibe_profil` /
:func:`lade_quittungen` / :func:`quittiere`.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

PROFIL = "profil.json"
QUITTUNGEN = "vorjahr_quittungen.json"
VERLAUF = "profil-verlauf"

#: Formulare mit wiederkehrenden Posten. Hilfsblatt 320 und „Übrige" nicht.
FORMULARE = ("Lohnausweis (100)", "Wertschriftenverzeichnis (400)", "Schuldenverzeichnis",
             "Versicherungsprämien", "Bescheinigung der Vorsorgeeinrichtung",
             "Aufstellung fremdbetreute Kinder")

# Grenzen — nicht im Datenmodell; Vorschlag an der Anwender.
ABWEICHUNG_HOECHSTENS = Decimal("0.5")   # ± 50 % gegenüber dem Vorjahr
ABWEICHUNG_AB_FRANKEN = Decimal("500")   # kleinere Beträge: kein Hinweis

_ZINSSATZ = "Zinssatz"


@dataclass
class Posten:
    formular: str
    art: str
    person: str
    aussteller: str
    kennung: str
    werte: dict[str, str] = field(default_factory=dict)

    @property
    def schluessel(self) -> str:
        """Identität über die Jahre: Kennung vor Ausstellername."""
        # Ohne Nummer: Aussteller plus Bezeichnung („Privatkonto") — sonst
        # fielen zwei Konten derselben Person bei derselben Bank zusammen.
        wer = _ziffern(self.kennung) or f"{_kern(self.aussteller)}/{_kern(self.kennung)}"
        return "|".join((self.formular, self.art, self.person, wer))

    @property
    def kurz(self) -> str:
        """Teilbares Kürzel — ein Hash, kein Name, keine Nummer."""
        return "V-" + hashlib.sha256(self.schluessel.encode()).hexdigest()[:6]

    def als_dict(self) -> dict:
        return {"formular": self.formular, "art": self.art, "person": self.person,
                "aussteller": self.aussteller, "kennung": self.kennung,
                "werte": dict(self.werte)}

    @classmethod
    def aus_dict(cls, d: dict) -> "Posten":
        return cls(str(d.get("formular", "")), str(d.get("art", "")),
                   str(d.get("person", "")), str(d.get("aussteller", "")),
                   str(d.get("kennung", "")), dict(d.get("werte") or {}))


@dataclass
class Befund:
    art: str          # fehlt | neu | anders
    posten: Posten
    was: str = ""
    quittung: str = ""


def _kern(name: str) -> str:
    return re.sub(r"[^a-z]", "", str(name or "").lower())[:8]


def _ziffern(text: str) -> str:
    z = re.sub(r"\D", "", str(text or ""))
    return z if len(z) >= 4 else ""


def _betrag(text) -> Decimal | None:
    t = str(text or "").replace("'", "").replace("’", "").replace(" ", "")
    t = t.replace("%", "").replace(",", ".")
    try:
        return Decimal(t) if t else None
    except InvalidOperation:
        return None


def _feld(block: dict, anfang: str) -> str:
    f = next((x for x in block.get("felder") or [] if str(x.get("titel", "")).startswith(anfang)),
             None)
    return str((f or {}).get("wert") or "")


def posten_der_leiste(daten: dict) -> list[Posten]:
    """Die wiederkehrenden Posten aus den Leistendaten eines Jahres."""
    aus: list[Posten] = []
    for f in daten.get("formulare") or []:
        form = f.get("formular", "")
        if form not in FORMULARE:
            continue
        for b in f.get("bloecke") or []:
            person = str(b.get("person") or "")
            if person in ("alle Personen", ""):
                continue  # Familien-Total, kein Posten
            if b.get("felder"):
                art = str(b.get("maske") or "")
                if form == "Schuldenverzeichnis":
                    aussteller = _feld(b, "Name, Vorname").split(",")[0]
                    # Die Hypothekennummer steht im Titel („Bank · Art · Nr.").
                    kennung = str(b.get("titel", "")).split("·")[-1].strip()
                else:
                    aussteller = _feld(b, "Bank")
                    kennung = _feld(b, "Konto-/Depot")
                    if not _ziffern(kennung):
                        kennung = _feld(b, "Bezeichnung") or kennung
                werte = {x["titel"]: str(x.get("wert") or "") for x in b["felder"]
                         if x.get("wert") and (_betrag(x.get("wert")) is not None)
                         and not str(x["titel"]).startswith(("Konto-/Depot", "Staat"))}
            else:
                art = form
                aussteller = str(b.get("aussteller") or "")
                kennung = ""
                werte = {}
                for w in b.get("werte") or []:
                    if w.get("betrag"):
                        bez = str(w.get("bezeichnung") or "")
                        werte[bez] = str(w["betrag"])
            aus.append(Posten(form, art, person, aussteller, kennung, werte))
    # Derselbe Posten zweimal (zwei Belege desselben Kontos): zusammenlegen.
    je: dict[str, Posten] = {}
    for p in aus:
        if p.schluessel in je:
            je[p.schluessel].werte.update({k: v for k, v in p.werte.items()
                                           if k not in je[p.schluessel].werte})
        else:
            je[p.schluessel] = p
    return list(je.values())


def vergleiche(vorjahr: list[Posten], jetzt: list[Posten],
               quittungen: dict[str, dict] | None = None) -> list[Befund]:
    quittungen = quittungen or {}
    alt = {p.schluessel: p for p in vorjahr}
    neu = {p.schluessel: p for p in jetzt}
    befunde: list[Befund] = []
    for s, p in alt.items():
        if s not in neu:
            q = quittungen.get(p.kurz) or {}
            befunde.append(Befund("fehlt", p, "im Vorjahr da, jetzt nicht",
                                  str(q.get("grund") or "")))
    for s, p in neu.items():
        if s not in alt:
            befunde.append(Befund("neu", p, "im Vorjahr nicht da"))
            continue
        for titel, wert in p.werte.items():
            vorher = alt[s].werte.get(titel)
            if vorher is None:
                continue
            a, b = _betrag(vorher), _betrag(wert)
            if a is None or b is None or a == b:
                continue
            if titel.startswith(_ZINSSATZ):
                befunde.append(Befund("anders", p, f"{titel}: geändert"))
            elif max(abs(a), abs(b)) >= ABWEICHUNG_AB_FRANKEN and (
                    a == 0 or abs(b - a) / abs(a) > ABWEICHUNG_HOECHSTENS):
                richtung = "höher" if b > a else "tiefer"
                befunde.append(Befund("anders", p, f"{titel}: mehr als "
                                      f"{int(ABWEICHUNG_HOECHSTENS * 100)} % {richtung}"))
    return befunde


def offen(befunde: list[Befund]) -> list[Befund]:
    """Was ``make pruefen`` rot macht: Fehlendes ohne Quittung."""
    return [b for b in befunde if b.art == "fehlt" and not b.quittung]


# --- Dateien -----------------------------------------------------------------

def lade_profil(ordner: Path) -> list[Posten] | None:
    pfad = Path(ordner) / PROFIL
    if not pfad.exists():
        return None
    daten = json.loads(pfad.read_text(encoding="utf-8"))
    return [Posten.aus_dict(d) for d in daten.get("posten") or []]


def schreibe_profil(ordner: Path, jahr: int, posten: list[Posten]) -> Path:
    """Schreibt das Profil; ein bestehendes geht vorher in den Verlauf."""
    ordner = Path(ordner)
    pfad = ordner / PROFIL
    if pfad.exists():
        verlauf = ordner / VERLAUF
        verlauf.mkdir(exist_ok=True)
        shutil.copy2(pfad, verlauf / f"{datetime.now():%Y%m%d-%H%M%S}-{PROFIL}")
    pfad.write_text(json.dumps({
        "jahr": int(jahr), "erzeugt": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "posten": [p.als_dict() for p in sorted(posten, key=lambda p: p.schluessel)],
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return pfad


def lade_quittungen(ordner: Path) -> dict[str, dict]:
    """Kürzel → jüngste Quittung (die Datei ist eine angehängte Liste)."""
    pfad = Path(ordner) / QUITTUNGEN
    if not pfad.exists():
        return {}
    aus: dict[str, dict] = {}
    for q in json.loads(pfad.read_text(encoding="utf-8")):
        if q.get("zurueckgenommen"):
            aus.pop(q.get("kurz", ""), None)
        else:
            aus[q.get("kurz", "")] = q
    return aus


def quittiere(ordner: Path, kurz: str, grund: str, zurueck: bool = False) -> None:
    """Hängt eine Quittung an — oder ihre Rücknahme (Grabstein, kein Löschen)."""
    if not re.fullmatch(r"V-[0-9a-f]{6}", kurz or ""):
        raise ValueError("Kürzel wie V-1a2b3c (steht in make pruefen).")
    if not zurueck and not str(grund or "").strip():
        raise ValueError("Eine Quittung braucht einen Grund (GRUND=\"…\").")
    pfad = Path(ordner) / QUITTUNGEN
    liste = json.loads(pfad.read_text(encoding="utf-8")) if pfad.exists() else []
    eintrag = {"kurz": kurz, "grund": str(grund or "").strip(),
               "zeit": datetime.now().strftime("%Y-%m-%d %H:%M")}
    if zurueck:
        eintrag["zurueckgenommen"] = True
    liste.append(eintrag)
    pfad.write_text(json.dumps(liste, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
