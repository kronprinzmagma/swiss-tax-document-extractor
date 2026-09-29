"""Schweizer Zahlen- und Datums-Normalisierung.

Stellt drei reine Funktionen für die Lohnausweis-Pipeline bereit:

* :func:`parse_swiss_amount` — robustes Parsing aller gängigen CH-Betragsformate
  (Apostroph-Tausendertrenner U+0027/U+2019/U+2018, NBSP/NNBSP/THINSP-Spaces,
  Komma- oder Punkt-Dezimaltrenner, optionaler Currency-Präfix).
* :func:`parse_swiss_date` — Whitelist-basiertes Datum-Parsing
  (TT.MM.JJJJ, T.M.JJJJ, JJJJ-MM-TT) mit ISO-String-Output.
* :func:`normalize` — Single-Source-of-Truth für Anker-Match-Vergleiche
  zwischen LLM-Output und PDF-Wort-Snippet (D-B5).

Doc-Strings auf Schweizer Hochdeutsch, API-Bezeichner in Englisch.
Pure Modul ohne I/O.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from decimal import Decimal, InvalidOperation

# Apostroph-Klasse: U+0027 ('), U+2019 (’), U+2018 (‘), U+02BC (ʼ).
# Diese Zeichen sind in Schweizer Beträgen reine Tausender-Separatoren und
# müssen für den Anker-Match äquivalent behandelt werden (RESEARCH §Pitfall 2).
_APOSTROPH_CLASS = "['‘’ʼ]"

# Whitespace-Klasse: regulärer Space, NBSP (U+00A0), NNBSP (U+202F),
# THINSP (U+2009), Tabulator und Zeilenumbruch.
_WHITESPACE_CLASS = "[    \t\n\r]"

# Akzeptierte Form nach voller Normalisierung: optionales Vorzeichen,
# Ziffern, optional Punkt + 1–2 Nachkommastellen.
_AMOUNT_RE = re.compile(r"^[+-]?\d+(?:\.\d{1,2})?$")

# Punkt als Tausender-Separator: Gruppen von exakt 3 Ziffern, KEIN Dezimalteil
# ("7.793", "1.234.567"). CHF hat maximal 2 Rappen-Stellen — 3 Ziffern nach
# dem Punkt sind immer Tausendertrennung.
#
# Die führende Gruppe MUSS mit [1-9] beginnen: "0.125" darf NICHT als
# Tausendertrennung (→ 125, Faktor-1000-Bug) interpretiert werden — eine
# führende 0 vor einem Punkt ist nie ein Tausenderblock, sondern ein
# (ungültiger) Dezimalwert mit 3 Nachkommastellen → fällt durch zu
# _AMOUNT_RE, das wegen 3 Nachkommastellen NICHT matcht → ValueError.
_DOT_THOUSANDS_RE = re.compile(r"^[+-]?[1-9]\d{0,2}(?:\.\d{3})+$")

# Strich-/Leer-Repräsentationen, die im Lohnausweis „kein Wert" bedeuten.
_NULL_TOKENS = frozenset({"", "-", "–", "—", "–.–", "-.-", ".--", ".—"})

# Currency-Präfixe, die vor dem Betrag stehen können — werden für das
# numerische Parsing entfernt, sind in :func:`normalize` aber erlaubt.
_CURRENCY_PREFIX_RE = re.compile(r"^(CHF|Fr\.?|SFr\.?|EUR|USD)", re.IGNORECASE)

_DATE_FORMATS = ("%d.%m.%Y", "%Y-%m-%d")


def normalize(s: str) -> str:
    """Vereinheitlicht einen String für den Anker-Match.

    Vorgehen (NFKC + Klassen-Strip + Lower-Case):

    1. NFKC-Normalisierung — vereinheitlicht z. B. zusammengesetzte Diakritika.
    2. Apostroph-Klasse (U+0027/U+2018/U+2019/U+02BC) entfernen — sie sind
       reine Tausender-Separatoren in CH-Beträgen und tragen keine Bedeutung
       für den Wert-Vergleich.
    3. Whitespace-Klasse inkl. NBSP/NNBSP/THINSP entfernen — Schweizer PDFs
       benutzen häufig NBSP/NNBSP statt Apostroph als Tausender-Trennung.
    4. Lower-Case + Strip.

    Zwei Strings gelten genau dann als „äquivalent für den Anker-Match",
    wenn ``normalize(a) == normalize(b)``. Diese Funktion ist Single-Source-
    of-Truth — sowohl :func:`parse_swiss_amount` (Eingabe-Cleanup) als
    auch :mod:`extractors.anchor_resolver` rufen sie auf.
    """
    if s is None:  # defensiv, obwohl der Typ-Hint str ist
        return ""
    out = unicodedata.normalize("NFKC", s)
    out = re.sub(_APOSTROPH_CLASS, "", out)
    out = re.sub(_WHITESPACE_CLASS, "", out)
    return out.lower().strip()


def parse_swiss_amount(s: str | None) -> Decimal | None:
    """Parst einen Schweizer Betrag in :class:`Decimal`.

    Akzeptiert (Roundtrip-getestet):

    * Apostroph-Tausendertrennung — ``"1'234.50"``, ``"1’234.50"`` (U+2019).
    * Space/NBSP/NNBSP-Tausendertrennung — ``"1 234.50"``, ``"1 234.50"``.
    * Komma als Dezimaltrenner — ``"1 234,50"`` → ``Decimal("1234.50")``.
    * Currency-Präfix — ``"CHF 1'234.50"``, ``"Fr. 1'234.50"``.
    * Negativbeträge — ``"-1'234.50"``.

    Liefert ``None`` für leere Strings, ``None``-Input oder Strich-
    Repräsentationen (``"-"``, ``"–"``, ``"–.–"``, …).

    Wirft :class:`ValueError` bei nicht-parsbaren Strings, damit Aufrufer
    den Reason-Code ``schema_validation_failed`` setzen können.
    """
    if s is None:
        return None
    raw = unicodedata.normalize("NFKC", s).strip()
    # Whitespace-Klasse (inkl. NBSP/NNBSP/THINSP) entfernen.
    raw = re.sub(_WHITESPACE_CLASS, "", raw)
    # Currency-Präfix entfernen.
    raw = _CURRENCY_PREFIX_RE.sub("", raw)
    # Strich-Varianten als „kein Wert" interpretieren.
    if raw in _NULL_TOKENS:
        return None
    # Müllwert-Guard (R6, 260612-m8t): nach dem Currency-Strip dürfen nur noch
    # Betrags-Zeichen (Ziffern, Punkt, Komma, Apostroph-Klasse, Vorzeichen)
    # übrig sein. Klammern (`{}` `[]` `()`) oder verbliebene Buchstaben sind
    # ein klares Indiz für einen LLM-Halluzinations-Müllwert wie "{CHF 8.718}"
    # — der NICHT als Best-Effort-Zahl (8718) durchgehen darf. Explizit, damit
    # der Guard nicht von der genauen Form der nachgelagerten Regexe abhängt.
    if re.search(r"[^\d.,'‘’ʼ+-]", raw):
        raise ValueError(
            f"Unparsbarer CH-Betrag (Müllwert): {s!r} (normalisiert: {raw!r})"
        )
    # Apostroph-Varianten (Tausender-Separator) entfernen.
    raw = re.sub(_APOSTROPH_CLASS, "", raw)
    # Komma als Dezimaltrenner → Punkt; Komma + Punkt → Komma als Tausender.
    if "," in raw and "." not in raw:
        raw = raw.replace(",", ".")
    elif "," in raw and "." in raw:
        raw = raw.replace(",", "")
    # Punkt-Tausendertrennung ("7.793" → 7793) — siehe _DOT_THOUSANDS_RE.
    if _DOT_THOUSANDS_RE.match(raw):
        raw = raw.replace(".", "")
    if not _AMOUNT_RE.match(raw):
        raise ValueError(f"Unparsbarer CH-Betrag: {s!r} (normalisiert: {raw!r})")
    try:
        return Decimal(raw)
    except InvalidOperation as e:  # pragma: no cover — Regex schliesst das aus
        raise ValueError(f"Decimal-Konvertierung fehlgeschlagen: {s!r}") from e


def parse_swiss_date(s: str) -> str:
    """Parst ein Datum aus einer Whitelist und liefert ISO-Output ``YYYY-MM-DD``.

    Akzeptierte Formate:

    * ``TT.MM.JJJJ`` (z. B. ``"31.12.2024"``)
    * ``T.M.JJJJ`` mit einstelligen Tagen/Monaten (z. B. ``"1.1.2024"``)
    * ``JJJJ-MM-TT`` (Idempotenz)

    Wirft :class:`ValueError` bei allem anderen — bewusst keine Auto-
    Detection à la ``dateutil.parser.parse``, weil mehrdeutige Formate
    wie ``01/02/2024`` (US vs. EU) im Schweizer Kontext nicht eindeutig
    sind und stille Fehler erzeugen würden.
    """
    if not s:
        raise ValueError(f"Unparsbares Datum: {s!r}")
    raw = s.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    raise ValueError(f"Unparsbares Datum: {s!r}")
