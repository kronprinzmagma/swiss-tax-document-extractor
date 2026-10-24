#!/usr/bin/env python3
"""Findet eSteuerauszüge (eCH-0196) unter den eigenen Dokumenten.

Warum das wichtig ist: Banken betten die steuerrelevanten Daten als
PDF417-Barcode und/oder als XML-Anhang ins PDF ein — das ist der
schweizweite Standard eCH-0196. ZHprivateTax **importiert solche PDFs
direkt** und übernimmt alle Werte automatisch. Für diese Dokumente braucht es
weder Extraktion noch Abtippen: hochladen genügt.

Und für alles, was doch extrahiert wird, ist der Barcode die perfekte
Gegenprobe — er enthält die Zahlen exakt so, wie die Bank sie meldet.

Das Skript prüft pro PDF drei Dinge:

1. XML-Anhang im PDF (``/EmbeddedFiles``) — der direkte Weg.
2. PDF417-Barcode auf einer der Seiten — der übliche Weg.
3. Textmarker wie „eSteuerauszug" — schwächstes Indiz, aber hilfreich.

Aufruf::

    PYTHONPATH=. .venv/bin/python scripts/esteuerauszug_check.py --input belege

Mit ``--anonym`` werden statt Dateinamen nur Nummern ausgegeben — diese
Fassung ist teilbar.
"""
from __future__ import annotations

import argparse
import io
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Textmarker eines eSteuerauszugs (schwaechstes, aber billigstes Signal).
MARKER_RE = re.compile(r"e-?\s?Steuerauszug|eCH-0196|Tax\s*Statement", re.IGNORECASE)


def xml_anhaenge(pdf_pfad: Path) -> list[str]:
    """Namen eingebetteter Dateien — beim eSteuerauszug meist eine XML."""
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(pdf_pfad))
        namen = list(getattr(reader, "attachments", {}) or {})
        return [n for n in namen if n.lower().endswith((".xml", ".zip"))] or namen
    except Exception:
        return []


def pdf417_gefunden(pdf_pfad: Path, max_seiten: int = 0) -> tuple[bool, int]:
    """Sucht einen PDF417-Barcode. Returns (gefunden, Nutzdaten-Laenge).

    Von hinten: der eSteuerauszug traegt seine Barcodes auf den LETZTEN
    Seiten. Hier wurden nur die ersten drei angesehen — ein Zins- und
    Saldoverzeichnis mit Barcode auf Seite 4 galt so als „ohne Barcode"
    (261024-jga). ``max_seiten=0`` heisst: alle Seiten.
    """
    try:
        import pdfplumber
        import zxingcpp
        from PIL import Image
    except ImportError:
        return False, 0

    try:
        with pdfplumber.open(pdf_pfad) as pdf:
            seiten = list(reversed(pdf.pages))
            for seite in (seiten[:max_seiten] if max_seiten else seiten):
                # 300 dpi: die Barcodes der Banken sind dichter als ein
                # Testcode, bei 200 dpi fielen Module zusammen.
                bild = seite.to_image(resolution=300).original
                if not isinstance(bild, Image.Image):
                    bild = Image.open(io.BytesIO(bild))
                treffer = zxingcpp.read_barcodes(bild)
                for t in treffer:
                    if "PDF417" in str(t.format):
                        return True, len(t.text or "")
    except Exception:
        return False, 0
    return False, 0


def barcode_art(pdf_pfad: Path) -> dict:
    """Was steckt im Barcode? eSteuerauszug oder etwas anderes?

    Ein eSteuerauszug (eCH-0196) ist zlib-komprimiertes XML, verteilt auf
    mehrere PDF417-Segmente (Macro-PDF417, „FileName" = Kennung des
    Auszugs). Ein Barcode mit 15 Zeichen Nutzdaten ist das nicht — die blosse
    Anwesenheit eines PDF417 sagte darüber nichts (261024-jga).

    Gibt nur Zählungen zurück, keine Werte — das Ergebnis ist teilbar.
    """
    import itertools
    import zlib

    try:
        import pdfplumber
        import zxingcpp
    except ImportError:
        return {"art": "nicht prüfbar"}
    segmente, macro = [], False
    try:
        with pdfplumber.open(pdf_pfad) as pdf:
            for seite in reversed(pdf.pages):
                bild = seite.to_image(resolution=300).original
                for t in zxingcpp.read_barcodes(
                        bild, formats=zxingcpp.BarcodeFormat.PDF417):
                    segmente.append(t.bytes)
                    macro |= bool((t.extra or {}).get("FileName"))
    except Exception:      # noqa: BLE001 - ein kaputtes PDF ist kein Treffer
        return {"art": "nicht lesbar"}
    if not segmente:
        return {"art": "kein Barcode"}
    xml = None
    if len(segmente) <= 6:
        for folge in itertools.permutations(segmente):
            try:
                xml = zlib.decompress(b"".join(folge))
                break
            except zlib.error:
                continue
    if xml and (b"taxStatement" in xml or b"eCH-0196" in xml):
        return {"art": "eSteuerauszug (eCH-0196), vollständig gelesen",
                "segmente": len(segmente),
                "konten": xml.count(b"<bankAccount "),
                "wertschriften": xml.count(b"<security "),
                "schulden": xml.count(b"<liabilityAccount ")}
    if macro:
        return {"art": "eSteuerauszug-Barcode, aber nicht alle Teile lesbar",
                "segmente": len(segmente)}
    return {"art": "anderer Barcode — kein eSteuerauszug",
            "segmente": len(segmente)}


def hat_marker(pdf_pfad: Path) -> bool:
    try:
        import pdfplumber
        with pdfplumber.open(pdf_pfad) as pdf:
            for seite in pdf.pages[:2]:
                if MARKER_RE.search(seite.extract_text() or ""):
                    return True
    except Exception:
        pass
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", type=Path, default=Path("belege"),
                    help="Ordner mit den PDFs (Default: ./belege/)")
    ap.add_argument("--anonym", action="store_true",
                    help="Nur Nummern statt Dateinamen ausgeben — teilbar")
    ap.add_argument("--seiten", type=int, default=0,
                    help="Wie viele Seiten je PDF (von hinten) absuchen; 0 = alle")
    args = ap.parse_args()

    ordner = args.input if args.input.is_absolute() else ROOT / args.input
    pdfs = sorted(ordner.glob("*.pdf"))
    if not pdfs:
        print(f"Keine PDFs in {ordner}", file=sys.stderr)
        return 1

    importierbar: list[str] = []
    print(f"{len(pdfs)} PDF(s) geprueft — Suche nach eSteuerauszug (eCH-0196)\n")
    for i, p in enumerate(pdfs, start=1):
        bezeichnung = f"Dokument {i}" if args.anonym else p.name
        anhaenge = xml_anhaenge(p)
        barcode, nutzlast = pdf417_gefunden(p, args.seiten)
        marker = hat_marker(p)
        if not (anhaenge or barcode or marker):
            continue
        teile = []
        if anhaenge:
            teile.append(f"XML-Anhang ({len(anhaenge)})")
        if barcode:
            art = barcode_art(p)
            zusatz = ", ".join(f"{n} {k}" for k, n in art.items()
                               if k != "art")
            teile.append(f"PDF417: {art['art']}" + (f" ({zusatz})" if zusatz else ""))
            barcode = art["art"].startswith("eSteuerauszug")
        if marker:
            teile.append("Textmarker")
        print(f"  ✅ {bezeichnung}")
        print(f"     {' · '.join(teile)}")
        if anhaenge or barcode:
            importierbar.append(bezeichnung)

    print()
    if importierbar:
        print(f"{len(importierbar)} Dokument(e) mit eSteuerauszug bzw. XML-Anhang.")
        print("Diese kannst du in ZHprivateTax direkt hochladen — die Werte werden")
        print("automatisch uebernommen, ohne Abtippen und ohne Extraktion.")
    else:
        print("Keine maschinenlesbaren Steuerdaten gefunden.")
        print("Deine Institute liefern (noch) keinen eSteuerauszug mit Barcode.")
        print("Tipp: im E-Banking gibt es den oft als separaten Download neben")
        print("dem klassischen PDF — dann lohnt es sich, den zu holen.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
