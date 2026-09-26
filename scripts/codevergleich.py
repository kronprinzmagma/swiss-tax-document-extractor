#!/usr/bin/env python3
"""Hat eine Code-Änderung die Zeilen verändert? Zwei Stände, dieselben Daten.

Nach einem Umbau bleibt die Frage: sieht die Oberfläche nur anders aus, oder
sind auch die Zeilen andere geworden? Die Tests sagen es nicht — sie laufen
auf synthetischen Fällen.

Dieses Skript baut die Zeilen zweimal: einmal mit dem Code eines früheren
Commits, einmal mit dem jetzigen, beide gegen **dieselben** Daten. Verglichen
werden Zahlen und Zielwert-Bezeichnungen, nie Beträge und nie Dokumentnamen —
der Bericht bleibt teilbar.

Aufruf::

    make vergleich ALT=45a89f2

Der alte Stand wird in ein temporäres Verzeichnis ausgepackt; am Repository
ändert sich nichts.
"""
from __future__ import annotations

import argparse
import collections
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TRENNER = "─" * 70

# Das Programm, das in beiden Ständen läuft. Es gibt nur Zahlen und
# Bezeichnungen zurück — keine Dokumentnamen, keine Beträge.
PROGRAMM = """
import json, sys, collections
from pathlib import Path
from scripts.build_review_html import sammle_zeilen

z = sammle_zeilen(Path(sys.argv[1]), mit_crops=False)
print(json.dumps({
    "zeilen": len(z),
    "dokumente": len({r.get("beleg") for r in z}),
    "bestaetigt": sum(1 for r in z if r.get("vorabBetrag")),
    "person_bestaetigt": sum(1 for r in z if r.get("vorabPerson")),
    "ohne_ziffer": sum(1 for r in z if not str(r.get("ziffer") or "").strip()),
    "ohne_betrag": sum(1 for r in z if not str(r.get("betrag") or "").strip()),
    "ohne_aussteller": sum(1 for r in z if not str(r.get("aussteller") or "").strip()),
    "ohne_person": sum(1 for r in z if not str(r.get("person") or "").strip()),
    "gestrichen": sum(1 for r in z if r.get("gestrichen")),
    "ohne_betrag_selbst": sum(1 for r in z if r.get("neu")
                              and not str(r.get("betrag") or "").strip()),
    "ohne_person_selbst": sum(1 for r in z if r.get("neu")
                              and not str(r.get("person") or "").strip()),
    "eigene": sum(1 for r in z if r.get("neu")),
    # Wie viele verschiedene Dokumente liefern denselben Zielwert? Drei
    # Zeilen aus drei Dokumenten sind drei Nachweise; drei Zeilen aus einem
    # Dokument sind drei Positionen darin. Ohne diese Zahl ist nicht zu
    # unterscheiden, ob eine Zeile zu viel ist (260923-dua).
    "dokumente_je_zielwert": {
        zz: len({str(r.get("beleg") or "") for r in z
                 if str(r.get("zielwert") or "") == zz})
        for zz in {str(r.get("zielwert") or "") for r in z}},
    "je_zielwert": dict(collections.Counter(
        str(r.get("zielwert") or "(ohne)") for r in z)),
    "eigene_je_zielwert": dict(collections.Counter(
        str(r.get("zielwert") or "(ohne)") for r in z if r.get("neu"))),
    # Je Dokument nur die ANZAHL — der Name wird durch seine Stelle in der
    # sortierten Liste ersetzt, damit der Bericht teilbar bleibt.
    "je_dokument": sorted(collections.Counter(
        str(r.get("beleg") or "") for r in z).values()),
    "bestaetigt_je_dokument": sorted(collections.Counter(
        str(r.get("beleg") or "") for r in z if r.get("vorabBetrag")).values()),
}))
"""


def _lauf(code_wurzel: Path, samples: Path) -> dict | None:
    fertig = subprocess.run(
        [sys.executable, "-c", PROGRAMM, str(samples.resolve())],
        capture_output=True, text=True, cwd=str(code_wurzel),
        env={"PYTHONPATH": str(code_wurzel), "PATH": "/usr/bin:/bin"},
        timeout=300)
    if fertig.returncode != 0:
        print(f"Lauf in {code_wurzel.name} fehlgeschlagen:\n"
              f"{fertig.stderr[-1500:]}", file=sys.stderr)
        return None
    try:
        return json.loads(fertig.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        print(f"Keine auswertbare Ausgabe aus {code_wurzel.name}",
              file=sys.stderr)
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--samples", type=Path, required=True)
    ap.add_argument("--zeigen", action="store_true",
                    help="Nur den jetzigen Stand zeigen, ohne Vergleich")
    ap.add_argument("--alt", default=None,
                    help="Commit, gegen den verglichen wird (z.B. 45a89f2)")
    args = ap.parse_args()

    if args.zeigen:
        jetzt = _lauf(ROOT, args.samples)
        if jetzt is None:
            return 1
        print("Jetziger Stand — nur Zahlen und Zielwerte, teilbar")
        print(TRENNER)
        for k, v in jetzt.items():
            if isinstance(v, int):
                print(f"   {k:<20} {v:>5}")
        eigen = jetzt.get("eigene_je_zielwert", {})
        print("\n   Zeilen je Zielwert  (davon selbst erfasst):")
        for z, n in sorted(jetzt.get("je_zielwert", {}).items(),
                           key=lambda kv: (-kv[1], kv[0])):
            e = eigen.get(z, 0)
            d = jetzt.get("dokumente_je_zielwert", {}).get(z, 0)
            zusatz = f"   (davon {e} selbst)" if e else ""
            print(f"      {n:>3}×  {z}{zusatz}"
                  f"{f'   aus {d} Dokument(en)' if d and d != n else ''}")
        return 0

    if not args.alt:
        print("Entweder --alt <commit> oder --zeigen.", file=sys.stderr)
        return 1

    tmp = Path(tempfile.mkdtemp(prefix="steuer-vergleich-"))
    try:
        entpacken = subprocess.run(
            f"git archive {args.alt} | tar -x -C {tmp}",
            shell=True, cwd=str(ROOT), capture_output=True, text=True)
        if entpacken.returncode != 0:
            print(f"Commit {args.alt} nicht auspackbar:\n"
                  f"{entpacken.stderr[-500:]}", file=sys.stderr)
            return 1
        # Das venv des Repos mitbenutzen — der alte Stand hat keines.
        venv = ROOT / ".venv"
        if venv.is_dir() and not (tmp / ".venv").exists():
            (tmp / ".venv").symlink_to(venv)

        alt = _lauf(tmp, args.samples)
        neu = _lauf(ROOT, args.samples)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if alt is None or neu is None:
        return 1

    print(f"Codevergleich {args.alt} → jetzt — nur Zahlen, teilbar")
    print(TRENNER)
    zahlen = [k for k, v in neu.items() if isinstance(v, int)]
    breit = max(len(k) for k in zahlen)
    unterschiede = 0
    for k in zahlen:
        a, n = alt.get(k), neu.get(k)
        marke = "  " if a == n else "→ "
        if a != n:
            unterschiede += 1
        print(f" {marke}{k:<{breit}}  {a!s:>5}  →  {n!s:>5}")

    for feld, titel in (("je_dokument", "Zeilen je Dokument"),
                        ("bestaetigt_je_dokument", "Bestätigte je Dokument")):
        if alt.get(feld) != neu.get(feld):
            unterschiede += 1
            print(f"\n {titel} (sortiert, ohne Namen):")
            print(f"   vorher: {alt.get(feld)}")
            print(f"   jetzt:  {neu.get(feld)}")

    a_zw, n_zw = alt.get("je_zielwert", {}), neu.get("je_zielwert", {})
    geaendert = {k: (a_zw.get(k, 0), n_zw.get(k, 0))
                 for k in set(a_zw) | set(n_zw)
                 if a_zw.get(k, 0) != n_zw.get(k, 0)}
    if geaendert:
        unterschiede += 1
        print("\n Zielwerte mit anderer Anzahl:")
        for k, (a, n) in sorted(geaendert.items()):
            print(f"   {k:<44} {a} → {n}")

    print()
    if unterschiede:
        print(f"→ {unterschiede} Unterschied(e). Die Änderung war nicht nur "
              f"Darstellung.")
        return 1
    print("→ Kein Unterschied. Dieselben Zeilen, nur anders gezeigt.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
