#!/usr/bin/env python3
"""Produktiver lokaler Lauf — echte PDFs → validierte Fokus-Tabelle.

Orchestriert die validierte Fokus-Pipeline auf echten lokalen PDFs, ohne
Anonymisierung und ohne Cloud-Calls. Wiederverwendet exakt dieselbe
Transfer-/Fokus-Logik wie der Sample-Pfad (``process_samples_full.py`` +
``build_tax_output.py``) — nur die Input-Quelle unterscheidet sich
(echte Word-JSONs statt anonymisierte).

Pipeline:
  1. ``belege/*.pdf``
  2. ``pdf_to_word_json.py``      → output/latest/json/*.json   (echte Werte, lokal)
  3. ``process_samples_full.py``  → output/latest/json/_results_full.json
  4. ``build_tax_output.py``      → steuer_uebertragung.md + nachweis_anhang.md
                                     (+ optional _results.csv)

Privacy-Constraints (hart):
  - KEIN Aufruf von ``anonymize_belege.py``
  - KEINE Cloud-Engine (Guard bricht ab bei --engine cloud / STEUER_ENGINE=cloud)
  - Output nach ``output/`` (gitignored) — verlässt das Gerät nicht

Aufruf::

    uv run python scripts/run_productive_local.py --input belege --output output/latest
    # oder ohne uv:
    PYTHONPATH=. python scripts/run_productive_local.py --input belege --output output/latest
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# Dateien in diesem Ordner, die der Abgleich niemals anfasst.
#
# Er loeschte jede *.json ohne passendes PDF — und korrekturen.json hat kein
# pdf_name-Feld. Damit hat jeder `make productive` die gesamte Durchsicht
# geloescht: genau die Datei, in die der Live-Server schreibt. Gefunden beim
# Audit nach dem Verlust vom 2026-09-10, bevor sie ein zweites Mal zuschlagen
# konnte.
#
# Doppelt abgesichert: Namensliste UND die Bedingung, dass ein Word-JSON ein
# "pages"-Feld hat. Eine Liste allein vergisst man beim naechsten neuen
# Dateinamen.
GESCHUETZT: frozenset[str] = frozenset({
    "korrekturen.json",
    "field_exceptions.json",
    "anon_truth_mapping.json",
})


def _pdf_fingerabdruck(pdf: Path) -> str:
    """Fingerabdruck eines PDFs — dieselbe Formel wie beim Erzeugen.

    Bewusst kein eigener Nachbau: der Erzeuger schreibt den Wert ins JSON,
    hier wird er verglichen. Zwei Formeln wuerden bedeuten, dass der Lauf
    stillschweigend jedes Mal alles neu einliest.
    """
    from scripts.pdf_to_word_json import pdf_fingerabdruck
    return pdf_fingerabdruck(pdf)


def _json_abgleich(input_dir: Path, json_dir: Path) -> tuple[int, int]:
    """Behaelt Word-JSONs unveraenderter PDFs, entfernt verwaiste.

    Returns ``(entfernt, behalten)``. Das Word-JSON traegt den Fingerabdruck
    des PDFs, aus dem es entstand; stimmt er nicht mehr oder fehlt das PDF,
    wird das JSON verworfen und im naechsten Schritt neu erzeugt.
    """
    import json as _json

    aktuell = {p.name: _pdf_fingerabdruck(p) for p in input_dir.glob("*.pdf")}
    entfernt = behalten = 0
    for jp in sorted(json_dir.glob("*.json")):
        if jp.name in GESCHUETZT:
            continue
        if jp.name.startswith("_"):
            jp.unlink()          # Ergebnisdatei wird ohnehin neu geschrieben
            continue
        try:
            doc = _json.loads(jp.read_text())
        except (OSError, ValueError):
            jp.unlink()
            entfernt += 1
            continue
        # Nur Word-JSONs kommen hier durch. Alles ohne "pages" ist etwas
        # anderes und geht diesen Abgleich nichts an.
        if not isinstance(doc, dict) or "pages" not in doc:
            continue
        pdf_name = doc.get("pdf_name", "")
        if aktuell.get(pdf_name) and doc.get("pdf_fingerabdruck") == aktuell[pdf_name]:
            behalten += 1
        else:
            jp.unlink()
            entfernt += 1
    return entfernt, behalten


def assert_local_only(engine: str) -> None:
    """Bricht ab, wenn eine Cloud-Engine angefordert wird (Arg ODER Env)."""
    if engine.strip().lower() not in ("local", "ollama"):
        print(
            f"ABBRUCH: --engine {engine!r} ist nicht erlaubt. Der produktive "
            f"lokale Lauf nutzt ausschließlich die lokale Engine (Ollama). "
            f"PDFs verlassen das Gerät nicht.",
            file=sys.stderr,
        )
        raise SystemExit(2)
    env_engine = os.environ.get("STEUER_ENGINE", "").strip().lower()
    if env_engine == "cloud":
        print(
            "ABBRUCH: STEUER_ENGINE=cloud gesetzt. Der produktive lokale Lauf "
            "verbietet jede Cloud-Engine (Privacy-Constraint).",
            file=sys.stderr,
        )
        raise SystemExit(2)


def _run(cmd: list[str]) -> tuple[int, str]:
    """Führt einen Pipeline-Schritt aus, streamt stdout zeilenweise und
    gibt (returncode, gesammelter_stdout) zurück.

    Pipeline-Schritte können mehrere Minuten laufen (Ollama-Extraktion über
    alle PDFs). ``capture_output=True`` würde alles puffern, der Nutzer sähe
    bis zum Schluss nichts. Stattdessen Popen + line-buffered Tee: jede Zeile
    geht sofort auf stdout UND in einen In-Memory-Buffer (kein Disk-Persist
    — Privacy-Constraint). stderr fließt direkt durch.
    """
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    proc = subprocess.Popen(
        cmd, cwd=ROOT, env=env, text=True,
        stdout=subprocess.PIPE, stderr=None, bufsize=1,
    )
    assert proc.stdout is not None
    buf: list[str] = []
    for line in proc.stdout:
        sys.stdout.write(line)
        sys.stdout.flush()
        buf.append(line)
    rc = proc.wait()
    return rc, "".join(buf)


def _write_csv(results_path: Path, csv_path: Path) -> int:
    """Schreibt eine CSV der Übertragungs-Zeilen aus _results_full.json.

    Nutzt dieselbe ``build_rows``-Logik wie die MD-Tabelle — identische Werte.
    """
    from scripts.build_tax_output import (  # validierte Logik
        build_rows, drop_zero_rows, ergaenze_eigene_zeilen, roher_betrag,
        wende_korrekturen_an,
    )

    data = json.loads(results_path.read_text())
    rows: list[dict] = []
    for entry in data:
        if entry.get("status") != "ok":
            continue
        rows.extend(build_rows(entry))
    # Eigene Korrekturen auch hier anwenden. Ohne das zeigt die CSV andere
    # Werte als Tabelle und Oberflaeche — und man weiss nicht, welcher gilt.
    wende_korrekturen_an(rows, results_path.parent)
    ergaenze_eigene_zeilen(rows, results_path.parent)
    # Nullbetrags-Zeilen wie in der MD-Tabelle verwerfen — sonst zeigen CSV
    # und Tabelle unterschiedlich viele Positionen.
    rows, _ = drop_zero_rows(rows)
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow([
            "Steuerbereich", "Ziffer", "Person", "Aussteller", "Beschreibung",
            "Betrag CHF", "Jahr/Periode", "Quelle", "Status",
        ])
        for r in rows:
            w.writerow([
                r.get("steuerbereich", ""), r.get("ziffer", ""),
                r.get("person", ""), r.get("aussteller", ""),
                r.get("beschreibung", ""),
                roher_betrag(r.get("betrag")), r.get("jahr") or "",
                r.get("pdf_name", ""), r.get("status", ""),
            ])
    return len(rows)


def _aussteller_belegtyp_year(entry: dict) -> tuple[str | None, str | None, int | None]:
    """Aussteller-Name, Belegtyp und Jahr eines ok-Belegs für den Lifecycle.

    Aussteller über ``spec.aussteller_field`` (analog build_basis_tabelle), Jahr
    über das ``jahr``-Feld (nur plausible 2018..2030). Liefert ``(None, …)`` wo
    nicht eindeutig — der Lifecycle überspringt solche Belege.
    """
    from extractors.steuer_zielmodell import get_spec
    from scripts.build_tax_output import is_real_value

    belegtyp = entry.get("belegtyp")
    if not belegtyp or belegtyp == "unknown":
        return None, None, None
    fmap = {f.get("feld"): f.get("value") for f in entry.get("fields", [])}
    spec = get_spec(belegtyp)
    aussteller_field = spec.aussteller_field if spec else None
    aussteller_raw = fmap.get(aussteller_field or "") if aussteller_field else None
    aussteller = aussteller_raw if is_real_value(aussteller_raw) else None

    year: int | None = None
    jahr_raw = fmap.get("jahr")
    if isinstance(jahr_raw, str) and jahr_raw.isdigit() and 2018 <= int(jahr_raw) <= 2030:
        year = int(jahr_raw)
    return aussteller, belegtyp, year


def _run_aussteller_lifecycle(results_path: Path, hist_path: Path) -> None:
    """Vorjahresvergleich (non-fatal): load → update → find_missing → write.

    Bricht den Lauf NIE ab (try/except um den ganzen Block, Fehler nur als
    Warnung). Privacy: in der Warnung nur Aussteller-Markernamen + Anzahl.
    """
    try:
        import extractors.aussteller as aussteller_mod

        if not results_path.exists():
            return
        data = json.loads(results_path.read_text())
        history, warn = aussteller_mod.load(hist_path)
        seen: set[str] = set()
        years: list[int] = []
        for entry in data:
            if entry.get("status") != "ok":
                continue
            aussteller, belegtyp, year = _aussteller_belegtyp_year(entry)
            if aussteller and belegtyp and year:
                key = aussteller_mod.make_key(aussteller, belegtyp)
                seen.add(key)
                aussteller_mod.update(history, aussteller, belegtyp, year)
                years.append(year)

        if warn:
            # Corrupt-Load: NICHT schreiben (würde Vorjahres-Historie löschen)
            # und kein find_missing (nichts geladen → kein ehrlicher Vergleich).
            print(
                f"WARNUNG: Aussteller-Historie nicht geladen ({warn}) — "
                "kein Write, kein Vorjahresvergleich in diesem Lauf.",
                file=sys.stderr,
            )
            return

        current_year = max(years) if years else None
        if current_year is not None:
            missing = aussteller_mod.find_missing(history, seen, current_year)
        else:
            missing = []
        aussteller_mod.write_json(hist_path, history)
        if missing:
            namen = ", ".join(sorted(e["aussteller_name"] for e in missing))
            print(
                f"WARNUNG: {len(missing)} Vorjahres-Aussteller fehlen im "
                f"aktuellen Lauf: {namen}",
                file=sys.stderr,
            )
    except Exception as exc:
        print(
            f"WARNUNG: Aussteller-Vorjahresvergleich übersprungen ({exc!r}).",
            file=sys.stderr,
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Produktiver lokaler Lauf: echte PDFs → validierte Fokus-Tabelle"
    )
    parser.add_argument("--input", type=Path, default=Path("belege"),
                        help="Ordner mit echten PDFs (Default: ./belege/)")
    parser.add_argument("--output", type=Path, default=ROOT / "output" / "latest",
                        help="Output-Ordner (Default: output/latest)")
    parser.add_argument("--engine", type=str, default="local",
                        help="Extraktions-Engine — nur 'local'/'ollama' erlaubt")
    parser.add_argument("--no-csv", action="store_true",
                        help="Keine CSV erzeugen")
    parser.add_argument("--keep-json", action="store_true",
                        help="Word-JSONs frueherer Laeufe im Output-Ordner "
                             "behalten (Debug). Default: leeren, damit nur die "
                             "PDFs aus --input verarbeitet werden.")
    parser.add_argument("--aussteller-history", type=Path,
                        default=ROOT / "aussteller.json",
                        help="Aussteller-Historie für den Vorjahresvergleich "
                             "(Default: ROOT/aussteller.json)")
    args = parser.parse_args()

    assert_local_only(args.engine)

    input_dir = args.input if args.input.is_absolute() else ROOT / args.input
    output_dir = args.output if args.output.is_absolute() else ROOT / args.output
    json_dir = output_dir / "json"
    output_dir.mkdir(parents=True, exist_ok=True)
    json_dir.mkdir(parents=True, exist_ok=True)

    # Abgleich statt Kahlschlag (260904-rmx).
    #
    # Erste Fassung leerte das json_dir komplett — damit war zwar kein
    # Altbestand mehr im Weg, aber jedes unveraenderte Dokument lief erneut
    # durch OCR und Sprachmodell. Bei 25 Dokumenten und einem neu
    # hinzugefuegten sind das 40 Minuten fuer fuenf Minuten Arbeit.
    #
    # Jetzt wird verglichen: ein Word-JSON bleibt, wenn sein PDF unveraendert
    # ist (Name, Groesse, mtime). Verschwundene PDFs nehmen ihr JSON mit —
    # das war das eigentliche Stale-Problem. Alles andere wird neu erzeugt.
    if not args.keep_json:
        entfernt, behalten = _json_abgleich(input_dir, json_dir)
        if entfernt or behalten:
            print(f"Abgleich: {behalten} unveraenderte(s) Dokument(e) "
                  f"wiederverwendet, {entfernt} veraltete(s) entfernt.")

    py = sys.executable

    # Schritt 2: PDF → Word-JSON (lokal, ohne Anonymisierung)
    print("── Schritt 1/3: PDF → Word-JSON (lokal, ohne Anonymisierung) ──")
    rc1, _ = _run([py, str(ROOT / "scripts" / "pdf_to_word_json.py"),
                   "--input", str(input_dir), "--output", str(json_dir)])
    if rc1 != 0:
        print("ABBRUCH: PDF→Word-JSON fehlgeschlagen.", file=sys.stderr)
        return 1
    n_json = len([p for p in json_dir.glob("*.json")
                  if p.name != "_results_full.json"])

    # Schritt 3: Extraktion (lokale Ollama-Engine) auf den Word-JSONs
    print("── Schritt 2/3: Extraktion (lokale Engine) ──")
    rc2, _ = _run([py, str(ROOT / "scripts" / "process_samples_full.py"),
                   "--samples", str(json_dir)])
    if rc2 != 0:
        print("ABBRUCH: Extraktion fehlgeschlagen.", file=sys.stderr)
        return 1

    # Schritt 4: Fokus-Tabelle + Nachweis-Anhang (validierte build_tax_output-Logik)
    print("── Schritt 3/3: Steuer-Übertragungstabelle + Nachweis-Anhang ──")
    rc3, _ = _run([py, str(ROOT / "scripts" / "build_tax_output.py"),
                   "--samples", str(json_dir)])
    if rc3 != 0:
        print("ABBRUCH: Tabellen-Bau fehlgeschlagen.", file=sys.stderr)
        return 1

    # Schritt 3b: Steueraufstellung-xlsx (ergänzender Komfort-Output).
    # Fehler-tolerant: die MD-Tabelle bleibt das harte Done-Kriterium, ein
    # gescheiterter xlsx-Bau darf den Lauf NICHT abbrechen — nur warnen.
    print("── Schritt 3b: Steueraufstellung-xlsx ──")
    rc_xlsx, _ = _run([py, str(ROOT / "scripts" / "build_steueraufstellung.py"),
                       "--samples", str(json_dir)])
    if rc_xlsx != 0:
        print("WARNUNG: Steueraufstellung-xlsx konnte nicht erzeugt werden "
              "(MD-Tabelle bleibt massgeblich).", file=sys.stderr)
    else:
        # Privacy-tauglicher xlsx-Validator (non-fatal). Befunde nur als
        # Warnung; die MD-Tabelle bleibt das harte Done-Kriterium. Der Check
        # gibt selbst NIE Zellwerte aus (nur Befundarten/Zähler).
        rc_check, _ = _run([py, str(ROOT / "scripts" / "check_aufstellung.py"),
                            "--file", str(json_dir / "steueraufstellung.xlsx")])
        if rc_check != 0:
            print("WARNUNG: check_aufstellung meldet Befunde in der "
                  "steueraufstellung.xlsx (manuell prüfen).", file=sys.stderr)

    # MDs + xlsx aus json/ nach output/latest/ heben (user-facing Ablage)
    for fname in ("steuer_uebertragung.md", "nachweis_anhang.md"):
        src = json_dir / fname
        if src.exists():
            (output_dir / fname).write_text(src.read_text(encoding="utf-8"),
                                            encoding="utf-8")
    # xlsx ist binär → bytes kopieren statt write_text.
    xlsx_src = json_dir / "steueraufstellung.xlsx"
    if xlsx_src.exists():
        (output_dir / "steueraufstellung.xlsx").write_bytes(xlsx_src.read_bytes())

    # Zählung direkt aus _results_full.json — gleiche build_rows-Logik wie die
    # MD-Tabelle, deshalb identische Werte. Ersetzt frühere stdout-Regex, die
    # bei Format-Drift schweigend 0 lieferte.
    from scripts.build_tax_output import build_rows  # validierte Logik

    n_auto = n_manual = 0
    results_path = json_dir / "_results_full.json"
    if results_path.exists():
        data = json.loads(results_path.read_text())
        for entry in data:
            if entry.get("status") != "ok":
                continue
            for r in build_rows(entry):
                if r.get("status") == "auto":
                    n_auto += 1
                else:
                    n_manual += 1

    # Aussteller-Vorjahresvergleich (Vollständigkeits-Constraint, non-fatal).
    hist_path = (
        args.aussteller_history if args.aussteller_history.is_absolute()
        else ROOT / args.aussteller_history
    )
    _run_aussteller_lifecycle(json_dir / "_results_full.json", hist_path)

    # Optional CSV
    csv_note = ""
    if not args.no_csv:
        results_path = json_dir / "_results_full.json"
        if results_path.exists():
            csv_path = output_dir / "steuer_uebertragung.csv"
            n_csv = _write_csv(results_path, csv_path)
            csv_note = f"  CSV:    {csv_path} ({n_csv} Zeilen)\n"

    # Abschluss-Hinweis
    print()
    print("═" * 60)
    print("Produktiver lokaler Lauf abgeschlossen.")
    print(f"  PDFs verarbeitet:    {n_json}")
    print(f"  🟢 Auto-Zeilen:      {n_auto}")
    print(f"  🟡 Manual-Review:    {n_manual}")
    print(f"  Output-Verzeichnis:  {output_dir}")
    print(f"    Tabelle: {output_dir / 'steuer_uebertragung.md'}")
    print(f"    Nachweis:{output_dir / 'nachweis_anhang.md'}")
    if csv_note:
        sys.stdout.write(csv_note)
    print("═" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
