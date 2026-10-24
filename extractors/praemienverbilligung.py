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


def lese(tabelle: Rohtabelle) -> Verbilligung | None:
    """Die Verbilligung (positiv) — oder None, wenn der Beleg keine nennt."""
    ipv = next((z for z in tabelle.zeilen if _IPV.search(z.text) and z.betraege()), None)
    if ipv is None:
        return None
    rechnung = next((z for z in tabelle.zeilen
                     if _IN_RECHNUNG.search(z.text) and z.betraege()), None)
    return Verbilligung(abs(ipv.betraege()[-1][1]), ipv.anker(),
                        rechnung.betraege()[-1][1] if rechnung else None)
