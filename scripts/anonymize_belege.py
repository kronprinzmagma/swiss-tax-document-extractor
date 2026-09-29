"""Anonymisiert echte Steuer-Belege fuer Test-Iteration.

Liest PDFs aus ~/Dokumente/Steuern/belege/, ersetzt personenbezogene Daten
mit Fantasie-Werten und schreibt das Ergebnis als JSON (mit Wort-Struktur
und Bounding-Boxes) sowie .txt (Klartext) nach evals/samples_real/.

Die JSON-Datei behaelt die PDF-Struktur (Seiten, Wort-Positionen) und wird
anschliessend von `scripts/process_samples_full.py` durch die Extraktions-
Pipeline gejagt.

Anonymisiert:
- Personennamen (via lokalem Ollama) -> "Hans Muster" / "Maria Muster" / ...
- CHF-Betraege (Regex) -> Fantasie-Betraege, konsistent pro Dokument
- IBAN, AHV-Nr., Telefon, Email (Regex) -> Platzhalter
- Strassenadressen (Regex-Heuristik) -> "Musterstrasse 1"

Behaelt:
- Firmen-/Institutionsnamen (BANK-Z, KK-C, KK-H, ...) -- noetig zur Erkennung
- Labels ("Bruttoertrag", "Vermoegensstand per 31.12.")
- Steuerjahr (2024, 2023, ...) -- noetig fuer Plausibility-Pruefung
- Original-Bounding-Boxes (jedes anonymisierte Wort bleibt an seiner Position)

Usage:
    python scripts/anonymize_belege.py
    python scripts/anonymize_belege.py --pdf path/to/specific.pdf
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import unicodedata
from pathlib import Path

# Repo-Wurzel ins PYTHONPATH einreihen.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pdfplumber

from extractors.family import load_family
from extractors.pii_patterns import (
    ACCOUNT_NR_DASH_RE,
    ACCOUNT_NR_DOT_RE,
    AHV_GLUED_RE,
    AHV_REAL_RE,
    DOMAIN_RE,
    EMAIL_RE,
    IBAN_REAL_RE,
    LEISTUNGS_LABEL_WHITELIST,
    LEISTUNGSERBRINGER_PERSON_RE,
    PHONE_OCR_RE,
    PHONE_RE,
    PLZ_ORT_GLUED_RE,
    THIRD_PARTY_NAME_RE,
    flatten_secret_institutions,
    generate_birthdate_variants,
    generate_name_variant_pairs,
    is_domain_whitelisted,
    is_plz_token_year_not_plz,
    load_privacy_secrets,
)

# Single Source of Truth (Finding 5): die PII-Detection-Regexes leben in
# extractors.pii_patterns. Der Anonymizer nutzt die STRENGEREN Varianten von
# dort. Lokale Aliase mit den historischen Namen, damit die bestehenden
# Scrub-Aufrufe unverändert bleiben.
IBAN_RE = IBAN_REAL_RE
AHV_RE = AHV_REAL_RE
# AHV_GLUED_RE, ACCOUNT_NR_DASH_RE, ACCOUNT_NR_DOT_RE, EMAIL_RE, PHONE_RE
# werden direkt unter ihren importierten Namen verwendet.

# --------------------------------------------------------------------------- #
# Fantasie-Pool                                                                #
# --------------------------------------------------------------------------- #

FANTASY_PERSONS_FULL = [
    "Hans Muster",
    "Maria Muster",
    "Lina Muster",
    "Tim Muster",
    "Anna Muster",
    "Peter Muster",
]
FANTASY_PERSONS_GENERIC = [f"Person{i} Muster" for i in range(1, 20)]

# Bekannte Schweizer Aussteller pro Belegtyp — werden mit BELEGTYP-X-Marker
# anonymisiert. Belegtyp bleibt erkennbar, spezifischer Aussteller nicht.
FIRM_NAMES_BY_TYPE: dict[str, list[str]] = {
    "KK": [
        "KK-H", "KK-S", "KK-C", "KK-V", "KPT", "KK-W", "Atupri",
        "Concordia", "Groupe Mutuel", "Mutuel", "Sympany", "ÖKK", "OEKK",
        "Assura", "Aquilana", "Vivao", "Compact", "EGK", "Innova",
        "Sanagate", "Agrisano",
    ],
    "BANK": [
        "BANK-P", "BANK-U", "BANK-Z", "BANK-Z",
        "Credit Suisse", "BANK-R", "BANK-H", "BANK-H",
        "BANK-M", "Bank Cler", "Cler", "BCV",
        "Banque Cantonale Vaudoise", "Berner Kantonalbank", "BCBE", "BLKB",
        "ZGKB", "AKB", "SGKB", "TKB", "GLKB", "OKB", "URKB",
    ],
    "BROKER": [
        "BROKER-X", "BROKER-X", "Swissquote", "BROKER-T", "BROKER-S",
        "BROKER-S", "Inyova", "Yuh", "Findependent",
    ],
    "STIFTUNG_3A": [
        "STIFTUNG-V", "STIFTUNG-F", "finpension", "Finpension",
        "STIFTUNG-Z", "STIFTUNG-Z", "Liberty", "Tellco",
        "Sparen 3", "myThird",
    ],
}

# Geburtsdaten-Pattern — Privacy-kritisch, MUSS vor allgemeiner Date-Protection
# anonymisiert werden. Erkennt "geboren am 01.01.1900", "geb. 1.1.00",
# "Geburtsdatum: 01.01.1900", auch CamelCase-zusammengeklebte Varianten.
BIRTHDATE_PHRASE_RE = re.compile(
    r"(geboren\s*am|geb\.?|Geburtsdatum:?)\s*(\d{1,2}\.\d{1,2}\.\d{2,4})",
    re.IGNORECASE,
)
# CamelCase-Variante: "geborenam01.01.1900" als ein Token
BIRTHDATE_GLUED_RE = re.compile(
    r"(geboren\s*am|geb\.?|Geburtsdatum:?)(\d{1,2}\.\d{1,2}\.\d{2,4})",
    re.IGNORECASE,
)

# --------------------------------------------------------------------------- #
# Regex-Muster                                                                 #
# --------------------------------------------------------------------------- #

SWISS_AMOUNT_RE = re.compile(
    r"^(?:\d{1,3}(?:[’'\. ]\d{3})+(?:[.,]\d{1,2})?"
    r"|\d{4,}(?:[.,]\d{1,2})?"
    r"|\d{1,4}[.,]\d{2})$"
)
SWISS_AMOUNT_IN_RE = re.compile(
    r"\b(?:\d{1,3}(?:[’'\. ]\d{3})+(?:[.,]\d{1,2})?"
    r"|\d{4,}(?:[.,]\d{1,2})?"
    r"|\d{1,4}[.,]\d{2})\b"
)

# Datum (CH-Format DD.MM.YYYY oder ISO YYYY-MM-DD) -- NIE anonymisieren.
# Behaelt Steuerjahr-Bezug bei und macht das Dokument fuer Code-Review lesbar.
DATE_TOKEN_RE = re.compile(
    r"^(?:\d{1,2}\.\d{1,2}\.\d{2,4}|\d{4}-\d{2}-\d{2})\.?$"
)

# Inline-Datum in einem groesseren Token (z.B. "Stichtag:31.12.2022")
DATE_INLINE_RE = re.compile(r"\d{1,2}\.\d{1,2}\.\d{2,4}|\d{4}-\d{2}-\d{2}")

# IBAN_RE / AHV_RE / AHV_GLUED_RE / ACCOUNT_NR_DASH_RE / ACCOUNT_NR_DOT_RE /
# PHONE_RE / EMAIL_RE stammen jetzt aus extractors.pii_patterns (Single Source
# of Truth, strengere Varianten — siehe Import oben). Hier nur noch
# Anonymizer-spezifische Helfer.

# Rechtsform-Regex: erkennt Firmen-Vollnamen anhand der Rechtsform-Endung.
# Match: bis zu 5 grossgeschriebene Wörter + Rechtsform-Token.
RECHTSFORM_RE = re.compile(
    r"\b([A-ZÀ-Þ][\wÀ-ÿ&\-.]{1,40}(?:\s+[A-ZÀ-Þ][\wÀ-ÿ&\-.]{1,40}){0,4})"
    r"\s+(AG|GmbH|Genossenschaft|Stiftung|Vorsorgestiftung|"
    r"Versicherung(?:en)?|Krankenkasse|Kinderkrippe|Kita|"
    r"Universit[äa]t|Hochschule|Spital|Klinik|Apotheke|"
    r"Bank|BANK-R|Sparkasse|Post|Finance|Broker|Management\s+AG)\b"
)

# Cross-Token Phone: pdfplumber splittet "+41 44 123 45 67" in 5 Tokens.
# Wird im Post-Pass ueber die volle Wort-Liste pro Seite gesucht.
PHONE_CROSS_RE = re.compile(
    r"(?:\+41|0041|0)[\s\.\-]?\d{2}[\s\.\-]?\d{3}[\s\.\-]?\d{2}[\s\.\-]?\d{2}"
)
# Glued-Phone-Token (ohne jegliche Trenner, z.B. "0440000000"): die strenge
# pii_patterns.PHONE_RE verlangt Trennzeichen und matcht diese Form bewusst
# nicht (FP-Vermeidung im Gate). Für die Token-Voll-Anonymisierung braucht der
# Scrubber sie trotzdem — anonymizer-spezifischer Helfer.
PHONE_GLUED_TOKEN_RE = re.compile(r"^(?:\+41|0041|0)\d{9}$")
STREET_RE = re.compile(
    # Strassenname + Suffix, optional mit direkt anhängender Hausnummer
    # (kein Pflicht-Leerzeichen): "Beispielstrasse34", "Teststr. 87",
    # "Mustergasse5" (Finding P1b). Suffix endet entweder am Wortende oder
    # geht direkt in 1-4 Ziffern über. `str\.?` erlaubt "Teststr" und "Teststr.".
    r"\b[A-ZÀ-ſ][A-Za-zÀ-ſ]{2,}"
    r"(?:strasse|str\.?|gasse|weg|platz|allee|ring|hof)"
    r"(?:\s?\d{1,4})?",
    re.IGNORECASE,
)
PLZ_RE = re.compile(r"\b\d{4}\b")  # Schweizer PLZ

# Datum (behalten -- Jahr ist wichtig)
DATE_RE = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b")

# Reine Jahr-Tokens (z.B. "2022", "2023") -- NIE anonymisieren.
# Vorher matched SWISS_AMOUNT_RE auf 4-stellige Zahlen und zerstörte
# Steuerjahr-Information im Body (z.B. "Steuerjahr 2022" → "Steuerjahr 2055").
YEAR_ONLY_RE = re.compile(r"^20\d{2}$")

# Kompakte Datums-Tokens YYYYMMDD (z.B. "20221231") -- NIE anonymisieren.
# Werden von SWISS_AMOUNT_RE als 8-stellige Zahl gefasst.
COMPACT_DATE_RE = re.compile(r"^20\d{6}$")

# --------------------------------------------------------------------------- #
# Personenerkennung via Ollama                                                 #
# --------------------------------------------------------------------------- #

LLM_PERSON_PROMPT = """Identifiziere alle natuerlichen Personennamen (Vorname + Nachname) im folgenden Schweizer Steuerbeleg.

WICHTIG:
- KEINE Firmennamen / Institutionen ("BANK-Z", "KK-H", "BANK-P", "KK-C", "KK-S", "Muster-Universitaet").
- KEINE Funktionsbezeichnungen ("Kontoinhaber", "Versicherte Person", "Spender").
- NUR echte Personennamen aus dem Text.
- Falls keine Person gefunden: leere Liste.

Gib eine JSON-Liste von Strings zurueck, in Reihenfolge des Auftretens (Duplikate ok).

Text:
{text}
"""


def detect_persons_via_llm(text: str, model: str = "qwen2.5:7b-instruct-q4_K_M") -> list[str]:
    """Identifiziert Personennamen via Ollama. Fallback: leere Liste."""
    try:
        import ollama
    except ImportError:
        print("  WARN: ollama nicht installiert -- ueberspringe Personen-Erkennung", file=sys.stderr)
        return []

    snippet = text[:4000]
    prompt = LLM_PERSON_PROMPT.format(text=snippet)
    schema = {"type": "array", "items": {"type": "string"}}
    try:
        response = ollama.chat(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            format=schema,
            options={"temperature": 0, "num_ctx": 8192, "num_predict": 512},
        )
        msg = response["message"] if isinstance(response, dict) else response.message
        content = msg["content"] if isinstance(msg, dict) else msg.content
        names = json.loads(content)
        if not isinstance(names, list):
            return []
        seen: set[str] = set()
        out: list[str] = []
        for n in names:
            if not isinstance(n, str):
                continue
            n = n.strip()
            if n and n not in seen:
                seen.add(n)
                out.append(n)
        return out
    except Exception as e:
        print(f"  WARN: LLM-Personen-Erkennung gescheitert: {e}", file=sys.stderr)
        return []


# --------------------------------------------------------------------------- #
# Personen-Mapping (token-level)                                               #
# --------------------------------------------------------------------------- #


LLM_INSTITUTION_PROMPT = """Identifiziere alle Firmen-/Institutionsnamen im folgenden Schweizer Steuerbeleg.

WICHTIG:
- Firmen, Kassen, Banken, Broker, Stiftungen, Arbeitgeber, Schulen, Kitas, Aerzte, Spitaeler, Behoerden, Apotheken
- KEINE natuerlichen Personen (Vor-/Nachnamen)
- KEINE allgemeinen Begriffe ("Konto", "Bescheinigung", "Total", "Versicherte Person")
- KEINE Steuer-/Belegtyp-Bezeichnungen ("Lohnausweis", "Zinsausweis", "Praemienbescheinigung")

Gib eine JSON-Liste der Original-Firmennamen zurueck.

Text:
{text}
"""


def detect_institutions_via_llm(text: str, model: str = "qwen2.5:7b-instruct-q4_K_M") -> list[str]:
    """Identifiziert Firmen-/Institutionsnamen via Ollama. Fallback: leere Liste."""
    try:
        import ollama
    except ImportError:
        return []
    snippet = text[:4000]
    prompt = LLM_INSTITUTION_PROMPT.format(text=snippet)
    schema = {"type": "array", "items": {"type": "string"}}
    try:
        response = ollama.chat(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            format=schema,
            options={"temperature": 0, "num_ctx": 4096, "num_predict": 512},
        )
        content = response["message"]["content"] if isinstance(response, dict) else response.message.content
        names = json.loads(content)
        out = []
        for n in names:
            if isinstance(n, str) and len(n.strip()) >= 2:
                out.append(n.strip())
        return out
    except Exception as e:
        print(f"  WARN: LLM-Institution-Erkennung gescheitert: {e}", file=sys.stderr)
        return []


def build_firm_token_map(
    text: str,
    llm_institutions: list[str],
    secret_institutions: list[str] | None = None,
) -> dict[str, str]:
    """Map Original-Firmenname → ``BELEGTYP-X``-Marker.

    Belegtyp bleibt im Output erkennbar (KK-A, BANK-B, BROKER-C, STIFTUNG_3A-A,
    INST-X für LLM-detected). Spezifischer Aussteller wird unkenntlich.
    """
    mapping: dict[str, str] = {}
    counters = {"KK": 0, "BANK": 0, "BROKER": 0, "STIFTUNG_3A": 0, "INST": 0}

    # 1. Bekannte Schweizer Aussteller per Liste (längste zuerst).
    # Wenn ein Längerer schon gemappped ist, kürzere Varianten ALIASEN auf
    # denselben Marker — verhindert dass "BROKER-S" und "BROKER-S" zwei
    # verschiedene BROKER-Marker bekommen, obwohl es dieselbe Firma ist.
    all_known = []
    for typ, names in FIRM_NAMES_BY_TYPE.items():
        for n in names:
            all_known.append((typ, n))
    all_known.sort(key=lambda x: -len(x[1]))
    for typ, name in all_known:
        if name not in text:
            continue
        if name in mapping:
            continue
        # Alias-Check: ist name ein Substring eines bereits gemappten Längeren?
        alias_marker = None
        for existing in mapping:
            if name in existing and len(name) < len(existing):
                alias_marker = mapping[existing]
                break
        if alias_marker:
            mapping[name] = alias_marker
        else:
            counters[typ] += 1
            mapping[name] = f"{typ}-{chr(64 + counters[typ])}"

    # 2. Secrets-File Institutionen (deterministisch, user-maintained).
    # Sowohl längere ("BANK-R") als auch kürzere ("BANK-N") Varianten
    # MÜSSEN in die Map — die per-Token-Replacement-Pattern matchen nur
    # exakte Token. "BANK-N" als Standalone-Token wird NICHT vom
    # "BANK-N AG"-Pattern erkannt.
    # → Wenn substring-Beziehung zu bereits gemappten Einträgen, aliasen
    # auf denselben Marker (statt skip).
    if secret_institutions:
        for inst in sorted(set(secret_institutions), key=lambda x: -len(x)):
            if not inst or inst in mapping:
                continue
            alias_marker = None
            # Kürzerer existing ⊆ inst (z.B. "BANK-R" ⊆ "BANK-R")
            # ODER inst ⊆ längerer existing (z.B. "BANK-N" ⊆ "BANK-N AG")
            for existing, marker in mapping.items():
                if existing == inst:
                    continue
                if existing in inst or inst in existing:
                    alias_marker = marker
                    break
            if alias_marker:
                mapping[inst] = alias_marker
                continue
            counters["INST"] += 1
            mapping[inst] = f"INST-{chr(64 + counters['INST'])}"

    # 3. LLM-detected Institutionen, die nicht schon abgedeckt sind
    for inst in llm_institutions:
        if inst in mapping:
            continue
        if any(inst in k or k in inst for k in mapping):
            continue
        counters["INST"] += 1
        mapping[inst] = f"INST-{chr(64 + counters['INST'])}"

    # 4. Rechtsform-Regex-Pass auf plain_text: fängt Firmen-Vollnamen ab,
    # die weder in den Listen noch beim LLM auftauchten.
    for m in RECHTSFORM_RE.finditer(text):
        full = m.group(0).strip()
        if not full or len(full) > 80:
            continue
        if full in mapping:
            continue
        # Skip wenn substring eines bereits gemappten Längeren ist (oder umgekehrt).
        skip = False
        for existing in list(mapping.keys()):
            if full in existing:
                skip = True
                break
            if existing in full and len(existing) >= 4:
                # Kurzer existing ist Substring der Rechtsform-Phrase
                # — diese ist umfassender, also als ALIAS auf existing-Marker.
                mapping[full] = mapping[existing]
                skip = True
                break
        if skip:
            continue
        counters["INST"] += 1
        mapping[full] = f"INST-{chr(64 + counters['INST'])}"

    return mapping


def build_person_token_map(
    persons: list[str],
    family_variants: list[tuple[str, str]] | None = None,
) -> dict[str, str]:
    """Erzeugt ein Token-/Phrase-Level-Mapping Originalname → Fantasie-Wert.

    Persons sind LLM-detected Strings (z.B. "Hans Mueller"); jeder wird in
    Tokens gesplittet und einzeln gemappt.

    ``family_variants`` ist optional und enthält bereits vor-berechnete
    Paare ``(variant_original, variant_fantasy)`` für family.yaml-Mitglieder.
    Diese Paare werden DETERMINISTISCH übernommen — auch Glued-Varianten wie
    ``"MusterHans"`` → ``"MusterPerson"``. Längste Varianten zuerst gemappt
    (Sortier-Garantie auf Aufrufer-Seite ausreichend, aber wir sortieren hier
    nochmal um robuste Reihenfolge sicherzustellen).

    Glued-Tokens wie "MusterHans" enthalten kein Whitespace; daher würde
    ``original.split()`` nichts liefern. Wir mappen diese full-string als
    eigenen Key.
    """
    mapping: dict[str, str] = {}

    # Schritt 1: family.yaml-Varianten zuerst (deterministisch).
    # Sortiere nach Länge absteigend, damit "MusterHans" vor "Muster" landet
    # — relevant für längengeordnete Substring-Replace im Anonymizer.
    if family_variants:
        for orig, fantasy in sorted(family_variants, key=lambda x: -len(x[0])):
            if orig and orig not in mapping:
                mapping[orig] = fantasy

    # Schritt 2: LLM-detected Personen ergänzen.
    if persons:
        fantasy_pool = FANTASY_PERSONS_FULL + FANTASY_PERSONS_GENERIC
        counter = 0
        for original in sorted(set(persons), key=lambda p: -len(p)):
            fantasy = fantasy_pool[counter % len(fantasy_pool)]
            counter += 1
            orig_parts = original.split()
            fantasy_parts = fantasy.split()
            if not orig_parts:
                continue
            for i, part in enumerate(orig_parts):
                if len(part) < 2:
                    continue
                if part in mapping:
                    continue
                if i < len(fantasy_parts):
                    mapping[part] = fantasy_parts[i]
                else:
                    mapping[part] = fantasy_parts[-1]
            # Auch die volle Phrase ohne Split mappen (für glued-LLM-Outputs).
            if original not in mapping and " " in original:
                mapping[original] = fantasy
    return mapping


def build_family_variant_pairs(family) -> list[tuple[str, str]]:
    """Erzeugt deterministische (original, fantasy) Paare aus family.yaml.

    Jedes Familienmitglied bekommt einen festen Fantasie-Namen aus dem
    Pool (zyklisch indiziert). Jede Variante (Voll, CamelCase-Glued,
    Initial, Reverse, Anrede, Einzeltokens mehrteiliger Namensfelder)
    wird via generate_name_variant_pairs auf die passende Fantasie-
    Variante gemappt — Template-parallel, kein index-basiertes zip mehr.
    """
    if family is None:
        return []
    pairs: list[tuple[str, str]] = []
    pool = FANTASY_PERSONS_FULL + FANTASY_PERSONS_GENERIC
    for idx, member in enumerate(family.members):
        fantasy_full = pool[idx % len(pool)]
        f_first, _, f_last = fantasy_full.partition(" ")
        if not f_last:
            f_last = "Muster"
        pairs.extend(generate_name_variant_pairs(
            member.first_name, member.last_name, f_first, f_last,
        ))
        # Edge: erste/letzte Initiale-Variante explizit absichern
        pairs.append((member.first_name, f_first))
        pairs.append((member.last_name, f_last))
    return pairs


# --------------------------------------------------------------------------- #
# Betrag-Anonymisierung (konsistent pro Dokument)                              #
# --------------------------------------------------------------------------- #


def _format_amount_like(orig: str, scale_factor: float) -> str:
    """Skaliert einen Betrag um ``scale_factor`` und formatiert das Ergebnis
    analog zum Original (Apostroph-Tausender, Dezimal-Separator, -Stellen).

    User-Anforderung: anonymisierte Werte müssen analog zu den Originalen sein,
    damit arithmetische Identitäten (Pos11 = Pos8 − Pos9 − Pos10, VRS ≈ 35%
    × Brutto) auch in anonymisierten Test-Belegen halten. Lösung: pro Dokument
    ein konstanter Skalierungs-Faktor, alle Beträge werden mit ihm multipliziert.

    Rounding: Dezimalstellen werden auf das Original-Format gerundet, sodass
    "12'413.76" × 0.456 → "5'660.48" (zwei Nachkommastellen, mit Apostroph).
    Bei nur ganzzahligen Beträgen wird das Ergebnis auf ganze CHF gerundet.
    """
    has_apostrophe = ("'" in orig) or ("’" in orig) or (" " in orig and orig.replace(" ", "").isdigit())
    rest = orig.replace("'", "").replace("’", "").replace(" ", "")
    if "," in rest and "." not in rest:
        decimal_sep = ","
        int_str, _, dec_str = rest.partition(",")
    elif "." in rest:
        decimal_sep = "."
        int_str, _, dec_str = rest.partition(".")
    else:
        decimal_sep = ""
        int_str, dec_str = rest, ""

    try:
        orig_value = float(int_str + ("." + dec_str if dec_str else ""))
    except ValueError:
        # Fallback bei unparsbarem Input: 0 (sollte nicht vorkommen)
        return orig

    scaled = orig_value * scale_factor
    # Mindest-Wert 1 um Division-by-0 / sinnlose 0-Werte zu vermeiden
    if abs(scaled) < 1 and orig_value > 0:
        scaled = 1.0

    dec_places = len(dec_str)
    if dec_places > 0:
        scaled_str = f"{scaled:.{dec_places}f}"
        new_int, _, new_dec = scaled_str.partition(".")
    else:
        new_int = str(int(round(scaled)))
        new_dec = ""

    if has_apostrophe and len(new_int) > 3:
        # Tausender-Trennzeichen mit Apostroph
        groups = []
        r = new_int
        while len(r) > 3:
            groups.insert(0, r[-3:])
            r = r[:-3]
        groups.insert(0, r)
        new_int = "'".join(groups)

    if new_dec:
        return f"{new_int}{decimal_sep}{new_dec}"
    return new_int


# --------------------------------------------------------------------------- #
# Wort-Anonymisierung                                                          #
# --------------------------------------------------------------------------- #


def _protect_dates(text: str) -> tuple[str, dict[str, str]]:
    """Ersetzt Datums-Substrings durch Platzhalter, gibt Mapping zurueck.

    Wird vor der Amount-Regex angewendet, damit Datumsteile wie '31.12.2022'
    nicht als Betraege fehlinterpretiert werden. Nach der Amount-Anonymisierung
    via :func:`_unprotect_dates` zurueckgesetzt.
    """
    placeholders: dict[str, str] = {}
    counter = [0]

    def repl(m: re.Match[str]) -> str:
        counter[0] += 1
        ph = f"__DATE{counter[0]}__"
        placeholders[ph] = m.group(0)
        return ph

    new_text = DATE_INLINE_RE.sub(repl, text)
    return new_text, placeholders


def _unprotect_dates(text: str, placeholders: dict[str, str]) -> str:
    for ph, original in placeholders.items():
        text = text.replace(ph, original)
    return text


def anonymize_word_text(
    text: str,
    person_token_map: dict[str, str],
    amount_map: dict[str, str],
    scale_factor: float,
    firm_token_map: dict[str, str] | None = None,
    birthdate_token_set: set[str] | None = None,
    account_nr_set: set[str] | None = None,
) -> str:
    """Anonymisiert den Text eines einzelnen Wort-Tokens.

    Anwendung in Reihenfolge:
    1. Datums-Substrings via Platzhalter schuetzen (Vor-Amount-Pass).
    2. IBAN / AHV / Telefon / Email.
    3. Strassennamen, PLZ.
    4. Personen-Token (Vor-/Nachname-Mapping).
    5. CHF-Betraege (mit konsistentem Mapping).
    6. Datums-Platzhalter zurueckersetzen.

    Das Steuerjahr (z.B. "2024") und vollstaendige Daten (TT.MM.JJJJ) werden
    NICHT veraendert -- Plausibility-relevant und macht den anonymisierten
    Beleg fuer Code-Review lesbar.
    """
    # Schritt 0: Geburtsdaten-Maskierung — Privacy-kritisch.
    # "geboren am 01.01.1900" und "geborenam01.01.1900" → "geboren am 01.01.1900".
    # Muss VOR der allgemeinen Date-Protection laufen, sonst wird das echte
    # Geburtsdatum als geschütztes Datum eingefroren.
    text = BIRTHDATE_PHRASE_RE.sub(lambda m: f"{m.group(1)} 01.01.1900", text)
    text = BIRTHDATE_GLUED_RE.sub(lambda m: f"{m.group(1)} 01.01.1900", text)

    # Standalone-Geburtsdaten (aus family.yaml / privacy_secrets):
    # auch ohne "geboren am"-Marker ersetzen. Längste Variante zuerst.
    if birthdate_token_set:
        for bd in sorted(birthdate_token_set, key=lambda x: -len(x)):
            if bd and bd in text:
                text = text.replace(bd, "01.01.1900")

    # Standalone-Kontonummern (aus privacy_secrets) — ersetzen mit Marker.
    if account_nr_set:
        for ac in sorted(account_nr_set, key=lambda x: -len(x)):
            if ac and ac in text:
                text = text.replace(ac, "KONTO-XXXX")

    # AHV-Glued (756 + 10 Ziffern ohne Trenner).
    text = AHV_GLUED_RE.sub("7560000000000", text)

    # Phone-Glued nach Marker im selben Token: "Telefon0440000000" →
    # "Telefon044-000-00-00". (?<![\d\.]) verhindert FPs in Float-Zahlen.
    text = re.sub(
        r"(?i)(Telefon|Telefax|Tel\.?|Fax|Mobile|Mobil|Natel|Phone)\s*"
        r"((?:\+41|0041|0)\d{9})",
        r"\1 044-000-00-00",
        text,
    )

    # PLZ + Stadt (CH-Adress-Footer): generisches Pattern. 4-stellige PLZ
    # (1xxx, 3xxx-9xxx) gefolgt von grossgeschriebenem Wort → "8000 Musterhausen".
    # Pattern bewusst eng, FPs vermeiden.
    text = re.sub(
        r"\b([13-9]\d{3})\s+[A-ZÀ-Þ][\wÀ-ÿ\-]{2,}\b",
        "8000 Musterhausen",
        text,
    )

    # 2xxx ist mehrdeutig (Jahr vs. PLZ). Finding 6: echte 2xxx-PLZ
    # (2000 Neuchâtel, 2502 Biel) wurden vorher NICHT anonymisiert, weil
    # `[13-9]\d{3}` sie ausschloss. Wir anonymisieren ein 2xxx nur dann, wenn
    # ein bekannter CH-Ort folgt — gemeinsame Heuristik mit privacy_gate +
    # grade_run (extractors.pii_patterns.is_plz_token_year_not_plz). Plausible
    # Jahreszahlen ("Steuerjahr 2022") bleiben unangetastet.
    def _scrub_2xxx_plz(m: "re.Match") -> str:
        plz, ort = m.group(1), m.group(2)
        if is_plz_token_year_not_plz(plz, ort):
            return m.group(0)  # Jahr → unverändert lassen
        return "8000 Musterhausen"

    text = re.sub(
        r"\b(2\d{3})\s+([A-ZÀ-Þ][\wÀ-ÿ\-]{2,})\b",
        _scrub_2xxx_plz,
        text,
    )

    # Kontonummer-Patterns CH (Dash- und Dot-Format).
    text = ACCOUNT_NR_DASH_RE.sub("KONTO-XXXX", text)
    text = ACCOUNT_NR_DOT_RE.sub("000.000.000", text)

    # Schritt 1: Datums-Substrings durch Platzhalter ersetzen
    orig, date_placeholders = _protect_dates(text)

    # Token-Voll-Match-Faelle: direkt zurueck (keine Datums-Restauration noetig,
    # da Datumstoken niemals als reine AHV/Phone/Email klassifiziert werden)
    if AHV_RE.fullmatch(text):
        return "756.0000.0000.00"
    if PHONE_RE.fullmatch(text) or PHONE_GLUED_TOKEN_RE.fullmatch(text):
        return "044-000-00-00"
    if EMAIL_RE.fullmatch(text):
        return "anon@example.com"

    # Substring-Faelle: anwenden auf 'orig' (mit Datums-Platzhaltern)
    if EMAIL_RE.search(orig):
        # Substring-Email: Token enthält Email mit anhängendem Komma/Punkt
        # (z.B. "info@firma.ch,"). Email-Teil ersetzen, Rest behalten.
        orig = EMAIL_RE.sub("anon@example.com", orig)
    if IBAN_RE.search(orig):
        orig = IBAN_RE.sub("CH00", orig)
    # Instituts-Domains (P1c): www.beispielbank.ch → INST-Marker; Whitelist
    # (example.com/.ch) bleibt stehen.
    if DOMAIN_RE.search(orig):
        orig = DOMAIN_RE.sub(
            lambda m: m.group(0) if is_domain_whitelisted(m.group(0)) else "INST-X",
            orig,
        )
    if STREET_RE.search(orig):
        orig = STREET_RE.sub("Musterstrasse", orig)
    # PLZ+Ort ohne Leerzeichen (P1b): "8041Zürich" → "8000 Musterhausen".
    if PLZ_ORT_GLUED_RE.search(orig):
        orig = PLZ_ORT_GLUED_RE.sub("8000 Musterhausen", orig)
    # Drittpersonen-Namen "Nachname Vorname, Ort" (P1a) → PERSON-Marker.
    # Konservativ (Komma+Ort als Anker); gewöhnliche Wortfolgen ohne Komma-Ort
    # bleiben unberührt.
    if THIRD_PARTY_NAME_RE.search(orig):
        orig = THIRD_PARTY_NAME_RE.sub("PERSON-X", orig)

    # Personen-Token — Substring-basiert ersetzen (NICHT nur am Token-Anfang).
    # pdfplumber gibt manchmal ganze Zeilen als ein Token ohne Whitespace
    # zurück (Beispiel: "VersichertePerson:MusterHans,geborenam01.01.1900").
    # Whitespace-Split allein erkennt "Muster"/"Hans" im Innern nicht — daher
    # explizit re.sub mit \b-Grenze pro mapped Name.
    # Sortiert nach Länge (lange Namen zuerst), damit "MuellerHans" nicht
    # erst zu "MusterHans" und dann nochmal "Hans"→"Hans" ersetzt wird.
    # Firmen-Replace MUSS vor CamelCase-Split laufen, sonst zerlegt "BANK-P"
    # → "Post Finance" und der Map-Lookup auf "BANK-P" matched nichts mehr.
    if firm_token_map:
        for original in sorted(firm_token_map.keys(), key=lambda x: -len(x)):
            marker = firm_token_map[original]
            # Boundary: nur Lowercase-Letter davor/danach blocken. Damit greift
            # die Substitution auch bei CamelCase-glued OCR-Tokens wie
            # ``CSSKranken-Versicherung`` (KK-C gefolgt von uppercase K).
            # IGNORECASE darf NICHT auf die Lookbehind-/Lookahead-Klassen
            # wirken — sonst werden auch Großbuchstaben gesperrt. Daher
            # inline-Flag (?i:...) nur für den Kern.
            pattern = re.compile(
                rf"(?<![a-zà-ÿ])(?i:{re.escape(original)})(?![a-zà-ÿ])"
            )
            orig = pattern.sub(lambda m, _mk=marker: _mk, orig)

    # CamelCase pre-tokenize für Personen-Replace: füge Leerzeichen vor jedem
    # Großbuchstaben ein, der direkt nach einem Kleinbuchstaben kommt
    # ("MusterHans" → "Muster Hans"). Firmen sind hier schon ersetzt; die
    # generischen Marker (KK-A, BANK-B) enthalten kein "klein → gross".
    camel_split = re.sub(r"(?<=[a-zà-ÿ])(?=[A-ZÀ-Þ])", " ", orig)
    for original in sorted(person_token_map.keys(), key=lambda x: -len(x)):
        fantasy = person_token_map[original]
        pattern = re.compile(
            rf"(?<![a-zà-ÿ]){re.escape(original)}(?![a-zà-ÿ])"
        )
        camel_split = pattern.sub(lambda m, _f=fantasy: _f, camel_split)
    orig = camel_split

    # Zusätzlicher Whitespace-Split-Pass für Edge-Cases (Token mit Satzzeichen
    # am Ende, die das Regex-Lookbehind/-ahead nicht fasst).
    parts = orig.split()
    new_parts = []
    for p in parts:
        # Token kann mit Satzzeichen enden -- abtrennen und wieder anhaengen
        m = re.match(r"^([A-ZÀ-ſ][A-Za-zÀ-ſ\-]+)(.*)$", p)
        if m and m.group(1) in person_token_map:
            new_parts.append(person_token_map[m.group(1)] + (m.group(2) or ""))
        else:
            new_parts.append(p)
    orig = " ".join(new_parts) if parts else orig

    # Reine Jahr-Tokens und kompakte Datums-Tokens NIE anonymisieren —
    # SWISS_AMOUNT_RE würde "2022" und "20221231" sonst fälschlich als
    # CHF-Betrag fassen und das Steuerjahr im Body zerstören.
    if YEAR_ONLY_RE.fullmatch(orig) or COMPACT_DATE_RE.fullmatch(orig):
        orig = _unprotect_dates(orig, date_placeholders)
        return orig

    # CHF-Betrag (mit konsistentem Mapping)
    # Match nur, wenn das gesamte Token ein Betrag ist (sonst sliding-window
    # waere noetig -- aber das ist meist nicht ein Wort-Token)
    if SWISS_AMOUNT_RE.fullmatch(orig):
        if orig in amount_map:
            return amount_map[orig]
        fantasy = _format_amount_like(orig, scale_factor)
        amount_map[orig] = fantasy
        return fantasy

    # Inline-Betraege im Token (z.B. "CHF1234.50")
    def repl_amount(m: re.Match[str]) -> str:
        s = m.group(0)
        # Jahres- und Kompakt-Datums-Tokens nicht inline anonymisieren
        if YEAR_ONLY_RE.fullmatch(s) or COMPACT_DATE_RE.fullmatch(s):
            return s
        if s in amount_map:
            return amount_map[s]
        fantasy = _format_amount_like(s, scale_factor)
        amount_map[s] = fantasy
        return fantasy

    orig = SWISS_AMOUNT_IN_RE.sub(repl_amount, orig)

    # Schritt 6: Datums-Platzhalter zurueckersetzen
    orig = _unprotect_dates(orig, date_placeholders)
    return orig


# --------------------------------------------------------------------------- #
# Pipeline                                                                     #
# --------------------------------------------------------------------------- #


def _final_firm_sweep(pages_out: list[dict], firm_token_map: dict[str, str]) -> int:
    """Letzter Pass: ersetzt alle verbleibenden Firmennamen in Wort-Texten.

    Verwendet einfaches case-insensitives re.sub (ohne Lookahead/Lookbehind),
    um Fälle abzufangen bei denen das Lookahead-Muster in anonymize_word_text
    scheiterte (z.B. "BANK-R" oder "my KK-C").
    Längste Namen zuerst, um Partial-Matches zu vermeiden.
    """
    if not firm_token_map:
        return 0
    patterns = [
        (re.compile(re.escape(name), re.IGNORECASE), marker)
        for name, marker in sorted(firm_token_map.items(), key=lambda x: -len(x[0]))
    ]
    replaced = 0
    for page in pages_out:
        for w in page["words"]:
            t = w["text"]
            for pat, marker in patterns:
                if pat.search(t):
                    # _literal: sonst deutet re.sub den Marker als Template.
                    t = pat.sub(_literal(marker), t)
                    replaced += 1
            w["text"] = t
    return replaced


def safe_filename(name: str) -> str:
    norm = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^A-Za-z0-9._-]+", "_", norm).strip("_")


def _scrub_filename_stem(
    name: str,
    firm_token_map: dict[str, str] | None,
    family_pairs: list[tuple[str, str]] | None,
) -> str:
    """Scrubbt Personen-/Institutionsnamen aus einem Datei-Stem (Finding P2).

    Ersatztexte werden über :func:`_literal` gereicht: ``re.sub`` deutet den
    Ersatz sonst als Template, sodass ein Backslash oder eine Ziffernfolge im
    Namen als Rückverweis gelesen wird. Ein Institutsname mit Backslash liess
    den ganzen Lauf mit ``bad escape`` abstürzen (260904-rmx).

    Case-insensitiver Substring-Replace gegen die bekannten Firm-Marker
    (firm_token_map) und Family-Fantasie-Paare. Längste Originale zuerst, damit
    Teil-Treffer ("Beispiel" ⊂ "BeispielFirma") nicht zu früh greifen. Fängt
    Fälle, in denen der Lookahead-/Boundary-Pass in anonymize_word_text scheiterte
    (Stem-Glue ohne Worttrenner). Der gescrubbte Stem ist damit ungleich dem
    Original-Stem und wird vom truth_mapping automatisch dokumentiert.
    """
    out = name
    if firm_token_map:
        for original in sorted(firm_token_map.keys(), key=lambda x: -len(x)):
            if not original:
                continue
            pattern = re.compile(re.escape(original), re.IGNORECASE)
            out = pattern.sub(_literal(firm_token_map[original]), out)
    if family_pairs:
        for original, fantasy in sorted(family_pairs, key=lambda p: -len(p[0])):
            if not original:
                continue
            pattern = re.compile(re.escape(original), re.IGNORECASE)
            out = pattern.sub(_literal(fantasy), out)
    return out


def _literal(ersatz: str):
    """Macht einen Ersatztext für ``re.sub`` buchstäblich.

    ``re.sub`` deutet den Ersatz als Template: ein Backslash oder eine Folge
    wie ``\\1`` wird als Rückverweis gelesen. Bei Ersatztexten aus Nutzerdaten
    ist das doppelt gefährlich — im harmlosen Fall stürzt der Lauf mit
    ``bad escape`` ab (so geschehen, sobald die Secrets-Datei erstmals gefüllt
    war), im stillen Fall wird ein falscher Text eingesetzt und die
    Anonymisierung bleibt unvollständig.

    Eine Funktion als Ersatz umgeht die Template-Auswertung vollständig.
    """
    return lambda _m: ersatz


def is_amount_pattern(text: str) -> bool:
    """Heuristik: sieht das Wort aus wie ein CHF-Betrag?"""
    return bool(SWISS_AMOUNT_RE.fullmatch(text))


def _scrub_cross_token_pii(words: list[dict]) -> int:
    """Konservativer Post-Pass: erkennt Telefonnummern ueber Token-Grenzen.

    Nur wenn ein Token EINDEUTIG ein Phone-Marker ist ("+41", "0041",
    "Tel.", "Telefon", "Fax", "Phone"), werden die folgenden 3-4 numerischen
    Tokens als Phone-Body geprueft und ersetzt.

    Beispiel: ['+41', '62', '885', '11', '11'] -> ['044', '000', '00', '00', '00']
    Beispiel: ['Tel.', '044', '123', '45', '67']  -> ['Tel.', '044', '000', '00', '00']

    Konservativ: ohne Marker-Token werden numerische Sequenzen nicht
    angetastet (sonst koennten Betraege oder PLZ-Sequenzen kaputtgehen).

    Returns: Anzahl ersetzter Tokens.
    """
    phone_marker_tokens = {"+41", "0041", "tel.", "tel", "telefon", "fax", "phone",
                           "telephone", "telefon:", "tel:", "fax:", "phone:",
                           "telefax", "telefax:", "natel", "mobile", "mobil",
                           "tel/fax", "t.", "t:", "f.", "f:"}
    # A1 (260630-dsn): Body-Chunks dürfen 2–9-stellig sein. Lange Endblöcke
    # (z.B. "+41 11 1111111", Endblock 7-stellig) rutschten zuvor durch, weil
    # ``^\d{2,4}$`` nur kurze Chunks akzeptierte. Marker-Pflicht + Ziffern-
    # Schwelle bleiben (Valoren-Schutz, s.u.).
    phone_body_re = re.compile(r"^\d{2,9}$")
    placeholders = ["000", "000", "00", "00"]

    replaced = 0
    n = len(words)
    i = 0
    while i < n:
        token_lower = words[i]["text"].lower().rstrip(":")
        is_explicit_marker = token_lower in phone_marker_tokens
        is_intl_prefix = words[i]["text"] in ("+41", "0041")

        # Glued-Marker-Variante: "Telefon052" (Marker + 2-4 Ziffern als Suffix).
        # Wir behandeln das Token in zwei Teile: Marker bleibt, Suffix wird
        # als erstes Body-Chunk gewertet und in den Run gesteckt.
        glued_suffix_digits = 0
        if not (is_explicit_marker or is_intl_prefix):
            m = re.match(r"^([A-Za-zÀ-ÿ\.]+?)(\d{2,4})$", words[i]["text"])
            if m and m.group(1).lower().rstrip(":") in phone_marker_tokens:
                is_explicit_marker = True
                glued_suffix_digits = len(m.group(2))
                # Suffix-Ziffern temporär abschneiden; werden nach erfolgreichem
                # Match ersetzt (Suffix → "044", Marker bleibt).

        if not (is_explicit_marker or is_intl_prefix):
            i += 1
            continue

        # Sammle bis zu 4 folgende numerische Tokens (jeder 2-4 Ziffern)
        run: list[int] = []
        j = i + 1
        while j < n and len(run) < 4:
            t = words[j]["text"]
            if phone_body_re.match(t):
                run.append(j)
                j += 1
            else:
                break

        # Phone-Pattern: entweder >=3 kurze numerische Folge-Tokens (klassische
        # Gruppierung "+41 44 123 45 67") ODER >=2 Tokens, von denen einer ein
        # langer Endblock (>=5 Ziffern) ist (A1, 260630-dsn: "+41 11 1111111").
        # Konservativ: Marker-Token bleibt Pflicht (sonst Valoren/Beträge
        # gefährdet) und die Gesamt-Ziffern-Schwelle (>=7) bleibt erhalten,
        # damit ein einzelner 7-stelliger Block OHNE Vorwahl nicht allein triggert.
        _hat_langen_endblock = any(
            len(words[k]["text"]) >= 5 for k in run
        )
        _genug_tokens = len(run) >= 3 or (len(run) >= 2 and _hat_langen_endblock)
        if _genug_tokens:
            total_digits = sum(len(words[k]["text"]) for k in run) + glued_suffix_digits
            if total_digits >= 7:
                if is_intl_prefix:
                    # +41 ersetzen mit "044" Praefix-Stil
                    words[i]["text"] = "044"
                    replaced += 1
                elif glued_suffix_digits:
                    # "Telefon052" → "Telefon044": Marker behalten, Suffix tauschen.
                    m = re.match(r"^([A-Za-zÀ-ÿ\.]+?)(\d{2,4})$", words[i]["text"])
                    if m:
                        words[i]["text"] = f"{m.group(1)}044"
                        replaced += 1
                for k_idx, w_idx in enumerate(run):
                    words[w_idx]["text"] = placeholders[min(k_idx, len(placeholders) - 1)]
                    replaced += 1
                i = j
                continue
        i += 1
    return replaced


def _scrub_cross_token_phrases(
    words: list[dict],
    phrases: list[tuple[str, str]],
) -> int:
    """Ersetzt Multi-Token-Phrasen (z.B. ``"Hans Muster"``) in der Wortliste.

    Iteriert Sliding-Window über alle Token-Sequenzen, vergleicht den
    konkatenierten Text mit Whitespace gegen jede Phrase. Bei Match wird das
    erste Token mit dem Fantasie-Wert ersetzt und die folgenden Tokens werden
    geleert (text="").

    Längste Phrasen zuerst, damit "Herr Hans Muster" vor "Hans Muster" matcht.
    """
    if not phrases or not words:
        return 0
    phrases_sorted = sorted(phrases, key=lambda p: -len(p[0].split()))
    replaced = 0
    n = len(words)
    i = 0
    while i < n:
        if not words[i].get("text"):
            i += 1
            continue
        matched = False
        for orig, fantasy in phrases_sorted:
            parts = orig.split()
            if not parts or i + len(parts) > n:
                continue
            window = [words[i + k]["text"] for k in range(len(parts))]
            # Exakter Match oder Match mit abgetrennter Trailing-Punctuation
            # am letzten Token ("Kinderkrippen," → "Kinderkrippen").
            window_joined = " ".join(window).strip()
            window_stripped = " ".join(
                w.rstrip(".,;:!?»") if j == len(parts) - 1 else w
                for j, w in enumerate(window)
            ).strip()
            if window_joined == orig or window_stripped == orig:
                # Trailing-Punctuation des letzten Tokens erhalten
                last_tok = window[-1]
                trailing = last_tok[len(last_tok.rstrip(".,;:!?»")):]
                words[i]["text"] = fantasy + trailing
                for k in range(1, len(parts)):
                    words[i + k]["text"] = ""
                replaced += len(parts)
                i += len(parts)
                matched = True
                break
        if not matched:
            i += 1
    return replaced


def _scrub_cross_token_plz_ort(words: list[dict]) -> int:
    """Erkennt CH-Adress-Footer ``PLZ Stadt`` über 2 Tokens.

    Beispiel: ``['1485', 'Bern']`` → ``['8000', 'Musterhausen']``.
    Konservativ: PLZ-Bereich 1000-9999, aber NICHT 2000-2030 (Steuerjahre),
    Stadt-Token mit Grossbuchstabe + mindestens 3 weitere Zeichen, keine Zahl.
    """
    replaced = 0
    n = len(words)
    # PLZ-Token kann pure Ziffern sein ("3380") oder mit Country-Prefix
    # ("CH-3380", "FL-9490"). Beide Varianten werden auf "8000" normalisiert.
    # PLZ 1000-9999, aber NICHT 2000-2030 (kollidiert mit Steuerjahren).
    plz_re = re.compile(
        r"^(?:CH-|LI-|FL-|D-|A-|F-|I-)?(?:[13-9]\d{3}|2(?:0[3-9]\d|[1-9]\d{2}))$"
    )
    ort_re = re.compile(r"^[A-ZÀ-Þ][A-Za-zÀ-ÿ\-]{2,}\.?,?$")
    i = 0
    while i + 1 < n:
        if plz_re.fullmatch(words[i]["text"]) and ort_re.fullmatch(words[i + 1]["text"]):
            words[i]["text"] = "8000"
            words[i + 1]["text"] = "Musterhausen"
            replaced += 2
            i += 2
            continue
        i += 1
    return replaced


def _scrub_cross_token_iban(words: list[dict]) -> int:
    """Erkennt CH/LI-IBAN über bis zu 6 aufeinanderfolgende Tokens.

    Ein CH-IBAN ist 21 Zeichen ``CH + 2 Prüfziffern + 17 Ziffern``. PdfPlumber
    splittet ihn meist in 6 Tokens: ``CH93 0076 2011 6238 5295 7``. Wir suchen  # gitleaks:allow
    nach einem Token, das mit ``CH``/``LI`` + 2 Ziffern beginnt und prüfen,
    ob die folgenden Tokens insgesamt 17 weitere Ziffern liefern.
    """
    replaced = 0
    n = len(words)
    # Head kann von "CH79" (4 chars) bis zur kompletten glued IBAN
    # "CH9300762011623852957" (21 chars) variieren.  # gitleaks:allow
    iban_head_re = re.compile(r"^(?:CH|LI)\d{2,19}$", re.IGNORECASE)
    i = 0
    while i < n:
        head = words[i]["text"]
        if not iban_head_re.fullmatch(head):
            i += 1
            continue
        # Bereits IBAN-Placeholder?
        if head.upper() == "CH00":
            i += 1
            continue
        # Sammle bis zu 5 Folge-Tokens (jeweils 1-5 Ziffern)
        digits = sum(c.isdigit() for c in head)
        j = i + 1
        run: list[int] = []
        while j < n and len(run) < 5:
            t = words[j]["text"]
            if t.isdigit() and 1 <= len(t) <= 6:
                run.append(j)
                digits += len(t)
                j += 1
            else:
                break
        # CH-IBAN total = 21 Zeichen = 19 Ziffern + 2 Buchstaben
        # LI-IBAN total = 21 Zeichen = ebenfalls 19 Ziffern + 2 Buchstaben
        # CH/LI-IBAN: 19 weitere Ziffern nach dem Länder-Code. In PDFs
        # gehen oft 1-2 Ziffern verloren oder werden mit Nachbarn gemerged.
        # Daher relaxen auf >=15 — Trade-off: marginale FP-Erhöhung bei
        # 15+ Ziffer-Sequenzen nach "CHxx" gegen vollständige IBAN-Abdeckung.
        if digits >= 13 and head[:2].upper() in {"CH", "LI"}:
            words[i]["text"] = "CH00"
            for w_idx in run:
                words[w_idx]["text"] = ""
            replaced += 1 + len(run)
            i = j
            continue
        i += 1
    return replaced


def _scrub_unmarkered_phones(words: list[dict]) -> int:
    """Erkennt CH-Phones in Token-Sequenzen OHNE Marker davor.

    Sucht 4-Token-Sequenzen der Form ``0XX XXX XX XX`` (CH-Mobile/Festnetz)
    und ersetzt sie durch ``044 000 00 00``. Konservativ: nur exakte Längen
    (3-3-2-2 Ziffern), nur reine Ziffer-Tokens.
    """
    replaced = 0
    n = len(words)
    i = 0
    while i + 3 < n:
        toks = [words[i + k]["text"] for k in range(4)]
        if (
            len(toks[0]) == 3 and toks[0].isdigit() and toks[0].startswith("0")
            and len(toks[1]) == 3 and toks[1].isdigit()
            and len(toks[2]) == 2 and toks[2].isdigit()
            and len(toks[3]) == 2 and toks[3].isdigit()
        ):
            placeholders = ["044", "000", "00", "00"]
            for k in range(4):
                words[i + k]["text"] = placeholders[k]
            replaced += 4
            i += 4
            continue
        i += 1
    return replaced


def _scrub_leistungserbringer_persons(
    words: list[dict],
    family_first_last: set[str] | None = None,
) -> int:
    """Scrubbt Drittpersonen-Namen in Leistungstabellen-Zeilen (R1, 260612-m8t).

    Erkennt das Muster ``Name(-Name)? Datum Betrag`` über Token-Grenzen hinweg:
    pro Sliding-Window aus 2–3 kapitalisierten Namens-Tokens, gefolgt von einem
    Datum-Token und einem Betrag-Token. Bei Treffer werden NUR die Namens-Tokens
    durch ``PERSON-EXT`` ersetzt; Datum + Beträge bleiben (Plausibilität).

    Übersprungen wird:
    - das erste Token in der Label-Whitelist (``Selbstbehalt``, ``Total`` …),
    - family.yaml-Namen (kommen zur Laufzeit, kein Hartcoding),
    - bereits gesetzte Marker (``PERSON-X``, ``KK-A`` …).

    Returns: Anzahl ersetzter Namens-Tokens.
    """
    family_first_last = family_first_last or set()
    _kap = re.compile(r"^[A-ZÀ-ſ][a-zà-ÿ]+(?:-[A-ZÀ-ſ][a-zà-ÿ]+)?$")
    _datum = re.compile(r"^\d{1,2}\.\d{1,2}\.\d{4}$")
    _betrag = re.compile(r"^\d+[.,]\d{2}$")
    _marker = re.compile(r"^[A-Z][A-Z0-9_]*-[A-Z0-9_]+$")  # KK-A, PERSON-X, INST-B

    replaced = 0
    n = len(words)
    i = 0
    while i < n:
        # Wieviele aufeinanderfolgende kapitalisierte Namens-Tokens (max 3)?
        name_len = 0
        while name_len < 3 and i + name_len < n and _kap.match(words[i + name_len]["text"]):
            name_len += 1
        if name_len < 2:
            i += 1
            continue
        # Direkt nach dem Namen: Datum + Betrag?
        j = i + name_len
        if not (j < n and _datum.match(words[j]["text"])):
            i += 1
            continue
        if not (j + 1 < n and _betrag.match(words[j + 1]["text"])):
            i += 1
            continue
        first_token = words[i]["text"]
        # Whitelist / Marker / family-Namen → nicht als Personenname werten.
        if (
            first_token in LEISTUNGS_LABEL_WHITELIST
            or _marker.match(first_token)
            or first_token in family_first_last
        ):
            i += 1
            continue
        # Treffer: Namens-Tokens scrubben (Datum/Betrag bleiben).
        words[i]["text"] = "PERSON-EXT"
        for k in range(1, name_len):
            words[i + k]["text"] = ""
        replaced += name_len
        i = j
    return replaced


def _scrub_cross_token_thirdparty_names(
    words: list[dict],
    family_first_last: set[str] | None = None,
) -> int:
    """Scrubbt Drittpersonen-Namen über Token-Grenzen (A2, 260630-dsn).

    ``THIRD_PARTY_NAME_RE`` und ``LEISTUNGSERBRINGER_PERSON_RE`` (Gruppe
    ``name``) sind auf einem zusammenhängenden String definiert, liefen aber
    bisher nur gegen EINEN Token-String — verteilte Namen ("Beispiel-Muster
    Erika, Teststadt") wurden nie erfasst.

    Dieser Pass setzt pro Seite alle Token in Lesereihenfolge zu einem String
    zusammen (mit Offset→Token-Mapping), wendet beide Patterns darauf an und
    ersetzt für jede gematchte Name-Span NUR die beitragenden Token: erstes
    Token → ``PERSON-X``, folgende Name-Token → "". Datum- und Betrag-Token
    bleiben unangetastet (Plausibilität).

    Übersprungen wird:
    - erstes Token in ``LEISTUNGS_LABEL_WHITELIST`` (Label-Zeile, kein Name),
    - Token aus ``family_first_last`` (eigene Mitglieder, kein Hartcoding),
    - bereits gesetzte Marker (``PERSON-X``, ``KK-A`` …).

    Returns: Anzahl ersetzter Namens-Token.
    """
    family_first_last = family_first_last or set()
    _marker = re.compile(r"^[A-Z][A-Z0-9_]*-[A-Z0-9_]+$")  # KK-A, PERSON-X, INST-B

    # Offset-Mapping: konkatenierter String mit Einzel-Space zwischen Tokens.
    # Pro Zeichen-Position des String-Starts eines Tokens merken wir uns den
    # Token-Index, damit eine Regex-Match-Span auf die Token zurückprojiziert
    # werden kann.
    pieces: list[str] = []
    starts: list[int] = []
    pos = 0
    for w in words:
        starts.append(pos)
        txt = w.get("text", "")
        pieces.append(txt)
        pos += len(txt) + 1  # +1 für den trennenden Space
    joined = " ".join(pieces)

    def _token_indices_for_span(span_start: int, span_end: int) -> list[int]:
        """Liefert die Token-Indizes, deren Text die [start,end)-Span berührt."""
        out: list[int] = []
        for idx, st in enumerate(starts):
            en = st + len(pieces[idx])
            # Überlappt das Token-Intervall [st, en) die Match-Span?
            if st < span_end and en > span_start:
                out.append(idx)
        return out

    replaced = 0
    spans: list[tuple[int, int]] = []
    for pattern in (THIRD_PARTY_NAME_RE, LEISTUNGSERBRINGER_PERSON_RE):
        for m in pattern.finditer(joined):
            # Bei LEISTUNGSERBRINGER_PERSON_RE nur die Gruppe ``name`` scrubben,
            # damit Datum + Betrag erhalten bleiben. THIRD_PARTY_NAME_RE hat
            # keine ``name``-Gruppe → ganzer Match ist der Name (inkl. ", Ort").
            try:
                gs, ge = m.span("name")
            except (IndexError, re.error):
                gs, ge = m.span()
            spans.append((gs, ge))

    for gs, ge in spans:
        idxs = _token_indices_for_span(gs, ge)
        if not idxs:
            continue
        first_token = words[idxs[0]].get("text", "")
        if (
            first_token in LEISTUNGS_LABEL_WHITELIST
            or _marker.match(first_token)
            or first_token in family_first_last
            or any(words[k].get("text", "") in family_first_last for k in idxs)
        ):
            continue
        # Bereits gescrubbt (z.B. durch das andere Pattern)? Dann skip.
        if words[idxs[0]].get("text", "") == "PERSON-X":
            continue
        words[idxs[0]]["text"] = "PERSON-X"
        for k in idxs[1:]:
            words[k]["text"] = ""
        replaced += len(idxs)
    return replaced


def _scrub_ocr_phones_after_label(words: list[dict]) -> int:
    """Scrubbt OCR-verstümmelte Telefonnummern hinter einem Telefon-Label (R2).

    Erkennt ``Telefon``/``Tel.``/``Fax`` … gefolgt von einer (ggf. durch
    Anführungszeichen/Plus verschmutzten) Ziffernfolge mit 9–11 Gesamtziffern,
    verteilt über mehrere Tokens. Konservativ: NUR mit Label davor — ohne Label
    bleiben Ziffernfolgen (Valoren, Identifier) unangetastet.

    Returns: Anzahl ersetzter Ziffern-Tokens.
    """
    label_re = re.compile(
        r"^(Telefon|Telefax|Tel\.?|Fax|Mobile|Mobil|Natel|Phone)[:\"“”+/]*$",
        re.IGNORECASE,
    )
    digit_chunk_re = re.compile(r"^[\"“”+/]*\d[\d\"“”+/.\-]*$")
    placeholders = ["044", "000", "00", "00"]

    replaced = 0
    n = len(words)
    i = 0
    while i < n:
        if not label_re.match(words[i]["text"]):
            i += 1
            continue
        # Sammle bis zu 5 folgende Tokens, die Ziffern-Chunks sind.
        run: list[int] = []
        j = i + 1
        while j < n and len(run) < 5 and digit_chunk_re.match(words[j]["text"]):
            run.append(j)
            j += 1
        total_digits = sum(
            sum(ch.isdigit() for ch in words[k]["text"]) for k in run
        )
        if run and 9 <= total_digits <= 11:
            for idx, w_idx in enumerate(run):
                words[w_idx]["text"] = placeholders[min(idx, len(placeholders) - 1)]
                replaced += 1
            i = j
            continue
        i += 1
    return replaced


def anonymize_one(
    pdf_path: Path,
    output_dir: Path,
    verbose: bool = True,
    family=None,
    secrets: dict | None = None,
) -> Path | None:
    """Anonymisiert ein PDF und schreibt JSON (+ TXT) mit erhaltener Struktur."""
    # Privacy: PDF-Dateinamen können Familiennamen enthalten — daher nur
    # einen sha256-Hash-Präfix loggen, nicht den vollen Namen.
    pdf_id = hashlib.sha256(pdf_path.name.encode("utf-8")).hexdigest()[:10]
    if verbose:
        print(f"-> pdf#{pdf_id}")
    try:
        pdf_doc = pdfplumber.open(pdf_path)
    except Exception as e:
        print(f"   FEHLER beim Oeffnen: {e}", file=sys.stderr)
        return None

    pages_out = []
    all_words = []
    try:
        for idx, page in enumerate(pdf_doc.pages, start=1):
            page_words = page.extract_words(use_text_flow=True, keep_blank_chars=False)
            pages_out.append({
                "page_num": idx,
                "width": float(page.width),
                "height": float(page.height),
                "raw_words": page_words,  # vorerst raw, wird unten ersetzt
            })
            for w in page_words:
                w["page"] = idx
                all_words.append(w)
    finally:
        pdf_doc.close()

    if not all_words:
        print("   WARN: keine Woerter (Scan-PDF? OCR noetig)", file=sys.stderr)
        return None

    plain_text = " ".join(w["text"] for w in all_words)

    secrets = secrets or {}

    # 1. family.yaml-Varianten zuerst (deterministisch, ohne LLM).
    family_pairs = build_family_variant_pairs(family)
    # Zusätzliche Personen aus privacy_secrets.local.yaml (als "synthetische
    # LLM-Findings" einreihen — sie laufen durch denselben Token-Mapper).
    secret_persons = [p for p in (secrets.get("persons") or []) if isinstance(p, str)]

    # 2. Personen via LLM erkennen (ergänzt family.yaml + secrets).
    persons = detect_persons_via_llm(plain_text)
    if verbose and persons:
        print(f"   Erkannte Personen: {len(persons)} (Namen NICHT geloggt)")
    person_token_map = build_person_token_map(
        persons + secret_persons,
        family_variants=family_pairs,
    )

    # 3. Firmen/Institutionen — Liste + secrets + LLM + Rechtsform-Regex.
    institutions = detect_institutions_via_llm(plain_text)
    if verbose and institutions:
        print(f"   Erkannte Institutionen: {len(institutions)} (Namen NICHT geloggt)")
    secret_institutions = flatten_secret_institutions(secrets)
    firm_token_map = build_firm_token_map(
        plain_text, institutions, secret_institutions=secret_institutions,
    )

    # 4. Geburtsdaten-Set aus family.yaml + secrets aufbauen.
    birthdate_token_set: set[str] = set()
    if family is not None:
        for m in family.members:
            if m.birth_date:
                birthdate_token_set.update(generate_birthdate_variants(m.birth_date))
    for bd in (secrets.get("birthdates") or []):
        if isinstance(bd, str):
            # Akzeptiere ISO-Form und DD.MM.YYYY-Form
            if re.match(r"^\d{4}-\d{2}-\d{2}$", bd):
                birthdate_token_set.update(generate_birthdate_variants(bd))
            else:
                # DD.MM.YYYY → ISO konvertieren
                m_match = re.match(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})$", bd)
                if m_match:
                    iso = f"{m_match.group(3)}-{int(m_match.group(2)):02d}-{int(m_match.group(1)):02d}"
                    birthdate_token_set.update(generate_birthdate_variants(iso))
                else:
                    birthdate_token_set.add(bd)

    # 5. Kontonummern-Set aus secrets.
    account_nr_set: set[str] = set()
    for ac in (secrets.get("iban_account_numbers") or []):
        if isinstance(ac, str) and ac.strip():
            account_nr_set.add(ac.strip())

    # 6. Adressen aus secrets — direkt im plain-text ersetzen (über alle Wörter).
    address_set: set[str] = set()
    for a in (secrets.get("addresses") or []):
        if isinstance(a, str) and a.strip():
            address_set.add(a.strip())

    # 2. Pro Dokument deterministischer Skalierungs-Faktor.
    # User-Anforderung: anonymisierte Werte müssen analog zu Originalen sein,
    # damit Plausibilitäts-Regeln (Pos11=Pos8−Pos9−Pos10, VRS≈35%×Brutto) auch
    # auf anonymisierten Test-Belegen halten. Lösung: alle Beträge × Faktor.
    # Faktor aus PDF-Stem-Hash → reproduzierbar; Range 0.30–0.95 (verschiebt
    # Werte sichtbar, behält aber Grössenordnung).
    pdf_stem_bytes = pdf_path.stem.encode("utf-8")
    hash_int = int.from_bytes(hashlib.sha256(pdf_stem_bytes).digest()[:4], "big")
    scale_factor = 0.30 + (hash_int % 65) / 100.0  # 0.30..0.95

    # 3. Wort fuer Wort anonymisieren (Bboxes bleiben erhalten)
    amount_map: dict[str, str] = {}
    for page in pages_out:
        anon_words = []
        for w in page["raw_words"]:
            new_text = anonymize_word_text(
                w["text"], person_token_map, amount_map, scale_factor,
                firm_token_map=firm_token_map,
                birthdate_token_set=birthdate_token_set,
                account_nr_set=account_nr_set,
            )
            # Adressen aus secrets (Multi-Token-Phrasen: hier nur Single-Token-
            # Substring-Replace; volle Phrasen werden im Cross-Token-Pass unten
            # ersetzt).
            for addr in address_set:
                if addr and addr in new_text:
                    new_text = new_text.replace(addr, "Musterstrasse 1")
            anon_words.append({
                "text": new_text,
                # Privacy: orig_text-Debug-Feld entfernt — leakte sonst PII.
                "x0": float(w["x0"]),
                "top": float(w["top"]),
                "x1": float(w["x1"]),
                "bottom": float(w["bottom"]),
                "is_amount": is_amount_pattern(new_text),
            })
        page["words"] = anon_words
        del page["raw_words"]

    # 3a. Post-Pass: Cross-Token Phrasen (Adressen + Person-Phrasen +
    # Firmen-Mehrwort-Namen aus family.yaml/secrets, die über Token-Grenzen
    # verteilt sind).
    cross_phrases: list[tuple[str, str]] = []
    for addr in address_set:
        if " " in addr:
            cross_phrases.append((addr, "Musterstrasse 1"))
    # Family Phrases mit Whitespace (z.B. "Vorname Nachname", "Herr Hans Muster")
    for orig, fant in family_pairs:
        if " " in orig:
            cross_phrases.append((orig, fant))
    # Firmen-Mehrwort-Namen — secret_institutions + LLM-detected aus
    # firm_token_map. Beispiel: "BROKER-T AG" → "INST-A", verteilt auf
    # Tokens ``['True', 'Wealth', 'AG']``.
    if firm_token_map:
        for orig, marker in firm_token_map.items():
            if " " in orig:
                cross_phrases.append((orig, marker))
    if cross_phrases:
        for page in pages_out:
            _scrub_cross_token_phrases(page["words"], cross_phrases)

    # family.yaml-Vor-/Nachnamen als Token-Set (R1: Drittpersonen-Scrubber darf
    # eigene Familienmitglieder NICHT als "Drittperson" überschreiben — die sind
    # bereits über person_token_map korrekt ersetzt). Kein Hartcoding.
    family_first_last: set[str] = set()
    if family is not None:
        for m in family.members:
            for tok in (m.first_name or "", m.last_name or ""):
                tok = tok.strip()
                if tok:
                    family_first_last.add(tok)

    # 3. Post-Pass: Cross-Token Telefonnummern + Drittpersonen unkenntlich machen
    total_phone_scrubs = 0
    for page in pages_out:
        total_phone_scrubs += _scrub_cross_token_pii(page["words"])
        total_phone_scrubs += _scrub_unmarkered_phones(page["words"])
        total_phone_scrubs += _scrub_ocr_phones_after_label(page["words"])  # R2
        total_phone_scrubs += _scrub_cross_token_iban(page["words"])
        total_phone_scrubs += _scrub_cross_token_plz_ort(page["words"])
        # R1: Drittpersonen-Namen in Leistungstabellen-Zeilen.
        _scrub_leistungserbringer_persons(page["words"], family_first_last)
        # A2 (260630-dsn): Cross-Token-Drittnamen (verteilt über mehrere Tokens,
        # beide Reihenfolgen Name-Datum-Betrag und Datum-Name-Ort-Betrag).
        _scrub_cross_token_thirdparty_names(page["words"], family_first_last)
        # is_amount neu berechnen, falls Tokens veraendert wurden
        for w in page["words"]:
            w["is_amount"] = is_amount_pattern(w["text"])
    if verbose and total_phone_scrubs:
        print(f"   Cross-Token Phone-Scrub: {total_phone_scrubs} Tokens unkenntlich")

    # Klassifikation (anhand der anonymisierten Wörter)
    from extractors.classifier import classify

    classify_words: list[dict] = []
    for page in pages_out:
        for w in page["words"]:
            classify_words.append({"text": w["text"]})
    belegtyp, _classify_reason = classify(classify_words)

    # Output JSON: Dateiname und pdf_name-Feld können PII enthalten (z.B.
    # "Zins-_und_Saldoverzeichnis_..._Hans_Peter_Muster_-_2022-12-31.pdf"
    # oder eine reale IBAN im Dateinamen). Daher pdf_name durch dieselben
    # Anonymizer-Pässe schicken und Output-Dateiname auf safe_filename(pdf_id)
    # plus den belegtyp-Hint stützen.
    sanitized_pdf_name = anonymize_word_text(
        pdf_path.name, person_token_map, {}, scale_factor,
        firm_token_map=firm_token_map,
        birthdate_token_set=birthdate_token_set,
        account_nr_set=account_nr_set,
    )
    # Cross-Token Phrasen auch auf pdf_name anwenden (Adressen, Family-Phrasen)
    for orig, fantasy in family_pairs:
        if orig and orig in sanitized_pdf_name:
            sanitized_pdf_name = sanitized_pdf_name.replace(orig, fantasy)
    for addr in address_set:
        if addr and addr in sanitized_pdf_name:
            sanitized_pdf_name = sanitized_pdf_name.replace(addr, "Musterstrasse 1")
    # IBAN-Bestandteile, die als Substring im Filename stehen (z.B.
    # "CH00XXXXXXXXXXXXXXXXX" ohne Spaces): IBAN_RE matched nicht zwingend,  # gitleaks:allow
    # wenn die Prüfziffer eingebaut ist — daher zusätzlicher Pass.
    # IBAN im Dateinamen (auch nach _ oder am Anfang): kein \b da _ ein
    # word-char ist und \b zwischen _ und C nicht greift.
    sanitized_pdf_name = re.sub(
        r"(?<![A-Za-z0-9])(CH|LI)\d{17,21}(?!\d)",
        "CH00",
        sanitized_pdf_name,
        flags=re.IGNORECASE,
    )

    # Dateinamen-Scrub (Finding P2): Personen-/Institutionsnamen, die als
    # Substring im Stem überlebt haben (Lookahead-Pass scheiterte z.B. bei
    # "Lohnausweis_BeispielFirma"), per case-insensitivem Substring-Replace
    # gegen die Firm-Marker scrubben. Längste Namen zuerst (kein Partial-Match).
    # KEINE echten Namen hartcodiert — sie kommen aus secrets/family/LLM zur
    # Laufzeit über firm_token_map.
    sanitized_pdf_name = _scrub_filename_stem(
        sanitized_pdf_name, firm_token_map, family_pairs
    )

    # Letzter Pass: verbleibende Firmennamen in Wort-Texten ersetzen.
    # Fängt Fälle ab, wo Lookahead/Lookbehind im per-Token-Pass scheiterte.
    if firm_token_map:
        _final_firm_sweep(pages_out, firm_token_map)

    out_stem = safe_filename(sanitized_pdf_name).rsplit(".", 1)[0] or f"pdf_{pdf_id}"
    json_path = output_dir / f"{out_stem}.json"
    json_data = {
        "pdf_name": sanitized_pdf_name,
        "belegtyp": belegtyp,
        "pages": pages_out,
        "persons_detected_count": len(persons),  # nur Anzahl — Original-Namen wären PII-Leak in die anonymisierte JSON
        "amounts_replaced": len(amount_map),
    }
    json_path.write_text(json.dumps(json_data, indent=2, ensure_ascii=False), encoding="utf-8")

    # TXT (zum schnellen Drueberlesen)
    txt_path = output_dir / f"{out_stem}.txt"
    txt_content = "\n".join(" ".join(w["text"] for w in page["words"]) for page in pages_out)
    txt_path.write_text(txt_content, encoding="utf-8")

    if verbose:
        print(f"   Belegtyp: {belegtyp}")
        print(f"   Personen ersetzt: {len(persons)}, Betraege ersetzt: {len(amount_map)}")
        print(f"   -> output: json#{hashlib.sha256(str(json_path).encode()).hexdigest()[:10]}")
    return json_path


# Spezialdateien, die NIE als stale gewertet/gelöscht werden dürfen (R3).
# Stem-Präfixe + Suffix-Endungen. Diese Dateien gehören zur Pipeline-
# Infrastruktur bzw. sind manuell gepflegte Wahrheits-/Output-Artefakte.
_STALE_PROTECTED_STEM_PREFIXES = ("_results", "anon_truth_mapping", "field_exceptions",
                                  "korrekturen")
_STALE_PROTECTED_SUFFIXES = (".truth.json", ".md", ".xlsx", ".csv")


def _is_protected_output(name: str) -> bool:
    """True wenn die Datei eine geschützte Spezialdatei ist (R3)."""
    if any(name.endswith(suf) for suf in _STALE_PROTECTED_SUFFIXES):
        return True
    stem = name.rsplit(".", 1)[0]
    return any(stem.startswith(p) for p in _STALE_PROTECTED_STEM_PREFIXES)


def prune_stale_outputs(
    output_dir: Path,
    fresh_stems: set[str],
    *,
    prune: bool = False,
) -> list[str]:
    """Findet stale ``*.json``/``*.txt``-Paare aus früheren Läufen (R3).

    Stale = im Output-Ordner, aber NICHT im aktuellen Lauf erzeugt
    (``fresh_stems``) und keine geschützte Spezialdatei.

    - ``prune=False`` (Default): nur WARNUNG, Datei bleibt. So sieht der User
      stale Paare, die Folgeskripte (process_samples_full, grade_run) sonst
      stumm mitzählen — entscheidet aber selbst über die Löschung.
    - ``prune=True``: stale Dateien werden gelöscht.

    Privacy: die WARNUNG nennt nur Dateinamen, niemals Inhalt.

    Returns:
        Liste der gelöschten Dateipfade (leer bei ``prune=False``).
    """
    deleted: list[str] = []
    stale_names: list[str] = []
    for path in sorted(output_dir.iterdir()):
        if not path.is_file():
            continue
        name = path.name
        if path.suffix not in (".json", ".txt"):
            continue
        if _is_protected_output(name):
            continue
        if path.stem in fresh_stems:
            continue
        # Stale: nicht im Lauf erzeugt, keine Spezialdatei.
        stale_names.append(name)
        if prune:
            path.unlink()
            deleted.append(str(path))
    if stale_names:
        if prune:
            print(
                f"   Stale-Cleanup: {len(deleted)} alte Datei(en) gelöscht: "
                f"{', '.join(sorted(stale_names))}",
                file=sys.stderr,
            )
        else:
            print(
                f"   WARNUNG: {len(stale_names)} stale Datei(en) im Output "
                f"(nicht in diesem Lauf erzeugt): {', '.join(sorted(stale_names))}. "
                f"Mit --prune entfernen, sonst zählen Folgeskripte sie mit.",
                file=sys.stderr,
            )
    return deleted


def main() -> None:
    parser = argparse.ArgumentParser(description="Anonymisiert Steuer-Belege fuer Test-Iteration")
    parser.add_argument(
        "--input",
        type=Path,
        default=Path.home() / "Dokumente" / "Steuern" / "belege",
        help="Input-Verzeichnis mit PDFs",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "evals" / "samples_real",
        help="Output-Verzeichnis",
    )
    parser.add_argument(
        "--pdf",
        type=Path,
        default=None,
        help="Optional: nur ein bestimmtes PDF anonymisieren",
    )
    parser.add_argument(
        "--prune",
        action="store_true",
        default=False,
        help=(
            "Stale Output-Paare (aus früheren Läufen, nicht im aktuellen Input) "
            "löschen. Ohne Flag wird nur gewarnt."
        ),
    )
    args = parser.parse_args()

    args.output.mkdir(parents=True, exist_ok=True)

    # Privacy-Setup: family.yaml + privacy_secrets.local.yaml einmalig laden.
    # Privacy: weder Inhalt noch Schlüssel werden geloggt.
    try:
        family = load_family(ROOT / "family.yaml")
    except Exception as e:
        print(f"   WARN: family.yaml konnte nicht geladen werden: {type(e).__name__}", file=sys.stderr)
        family = None
    secrets = load_privacy_secrets(ROOT / "privacy_secrets.local.yaml")
    if family is not None:
        print(f"   family.yaml geladen: {len(family.members)} Mitglieder (Namen NICHT geloggt)")
    if secrets and any(secrets.get(k) for k in secrets):
        sec_counts = {
            k: (len(v) if isinstance(v, (list, dict)) else 0)
            for k, v in secrets.items()
        }
        print(f"   privacy_secrets.local.yaml geladen: {sec_counts} (Inhalte NICHT geloggt)")

    if args.pdf:
        pdfs = [args.pdf]
    else:
        if not args.input.is_dir():
            print(f"FEHLER: {args.input} existiert nicht", file=sys.stderr)
            sys.exit(1)
        pdfs = sorted(args.input.glob("*.pdf"))

    print(f"Anonymisiere {len(pdfs)} PDF(s) -> {args.output}")
    print()
    success = 0
    # Mapping: anonymisierter Stem → originaler PDF-Stem (für Truth-File-Verknüpfung)
    truth_mapping: dict[str, str] = {}
    # Stems, die dieser Lauf erzeugt hat (R3: für Stale-Cleanup am Lauf-Ende).
    fresh_stems: set[str] = set()
    for pdf in pdfs:
        result = anonymize_one(pdf, args.output, family=family, secrets=secrets)
        if result:
            # result ist der json_path; sein Stem ist der anonymisierte Dateiname
            anon_stem = result.stem
            fresh_stems.add(anon_stem)
            # safe_filename normalisiert Original-PDF-Stem (Umlaute, Leerzeichen →
            # Underscores) — damit er mit dem Truth-File-Stem (ebenfalls safe_filename)
            # übereinstimmt.
            # safe_filename normalisiert Umlaute + Leerzeichen → Underscores.
            # KEIN rsplit(".",1)[0] — pdf.stem ist bereits ohne Extension,
            # und Dateinamen können legitime Punkte enthalten ("2022-12-31").
            orig_stem_raw = pdf.stem
            orig_stem = safe_filename(orig_stem_raw) or orig_stem_raw
            if anon_stem != orig_stem:
                truth_mapping[anon_stem] = orig_stem
            success += 1
    # Schreibe Mapping-Datei (wird von grade_run.py check_json_truth_mapping verwendet)
    mapping_path = args.output / "anon_truth_mapping.json"
    mapping_path.write_text(
        json.dumps(truth_mapping, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    # R3: stale Output-Paare aus früheren Läufen sichtbar machen / entfernen.
    prune_stale_outputs(args.output, fresh_stems, prune=args.prune)
    print()
    print(f"Fertig: {success}/{len(pdfs)} anonymisiert -> {args.output}")
    if truth_mapping:
        print(f"  anon_truth_mapping.json: {len(truth_mapping)} Umbenennungen dokumentiert")
    print()
    print("Naechster Schritt: python scripts/process_samples_full.py --samples evals/samples_real_2022")


if __name__ == "__main__":
    main()
