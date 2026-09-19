"""Gemeinsame PII-Pattern-Bibliothek für Anonymizer und Privacy-Gate.

Privacy-Constraint: dieses Modul nimmt PII-Inhalte (Namen, Geburtsdaten,
IBANs, Adressen) als Input und liefert sie wieder als Output — aber es
**logged sie niemals**, hält sie nicht als Modul-Konstante und gibt nichts
an stdout/stderr aus. Aufrufer sind dafür verantwortlich, Rückgaben
nicht zu drucken.

Liefert zwei Klassen von Helfern:

* :func:`generate_name_variants` — alle Schreibweisen eines Vor-/Nachname-Paars
  (Glued, CamelCase, Initial, Reverse, Anredeformen). Verwendet von:
  - Anonymizer: synthetische "LLM-Findings", damit family.yaml-Personen
    deterministisch ersetzt werden, auch wenn der LLM-Detect sie verpasst.
  - Privacy-Gate: Sucht in anonymisierten Artefakten nach diesen Varianten,
    failed bei jedem Treffer.

* :func:`generate_birthdate_variants` — alle Schreibweisen eines ISO-Datums.

* :func:`load_privacy_secrets` — Lader für ``privacy_secrets.local.yaml``.

* Heuristik-Regexes (IBAN, AHV, Telefon, PLZ-Strasse) für den Privacy-Gate.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml


# --------------------------------------------------------------------------- #
# Name-Varianten                                                               #
# --------------------------------------------------------------------------- #


# Varianten-Templates als Funktionsliste: jedes Template bildet ein
# (first, last)-Paar auf genau eine Schreibvariante ab. Anonymizer und
# Privacy-Gate teilen sich diese Liste — generate_name_variant_pairs wendet
# dasselbe Template parallel auf Original- und Fantasie-Namen an, womit die
# (orig, fantasy)-Paarung strukturell garantiert ist (statt des früheren
# index-basierten zip über zwei getrennt sortierte Listen).
_VARIANT_TEMPLATES: list = [
    # Standalone-Tokens
    lambda f, l: f,
    lambda f, l: l,
    # Voll-Schreibweisen
    lambda f, l: f"{f} {l}",
    lambda f, l: f"{l} {f}",
    lambda f, l: f"{l}, {f}",
    # Anredeformen
    lambda f, l: f"Herr {f} {l}",
    lambda f, l: f"Frau {f} {l}",
    lambda f, l: f"Hr. {f} {l}",
    lambda f, l: f"Fr. {f} {l}",
    # Initial-Varianten
    lambda f, l: f"{f[0]}. {l}",
    lambda f, l: f"{f[0]} {l}",
    # CamelCase-Glued
    lambda f, l: f"{f}{l}",
    lambda f, l: f"{l}{f}",
    # Dot-Glued
    lambda f, l: f"{f}.{l}",
    lambda f, l: f"{l}.{f}",
    # Hyphen-Glued
    lambda f, l: f"{f}-{l}",
    lambda f, l: f"{l}-{f}",
]


def _name_token_forms(field: str) -> list[str]:
    """Zerlegt ein Namensfeld in alle relevanten Formen.

    Mehrteilige Felder (Doppel-Vorname „Vorname Zweitname", mehrteiliger
    Nachname) liefern zusätzlich zur Gesamtphrase jeden Whitespace-Token
    einzeln (len ≥ 2). Belege nennen Personen oft nur mit EINEM der Tokens
    — ohne diese Formen überlebt der Einzeltoken die Anonymisierung und
    bleibt für den Privacy-Gate unsichtbar.
    """
    field = field.strip()
    if not field:
        return []
    forms = [field]
    tokens = field.split()
    if len(tokens) > 1:
        forms.extend(t for t in tokens if len(t) >= 2)
    return forms


def generate_name_variants(first: str, last: str) -> list[str]:
    """Erzeugt alle Schreibvarianten eines Vor-/Nachname-Paars.

    Liefert eine deterministisch geordnete Liste (lange Varianten zuerst, damit
    Map-basierte Ersetzungen "MusterHans" vor "Muster" treffen).

    Beispiel für ``first="Hans"``, ``last="Muster"`` enthält u.a.:
    ``Hans``, ``Muster``, ``Hans Muster``, ``Muster Hans``, ``Muster, Hans``,
    ``Herr Hans Muster``, ``N. Muster``, ``HansMuster``, ``MusterHans``,
    ``Hans.Muster``, ``Muster.Hans``, ``Hans-Muster``.

    Mehrteilige Namensfelder (z.B. ``first="Hans Peter"``) durchlaufen die
    Templates zusätzlich für jede Token-Kombination — insbesondere ist jeder
    Einzeltoken (``Hans``, ``Peter``) selbst eine Variante.
    """
    first = first.strip()
    last = last.strip()
    if not first or not last:
        return []

    variants: list[str] = []
    for f in _name_token_forms(first):
        for l in _name_token_forms(last):
            for tpl in _VARIANT_TEMPLATES:
                variants.append(tpl(f, l))

    # Dedupe, längste zuerst
    seen: set[str] = set()
    out: list[str] = []
    for v in sorted(variants, key=lambda x: -len(x)):
        if v not in seen:
            seen.add(v)
            out.append(v)
    return out


def generate_name_variant_pairs(
    first: str, last: str, f_first: str, f_last: str
) -> list[tuple[str, str]]:
    """Erzeugt (original, fantasy)-Paare für die Anonymizer-Ersetzungs-Map.

    Jedes Template wird parallel auf die Original-Token-Kombination und auf
    das Fantasie-Paar angewendet — die Paarung ist pro Template strukturell
    korrekt. Alle Tokens eines mehrteiligen Vornamens mappen auf den
    Fantasie-Vornamen, alle Nachname-Tokens auf den Fantasie-Nachnamen.

    Rückgabe: dedupliziert nach Original, längste Originale zuerst (damit
    Map-Ersetzungen die längste Variante zuerst treffen).
    """
    first, last = first.strip(), last.strip()
    f_first, f_last = f_first.strip(), f_last.strip()
    if not first or not last or not f_first or not f_last:
        return []

    pairs: list[tuple[str, str]] = []
    for f in _name_token_forms(first):
        for l in _name_token_forms(last):
            for tpl in _VARIANT_TEMPLATES:
                pairs.append((tpl(f, l), tpl(f_first, f_last)))

    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for orig, fant in sorted(pairs, key=lambda p: -len(p[0])):
        if orig not in seen:
            seen.add(orig)
            out.append((orig, fant))
    return out


# --------------------------------------------------------------------------- #
# Geburtsdaten-Varianten                                                       #
# --------------------------------------------------------------------------- #


def generate_birthdate_variants(iso_date: str) -> list[str]:
    """Erzeugt alle gängigen Schreibvarianten eines ISO-Datums ``YYYY-MM-DD``.

    Liefert: ISO, ``DD.MM.YYYY``, ``DD.MM.YY``, ``D.M.YYYY``, ``D.M.YY``,
    ``YYYYMMDD`` (kompakt), ``YYYY/MM/DD``, ``DD/MM/YYYY``.
    Bei ungültigen Inputs eine leere Liste.
    """
    try:
        d = datetime.strptime(iso_date.strip(), "%Y-%m-%d")
    except (ValueError, AttributeError):
        return []

    yyyy = f"{d.year:04d}"
    mm = f"{d.month:02d}"
    dd = f"{d.day:02d}"
    yy = yyyy[-2:]
    m = str(d.month)
    day = str(d.day)

    variants = [
        f"{yyyy}-{mm}-{dd}",
        f"{dd}.{mm}.{yyyy}",
        f"{dd}.{mm}.{yy}",
        f"{day}.{m}.{yyyy}",
        f"{day}.{m}.{yy}",
        f"{yyyy}{mm}{dd}",
        f"{yyyy}/{mm}/{dd}",
        f"{dd}/{mm}/{yyyy}",
        f"{dd}-{mm}-{yyyy}",
    ]
    # Dedupe, längste zuerst
    seen: set[str] = set()
    out: list[str] = []
    for v in sorted(variants, key=lambda x: -len(x)):
        if v not in seen:
            seen.add(v)
            out.append(v)
    return out


# --------------------------------------------------------------------------- #
# privacy_secrets.local.yaml Lader                                             #
# --------------------------------------------------------------------------- #


def load_privacy_secrets(path: Path | None = None) -> dict[str, Any]:
    """Lädt ``privacy_secrets.local.yaml`` aus dem Repo-Root.

    Liefert ein dict mit Top-Level-Keys ``institutions``, ``persons``,
    ``iban_account_numbers``, ``addresses``, ``birthdates``. Wenn die Datei
    fehlt, wird ein leeres dict mit allen erwarteten Keys zurückgegeben
    (graceful — der Anonymizer/Gate funktioniert auch ohne).
    """
    empty: dict[str, Any] = {
        "institutions": {},
        "persons": [],
        "iban_account_numbers": [],
        "addresses": [],
        "birthdates": [],
    }
    target = path if path is not None else Path("privacy_secrets.local.yaml")
    if not target.exists():
        return empty
    try:
        raw = yaml.safe_load(target.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        return empty
    if not isinstance(raw, dict):
        return empty
    # Merge mit Defaults
    for k, v in empty.items():
        raw.setdefault(k, v)
    return raw


def flatten_secret_institutions(secrets: dict[str, Any]) -> list[str]:
    """Flacht den ``institutions``-Subbaum zu einer flachen Namensliste ab."""
    insts = secrets.get("institutions", {}) or {}
    if not isinstance(insts, dict):
        return []
    out: list[str] = []
    for _category, names in insts.items():
        if isinstance(names, list):
            for n in names:
                if isinstance(n, str) and n.strip():
                    out.append(n.strip())
    return out


# --------------------------------------------------------------------------- #
# Heuristik-Patterns (immer aktiv im Privacy-Gate)                             #
# --------------------------------------------------------------------------- #

# IBAN: CH/LI gefolgt von Prüfziffer + 16-19 Ziffern (mit oder ohne Spaces).
# Erkennt sowohl `CH9300762011623852957` als auch `CH93 0076 2011 6238 5295 7`.  # gitleaks:allow
IBAN_REAL_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:CH|LI)(\d{2})[\s\d]{15,30}(?!\d)",
    re.IGNORECASE,
)

# AHV-Nr CH: 756 + 10 Ziffern, mit Punkten/Spaces oder ohne (glued).
# (?<![\d\.]) verhindert FPs bei Bbox-Floats.
AHV_REAL_RE = re.compile(
    r"(?<![\d\.])756[\.\s]?\d{4}[\.\s]?\d{4}[\.\s]?\d{2}(?!\d)"
)
AHV_GLUED_RE = re.compile(r"(?<![\d\.])756\d{10}(?!\d)")

# CH-Telefon (strikt für Privacy-Gate): CH-Prefix +41/0041 ODER 0XX MIT
# Pflicht-Trennzeichen (Space/Dash/Dot). Damit FPs aus zusammenhängenden
# 10-Ziffern-Sequenzen (Valoren-Nummern in ETF-Daten-Tabellen, Identifier
# wie "0123456789") sicher ausgeschlossen sind.
# (?<![\d\.]) verhindert FPs bei Bounding-Box-Floats wie "384.0478175588".
PHONE_RE = re.compile(
    r"(?<![\d\.])(?:"
    r"(?:\+41|0041)[\s.\-]?\d{2}[\s.\-]?\d{3}[\s.\-]?\d{2}[\s.\-]?\d{2}"
    r"|0\d{2}[\s.\-]\d{3}[\s.\-]\d{2}[\s.\-]\d{2}"
    r")(?!\d)"
)

# E-Mail
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")

# Bekannte CH-Ortschaften (häufige Adress-Footer). Wird vom Gate genutzt,
# um zwischen echtem PLZ+Ort und FPs wie "2022 Steuerausweis" zu unterscheiden.
# Liste bewusst nicht erschöpfend — User kann eigene Adressen via
# privacy_secrets.local.yaml.addresses deklarieren für volle Abdeckung.
KNOWN_CH_ORTE: set[str] = {
    "Zürich", "Zuerich", "Bern", "Basel", "Lausanne", "Genf", "Geneve",
    "Winterthur", "Luzern", "St. Gallen", "St.Gallen", "Lugano", "Biel",
    "Thun", "Köniz", "Koeniz", "La Chaux-de-Fonds", "Schaffhausen",
    "Fribourg", "Freiburg", "Chur", "Vernier", "Neuchâtel", "Neuchatel",
    "Uster", "Sion", "Zug", "Yverdon", "Emmen", "Rapperswil", "Kriens",
    "Dübendorf", "Duebendorf", "Dietikon", "Aarau", "Wil", "Baar",
    "Wettingen", "Allschwil", "Riehen", "Wädenswil", "Waedenswil",
    "Renens", "Kreuzlingen", "Olten", "Baden", "Pully", "Solothurn",
    "Bellinzona", "Locarno", "Carouge", "Meyrin", "Kloten", "Volketswil",
    "Onex", "Adliswil", "Bülach", "Buelach", "Horgen", "Wohlen",
    "Burgdorf", "Frauenfeld", "Liestal", "Muttenz", "Reinach", "Pratteln",
    "Davos", "Brig", "Glarus", "Spiez", "Steffisburg", "Münsingen",
    "Muensingen", "Ostermundigen", "Belp", "Worb", "Münchenbuchsee",
    "Muenchenbuchsee", "Stans", "Sarnen", "Schwyz", "Altdorf", "Herisau",
    "Appenzell", "Wangen", "Mühlethurnen", "Muehlethurnen", "Toffen",
    "Wattenwil", "Burgistein", "Riggisberg", "Konolfingen",
    "Langnau", "Interlaken", "Sigriswil", "Sumiswald",
    "Huttwil", "Langenthal", "Herzogenbuchsee", "Lyss", "Aarberg",
    "Ins", "Erlach", "Nidau", "Lengnau", "Grenchen", "Bettlach",
    "Selzach", "Gerzensee", "Liebefeld", "Gümligen", "Guemligen", "Muri", "Bolligen",
    "Ittigen", "Zollikofen", "Bremgarten", "Kirchberg",
    "Utzenstorf", "Bätterkinden", "Baetterkinden", "Limpach", "Fraubrunnen",
    "Hindelbank", "Heimiswil", "Affoltern", "Wasen", "Eggiwil", "Schangnau", "Trubschachen",
    "Trub", "Marbach", "Escholzmatt", "Schüpfheim", "Schuepfheim",
    "Flühli", "Fluehli", "Sörenberg", "Soerenberg", "Romoos", "Werthenstein",
    "Wolhusen", "Entlebuch", "Hasle", "Doppleschwand",
    "Hellbühl", "Hellbuehl", "Malters", "Schwarzenberg", "Eigenthal",
    "Horw", "Meggen", "Adligenswil", "Udligenswil", "Greppen",
    "Vitznau", "Weggis", "Gersau", "Brunnen", "Ingenbohl", "Steinen", "Lauerz", "Goldau", "Arth", "Küssnacht", "Kuessnacht",
}

# PLZ + Ortsname (4-stellige Zahl + grossgeschriebenes Wort).
# Strikt: Ort muss in KNOWN_CH_ORTE-Liste (oder via privacy_secrets ergänzt).
# Damit werden FPs wie "2022 Steuerausweis" sicher ausgefiltert.
PLZ_ORT_RE = re.compile(r"\b\d{4}\s+([A-ZÀ-Þ][\wÀ-ÿ\-\.]+)\b")

# PLZ + Ort OHNE Leerzeichen ("8000Musterstadt", "8041Zürich"). pdfplumber gibt
# bei engem Layout PLZ und Ort als ein einziges Token zurück. Privacy-Constraint:
# auch diese Glued-Form muss erkannt werden (Finding P1b). Erste Gruppe = PLZ
# (4 Ziffern), zweite Gruppe = Ortsname (Grossbuchstabe + Kleinbuchstaben).
PLZ_ORT_GLUED_RE = re.compile(r"\b(\d{4})([A-ZÀ-Þ][a-zà-ÿ][\wÀ-ÿ\-]+)\b")

# A3 (260630-dsn): Dokument-Label-Wörter, die NACH einer 4-stelligen Zahl
# stehen können ("1267 Abrechnungsperiode"), aber KEINE Ortschaft sind. Der
# Privacy-Gate würde sie sonst fälschlich als ``plz_ort_real``-Treffer werten.
# Single Source of Truth: vom Gate (_is_placeholder_match) konsumiert; bei
# Bedarf auch vom Scrubber nutzbar. Eng gehalten — NUR klar nicht-ortige
# Label-Wörter, damit echte "8001 Zürich"-Paare Treffer bleiben.
PLZ_ORT_LABEL_DENYLIST: set[str] = {
    "Abrechnungsperiode", "Police", "Policen", "Police-Nr", "Police-Nr.",
    "Prämien", "Prämie", "Erstellt", "Zusammenzug", "Versicherte",
    "Jahrgang",
}

# Drittpersonen-Namen in Tabellenkontext: "Nachname(-Nachname)? Vorname, Ort"
# (Finding P1a). Belege listen Drittpersonen oft als kapitalisierte Namens-Zeile
# mit nachgestelltem Ort (z.B. Begünstigte, Mitversicherte). Konservativ gehalten:
# Komma + Ort als Anker, damit gewöhnliche Sätze ("Der Betrag, ausbezahlt") NICHT
# matchen. Erlaubt einen optionalen Doppel-Nachnamen ("Beispiel-Muster Erika").
THIRD_PARTY_NAME_RE = re.compile(
    r"\b[A-ZÀ-ſ][a-zà-ÿ]+(?:-[A-ZÀ-ſ][a-zà-ÿ]+)? "
    r"[A-ZÀ-ſ][a-zà-ÿ]+, [A-ZÀ-ſ][a-zà-ÿ]+\b"
)

# Leistungserbringer-/Drittpersonen-Namen in Leistungstabellen (R1, 260612-m8t).
# KK-Leistungsabrechnungen listen Drittpersonen als kapitalisierte Namens-Zeile
# DIREKT gefolgt von Datum + Betrag (ohne nachgestellten Ort, daher fasst
# THIRD_PARTY_NAME_RE sie nicht): "Beispiel Musterfrau-Test 22.12.2023 77.06".
# Pattern: 2–3 kapitalisierte Wörter (eines optional mit Bindestrich),
# UNMITTELBAR gefolgt von einem Datum TT.MM.JJJJ und mind. einem Betrag.
# Der Namensteil steht in Gruppe "name", damit der Anonymizer NUR den Namen
# ersetzt (Datum + Beträge bleiben für Plausibilität erhalten).
# Konservativ: Datum+Betrag-Anker verhindert FPs auf gewöhnliche Wortfolgen.
_KAP_WORT = r"[A-ZÀ-ſ][a-zà-ÿ]+(?:-[A-ZÀ-ſ][a-zà-ÿ]+)?"
LEISTUNGSERBRINGER_PERSON_RE = re.compile(
    r"(?P<name>" + _KAP_WORT + r"(?: " + _KAP_WORT + r"){1,2})"
    r"\s+\d{1,2}\.\d{1,2}\.\d{4}"
    r"\s+\d+[.,]\d{2}"
)

# Whitelist: erstes Token ist ein Label, kein Personenname. Der Anonymizer
# prüft das erste Token gegen diese Menge und lässt die Zeile dann unverändert
# ("Selbstbehalt 22.12.2023 50.00" ist eine Label-Zeile, kein Name).
LEISTUNGS_LABEL_WHITELIST: set[str] = {
    "Selbstbehalt", "Jahresfranchise", "Franchise", "Kostenbeteiligung",
    "Total", "Datum", "Behandlung", "Leistung", "Rechnung", "Betrag",
    "Subtotal", "Saldo", "Zwischentotal", "Position", "Pos",
}

# OCR-verstümmelte Telefonnummern hinter Telefon-Label (R2, 260612-m8t).
# Scan-OCR liefert hinter "Telefon"/"Tel." oft Müll-Trennzeichen
# (Anführungszeichen ["“”], +, /) statt sauberer Spaces:
#   'Telefon "4 43 244 6854'.
# PHONE_RE verlangt strikte Trenner und fasst das nicht. Dieses Pattern matcht
# NUR mit Label-Präfix (konservativ — sonst würden Valoren-/Identifier-Folgen
# ohne Telefon-Label fälschlich gescrubbt). Gesamt-Ziffernzahl 9–11.
# Gruppe "num" ist die komplette (möglicherweise verstümmelte) Ziffernfolge.
PHONE_OCR_RE = re.compile(
    r"(?P<label>Telefon|Telefax|Tel\.?|Fax|Mobile|Mobil|Natel|Phone)"
    r"[\s:]*"
    r"(?P<num>[\"“”+/]{0,3}\s*\d(?:[\s.\-\"“”+/]*\d){8,10})"
)


# Instituts-/Firmen-Domains ("www.beispielbank.ch", "www.muster.com"). Finding
# P1c: Domains verraten den Aussteller und müssen durch einen INST-Marker
# ersetzt werden. Whitelist (Beispiel-Domains aus Tests/Doku) bleibt stehen.
DOMAIN_RE = re.compile(r"\bwww\.[A-Za-z0-9-]+\.(?:ch|com)\b", re.IGNORECASE)

# Domains, die als synthetische Beispiele bewusst NICHT anonymisiert werden.
DOMAIN_WHITELIST: set[str] = {
    "example.com",
    "example.ch",
    "www.example.com",
    "www.example.ch",
}


def is_domain_whitelisted(domain: str) -> bool:
    """True wenn die Domain in der Whitelist steht (case-insensitive)."""
    return domain.strip().lower() in DOMAIN_WHITELIST

# Kontonummer-Patterns CH (z.B. "15-20962-7", "92-122942-5", "300.710.305")
ACCOUNT_NR_DASH_RE = re.compile(r"\b\d{2,3}-\d{4,8}-\d\b")
ACCOUNT_NR_DOT_RE = re.compile(r"\b\d{3}\.\d{3}\.\d{3}\b")


# Placeholders die der Anonymizer/Generator setzt — NICHT als Treffer werten.
PLACEHOLDER_STRINGS: set[str] = {
    "CH00",
    "756.0000.0000.00",
    "044-000-00-00",
    "044 000 00 00",
    "anon@example.com",
    "Musterstrasse",
    "01.01.1900",
}


def is_iban_placeholder(match: str) -> bool:
    """True wenn das IBAN-Match der Anonymizer-Platzhalter ist (Prüfziffer 00)."""
    m = IBAN_REAL_RE.search(match)
    if not m:
        return False
    return m.group(1) == "00"


def is_ahv_placeholder(match: str) -> bool:
    """True wenn das AHV-Match der Anonymizer-Platzhalter ist (alle Nullen)."""
    digits_only = re.sub(r"\D", "", match)
    if not digits_only.startswith("756"):
        return False
    return set(digits_only[3:]) == {"0"}


def is_phone_placeholder(match: str) -> bool:
    """True wenn das Phone-Match ein Platzhalter ist (drei oder mehr Nullen)."""
    digits_only = re.sub(r"\D", "", match)
    # Anonymizer schreibt 044 000 00 00 / 044-000-00-00 / 0440000000
    if digits_only in {"0440000000", "044000000000"}:
        return True
    # Heuristik: mehr als die Hälfte der Stellen sind 0 → Placeholder
    if len(digits_only) >= 9 and digits_only.count("0") >= 6:
        return True
    return False


# --------------------------------------------------------------------------- #
# PLZ-vs-Jahr-Disambiguierung (Single Source of Truth, Finding 6)             #
# --------------------------------------------------------------------------- #
# Problem: 4-stellige Zahlen im Bereich 2000–2099 sind mehrdeutig — entweder
# eine plausible Jahreszahl ("Steuerjahr 2022") ODER eine echte CH-PLZ
# (2000 Neuchâtel, 2502 Biel, 2800 Delémont). Vorher schloss `[13-9]\d{3}`
# bzw. der `first4[:2] == "20"`-Skip ALLE 2xxx pauschal aus → echte 2xxx-PLZ
# wurden weder anonymisiert noch gemeldet (PII-Leak-Risiko).
#
# Diese Funktion ist die gemeinsame Heuristik, die Scrubber (anonymize_belege)
# UND Checker (privacy_gate, grade_run) teilen, damit beide identisch
# entscheiden.

# Orte, deren reale PLZ im Bereich 2000–2999 liegt (Kantone NE/JU, Berner
# Jura, Seeland). Nur diese Orte machen eine 2xxx-Zahl zur plausiblen PLZ —
# "2023 Bern" ist dagegen Jahreszahl + Ort (Berns PLZ ist 3xxx), typisch in
# Briefkoepfen ("Kontoauszug 2023 Bern").
KNOWN_2XXX_ORTE: set[str] = {
    "Neuchâtel", "Neuchatel", "Biel", "Bienne", "La Chaux-de-Fonds",
    "Le Locle", "Delémont", "Delemont", "Grenchen", "Moutier",
    "Porrentruy", "Saint-Imier", "St-Imier", "Lyss", "Nidau",
    "La Neuveville", "Tramelan", "Saignelégier", "Saignelegier",
    "Cernier", "Boudry", "Colombier", "Peseux", "Marin-Epagnier",
    "Le Landeron", "Gorgier", "Bevaix", "Couvet", "Fleurier",
}


def is_plz_token_year_not_plz(plz_token: str, following_token: str | None) -> bool:
    """Entscheidet, ob eine 4-stellige Zahl eine Jahreszahl (KEINE PLZ) ist.

    Regel:
    - Zahlen ausserhalb 2000–2099 sind nie als Jahr mehrdeutig → False
      (also als PLZ behandeln/melden).
    - Zahlen 2000–2099: nur dann PLZ (→ False), wenn das Folge-Token ein
      Ort ist, dessen reale PLZ tatsaechlich im 2xxx-Bereich liegt
      (KNOWN_2XXX_ORTE). Sonst Jahr (→ True), z.B. "2022 Steuerausweis",
      "2024 Veranlagung" — und auch "2023 Bern" (Jahr + Bank-Sitz im
      Briefkopf; Berns PLZ ist 3xxx, eine Adresse "2023 Bern" existiert
      nicht).

    Args:
        plz_token: die 4-stellige Zahl als String (z.B. "2502").
        following_token: das unmittelbar folgende Token (Ortsname-Kandidat)
            oder None, wenn keines vorhanden ist.
    """
    if not plz_token.isdigit() or len(plz_token) != 4:
        return False
    if not (2000 <= int(plz_token) <= 2099):
        # 1xxx, 3xxx–9xxx: keine Jahres-Mehrdeutigkeit.
        return False
    # 2xxx: nur als PLZ werten, wenn der Folge-Ort real im 2xxx-Bereich liegt.
    if following_token and following_token.strip(".,;:") in KNOWN_2XXX_ORTE:
        return False
    return True
