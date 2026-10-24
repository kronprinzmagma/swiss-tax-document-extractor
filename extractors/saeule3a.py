"""Säule 3a: die Einzahlung des Steuerjahrs — aus Formular 21 oder Tabelle.

Zwei Formen (261024-jga):

* **Formular 21** — kann hinter einem Begleitbrief stehen (Seite 2). Die
  Vertragszeilen unter „2. Beiträge … (Säule 3a)" tragen den Betrag als
  letzte Zelle; „q Total Beiträge an die Säule 3a" fasst sie zusammen.
* **Bescheinigung der Stiftung** — Tabelle ``… | Jahr | Beiträge in CHF``,
  die Zeile des Steuerjahrs gilt.

Höchstbetrag 2025 mit 2. Säule: 7'258.
"""
from __future__ import annotations

import re
from decimal import Decimal

from extractors.eintraege import betrag
from extractors.rohtabelle import Rohtabelle

MAXIMUM = Decimal("7258")


def lese(tabelle: Rohtabelle, jahr: str = "2025") -> tuple[Decimal | None, list[str]]:
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
            posten = []
            for z in s[start + 1:ende]:
                w = betrag(z.zellen[-1].text) if z.zellen else None
                # Jahreszahlen (Abschluss, Fälligkeit) sind keine Beiträge.
                if w is not None and not re.fullmatch(r"(19|20)\d{2}", z.zellen[-1].text.strip()):
                    posten.append(w)
            total = betrag(s[ende].zellen[-1].text) if s[ende].zellen else None
            wert = total if total is not None else (sum(posten) if posten else None)
            if total is not None and posten and sum(posten) != total:
                befunde.append(f"Summe der Verträge {sum(posten)} ≠ Total {total}")
            if wert is not None:
                if wert > MAXIMUM:
                    befunde.append(f"über dem Höchstbetrag {MAXIMUM}")
                return wert, befunde
    for i, kopf in enumerate(zeilen):
        if not re.search(r"Beiträge in CHF|Einzahlungen? in CHF", kopf.text):
            continue
        for z in zeilen[i + 1:i + 6]:
            texte = [c.text.strip() for c in z.zellen]
            if jahr in texte and z.zellen:
                w = betrag(texte[-1])
                if w is not None:
                    if w > MAXIMUM:
                        befunde.append(f"über dem Höchstbetrag {MAXIMUM}")
                    return w, befunde
    return None, befunde
