"""Säule 3a: die Einzahlung des Steuerjahrs — aus Formular 21 oder Tabelle.

Zwei Formen (261024-jga):

* **Formular 21** — kann hinter einem Begleitbrief stehen (Seite 2). Die
  Vertragszeilen unter „2. Beiträge … (Säule 3a)" tragen den Betrag als
  letzte Zelle; „q Total Beiträge an die Säule 3a" fasst sie zusammen.
* **Bescheinigung der Stiftung** — Tabelle ``… | Jahr | Beiträge in CHF``,
  die Zeile des Steuerjahrs gilt.

Höchstbetrag aus dem Datenmodell (``datenmodell.saeule_3a_maximum``).

261025-kme (B1b): die Beitragsspalte wird über ihre Kopfzelle gefunden, nicht
als letzte Zelle der Zeile („Guthaben 31.12." stand rechts daneben); die
ganze Tabelle wird durchsucht, nicht fünf Zeilen; das Steuerjahr kommt vom
Aufrufer. Was nicht eindeutig ist, ist ein Befund ohne Wert.
"""
from __future__ import annotations

import re
from decimal import Decimal
from typing import NamedTuple

from extractors.rohtabelle import Rohtabelle

_BEITRAG_KOPF_RE = re.compile(r"Beiträge in CHF|Einzahlungen? in CHF")
_JAHR_KOPF_RE = re.compile(r"Jahr|Year|Année|Anno", re.IGNORECASE)
_JAHR_RE = re.compile(r"(?:19|20)\d{2}")
_ENDE_RE = re.compile(r"(?:Total|Summe)\b", re.IGNORECASE)


class Einzahlung(NamedTuple):
    """Die Einzahlung, ihre Befunde, ihre Herkunft und ihr Anker.

    ``herkunft``: ``dokument`` (Total bzw. Zeile des Jahres gelesen),
    ``berechnet`` (Summe der Vertragszeilen, kein Total gedruckt) oder ``""``
    (nichts gefunden). Berechnetes heisst berechnet (261025-hbv, B6).
    """
    wert: Decimal | None
    befunde: list[str]
    herkunft: str = ""
    anker: dict | None = None


def _maximum() -> Decimal:
    from extractors import datenmodell
    return datenmodell.saeule_3a_maximum()


def _letzter_betrag(z):
    """Der letzte Betrag der Zeile — „3'000.00 | CHF" trägt ihn vor der
    Währung; Jahreszahlen schliesst ``betrag_in`` aus (261025-kme, B1b)."""
    b = z.betraege()
    return b[-1][1] if b else None


def _ueberlappt(c, x0: float, x1: float) -> bool:
    return min(c.x1, x1) - max(c.x0, x0) > 0


def _tabelle_lesen(zeilen, i: int, jahr: str | None) -> Einzahlung | None:
    """Beitragstabelle unter dem Kopf ``zeilen[i]`` — None, wenn keine Jahreszeile."""
    kopf = zeilen[i]
    bk = next((c for c in kopf.zellen if _BEITRAG_KOPF_RE.search(c.text)), None)
    if bk is None:
        return None
    jk = next((c for c in kopf.zellen if _JAHR_KOPF_RE.fullmatch(c.text.strip())), None)
    halb = (bk.x1 - bk.x0) / 2 + 20
    mitte_bk = (bk.x0 + bk.x1) / 2
    gefunden = []          # (jahr, zeile, betraege)
    for z in zeilen[i + 1:]:
        if z.seite != kopf.seite or (z.zellen and _ENDE_RE.match(z.zellen[0].text.strip())):
            break
        j = None
        if jk is not None:
            jz = next((c for c in z.zellen if _ueberlappt(c, jk.x0, jk.x1)), None)
            if jz is not None and _JAHR_RE.fullmatch(jz.text.strip()):
                j = jz.text.strip()
        else:
            j = next((c.text.strip() for c in z.zellen
                      if _JAHR_RE.fullmatch(c.text.strip())), None)
        if j is None:
            continue
        zelle_i = next((k for k, c in enumerate(z.zellen) if _ueberlappt(c, bk.x0, bk.x1)), None)
        if zelle_i is None:
            naechste = sorted((abs((c.x0 + c.x1) / 2 - mitte_bk), k)
                              for k, c in enumerate(z.zellen))
            if naechste and naechste[0][0] <= halb:
                zelle_i = naechste[0][1]
        betraege = [w for k, w in z.betraege() if k == zelle_i] if zelle_i is not None else []
        gefunden.append((j, z, betraege))
    if not gefunden:
        return None
    jahre = [j for j, _, _ in gefunden]
    if jahr:
        wahl = [g for g in gefunden if g[0] == str(jahr)]
        if not wahl:
            return Einzahlung(None, [f"keine Zeile für das Steuerjahr {jahr} "
                                     f"(gefunden: {', '.join(jahre)})"], "", kopf.anker())
    elif len(gefunden) > 1:
        return Einzahlung(None, [f"mehrere Jahreszeilen ({', '.join(jahre)}) — "
                                 "Steuerjahr nicht angegeben"], "", kopf.anker())
    else:
        wahl = gefunden
    if len(wahl) > 1:
        return Einzahlung(None, [f"Jahr {wahl[0][0]} steht {len(wahl)}× in der Tabelle"],
                          "", kopf.anker())
    _, z, betraege = wahl[0]
    if len(betraege) > 1:
        return Einzahlung(None, ["Beitragszelle mehrdeutig"], "", z.anker())
    if not betraege:
        return Einzahlung(None, ["kein Betrag in der Spalte der Beiträge"], "", z.anker())
    befunde = []
    if betraege[0] > _maximum():
        befunde.append(f"über dem Höchstbetrag {_maximum()}")
    return Einzahlung(betraege[0], befunde, "dokument", z.anker())


def lese(tabelle: Rohtabelle, jahr: str | None = None) -> Einzahlung:
    """Die Einzahlung des Steuerjahrs ``jahr`` (vom Aufrufer; 261025-kme).

    Ohne Jahr gilt eine Beitragstabelle nur, wenn sie genau eine Jahreszeile
    hat. Ohne passenden Kopf: ``Einzahlung(None, [])`` wie bisher.
    """
    befunde: list[str] = []
    zeilen = tabelle.zeilen
    form21 = sorted({z.seite for z in zeilen if re.search(r"Form\.?\s*21\b", z.text)})
    if form21:
        seite = form21[0]          # Original; spätere Seiten sind Kopien
        s = [z for z in zeilen if z.seite == seite]
        start = next((i for i, z in enumerate(s)
                      if re.search(r"Beiträge für anerkannte Formen.*Säule 3a", z.text)), None)
        ende = next((i for i, z in enumerate(s)
                     if re.search(r"Total Beiträge an die Säule 3a", z.text)), None)
        if start is not None and ende is not None:
            posten = [w for z in s[start + 1:ende]
                      if (w := _letzter_betrag(z)) is not None]
            total = _letzter_betrag(s[ende])
            wert = total if total is not None else (sum(posten) if posten else None)
            if total is not None and posten and sum(posten) != total:
                befunde.append(f"Summe der Verträge {sum(posten)} ≠ Total {total}")
            if wert is not None:
                if wert > _maximum():
                    befunde.append(f"über dem Höchstbetrag {_maximum()}")
                if total is not None:
                    return Einzahlung(wert, befunde, "dokument", s[ende].anker())
                # Kein Total gedruckt: die Summe der Verträge, Anker auf den
                # Kopf des Abschnitts.
                return Einzahlung(wert, befunde, "berechnet", s[start].anker())
    for i, kopf in enumerate(zeilen):
        if not _BEITRAG_KOPF_RE.search(kopf.text):
            continue
        e = _tabelle_lesen(zeilen, i, jahr)
        if e is not None:
            return e
    return Einzahlung(None, befunde)
