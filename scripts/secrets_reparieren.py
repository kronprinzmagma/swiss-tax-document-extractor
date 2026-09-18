#!/usr/bin/env python3
"""Repariert die Secrets-Datei und führt die Vorschläge ein.

Die Datei ist von Hand gepflegt, und ein einziger Einrückungsfehler macht sie
für YAML unlesbar — dann ersetzt der Anonymizer gar nichts mehr, ohne dass es
im Alltag auffällt. Genau das war passiert.

Dieses Skript liest die Datei **zeilenweise statt als YAML**, sammelt alle
Einträge ein, die es erkennen kann, führt die Vorschlagsdatei hinzu und
schreibt das Ergebnis sauber formatiert zurück. Das Original wird vorher als
``.bak`` gesichert.

Bewusst tolerant: es ist besser, einen kaputten Eintrag zu verlieren und das
gemeldet zu bekommen, als die ganze Datei unbrauchbar zu lassen. Was nicht
interpretiert werden konnte, wird gezählt und in der ``.bak`` bleibt es
erhalten.

Privacy: die Konsolenausgabe nennt ausschliesslich Schlüsselnamen und
Anzahlen — nie einen Eintrag. Sie ist teilbar.

Aufruf::

    PYTHONPATH=. .venv/bin/python scripts/secrets_reparieren.py            # Probelauf
    PYTHONPATH=. .venv/bin/python scripts/secrets_reparieren.py --schreiben
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Bekannte Struktur laut privacy_secrets.local.example.yaml.
UNTERSCHLUESSEL = ("banks", "kk", "brokers", "stiftungen", "arbeitgeber",
                   "kitas", "weitere")
LISTEN_OBEN = ("persons", "iban_account_numbers", "addresses", "birthdates")

_SCHLUESSEL_RE = re.compile(r"^(\w[\w_]*):\s*$")
_UNTER_RE = re.compile(r"^\s+(\w[\w_]*):\s*$")
_EINTRAG_RE = re.compile(r"""^\s*-\s*["']?(.+?)["']?\s*$""")


def lies_tolerant(pfad: Path) -> tuple[dict[str, list[str]], int]:
    """Sammelt Einträge zeilenweise ein, auch wenn YAML scheitert.

    Returns:
        ``({pfad_als_string: [eintraege]}, anzahl_unlesbarer_zeilen)``.
        Der Pfad ist ``"addresses"`` oder ``"institutions.banks"``.
    """
    if not pfad.exists():
        return {}, 0
    gesammelt: dict[str, list[str]] = {}
    oben: str | None = None
    unten: str | None = None
    unlesbar = 0

    for rohzeile in pfad.read_text(encoding="utf-8").splitlines():
        zeile = rohzeile.split("#", 1)[0].rstrip() if not rohzeile.lstrip().startswith("#") else ""
        if not zeile.strip():
            continue
        m = _SCHLUESSEL_RE.match(zeile)
        if m:
            oben, unten = m.group(1), None
            continue
        m = _UNTER_RE.match(zeile)
        if m and oben == "institutions":
            unten = m.group(1)
            continue
        if zeile.strip() in ("[]", "{}"):
            continue  # leerer Abschnitt — erkannt, nur ohne Inhalt
        m = _EINTRAG_RE.match(zeile)
        if m and oben:
            wert = m.group(1).strip()
            if not wert or wert in ("[]", "{}"):
                continue
            pfadname = f"{oben}.{unten}" if unten else oben
            gesammelt.setdefault(pfadname, [])
            if wert not in gesammelt[pfadname]:
                gesammelt[pfadname].append(wert)
            continue
        unlesbar += 1
    return gesammelt, unlesbar


def normalisiere(daten: dict[str, list[str]]) -> dict[str, list[str]]:
    """Ordnet Einträge unbekannter Abschnitte ``institutions.weitere`` zu.

    Einträge, die direkt unter ``institutions:`` standen — also ohne
    Unterschlüssel — oder unter einem unbekannten Unterschlüssel, wären beim
    Schreiben sonst verloren. Das wäre der schlimmere Ausgang: der Anonymizer
    liesse diese Namen ab dann stehen.
    """
    daten = {k: list(v) for k, v in daten.items()}
    bekannt = {f"institutions.{u}" for u in UNTERSCHLUESSEL} | set(LISTEN_OBEN)
    gerettet = daten.setdefault("institutions.weitere", [])
    for pfad in [p for p in list(daten) if p not in bekannt]:
        if pfad == "institutions" or pfad.startswith("institutions."):
            for e in daten.pop(pfad):
                if e not in gerettet:
                    gerettet.append(e)
    return daten


def schreibe_sauber(daten: dict[str, list[str]]) -> str:
    """Erzeugt eine gültige, kanonisch formatierte Datei.

    Einträge, die in der Vorlage direkt unter ``institutions:`` standen — also
    ohne Unterschlüssel — oder unter einem unbekannten Unterschlüssel, wandern
    nach ``institutions.weitere``. Sie kommentarlos wegzulassen wäre der
    schlimmere Ausgang: der Anonymizer würde diese Namen ab dann stehenlassen.
    """
    daten = normalisiere(daten)
    zeilen = [
        "# privacy_secrets.local.yaml — reale PII fuer Anonymizer und Gate.",
        "# Automatisch bereinigt und zusammengefuehrt (secrets_reparieren.py).",
        "# Gitignored. Nicht committen, nicht teilen.",
        "",
        "institutions:",
    ]
    for u in UNTERSCHLUESSEL:
        eintraege = daten.get(f"institutions.{u}", [])
        if not eintraege:
            continue
        zeilen.append(f"  {u}:")
        zeilen += [f'    - "{e}"' for e in eintraege]
    if not any(daten.get(f"institutions.{u}") for u in UNTERSCHLUESSEL):
        zeilen.append("  weitere: []")

    for schluessel in LISTEN_OBEN:
        eintraege = daten.get(schluessel, [])
        zeilen.append("")
        zeilen.append(f"{schluessel}:")
        zeilen += [f'    - "{e}"' for e in eintraege] or ["    []"]
    return "\n".join(zeilen) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--datei", type=Path,
                    default=ROOT / "privacy_secrets.local.yaml")
    ap.add_argument("--vorschlag", type=Path,
                    default=ROOT / "privacy_secrets.vorschlag.yaml")
    ap.add_argument("--schreiben", action="store_true",
                    help="Aenderung wirklich schreiben (sonst nur Probelauf)")
    args = ap.parse_args()

    bestand, unlesbar = lies_tolerant(args.datei)
    vorschlag, _ = lies_tolerant(args.vorschlag)

    zusammen: dict[str, list[str]] = {k: list(v) for k, v in bestand.items()}
    neu = 0
    for pfad, eintraege in vorschlag.items():
        ziel = zusammen.setdefault(pfad, [])
        for e in eintraege:
            if e not in ziel:
                ziel.append(e)
                neu += 1

    print(f"Bestand:   {sum(len(v) for v in bestand.values())} Eintraege in "
          f"{len(bestand)} Abschnitt(en)"
          + (f", {unlesbar} Zeile(n) nicht interpretierbar" if unlesbar else ""))
    print(f"Vorschlag: {sum(len(v) for v in vorschlag.values())} Eintraege")
    print(f"Neu dazu:  {neu}")
    zusammen = normalisiere(zusammen)
    print("\nErgebnis je Abschnitt (so wird geschrieben):")
    for pfad in sorted(zusammen):
        if zusammen[pfad]:
            print(f"   {pfad:34} {len(zusammen[pfad])}")

    inhalt = schreibe_sauber(zusammen)
    try:
        import yaml
        yaml.safe_load(inhalt)
    except Exception as exc:
        print(f"\nABBRUCH: Ergebnis waere kein gueltiges YAML ({exc!r})",
              file=sys.stderr)
        return 1

    if not args.schreiben:
        print("\nProbelauf — nichts geschrieben. Mit --schreiben ausfuehren.")
        return 0

    if args.datei.exists():
        sicherung = args.datei.with_suffix(".yaml.bak")
        shutil.copy(args.datei, sicherung)
        print(f"\nOriginal gesichert: {sicherung.name}")
    args.datei.write_text(inhalt, encoding="utf-8")
    print(f"Geschrieben: {args.datei.name} — parst als gueltiges YAML.")
    print("Diese Ausgabe enthaelt keine Eintraege, nur Anzahlen.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
