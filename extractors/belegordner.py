"""Wo liegen die Dokumente? Eine Antwort für alle Skripte.

Der Eingangsordner hat Unterordner (Konten, Wertschriften, Krankenkassen,
Einkünfte, … — vom Nutzer angelegt, 2026-10-24). Bis dahin suchte jedes
Skript selbst und nur eine Ebene tief (``glob("*.pdf")``); ein Dokument in
einem Unterordner war für Einlesen, Bereitschaft, eSteuerauszug-Prüfung und
Review unsichtbar — still, ohne Meldung.

Regeln:

* Alle Unterordner zählen, beliebig tief.
* ``irrelevant/`` (gross/klein egal, auf jeder Ebene) ist beiseitegelegt:
  nicht verarbeitet, aber auffindbar (für Links und Bilanz).
* Versteckte Ordner und Dateien (``.``) zählen nicht.
* Der Dateiname ist die Kennung eines Dokuments (so heissen Word-JSON,
  Ablage und Korrekturen). Zwei Dateien gleichen Namens in verschiedenen
  Ordnern sind deshalb ein Fehler, den ``doppelte_namen`` meldet.
"""
from __future__ import annotations

from pathlib import Path

AUSSORTIERT = "irrelevant"


def _pdfs(ordner: Path) -> list[Path]:
    if not ordner.is_dir():
        return []
    aus = []
    for p in ordner.rglob("*"):
        if not p.is_file() or p.suffix.lower() != ".pdf":
            continue
        teile = p.relative_to(ordner).parts
        if any(t.startswith(".") for t in teile):
            continue
        aus.append(p)
    return sorted(aus, key=lambda p: str(p.relative_to(ordner)).lower())


def _aussortiert(ordner: Path, p: Path) -> bool:
    return any(t.lower() == AUSSORTIERT for t in p.relative_to(ordner).parts[:-1])


def alle_pdfs(ordner: Path) -> list[Path]:
    """Alle zu verarbeitenden PDFs — Unterordner eingeschlossen, ohne irrelevant/."""
    return [p for p in _pdfs(ordner) if not _aussortiert(ordner, p)]


def aussortierte_pdfs(ordner: Path) -> list[Path]:
    """Die PDFs in einem Ordner ``irrelevant`` (auf jeder Ebene)."""
    return [p for p in _pdfs(ordner) if _aussortiert(ordner, p)]


def finde_pdf(ordner: Path | None, name: str) -> Path | None:
    """Das PDF zu einem Dokumentnamen, wo immer es im Ordnerbaum liegt.

    Nur der Basisname zählt; ``../`` oder Pfade laufen ins Leere. Das
    Ergebnis liegt immer innerhalb von ``ordner``.
    """
    if ordner is None or not name:
        return None
    basis = Path(name).name
    direkt = ordner / basis
    if direkt.is_file():
        return direkt
    treffer = [p for p in _pdfs(ordner) if p.name == basis]
    return treffer[0] if treffer else None


def doppelte_namen(ordner: Path) -> dict[str, list[Path]]:
    """Dateinamen, die in mehreren Unterordnern vorkommen."""
    je_name: dict[str, list[Path]] = {}
    for p in _pdfs(ordner):
        je_name.setdefault(p.name, []).append(p)
    return {n: ps for n, ps in je_name.items() if len(ps) > 1}


# ---------------------------------------------------------------------------
# Der Ordner sagt den Dokumenttyp (der Anwender, 261024-jga): „die Ordner haben klar
# definierten Inhalt — pro Dokument ein Konto, Krankenkassenbeleg etc."
# Schlüssel klein und ohne Umlaut; ein Dokument ohne Unterordner ist frei.

def _ordner_typen() -> dict[str, tuple[str, ...]]:
    """Aus `extractors/datenmodell.yaml` — Ein- und Mehrzahl stehen dort."""
    from extractors.datenmodell import ordner_typen
    return ordner_typen()


def ordner_von(pdf: Path, eingang: Path) -> str:
    """Erster Unterordner unter dem Eingang, z. B. „Konten" — oder ""."""
    try:
        teile = pdf.relative_to(eingang).parts[:-1]
    except ValueError:
        return ""
    return teile[0] if teile else ""


def _schluessel(ordner: str) -> str:
    o = (ordner or "").strip().lower()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue")):
        o = o.replace(a, b)
    return o


def erlaubte_typen(ordner: str) -> tuple[str, ...] | None:
    """Welche Belegtypen der Ordner zulässt; None = keine Vorgabe."""
    return _ordner_typen().get(_schluessel(ordner))


def typ_aus_ordner(ordner: str, erkannt: str) -> str:
    """Erkannter Typ, wenn der Ordner ihn zulässt — sonst der des Ordners.

    Lässt der Ordner mehrere zu (Konten: Konto oder Hypothek), bleibt ein
    unpassender Typ beim ersten, dem Regelfall.
    """
    erlaubt = erlaubte_typen(ordner)
    if not erlaubt or erkannt in erlaubt:
        return erkannt
    return erlaubt[0]
