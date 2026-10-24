"""Depot: Steuerwert, Ertrag A/B und Depot-Nr. — aus festen Stellen gelesen.

Regel vor Modell (261024-jga). Das Modell lieferte beim Depot das Total des
Ertrags als „ohne Verrechnungssteuer"; Kolonne A (mit VSt) ging verloren und
damit der Rückerstattungsanspruch. Die Steuerauszüge sind aber Formulare:

* **Konsolidierte Übersicht** — Kopf ``Total CHF | A* CHF | B* CHF``,
  darunter ``Steuerwert | t | a | b`` und ``Ertrag | t | a | b``.
* **Übersicht fürs Wertschriftenverzeichnis** — Kopf „Steuerwert der A- und
  B-Werte | Bruttoertrag mit VSt.-Abzug | … ohne VSt.-Abzug | Verrechnungs-
  steueranspruch", darunter eine Zeile ``12'000(1) | 40 | 300 | 14`` und
  „Davon A … und B …".
* **Portfolio-Übersicht** (Wertentwicklung) — nur „Gesamtvermögen"; einen
  Ertrag weist sie nicht aus. Das wird gesagt, nicht geraten.

Gegenprobe: Verrechnungssteuer = 35 % von A.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal

from extractors.eintraege import betrag
from extractors.rohtabelle import Rohtabelle

VST_SATZ = Decimal("0.35")


@dataclass
class Depot:
    kennung: str = ""
    kennung_art: str = ""            # depot | konto | kunde
    steuerwert: Decimal | None = None
    ertrag_a: Decimal | None = None  # mit Verrechnungssteuer
    ertrag_b: Decimal | None = None  # ohne Verrechnungssteuer
    ertrag_total: Decimal | None = None
    vst: Decimal | None = None
    form: str = ""                   # uebersicht | wv | portfolio | ""
    liquiditaet: Decimal | None = None   # Portfolio: Kontokorrent, steht als Konto
    gesamt: Decimal | None = None        # Portfolio: Gesamtvermögen inkl. Liquidität
    befunde: list[str] = field(default_factory=list)


_KENNUNG = (
    ("depot", re.compile(r"\bDepot(?:-?Nr\.?)?\s*\|?\s*(\d[\d .-]{5,}\d)\b")),
    ("konto", re.compile(r"\bKonto-?(?:nummer|Nr\.?)\s*\|?\s*(\d[\d .-]{4,}\d)\b", re.I)),
    ("kunde", re.compile(r"\b(?:Kunde\s*nr\.?|Kdnr\.?|Kunden-?Nr\.?)\s*\|?\s*(\d{4,})\b", re.I)),
)


def _zahl(text: str) -> Decimal | None:
    return betrag(re.sub(r"\(\d\)", "", text or "").strip())


def lese(tabelle: Rohtabelle) -> Depot:
    d = Depot()
    zeilen = tabelle.zeilen
    for art, rx in _KENNUNG:
        m = next((rx.search(z.text) for z in zeilen if rx.search(z.text)), None)
        if m:
            d.kennung, d.kennung_art = re.sub(r"\s", "", m.group(1)), art
            break
    # Gültige IBAN im Dokument (Anlage-/Kontokorrent) schlägt eine Kunden-
    # nummer und ersetzt eine fehlende Kennung (261024-jga, echter Lauf).
    if d.kennung_art in ("", "kunde"):
        from extractors.bankobjekte import iban_gueltig
        from extractors.rohtabelle import ibans_in
        iban = next((i for z in zeilen for i in ibans_in(z.text)
                     if iban_gueltig(i) is None), None)
        if iban:
            d.kennung, d.kennung_art = iban.replace(" ", ""), "iban"

    # 1. Konsolidierte Übersicht mit A*/B*
    for i, z in enumerate(zeilen):
        if re.search(r"\bA\*", z.text) and re.search(r"\bB\*", z.text):
            for z2 in zeilen[i + 1:i + 6]:
                zellen = [c.text.strip() for c in z2.zellen]
                zahlen = [_zahl(c) for c in zellen]
                zahlen = [x for x in zahlen if x is not None]
                if len(zahlen) < 3:
                    continue
                if re.search(r"Steuerwert", z2.text) and d.steuerwert is None:
                    d.steuerwert = zahlen[-3]
                elif re.match(r"Ertrag\b", z2.text) and d.ertrag_a is None:
                    d.ertrag_total, d.ertrag_a, d.ertrag_b = zahlen[-3:]
            if d.steuerwert is not None:
                d.form = "uebersicht"
                break

    # 2. Übersicht fürs Wertschriftenverzeichnis
    if not d.form:
        for i, z in enumerate(zeilen):
            if not re.match(r"Steuerwert der\b", z.text):
                continue
            for z2 in zeilen[i + 1:i + 6]:
                zahlen = [_zahl(c.text) for c in z2.zellen]
                if len(zahlen) >= 3 and all(x is not None for x in zahlen[:3]):
                    d.steuerwert, d.ertrag_a, d.ertrag_b = zahlen[:3]
                    if len(zahlen) >= 4 and zahlen[3] is not None:
                        d.vst = zahlen[3]
                    d.form = "wv"
                    break
            # „Davon A … und B …" muss nicht aufgehen: der Steuerwert gilt
            # „inkl. Konti, ohne DA-1 und USA". Keine Gegenprobe daraus.
            if d.form:
                break

    # 3. Portfolio-Übersicht: Gesamtvermögen. Die Liquidität darin ist das
    # Konto bei der Bank und steht im Verzeichnis schon als Konto — im Depot
    # nur die Wertschriften, sonst zählt das Konto doppelt (der Anwender, 261024-jga).
    if not d.form:
        for z in zeilen:
            if re.search(r"Gesamtverm\S*gen", z.text, re.I) and z.betraege():
                d.gesamt = z.betraege()[-1][1]
                d.form = "portfolio"
                liq = next((m for z2 in zeilen for m in [re.search(
                    r"Total\s+Liquidit\S*\s*\|?\s*(-?[\d'’]+\.\d{2})", z2.text, re.I)] if m),
                    None)
                if liq is not None:
                    d.liquiditaet = betrag(liq.group(1))
                d.steuerwert = d.gesamt - (d.liquiditaet or Decimal(0))
                d.befunde.append("Portfolio-Übersicht: Ertrag steht beim Konto der Bank")
                break

    if d.ertrag_a is not None and d.vst is not None:
        soll = (d.ertrag_a * VST_SATZ).quantize(Decimal("1"))
        if abs(soll - d.vst) > 1:
            d.befunde.append(f"VSt {d.vst} ≠ 35 % von A ({soll})")
    return d
