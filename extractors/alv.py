"""Bescheinigung der Arbeitslosenkasse → Ziffer 3.3 (Feld 140/141).

Wegleitung ZH 2025, S. 12: „3.3 Erwerbsausfallentschädigungen aus
Arbeitslosenversicherung — Bescheinigung". Die Kasse druckt eine Jahres-
zeile unter dem Kopf ``Jahr | Taggelder | Familienzulagen | Beiträge
AHV/IV/EO | Prämien NBUV | Beitrag berufl. Vorsorge | Steuer``. Die Werte
werden über die Lage der Spalte zugeordnet, nicht über die Reihenfolge
(261024-jga). Beträge stehen als „12,345" (Komma = Tausender).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal

from extractors.rohtabelle import Rohtabelle

ALV_RE = re.compile(r"Arbeitslosen(?:versicherung|entsch[äa]digung|kasse)|Taggelder",
                    re.IGNORECASE)
# Spaltennamen in allen drei Sprachen — im deutschen Kopf verschmelzen
# „Beitrag berufl. Vorsorge" und „Steuerbarer Betrag" zu einer Zelle.
_SPALTEN = (
    ("taggelder", re.compile(r"Taggeld|Indemn|Indenn", re.I)),
    ("familienzulagen", re.compile(r"Fa\w*zulage|Allocation|Assegno", re.I)),
    ("ahv", re.compile(r"AHV|AVS|Cobsations|Contributi", re.I)),
    ("nbuv", re.compile(r"NBU|AANP|AINP", re.I)),
    ("bvg", re.compile(r"Vorsorge|pr[eé]voyance|Previdenza", re.I)),
    ("steuerbar", re.compile(r"Steuerbar|imposable|ımposable|imponib|ımponib", re.I)),
    ("krankenversicherung", re.compile(r"Kranke|maladie|malatt", re.I)),
    ("quellensteuer", re.compile(r"Quellensteuer|source|sousce|fonte|fonıte", re.I)),
)


def _zahl(text: str) -> Decimal | None:
    t = re.sub(r"\s", "", text or "")
    if re.fullmatch(r"\d{1,3}(?:[,.'’]\d{3})+", t):
        return Decimal(re.sub(r"\D", "", t))
    if re.fullmatch(r"\d+(?:\.\d{2})?", t):
        return Decimal(t)
    return None


@dataclass
class Bescheinigung:
    jahr: str = ""
    werte: dict[str, Decimal] = field(default_factory=dict)
    anker: dict | None = None
    befunde: list[str] = field(default_factory=list)


def _spalten(kopfzeilen) -> list[tuple[float, str]]:
    """(Anfang x, Name) je Spalte, aus allen Sprachzeilen des Kopfs.

    Verschmolzene Zellen nennen zwei Spalten; je Anfang entscheidet die
    Mehrheit über die drei Sprachen.
    """
    from collections import Counter

    anfaenge: list[tuple[float, Counter]] = []
    for z in kopfzeilen:
        for c in z.zellen:
            namen = {n for n, rx in _SPALTEN if rx.search(c.text)}
            if not namen:
                continue
            treffer = next((a for a in anfaenge if abs(a[0] - c.x0) < 8), None)
            if treffer is None:
                treffer = (c.x0, Counter())
                anfaenge.append(treffer)
            treffer[1].update(namen)
    return [(x0, zaehler.most_common(1)[0][0]) for x0, zaehler in sorted(anfaenge)]


def lese(tabelle: Rohtabelle, jahr: str | None = None) -> Bescheinigung | None:
    zeilen = tabelle.zeilen
    for i, kopf in enumerate(zeilen):
        if not re.search(r"Taggeld", kopf.text, re.I):
            continue
        for k, z in enumerate(zeilen[i + 1:i + 6], start=i + 1):
            if z.seite != kopf.seite or not z.zellen:
                continue
            j = z.zellen[0].text.strip()
            if not re.fullmatch(r"20\d{2}", j) or (jahr and j != jahr):
                continue
            spalten = _spalten(zeilen[i:k])
            if not any(n == "taggelder" for _, n in spalten):
                break
            b = Bescheinigung(j, anker=z.anker())
            for c in z.zellen[1:]:
                vor = [(x, n) for x, n in spalten if x <= c.x0 + 2]
                wert = _zahl(c.text)
                if vor and wert is not None:
                    b.werte.setdefault(max(vor)[1], wert)
            if "taggelder" in b.werte:
                abzuege = sum(b.werte.get(n, Decimal(0)) for n in ("ahv", "nbuv", "bvg"))
                brutto = b.werte["taggelder"] + b.werte.get("familienzulagen", Decimal(0))
                if "steuerbar" in b.werte and abs(brutto - abzuege - b.werte["steuerbar"]) > 2:
                    b.befunde.append(
                        f"Taggelder + Zulagen − Beiträge = {brutto - abzuege} "
                        f"≠ steuerbar {b.werte['steuerbar']}")
                return b
    return None
