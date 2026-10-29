"""Ein Steuerjahr, ein Ausgabeordner — und ein abgeschlossenes Jahr bleibt zu.

Bis 2026-10 gab es nur ``output/latest``. Ein Lauf mit den Belegen des
nächsten Jahres hätte in dieselbe Ablage geschrieben wie die geprüften Werte
2025. Die Trigger der Ablage hätten die geprüften Zeilen geschützt, aber zwei
Jahre lägen vermischt in einer Datenbank.

Seither:

* **2025 bleibt, wo es ist** (``output/latest``) — kein Umzug, keine
  Umbenennung des Altbestands. Jedes spätere Jahr hat ``output/<Jahr>``.
* **Abschliessen** legt ``ABGESCHLOSSEN.json`` in den Ordner. Danach weisen
  die schreibenden Befehle (Lauf, Einlesen, Neu-Extraktion, Review) ab.
  Lesen, Prüfen, Sichern, Einfrieren und die Leiste bleiben erlaubt.
* **Wieder öffnen** legt die Marke in den Verlauf, statt sie zu löschen.

Die Regel steht im Makefile ein zweites Mal (``LATEST``);
``evals/test_steuerjahr.py`` hält beide gleich.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: Das erste Jahr, das dieses Werkzeug kennt — es liegt in ``output/latest``.
ERSTES_JAHR = 2025
MARKE = "ABGESCHLOSSEN.json"
VERLAUF = "abschluss-verlauf"


class JahrAbgeschlossen(SystemExit):
    """Ein schreibender Befehl auf einem abgeschlossenen Jahr."""


def ausgabe_fuer(jahr: int, root: Path = ROOT) -> Path:
    """Ausgabeordner eines Steuerjahrs (2025: ``output/latest``)."""
    jahr = int(jahr)
    return root / "output" / ("latest" if jahr == ERSTES_JAHR else str(jahr))


def ordner_von(samples: Path) -> Path:
    """Ausgabeordner zu einem ``…/json``-Ordner (oder dem Ordner selbst)."""
    samples = Path(samples)
    return samples.parent if samples.name == "json" else samples


def abschluss(ordner: Path) -> dict | None:
    """Inhalt der Abschlussmarke, oder None, wenn das Jahr offen ist."""
    pfad = ordner_von(ordner) / MARKE
    if not pfad.exists():
        return None
    try:
        return json.loads(pfad.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # Eine kaputte Marke ist eine Marke: lieber zu als offen.
        return {"jahr": "?", "seit": "?", "defekt": True}


def pruefe_offen(ordner: Path, was: str) -> None:
    """Bricht ab, wenn das Jahr abgeschlossen ist. Teilbar (kein Pfad)."""
    m = abschluss(ordner)
    if m is None:
        return
    raise JahrAbgeschlossen(
        f"Steuerjahr {m.get('jahr', '?')} ist abgeschlossen (seit {m.get('seit', '?')}) — "
        f"{was} ist gesperrt, damit die geprüften Werte bleiben, wie sie sind.\n"
        "Lesen, prüfen, sichern und einfrieren gehen weiter. Wieder öffnen nur "
        "bewusst: make jahr-oeffnen STEUERJAHR=… GRUND=\"…\"")


def _commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                              capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def schliesse_ab(ordner: Path, jahr: int) -> Path:
    ordner = ordner_von(ordner)
    pfad = ordner / MARKE
    if pfad.exists():
        return pfad
    pfad.write_text(json.dumps({
        "jahr": int(jahr), "seit": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "code": _commit(),
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return pfad


def oeffne_wieder(ordner: Path, grund: str) -> Path | None:
    """Marke in den Verlauf legen — mit Grund, nie löschen."""
    if not str(grund or "").strip():
        raise ValueError("Wieder öffnen braucht einen Grund (GRUND=\"…\").")
    ordner = ordner_von(ordner)
    pfad = ordner / MARKE
    if not pfad.exists():
        return None
    verlauf = ordner / VERLAUF
    verlauf.mkdir(exist_ok=True)
    ziel = verlauf / f"{datetime.now():%Y%m%d-%H%M%S}-{MARKE}"
    shutil.move(str(pfad), ziel)
    (verlauf / f"{ziel.stem}.grund.txt").write_text(grund.strip() + "\n", encoding="utf-8")
    return ziel
