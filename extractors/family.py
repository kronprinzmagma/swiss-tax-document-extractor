"""Familien-Konfiguration für Person-Inferenz pro Beleg (Plan 02-02).

Lädt die optionale ``family.yaml`` aus dem Arbeitsverzeichnis und liefert
ein pydantic-validiertes :class:`Family`-Objekt. Decisions D-A1..A6 aus
``02-CONTEXT.md`` werden hier umgesetzt:

* D-A1 — Standard-Pfad ist ``family.yaml`` im aktuellen Arbeitsverzeichnis.
* D-A2 — Wenn die Datei nicht existiert, gibt :func:`load_family` ``None``
  zurück (graceful, ohne Crash). Person-Inferenz ist dann disabled.
* D-A3 — Schema: ``members`` ist eine Liste von :class:`FamilyMember` mit
  ``role`` (closed enum), ``first_name``, ``last_name`` (Pflicht), sowie
  optionalen ``birth_date`` (YYYY-MM-DD) und ``ahv`` (756.xxxx.xxxx.xx).
* D-A4 — Wenn ein Member ``ahv`` gesetzt hat, wird in :attr:`Family.warnings`
  ein Privacy-Hinweis hinterlegt. Es findet **kein** externer AHV-Lookup statt.
* D-A5 — Roles sind closed enum: ``mann | frau | kind1 | kind2``. Maximal
  4 Members; jede Role darf nur einmal vorkommen.
* D-A6 — Rückgabe ``Family | None``: ``None`` heisst „keine Konfig,
  Person-Inferenz disabled".

Reines Modul mit genau einer I/O-Operation (``yaml.safe_load`` in
:func:`load_family`). Keine externen Lookups, keine PII in Logs.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator


# Closed enum für Familienrollen (D-A5). Kein ``kind3+`` in v1.
Role = Literal["mann", "frau", "kind1", "kind2"]

# Plausibility-Regex für Schweizer AHV-Nummer (13-stellig im Format
# 756.xxxx.xxxx.xx). Kein Prüfziffer-Check — wir machen keine externen
# Lookups (D-A4); diese Regex schützt nur vor offensichtlichen Eingabefehlern.
_AHV_RE = re.compile(r"^756\.\d{4}\.\d{4}\.\d{2}$")


class FamilyMember(BaseModel):
    """Ein einzelnes Familienmitglied mit Pflicht- und optionalen Feldern."""

    model_config = ConfigDict(extra="forbid")

    role: Role
    first_name: str = Field(min_length=1)
    last_name: str = Field(min_length=1)
    birth_date: str | None = None
    ahv: str | None = None

    @field_validator("birth_date", mode="before")
    @classmethod
    def _validate_birth_date(cls, v):
        """Akzeptiert ISO-Datum ``YYYY-MM-DD`` als String oder ``datetime.date``.

        YAML parst ISO-Datumsstrings automatisch zu :class:`datetime.date`;
        dieser Validator coerced beide Eingabeformen zum kanonischen
        ISO-String-Format und lehnt alles andere ab.
        """
        if v is None:
            return None
        if isinstance(v, date) and not isinstance(v, datetime):
            return v.isoformat()
        if isinstance(v, datetime):
            return v.date().isoformat()
        if isinstance(v, str):
            try:
                datetime.strptime(v, "%Y-%m-%d")
            except ValueError as e:
                raise ValueError(
                    f"birth_date muss im Format YYYY-MM-DD vorliegen, erhalten: {v!r}"
                ) from e
            return v
        raise ValueError(f"birth_date hat unerwarteten Typ: {type(v).__name__}")

    @field_validator("ahv")
    @classmethod
    def _validate_ahv(cls, v: str | None) -> str | None:
        """Plausibility-Check für AHV (756.xxxx.xxxx.xx). Kein externer Lookup (D-A4)."""
        if v is None:
            return None
        if not _AHV_RE.match(v):
            raise ValueError(
                f"AHV muss dem Format 756.xxxx.xxxx.xx entsprechen, erhalten: {v!r}"
            )
        return v


class Family(BaseModel):
    """Familien-Konfiguration mit aggregierten Privacy-Warnungen."""

    model_config = ConfigDict(extra="forbid")

    members: list[FamilyMember] = Field(min_length=1, max_length=4)
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_unique_roles_and_collect_warnings(self) -> "Family":
        """Stellt sicher dass jede Role nur einmal vorkommt und sammelt AHV-Warnungen."""
        seen_roles: set[str] = set()
        for m in self.members:
            if m.role in seen_roles:
                raise ValueError(
                    f"Role {m.role!r} kommt mehrfach vor — jede Role darf nur einmal "
                    f"belegt sein (D-A5)."
                )
            seen_roles.add(m.role)
        # AHV-Privacy-Warnungen aggregieren (D-A4).
        for m in self.members:
            if m.ahv is not None:
                self.warnings.append(
                    f"AHV-Feld bei {m.role} gesetzt — Privacy-Warnung: AHV ist PII "
                    f"und sollte nur gespeichert werden, wenn unbedingt nötig."
                )
        return self


def load_family(path: Path | None = None) -> Family | None:
    """Lädt ``family.yaml`` und liefert ein :class:`Family` oder ``None``.

    Verhalten:

    * ``path=None`` → benutzt ``Path("family.yaml")`` im aktuellen Arbeits-
      verzeichnis (D-A1).
    * Datei existiert nicht → ``None`` (D-A2, graceful, kein Crash).
    * YAML-Parse-Fehler → :class:`ValueError` mit klarer Meldung.
    * Pydantic-Validation-Fehler → :class:`ValueError` mit den Feldfehlern.
    * Erfolg → :class:`Family`-Instanz.

    Diese Funktion macht **keinen** externen Lookup (D-A4). Die geladene
    Konfiguration lebt im Speicher; Aufrufer sind dafür verantwortlich, dass
    AHV-Werte nicht in Logs landen — siehe :attr:`Family.warnings`.
    """
    target = path if path is not None else Path("family.yaml")
    if not target.exists():
        return None
    try:
        raw = yaml.safe_load(target.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ValueError(f"family.yaml ist kein gültiges YAML: {e}") from e
    if raw is None:
        raise ValueError("family.yaml ist leer.")
    try:
        return Family.model_validate(raw)
    except ValidationError as e:
        raise ValueError(f"family.yaml schlug Pydantic-Validation fehl:\n{e}") from e
