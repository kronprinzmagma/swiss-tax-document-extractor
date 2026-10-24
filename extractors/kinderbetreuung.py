"""Fremdbetreuung: ein Betrag je Kind — aus der Tabelle des Ausweises.

Das Formular fragt die Kosten je Kind (Feld 376). Der Ausweis der Stadt
listet beide Kinder in einer Tabelle ``Name | Geburtsdatum | Betrag`` und
darunter ``Total``. Das Modell gab das Total für ein unbestimmtes Kind aus
(261024-jga). Gegenprobe: Summe der Zeilen = Total.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal

from extractors.eintraege import betrag
from extractors.rohtabelle import Rohtabelle

_DATUM = re.compile(r"\b\d{2}\.\d{2}\.\d{4}\b")


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
    for z in tabelle.zeilen:
        zellen = [c.text.strip() for c in z.zellen]
        if len(zellen) >= 3 and _DATUM.fullmatch(zellen[1]):
            wert = betrag(zellen[-1])
            if wert is not None and re.search(r"[A-Za-zÄÖÜäöü]{2,}", zellen[0]):
                a.kinder.append(Kindbetrag(zellen[0], wert, z.anker()))
        elif zellen and re.match(r"Total\b", zellen[0]) and len(zellen) >= 2:
            # „1'234, 50" (OCR) → 1234.50
            a.total = betrag(re.sub(r",\s+(\d{2})\b", r".\1", zellen[-1]))
    if a.kinder and a.total is not None:
        summe = sum(k.betrag for k in a.kinder)
        if abs(summe - a.total) > Decimal("0.05"):
            a.befunde.append(f"Summe der Kinder {summe} ≠ Total {a.total}")
    return a
