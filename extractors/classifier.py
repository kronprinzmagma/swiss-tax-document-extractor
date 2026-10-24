"""Belegtyp-Klassifikator. Phase 2: 4-Belegtyp-Score-Cascade mit Tuple-Return.
Phase 3 Wave 3 (Plan 03-04): erweitert um 3 weitere Header-Regex
(``wertschriftenverzeichnis``, ``spenden``, ``berufsauslagen``).
Phase 3 Wave 4 (Plan 03-05): ergänzt 4 weitere Header-Regex
(``kinderbetreuung``, ``hypothek_zinsbestaetigung``, ``liegenschaftsunterhalt``,
``krankheitskosten``) — _PATTERNS hat damit 11 Einträge (vollständige Belegtyp-
Abdeckung der Phase-3-Liste).
Phase 3 Wave 5 (Plan 03-06): Cascade aus CLS-02 step 3 — bei
``max_score == 0`` wird :func:`extractors.llm_classifier.classify_via_llm`
aufgerufen (1 zusätzlicher Qwen-Call mit ``ClassifierFallbackResult``-Schema).
Threshold 0.6 (D-C4); darunter ⇒ ``("unknown", UNSUPPORTED_TYPE)``.

Cascade aus CLS-02 (aussteller.json → Regex → LLM-Fallback) — Phase 3
aktiviert step 3 (LLM-Fallback); step 1 (aussteller.json-Lookup) ist
v1.x-Backlog. Der Klassifikator erkennt elf Belegtypen via Header-Regex
und nutzt das LLM nur als letzte Instanz, wenn kein Regex matcht:

* ``lohnausweis`` (DE/FR/EN, Form 11)
* ``bank_zinsausweis`` (Zinsausweis / Steuerausweis / Vermögensausweis)
* ``kk_praemienbescheinigung`` (Krankenkasse / Prämienbescheinigung)
* ``saeule_3a`` (Säule 3a / 3a-Stiftung / gebundene Vorsorge)
* ``wertschriftenverzeichnis`` (Depot-Auszug / Wertschriftenverzeichnis, D-A1)
* ``spenden`` (Spendenbescheinigung / Zuwendungsbestätigung, D-E1)
* ``berufsauslagen`` (Weiterbildung / Kursbestätigung, D-E2)
* ``kinderbetreuung`` (Kita / Hort / Tagesfamilie, D-E3)
* ``hypothek_zinsbestaetigung`` (Hypothek-Zinsbestätigung / Schuldzinsausweis, D-E4)
* ``liegenschaftsunterhalt`` (Handwerker-Rechnung / Unterhaltskosten, D-E5)
* ``krankheitskosten`` (Arzt-/Apotheken-/Spital-Rechnung, D-E6)

Score-Cascade (D-D2):

* Score pro Belegtyp = Anzahl distinct Pattern-Matches im First-Page-Head
  (HEAD_WORDS Wörter; Phase 2 erweitert von 50 → 200, da Bank/KK/3a-Header
  oft tiefer im PDF stehen — RESEARCH §1).
* Best-Score-Belegtyp gewinnt. Bei Score-Gleichstand zwischen ≥ 2
  nicht-Lohnausweis-Typen → ``("unknown", MULTIPLE_BELEGTYP_MATCH)``
  (D-D3). Falls Lohnausweis am Tie beteiligt ist, gewinnt Lohnausweis
  (Stabilität für Phase-1-Verträge — RESEARCH-Empfehlung).
* Score == 0 für alle → ``("unknown", UNSUPPORTED_TYPE)`` (D-D4).
"""
from __future__ import annotations

import re

from extractors.schema import Belegtyp, ReasonCode

HEAD_WORDS = 200

# CID-Escape-Map: pdfplumber liefert Umlaute manchmal als (cid:NNN).
# Ohne Normalisierung würde "Pr(cid:228)mien" nicht gegen "Prämien" matchen.
_CID_MAP: dict[int, str] = {
    196: "Ä", 214: "Ö", 220: "Ü",
    228: "ä", 246: "ö", 252: "ü",
    223: "ß", 8217: "'",
}
_CID_RE = re.compile(r"\(cid:(\d+)\)")


def _normalize_cid(text: str) -> str:
    """Ersetzt (cid:NNN) durch das entsprechende Unicode-Zeichen (falls bekannt)."""
    return _CID_RE.sub(lambda m: _CID_MAP.get(int(m.group(1)), ""), text)


LOHNAUSWEIS_HEADER_RE = re.compile(
    r"\b(Lohnausweis|Certificat\s+de\s+salaire|Salary\s+Statement|Form\.?\s*11)\b",
    flags=re.IGNORECASE,
)
BANK_HEADER_RE = re.compile(
    r"\b(Zinsausweis|Steuerausweis|Konto-?auszug|Vermögensausweis"
    r"|Kapitalbescheinigung|Saldoverzeichnis|Guthabensaldo|Zinsabschluss|ZINSABSCHLUSS)\b",
    flags=re.IGNORECASE,
)
KK_HEADER_RE = re.compile(
    r"(Krankenkasse|Krankenversicherer|Prämienbescheinigung"
    r"|Prämienübersicht|Prämien-?\s*und\s*Kosten[üu]bersicht"
    r"|Bescheinigung über die Bezahlung)",
    flags=re.IGNORECASE,
)
SAEULE_3A_HEADER_RE = re.compile(
    r"(Säule\s*3a|3a-Konto|3a-Stiftung|gebundene Vorsorge"
    r"|Form\.?\s*21\b|Vorsorgestiftung\s+3a|STIFTUNG-Z\s+Vorsorgestiftung)",
    flags=re.IGNORECASE,
)
WERTSCHRIFTEN_HEADER_RE = re.compile(
    r"\b(Wertschriften(?:verzeichnis|auszug)|Depot-?(?:auszug|übersicht|verzeichnis)"
    r"|Steuer-?Reporting|Portfolio[- ]?Auszug|Custodian|Vermögensverwalter)\b",
    flags=re.IGNORECASE,
)
SPENDEN_HEADER_RE = re.compile(
    r"\b(Spende(?:n)?(?:quittung|bescheinigung)?|Zuwendungs[- ]?bestätigung)\b",
    flags=re.IGNORECASE,
)
BERUFSAUSLAGEN_HEADER_RE = re.compile(
    r"\b(Weiterbildung(?:s|skurs|skosten)?|Berufsauslagen|Kursbestätigung|Aus-?\s*und\s*Weiterbildung)\b",
    flags=re.IGNORECASE,
)
KINDERBETREUUNG_HEADER_RE = re.compile(
    r"\b(Kinderbetreuung|Kita|Krippe|Tagesfamilie|Hort|Mittagstisch|Betreuung(?:sbestätigung|skosten)?)\b",
    flags=re.IGNORECASE,
)
HYPOTHEK_HEADER_RE = re.compile(
    r"\b(Hypothek(?:ar)?(?:zins(?:bestätigung|ausweis)?|kredit)?|Schuld-?Zins(?:bestätigung|ausweis)?)\b",
    flags=re.IGNORECASE,
)
LIEGENSCHAFTSUNTERHALT_HEADER_RE = re.compile(
    r"\b(Liegenschaftsunterhalt|Unterhaltskosten|Handwerker-?(?:rechnung|Quittung)|Renovation(?:srechnung)?)\b",
    flags=re.IGNORECASE,
)
KRANKHEITSKOSTEN_HEADER_RE = re.compile(
    r"\b(Krankheitskosten|Arzt-?rechnung|Apotheken-?rechnung|Behandlung(?:s|skosten)?|Spital-?rechnung)\b",
    flags=re.IGNORECASE,
)
# Freizügigkeitskonto (2. Säule, Finding E5). OCR-tolerant: das "ü" wird in
# Scans/OCR oft als "u" gelesen ("Freizugigkeit", "Fronzugigkeitsstiftung").
# Greift auch auf "Rubrik FZK", "Vorsorgetechnische Daten" und
# "Freizügigkeitsstiftung". Muss bei FZK-Belegen STÄRKER ziehen als
# BANK_HEADER_RE (das "Saldoverzeichnis"/"Zinsabschluss" matchen kann).
FREIZUEGIGKEIT_HEADER_RE = re.compile(
    r"\b(Freiz[üu]gigkeit(?:skonto|sstiftung|sguthaben|sleistung)?"
    r"|Fronz[üu]gigkeit(?:sstiftung)?"
    r"|FZK"
    r"|Vorsorgetechnische(?:\s+Daten)?)\b",
    flags=re.IGNORECASE,
)

_PATTERNS: dict[Belegtyp, re.Pattern[str]] = {
    "lohnausweis": LOHNAUSWEIS_HEADER_RE,
    "bank_zinsausweis": BANK_HEADER_RE,
    "kk_praemienbescheinigung": KK_HEADER_RE,
    "saeule_3a": SAEULE_3A_HEADER_RE,
    "wertschriftenverzeichnis": WERTSCHRIFTEN_HEADER_RE,
    "spenden": SPENDEN_HEADER_RE,
    "berufsauslagen": BERUFSAUSLAGEN_HEADER_RE,
    "kinderbetreuung": KINDERBETREUUNG_HEADER_RE,
    "hypothek_zinsbestaetigung": HYPOTHEK_HEADER_RE,
    "liegenschaftsunterhalt": LIEGENSCHAFTSUNTERHALT_HEADER_RE,
    "krankheitskosten": KRANKHEITSKOSTEN_HEADER_RE,
    "freizuegigkeitskonto": FREIZUEGIGKEIT_HEADER_RE,
}


def _score(head: str, pattern: re.Pattern[str]) -> int:
    """Liefert Anzahl distinct (case-insensitive) Pattern-Matches im Head."""
    return len({m.group(0).lower() for m in pattern.finditer(head)})


def classify(words: list[dict]) -> tuple[Belegtyp, ReasonCode | None]:
    """Klassifiziert ein Wort-Listen-Dokument als einen der 4 Belegtypen.

    Score-Cascade über alle vier Header-Regex; Tie-Wins-Regel zugunsten
    Lohnausweis bei Mehrdeutigkeit. Bei echter Mehrdeutigkeit zwischen
    zwei nicht-Lohnausweis-Typen wird die Klassifikation verweigert
    (``MULTIPLE_BELEGTYP_MATCH``). Wenn keine Pattern matcht, wird
    ``UNSUPPORTED_TYPE`` zurückgegeben (D-D4 — niemals raten).

    Args:
        words: pdf_reader.read_pdf-Output (Liste von Wort-Dicts).
    Returns:
        ``(belegtyp, None)`` bei Erfolg, sonst ``("unknown", ReasonCode)``.
    """
    if not words:
        return ("unknown", ReasonCode.UNSUPPORTED_TYPE)

    head = _normalize_cid(" ".join(w.get("text", "") for w in words[:HEAD_WORDS]))

    scores: dict[Belegtyp, int] = {
        bt: _score(head, pat) for bt, pat in _PATTERNS.items()
    }
    max_score = max(scores.values())

    if max_score == 0:
        # CLS-02 step 3 (Plan 03-06): kein Header-Match — LLM-Fallback fragen.
        # Lazy-Import, damit Phase-2-Tests ohne Ollama weiter durchlaufen
        # (bei score > 0 wird der Fallback gar nicht erst aufgerufen).
        try:
            from extractors.llm_classifier import classify_via_llm

            return classify_via_llm(words)
        except Exception:
            return ("unknown", ReasonCode.UNSUPPORTED_TYPE)

    # Freizügigkeitskonto-Vorrang (E5): sobald das FZK-Pattern ÜBERHAUPT matcht,
    # gewinnt freizuegigkeitskonto — auch wenn BANK_HEADER_RE ("Saldoverzeichnis",
    # "Zinsabschluss") gleich viele oder mehr Treffer hat. FZK-Belege tragen oft
    # Bank-Vokabular, sind aber 2. Säule (out-of-scope) und dürfen NICHT als
    # bank_zinsausweis ins Wertschriftenverzeichnis wandern.
    if scores.get("freizuegigkeitskonto", 0) > 0:
        return ("freizuegigkeitskonto", None)

    winners = [bt for bt, s in scores.items() if s == max_score]

    # Tie-Wins-Regel: Lohnausweis dominiert bei Mehrdeutigkeit (Stabilität).
    if "lohnausweis" in winners:
        return ("lohnausweis", None)

    if len(winners) > 1:
        return ("unknown", ReasonCode.MULTIPLE_BELEGTYP_MATCH)

    return (winners[0], None)
