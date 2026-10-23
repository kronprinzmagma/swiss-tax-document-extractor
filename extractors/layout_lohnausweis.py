"""Deterministischer Layout-Extraktor für den Lohnausweis (Form 11).

Warum überhaupt: der Lohnausweis ist ein eidgenössisch standardisiertes Formular.
Ein Layout für alle Arbeitgeber, feste Ziffern, eine feste Betragsspalte am rechten
Rand. Für so ein Dokument ist eine Regel dem Sprachmodell überlegen — sie ist
exakt, schnell und liefert den Nachweis gratis mit (die Bounding-Box *ist* der
Anker).

Der LLM-Pfad scheitert hier systematisch, weil er den Beleg zu Klartext plättet
und dann die Label-Wert-Zuordnung rekonstruieren soll. Genau die zerstört die OCR
bei gescannten Formularen (`11. Nettolohn` → `11.Nettoioha`). Die *Ziffern* liest
die OCR dagegen fehlerfrei — sie stehen sauber untereinander in einer Spalte.

Kernregel (an echten Belegen verifiziert):

- Betragskandidaten stehen im rechten Viertel der Seite, unterhalb des Kopfbands.
- Der grösste Wert ist der Bruttolohn (Ziffer 8), der zweitgrösste der
  Nettolohn (Ziffer 11).
- Die Abzüge (Ziffer 9 AHV/ALV/NBU, Ziffer 10.1 BVG) stehen in den Zeilen
  *zwischen* beiden. Die Zeilenbedingung schliesst die Spesen (Ziffer 13.x)
  aus, die unterhalb der Netto-Zeile stehen.
- Gegenprobe: ``Brutto − Σ Abzüge == Netto``. Geht sie auf, ist das Ergebnis
  arithmetisch bewiesen und braucht keine weitere Anker-Prüfung.

Beispiel aus einem echten Beleg: 9409 − 587 − 625 = 8197 (exakt).
"""
from __future__ import annotations

from typing import Any

from extractors.numbers import parse_swiss_amount

# Betragsspalte: rechtes Viertel der Seite. Auf allen geprüften Belegen liegen
# die Beträge bei x0 ≈ 0.89–0.94 der Seitenbreite; 0.75 ist bewusst grosszügig.
SPALTE_AB = 0.75

# Kopfband: dort stehen Formular- und Personennummern (z.B. "D 391697 B"), die
# wie Beträge aussehen. Die erste Betragszeile lag auf keinem Beleg über 0.30.
KOPFBAND_BIS = 0.15

# Kleinstwerte sind Zeilennummern, Prozentsätze oder Formularcodes, keine Löhne.
MIN_BETRAG = 100.0

# Toleranz der Gegenprobe in Franken (Rundungen in Rappenangaben).
PLAUSI_TOLERANZ = 0.01


def _parse(text: str) -> float | None:
    """CH-Betrag → float. Gibt None zurück, wenn das Token kein Betrag ist.

    Nutzt bewusst ``extractors.numbers.parse_swiss_amount`` — den kanonischen
    Parser des Projekts (Apostroph-, Komma- und Punkt-Tausender).
    """
    kern = text.strip().rstrip(".")
    if not kern:
        return None
    try:
        betrag = parse_swiss_amount(kern)
    except ValueError:
        # Kein Betrag (Operatoren "+"/"=", Formularcodes, Wortfragmente).
        return None
    return float(betrag) if betrag is not None else None


def _seitenmasse(words: list[dict], page_width: float | None,
                 page_height: float | None) -> tuple[float, float]:
    """Seitenbreite/-höhe, notfalls aus den Wortboxen geschätzt."""
    breite = page_width or max((float(w.get("x1", 0)) for w in words), default=0.0)
    hoehe = page_height or max((float(w.get("bottom", 0)) for w in words), default=0.0)
    return breite, hoehe


def _kandidaten(words: list[dict], breite: float, hoehe: float) -> list[dict]:
    """Betragskandidaten aus der rechten Spalte, Kopfband ausgeschlossen."""
    out: list[dict] = []
    for w in words:
        if float(w.get("x0", 0)) < breite * SPALTE_AB:
            continue
        if float(w.get("top", 0)) < hoehe * KOPFBAND_BIS:
            continue
        betrag = _parse(str(w.get("text", "")))
        if betrag is None or betrag < MIN_BETRAG:
            continue
        out.append({"betrag": betrag, "word": w})
    return out


def _bbox(w: dict) -> list[float]:
    return [float(w["x0"]), float(w["top"]), float(w["x1"]), float(w["bottom"])]


def extract_lohnausweis_layout(
    words: list[dict],
    page_width: float | None = None,
    page_height: float | None = None,
) -> dict[str, Any] | None:
    """Liest Brutto- und Nettolohn geometrisch aus einem Lohnausweis.

    Args:
        words: Wortboxen mit ``text``, ``x0``, ``top``, ``x1``, ``bottom``
            (optional ``page``) — Format von ``pdfplumber.extract_words()``
            bzw. ``_json_to_words()``.
        page_width: Seitenbreite; ohne Angabe aus den Boxen geschätzt.
        page_height: Seitenhöhe; ohne Angabe aus den Boxen geschätzt.

    Returns:
        Dict mit ``bruttolohn_pos8`` und ``nettolohn_pos11`` (je ``value``,
        ``bbox``, ``page``, ``snippet``) plus ``plausibel`` (bool) und
        ``abzuege_summe``. ``None``, wenn die Betragsspalte weniger als zwei
        unterscheidbare Werte enthält — dann bleibt der LLM-Pfad zuständig.
    """
    if not words:
        return None

    breite, hoehe = _seitenmasse(words, page_width, page_height)
    if breite <= 0 or hoehe <= 0:
        return None

    kand = _kandidaten(words, breite, hoehe)
    werte = sorted({k["betrag"] for k in kand}, reverse=True)
    if len(werte) < 2:
        return None

    brutto_wert, netto_wert = werte[0], werte[1]

    # Netto-Zeile: unterste Fundstelle des zweitgrössten Werts. Auf dem Formular
    # steht Ziffer 11 unterhalb der Abzüge.
    netto = max((k for k in kand if k["betrag"] == netto_wert),
                key=lambda k: float(k["word"]["top"]))
    netto_top = float(netto["word"]["top"])

    # Brutto-Zeile: die Fundstelle des grössten Werts, die der Netto-Zeile am
    # nächsten oberhalb liegt. Ziffer 1 (Lohn) trägt oft denselben Betrag wie
    # Ziffer 8 (Bruttolohn) — massgeblich ist die untere, also die Summenzeile.
    oberhalb = [k for k in kand
                if k["betrag"] == brutto_wert and float(k["word"]["top"]) < netto_top]
    if not oberhalb:
        return None
    brutto = max(oberhalb, key=lambda k: float(k["word"]["top"]))
    brutto_top = float(brutto["word"]["top"])

    # Abzüge: alles zwischen Brutto- und Netto-Zeile. Damit fallen die Spesen
    # (Ziffer 13.x, unterhalb der Netto-Zeile) automatisch heraus.
    abzuege = [k["betrag"] for k in kand
               if brutto_top < float(k["word"]["top"]) < netto_top]
    differenz = brutto_wert - netto_wert
    plausibel = abs(sum(abzuege) - differenz) < PLAUSI_TOLERANZ

    def feld(k: dict) -> dict[str, Any]:
        w = k["word"]
        return {
            "value": str(w["text"]).strip().rstrip("."),
            "bbox": _bbox(w),
            "page": w.get("page", 1),
            "snippet": str(w["text"]).strip(),
        }

    return {
        "bruttolohn_pos8": feld(brutto),
        "nettolohn_pos11": feld(netto),
        "plausibel": plausibel,
        "abzuege_summe": sum(abzuege),
        "differenz": differenz,
    }
