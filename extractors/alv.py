"""Bescheinigung der Arbeitslosenkasse → Ziffer 3.3 (Feld 140/141).

Wegleitung ZH 2025, S. 12: „3.3 Erwerbsausfallentschädigungen aus
Arbeitslosenversicherung — Bescheinigung". Die Kasse druckt eine Jahres-
zeile unter dem Kopf ``Jahr | Taggelder | Familienzulagen | Beiträge
AHV/IV/EO | Prämien NBUV | Beitrag berufl. Vorsorge | Steuer``. Die Werte
werden über die Lage der Spalte zugeordnet, nicht über die Reihenfolge
(261024-jga). Beträge stehen als „12,345" (Komma = Tausender) und werden
über ``rohtabelle.betrag_in`` gelesen — eine Zahl-Logik (261025-kme).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal

from extractors.rohtabelle import Rohtabelle, betrag_in

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


# OCR-Rauschen in Zellen eines Scans („48,393 Eu", „1,195 DEE 39", „44 EIER , 595"):
# reine Buchstabenwörter sind keine Zahlen und fallen weg.
_RAUSCH_RE = re.compile(r"\b[^\W\d_]{1,5}\b")
_ZAHL_RE = re.compile(r"\d{1,3}(?:[,.'’]\d{3})+(?:\.\d{2})?|\d+(?:\.\d{2})?")


def _zahlen_in_zelle(text: str) -> list[Decimal]:
    """Alle Beträge einer Zelle, von links nach rechts.

    Ist die ganze Zelle ein Betrag, gilt er. Sonst werden Rauschwörter
    entfernt und die verbleibenden Zahlen einzeln gelesen — nicht als eine
    Zahl zusammengezogen („500 7 40" ist nicht 500'740). Lässt sich eine Zahl
    nicht lesen, bleibt die Zelle leer: lieber kein Wert als ein falscher.
    """
    eng = re.sub(r"\s*([,.'’])\s*", r"\1", text.strip())
    wert = betrag_in(eng)
    if wert is not None:
        return [wert]
    bereinigt = re.sub(r"\s*([,.'’])\s*", r"\1", _RAUSCH_RE.sub(" ", text))
    zahlen = _ZAHL_RE.findall(bereinigt)
    werte = [betrag_in(z) for z in zahlen]
    if not zahlen or any(w is None for w in werte):
        return []
    return werte


@dataclass
class Bescheinigung:
    jahr: str = ""
    werte: dict[str, Decimal] = field(default_factory=dict)
    anker: dict | None = None
    befunde: list[str] = field(default_factory=list)


def _spalten(kopfzeilen) -> tuple[list[tuple[float, str]], set[str]]:
    """(Anfang x, Name) je Spalte, aus allen Sprachzeilen des Kopfs.

    Verschmolzene Zellen nennen zwei Spalten; je Anfang entscheidet die
    Mehrheit über die drei Sprachen, bei Gleichstand die Reihenfolge in
    ``_SPALTEN`` (vorher die zufällige Reihenfolge einer Menge). Zurück
    kommen dazu die Namen, die in einer verschmolzenen Zelle standen —
    gewinnt ein solcher Name nirgends, ist seine Spalte nicht lesbar
    (261025-kme, B1a).
    """
    from collections import Counter

    rang = {n: i for i, (n, _) in enumerate(_SPALTEN)}
    anfaenge: list[tuple[float, Counter]] = []
    verschmolzen: set[str] = set()
    for z in kopfzeilen:
        for c in z.zellen:
            namen = [n for n, rx in _SPALTEN if rx.search(c.text)]
            if not namen:
                continue
            if len(namen) > 1:
                verschmolzen.update(namen)
            treffer = next((a for a in anfaenge if abs(a[0] - c.x0) < 8), None)
            if treffer is None:
                treffer = (c.x0, Counter())
                anfaenge.append(treffer)
            treffer[1].update(namen)
    spalten = []
    for x0, zaehler in sorted(anfaenge, key=lambda a: a[0]):
        name = min(zaehler, key=lambda n: (-zaehler[n], rang[n]))
        spalten.append((x0, name))
    return spalten, verschmolzen


def _jahreszeile(z) -> str | None:
    if not z.zellen:
        return None
    j = z.zellen[0].text.strip()
    return j if re.fullmatch(r"20\d{2}", j) else None


def lese(tabelle: Rohtabelle, jahr: str | None = None) -> Bescheinigung | None:
    """Die Zeile des Steuerjahrs unter dem Kopf mit „Taggeld".

    Das Steuerjahr kommt vom Aufrufer (261025-kme, B1a). Bisher gewann die
    erste Jahreszeile — bei zwei Jahren auf einer Bescheinigung das falsche,
    mit gültigem Anker. Ohne Jahr gilt nur eine einzige Jahreszeile; bei
    mehreren wird nichts gewählt, sondern ein Befund gemeldet. ``None`` nur,
    wenn es keinen Kopf mit Jahreszeile gibt.
    """
    zeilen = tabelle.zeilen
    for i, kopf in enumerate(zeilen):
        if not re.search(r"Taggeld", kopf.text, re.I):
            continue
        jahreszeilen = [(k, z) for k, z in enumerate(zeilen[i + 1:i + 12], start=i + 1)
                        if z.seite == kopf.seite and _jahreszeile(z)]
        if not jahreszeilen:
            continue
        erste = jahreszeilen[0][0]
        spalten, verschmolzen = _spalten(zeilen[i:erste])
        if not any(n == "taggelder" for _, n in spalten):
            continue
        jahre = [_jahreszeile(z) for _, z in jahreszeilen]
        if jahr:
            wahl = [(k, z) for k, z in jahreszeilen if _jahreszeile(z) == str(jahr)]
            if not wahl:
                return Bescheinigung("", {}, kopf.anker(), [
                    f"keine Zeile für das Steuerjahr {jahr} "
                    f"(gefunden: {', '.join(jahre)})"])
        elif len(jahreszeilen) > 1:
            return Bescheinigung("", {}, kopf.anker(), [
                f"mehrere Jahreszeilen ({', '.join(jahre)}) — Steuerjahr nicht "
                "angegeben, kein Wert gewählt"])
        else:
            wahl = jahreszeilen
        _, z = wahl[0]
        b = Bescheinigung(_jahreszeile(z), anker=z.anker())
        if not any(n == "steuerbar" for _, n in spalten):
            b.befunde.append("Spalte steuerbar nicht lesbar (Kopf verschmolzen)"
                             if "steuerbar" in verschmolzen else "Spalte steuerbar fehlt")
        for c in z.zellen[1:]:
            vor = [(x, n) for x, n in spalten if x <= c.x0 + 2]
            zahlen = _zahlen_in_zelle(c.text)
            if not vor or not zahlen:
                continue
            x_basis, name = max(vor)
            # Eine Zelle mit mehreren Zahlen gehört den Spalten, die in ihr
            # beginnen: zuerst der Spalte an ihrem Anfang, dann den folgenden.
            ziele = [name]
            if len(zahlen) > 1:
                ziele += [n for x, n in sorted(spalten) if x_basis < x < c.x1 - 2]
            if len(zahlen) != len(ziele):
                b.befunde.append(
                    f"Zelle mit {len(zahlen)} Zahlen, {len(ziele)} Spalte(n) — "
                    "nicht eindeutig zuzuordnen")
                continue
            for name, wert in zip(ziele, zahlen):
                if name in b.werte:
                    # Kein stilles setdefault: der erste bleibt, gesagt wird es.
                    b.befunde.append(f"zwei Werte unter Spalte {name}")
                    continue
                b.werte[name] = wert
        if "taggelder" in b.werte:
            abzuege = sum(b.werte.get(n, Decimal(0)) for n in ("ahv", "nbuv", "bvg"))
            brutto = b.werte["taggelder"] + b.werte.get("familienzulagen", Decimal(0))
            if "steuerbar" in b.werte and abs(brutto - abzuege - b.werte["steuerbar"]) > 2:
                b.befunde.append(
                    f"Taggelder + Zulagen − Beiträge = {brutto - abzuege} "
                    f"≠ steuerbar {b.werte['steuerbar']}")
        return b
    return None
