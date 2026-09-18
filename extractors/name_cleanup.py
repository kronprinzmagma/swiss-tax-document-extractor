"""Post-LLM-Cleanup für Namens-Felder.

Das LLM gibt manchmal Personennamen mit unerwünschtem Anhang zurück:
- ``"Muster, Hans, geboren am 01.01.1900"`` (Sanitas — Name + Geburtsdatum)
- ``"Anna Hans, Peter, Lina Hans"`` (mehrere Kinder als Komma-Liste)

Beide Fälle scheitern am Anker-Resolver, weil keine zusammenhängende
Tokenfolge dem Wert entspricht. Cleanup-Heuristik: trailing-junk
abschneiden, bei Listen das erste Element nehmen.

Wirkt nur auf Name-Felder, nicht auf Adressen/Institutionen.
"""
from __future__ import annotations

import re


# "..., geboren am DD.MM.YYYY" oder "geb. ..." am Ende abschneiden
_TRAILING_BIRTHDATE_RE = re.compile(
    r"\s*,?\s*(?:geboren\s+am|geb\.?|geboren)\s+\d{1,2}\.\d{1,2}\.\d{2,4}\s*$",
    flags=re.IGNORECASE,
)


def clean_person_name(value: str) -> str:
    """Entfernt Geburtsdatum-Suffix und nimmt bei Komma-Listen das erste Element."""
    if not value or not value.strip():
        return value
    s = value.strip()
    # 1. Geburtsdatum-Suffix entfernen
    s = _TRAILING_BIRTHDATE_RE.sub("", s).rstrip(",;. ")
    # 2. Komma-Listen mit ≥3 Teilen sind Kinder-/Personen-Listen.
    # 2-teilige "Nachname, Vorname"-Formate bleiben unverändert (Schweizer
    # Standard, kommt in Behörden-Belegen häufig vor).
    if "," in s:
        parts = [p.strip() for p in s.split(",")]
        if len(parts) >= 3:
            s = parts[0]
    return s.strip()


def clean_aussteller_name(value: str) -> str:
    """Generischer Trim für Aussteller-Namen — entfernt Adress-Anhänge.

    Konservativer als clean_person_name: nur trailing-Adress-Patterns
    (PLZ + Ort, Postfach-Strings).
    """
    if not value:
        return value
    s = value.strip()
    # Trailing PLZ + Ort: "Foo AG, 8000 Zürich" → "Foo AG"
    s = re.sub(r"\s*,?\s*\d{4}\s+[A-ZÀ-ſ][\w-]+\s*$", "", s)
    return s.strip()
