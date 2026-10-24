"""Schritt 1 der Kette: alles aus dem Dokument in eine Tabelle.

der Anwender' Kette (2026-10-24): Dokument vollständig in eine Tabelle → Typ
erkennen → Zusammengehöriges zu Objekten zusammenführen → nachsehen, was das
Formular für diesen Typ verlangt → genau diese Werte übertragen.

Vorher fragte das Modell nur nach einer kleinen Feldliste; was nicht darauf
stand, wurde nie festgehalten und war verloren. Die Rohtabelle hält alles
fest — jede Zeile mit Seite, Lage und Zellen. Fehlt später ein Feld, wird es
hier gesucht, nicht neu extrahiert.

Kein Sprachmodell: die Wortboxen liegen aus `pdf_to_word_json` vor (bei
Scans aus der OCR). Eine Zeile sind Wörter auf gleicher Höhe; eine Zelle
endet an einer Lücke von mehr als ``ZELLENLUECKE`` Punkten.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from extractors.numbers import parse_swiss_amount

ZEILENTOLERANZ = 3.0      # pt: Wortmitten näher beieinander = eine Zeile
ZELLENLUECKE = 12.0       # pt: grössere horizontale Lücke = neue Zelle

_BETRAG_RE = re.compile(r"^-?\d{1,3}(?:[ '’.]?\d{3})*(?:[.,]\s?\d{1,2}|\.[-–]{1,2})?$")
_DATUM_RE = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{2}|\d{4})\b")
_IBAN_RE = re.compile(r"\b((?:CH|LI)\d{2}(?:\s?[0-9A-Z]{4}){0,4}\s?[0-9A-Z]{0,5})\b")
_WAEHRUNG_RE = re.compile(r"\b(CHF|EUR|USD|GBP)\b")
_PROZENT_RE = re.compile(r"\d+(?:[.,]\d+)?\s?%")


@dataclass
class Zelle:
    text: str
    x0: float
    x1: float


@dataclass
class Zeile:
    seite: int
    y: float
    x0: float
    zellen: list[Zelle]
    bbox: tuple[float, float, float, float]

    @property
    def text(self) -> str:
        return " | ".join(z.text for z in self.zellen)

    def betraege(self) -> list[tuple[int, object]]:
        """(Zellenindex, Decimal) für jede Zelle, die ein Betrag ist."""
        aus = []
        for i, z in enumerate(self.zellen):
            wert = betrag_in(z.text)
            if wert is not None:
                aus.append((i, wert))
        return aus

    def anker(self) -> dict:
        return {"seite": self.seite, "bbox": list(self.bbox),
                "snippet": self.text[:160]}


def betrag_in(text: str):
    """Ist die ganze Zelle ein Betrag? Dann als Decimal, sonst None.

    Zellen wie ``1'775 .21`` oder ``2 485.01`` kommen zerteilt aus dem PDF;
    Leerzeichen zwischen Zifferngruppen werden deshalb zusammengezogen.
    Prozente, Daten und Jahreszahlen sind keine Beträge.
    """
    t = text.strip().replace("CHF", "").strip()
    if not t or _PROZENT_RE.search(t) or _DATUM_RE.search(t):
        return None
    # OCR liest das Tausender-Apostroph auch als ° oder ´ („-100°000.00").
    t = re.sub(r"(?<=\d)[°´`](?=\d{3})", "'", t)
    kompakt = re.sub(r"(?<=\d)\s+(?=[\d.])", "", t)
    if not _BETRAG_RE.match(kompakt):
        return None
    try:
        return parse_swiss_amount(kompakt)
    except ValueError:
        return None


def ibans_in(text: str) -> list[str]:
    return [m.group(1).replace(" ", "") for m in _IBAN_RE.finditer(text)]


def waehrungen_in(text: str) -> list[str]:
    return _WAEHRUNG_RE.findall(text)


def _ist_randtext(w: dict, breite: float) -> bool:
    """Senkrechter Randtext (Formularnummern) — Einzelzeichen am Rand."""
    return float(w["x0"]) < breite * 0.06 and len(str(w["text"]).strip()) <= 3


@dataclass
class Rohtabelle:
    dokument: str
    zeilen: list[Zeile] = field(default_factory=list)

    def suche(self, muster: str, ab: int = 0, bis: int | None = None):
        """Zeilen (Index, Zeile), deren Text das Muster enthält."""
        rx = re.compile(muster, re.IGNORECASE)
        ende = len(self.zeilen) if bis is None else bis
        return [(i, z) for i, z in enumerate(self.zeilen[ab:ende], start=ab)
                if rx.search(z.text)]


def baue(json_doc: dict) -> Rohtabelle:
    """Wortboxen eines Dokuments → Rohtabelle (Seite für Seite, oben nach unten)."""
    tabelle = Rohtabelle(str(json_doc.get("pdf_name") or ""))
    for seite in json_doc.get("pages") or []:
        breite = float(seite.get("width") or 595)
        woerter = [w for w in seite.get("words") or []
                   if str(w.get("text", "")).strip()
                   and not _ist_randtext(w, breite)]
        gruppen: list[dict] = []
        for w in sorted(woerter, key=lambda w: float(w["top"])):
            mitte = (float(w["top"]) + float(w["bottom"])) / 2
            for g in gruppen:
                if abs(g["mitte"] - mitte) < ZEILENTOLERANZ:
                    g["w"].append(w)
                    break
            else:
                gruppen.append({"mitte": mitte, "w": [w]})
        for g in sorted(gruppen, key=lambda g: g["mitte"]):
            ws = sorted(g["w"], key=lambda w: float(w["x0"]))
            zellen: list[Zelle] = []
            for w in ws:
                x0, x1 = float(w["x0"]), float(w["x1"])
                if zellen and x0 - zellen[-1].x1 <= ZELLENLUECKE:
                    zellen[-1].text += " " + str(w["text"])
                    zellen[-1].x1 = x1
                else:
                    zellen.append(Zelle(str(w["text"]), x0, x1))
            bbox = (min(float(w["x0"]) for w in ws),
                    min(float(w["top"]) for w in ws),
                    max(float(w["x1"]) for w in ws),
                    max(float(w["bottom"]) for w in ws))
            tabelle.zeilen.append(Zeile(int(seite.get("page_num") or 1),
                                        g["mitte"], bbox[0], zellen, bbox))
    return tabelle
