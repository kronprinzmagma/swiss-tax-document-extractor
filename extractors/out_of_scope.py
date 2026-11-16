"""Out-of-Scope-Marker: Belege, die zwar erkennbar sind, für die User-Familie
aber bewusst nicht extrahiert werden müssen.

Anders als `UNSUPPORTED_TYPE` (Klassifikator hat nichts erkannt) markiert
``OUT_OF_SCOPE`` eine *fachliche* Entscheidung: das Tool versteht den
Beleg, aber der User braucht ihn nicht (Codex' Punkt 4 "bewusst unsupported").

Beispiel: BANK-P-Jahresgebühren (Vermögensverwaltungskosten) sind für
Familien, die den 3‰-Pauschalabzug nutzen, irrelevant.

Pflege: ``OUT_OF_SCOPE_PATTERNS`` ist die Single-Source-of-Truth. Pattern
muss spezifisch genug sein, dass es echte Steuerbelege NICHT falsch-positiv
matcht.
"""
from __future__ import annotations

import re

# (Pattern, Belegtyp-Hinweis, Begründung für den User-Report)
# Pattern muss robust gegen PDF-Encoding sein — pdfplumber liefert Umlaute
# manchmal als "(cid:252)" für ü. Wir matchen die ASCII-Stamm-Form "Jahresgeb"
# plus einen kontextuellen Anker (Konto/Depot/Wertschriften/Transaktionsbeleg),
# damit z.B. "Jahresgebühr Mitgliedschaft" nicht falsch-positiv klassifiziert wird.
OUT_OF_SCOPE_PATTERNS: list[tuple[re.Pattern[str], str, str]] = [
    # BANK-P Jahresgebühr: Dateiname enthält "Jahresgebühr" direkt.
    # Nur Filename-Match genügt — kein "Konto/Depot"-Kontextanker nötig.
    (
        re.compile(r"Jahresgeb(?:ü|u)hr", flags=re.IGNORECASE),
        "vermoegensverwaltungskosten",
        "Familie nutzt Pauschalabzug 3‰ (Ziffer 14) — Einzelbelege nicht erforderlich",
    ),
    (
        re.compile(
            r"\b(?:Depot-?|Konto-?)(?:Verwaltungs|F(?:ü|u)hrungs|Geb(?:ü|u)hren)abrechnung\b",
            flags=re.IGNORECASE,
        ),
        "vermoegensverwaltungskosten",
        "Familie nutzt Pauschalabzug 3‰ (Ziffer 14)",
    ),
    # Corporate-Action-Abrechnungen: einzelne Transaktion (Dividende,
    # Bezugsrecht etc.), nicht WSV-Vollauszug. Die VRS dieser einzelnen
    # Position fliesst ins aggregierte Steuer-Reporting der Bank/des Brokers
    # — diese Einzelbelege selbst sind nicht relevant für die Steuererklärung.
    (
        re.compile(
            r"\bCorporate[- ]?Action(?:[- ]?Abrechnung)?\b",
            flags=re.IGNORECASE,
        ),
        "corporate_action_einzeltransaktion",
        "Einzeltransaktion (Dividende/Bezugsrecht) — fließt ins aggregierte "
        "WSV der Bank; Einzelbeleg nicht steuer-relevant",
    ),
]


def classify_out_of_scope(text: str, pdf_name: str = "") -> tuple[str, str] | None:
    """Prüft, ob Beleg-Text ODER Dateiname einem Out-of-Scope-Muster entspricht.

    Manche Out-of-Scope-Marker stehen nur im Dateinamen (z.B. BANK-P-
    Corporate-Action-Belege: der Doc-Text enthält "Dividende"; der Marker
    "Corporate-Action-Abrechnung" steht nur im Filename). Daher beide
    Quellen prüfen.

    **Wichtige Einschränkung:** Jahresgebühr-Pattern wird NUR auf den Dateinamen
    angewendet, NICHT auf den Volltext. Ein Kontoauszug kann eine Jahresgebühr
    als Transaktion enthalten — das macht ihn nicht zu einem OOS-Beleg.

    Returns:
        ``(belegtyp_hinweis, reason)`` bei Match, sonst ``None``.
    """
    # Jahresgebühr/VV-Kosten: Nur Filename (Text-Match führt zu False-Positives
    # bei Kontoauszügen, die eine Jahresgebühr-Buchung enthalten).
    FILENAME_ONLY_PATTERNS = {"vermoegensverwaltungskosten"}

    haystacks_all = [text, pdf_name] if pdf_name else [text]
    haystacks_filename = [pdf_name] if pdf_name else []

    for pattern, belegtyp_hinweis, reason in OUT_OF_SCOPE_PATTERNS:
        haystacks = (
            haystacks_filename
            if belegtyp_hinweis in FILENAME_ONLY_PATTERNS
            else haystacks_all
        )
        for h in haystacks:
            if h and pattern.search(h):
                return belegtyp_hinweis, reason
    return None
