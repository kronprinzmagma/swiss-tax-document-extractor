"""Feld-spezifische Datums-/Jahres-Inferenz.

Codex' Punkt 5: ``jahr`` und ``periode_*`` werden heute via generischem
Verbatim-Match auf 4-stellige Zahlen aufgelöst — das findet bei
anonymisierten Belegen oft falsche "Jahre" (z.B. ``1557``, ``1999``).

Robuste Quellen, in dieser Reihenfolge:

1. **Dateiname** — fast immer enthält der PDF-Name Datum oder Jahr.
   ``Kontoauszug_01.01.2022_-_31.12.2022_-_...`` → 2022.
   ``TAX_P_..._20230101...`` → Generierungsdatum Jan 2023 → Steuerjahr 2022
   (``filename_rule:postfinance_early_year``).
2. **Text-Header** — Label-getriebene Suche wie "Steuerjahr 2022" oder
   "Periode vom 01.01.2022 bis 31.12.2022".

Steuerjahr-Heuristik: BANK-P-Belege werden Anfang Folgejahr generiert
(``20230101`` für Steuerjahr 2022). Wenn Dateiname und Header sich
widersprechen, gewinnt der Header.

Rückgabewerte enthalten immer die ``inference_source`` als zweites Element
des Tupels (``None`` wenn keine Inferenz möglich).
"""
from __future__ import annotations

import re
from pathlib import Path

# Mögliche Jahres-Token im Dateinamen — 4-stellig, plausibel Steuerjahr.
# Non-digit lookaround statt `\b`, weil `_2022_tax` keine Wortgrenze zwischen
# Unterstrich und Ziffer hat (beide sind Wortzeichen).
_YEAR_RE = re.compile(r"(?<!\d)(20\d{2})(?!\d)")

# DD.MM.YYYY oder YYYY-MM-DD in Dateinamen
_DATE_RE = re.compile(
    r"(?:(\d{2})[.\-/](\d{2})[.\-/](20\d{2}))"
    r"|(?:(20\d{2})[.\-/](\d{2})[.\-/](\d{2}))"
    r"|(?:(20\d{2})(\d{2})(\d{2}))"  # YYYYMMDD ohne Separator
)

# BANK-P-Belege: Dateiname enthält Generierungsdatum als YYYYMMDD.
# Wenn Monat = 01 oder 02 → Steuerjahr = YYYY - 1 (Dokument deckt Vorjahr ab).
# Abgedeckte Muster:
#   TAX_P_CH..._20230101...  → Steuerjahr 2022
#   REP_P_CH..._20230101...  → Steuerjahr 2022
#   Portfolio-Wertentwicklung_123456_20230104  → Steuerjahr 2022
#   Kontoauszug_123456_20230101               → Steuerjahr 2022
#   Zinsabrechnung_123456_20230104            → Steuerjahr 2022
# NICHT betroffen: Jahresgebühr (Gebühr für laufendes Jahr, kein Rückbezug).
_PF_EARLY_YEAR_RE = re.compile(
    r"^(?:TAX_P|REP_P)_.*?_(20\d{2})(0[12])\d{2}",
    re.IGNORECASE,
)
_PF_ACCOUNT_EARLY_YEAR_RE = re.compile(
    r"^(?:Portfolio-Wertentwicklung|Kontoauszug|Zinsabrechnung|Zins-und-Saldo)"
    r"_\d+_.*?(20\d{2})(0[12])\d{2}",
    re.IGNORECASE,
)


def _plausible_tax_year(years: list[int]) -> int | None:
    """Wählt das wahrscheinlichste Steuerjahr aus mehreren Kandidaten.

    Heuristik: aktuelle Steuerjahre liegen 1-2 Jahre in der Vergangenheit.
    Wenn ein Beleg "20221231" und "20230101" enthält (BANK-P-Pattern),
    ist das Steuerjahr das ÄLTERE (2022). Wenn alle Jahre gleich → das.
    """
    if not years:
        return None
    # Heuristik: kleinster Wert ist meist das Steuerjahr (Stichtag 31.12.YYYY).
    return min(years)


def infer_jahr_from_filename(pdf_name: str) -> tuple[str, str] | tuple[None, None]:
    """Extrahiert das wahrscheinliche Steuerjahr + inference_source aus dem Dateinamen.

    Rückgabe: (jahr_str, inference_source) oder (None, None).

    Beispiele:
    * ``2022_tax_statement.pdf`` → ``("2022", "filename_rule:year_in_name")``
    * ``Kontoauszug 01.01.2022 - 31.12.2022 - CH...pdf`` → ``("2022", "filename_rule:year_in_name")``
    * ``TAX_P_..._20230101...pdf`` → ``("2022", "filename_rule:postfinance_early_year")``
    * ``REP_P_..._20230101...pdf`` → ``("2022", "filename_rule:postfinance_early_year")``
    * ``Steuernachweis 2022 Muster.pdf`` → ``("2022", "filename_rule:year_in_name")``
    """
    stem = Path(pdf_name).stem

    # 0. BANK-P early-year Spezialregel (TAX_P / REP_P / Portfolio etc.).
    m = _PF_EARLY_YEAR_RE.match(stem) or _PF_ACCOUNT_EARLY_YEAR_RE.match(stem)
    if m:
        gen_year = int(m.group(1))
        return str(gen_year - 1), "filename_rule:postfinance_early_year"

    years_int: list[int] = []

    # 1. Volldatums-Matches zuerst (DD.MM.YYYY etc.) → Jahr extrahieren.
    for m in _DATE_RE.finditer(stem):
        groups = m.groups()
        # je nach Match-Variante steht das Jahr an anderer Stelle
        if groups[2]:    # DD.MM.YYYY
            years_int.append(int(groups[2]))
        elif groups[3]:  # YYYY-MM-DD
            years_int.append(int(groups[3]))
        elif groups[6]:  # YYYYMMDD
            years_int.append(int(groups[6]))

    # 2. Fallback: einfaches 4-stelliges Jahr-Pattern
    if not years_int:
        for m in _YEAR_RE.finditer(stem):
            years_int.append(int(m.group(1)))

    year = _plausible_tax_year(years_int)
    if year is not None:
        return str(year), "filename_rule:year_in_name"
    return None, None


def infer_jahr_from_text(text: str, hint_labels: list[str] | None = None) -> tuple[str, str] | tuple[None, None]:
    """Sucht nach Steuerjahr-Label im Volltext.

    Rückgabe: (jahr_str, inference_source) oder (None, None).

    Pattern wie "Steuerjahr 2022", "Steuerperiode 2022", "Jahresübersicht 2022"
    haben hohe Spezifität — der Label-Kontext filtert Transaktions-Jahre
    (im Stream "06.01.2022 Belastung ..." gibt es viele 2022er ohne
    Steuerjahr-Bezug).
    """
    hint_labels = hint_labels or [
        "Steuerjahr", "Steuerperiode", "Jahresübersicht",
        "Stichtag 31.12", "per 31.12", "31. Dezember",
    ]
    for label in hint_labels:
        # 50 Zeichen nach dem Label nach einem 20xx-Jahr suchen
        m = re.search(rf"{re.escape(label)}.{{0,50}}?(20\d{{2}})",
                      text, flags=re.IGNORECASE)
        if m:
            return m.group(1), "text:steuerjahr_label"
    return None, None


def infer_jahr(pdf_name: str, text: str | None = None) -> tuple[str, str] | tuple[None, None]:
    """Kombinierte Inferenz: Header gewinnt vor Dateiname.

    Rückgabe: (jahr_str, inference_source) oder (None, None).

    Bei Konflikt (Header sagt 2022, Dateiname 2023 wegen Generierungs-Datum)
    nimmt der Header recht — der wird vom Aussteller bewusst gesetzt.
    """
    if text:
        from_text, source = infer_jahr_from_text(text)
        if from_text is not None:
            return from_text, source
    return infer_jahr_from_filename(pdf_name)
