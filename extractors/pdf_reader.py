"""PDF-Ingestion: pdfplumber-Wrapper plus OCR-Fallback-Hook.

Liefert Wort-Listen mit BBoxes und 1-basiertem `page`-Schlüssel pro Seite.
OCR-Fallback (ocrmypdf+tesseract) ist Phase 4 — Phase 1 markiert leeren
Text-Layer mit `NotImplementedError` und Marker `ocr_failed` (D-D3 / RESEARCH §9).
"""
from __future__ import annotations

from pathlib import Path

import pdfplumber


def build_ocr_command(
    input_pdf,
    output_pdf,
    *,
    force: bool = False,
    language: str = "deu",
) -> list[str]:
    """Konstruiert das ocrmypdf-Argv für den Scan-Fallback (R9, 260612-m8t).

    Scan-PDFs liegen oft rotiert vor — ``--rotate-pages`` korrigiert die
    Seiten-Orientierung automatisch (mit konservativer Konfidenz-Schwelle, damit
    aufrechte Seiten nicht fälschlich gedreht werden). Reine Kommando-
    konstruktion: KEIN echter OCR-Aufruf hier (der bleibt Phase-4-Stub).

    Args:
        input_pdf: Pfad zur Eingabe-PDF (Path oder str).
        output_pdf: Pfad zur OCR-Ausgabe-PDF (Path oder str).
        force: Wenn True, ``--force-ocr`` ergänzen (re-OCR auch bei vorhandenem
            Text-Layer). Bewusst NICHT zusammen mit ``--redo-ocr``/``--skip-text``
            — diese Flags schliessen sich gegenseitig aus.
        language: tesseract-Sprachcode (Default ``deu`` für deutsche Belege).

    Returns:
        Das ocrmypdf-Kommando als Argv-Liste (Input/Output am Ende).
    """
    cmd: list[str] = [
        "ocrmypdf",
        "--rotate-pages",
        "--rotate-pages-threshold", "6",
        "-l", language,
    ]
    if force:
        # --force-ocr: rastert die Seite und OCRt alles neu (auch bei
        # vorhandenem Text). Konfliktfrei — ohne --redo-ocr/--skip-text.
        cmd.append("--force-ocr")
    cmd.extend([str(input_pdf), str(output_pdf)])
    return cmd


def read_pdf(pdf_path: Path) -> list[dict]:
    """Liest alle Seiten via `pdfplumber.extract_words` und ergänzt 'page' (1-basiert).

    Args:
        pdf_path: Pfad zum PDF.
    Returns:
        Liste von Wort-Dicts mit Schlüsseln `page, x0, top, x1, bottom, text` (mind.).
    Raises:
        FileNotFoundError: PDF existiert nicht.
        NotImplementedError: Text-leeres PDF; Marker `ocr_failed` für CLI-Routing
            in Wave 4 (Phase-4-Hook für ocrmypdf-Fallback).
    """
    if not pdf_path.exists():
        raise FileNotFoundError(pdf_path)

    all_words: list[dict] = []
    with pdfplumber.open(pdf_path) as pdf:
        for idx, page in enumerate(pdf.pages, start=1):
            # `use_text_flow=True` erhält die Lese-Reihenfolge bei Spalten/Tabellen
            # (RESEARCH §Pitfall 3). `keep_blank_chars=False` filtert Whitespace-only-Tokens.
            page_words = page.extract_words(use_text_flow=True, keep_blank_chars=False)
            for w in page_words:
                w["page"] = idx
            all_words.extend(page_words)

    if not all_words:
        # Phase-1-Stub für OCR-Fallback (RESEARCH §9, ING-03 Phase-1-Partial).
        raise NotImplementedError(
            f"ocr_failed: {pdf_path.name} liefert 0 Wörter aus Text-Layer. "
            "OCR-Fallback (ocrmypdf+tesseract) ist Phase 4."
        )

    return all_words
