#!/usr/bin/env python3
"""Was ändert ein Code-Stand an den Werten der Leiste? — teilbar, ohne Beträge.

Baut die Leiste zweimal aus DENSELBEN Daten (Extraktionsergebnis, Ablage,
Durchsicht): mit dem Code eines früheren Commits und mit dem jetzigen. Je
Wert steht dann ``gleich``, ``anders``, ``neu`` oder ``weg`` — mit Kürzel
(D07 Ordner · Typ), Formular und Feldname, nie mit Betrag oder Namen.

Anlass (261025): Ein Code-Review hat Leser und Regeln geändert; der Anwender fragt,
was das für die 2025-Werte bedeutet. Die Werte selbst bleiben lokal (Temp-
Dateien, am Ende gelöscht).

Der alte Stand läuft in einem Temp-Worktree; die nicht eingecheckten
Konfigurationsdateien werden dorthin verlinkt, damit Personen gleich
aufgelöst werden — gelesen werden sie nur vom Code, nicht ausgegeben.

Aufruf::

    make leistenvergleich ALT=<commit>
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOKAL = ("family.yaml", "privacy_secrets.local.yaml", "aussteller.json")

_BAUEN = r"""
import json, sys
from pathlib import Path
from unittest import mock
sys.path.insert(0, sys.argv[1])
from scripts import export_extension
with mock.patch("scripts.build_review_html.personennamen", return_value={}):
    d = export_extension.baue(Path(sys.argv[2]))
aus = []
for f in d["formulare"]:
    for b in f["bloecke"]:
        for x in b.get("felder") or []:
            for dok in b.get("dokumente") or [""]:
                aus.append([f["formular"], dok, b.get("titel", ""), x.get("titel", ""),
                            str(x.get("wert") or ""), b.get("person", "")])
        for w in b.get("werte") or []:
            aus.append([f["formular"], w.get("quelle", ""), "", w.get("bezeichnung", ""),
                        str(w.get("betrag") or ""), b.get("person", "")])
Path(sys.argv[3]).write_text(json.dumps(aus, ensure_ascii=False))
"""


def _bauen(code: Path, samples: Path, ziel: Path) -> list:
    r = subprocess.run([sys.executable, "-c", _BAUEN, str(code), str(samples.resolve()),
                        str(ziel)], cwd=code, capture_output=True, text=True,
                       env={**os.environ, "PYTHONPATH": str(code)})
    if r.returncode != 0:
        # Nur die letzte Zeile — sie nennt den Fehlertyp, keine Daten.
        raise SystemExit(f"Leiste nicht baubar ({code.name}): "
                         f"{(r.stderr.strip().splitlines() or ['?'])[-1][:160]}")
    return json.loads(ziel.read_text(encoding="utf-8"))


def _schluessel(zeilen: list) -> dict[tuple, tuple[str, str]]:
    from extractors.eintraege import dok_name
    aus: dict[tuple, tuple[str, str]] = {}
    zaehler: dict[tuple, int] = {}
    for form, dok, block, titel, wert, person in zeilen:
        basis = (form, dok_name(dok), titel.split(" (")[0])
        zaehler[basis] = zaehler.get(basis, 0) + 1
        aus[basis + (zaehler[basis],)] = (wert, person)
    return aus


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--samples", type=Path, required=True)
    ap.add_argument("--alt", help="Commit des früheren Stands")
    ap.add_argument("--merken", type=Path,
                    help="Schnappschuss der jetzigen Leiste (nur Prüfsummen) schreiben")
    ap.add_argument("--gegen", type=Path,
                    help="jetzige Leiste mit einem Schnappschuss vergleichen")
    args = ap.parse_args()
    sys.path.insert(0, str(ROOT))
    from scripts.pflichtfelder_check import _kuerzel
    import hashlib

    def pruefsumme(wert: tuple[str, str]) -> str:
        return hashlib.sha256("|".join(wert).encode()).hexdigest()[:16]

    kuerzel = _kuerzel(args.samples)
    if args.merken or args.gegen:
        # Vorher/nachher um einen Lauf: dieselbe Codebasis, andere Daten.
        with tempfile.TemporaryDirectory() as tmp:
            jetzt = _schluessel(_bauen(ROOT, args.samples, Path(tmp) / "n.json"))
        summen = {"\t".join(map(str, k)): pruefsumme(v) for k, v in jetzt.items()}
        if args.merken:
            args.merken.write_text(json.dumps(summen, indent=1), encoding="utf-8")
            print(f"Schnappschuss: {len(summen)} Werte (nur Prüfsummen) gemerkt.")
            return 0
        vorher = json.loads(args.gegen.read_text(encoding="utf-8"))
        # Prüfsumme über Betrag und Person: „anders" heisst dann eins von beiden.
        alt = {tuple(k.split("\t")[:3]) + (int(k.split("\t")[3]),): (v, "")
               for k, v in vorher.items()}
        neu = {k: (pruefsumme(v), "") for k, v in jetzt.items()}
    else:
        if not args.alt:
            raise SystemExit("--alt, --merken oder --gegen angeben")
        alt = neu = None
    if alt is None:
        alt, neu = _vergleich_code(args, kuerzel)
    return _bericht(alt, neu, kuerzel, args.alt or "Schnappschuss")


def _vergleich_code(args, kuerzel):
    with tempfile.TemporaryDirectory() as tmp:
        alt_code = Path(tmp) / "alt"
        subprocess.run(["git", "worktree", "add", "--detach", "-q", str(alt_code), args.alt],
                       cwd=ROOT, check=True)
        try:
            for name in LOKAL:
                if (ROOT / name).exists():
                    (alt_code / name).symlink_to(ROOT / name)
            alt = _schluessel(_bauen(alt_code, args.samples, Path(tmp) / "a.json"))
            neu = _schluessel(_bauen(ROOT, args.samples, Path(tmp) / "n.json"))
        finally:
            subprocess.run(["git", "worktree", "remove", "--force", str(alt_code)], cwd=ROOT)
    return alt, neu


def _bericht(alt, neu, kuerzel, name: str) -> int:
    def kurz(dok: str) -> str:
        return kuerzel.get(dok, "X (nicht im Lauf)").split(" ·")[0]

    zaehl = {"gleich": 0, "anders": 0, "neu": 0, "weg": 0}
    zeilen = []
    for k in sorted(set(alt) | set(neu)):
        a, n = alt.get(k), neu.get(k)
        if a and n:
            if a[0] == n[0] and a[1] == n[1]:
                zaehl["gleich"] += 1
                continue
            was = "anders" + ((" (Betrag)" if a[0] != n[0] else "")
                              if a[1] or n[1] else " (Betrag/Person)") + \
                  (" (Person)" if a[1] != n[1] else "")
            zaehl["anders"] += 1
        else:
            was = "neu" if n else "weg"
            zaehl[was] += 1
        form, dok, titel, nr = k
        zeilen.append(f"  {was:20} {kurz(dok):6} {form[:24]:24} {titel[:34]}"
                      + (f" #{nr}" if nr > 1 else ""))
    print(f"Leistenvergleich {name[:12]} → jetzt — teilbar, ohne Beträge")
    print("  " + ", ".join(f"{k} {v}" for k, v in zaehl.items()))
    print("\n".join(zeilen))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
