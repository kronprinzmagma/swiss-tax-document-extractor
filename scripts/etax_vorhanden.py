#!/usr/bin/env python3
"""eSteuerauszüge der Banken unter den Belegen — kopieren statt selbst bauen.

der Anwender (261120): „Wenn ein Beleg schon als eSteuerauszug vorliegt, möchte ich,
dass du ihn von den Belegen rüberkopierst und nicht selber erstellst."

Der Auszug der Bank ist die bessere Quelle: er enthält die Werte so, wie die
Bank sie meldet, mit allen Feldern, die ZHprivateTax liest. Ein eigener
Auszug für dieselben Konten wäre im besten Fall eine Kopie, im schlechtesten
ein Widerspruch — und importiert würden beide.

Gelesen wird der Inhalt, nicht der Dateiname: der XML-Anhang des PDFs oder
der PDF417-Barcode (zlib-komprimiertes eCH-0196). Daraus die Kontonummern
(``iban``, ``bankAccountNumber``, ``depotNumber``), die Zahl der Schulden
und das Steuerjahr (``periodTo``). Konten der Leiste mit derselben Nummer
baut ``etax_bauen`` nicht mehr; das PDF der Bank wird unverändert kopiert.

Das Lesen kostet (jede Seite als Bild): das Ergebnis liegt je Datei
(Grösse + Änderungszeit) in einem Zwischenspeicher im Ausgabeordner.
"""
from __future__ import annotations

import itertools
import json
import re
import zlib
from pathlib import Path

ZWISCHENSPEICHER = "etax_vorhanden.json"
#: Ändert sich, was gelesen wird, wird jeder Eintrag neu gelesen.
LESART = 2


def norm(nummer) -> str:
    """Nummer ohne Leerzeichen und Trenner, gross — zum Vergleichen."""
    return re.sub(r"[^0-9A-Z]", "", str(nummer or "").upper())


def _aus_anhang(pfad: Path) -> bytes | None:
    try:
        from pypdf import PdfReader
        anhaenge = PdfReader(str(pfad)).attachments or {}
    except Exception:      # noqa: BLE001 - kein Anhang lesbar
        return None
    for name, inhalte in anhaenge.items():
        if name.lower().endswith(".xml"):
            for inhalt in inhalte:
                if b"taxStatement" in inhalt:
                    return inhalt
    return None


def _aus_barcode(pfad: Path) -> bytes | bool | None:
    """Segmente in Seitenreihenfolge, sonst (bei wenigen) durchprobieren.

    ``False``: PDF417 da, aber kein eSteuerauszug daraus lesbar — das wird
    gemeldet, sonst baute etax_bauen still einen eigenen.
    """
    try:
        import pdfplumber
        import zxingcpp
    except ImportError:
        return None
    segmente: list[bytes] = []
    macro = False
    try:
        with pdfplumber.open(pfad) as pdf:
            for seite in pdf.pages:
                bild = seite.to_image(resolution=300).original
                treffer = zxingcpp.read_barcodes(bild, formats=zxingcpp.BarcodeFormat.PDF417)
                # Oben vor unten, links vor rechts — die Lesereihenfolge.
                treffer = sorted(treffer, key=lambda t: (t.position.top_left.y,
                                                         t.position.top_left.x))
                segmente += [t.bytes for t in treffer]
                # Macro-PDF417 mit Dateiname: das Kennzeichen des eSteuerauszugs.
                macro |= any((t.extra or {}).get("FileName") for t in treffer)
    except Exception:      # noqa: BLE001 - ein kaputtes PDF ist kein Auszug
        return None
    if not segmente:
        return None
    folgen = [segmente, segmente[::-1]]
    if len(segmente) <= 6:
        folgen += list(itertools.permutations(segmente))
    for folge in folgen:
        try:
            xml = zlib.decompress(b"".join(folge))
        except zlib.error:
            continue
        if b"taxStatement" in xml:
            return xml
    # Ein anderer PDF417 (Versandetikett, Formularcode) ist kein Auszug und
    # kein Befund; „unlesbar" nur, wenn er wie ein eSteuerauszug aussieht.
    return _schrittweise(segmente) or (False if macro else None)


def _schrittweise(segmente: list[bytes]) -> bytes | None:
    """Viele Teile in unbekannter Reihenfolge: Stück für Stück anfügen.

    zlib meldet falsche Daten sofort. Je Schritt wird das Teil angefügt, mit
    dem der Strom gültig weiterläuft — bei Bankauszügen mit mehr als sechs
    Teilen wäre Durchprobieren aller Reihenfolgen zu teuer.
    """
    def weiter(obj, rest, bisher):
        if not rest:
            return bisher if obj.eof else None
        for i, teil in enumerate(rest):
            kopie = obj.copy()
            try:
                stueck = kopie.decompress(teil)
            except zlib.error:
                continue
            ergebnis = weiter(kopie, rest[:i] + rest[i + 1:], bisher + stueck)
            if ergebnis is not None:
                return ergebnis
        return None

    for i, erstes in enumerate(segmente):
        obj = zlib.decompressobj()
        try:
            anfang = obj.decompress(erstes)
        except zlib.error:
            continue
        xml = weiter(obj, segmente[:i] + segmente[i + 1:], anfang)
        if xml and b"taxStatement" in xml:
            return xml
    return None


def lies(xml: bytes) -> dict:
    """Was im Auszug steht — Nummern, Zahl der Schulden, Steuerjahr."""
    text = xml.decode("utf-8", errors="replace")
    konten = set()
    for tag in re.findall(r"<(?:\w+:)?(?:bankAccount|depot)\s[^>]*>", text):
        for attr in ("iban", "bankAccountNumber", "depotNumber"):
            m = re.search(rf'\b{attr}="([^"]+)"', tag)
            if m and norm(m.group(1)):
                konten.add(norm(m.group(1)))
    bis = re.search(r'\bperiodTo="(\d{4})', text)
    return {"jahr": bis.group(1) if bis else "",
            "konten": sorted(konten),
            "schulden": len(re.findall(r"<(?:\w+:)?liabilityAccount\s", text))}


def finde(pdf_dir: Path, speicher: Path | None = None) -> list[dict]:
    """Alle eSteuerauszüge im Belegordner (ohne ``irrelevant/``)."""
    pdf_dir = Path(pdf_dir)
    alt: dict = {}
    if speicher and speicher.exists():
        try:
            alt = json.loads(speicher.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            alt = {}
    neu: dict = {}
    aus = []
    for pfad in sorted(pdf_dir.rglob("*.pdf")):
        rel = pfad.relative_to(pdf_dir)
        if any(t.lower() == "irrelevant" for t in rel.parts[:-1]):
            continue
        st = pfad.stat()
        stempel = f"{st.st_size}:{int(st.st_mtime)}"
        eintrag = alt.get(str(rel))
        if not eintrag or eintrag.get("stempel") != stempel or eintrag.get("lesart") != LESART:
            xml = _aus_anhang(pfad) or _aus_barcode(pfad)
            if xml:
                eintrag = {"stempel": stempel, "lesart": LESART, **lies(xml)}
            else:
                eintrag = {"stempel": stempel, "lesart": LESART, "kein_auszug": True,
                           "unlesbar": xml is False}
        neu[str(rel)] = eintrag
        if eintrag.get("unlesbar"):
            aus.append({"pdf": str(pfad), "unlesbar": True, "jahr": "", "konten": [],
                        "schulden": 0})
        elif not eintrag.get("kein_auszug"):
            aus.append({"pdf": str(pfad), **{k: v for k, v in eintrag.items()
                                             if k not in ("stempel", "lesart")}})
    if speicher:
        speicher.write_text(json.dumps(neu, ensure_ascii=False, indent=1), encoding="utf-8")
    return aus


def decke_ab(institute: list[dict], bankauszuege: list[dict]) -> tuple[list[dict], list[dict]]:
    """Konten, die ein Auszug der Bank schon führt, fallen aus dem Bau.

    Schulden eines Instituts fallen nur weg, wenn ein Auszug, der Konten
    dieses Instituts abdeckt, mindestens ebenso viele Schulden führt — sonst
    fehlten sie, ohne dass es jemand merkt. Ein Institut ohne Rest wird nicht
    gebaut. Rückgabe: die verbleibenden Institute und je Bankauszug, was er
    abdeckt (auch die ohne Gegenstück in der Leiste: kopiert werden alle).
    """
    vorhanden = [{**b, "abgedeckt_konten": 0, "abgedeckt_schulden": 0} for b in bankauszuege]
    bleiben = []
    for h in institute:
        rest_konten, treffer = [], []
        for k in h.get("konten") or []:
            b = next((b for b in vorhanden if norm(k.get("nummer")) and
                      norm(k.get("nummer")) in b["konten"]), None)
            if b is None:
                rest_konten.append(k)
            else:
                b["abgedeckt_konten"] += 1
                treffer.append(b)
        schulden = list(h.get("schulden") or [])
        b_schuld = next((b for b in treffer
                         if schulden and b["schulden"] - b["abgedeckt_schulden"] >= len(schulden)),
                        None)
        if b_schuld is not None:
            b_schuld["abgedeckt_schulden"] += len(schulden)
            schulden = []
        if rest_konten or schulden:
            bleiben.append({**h, "konten": rest_konten, "schulden": schulden})
    return bleiben, vorhanden
