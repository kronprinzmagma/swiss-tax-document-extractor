"""Prämienverbilligung aus der Prämienbescheinigung der Krankenkasse.

Das Formular verlangt die Prämien „abzüglich erhaltene Prämienverbilligungen"
(Feldliste ZHprivateTax 2025). Die Kasse weist sie als eigene Zeile aus:
``Prämienverbilligung | -111.11`` und darunter ``Total in Rechnung gestellte
Prämien | 888.89``. Bisher las niemand diese Zeile (261024-jga).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

from extractors.rohtabelle import Rohtabelle

_IPV = re.compile(r"Pr[äa]mienverbilligung|individuelle\s+Pr[äa]mienverbilligung|\bIPV\b",
                  re.IGNORECASE)
_IN_RECHNUNG = re.compile(r"Total\s*in\s*Rechnung\s*gestellte\s*Pr[äa]mien", re.IGNORECASE)


@dataclass
class Verbilligung:
    betrag: Decimal
    anker: dict | None
    in_rechnung: Decimal | None = None


# Ein gebuchter Betrag hat Rappen oder ein Vorzeichen („-111.10"); eine
# Überschrift „Prämienverbilligung | 2025" nicht (261025-hbv, B7).
_GEBUCHT = re.compile(r"(?:[.,]\d{2}|^\s*[-–−].*\d)\s*$")


def _gebucht(z) -> list:
    """Beträge der Zeile, deren Zelle wie eine Buchung aussieht."""
    return [w for i, w in z.betraege() if _GEBUCHT.search(z.zellen[i].text)]


def _beste(zeilen):
    """(Zeile, Betrag): die erste Zeile mit gebuchtem Betrag, sonst die erste
    mit irgendeinem Betrag — oder (None, None)."""
    for z in zeilen:
        werte = _gebucht(z)
        if werte:
            return z, werte[-1]
    for z in zeilen:
        if z.betraege():
            return z, z.betraege()[-1][1]
    return None, None


def lese(tabelle: Rohtabelle) -> Verbilligung | None:
    """Die Verbilligung (positiv) — oder None, wenn der Beleg keine nennt."""
    ipv, wert = _beste([z for z in tabelle.zeilen if _IPV.search(z.text)])
    if ipv is None:
        return None
    _, rechnung = _beste([z for z in tabelle.zeilen if _IN_RECHNUNG.search(z.text)])
    return Verbilligung(abs(wert), ipv.anker(), rechnung)
