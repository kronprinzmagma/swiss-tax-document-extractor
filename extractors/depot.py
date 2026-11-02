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

261025-kme (B1c): Stehen in der Zeile „Gesamtvermögen" mehrere Beträge
(Vorjahr | Stichtag | Veränderung), entscheidet die Kopfzelle des Stichtags,
nicht die Lage am Zeilenende — sonst stand die Veränderung als Steuerwert
da. Ohne eindeutige Spalte, bei negativer Gegenprobe und ohne erkannte Form
gibt es eine Warnung statt eines Werts.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal

from extractors.rohtabelle import Rohtabelle, betrag_in

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
    # Was der Mensch ansehen muss (steht zusätzlich in befunde): Spalte des
    # Stichtags unklar, Gegenprobe negativ, keine Form erkannt (261025-kme).
    warnungen: list[str] = field(default_factory=list)
    # dokument | berechnet (Portfolio: Gesamtvermögen − Liquidität) — 261025-hbv
    steuerwert_herkunft: str = ""
    # Zeile, aus der ein Wert stammt: steuerwert, ertrag_a, ertrag_b, kennung, gesamt
    anker: dict[str, dict] = field(default_factory=dict)


_KENNUNG = (
    ("depot", re.compile(r"\bDepot(?:-?Nr\.?)?\s*\|?\s*(\d[\d .-]{5,}\d)\b")),
    ("konto", re.compile(r"\bKonto-?(?:nummer|Nr\.?)\s*\|?\s*(\d[\d .-]{4,}\d)\b", re.I)),
    ("kunde", re.compile(r"\b(?:Kunde\s*nr\.?|Kdnr\.?|Kunden-?Nr\.?)\s*\|?\s*(\d{4,})\b", re.I)),
)


def _zahl(text: str) -> Decimal | None:
    """Betrag einer Zelle ohne Fussnotenmarke „(1)" — über ``betrag_in``."""
    return betrag_in(re.sub(r"\(\d\)", "", text or "").strip())


_KOPF_DATUM_RE = re.compile(r"\b(\d{2})\.(\d{2})\.(\d{4})\b")
_KOPF_WORT_RE = re.compile(r"\b(?:19|20)\d{2}\b|aktuell|Vorjahr|Ver[äa]nderung|Differenz"
                           r"|Wert|Bestand|%", re.IGNORECASE)
_STICHTAG_RE = re.compile(r"aktuell|Stichtag|per\s+31\.12", re.IGNORECASE)
_NICHT_STICHTAG_RE = re.compile(r"Ver[äa]nderung|Differenz|Vorjahr|%|Performance",
                                re.IGNORECASE)


def _stichtagsspalte(zeilen, i: int) -> tuple[float, float] | None:
    """Lage der Kopfzelle des Stichtags über Zeile ``i`` — oder None.

    Gesucht wird nach oben (bis 15 Zeilen, dieselbe Seite) die nächste
    Kopfzeile mit mindestens zwei Kopfzellen (Datum, Jahreszahl, Wörter wie
    aktuell/Vorjahr/Veränderung/Wert/Bestand/%). Stehen Daten im Kopf, ist
    das jüngste der Stichtag; sonst eine Zelle „aktuell"/„Stichtag"/„per
    31.12". Veränderung, Differenz, Vorjahr, % und Performance sind es nie.
    Nur ein einziger Kandidat zählt.
    """
    z = zeilen[i]
    for k in range(i - 1, max(-1, i - 16), -1):
        kopf = zeilen[k]
        if kopf.seite != z.seite:
            break
        kopfzellen = [c for c in kopf.zellen
                      if _KOPF_DATUM_RE.search(c.text) or _KOPF_WORT_RE.search(c.text)]
        if len(kopfzellen) < 2 or kopf.betraege():
            continue
        moegliche = [c for c in kopfzellen if not _NICHT_STICHTAG_RE.search(c.text)]
        datiert = [(m.group(3), m.group(2), m.group(1), c) for c in moegliche
                   for m in [_KOPF_DATUM_RE.search(c.text)] if m]
        if datiert:
            juengstes = max(d[:3] for d in datiert)
            kandidaten = [d[3] for d in datiert if d[:3] == juengstes]
        else:
            kandidaten = [c for c in moegliche if _STICHTAG_RE.search(c.text)]
        if len(kandidaten) == 1:
            return kandidaten[0].x0, kandidaten[0].x1
        return None
    return None


def _betrag_der_zeile(zeilen, i: int) -> tuple[Decimal | None, int]:
    """(Betrag, Anzahl Beträge) einer Zeile: ein Betrag → dieser; mehrere →
    der in der Stichtagsspalte (Überlappung, sonst nächste Mitte)."""
    z = zeilen[i]
    b = z.betraege()
    if len(b) == 1:
        return b[0][1], 1
    if not b:
        return None, 0
    spalte = _stichtagsspalte(zeilen, i)
    if spalte is None:
        return None, len(b)
    x0, x1 = spalte
    in_spalte = [w for k, w in b if min(z.zellen[k].x1, x1) - max(z.zellen[k].x0, x0) > 0]
    if len(in_spalte) == 1:
        return in_spalte[0], len(b)
    if in_spalte:
        return None, len(b)
    mitte = (x0 + x1) / 2
    abstand = sorted((abs((z.zellen[k].x0 + z.zellen[k].x1) / 2 - mitte), k, w) for k, w in b)
    if len(abstand) > 1 and abstand[0][0] == abstand[1][0]:
        return None, len(b)
    return abstand[0][2], len(b)


def lese(tabelle: Rohtabelle) -> Depot:
    d = Depot()
    zeilen = tabelle.zeilen
    for art, rx in _KENNUNG:
        z = next((z for z in zeilen if rx.search(z.text)), None)
        if z is not None:
            m = rx.search(z.text)
            d.kennung, d.kennung_art = re.sub(r"\s", "", m.group(1)), art
            d.anker["kennung"] = z.anker()
            break
    # Gültige IBAN im Dokument (Anlage-/Kontokorrent) schlägt eine Kunden-
    # nummer und ersetzt eine fehlende Kennung (261024-jga, echter Lauf).
    if d.kennung_art in ("", "kunde"):
        from extractors.bankobjekte import iban_gueltig
        from extractors.rohtabelle import ibans_in
        treffer = next(((i, z) for z in zeilen for i in ibans_in(z.text)
                        if iban_gueltig(i) is None), None)
        if treffer:
            iban, z = treffer
            d.kennung, d.kennung_art = iban.replace(" ", ""), "iban"
            d.anker["kennung"] = z.anker()

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
                    d.anker["steuerwert"] = z2.anker()
                elif re.match(r"Ertrag\b", z2.text) and d.ertrag_a is None:
                    d.ertrag_total, d.ertrag_a, d.ertrag_b = zahlen[-3:]
                    d.anker["ertrag_a"] = d.anker["ertrag_b"] = z2.anker()
            if d.steuerwert is not None:
                d.form = "uebersicht"
                d.steuerwert_herkunft = "dokument"
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
                    d.steuerwert_herkunft = "dokument"
                    for k in ("steuerwert", "ertrag_a", "ertrag_b"):
                        d.anker[k] = z2.anker()
                    break
            # „Davon A … und B …" muss nicht aufgehen: der Steuerwert gilt
            # „inkl. Konti, ohne DA-1 und USA". Keine Gegenprobe daraus.
            if d.form:
                break

    # 3. Portfolio-Übersicht: Gesamtvermögen. Die Liquidität darin ist das
    # Konto bei der Bank und steht im Verzeichnis schon als Konto — im Depot
    # nur die Wertschriften, sonst zählt das Konto doppelt (der Anwender, 261024-jga).
    def warne(text: str) -> None:
        d.warnungen.append(text)
        d.befunde.append(text)

    if not d.form:
        for i, z in enumerate(zeilen):
            if not (re.search(r"Gesamtverm\S*gen", z.text, re.I) and z.betraege()):
                continue
            d.form = "portfolio"
            d.anker["gesamt"] = d.anker["steuerwert"] = z.anker()
            d.befunde.append("Portfolio-Übersicht: Ertrag steht beim Konto der Bank")
            d.gesamt, n = _betrag_der_zeile(zeilen, i)
            if d.gesamt is None:
                warne(f"Gesamtvermögen: {n} Beträge in der Zeile, Spalte des "
                      "Stichtags nicht eindeutig — kein Wert gelesen")
                break
            liq_i = next((k for k, z2 in enumerate(zeilen)
                          if re.search(r"Total\s+Liquidit", z2.text, re.I) and z2.betraege()),
                         None)
            if liq_i is not None:
                d.liquiditaet, n = _betrag_der_zeile(zeilen, liq_i)
                if d.liquiditaet is None:
                    # Enge Spalten („1'000.00 10.00", Betrag und Anteil in
                    # einer Zelle) ohne Kopf: der erste Betrag wie bisher,
                    # aber mit Warnung (261025-kme, B1c).
                    d.liquiditaet = zeilen[liq_i].betraege()[0][1]
                    warne(f"Total Liquidität: {n} Beträge in der Zeile, Spalte des "
                          "Stichtags nicht eindeutig — erster Betrag genommen, prüfen")
            d.steuerwert = d.gesamt - (d.liquiditaet or Decimal(0))
            if d.steuerwert < 0:
                warne("Gegenprobe: Liquidität grösser als Gesamtvermögen")
                d.steuerwert = None
                break
            # Mit Liquidität ist der Steuerwert gerechnet, nicht gelesen
            # (261025-hbv, B6); der Anker zeigt aufs Gesamtvermögen.
            d.steuerwert_herkunft = "berechnet" if d.liquiditaet is not None else "dokument"
            break

    if not d.form:
        warne("keine bekannte Form erkannt (Übersicht A*/B*, Übersicht "
              "Wertschriftenverzeichnis, Portfolio) — Steuerwert nicht gelesen")

    if d.ertrag_a is not None and d.vst is not None:
        soll = (d.ertrag_a * VST_SATZ).quantize(Decimal("1"))
        if abs(soll - d.vst) > 1:
            d.befunde.append(f"VSt {d.vst} ≠ 35 % von A ({soll})")
    return d
