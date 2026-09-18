"""Deterministischer Layout-Extraktor für die Säule-3a-Bescheinigung (Form 21).

Wie der Lohnausweis ist Form 21 ein eidgenössisch standardisiertes Formular:
eine Betragsspalte am rechten Rand, darin die Beiträge je Vertrag und darunter
die Zeile „Total der Beiträge an die Säule 3a". Für so ein Dokument ist eine
Regel dem Sprachmodell überlegen — sie ist exakt, und die Bounding-Box ist
zugleich der Nachweis.

Die Gegenprobe steckt im Formular selbst: bei einem einzigen Vertrag trägt die
Vertragszeile denselben Betrag wie die Totalzeile, bei mehreren muss ihre Summe
aufgehen. Geht sie auf, ist der Wert arithmetisch bewiesen.

Fallstrick, den die Regel umgeht: in derselben Spalte stehen auch Jahreszahlen
(Abschluss- und Fälligkeitsjahr, z.B. 2020 und 2045) sowie Formular-Codes in
der Fusszeile. Deshalb wird der Zielwert über die Beschriftung der Zeile
gefunden, nicht über die Grösse des Betrags.
"""
from __future__ import annotations

import re
from typing import Any

from extractors.numbers import parse_swiss_amount

# Betragsspalte: rechter Rand. Auf den geprüften Belegen liegen die Beträge bei
# x0 ≈ 0.92 der Seitenbreite.
SPALTE_AB = 0.82

# Kopf- und Fussbereich ausschliessen: dort stehen PLZ, Formularnummern und
# Codes, die wie Beträge aussehen.
KOPF_BIS = 0.15
FUSS_AB = 0.90

# Zeile mit dem Zielwert. Dreisprachiges Formular, deshalb tolerant.
TOTAL_RE = re.compile(r"total.{0,80}?(3a|3\s*a\b)", re.IGNORECASE | re.DOTALL)

# Jahreszahlen sehen wie Beträge aus und stehen in derselben Spalte.
JAHR_RE = re.compile(r"^(19|20)\d{2}$")

PLAUSI_TOLERANZ = 0.01


def _betrag(text: str) -> float | None:
    kern = text.strip().rstrip(".")
    if not kern or JAHR_RE.match(kern):
        return None
    try:
        wert = parse_swiss_amount(kern)
    except ValueError:
        return None
    return float(wert) if wert is not None else None


def _zeilen_kontext(words: list[dict], wort: dict, toleranz: float = 4.0) -> str:
    """Text links des Betrags in derselben Zeile."""
    return " ".join(
        w["text"] for w in sorted(
            (w for w in words
             if abs(float(w["top"]) - float(wort["top"])) <= toleranz
             and float(w["x0"]) < float(wort["x0"])),
            key=lambda w: float(w["x0"]),
        )
    )


def extract_saeule3a_layout(
    words: list[dict],
    page_width: float | None = None,
    page_height: float | None = None,
) -> dict[str, Any] | None:
    """Liest den Total-Beitrag geometrisch aus einer Form-21-Bescheinigung.

    Returns:
        Dict mit ``einzahlung_betrag`` (``value``, ``bbox``, ``page``,
        ``snippet``), ``plausibel`` und ``vertragssumme``. ``None``, wenn keine
        Totalzeile gefunden wird — dann bleibt der LLM-Pfad zuständig.
    """
    if not words:
        return None
    breite = page_width or max((float(w.get("x1", 0)) for w in words), default=0.0)
    hoehe = page_height or max((float(w.get("bottom", 0)) for w in words), default=0.0)
    if breite <= 0 or hoehe <= 0:
        return None

    kandidaten = []
    for w in words:
        if float(w.get("x0", 0)) < breite * SPALTE_AB:
            continue
        oben = float(w.get("top", 0))
        if oben < hoehe * KOPF_BIS or oben > hoehe * FUSS_AB:
            continue
        betrag = _betrag(str(w.get("text", "")))
        if betrag is None or betrag <= 0:
            continue
        kandidaten.append((betrag, w))

    if not kandidaten:
        return None

    # Totalzeile über die Beschriftung finden — nicht über die Grösse.
    total = next(
        ((b, w) for b, w in sorted(kandidaten, key=lambda k: float(k[1]["top"]))
         if TOTAL_RE.search(_zeilen_kontext(words, w))),
        None,
    )
    if total is None:
        return None
    total_betrag, total_wort = total

    # Vertragszeilen: Beträge oberhalb der Totalzeile in derselben Spalte.
    vertraege = [b for b, w in kandidaten
                 if float(w["top"]) < float(total_wort["top"])]
    summe = round(sum(vertraege), 2)
    plausibel = bool(vertraege) and abs(summe - total_betrag) < PLAUSI_TOLERANZ

    return {
        "einzahlung_betrag": {
            "value": str(total_wort["text"]).strip().rstrip("."),
            "bbox": [float(total_wort["x0"]), float(total_wort["top"]),
                     float(total_wort["x1"]), float(total_wort["bottom"])],
            "page": total_wort.get("page", 1),
            "snippet": str(total_wort["text"]).strip(),
        },
        "plausibel": plausibel,
        "vertragssumme": summe,
        "total": total_betrag,
    }
