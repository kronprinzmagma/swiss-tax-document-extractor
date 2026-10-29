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
# Anteil der kleineren Höhe, um den sich zwei Kästen in y überlappen müssen,
# damit sie trotz verschiedener Mitte auf einer Zeile stehen: die fette
# Total-Zeile (15 pt hoch) neben dem kleinen Betrag (8 pt) hat Mitten 3.5 pt
# auseinander — bisher zwei Zeilen, und der Betrag stand ohne Beschriftung
# da (261025-kme, B1e).
ZEILENUEBERLAPPUNG = 0.6

# Tausendergruppe auch mit Komma („12,345", „1,234.50") — wie
# numbers.parse_swiss_amount; zwei Zahl-Logiken lasen dieselbe Zelle
# verschieden (261025-kme, B1g).
_BETRAG_RE = re.compile(r"^-?\d{1,3}(?:[ '’.,]?\d{3})*(?:[.,]\s?\d{1,2}|\.[-–]{1,2})?$")
_RAPPEN_RE = re.compile(r"(?:[.,]\s?\d{2}|\.[-–]{1,2})$")
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
        """(Zellenindex, Decimal) für jeden Betrag der Zeile, links nach rechts.

        Eine Zelle, die als Ganzes ein Betrag ist, liefert ihn wie bisher.
        Enge Spalten verschmelzen zu einer Zelle („1'000.00 10.00", Lücke
        unter ``ZELLENLUECKE``) — dann trägt jeder Teilbetrag denselben
        Zellenindex, statt dass die Zelle ganz wegfällt (261025-kme, B1e).
        """
        aus = []
        for i, z in enumerate(self.zellen):
            wert = betrag_in(z.text)
            if wert is not None:
                aus.append((i, wert))
                continue
            aus.extend((i, t) for t in _teilbetraege(z.text))
        return aus

    def anker(self) -> dict:
        return {"seite": self.seite, "bbox": list(self.bbox),
                "snippet": self.text[:160]}


def betrag_in(text: str):
    """Ist die ganze Zelle ein Betrag? Dann als Decimal, sonst None.

    Liest wie ``numbers.parse_swiss_amount`` (auch „12,345" und
    „1,234.50"); die einzige Abweichung ist die blosse Jahreszahl, die hier
    kein Betrag ist (261025-kme, B1g).

    Zellen wie ``1'775 .21`` oder ``2 485.01`` kommen zerteilt aus dem PDF;
    Leerzeichen zwischen Zifferngruppen werden deshalb zusammengezogen.
    Prozente und Daten sind keine Beträge — und eine Zelle, die nur eine
    Jahreszahl 1900–2100 ist, auch nicht (261025-hbv: „Prämienverbilligung |
    2025" war sonst eine Verbilligung von 2'025.00). Mit Vorzeichen, Trenner,
    Dezimalstellen oder Währung („CHF 2025", „2'025", „2025.00", „-2025")
    bleibt es ein Betrag.
    """
    roh = text.strip()
    if re.fullmatch(r"\d{4}", roh) and 1900 <= int(roh) <= 2100:
        return None
    t = roh.replace("CHF", "").strip()
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


def _teilbetraege(text: str) -> list:
    """„1'000.00 10.00" → [1000.00, 10.00]; sonst eine leere Liste.

    Teile werden von links gesammelt, bis die Sammlung ein Betrag ist UND
    auf Rappen endet („.50", „, 75", „.--") — so bleibt „1 000.00" ein
    Betrag und wird nicht zu 1 und 0.00. Gilt nur, wenn nichts übrig bleibt
    und mindestens zwei Beträge entstehen; „Total 1'000.00" ist keine
    Betragsfolge (261025-kme, B1e).
    """
    aus, sammlung = [], []
    for teil in text.split():
        sammlung.append(teil)
        kandidat = " ".join(sammlung)
        wert = betrag_in(kandidat)
        if wert is not None and _RAPPEN_RE.search(kandidat):
            aus.append(wert)
            sammlung = []
    if sammlung or len(aus) < 2:
        return []
    return aus


def ibans_in(text: str) -> list[str]:
    return [m.group(1).replace(" ", "") for m in _IBAN_RE.finditer(text)]


def waehrungen_in(text: str) -> list[str]:
    return _WAEHRUNG_RE.findall(text)


def _ist_randtext(w: dict, breite: float) -> bool:
    """Kandidat für senkrechten Randtext (Formularnummern) — kurz, am Rand.

    Ob er wirklich wegfällt, entscheidet ``_randtext_entfernen`` nach dem
    Zeilenbau (261025-kme, B1e).
    """
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


def _randtext_entfernen(gruppen: list[dict], breite: float) -> None:
    """Senkrechter Randtext (Formularnummern) fällt weg, Zeilenanfänge bleiben.

    Kandidat ist wie bisher ein Wort mit höchstens 3 Zeichen links von 6 %
    der Breite. Bisher fiel jeder Kandidat VOR dem Zeilenbau weg — auch
    „1." vor „Musterposten". Am anonymisierten Korpus gezählt (261025-kme,
    B1e): 498 Kandidaten, 307 allein in ihrer Zeile, 101 senkrecht gestapelt
    mit grosser Lücke zum Zeilentext, 90 echte Zeilenanfänge, die bisher
    verloren gingen. Weg fällt ein Kandidat deshalb nur, wenn (a) seine
    Zeile nur aus Kandidaten besteht oder (b) er in einem senkrechten Stapel
    steht und die Lücke zum ersten Wort rechts davon grösser als
    3 × ``ZELLENLUECKE`` ist.
    """
    def kandidat(w: dict) -> bool:
        return _ist_randtext(w, breite)

    alle_k = [w for g in gruppen for w in g["w"] if kandidat(w)]

    def im_stapel(w: dict) -> bool:
        return any(o is not w and abs(float(o["x0"]) - float(w["x0"])) < 2
                   and abs(float(o["top"]) - float(w["top"])) < 10 for o in alle_k)

    for g in gruppen:
        ks = [w for w in g["w"] if kandidat(w)]
        if not ks:
            continue
        rest = [w for w in g["w"] if not kandidat(w)]
        if not rest:
            g["w"] = []
            continue
        weg = []
        for w in ks:
            rechts = [float(o["x0"]) for o in rest if float(o["x0"]) >= float(w["x1"])]
            luecke = (min(rechts) - float(w["x1"])) if rechts else float("inf")
            if im_stapel(w) and luecke > 3 * ZELLENLUECKE:
                weg.append(w)
        g["w"] = [w for w in g["w"] if not any(w is x for x in weg)]


def _ueberlappung(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def baue(json_doc: dict) -> Rohtabelle:
    """Wortboxen eines Dokuments → Rohtabelle (Seite für Seite, oben nach unten).

    Eine Gruppe (Zeile) führt das Mittel von top, bottom und Mitte ihrer
    Wörter nach. Ein Wort kommt zur nächstliegenden Gruppe, deren mittlere
    Mitte näher als ``ZEILENTOLERANZ`` ist ODER deren mittlerer Kasten sich
    mit dem Wort um mindestens ``ZEILENUEBERLAPPUNG`` der kleineren Höhe
    überlappt (261025-kme, B1e). Bisher entschied die Mitte des ersten
    Worts allein.
    """
    tabelle = Rohtabelle(str(json_doc.get("pdf_name") or ""))
    for seite in json_doc.get("pages") or []:
        breite = float(seite.get("width") or 595)
        woerter = [w for w in seite.get("words") or []
                   if str(w.get("text", "")).strip()]
        gruppen: list[dict] = []
        for w in sorted(woerter, key=lambda w: float(w["top"])):
            top, bottom = float(w["top"]), float(w["bottom"])
            mitte = (top + bottom) / 2
            passend = []
            for g in gruppen:
                abstand = abs(g["mitte"] - mitte)
                hoehe = min(bottom - top, g["bottom"] - g["top"])
                ueber = _ueberlappung(g["top"], g["bottom"], top, bottom)
                # Die Überlappung zählt nur, wenn die Mitte jedes Kastens im anderen
                # liegt: eng gesetzte Zeilen und Fussnotenmarken („3'730(1)")
                # überlappen auch, ohne eine Zeile zu sein — am anonymisierten
                # Korpus verschob die blosse Überlappung ganze Spalten.
                if abstand < ZEILENTOLERANZ or (
                        hoehe > 0 and ueber >= ZEILENUEBERLAPPUNG * hoehe
                        and g["top"] <= mitte <= g["bottom"] and top <= g["mitte"] <= bottom):
                    passend.append((abstand, g))
            if passend:
                g = min(passend, key=lambda p: p[0])[1]
                g["w"].append(w)
                n = len(g["w"])
                g["top"] += (top - g["top"]) / n
                g["bottom"] += (bottom - g["bottom"]) / n
                g["mitte"] += (mitte - g["mitte"]) / n
            else:
                gruppen.append({"mitte": mitte, "top": top, "bottom": bottom, "w": [w]})
        _randtext_entfernen(gruppen, breite)
        for g in sorted((g for g in gruppen if g["w"]), key=lambda g: g["mitte"]):
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
