#!/usr/bin/env python3
"""Schlägt Einträge für ``privacy_secrets.local.yaml`` aus den eigenen Belegen vor.

Die Datei listet reale Instituts-, Personen- und Adressangaben, die der
Anonymizer ersetzen soll. Sie von Hand zu pflegen ist mühsam und wird
vergessen — sind Adressen nie erfasst worden, bleiben sie in den
„anonymisierten" Testdaten stehen.

Dieses Skript füllt sie aus den vorhandenen Dokumenten. Bewusst **ohne
Sprachmodell**: eine Schweizer Adresse ist ein regelmässiges Muster
(Strasse + Hausnummer, vierstellige PLZ + Ort), und die Institutsnamen liegen
bereits als extrahierte Aussteller vor. Regeln sind hier exakt, wiederholbar
und brauchen kein laufendes Ollama.

Privacy — zwei harte Grenzen:

* Das Ergebnis landet in einer **Vorschlagsdatei** neben der echten Datei, nie
  direkt in ihr. Was der Anonymizer ersetzt, entscheidet der Mensch.
* Die Konsolenausgabe nennt ausschliesslich **Anzahlen** — nie eine Adresse,
  nie einen Namen. Sie ist damit teilbar.

Aufruf::

    PYTHONPATH=. .venv/bin/python scripts/secrets_vorschlag.py \\
        --samples output/latest/json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Strasse + Hausnummer. Hausnummern sind ein- bis dreistellig und beginnen
# nie mit einer Null — sonst wuerden Telefonvorwahlen ("Limmatquai 044") und
# nachfolgende Postleitzahlen ("Musterstrasse 6560") als Adresse gelten.
STRASSE_RE = re.compile(
    r"\b([A-ZÀ-Þ][\wÀ-ÿ\-]{2,28}(?:strasse|str\.|weg|platz|gasse|allee|ring|"
    r"promenade|quai|route|via|piazza))\s+([1-9]\d{0,2}[a-z]?)\b",
    re.IGNORECASE,
)

# Vierstellige PLZ + Ort. Jahreszahlen sehen gleich aus, deshalb der Ausschluss.
PLZ_ORT_RE = re.compile(r"\b((?!19\d{2}|20[0-4]\d)\d{4})\s+([A-ZÀ-Þ][\wÀ-ÿ\-]{2,30})\b")

# Kein Ortsname, sondern Formular-Vokabular in derselben Form.
ORT_DENYLIST = {
    "total", "seite", "page", "pagina", "chf", "franken", "januar", "februar",
    "maerz", "märz", "april", "mai", "juni", "juli", "august", "september",
    "oktober", "november", "dezember", "prämie", "praemie", "steuerjahr",
    "konto", "kontostand", "saldo", "beitrag", "beiträge", "nettolohn",
    "bruttolohn", "jahr", "form", "fr", "eur", "usd",
}


def texte_aus_samples(samples_dir: Path) -> list[str]:
    """Volltext je Dokument aus den Word-JSONs."""
    out: list[str] = []
    for p in sorted(samples_dir.glob("*.json")):
        if p.name.startswith("_") or "mapping" in p.name:
            continue
        try:
            doc = json.loads(p.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        out.append(" ".join(w["text"] for pg in doc.get("pages", [])
                            for w in pg.get("words", [])))
    return out


def finde_adressen(texte: list[str]) -> list[str]:
    """Strassen mit Hausnummer und PLZ+Ort, nach Häufigkeit sortiert."""
    zaehler: Counter[str] = Counter()
    for t in texte:
        for strasse, nr in STRASSE_RE.findall(t):
            zaehler[f"{strasse} {nr}"] += 1
        for plz, ort in PLZ_ORT_RE.findall(t):
            if ort.lower() in ORT_DENYLIST:
                continue
            zaehler[f"{plz} {ort}"] += 1
    return [a for a, _ in zaehler.most_common()]


def finde_institute(samples_dir: Path) -> list[str]:
    """Aussteller aus dem Extraktionsergebnis — die sind bereits identifiziert."""
    ergebnisse = samples_dir / "_results_full.json"
    if not ergebnisse.exists():
        return []
    try:
        daten = json.loads(ergebnisse.read_text())
    except (OSError, json.JSONDecodeError):
        return []
    from extractors.steuer_zielmodell import get_spec, is_placeholder

    # Eine Anrede verraet eine Person. Beim Lohnausweis landet gelegentlich
    # der Arbeitnehmer im Arbeitgeber-Feld — der gehoert unter persons, nicht
    # unter institutions.
    anrede = re.compile(r"^(Herr|Frau|Familie|Hr\.|Fr\.)\b", re.IGNORECASE)

    namen: set[str] = set()
    for eintrag in daten:
        if eintrag.get("status") != "ok":
            continue
        spec = get_spec(eintrag.get("belegtyp", ""))
        feld = getattr(spec, "aussteller_field", None) if spec else None
        if not feld:
            continue
        for f in eintrag.get("fields", []):
            if f.get("feld") != feld:
                continue
            wert = str(f.get("value") or "").strip()
            if (wert and not wert.startswith("manual_review:")
                    and not is_placeholder(wert) and len(wert) > 2
                    and not anrede.match(wert)):
                namen.add(wert)
    return sorted(namen)


def pruefe_bestand(pfad: Path) -> str:
    """Meldet, ob die vorhandene Datei parst — ohne ihren Inhalt zu zeigen."""
    if not pfad.exists():
        return "existiert nicht"
    try:
        import yaml
        daten = yaml.safe_load(pfad.read_text())
    except Exception as exc:
        erste = str(exc).splitlines()[0][:80]
        mark = getattr(exc, "problem_mark", None)
        ort = f", Zeile {mark.line + 1} Spalte {mark.column + 1}" if mark else ""
        return f"SYNTAXFEHLER{ort} — {erste}"
    if not isinstance(daten, dict):
        return "parst, enthält aber kein Objekt"
    return "parst, Schlüssel: " + ", ".join(
        f"{k}({len(v) if isinstance(v, (list, dict)) else 1})"
        for k, v in sorted(daten.items())
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--samples", type=Path, required=True,
                    help="Verzeichnis mit den Word-JSONs (z.B. output/latest/json)")
    ap.add_argument("--out", type=Path, default=None,
                    help="Vorschlagsdatei (Default: privacy_secrets.vorschlag.yaml "
                         "im Projektwurzelverzeichnis)")
    args = ap.parse_args()

    echt = ROOT / "privacy_secrets.local.yaml"
    print(f"Vorhandene privacy_secrets.local.yaml: {pruefe_bestand(echt)}\n")

    texte = texte_aus_samples(args.samples)
    if not texte:
        print(f"Keine Word-JSONs in {args.samples}", file=sys.stderr)
        return 1

    adressen = finde_adressen(texte)
    institute = finde_institute(args.samples)

    ziel = args.out or ROOT / "privacy_secrets.vorschlag.yaml"
    zeilen = [
        "# VORSCHLAG — aus den eigenen Dokumenten erzeugt, NICHT geprüft.",
        "#",
        "# Durchsehen, Falsches löschen, Fehlendes ergänzen. Erst dann den",
        "# Inhalt nach privacy_secrets.local.yaml übernehmen. Diese Datei",
        "# enthält echte Angaben und gehört nicht ins Repository.",
        "",
        "institutions:",
        "  weitere:",
    ]
    zeilen += [f'    - "{n}"' for n in institute] or ["    []"]
    zeilen += ["", "addresses:"]
    zeilen += [f'    - "{a}"' for a in adressen] or ["    []"]
    ziel.write_text("\n".join(zeilen) + "\n", encoding="utf-8")

    print(f"{len(texte)} Dokument(e) durchsucht.")
    print(f"  Adress-Kandidaten:    {len(adressen)}")
    print(f"  Instituts-Kandidaten: {len(institute)}")
    print(f"\nGeschrieben: {ziel}")
    print("Diese Ausgabe enthält keine Adressen und keine Namen — nur Anzahlen.")
    print("\nNaechster Schritt: Datei durchsehen, dann den Inhalt nach")
    print("privacy_secrets.local.yaml uebernehmen und neu anonymisieren.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
