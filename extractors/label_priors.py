"""Belegtyp- und feldspezifische Label-Priors für den Anker-Resolver.

Wenn der LLM keine ``tag_refs`` liefert und im Dokument mehrere identische
Token den extrahierten Wert tragen, wählt ``_verbatim_fallback`` ohne Hint
das erste Vorkommen — das ist bei Bank-Kontoauszügen oft die Saldovortrag-
oder Running-Balance-Spalte, nicht die Endsaldo-Zeile.

Mit einem geordneten Prior-Vektor entscheidet der Resolver räumlich: das
erste Label aus der Liste, das einen räumlich nahen Value-Hit hat, gewinnt.
Spezifischere Labels stehen oben — ``"Kontostand nach Zinsabschluss"`` vor
``"Kontostand"``, damit das spezifische Vorkommen das generische schlägt.

Konvention: Substring-Matching, case-insensitive, Multi-Token bis 4 Wörter.
Symmetrisch zur Label-Suche in ``scripts/eval_anchors.py``.
"""
from __future__ import annotations

# (belegtyp, feldname) → priorisierte Label-Liste
# Erstes Match wird bevorzugt; spezifische Labels zuerst.
FIELD_LABEL_PRIORS: dict[tuple[str, str], list[str]] = {
    # Bank-Zinsausweis: vermoegensstand erscheint oft mehrfach (Saldovortrag,
    # running balance, Endsaldo). Der Endsaldo-Anker steht neben einem dieser
    # institutsspezifischen Labels.
    ("bank_zinsausweis", "vermoegensstand_3112"): [
        "Saldo zu Ihren Gunsten",        # Raiffeisen
        "Kontostand nach Zinsabschluss", # PostFinance TAX_P
        "Endsaldo",
        "Guthabensaldo",                  # Hypothekarbank Lenzburg
        "Kontostand",                     # PostFinance Zinsabrechnung (generisch, last resort)
        # R4 (260612-m8t): Mehrkonten-Zusammenfassung. NACH den spezifischen
        # Einzelkonto-Labels — greift nur, wenn Endsaldo/Kontostand nicht passt
        # (z.B. die Total-Zeile eines Sammelbelegs mit mehreren Konten).
        "Guthaben Steuerwert",
        "Total",
    ],
    # Bruttoertrag erscheint in TAX_P-Belegen mehrfach (Spaltenheader + Datenzeile).
    ("bank_zinsausweis", "bruttoertrag"): [
        "Bruttozins",   # PostFinance TAX_P
        "Zinszahlung",  # Raiffeisen
        "Habenzins",    # PostFinance Zinsabrechnung
    ],
}


def get_priors(belegtyp: str | None, feldname: str) -> list[str] | None:
    """Lookup; gibt ``None`` zurück, wenn kein Prior hinterlegt ist."""
    if belegtyp is None:
        return None
    return FIELD_LABEL_PRIORS.get((belegtyp, feldname))
