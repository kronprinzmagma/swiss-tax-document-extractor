"""Verlauf: vor jedem Überschreiben eine Kopie, statt Löschen ein Verschieben.

CLAUDE.md „Schweigen heisst nie löschen" und der Anwender' Bestandsschutz 2025
(261025-kme): die Daten des Steuerjahrs 2025 bleiben bestehen. Wer eine
Ergebnisdatei überschreibt, legt vorher eine Kopie in ihren Verlauf; wer
eine Datei wegräumen will, verschiebt sie dorthin.

Namensschema: ``<stamm>-<JJJJMMTT-HHMMSS-ffffff><endung>``; gibt es den
Namen schon, ``-2``, ``-3`` … dahinter. Es wird nie überschrieben und nie
aufgeräumt — es sind Kilobytes.

Nur Standardbibliothek: das Modul läuft auch in ``.venv-etax``.
"""
from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path


def zeitmarke() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S-%f")


def freier_name(ordner: Path, stamm: str, endung: str) -> Path:
    """``<ordner>/<stamm>-<Zeit><endung>``, notfalls mit ``-2``, ``-3`` …"""
    basis = f"{stamm}-{zeitmarke()}"
    ziel = ordner / f"{basis}{endung}"
    n = 2
    while ziel.exists():
        ziel = ordner / f"{basis}-{n}{endung}"
        n += 1
    return ziel


def _teile(pfad: Path) -> tuple[str, str]:
    if pfad.is_dir():
        return pfad.name, ""
    return pfad.stem, pfad.suffix


def kopie_in_verlauf(pfad: Path, ordner: Path | None = None) -> Path | None:
    """Kopie von ``pfad`` in den Verlauf; ``None``, wenn es ``pfad`` nicht gibt.

    Ohne ``ordner``: ``<pfad.parent>/<stamm>-verlauf``.
    """
    pfad = Path(pfad)
    if not pfad.exists():
        return None
    stamm, endung = _teile(pfad)
    ziel_ordner = Path(ordner) if ordner is not None else pfad.parent / f"{stamm}-verlauf"
    ziel_ordner.mkdir(parents=True, exist_ok=True)
    ziel = freier_name(ziel_ordner, stamm, endung)
    if pfad.is_dir():
        shutil.copytree(pfad, ziel)
    else:
        shutil.copy2(pfad, ziel)
    return ziel


def in_verlauf_verschieben(pfad: Path, ordner: Path) -> Path:
    """``pfad`` in den Verlauf ``ordner`` verschieben (nicht löschen)."""
    pfad = Path(pfad)
    stamm, endung = _teile(pfad)
    ordner = Path(ordner)
    ordner.mkdir(parents=True, exist_ok=True)
    ziel = freier_name(ordner, stamm, endung)
    shutil.move(str(pfad), str(ziel))
    return ziel
