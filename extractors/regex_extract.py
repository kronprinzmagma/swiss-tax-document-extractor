"""Regex-basierte Feldextraktion als zuverlässige Alternative zum LLM.

Basiert auf dem Zielbild-Dokument `ZIELBILD_EXTRAKTION.md` das
die genauen Label-Positionen und Muster aus 31 anonymisierten PDF-Samples ableitet.

Strategie: Für jedes Feld mit klar beschriftetem Label im Dokument einen
Regex definieren. Wenn der Regex einen Treffer erzielt, wird der LLM-Wert
überschrieben. Das LLM bleibt zuständig für semantisch schwierige Felder
(Namen, Jahresangaben) und Fälle ohne eindeutigen Label-Match.

Aufruf:
    overrides = regex_overrides(plain_text, belegtyp)
    # → dict[feld_name, wert_als_str]

Die Feldnamen entsprechen den Raw-Schema-Attributen (snake_case).
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Zahlenmuster — Schweizer Format: Apostroph als Tausendertrennzeichen
# ---------------------------------------------------------------------------

_N = r"[\d'][\d'.]*"   # allgemeines Zahlenmuster (CHF mit Apostroph)
# Wie _N, aber auch "2 593.18" (Leerzeichen als Tausendertrenner in PostFinance TAX_P).
# Strikte Struktur: optional ein Space-Tausenderblock ("2 593") + Dezimalteil.
_N_SPACE = r"\d+(?:[' ]\d{3})*(?:\.\d{1,2})?"

# ---------------------------------------------------------------------------
# bank_zinsausweis
# ---------------------------------------------------------------------------

# TAX_P / Zinsabschluss — Bruttozins [Betrag] (Label VOR Betrag).
# Früher stand im Kommentar "Betrag VOR Label" (PostFinance alt);
# in aktuellen TAX_P-Dokumenten (HBL, PostFinance) steht das Label ZUERST.
BANK_TAX_P_BRUTTOZINS = re.compile(
    rf"Bruttozins\s+({_N})",
    flags=re.IGNORECASE,
)

# PostFinance REP_P — Gutschrift (Bruttoertrag).
# Layout: "Gutschrift Text Total [Anteil-Versicherer] [BETRAG] [Datum] [Saldo]"
# Der Betrag zwischen "Total [0.00]" und dem Datum ist der Gutschrift-Betrag.
BANK_REP_P_GUTSCHRIFT = re.compile(
    rf"Gutschrift\b.*?Total\s+{_N}\s+({_N})\s+\d{{2}}\.\d{{2}}\.\d{{2}}",
    flags=re.IGNORECASE | re.DOTALL,
)

# Raiffeisen Kontoauszug — Saldo zu Ihren Gunsten
BANK_RAIFFEISEN_SALDO = re.compile(
    rf"Saldo zu Ihren Gunsten\s+({_N})",
    flags=re.IGNORECASE,
)

# Raiffeisen Zins- und Saldoverzeichnis — Endsaldo
BANK_RAIFFEISEN_ENDSALDO = re.compile(
    rf"Endsaldo\s+({_N})",
    flags=re.IGNORECASE,
)

# Raiffeisen Zins- und Saldoverzeichnis / Kontoauszug — Zinszahlung (Bruttozins)
BANK_RAIFFEISEN_ZINSZAHLUNG = re.compile(
    rf"Zinszahlung\s+({_N})",
    flags=re.IGNORECASE,
)

# Raiffeisen Kontoauszug — Abschlussbetreffnis (Bruttozins-Buchung)
BANK_RAIFFEISEN_ABSCHLUSS = re.compile(
    rf"Abschlussbetreffnis\s+(?:von\s+\S+\s+bis\s+\S+\s+)?({_N})",
    flags=re.IGNORECASE,
)

# HBL / Neon — Guthabensaldo am [Datum] .XX [Betrag]
# Format: "Guthabensaldo am 31.12.2022 .56 1'234" (Cents und Betrag getrennt)
BANK_HBL_GUTHABENSALDO = re.compile(
    rf"Guthabensaldo am\s+[\d.]+\s+(?:\.\d{{2}}\s+)?({_N})",
    flags=re.IGNORECASE,
)

# HBL — alle Guthabensalden (für Multi-Konto-Dokumente)
BANK_HBL_GUTHABENSALDO_ALL = re.compile(
    rf"Guthabensaldo am\s+[\d.]+\s+(?:\.\d{{2}}\s+)?({_N})",
    flags=re.IGNORECASE,
)

# PostFinance E-Trading / Zinsabrechnung — Neuer Saldo
BANK_POSTFINANCE_NEUER_SALDO = re.compile(
    rf"Neuer Saldo\s+({_N})",
    flags=re.IGNORECASE,
)

# PostFinance TAX_P — Kontostand nach Zinsabschluss
# Format: "[optionaler 1-stelliger Vorblock] [Betrag] [Datum] Kontostand..."
# Beispiel 1: "2 593.18 31.12.2022 Kontostand" → Vorblock "2", Betrag "593.18"
# Beispiel 2: "21 225.51 31.12.2022 Kontostand" → kein Vorblock, Betrag "225.51"
# Regel: Vorblock wird nur als Tausender gewertet wenn er EXAKT 1 Stelle hat.
BANK_POSTFINANCE_KONTOSTAND_NACH = re.compile(
    # Optionaler 1-stelliger Tausender-Vorblock: nur wenn danach exakt 3 Ziffern + Punkt
    # (z.B. "2 593.18" ✓ aber "3 28.97" ✗ und "21 225.51" ✗ wegen Lookbehind).
    rf"(?:(?<!\d)(\d)\s+(?=\d{{3}}[.]))?({_N})\s+\d{{2}}\.\d{{2}}\.\d{{4}}\s+Kontostand nach Zinsabschluss",
    flags=re.IGNORECASE,
)

# PostFinance Zinsabrechnung — Kontostand [Betrag] (Ende Dokument, ohne Suffix)
BANK_POSTFINANCE_KONTOSTAND = re.compile(
    rf"Kontostand\s+({_N})",
    flags=re.IGNORECASE,
)

# Kontoart aus "Kontoart / Währung <Kontoart> / <Währung>".
# Real verifiziert: "Kontoart / Währung Sparkonto Plus / CHF" → "Sparkonto Plus".
# Das non-greedy (.+?) greift bis zum LETZTEN " / <Währung>"-Trenner (\S+ am
# Ende ohne Slash), nicht beim ersten Slash — mehrteilige Kontoarten bleiben
# erhalten.
BANK_KONTOART = re.compile(
    r"Kontoart\s*/\s*W[äa]hrung\s+(.+?)\s+/\s+\S+",
    flags=re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# lohnausweis
# ---------------------------------------------------------------------------

# Nettolohn Ziffer 11: "11. [+=] [Betrag]" oder "11. Nettolohn ... [Betrag]"
LOHN_NETTOLOHN = re.compile(
    rf"11\.\s*(?:Nettolohn[^=\n]*=\s*|(?:\+\s*)?=\s*)({_N})",
    flags=re.IGNORECASE,
)

# Arbeitnehmer-Name: "Herr/Frau [Vorname] [Name]" vor der Adresse (Musterstrasse).
# Im anonymisierten Text z.B. "Herr Hans Hans Musterstrasse" / "Frau Hans Muster Musterstrasse".
LOHN_ARBEITNEHMER = re.compile(
    r"(?:Herr|Frau)\s+([A-ZÀ-Þ][\wÀ-ÿ]+(?:\s+[A-ZÀ-Þ][\wÀ-ÿ]+){0,2}?)\s+(?:Musterstrasse|Maneggplatz|[A-ZÀ-Þ][\wÀ-ÿ]+strasse|[A-ZÀ-Þ][\wÀ-ÿ]+platz)",
)

# ---------------------------------------------------------------------------
# kk_praemienbescheinigung
# ---------------------------------------------------------------------------

# CSS / Helsana — Grundversicherung (KVG) [Betrag]
KK_CSS_KVG = re.compile(
    rf"Grundversicherung\s*\(KVG\)\s+({_N})",
    flags=re.IGNORECASE,
)

# CSS / Sanitas — Zusatzversicherung (VVG) [Betrag]
KK_CSS_VVG = re.compile(
    rf"Zusatzversicherung\s*\(VVG\)\s+({_N})",
    flags=re.IGNORECASE,
)

# Sanitas — VVG (Januar–Dezember) Total [Betrag]
KK_SANITAS_VVG_TOTAL = re.compile(
    rf"VVG\s*\(Januar[–\-]Dezember\)\s+Total\s+({_N})",
    flags=re.IGNORECASE,
)

# CSS / PostFinance — Total von Ihnen bezahlte Kosten (selbstgetragen)
KK_CSS_SELBST_KOSTEN = re.compile(
    rf"(?:Total\s+von\s+Ihnen\s+bezahlte\s+Kosten|bezahlte\s+Kosten)\s+({_N})",
    flags=re.IGNORECASE,
)

# Sanitas / HBL — Nichtversicherte Behandlungskosten Total [Betrag]
KK_SANITAS_NICHTVERSICHERT = re.compile(
    rf"Nichtversicherte Behandlungskosten\s+Total\s+({_N})",
    flags=re.IGNORECASE,
)

# KK-A (Visana-Format) — Total ... nicht getragenen Krankheitskosten [Betrag]
KK_VISANA_NICHT_GETRAGEN = re.compile(
    rf"nicht\s+getragenen\s+Krankheitskosten\s+in\s+CHF\s+({_N})",
    flags=re.IGNORECASE,
)

# --- Selbstgetragene Kosten: Komponenten-Aufschluesselung (260904-rmx) -------
#
# Manche Kassen weisen kein Total der selbstgetragenen Kosten aus, sondern
# brechen sie auf. Beobachtetes Layout:
#
#   Jahresfranchise 300.00  Selbstbehalt 412.60  Spitalbetrag 0.00
#   Nichtversicherte Leistungen 20.15  Nichtpflichtige Leistungen 0.00
#
# Summe 732.75 — und exakt dieser Wert steht in der Spalte "Ihr Anteil".
# Daneben steht in derselben Zeile der Bruttorechnungsbetrag (3'980.25) und
# der Kassenanteil (3'247.50). Wer dort die erste Zahl greift, traegt den
# Bruttobetrag in die Steuererklaerung ein — ein Vielfaches des Zulaessigen.
# Die Schreibweisen unterscheiden sich je Kasse erheblich. Jede Komponente
# wird deshalb ueber mehrere Varianten gesucht; ein optionaler Zusatz wie
# "Jahres-", "Ihr(e)" oder ein nachgestelltes "Total"/"CHF" darf dazwischen
# stehen. Fehlt eine Variante, faellt die Summe zu klein aus — deshalb meldet
# `kk_selbstkosten_aus_komponenten` unvollstaendige Funde ausdruecklich.
_ZWISCHEN = r"(?:\s+(?:Total|CHF|in\s+CHF))?\s+"

KK_KOMPONENTEN: dict[str, "re.Pattern[str]"] = {
    "franchise": re.compile(
        rf"(?:Jahres-?\s*)?Franchise(?:nanteil)?{_ZWISCHEN}({_N})", re.IGNORECASE),
    "selbstbehalt": re.compile(
        rf"Selbstbehalt(?:santeil)?{_ZWISCHEN}({_N})", re.IGNORECASE),
    "spitalbeitrag": re.compile(
        rf"Spital(?:kosten)?(?:beitrag|betrag|anteil){_ZWISCHEN}({_N})", re.IGNORECASE),
    "nichtversicherte_leistungen": re.compile(
        rf"[Nn]icht[\s-]?versicherte\s+(?:Leistungen|Behandlungskosten|Kosten)"
        rf"{_ZWISCHEN}({_N})", re.IGNORECASE),
    "nichtpflichtige_leistungen": re.compile(
        rf"[Nn]icht[\s-]?pflichtige\s+(?:Leistungen|Kosten){_ZWISCHEN}({_N})",
        re.IGNORECASE),
    "kostenbeteiligung": re.compile(
        rf"(?:Ihre\s+)?Kostenbeteiligung{_ZWISCHEN}({_N})", re.IGNORECASE),
}

# "Kostenbeteiligung" ist bei manchen Kassen bereits die Summe aus Franchise
# und Selbstbehalt. Dann darf sie nicht zusaetzlich addiert werden.
KK_KOMPONENTEN_UEBERLAPPEND: frozenset[str] = frozenset({"kostenbeteiligung"})

# "Ihr Anteil" als letzte Zahl der Total-Zeile:
#   "Rechnungsbetrag  KK-Anteil  Ihr Anteil  Total CHF 3'980.25 3'247.50 732.75"
KK_IHR_ANTEIL_TOTAL = re.compile(
    rf"Ihr\s+Anteil\b.*?Total\s+CHF\s+{_N}\s+{_N}\s+({_N})",
    re.IGNORECASE | re.DOTALL,
)


def kk_selbstkosten_aus_komponenten(plain_text: str) -> tuple[str | None, dict]:
    """Summiert die aufgeschluesselten Selbstkosten-Komponenten.

    Returns:
        ``(summe_als_str_oder_None, befund_dict)``. ``befund`` enthaelt die
        gefundenen Einzelwerte und — falls im Dokument eine "Ihr Anteil"-Spalte
        steht — ob die Summe dazu passt. Stimmen beide ueberein, ist der Wert
        rechnerisch belegt; weichen sie ab, gehoert der Beleg in die manuelle
        Pruefung statt in eine gruene Zeile.
    """
    from extractors.numbers import parse_swiss_amount

    teile: dict[str, float] = {}
    for name, muster in KK_KOMPONENTEN.items():
        v = _first(muster, plain_text)
        if not v:
            continue
        try:
            betrag = parse_swiss_amount(v)
        except ValueError:
            continue
        if betrag is not None:
            teile[name] = float(betrag)

    if not teile:
        return None, {}

    # "Kostenbeteiligung" ist bei manchen Kassen bereits Franchise +
    # Selbstbehalt. Dann nur die Einzelteile zaehlen, sonst doppelt.
    summanden = dict(teile)
    if "kostenbeteiligung" in summanden and (
        "franchise" in summanden or "selbstbehalt" in summanden
    ):
        summanden.pop("kostenbeteiligung")

    summe = round(sum(summanden.values()), 2)
    befund: dict = {
        "komponenten": teile,
        "summiert": sorted(summanden),
        "summe": summe,
        "belegt": False,
    }

    ausgewiesen = _first(KK_IHR_ANTEIL_TOTAL, plain_text)
    if ausgewiesen:
        try:
            ist = parse_swiss_amount(ausgewiesen)
        except ValueError:
            ist = None
        if ist is not None:
            befund["ihr_anteil_ausgewiesen"] = float(ist)
            befund["belegt"] = abs(float(ist) - summe) < 0.01
            # Der ausgewiesene Wert hat Vorrang — er ist der Beleg selbst.
            return f"{float(ist):.2f}", befund

    # Ohne ausgewiesenes Total ist die Summe nur so vollstaendig wie die
    # erkannten Labels. Findet sich nur eine einzige Komponente, ist der Wert
    # mit hoher Wahrscheinlichkeit zu klein — z.B. nur der Selbstbehalt, ohne
    # Franchise. Still zu wenig zu melden waere der schlimmste Ausgang, also
    # wird es hier ausdruecklich als unbelegt markiert.
    befund["belegt"] = len(summanden) >= 2
    return f"{summe:.2f}", befund


# CSS kombiniert — Prämie Total CHF [Betrag] (wenn keine KVG/VVG-Trennung)
KK_PRAEMIE_TOTAL = re.compile(
    rf"Pr[äa]mie\s+(?:®\s+)?Total\s+CHF\s+({_N})",
    flags=re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# krankheitskosten
# ---------------------------------------------------------------------------

# Helsana — Total selbstgetragene Krankheits- und Unfallkosten [Betrag]
KK_HELSANA_EIGENANTEIL = re.compile(
    rf"Total selbstgetragene Krankheits-\s*und Unfallkosten\s+({_N})",
    flags=re.IGNORECASE,
)

# Visana — Total von Visana nicht getragenen Krankheitskosten in CHF [Betrag]
KK_VISANA_TOTAL = re.compile(
    rf"Total von Visana nicht getragenen Krankheitskosten in CHF\s+({_N})",
    flags=re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# saeule_3a
# ---------------------------------------------------------------------------

# Form. 21 EDP — r Total Beiträge an die Säule 3a [Betrag]
# Das "r" ist die Feldbezeichnung im ESTV-Formular.
# Strategie: Abschnitt zwischen "r Total Beiträge" und "Bitte obige" finden,
# darin das Betrag-Duplikat suchen (Form erscheint als Original + Kopie).
_SAEULE3A_SECTION_START = re.compile(
    r"r\s+Total Beiträge an die Säule 3a",
    flags=re.IGNORECASE,
)
_SAEULE3A_SECTION_END = re.compile(
    r"Bitte obige Beiträge|Bitte.*Steuererklärung|Ort und Datum",
    flags=re.IGNORECASE,
)
# Duplikat-Muster: selbe Zahl zweimal hintereinander (Original + Kopie)
_SAEULE3A_DUPLIKAT = re.compile(
    rf"({_N})\s+\1\b",
    flags=re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# kinderbetreuung
# ---------------------------------------------------------------------------

# Fugu / Krippe — einen Elternbeitrag von CHF [Betrag]
KITA_ELTERNBEITRAG = re.compile(
    rf"Elternbeitrag von CHF\s+({_N})",
    flags=re.IGNORECASE,
)

# Kita-Brief — Total [Betrag] (am Ende der Rechnungstabelle)
# Nur für Belegtyp kinderbetreuung — Whitespace-Grenze genügt.
KITA_TOTAL = re.compile(
    rf"(?<!\w)Total\s+({_N})(?!\w)",
    flags=re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# wertschriftenverzeichnis
# ---------------------------------------------------------------------------

# Selma Finance / Saxo — Total Steuerwert [Betrag]
WERTSCHRIFTEN_SELMA_STEUERWERT = re.compile(
    rf"Total Steuerwert\s+({_N})",
    flags=re.IGNORECASE,
)

# True Wealth — [Betrag](1) (1) Davon A ... (Fussnoten-Format)
# True Wealth verwendet Komma als Tausendertrennzeichen (z.B. "7,793")
_N_WITH_COMMA = r"[\d,'][\d'.,]*"
WERTSCHRIFTEN_TRUEWEALTH_STEUERWERT = re.compile(
    rf"({_N_WITH_COMMA})\(1\)\s*\(1\)\s*Davon A",
    flags=re.IGNORECASE,
)

# Selma Finance / True Wealth — Total Ertrag [Betrag]
WERTSCHRIFTEN_TOTAL_ERTRAG = re.compile(
    rf"Total Ertrag\s+({_N})",
    flags=re.IGNORECASE,
)

# PostFinance Portfolio — GESAMTVERMÖGEN IN CHF [Betrag]
WERTSCHRIFTEN_POSTFINANCE_GESAMTVERMOEGEN = re.compile(
    rf"GESAMTVERM(?:Ö|Oe|OE|oe|ö)GEN\s+IN\s+CHF\s+({_N})",
    flags=re.IGNORECASE,
)

# Selma / Saxo — Verrechnungssteuer total (Ertrag Rubrik A × 35%)
# Nicht direkt aus dem Dokument ableitbar — LLM bleibt zuständig.

# ---------------------------------------------------------------------------
# Haupt-Dispatcher
# ---------------------------------------------------------------------------


def _first(pattern: re.Pattern[str], text: str) -> str | None:
    """Gibt erstes Match von Gruppe 1 zurück, bereinigt um Leerzeichen."""
    m = pattern.search(text)
    return m.group(1).strip() if m else None


def _last(pattern: re.Pattern[str], text: str) -> str | None:
    """Gibt letztes Match von Gruppe 1 zurück (z.B. letzter Kontostand)."""
    matches = list(pattern.finditer(text))
    return matches[-1].group(1).strip() if matches else None


def regex_overrides(plain_text: str, belegtyp: str) -> dict[str, str]:
    """Extrahiert Felder via Regex und gibt Overrides für LLM-Felder zurück.

    Args:
        plain_text: Bereinigter Dokumenttext (ohne [T<id>]-Tags).
        belegtyp: Klassifizierter Belegtyp (z.B. "bank_zinsausweis").

    Returns:
        Dict mit Feldname → Wert für alle via Regex gefundenen Felder.
        Leeres Dict wenn keine Treffer. Werte sind rohe Strings (kein Parsing).
    """
    overrides: dict[str, str] = {}

    if belegtyp == "lohnausweis":
        # Nettolohn Ziffer 11 (primärer Steuer-Zielwert)
        v = _first(LOHN_NETTOLOHN, plain_text)
        if v:
            overrides["nettolohn_pos11"] = v
        # Arbeitnehmer-Name (Person)
        v = _first(LOHN_ARBEITNEHMER, plain_text)
        if v:
            overrides["arbeitnehmer_name"] = v.strip()

    elif belegtyp == "bank_zinsausweis":
        # --- Bruttozins ---
        # Prio 1: "Bruttozins [Betrag]" (TAX_P HBL/PostFinance neu, label-first)
        v = _first(BANK_TAX_P_BRUTTOZINS, plain_text)
        if v:
            overrides["bruttoertrag"] = v

        # Prio 2: REP_P Gutschrift (Nettoertrag aus Transaktionszeile)
        if "bruttoertrag" not in overrides:
            v = _first(BANK_REP_P_GUTSCHRIFT, plain_text)
            if v:
                overrides["bruttoertrag"] = v

        # Prio 3: Raiffeisen "Zinszahlung"
        if "bruttoertrag" not in overrides:
            v = _first(BANK_RAIFFEISEN_ZINSZAHLUNG, plain_text)
            if v:
                overrides["bruttoertrag"] = v

        # Prio 4: Raiffeisen "Abschlussbetreffnis"
        if "bruttoertrag" not in overrides:
            v = _first(BANK_RAIFFEISEN_ABSCHLUSS, plain_text)
            if v:
                overrides["bruttoertrag"] = v

        # Guard: vermeide bestand==ertrag-Dupe.
        # Wenn Bruttozins 0.00 oder kein Label sichtbar → kein bruttoertrag setzen.
        # (Downstream-Logik in process_samples_full prüft den Wert unabhängig.)

        # --- Vermögensstand ---
        # 1) Raiffeisen: "Saldo zu Ihren Gunsten"
        v = _first(BANK_RAIFFEISEN_SALDO, plain_text)
        if v:
            overrides["vermoegensstand_3112"] = v
        # 2) Raiffeisen Saldoverzeichnis: "Endsaldo" (letztes)
        elif (v := _last(BANK_RAIFFEISEN_ENDSALDO, plain_text)):
            overrides["vermoegensstand_3112"] = v
        # 3) PostFinance E-Trading: "Neuer Saldo"
        elif (v := _first(BANK_POSTFINANCE_NEUER_SALDO, plain_text)):
            overrides["vermoegensstand_3112"] = v
        # 4) PostFinance TAX_P: Kontostand nach Zinsabschluss
        # Zwei-Gruppen-Pattern: Gruppe 1 = optionaler 1-stelliger Tausender-Vorblock
        # Gruppe 2 = Hauptbetrag. Kombiniere zu "N'NNN.NN" wenn Gruppe 1 vorhanden.
        elif list(BANK_POSTFINANCE_KONTOSTAND_NACH.finditer(plain_text)):
            _matches = list(BANK_POSTFINANCE_KONTOSTAND_NACH.finditer(plain_text))
            _m = _matches[-1]  # letztes Vorkommen
            _prefix = _m.group(1)  # z.B. "2" oder None
            _main = _m.group(2).strip()  # z.B. "593.18"
            overrides["vermoegensstand_3112"] = f"{_prefix}'{_main}" if _prefix else _main
        # 5) HBL/Neon: Guthabensaldo.
        # Format im Text: "Guthabensaldo am DD.MM.YYYY .CC N'NNN"
        # Cents-Präfix (".CC") steht vor dem Betrag ("N'NNN") — zusammenführen.
        elif list(BANK_HBL_GUTHABENSALDO_ALL.finditer(plain_text)):
            _m_all = list(BANK_HBL_GUTHABENSALDO_ALL.finditer(plain_text))
            _m0 = _m_all[0]
            _raw_val = _m0.group(1)
            # Cents-Präfix: ".NN" unmittelbar vor dem erfassten Betrag
            _ctx = plain_text[max(0, _m0.start(1) - 8):_m0.start(1)]
            _cents = re.search(r"\.(\d{2})\s*$", _ctx)
            if _cents and "." not in _raw_val:
                overrides["vermoegensstand_3112"] = f"{_raw_val}.{_cents.group(1)}"
            else:
                overrides["vermoegensstand_3112"] = _raw_val
            # Alle Guthabensaldo-Werte für Multi-Konto-Dokumente
            if len(_m_all) > 1:
                all_vals = []
                for _mi in _m_all:
                    _rv = _mi.group(1)
                    _c2 = plain_text[max(0, _mi.start(1) - 8):_mi.start(1)]
                    _cc = re.search(r"\.(\d{2})\s*$", _c2)
                    all_vals.append(
                        f"{_rv}.{_cc.group(1)}" if _cc and "." not in _rv else _rv
                    )
                overrides["vermoegensstand_3112_all"] = ";".join(all_vals)
        # 6) PostFinance Zinsabrechnung / REP_P: letzter "Kontostand [Betrag]"
        elif (v := _last(BANK_POSTFINANCE_KONTOSTAND, plain_text)):
            if not re.match(r"^\d+\.\d{1,2}\.\d{2,4}$", v):
                overrides["vermoegensstand_3112"] = v

        # --- Kontoart ---
        # "Kontoart / Währung <Kontoart> / <Währung>" → kontotyp.
        # Kein Label → kein Eintrag (Override greift nur bei Treffer).
        v = _first(BANK_KONTOART, plain_text)
        if v:
            overrides["kontotyp"] = v.strip()

    elif belegtyp == "kk_praemienbescheinigung":
        # KVG-Prämie: CSS/Helsana "Grundversicherung (KVG)"
        v = _first(KK_CSS_KVG, plain_text)
        if v:
            overrides["praemie_kvg_total"] = v

        # VVG-Prämie: CSS "Zusatzversicherung (VVG)" oder Sanitas-Format
        v = _first(KK_CSS_VVG, plain_text) or _first(KK_SANITAS_VVG_TOTAL, plain_text)
        if v:
            overrides["praemie_vvg_total"] = v

        # Prämie Total — nur wenn weder KVG noch VVG sauber getrennt sichtbar.
        if "praemie_kvg_total" not in overrides and "praemie_vvg_total" not in overrides:
            v = _first(KK_PRAEMIE_TOTAL, plain_text)
            if v:
                overrides["praemie_total"] = v

        # Selbstgetragene Kosten (CSS "Total von Ihnen bezahlte Kosten")
        v = _first(KK_CSS_SELBST_KOSTEN, plain_text)
        if v:
            overrides["selbstgetragene_kosten"] = v
        # Sanitas "Nichtversicherte Behandlungskosten Total"
        if "selbstgetragene_kosten" not in overrides:
            v = _first(KK_SANITAS_NICHTVERSICHERT, plain_text)
            if v:
                overrides["selbstgetragene_kosten"] = v
        # Visana "nicht getragenen Krankheitskosten"
        if "selbstgetragene_kosten" not in overrides:
            v = _first(KK_VISANA_NICHT_GETRAGEN, plain_text)
            if v:
                overrides["selbstgetragene_kosten"] = v
        # Kein ausgewiesenes Total: aus den Komponenten rekonstruieren
        # (Franchise + Selbstbehalt + Spitalbetrag + nicht versicherte /
        # nicht pflichtige Leistungen). Wichtig, weil sonst das Sprachmodell
        # in derselben Zeile den Bruttorechnungsbetrag greift.
        if "selbstgetragene_kosten" not in overrides:
            v, _befund = kk_selbstkosten_aus_komponenten(plain_text)
            if v:
                overrides["selbstgetragene_kosten"] = v
                if not _befund.get("belegt"):
                    # Nur eine Komponente gefunden und kein Total zum
                    # Gegenrechnen: der Wert ist vermutlich unvollstaendig.
                    # Sichtbar machen statt stillschweigend zu wenig melden.
                    overrides["_selbstkosten_unvollstaendig"] = ",".join(
                        _befund.get("summiert", [])
                    ) or "keine"

    elif belegtyp == "krankheitskosten":
        # Prämie KVG (kombinierte Belege: Prämie + Kosten in einem Dokument)
        v = _first(KK_CSS_KVG, plain_text)
        if v:
            overrides["praemie_kvg_total"] = v

        # Prämie Total — NUR wenn im Dokument keine KVG/VVG-Trennung sichtbar ist.
        # WICHTIG: nicht als KVG ausweisen (eigenes Feld praemie_total), damit die
        # Beschreibung "Prämie Total" lautet und nicht fälschlich "Prämie KVG".
        if "praemie_kvg_total" not in overrides:
            v = _first(KK_PRAEMIE_TOTAL, plain_text)
            if v:
                overrides["praemie_total"] = v

        # Selbstgetragene Kosten: Helsana Jahresübersicht
        v = _first(KK_HELSANA_EIGENANTEIL, plain_text)
        if v:
            overrides["betrag"] = v
        # Visana Jahresübersicht
        elif (v := _first(KK_VISANA_TOTAL, plain_text)):
            overrides["betrag"] = v

    elif belegtyp == "saeule_3a":
        # Form. 21 EDP dfi — "r Total Beiträge an die Säule 3a [Betrag]"
        # 1) Abschnitt isolieren
        start_m = _SAEULE3A_SECTION_START.search(plain_text)
        if start_m:
            section_text = plain_text[start_m.end():]
            end_m = _SAEULE3A_SECTION_END.search(section_text)
            if end_m:
                section_text = section_text[: end_m.start()]
            # 2) Duplikat-Muster (Original + Kopie → selbe Zahl zweimal)
            dup = _SAEULE3A_DUPLIKAT.search(section_text)
            if dup:
                overrides["einzahlung_betrag"] = dup.group(1)
            else:
                # Fallback: letzte sauber formatierte Zahl (mit Apostroph oder ≥4 Stellen)
                candidates = re.findall(r"[\d']{4,}", section_text)
                if candidates:
                    overrides["einzahlung_betrag"] = candidates[-1]

    elif belegtyp == "kinderbetreuung":
        # Krippe: "Elternbeitrag von CHF [Betrag]"
        v = _first(KITA_ELTERNBEITRAG, plain_text)
        if v:
            overrides["betrag"] = v
        # Kita-Brief: "Total [Betrag]" am Zeilenende
        elif (v := _first(KITA_TOTAL, plain_text)):
            overrides["betrag"] = v

    elif belegtyp == "wertschriftenverzeichnis":
        # Depot-Bestand
        v = (
            _first(WERTSCHRIFTEN_SELMA_STEUERWERT, plain_text)
            or _first(WERTSCHRIFTEN_TRUEWEALTH_STEUERWERT, plain_text)
            or _first(WERTSCHRIFTEN_POSTFINANCE_GESAMTVERMOEGEN, plain_text)
        )
        if v:
            overrides["bestand_3112"] = v

        # Bruttoertrag: "Total Ertrag" nach "Subtotal Ertrag" (True Wealth / Selma)
        # Dokument-Struktur: ... Subtotal Ertrag X → Total Ertrag GESAMT → Total Ertrag 0 (VSt)
        # Strategie: erstes "Total Ertrag" NACH "Subtotal Ertrag" wählen; falls kein Subtotal,
        # erstes nicht-null Vorkommen im gesamten Text.
        subtotal_m = re.search(r"Subtotal Ertrag", plain_text, re.IGNORECASE)
        if subtotal_m:
            v = _first(WERTSCHRIFTEN_TOTAL_ERTRAG, plain_text[subtotal_m.end():])
        else:
            v = None
        if not v:
            # Fallback: erstes nicht-null Vorkommen im gesamten Text
            for _m in WERTSCHRIFTEN_TOTAL_ERTRAG.finditer(plain_text):
                _candidate = _m.group(1)
                _num_str = _candidate.replace("'", "").replace(",", "").replace(".", "")
                try:
                    if int(_num_str) > 0:
                        v = _candidate
                        break
                except ValueError:
                    pass
        if v:
            overrides["bruttoertrag_total"] = v

    return overrides
