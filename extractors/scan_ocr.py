"""OCR für Scan-Seiten — so, dass grau hinterlegte Zeilen lesbar werden.

Die Textebene, die ein Scanner mitliefert, lässt Zeilen mit gerasterter
grauer Hinterlegung aus. Bei den Ausweisen der Bezirks-Sparkasse sind das
genau die wichtigen: „Buchsaldo am 31.12.", „Rechnungsbetrag" (= der Zins),
der Betrag neben „Habenzins" (261024-jga, an den Originalen geprüft).
``ocrmypdf --force-ocr`` holte sie nicht zuverlässig und las einen Betrag
mit einer Ziffer zu wenig — falsch statt fehlend, also schlimmer.

Was hilft: die Seite mit 300 dpi rendern, das Raster mit einem 3×3-Median
glätten, bei Grauwert 170 schwarz-weiss machen, dann Tesseract (deutsch,
ein Textblock). Höhere Auflösung verschlechterte es (das Raster schlägt
durch, ein Apostroph wurde zur 7). Alles lokal.
"""
from __future__ import annotations

import csv
import io
import shutil
import subprocess
import tempfile
from pathlib import Path

DPI = 300
MEDIAN = 3
SCHWELLE = 170


def verfuegbar() -> bool:
    return shutil.which("tesseract") is not None


def seite_lesen(page) -> list[dict]:
    """Eine pdfplumber-Seite → Wortboxen in PDF-Punkten (wie extract_words)."""
    from PIL import ImageFilter

    bild = page.to_image(resolution=DPI).original.convert("L")
    bild = bild.filter(ImageFilter.MedianFilter(MEDIAN)).point(
        lambda v: 255 if v > SCHWELLE else 0)
    faktor = 72.0 / DPI
    with tempfile.TemporaryDirectory() as td:
        pfad = Path(td) / "seite.png"
        bild.save(pfad)
        proc = subprocess.run(
            ["tesseract", str(pfad), "-", "-l", "deu", "--psm", "6", "tsv"],
            capture_output=True, text=True, timeout=180)
    if proc.returncode != 0:
        return []
    woerter = []
    for z in csv.DictReader(io.StringIO(proc.stdout), delimiter="\t",
                            quoting=csv.QUOTE_NONE):
        text = (z.get("text") or "").strip()
        if not text or z.get("level") != "5":
            continue
        try:
            if float(z.get("conf") or -1) < 0:
                continue
            x0, top = float(z["left"]) * faktor, float(z["top"]) * faktor
            x1 = x0 + float(z["width"]) * faktor
            bottom = top + float(z["height"]) * faktor
        except (TypeError, ValueError):
            continue
        woerter.append({"text": text, "x0": x0, "top": top,
                        "x1": x1, "bottom": bottom})
    return woerter
