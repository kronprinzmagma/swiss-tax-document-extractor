#!/usr/bin/env python3
"""Neutraler lokaler PDF→Word-JSON-Schritt für den Produktivpfad.

Liest PDFs aus einem Input-Ordner (default ``belege/``) und schreibt pro PDF
ein JSON mit Wort-BBoxes — **ohne Anonymisierung, ohne Redaktion, ohne
externe Calls**. Das Output-JSON hat dieselbe Struktur wie die anonymisierten
Sample-JSONs, sodass die validierte Fokus-Pipeline
(``process_samples_full.py`` → ``build_tax_output.py``) unverändert darauf
läuft.

Unterschied zu ``anonymize_belege.py``:
- KEINE Personen-/Firmen-/Betrags-Ersetzung
- KEINE PII-Maskierung
- Original-Text bleibt erhalten (Output ist lokal, gitignored)

Privacy-Modell: Der erzeugte Output enthält echte Werte und gehört in
``output/`` (gitignored). Er verlässt das Gerät nicht.

Aufruf::

    python scripts/pdf_to_word_json.py --input belege --output output/latest/json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pdfplumber


def pdf_fingerabdruck(pdf: Path) -> str:
    """Kennzeichen eines PDFs — Groesse und Aenderungszeit genuegen.

    Steht hier und nicht beim Aufrufer, damit Erzeugung und Abgleich
    dieselbe Formel benutzen. Laufen sie auseinander, wird bei jedem Lauf
    alles neu eingelesen, ohne dass es jemand merkt.
    """
    st = pdf.stat()
    return f"{st.st_size}:{int(st.st_mtime)}"

from extractors.classifier import classify
from extractors.pdf_reader import build_ocr_command

# ocrmypdf auf einem mehrseitigen Scan braucht Zeit; grosszuegig, aber endlich,
# damit ein einzelnes Dokument nie den ganzen Lauf blockiert.
OCR_TIMEOUT_S = 300


def assert_local_only() -> None:
    """Bricht ab, wenn ein Cloud-Engine-Pfad angefordert wird.

    Dieser Schritt liest ausschließlich lokal und ruft NIE eine Cloud-API.
    Der Guard verhindert, dass der Produktivpfad versehentlich gegen eine
    Cloud-Engine konfiguriert wird.
    """
    engine = os.environ.get("STEUER_ENGINE", "").strip().lower()
    if engine == "cloud":
        print(
            "ABBRUCH: STEUER_ENGINE=cloud ist im lokalen Produktivpfad nicht "
            "erlaubt (Privacy-Constraint: PDFs verlassen das Gerät nicht).",
            file=sys.stderr,
        )
        raise SystemExit(2)


def safe_filename(name: str) -> str:
    """Spiegelt ``anonymize_belege.safe_filename`` — Umlaute/Sonderzeichen → ASCII."""
    norm = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^A-Za-z0-9._-]+", "_", norm).strip("_")


def pdf_to_json(pdf_path: Path, _ocr_attempted: bool = False) -> dict | None:
    """Liest ein PDF und liefert die Word-JSON-Struktur (oder None).

    Hat das PDF keine Textebene (reiner Scan), wird einmalig ocrmypdf
    angewendet und erneut gelesen. ``_ocr_attempted`` verhindert Rekursion.

    Struktur identisch zu den anonymisierten Sample-JSONs:
    ``{pdf_name, belegtyp, pages:[{page_num, width, height, words:[{text,x0,top,x1,bottom}]}]}``
    """
    pages_out: list[dict] = []
    all_words: list[dict] = []
    with pdfplumber.open(pdf_path) as pdf:
        for idx, page in enumerate(pdf.pages, start=1):
            # Gleiche Leselogik wie pdf_reader/anonymizer (Lese-Reihenfolge erhalten)
            page_words = page.extract_words(
                use_text_flow=True, keep_blank_chars=False
            )
            words_out = []
            for w in page_words:
                wo = {
                    "text": w["text"],
                    "x0": float(w["x0"]),
                    "top": float(w["top"]),
                    "x1": float(w["x1"]),
                    "bottom": float(w["bottom"]),
                }
                words_out.append(wo)
                # für Klassifikation: flache Liste mit page
                all_words.append({**wo, "page": idx})
            pages_out.append({
                "page_num": idx,
                "width": float(page.width),
                "height": float(page.height),
                "words": words_out,
            })

    if not all_words:
        # Scan-PDF ohne Text-Layer. Frueher wurde hier uebersprungen — das
        # verschluckte stillschweigend jeden nicht vor-OCRten Scan. ocrmypdf
        # laeuft lokal (Privacy-Constraint erfuellt); das Ergebnis landet in
        # einem temporaeren Verzeichnis und wird danach geloescht, damit keine
        # zusaetzliche Kopie der Originaldokumente liegen bleibt.
        if not _ocr_attempted and shutil.which("ocrmypdf"):
            print("   OCR: ein Dokument hat keine Textebene — starte ocrmypdf …",
                  file=sys.stderr)
            with tempfile.TemporaryDirectory() as td:
                ocr_pdf = Path(td) / "ocr.pdf"
                try:
                    proc = subprocess.run(
                        build_ocr_command(pdf_path, ocr_pdf),
                        capture_output=True, timeout=OCR_TIMEOUT_S,
                    )
                    rc = proc.returncode
                except subprocess.TimeoutExpired:
                    rc = -1
                    print(f"   WARN: OCR-Timeout nach {OCR_TIMEOUT_S}s", file=sys.stderr)
                if rc == 0 and ocr_pdf.exists():
                    doc = pdf_to_json(ocr_pdf, _ocr_attempted=True)
                    if doc is not None:
                        # Identitaet des Originals behalten — der Temp-Name
                        # darf nicht in die Tabelle wandern.
                        doc["pdf_name"] = pdf_path.name
                        doc["pdf_fingerabdruck"] = pdf_fingerabdruck(pdf_path)
                        doc["ocr_applied"] = True
                        print("   OCR: erfolgreich nachgelesen.",
                              file=sys.stderr)
                        return doc
                print(
                    f"   WARN: OCR lieferte fuer ein Dokument keinen Text "
                    f"(rc={rc}) — uebersprungen",
                    file=sys.stderr,
                )
                return None
        print(
            f"   WARN: ein Dokument liefert 0 Wörter (Scan-PDF, ocrmypdf "
            f"nicht verfügbar) — übersprungen. Welches: make dokumente",
            file=sys.stderr,
        )
        return None

    # Lokale Klassifikation (best effort; None wenn nicht erkennbar)
    try:
        belegtyp, _reason = classify(all_words)
    except Exception:
        belegtyp = None

    return {
        "pdf_name": pdf_path.name,
        "pdf_fingerabdruck": pdf_fingerabdruck(pdf_path),
        "belegtyp": belegtyp,
        "pages": pages_out,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Neutraler lokaler PDF→Word-JSON-Schritt (keine Anonymisierung)"
    )
    parser.add_argument(
        "--input", type=Path, default=Path("belege"),
        help="Ordner mit PDFs (Default: ./belege/)",
    )
    parser.add_argument(
        "--output", type=Path, default=ROOT / "output" / "latest" / "json",
        help="Output-Ordner für Word-JSONs (Default: output/latest/json)",
    )
    args = parser.parse_args()

    assert_local_only()

    input_dir = args.input if args.input.is_absolute() else ROOT / args.input
    output_dir = args.output if args.output.is_absolute() else ROOT / args.output
    if not input_dir.is_dir():
        print(f"FEHLER: {input_dir} existiert nicht oder ist kein Verzeichnis", file=sys.stderr)
        return 1
    output_dir.mkdir(parents=True, exist_ok=True)

    pdfs = sorted(input_dir.glob("*.pdf"))
    if not pdfs:
        print(f"FEHLER: keine PDFs in {input_dir}", file=sys.stderr)
        return 1

    print(f"PDF→Word-JSON: {len(pdfs)} PDF(s) aus {input_dir} → {output_dir}")
    success = 0
    for pdf in pdfs:
        try:
            data = pdf_to_json(pdf)
        except Exception as e:
            print(f"   FEHLER bei {pdf.name}: {type(e).__name__}", file=sys.stderr)
            continue
        if data is None:
            continue
        out_stem = safe_filename(pdf.stem) or f"pdf_{success}"
        out_path = output_dir / f"{out_stem}.json"
        out_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        success += 1

    print(f"Fertig: {success}/{len(pdfs)} Word-JSONs geschrieben → {output_dir}")
    return 0 if success > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
