"""Aussteller-Lifecycle-Modul für ``aussteller.json`` (Plan 03-03, Wave 2).

Implementiert die persistente Aussteller-Historie für den Vorjahresvergleich
(CLAUDE.md Constraint 3 — „Vollständigkeit"). Schema-Definition gemäss
D-B1..B7 aus 03-CONTEXT.md:

* Flache Map ``{aussteller_key: AusstellerEntry}`` (D-B2).
* ``aussteller_key = f"{normalize(name)}__{belegtyp}"`` (D-B3) — derselbe
  Aussteller mit verschiedenen Belegtypen ergibt verschiedene Keys
  (z. B. BANK-Z als Bank vs. BANK-Z als Spenden-Empfänger).
* ``fingerprint = sha256(name + belegtyp + str(year))[:12]`` (D-B4) —
  Stabil identifizierbares Tupel, falls Migration nötig wird.
* ``last_seen_year = max(prev, current)`` beim Update (D-B5) —
  idempotent gegen Mehrfach-Reads desselben Jahres und gegen
  Out-of-Order-Verarbeitung (z. B. erst 2024er-Belege, dann 2023er).
* ``find_missing`` liefert Aussteller mit ``last_seen_year >= current - 1``
  die im aktuellen Lauf NICHT gesehen wurden (D-B6) — Aussteller mit
  ``last_seen_year < current - 1`` sind „verjährt" (z. B. Bank gewechselt
  vor zwei Jahren) und werden NICHT gemeldet.

Korruption-Toleranz (Pitfall 6, 03-RESEARCH.md): ``load`` crasht NIE.
Bei fehlender Datei → leere Map, kein Warning. Bei korruptem JSON oder
``OSError`` → leere Map + Warning-String (Caller dokumentiert das im
Report).

Atomarer Write via ``.bak``-Pattern (Pitfall 6): vor dem Schreiben wird
das Original zu ``aussteller.json.bak`` kopiert; der neue Inhalt wird
nach ``aussteller.json.tmp`` geschrieben und via ``os.replace`` atomar
auf POSIX umbenannt.

Privacy-Hinweis: ``aussteller.json`` enthält Aussteller-Namen (PII-light)
und ist strikt gitignored — Verifikation erfolgte in Plan 03-01.

Doc-Strings auf Schweizer Hochdeutsch, API-Bezeichner in Englisch.
Reines Modul: I/O nur in :func:`load` und :func:`write_json`.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import TypedDict

from extractors.numbers import normalize

logger = logging.getLogger(__name__)

#: Schema-Version der ``aussteller.json``-Struktur. Aktuell NICHT in die
#: Datei geschrieben (Aufwand-Mismatch in v1) — Konstante existiert als
#: Anker für künftige Migration (siehe 03-RESEARCH.md §Schema-Versioning).
AUSSTELLER_SCHEMA_VERSION = 1


class AusstellerEntry(TypedDict):
    """Ein Eintrag in der ``aussteller.json``-Map.

    * ``aussteller_name`` — Original-Name aus dem Beleg (NICHT normalisiert),
      damit der Report-Output menschen­lesbar bleibt.
    * ``belegtyp`` — geschlossener Belegtyp-Katalog aus
      :data:`extractors.schema.Belegtyp` (validiert beim Update upstream).
    * ``last_seen_year`` — höchstes Jahr, in dem dieser Aussteller bisher
      gesehen wurde (D-B5).
    * ``fingerprint`` — 12-Zeichen-sha256-Präfix für Migrations-Stabilität.
    """

    aussteller_name: str
    belegtyp: str
    last_seen_year: int
    fingerprint: str


def make_key(aussteller_name: str, belegtyp: str) -> str:
    """Bildet den eindeutigen Map-Schlüssel ``f"{normalize(name)}__{belegtyp}"`` (D-B3).

    Die Normalisierung via :func:`extractors.numbers.normalize` macht den
    Key invariant gegen Apostroph-Varianten (U+0027/U+2019/U+2018/U+02BC),
    NBSP/NNBSP/THINSP und Case-Unterschiede — wichtig für die Wieder­
    erkennung zwischen Jahren, in denen z. B. „L'Oréal" mal mit ASCII-,
    mal mit typografischem Apostroph gedruckt wird.
    """
    return f"{normalize(aussteller_name)}__{belegtyp}"


def make_fingerprint(aussteller_name: str, belegtyp: str, year: int) -> str:
    """Bildet den 12-stelligen sha256-Fingerprint ``[:12]`` (D-B4).

    Deterministisch (selbe Inputs → selber Output). Dient als Migrations-
    Anker, falls sich das Map-Schema ändert und Einträge anhand eines
    stabilen Tupels neu zugeordnet werden müssen.
    """
    payload = f"{aussteller_name}{belegtyp}{year}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:12]


def load(path: Path) -> tuple[dict[str, AusstellerEntry], str | None]:
    """Lädt die ``aussteller.json``-Map tolerant.

    * Datei fehlt → ``({}, None)`` (kein Warning, Erst-Lauf normal).
    * Pfad-``OSError`` (z. B. Permission-Denied) → ``({}, warning)``.
    * Invalides JSON → ``({}, warning)`` + ``logger.warning(...)``.
    * Valides JSON → ``(data, None)``.

    Der Warning-String soll vom Caller in den Lauf-Report aufgenommen
    werden, damit der Nutzer nachvollziehen kann, warum die Historie
    leer erscheint (siehe 03-RESEARCH.md §Pitfall 6).
    """
    if not path.exists():
        return {}, None
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        msg = f"aussteller.json nicht lesbar ({exc}) — neue Map gestartet"
        logger.warning(msg)
        return {}, msg
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        msg = f"aussteller.json korrupt ({exc.msg}) — neue Map gestartet"
        logger.warning(msg)
        return {}, msg
    if not isinstance(data, dict):
        msg = "aussteller.json hat unerwartetes Top-Level-Format — neue Map gestartet"
        logger.warning(msg)
        return {}, msg
    # Pragmatisches Schema-Forgiveness: defekte Einzel-Einträge werden beim
    # nächsten Update überschrieben — wir validieren NICHT proaktiv, damit
    # ein einzelner Tippfehler nicht die ganze Historie verwirft.
    return data, None


def update(
    history: dict[str, AusstellerEntry],
    aussteller_name: str,
    belegtyp: str,
    year: int,
) -> dict[str, AusstellerEntry]:
    """Ergänzt oder aktualisiert einen Aussteller in ``history`` (in-place).

    * Neuer Key → neuer Eintrag mit ``last_seen_year = year``.
    * Bekannter Key → ``last_seen_year = max(prev, year)`` (D-B5,
      idempotent gegen Mehrfach-Reads und Out-of-Order-Verarbeitung).
    * ``aussteller_name`` und ``fingerprint`` werden bei jedem Update
      überschrieben (letzte Schreibweise gewinnt — relevant z. B. wenn
      eine Bank ihren Marken-Namen geändert hat).

    Gibt ``history`` zurück als Convenience für Chaining; die Mutation
    geschieht trotzdem in-place.
    """
    key = make_key(aussteller_name, belegtyp)
    prev = history.get(key)
    last_year = year if prev is None else max(prev["last_seen_year"], year)
    history[key] = AusstellerEntry(
        aussteller_name=aussteller_name,
        belegtyp=belegtyp,
        last_seen_year=last_year,
        fingerprint=make_fingerprint(aussteller_name, belegtyp, last_year),
    )
    return history


def find_missing(
    history: dict[str, AusstellerEntry],
    seen_keys: set[str],
    current_year: int,
) -> list[AusstellerEntry]:
    """Liefert Vorjahres-Aussteller, die im aktuellen Lauf fehlen (D-B6).

    Filter:

    1. ``last_seen_year >= current_year - 1`` — Aussteller aus direktem
       Vorjahr ODER aus dem aktuellen Jahr (letzteres relevant, falls
       der Lauf mehrere Sub-Sets liest und Konsistenz prüft).
    2. Key ``NOT IN seen_keys`` — wer im aktuellen Lauf bereits gesehen
       wurde, ist nicht „fehlend".

    Verjährte Aussteller (``last_seen_year <= current_year - 2``) werden
    NICHT gemeldet — z. B. wenn eine Bank vor zwei Jahren gewechselt wurde
    und der Nutzer dort kein Konto mehr hat.

    Output ist sortiert nach ``(belegtyp, aussteller_name)`` für stabile
    Report-Snapshots und deterministische Tests.
    """
    threshold = current_year - 1
    candidates = [
        entry
        for key, entry in history.items()
        if entry["last_seen_year"] >= threshold and key not in seen_keys
    ]
    candidates.sort(key=lambda e: (e["belegtyp"], e["aussteller_name"]))
    return candidates


def write_json(path: Path, history: dict[str, AusstellerEntry]) -> None:
    """Schreibt ``history`` atomar nach ``path`` mit ``.bak``-Sicherung (Pitfall 6).

    Ablauf:

    1. Wenn ``path`` existiert: alten Inhalt nach ``path + ".bak.tmp"``
       schreiben, dann ``os.replace(bak_tmp, path + ".bak")`` —
       atomarer Backup-Swap. Der Sicherheits-Snapshot erscheint also
       erst als vollständige Datei, nie als Halb-Write.
    2. Neuer Inhalt wird nach ``path + ".tmp"`` geschrieben — der
       ``.tmp``-Pfad ist explizit, damit Halb-Writes nicht den Pfad
       belegen, den ein paralleler Reader öffnen würde.
    3. ``os.replace(tmp, path)`` — auf POSIX atomar (rename(2)). Der
       Reader sieht entweder den alten oder den neuen Inhalt, niemals
       einen halb geschriebenen.

    JSON-Encoding: ``sort_keys=True`` (deterministische Git-Diffs),
    ``indent=2`` (lesbar), ``ensure_ascii=False`` (Schweizer Typografie
    bleibt erhalten, z. B. „Zürcher").
    """
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    if path.exists():
        bak_path = path.with_suffix(path.suffix + ".bak")
        bak_tmp = path.with_suffix(path.suffix + ".bak.tmp")
        bak_tmp.write_bytes(path.read_bytes())
        os.replace(bak_tmp, bak_path)
    payload = json.dumps(history, sort_keys=True, indent=2, ensure_ascii=False)
    tmp_path.write_text(payload, encoding="utf-8")
    os.replace(tmp_path, path)
