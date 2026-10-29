"""Fremdbetreuung: ein Betrag je Kind — aus der Tabelle des Ausweises.

Das Formular fragt die Kosten je Kind (Feld 376). Der Ausweis der Stadt
listet beide Kinder in einer Tabelle ``Name | Geburtsdatum | Betrag`` und
darunter ``Total``. Das Modell gab das Total für ein unbestimmtes Kind aus
(261024-jga). Gegenprobe: Summe der Zeilen = Total.

261025-kme (B1d): Kind ist nur eine Zeile, deren Text ein Name ist — eine
Rechnungszeile „Rechnung Nr. 17 | Datum | Betrag" war sonst ein Kind. Was
verworfen wird, wird gezählt (nie mit Text); ein fehlendes Total heisst:
die Summe ist nicht gegengeprüft.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal

from extractors.rohtabelle import Rohtabelle, betrag_in

_DATUM = re.compile(r"\b\d{2}\.\d{2}\.\d{4}\b")
_SCHLAGWORT = re.compile(r"\b(?:Rechnung|Total|Summe|Nr|Datum|Betrag|Periode|Monat"
                         r"|Zahlung|Gutschrift|Saldo|Rabatt|Beitrag)\b", re.IGNORECASE)


def _ist_name(text: str) -> bool:
    """Mindestens zwei Wörter, jedes beginnt mit einem Buchstaben, keines nur
    aus Ziffern/Satzzeichen, kein Schlagwort einer Rechnung.

    Ziffern im Wort sind erlaubt: der Anonymisierer vergibt „PersonN
    Muster" — das ist ein Name.
    """
    woerter = text.split()
    if len(woerter) < 2 or _SCHLAGWORT.search(text):
        return False
    return all(w[0].isalpha() and not re.fullmatch(r"[\d\W_]+", w) for w in woerter)


@dataclass
class Kindbetrag:
    name: str
    betrag: Decimal
    anker: dict | None = None


@dataclass
class Ausweis:
    kinder: list[Kindbetrag] = field(default_factory=list)
    total: Decimal | None = None
    befunde: list[str] = field(default_factory=list)


def lese(tabelle: Rohtabelle) -> Ausweis:
    a = Ausweis()
    verworfen = 0
    for z in tabelle.zeilen:
        zellen = [c.text.strip() for c in z.zellen]
        if len(zellen) >= 3 and _DATUM.fullmatch(zellen[1]):
            wert = betrag_in(zellen[-1])
            if wert is None:
                continue
            if _ist_name(zellen[0]):
                a.kinder.append(Kindbetrag(zellen[0], wert, z.anker()))
            else:
                verworfen += 1
        elif zellen and re.match(r"Total\b", zellen[0]) and len(zellen) >= 2:
            # „1'234, 50" (OCR) liest betrag_in als 1234.50.
            a.total = betrag_in(zellen[-1])
    if verworfen:
        a.befunde.append(f"{verworfen} Zeile mit Datum und Betrag ist kein Kind (verworfen)"
                         if verworfen == 1 else
                         f"{verworfen} Zeilen mit Datum und Betrag sind kein Kind (verworfen)")
    if a.kinder and a.total is None:
        a.befunde.append("Total fehlt, Summe nicht gegengeprüft")
    if a.kinder and a.total is not None:
        summe = sum(k.betrag for k in a.kinder)
        if abs(summe - a.total) > Decimal("0.05"):
            a.befunde.append(f"Summe der Kinder {summe} ≠ Total {a.total}")
    return a
