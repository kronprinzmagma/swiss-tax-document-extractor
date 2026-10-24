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


def _ist_scanseite(page) -> bool:
    """Trägt die Seite ein seitenfüllendes Bild — ist sie ein Scan?"""
    flaeche = float(page.width) * float(page.height) or 1.0
    for bild in page.images or []:
        b = (float(bild["x1"]) - float(bild["x0"])) * (float(bild["bottom"]) - float(bild["top"]))
        if b >= 0.6 * flaeche:
            return True
    return False


def _zahlwoerter(words: list[dict]) -> int:
    return sum(1 for w in words if any(c.isdigit() for c in str(w.get("text", ""))))


def _lesbarkeit(words: list[dict]) -> int:
    """Wie gut ist eine Zeile lesbar? Saubere Zahlen zählen, Müll kostet.

    „-100'Obo:00" enthält Ziffern, ist aber kein Betrag — die Zeile mit
    „-100'000.00" ist die bessere, auch wenn beide gleich viele Ziffern-
    wörter haben.
    """
    import re as _re
    from extractors.rohtabelle import betrag_in
    gut = 0
    for w in words:
        t = str(w.get("text", ""))
        if betrag_in(t) is not None or _re.fullmatch(r"\d{1,2}\.\d{1,2}\.\d{2,4}", t):
            gut += 2
        elif _re.search(r"\d[A-Za-z:{}°`~]|[A-Za-z:{}°`~]\d", t) and not \
                _re.fullmatch(r"(CH|LI)\d{2}|[A-Z]{2,}\d+", t):
            gut -= 1           # Ziffern mit Buchstaben vermengt: „Obo:00", „24.i2"
        if _re.search(r"[{}`°~\\]|(?<=\w):(?=\w)", t):
            gut -= 1
    return gut


def _zeilenweise(words: list[dict]) -> list[list[dict]]:
    zeilen: list[dict] = []
    for w in sorted(words, key=lambda w: float(w["top"])):
        mitte = (float(w["top"]) + float(w["bottom"])) / 2
        for z in zeilen:
            if abs(z["mitte"] - mitte) < 4:
                z["w"].append(w)
                break
        else:
            zeilen.append({"mitte": mitte, "w": [w]})
    return [(z["mitte"], z["w"]) for z in zeilen]


def bessere_seiten(original: list[dict], ocr: list[dict],
                   scan: list[bool]) -> tuple[list[dict], list[int]]:
    """Scan-Seiten zeilenweise aus Textebene und OCR zusammensetzen.

    Die Textebene des Scanners lässt grau hinterlegte Zeilen aus (Buchsaldo,
    Rechnungsbetrag), liest aber den Briefkopf oft besser als Tesseract.
    Deshalb je Zeile: hat nur das OCR sie, kommt sie dazu; haben beide sie,
    gilt die Fassung mit mehr Zahlen, bei Gleichstand die Textebene
    (261024-jga, an den Originalen der Bezirks-Sparkasse geprüft).
    """
    aus, ersetzt = [], []
    for i, seite in enumerate(original):
        neu = ocr[i] if i < len(ocr) else None
        if not scan[i] or neu is None or not neu["words"]:
            aus.append(seite)
            continue
        orig_z = _zeilenweise(seite["words"])
        woerter: list[dict] = []
        benutzt = set()
        geaendert = False
        for y, ow in _zeilenweise(neu["words"]):
            nah = [(abs(oy - y), j) for j, (oy, _) in enumerate(orig_z)
                   if j not in benutzt and abs(oy - y) < 5]
            treffer = min(nah)[1] if nah else None
            if treffer is None:
                woerter += ow
                geaendert = True
                continue
            benutzt.add(treffer)
            rw = orig_z[treffer][1]
            if _lesbarkeit(ow) > _lesbarkeit(rw):
                woerter += ow
                geaendert = True
            else:
                woerter += rw
        woerter += [w for j, (_, rw) in enumerate(orig_z) if j not in benutzt
                    for w in rw]
        aus.append({**seite, "words": woerter})
        if geaendert:
            ersetzt.append(seite["page_num"])
    return aus, ersetzt


def pdf_to_json(pdf_path: Path, _ocr_attempted: bool = False) -> dict | None:
    """Liest ein PDF und liefert die Word-JSON-Struktur (oder None).

    Hat das PDF keine Textebene (reiner Scan), wird einmalig ocrmypdf
    angewendet und erneut gelesen. ``_ocr_attempted`` verhindert Rekursion.

    Struktur identisch zu den anonymisierten Sample-JSONs:
    ``{pdf_name, belegtyp, pages:[{page_num, width, height, words:[{text,x0,top,x1,bottom}]}]}``
    """
    pages_out: list[dict] = []
    all_words: list[dict] = []
    scan: list[bool] = []
    with pdfplumber.open(pdf_path) as pdf:
        for idx, page in enumerate(pdf.pages, start=1):
            scan.append(_ist_scanseite(page))
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

    # Scan mit unvollständiger Textebene: jede Scan-Seite selbst per OCR
    # lesen (extractors/scan_ocr.py), pro Seite das Bessere behalten.
    ocr_seiten: list[int] = []
    from extractors import scan_ocr
    if any(scan) and not _ocr_attempted and scan_ocr.verfuegbar():
        with pdfplumber.open(pdf_path) as pdf:
            ocr_pages = [{"page_num": i + 1,
                          "words": scan_ocr.seite_lesen(seite) if scan[i] else []}
                         for i, seite in enumerate(pdf.pages)]
        pages_out, ocr_seiten = bessere_seiten(pages_out, ocr_pages, scan)
        all_words = [{**w, "page": p["page_num"]}
                     for p in pages_out for w in p["words"]]
        if ocr_seiten:
            print(f"   OCR: {len(ocr_seiten)} Scan-Seite(n) neu gelesen.",
                  file=sys.stderr)

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
        **({"ocr_seiten": ocr_seiten} if ocr_seiten else {}),
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

    # Alle Unterordner, ohne irrelevant/ (extractors/belegordner.py).
    from extractors.belegordner import alle_pdfs, doppelte_namen, ordner_von
    pdfs = alle_pdfs(input_dir)
    # Der Dateiname ist die Kennung (Word-JSON, Ablage, Korrekturen). Zwei
    # gleichnamige Dateien in verschiedenen Unterordnern wuerden sich still
    # ueberschreiben — also abbrechen und sagen, welche.
    doppelt = doppelte_namen(input_dir)
    if doppelt:
        print(f"FEHLER: {len(doppelt)} Dateiname(n) kommen in mehreren "
              "Unterordnern vor — bitte eindeutig benennen:", file=sys.stderr)
        for name, pfade in sorted(doppelt.items()):
            orte = ", ".join(str(p.parent.relative_to(input_dir)) for p in pfade)
            print(f"  {name}: {orte}", file=sys.stderr)
        return 1
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
        # Der Unterordner ist Vorgabe des Menschen: er sagt, was für ein
        # Dokument es ist (Konten, Wertschriften, …) — 261024-jga.
        data["ordner"] = ordner_von(pdf, input_dir)
        out_stem = safe_filename(pdf.stem) or f"pdf_{success}"
        out_path = output_dir / f"{out_stem}.json"
        out_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        success += 1

    print(f"Fertig: {success}/{len(pdfs)} Word-JSONs geschrieben → {output_dir}")
    return 0 if success > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
