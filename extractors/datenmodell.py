"""Das Datenmodell aus `extractors/datenmodell.yaml` — eine Quelle für die Regeln.

der Anwender (261024-jga): „ich kann zig mal das Gleiche sagen und wir stehen wieder
am Anfang". Seine Vorgaben stehen deshalb in einer Datei, die Mensch und
Prüfung lesen. Die maschinenlesbare Fassung liegt seit 2026-10-28 in
`extractors/datenmodell.yaml`, nicht mehr im YAML-Block von `DATENMODELL.md`:
der öffentliche Spiegel hat kein ``, dort lief der Code sonst nicht
(alle Sync-Läufe ab 2026-10-26 brachen deshalb ab). Die Begründung bleibt dort.

``lade()`` prüft die Pflichtschlüssel. Ein fehlender oder umbenannter
Schlüssel ist ein Fehler, keine leere Regel (261025-jm6, A1): vorher
lieferten die Accessoren dann still ``{}``, ``()`` oder ``0``, und die
Regel war aus, ohne dass es jemand merkte.
"""
from __future__ import annotations

import re
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

PFAD = Path(__file__).resolve().parent / "datenmodell.yaml"

STILL_AUS = "ohne ihn wäre die Regel still aus"


class DatenmodellFehler(ValueError):
    """Das Datenmodell ist unvollständig oder hat eine unerwartete Form."""


def _fehler(pfad: str, was: str) -> DatenmodellFehler:
    return DatenmodellFehler(f"{pfad}: {was} im Datenmodell — {STILL_AUS}")


def _zahl(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _textliste(x) -> bool:
    return (isinstance(x, list) and bool(x)
            and all(isinstance(t, str) and t.strip() for t in x))


def _pruefe(daten) -> dict:
    if not isinstance(daten, dict):
        raise _fehler("(Wurzel)", "keine Abbildung")
    for name in ("ordner_typen", "pflicht_je_typ"):
        if name not in daten:
            raise _fehler(name, "fehlt")
        wert = daten[name]
        if not isinstance(wert, dict) or not wert:
            raise _fehler(name, "leer oder keine Abbildung")
        for k, v in wert.items():
            if not _textliste(v):
                raise _fehler(f"{name}.{k}", "keine nicht leere Liste von Texten")
    personen = daten.get("personen")
    if not isinstance(personen, dict):
        raise _fehler("personen", "fehlt")
    if "rollen" not in personen:
        raise _fehler("personen.rollen", "fehlt")
    if not _textliste(personen["rollen"]):
        raise _fehler("personen.rollen", "leer oder keine Liste von Texten")
    if "krankenkasse_dokumente" not in personen:
        raise _fehler("personen.krankenkasse_dokumente", "fehlt")
    kk = personen["krankenkasse_dokumente"]
    if not isinstance(kk, dict) or not kk:
        raise _fehler("personen.krankenkasse_dokumente", "leer oder keine Abbildung")
    for rolle, n in kk.items():
        if rolle not in personen["rollen"]:
            raise _fehler(f"personen.krankenkasse_dokumente.{rolle}",
                          "Rolle steht nicht in personen.rollen")
        if not isinstance(n, int) or isinstance(n, bool) or n < 0:
            raise _fehler(f"personen.krankenkasse_dokumente.{rolle}",
                          "keine ganze Zahl ≥ 0")
    grenzen = personen.get("kvg_fast_leer")
    if not isinstance(grenzen, dict):
        raise _fehler("personen.kvg_fast_leer", "fehlt")
    for k in ("erwachsene", "kinder"):
        if k not in grenzen:
            raise _fehler(f"personen.kvg_fast_leer.{k}", "fehlt")
        if not _zahl(grenzen[k]):
            raise _fehler(f"personen.kvg_fast_leer.{k}", "keine Zahl")
    for k in ("saeule_3a_maximum", "kita_maximum_je_kind"):
        if k not in personen:
            raise _fehler(f"personen.{k}", "fehlt")
        if not _zahl(personen[k]) or personen[k] <= 0:
            raise _fehler(f"personen.{k}", "keine Zahl > 0")
    return daten


def lade_aus(pfad: Path) -> dict:
    """Das Datenmodell aus ``pfad`` laden und prüfen — ohne Cache.

    ``pfad`` ist reines YAML (``datenmodell.yaml``) oder Markdown mit einem
    ```yaml-Block (frühere Form, Tests schreiben sie noch so).
    """
    import yaml

    try:
        text = Path(pfad).read_text(encoding="utf-8")
    except OSError as e:
        raise DatenmodellFehler(f"{Path(pfad).name} nicht lesbar ({e.__class__.__name__}) "
                                f"— {STILL_AUS}") from e
    m = re.search(r"```yaml\n(.*?)```", text, re.S)
    if m is None and Path(pfad).suffix == ".md":
        raise DatenmodellFehler(f"kein YAML-Block in {Path(pfad).name} — {STILL_AUS}")
    try:
        daten = yaml.safe_load(m.group(1) if m else text)
    except yaml.YAMLError as e:
        raise DatenmodellFehler(f"{Path(pfad).name} nicht lesbar: {e}") from e
    return _pruefe(daten)


@lru_cache(maxsize=1)
def lade() -> dict:
    # PFAD zur Aufrufzeit lesen — Tests setzen ihn um.
    return lade_aus(PFAD)


def ordner_typen() -> dict[str, tuple[str, ...]]:
    return {k: tuple(v) for k, v in lade()["ordner_typen"].items()}


def kk_dokumente_je_rolle() -> dict[str, int]:
    return dict(lade()["personen"]["krankenkasse_dokumente"])


def rollen() -> tuple[str, ...]:
    return tuple(lade()["personen"]["rollen"])


def kvg_fast_leer(rolle: str) -> int:
    grenzen = lade()["personen"]["kvg_fast_leer"]
    return int(grenzen["erwachsene" if rolle.startswith("eltern") else "kinder"])


def pflicht_je_typ() -> dict[str, list[str]]:
    return {k: list(v) for k, v in lade()["pflicht_je_typ"].items()}


def saeule_3a_maximum() -> Decimal:
    return Decimal(str(lade()["personen"]["saeule_3a_maximum"]))


def kita_maximum_je_kind() -> Decimal:
    return Decimal(str(lade()["personen"]["kita_maximum_je_kind"]))
