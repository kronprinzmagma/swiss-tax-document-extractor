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
    """Kennzeichen eines PDFs — Groesse und Aenderungszeit beim Einlesen.

    Steht hier und nicht beim Aufrufer, damit Erzeugung und Abgleich
    dieselbe Formel benutzen. Laufen sie auseinander, wird bei jedem Lauf
    alles neu eingelesen, ohne dass es jemand merkt.

    Der Fingerabdruck ist die Dokumentkennung der Ablage
    (``extractors/ablage.py`` ``namen_je_fingerabdruck``, Spalte
    ``dokument_id``): darüber findet eine geprüfte Zeile ihr umbenanntes
    Dokument. Format und Wert NIE ändern (261025-kme, B4c) — ob sich ein PDF
    geändert hat, entscheidet ``pdf_inhalt``.
    """
    st = pdf.stat()
    return f"{st.st_size}:{int(st.st_mtime)}"


def pdf_inhalt(pdf: Path) -> str:
    """„sha256:<hex>" über die Bytes des PDFs, gestreamt (1 MiB).

    Entscheidet, ob ein PDF neu eingelesen werden muss: eine Kopie ohne
    ``-p`` (neue Zeit) ist dasselbe Dokument, ein anderes PDF gleicher Grösse
    und Zeit nicht (261025-kme, B4c).
    """
    import hashlib
    h = hashlib.sha256()
    with open(pdf, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return "sha256:" + h.hexdigest()


# Was das Einlesen bestimmt. Nicht der Klassifikator: der Belegtyp im
# Word-JSON ist nur ein Hinweis, process_samples_full klassifiziert selbst.
EINLESE_DATEIEN = (
    ROOT / "scripts" / "pdf_to_word_json.py",
    ROOT / "extractors" / "scan_ocr.py",
    ROOT / "extractors" / "pdf_reader.py",
)


def einlese_stand(dateien=None) -> str:
    """16 Zeichen SHA-256 über Name und Bytes der Einlese-Module.

    Ändert sich das Einlesen (OCR, Zusammensetzen der Scan-Seiten), wird ein
    Word-JSON einmal neu gelesen — sonst bliebe ein alter Stand liegen
    (261025-kme, B4b). Fehlende Datei geht als Name + „<fehlt>" ein.
    """
    import hashlib
    h = hashlib.sha256()
    for d in (EINLESE_DATEIEN if dateien is None else dateien):
        d = Path(d)
        h.update(d.name.encode())
        h.update(d.read_bytes() if d.exists() else b"<fehlt>")
    return h.hexdigest()[:16]


def neu_einlesen_noetig(alt: dict, pdf: Path, stand: str) -> str | None:
    """Warum ein vorhandenes Word-JSON neu gelesen werden muss — oder None.

    ``neu`` (kein JSON), ``name`` (anderes PDF unter diesem Namen),
    ``altes_format`` (vor 261025-kme, nur size:mtime), ``inhalt`` (Grösse als
    Schnellprüfung, sonst SHA-256), ``stand`` (Einlesen hat sich geändert).
    """
    if not alt:
        return "neu"
    if alt.get("pdf_name") != pdf.name:
        return "name"
    if not alt.get("pdf_inhalt"):
        return "altes_format"
    if alt.get("pdf_groesse") != pdf.stat().st_size:
        return "inhalt"
    if alt.get("pdf_inhalt") != pdf_inhalt(pdf):
        return "inhalt"
    if alt.get("einlese_stand") != stand:
        return "stand"
    return None


def _stempel(doc: dict, pdf: Path) -> dict:
    """Kennung, Inhalt, Grösse und Einlese-Stand des Originals ins Word-JSON."""
    doc["pdf_fingerabdruck"] = pdf_fingerabdruck(pdf)
    doc["pdf_inhalt"] = pdf_inhalt(pdf)
    doc["pdf_groesse"] = pdf.stat().st_size
    doc["einlese_stand"] = einlese_stand()
    return doc

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
        # Eine reine Jahreszahl ist kein Betrag (betrag_in, 261025-hbv), aber
        # sauber gelesen — die OCR-Bewertung bleibt dieselbe.
        if (betrag_in(t) is not None or _re.fullmatch(r"\d{1,2}\.\d{1,2}\.\d{2,4}", t)
                or _re.fullmatch(r"\d{4}", t.strip())):
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
                        _stempel(doc, pdf_path)
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

    return _stempel({
        "pdf_name": pdf_path.name,
        "belegtyp": belegtyp,
        "pages": pages_out,
        **({"ocr_seiten": ocr_seiten} if ocr_seiten else {}),
    }, pdf_path)


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
    parser.add_argument(
        "--alle", action="store_true",
        help="auch unveränderte PDFs neu einlesen",
    )
    parser.add_argument(
        "--nur", default="",
        help="nur dieses eine PDF (Dateiname) einlesen — für ein neues Dokument",
    )
    args = parser.parse_args()
    # Abgeschlossenes Steuerjahr: nichts mehr hineinschreiben (261029).
    from extractors.steuerjahr import pruefe_offen
    pruefe_offen(Path(args.output), "das Einlesen")

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
    if args.nur:
        # Ein neues Dokument braucht keinen Lauf über alle (261024-jga).
        import unicodedata
        gesucht = unicodedata.normalize("NFC", args.nur)
        pdfs = [p for p in pdfs if unicodedata.normalize("NFC", p.name) == gesucht]
        if not pdfs:
            print(f"FEHLER: kein PDF mit diesem Namen im Ordner", file=sys.stderr)
            return 1
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
    from scripts.verlauf import kopie_in_verlauf
    verlauf = output_dir / "_einlese-verlauf"
    stand = einlese_stand()
    success = 0
    uebersprungen = 0
    gruende = {"altes_format": 0, "inhalt": 0, "stand": 0}
    nicht_eingelesen: list[int] = []
    for nr, pdf in enumerate(pdfs, start=1):
        out_stem = safe_filename(pdf.stem) or f"pdf_{success}"
        out_path = output_dir / f"{out_stem}.json"
        alt: dict = {}
        if out_path.exists():
            try:
                alt = json.loads(out_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                alt = {}
            if not isinstance(alt, dict):
                alt = {}
        grund = neu_einlesen_noetig(alt, pdf, stand)
        if not args.alle and grund is None:
            # Unverändertes PDF (gleicher Inhalt, gleiches Einlesen): nicht
            # neu lesen, schon gar nicht neu durch OCR (261024-jga). Die Zeit
            # zählt nicht — eine Kopie ohne -p ist dasselbe Dokument, und
            # pdf_fingerabdruck (die Kennung der Ablage) bleibt (261025-kme).
            if alt.get("ordner") != ordner_von(pdf, input_dir):
                kopie_in_verlauf(out_path, verlauf)
                alt["ordner"] = ordner_von(pdf, input_dir)
                out_path.write_text(json.dumps(alt, indent=2, ensure_ascii=False),
                                    encoding="utf-8")
            uebersprungen += 1
            success += 1
            continue
        try:
            data = pdf_to_json(pdf)
        except Exception as e:
            # Ohne Dateinamen: er nennt Menschen, Konten, Arbeitgeber (B4i).
            print(f"   FEHLER bei Dokument {nr}/{len(pdfs)}: {type(e).__name__}",
                  file=sys.stderr)
            nicht_eingelesen.append(nr)
            continue
        if data is None:
            nicht_eingelesen.append(nr)
            continue
        if grund in gruende:
            gruende[grund] += 1
        # Gleicher Inhalt (Einlese-Stand geändert, --alle): die Kennung der
        # Ablage bleibt die des ersten Einlesens (261025-kme, BS-1).
        if alt.get("pdf_inhalt") and alt.get("pdf_inhalt") == data.get("pdf_inhalt") \
                and alt.get("pdf_fingerabdruck"):
            data["pdf_fingerabdruck"] = alt["pdf_fingerabdruck"]
        # Der Unterordner ist Vorgabe des Menschen: er sagt, was für ein
        # Dokument es ist (Konten, Wertschriften, …) — 261024-jga.
        data["ordner"] = ordner_von(pdf, input_dir)
        # Bestandsschutz 2025: das alte Word-JSON vorher in den Verlauf.
        kopie_in_verlauf(out_path, verlauf)
        out_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        success += 1

    # Zählzeilen ohne Namen (und ohne das Wort für Unverändertes, auf das
    # die Schlusszeile und ihre Tests hören).
    if gruende["altes_format"]:
        print(f"   einmalig neu eingelesen (altes Format): {gruende['altes_format']}")
    if gruende["inhalt"]:
        print(f"   Inhalt geändert: {gruende['inhalt']}")
    if gruende["stand"]:
        print(f"   Einlese-Stand geändert: {gruende['stand']}")
    if nicht_eingelesen:
        print(f"   nicht eingelesen: {len(nicht_eingelesen)} (Dokument-Nr. "
              f"{', '.join(str(n) for n in nicht_eingelesen)}) — das alte "
              "Word-JSON bleibt, falls es eines gibt")
    print(f"Fertig: {success}/{len(pdfs)} Word-JSONs → {output_dir}"
          + (f" ({uebersprungen} unverändert übersprungen)" if uebersprungen else ""))
    return 0 if success > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
