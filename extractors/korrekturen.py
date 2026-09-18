"""Bestätigte Werte als Regressionsschutz.

Bisher wurde strukturell gemessen: ist ein Anker da, sind die Pflichtfelder
gefüllt, steckt kein PII in der Ausgabe. Nie wurde geprüft, ob die **Zahl
stimmt**. Deshalb konnte monatelang ein Bruttorechnungsbetrag in der Tabelle
stehen, obwohl die Spezifikation ihn namentlich ausschliesst.

Diese Lücke schliesst der Korrekturzirkel: was der Mensch in der
Review-Oberfläche bestätigt oder korrigiert, landet in ``korrekturen.json``
und wird ab dann bei jedem Lauf nachgeprüft. Weicht ein späterer Lauf ab,
schlägt er fehl — auch wenn Anker und Struktur einwandfrei aussehen.

Privacy: die Berichte nennen Dokument, Zielwert und Status, aber **niemals
einen Betrag**. Ein Abweichungsbericht sagt „weicht ab", nicht „6300 statt
9300". Damit bleibt er teilbar.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Abgleich:
    """Ergebnis eines Soll-Ist-Vergleichs für einen bestätigten Wert."""

    beleg: str
    zielwert: str
    zustand: str   # "stimmt" | "weicht_ab" | "fehlt"


def lade(pfad: Path) -> dict[str, dict]:
    """Liest ``korrekturen.json``. Fehlt sie, gibt es nichts zu prüfen."""
    if not pfad.exists():
        return {}
    try:
        daten = json.loads(pfad.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return daten if isinstance(daten, dict) else {}


# Umbenannte Zielwerte: neue Bezeichnung -> frueher gebrauchte.
#
# Wird eine Bezeichnung im Feldvertrag praeziser gefasst, zeigen alle
# bestehenden Schluessel ins Leere. Das ist an EINER Stelle abgefangen worden
# und an drei weiteren nicht — mit der Folge, dass die Oberflaeche die
# Bestaetigungen nicht mehr fand und sie beim naechsten Speichern verschwanden
# (260904-rmx). Die Zuordnung gehoert deshalb hierher, wo sie jeder Aufrufer
# sieht.
ALTE_BEZEICHNUNGEN: dict[str, tuple[str, ...]] = {
    "Bruttoertrag ohne Verrechnungssteuer": (
        "Bruttozins / Bruttoertrag", "Bruttoertrag Wertschriften",
        "Bruttoertrag", "Bruttozins",
    ),
    "Bruttoertrag mit Verrechnungssteuer": ("Verrechnungssteuer 35%",),
    # Der Feldvertrag und die Uebertragungstabelle benannten dieselben Felder
    # verschieden ("Saldo 31.12." gegen "Vermögensstand 31.12."). Jede
    # Pruefung, die beide vergleicht, war dadurch blind. Seit die Tabelle die
    # Bezeichnungen des Vertrags uebernimmt, brauchen die alten einen Alias
    # (260904-rmx).
    "Saldo 31.12.": ("Vermögensstand 31.12.",),
    "Steuerwert 31.12.": ("Vermögensstand 31.12.",),
    "Prämie Grundversicherung KVG": ("Prämie KVG (Grundversicherung)",),
    "Prämie Zusatzversicherung VVG": ("Prämie VVG (Zusatzversicherung)",),
    "Nettolohn": ("Nettolohn (Pos. 11)",),
}


def schluessel_kandidaten(beleg: str, zielwert: str) -> list[str]:
    """Alle Schluessel, unter denen dieser Wert gespeichert sein kann."""
    namen = [zielwert, *ALTE_BEZEICHNUNGEN.get(zielwert, ())]
    return [f"{beleg}|{n}" for n in namen]


def finde(korrekturen: dict[str, dict], beleg: str, zielwert: str) -> dict | None:
    """Den Eintrag zu einer Zeile — auch unter einer frueheren Bezeichnung."""
    for k in schluessel_kandidaten(beleg, zielwert):
        eintrag = korrekturen.get(k)
        if isinstance(eintrag, dict):
            return eintrag
    return None


def lade_alle(samples_dir: Path) -> dict[str, dict]:
    """Alle Korrekturen zu einem Lauf — aus beiden möglichen Ablagen.

    Es gibt zwei Wege, wie Korrekturen entstehen, und sie landen an
    verschiedenen Orten:

    * ``apply_korrekturen`` schreibt neben die CSV, also nach
      ``output/latest/korrekturen.json``.
    * Der Live-Server schreibt in den Sample-Ordner, also nach
      ``output/latest/json/korrekturen.json``.

    Die Aufrufer nahmen bisher ``lade(a) or lade(b)`` — das ist kein „beide",
    sondern „die erste, die es gibt". Sobald der Server einmal gespeichert
    hatte, war die ganze über die CSV eingespielte Arbeit unsichtbar. Ohne
    Fehlermeldung: die Tabelle sah einfach wieder aus wie vor der Durchsicht.

    Deshalb zusammenführen. Bei gleichem Schlüssel gewinnt der Sample-Ordner —
    er ist der Ort, an dem die laufende Sitzung schreibt, also der jüngere
    Stand.
    """
    zusammen: dict[str, dict] = {}
    zusammen.update(lade(samples_dir.parent / "korrekturen.json"))
    zusammen.update(lade(samples_dir / "korrekturen.json"))
    return zusammen


def _normalisiere(wert) -> str:
    """Vergleichsform eines Betrags — Apostroph, Leerzeichen, Komma egal."""
    s = str(wert or "").strip()
    s = s.replace("'", "").replace("’", "").replace(" ", "").replace(",", ".")
    # "1000" und "1000.00" sind derselbe Betrag.
    if s.endswith(".00"):
        s = s[:-3]
    return s


def vergleiche(korrekturen: dict[str, dict],
               zeilen: list[dict]) -> list[Abgleich]:
    """Prüft jeden bestätigten Sollwert gegen den aktuellen Lauf.

    Args:
        korrekturen: Inhalt von ``korrekturen.json``, Schlüssel
            ``"<beleg>|<zielwert>"``.
        zeilen: Zeilen des aktuellen Laufs mit ``pdf_name``/``beleg``,
            ``beschreibung``/``zielwert`` und ``betrag``.

    Returns:
        Ein :class:`Abgleich` je Eintrag mit hinterlegtem ``soll``. Einträge
        ohne ``soll`` (nur eine notierte Beschriftung) werden übersprungen —
        sie sind ein Hinweis für eine Regel, kein Sollwert.
    """
    ist: dict[str, str] = {}
    for z in zeilen:
        beleg = str(z.get("pdf_name") or z.get("beleg") or "")
        ziel = str(z.get("beschreibung") or z.get("zielwert") or "")
        if beleg and ziel:
            ist[f"{beleg}|{ziel}"] = _normalisiere(z.get("betrag"))

    # Ein Eintrag kann unter einer frueheren Bezeichnung gespeichert sein.
    # Ohne diesen Umweg meldet der Abgleich „fehlt" fuer jede umbenannte
    # Zeile — sechs Stueck nach der Wertschriften-Umbenennung, und es sah
    # aus wie verlorene Arbeit statt wie ein Namensunterschied (260904-rmx).
    neu_zu_alt: dict[str, list[str]] = {}
    for neu_name, alte in ALTE_BEZEICHNUNGEN.items():
        for a in alte:
            neu_zu_alt.setdefault(a, []).append(neu_name)

    def _ist_wert(beleg: str, ziel: str) -> str | None:
        for kandidat in [ziel, *neu_zu_alt.get(ziel, [])]:
            wert = ist.get(f"{beleg}|{kandidat}")
            if wert:
                return wert
        return None

    out: list[Abgleich] = []
    for schluessel, eintrag in sorted(korrekturen.items()):
        soll = eintrag.get("soll") if isinstance(eintrag, dict) else None
        if soll is None:
            continue
        beleg, _, ziel = schluessel.partition("|")
        gefunden = _ist_wert(beleg, ziel)
        if gefunden is None:
            out.append(Abgleich(beleg, ziel, "fehlt"))
        elif gefunden == _normalisiere(soll):
            out.append(Abgleich(beleg, ziel, "stimmt"))
        else:
            out.append(Abgleich(beleg, ziel, "weicht_ab"))
    return out


def bericht(abgleiche: list[Abgleich]) -> tuple[str, bool]:
    """Teilbarer Bericht ohne Betraege. Returns ``(text, ist_sauber)``."""
    if not abgleiche:
        return ("Keine bestätigten Sollwerte hinterlegt — nichts zu prüfen. "
                "In der Review-Oberfläche Werte bestätigen oder korrigieren, "
                "dann greift der Schutz ab dem nächsten Lauf.", True)

    zaehler = {"stimmt": 0, "weicht_ab": 0, "fehlt": 0}
    for a in abgleiche:
        zaehler[a.zustand] += 1

    zeilen = [f"{len(abgleiche)} bestätigte Sollwerte geprüft: "
              f"{zaehler['stimmt']} stimmen, {zaehler['weicht_ab']} weichen ab, "
              f"{zaehler['fehlt']} fehlen im Lauf."]
    for a in abgleiche:
        if a.zustand == "stimmt":
            continue
        wort = "weicht ab" if a.zustand == "weicht_ab" else "fehlt"
        zeilen.append(f"  [{wort}] {a.beleg} — {a.zielwert}")
    if zaehler["weicht_ab"] or zaehler["fehlt"]:
        zeilen.append("(Betraege werden bewusst nicht ausgegeben — "
                      "der Bericht bleibt teilbar.)")
    return "\n".join(zeilen), not (zaehler["weicht_ab"] or zaehler["fehlt"])
