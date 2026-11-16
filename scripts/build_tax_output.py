"""Erzeugt die ECHTE Steuer-Übertragungstabelle aus ``_results_full.json``.

Im Unterschied zu ``build_table.py`` (Debug-Extraktions-Sicht) produziert
dieser Generator zwei separate Outputs:

- ``steuer_uebertragung.md`` — 3-Sektionen-Tabelle für ZHprivateTax-Übertragung
  (auto · manuell prüfen · out-of-scope).
- ``nachweis_anhang.md`` — Quelle + Snippet pro Row, gruppiert nach Beleg.

Spalten der Übertragungstabelle:
``Steuerbereich | ZHprivateTax-Ziffer | Person/Rolle | Aussteller |
Beschreibung | Betrag CHF | Jahr/Periode | Quelle | Status``

Status:
- ``auto`` — Wert + Anker valid, kein Placeholder, kein ``manual_review:*``-Marker.
- ``manual_review`` — Wert vorhanden, aber Anker fehlt, Placeholder,
  ``manual_review:*``-Marker, Conditional ohne Wert (legitim leer), oder
  Plausibility-Fail im Beleg.
- ``oos`` — Ganzer Beleg ist out-of-scope.
"""
from __future__ import annotations

from decimal import Decimal
import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from extractors.steuer_zielmodell import (
    SPECS, get_spec, is_placeholder, run_plausibility,
)
from extractors.family import load_family
from extractors.numbers import parse_swiss_amount
from extractors.zielwerte import ZIELWERTE
from extractors.person_inference import _name_tokens

# Rollenmodell (User-Vorgabe, geschlossene Menge)
ROLE_ELTERNTEIL_1 = "elternteil_1"
ROLE_ELTERNTEIL_2 = "elternteil_2"
ROLE_KIND_1 = "kind_1"
ROLE_KIND_2 = "kind_2"
ROLE_FAMILIE = "familie"
ROLE_UNKNOWN = "unbekannt/manuell"

# Belegtypen, bei denen die Person/Rolle fachlich zwingend ist.
# Auto-Zeilen mit ROLE_UNKNOWN in diesen Belegtypen werden zu manual_review
# demotiert (Grader-Anforderung §1).
PERSON_REQUIRED_BELEGTYPEN: frozenset[str] = frozenset({
    "lohnausweis",
    "kk_praemienbescheinigung",
    "saeule_3a",
    "kinderbetreuung",
    "krankheitskosten",
})

# Familien-Constraints: pro Belegtyp die geschlossene Menge fachlich erlaubter
# Rollen. Begruendung (User-Domaenenwissen, locked): nur die zwei Erwachsenen
# haben Lohnausweise, Bankkonten und Saeule 3a; Kinder spielen nur bei
# Krankenkasse/Krankheitskosten eine eigenstaendige Rolle; Kinderbetreuung
# betrifft ausschliesslich Kinder. Belegtyp NICHT in der Map → keine
# Einschraenkung (Verhalten wie map_person_to_role).
ALLOWED_ROLES_FOR_BELEGTYP: dict[str, frozenset[str]] = {
    "lohnausweis": frozenset({ROLE_ELTERNTEIL_1, ROLE_ELTERNTEIL_2}),
    "saeule_3a": frozenset({ROLE_ELTERNTEIL_1, ROLE_ELTERNTEIL_2}),
    "bank_zinsausweis": frozenset({ROLE_ELTERNTEIL_1, ROLE_ELTERNTEIL_2}),
    "wertschriftenverzeichnis": frozenset({ROLE_ELTERNTEIL_1, ROLE_ELTERNTEIL_2}),
    "kk_praemienbescheinigung": frozenset({
        ROLE_ELTERNTEIL_1, ROLE_ELTERNTEIL_2, ROLE_KIND_1, ROLE_KIND_2, ROLE_FAMILIE,
    }),
    "krankheitskosten": frozenset({
        ROLE_ELTERNTEIL_1, ROLE_ELTERNTEIL_2, ROLE_KIND_1, ROLE_KIND_2, ROLE_FAMILIE,
    }),
    "kinderbetreuung": frozenset({ROLE_KIND_1, ROLE_KIND_2}),
}

# Mapping family.yaml-Role -> User-Rollenmodell
_FAMILY_ROLE_MAP = {
    "mann": ROLE_ELTERNTEIL_1,
    "frau": ROLE_ELTERNTEIL_2,
    "kind1": ROLE_KIND_1,
    "kind2": ROLE_KIND_2,
}


def _build_fantasy_first_name_map() -> dict[str, str]:
    """Mappe Fantasy-Vornamen (vom Anonymizer gesetzt) auf User-Rollen.

    Anonymizer-Konvention (siehe FANTASY_PERSONS_FULL in anonymize_belege.py):
    members[idx] der family.yaml → pool[idx] aus
    ["Hans Muster", "Maria Muster", "Lina Muster", "Tim Muster", ...].
    Für Muster-Familie also: Hans→mann, Maria→frau, Lina→kind1, Tim→kind2.
    """
    pool = ["Hans", "Maria", "Lina", "Tim", "Anna", "Peter"]
    fmly = load_family(ROOT / "family.yaml")
    out: dict[str, str] = {}
    if fmly is None:
        return out
    for idx, m in enumerate(fmly.members):
        if idx >= len(pool):
            break
        target_role = _FAMILY_ROLE_MAP.get(m.role, ROLE_UNKNOWN)
        out[pool[idx]] = target_role
    return out


_FANTASY_FIRSTNAME_TO_ROLE = _build_fantasy_first_name_map()


def _build_real_firstname_map() -> dict[str, set[str]]:
    """Mappe echte family.yaml-Vornamen (normalisiert) auf User-Rollen.

    Produktiver Pfad: Belege enthalten die echten Namen, keine Fantasy-Namen.
    Die Vornamen stammen aus dem lokalen, gitignorierten ``family.yaml`` und
    landen ausschliesslich im ebenfalls gitignorierten Output. Tokens werden
    via :func:`_name_tokens` normalisiert (Lower + Diakritika-Fold), damit
    auch "ANNA MUSTER"/"Muster Anna"-Schreibweisen aus PDFs matchen.
    """
    fmly = load_family(ROOT / "family.yaml")
    out: dict[str, set[str]] = {}
    if fmly is None:
        return out
    for m in fmly.members:
        target_role = _FAMILY_ROLE_MAP.get(m.role, ROLE_UNKNOWN)
        for tok in _name_tokens(m.first_name):
            out.setdefault(tok, set()).add(target_role)
    return out


_REAL_FIRSTNAME_TO_ROLE = _build_real_firstname_map()


def _collect_candidate_roles(person_value: str | None) -> set[str]:
    """Sammle die rohen Einzel-Rollen-Treffer eines Personen-Strings.

    Zwei Stufen (identisch zur Logik von :func:`map_person_to_role`):

    1. Fantasy-Vornamen (Hans/Maria/Lina/Tim/…) — Sample-Pfad nach
       Anonymisierung (case-sensitive Word-Boundary).
    2. Nur wenn Stufe 1 leer bleibt: echte family.yaml-Vornamen
       (normalisierte Tokens) — produktiver Pfad mit echten Namen.

    Liefert das Set der gefundenen Einzel-Rollen OHNE Reduktion auf
    ``familie``/``unbekannt``. None/leer/Manual-Review-Marker → leeres Set.
    """
    if not person_value or not isinstance(person_value, str):
        return set()
    if is_manual_review_marker(person_value):
        return set()
    found_roles: set[str] = set()
    for name, role in _FANTASY_FIRSTNAME_TO_ROLE.items():
        # Word-Boundary check (case-sensitive, Fantasy-Namen starten gross)
        if re.search(rf"\b{re.escape(name)}\b", person_value):
            found_roles.add(role)
    if not found_roles:
        # Produktiver Pfad: echte Vornamen aus family.yaml (normalisiert).
        for tok in _name_tokens(person_value):
            if tok in _REAL_FIRSTNAME_TO_ROLE:
                found_roles.update(_REAL_FIRSTNAME_TO_ROLE[tok])
    return found_roles


def map_person_to_role(person_value: str | None) -> str:
    """Mappe extrahierten Personen-String auf eine erlaubte Rolle.

    Zwei Stufen (siehe :func:`_collect_candidate_roles`): Fantasy-Vornamen
    zuerst, sonst echte family.yaml-Vornamen.

    Mehrere Treffer → ``familie``. Kein Treffer → ``unbekannt/manuell``.
    """
    found_roles = _collect_candidate_roles(person_value)
    if not found_roles:
        return ROLE_UNKNOWN
    if len(found_roles) == 1:
        return next(iter(found_roles))
    return ROLE_FAMILIE


def resolve_role_constrained(person_value: str | None, belegtyp: str | None) -> str:
    """Bevorzugte Rollen-Aufloesung mit Familien-Constraints pro Belegtyp.

    Nur die in :data:`ALLOWED_ROLES_FOR_BELEGTYP` fuer den Belegtyp erlaubten
    Rollen kommen infrage. Dadurch loesen sich mehrdeutige Personen-Strings
    korrekt auf (ein Lohnausweis kann kein Kind betreffen) und eine unerlaubte
    Rolle wird NIE ausgegeben.

    - Belegtyp NICHT in der Map (oder None) → keine Einschraenkung, Verhalten
      identisch zu :func:`map_person_to_role`.
    - Genau eine erlaubte Rolle uebrig → diese Rolle (auch wenn der rohe String
      mehrdeutig war).
    - Keine erlaubte Rolle uebrig → ``unbekannt/manuell``.
    - Mehrere erlaubte Rollen uebrig → ``familie`` falls ``familie`` im
      Belegtyp erlaubt ist (KK-Kontext), sonst ``unbekannt/manuell``.
    """
    allowed = ALLOWED_ROLES_FOR_BELEGTYP.get(belegtyp) if belegtyp else None
    if allowed is None:
        return map_person_to_role(person_value)
    candidates = _collect_candidate_roles(person_value)
    constrained = candidates & allowed
    if len(constrained) == 1:
        return next(iter(constrained))
    if len(constrained) == 0:
        return ROLE_UNKNOWN
    # >1 erlaubte Rolle uebrig → nur familie, wenn dort fachlich erlaubt.
    if ROLE_FAMILIE in allowed:
        return ROLE_FAMILIE
    return ROLE_UNKNOWN


def resolve_kinderbetreuung_kind(entry: dict) -> str:
    """Bestimme das Kind eines Kinderbetreuungs-Entrys, robust ueber Felder.

    Zuerst das primaere Kind-Feld (``spec.person_field`` bzw. ``kind_name``)
    via :func:`resolve_role_constrained`. Loest dieses eindeutig auf, wird es
    zurueckgegeben. Sonst (oder wenn das primaere Feld unbestimmt bleibt)
    Fallback: ueber alle Felder iterieren, deren Feldname (lower) "person",
    "kind" oder "name" enthaelt; jeden Wert constrained aufloesen und die
    nicht-UNKNOWN Treffer sammeln. Genau EIN eindeutiges Kind ueber alle
    Felder → dieses; 0 oder mehrere verschiedene → ``unbekannt/manuell``.

    Die ueber alle Felder gesammelten Treffer haben Vorrang vor dem reinen
    Primaerfeld-Ergebnis: nennen zwei Felder verschiedene Kinder, ist die
    Zuordnung nicht eindeutig und faellt auf ``unbekannt/manuell``.
    """
    fields = entry.get("fields", []) or []
    fmap = {f.get("feld"): f.get("value") for f in fields}

    spec = get_spec("kinderbetreuung")
    primary_field = (spec.person_field if spec else None) or "kind_name"

    # Alle relevanten Felder sammeln (Primaerfeld inklusive), damit Konflikte
    # zwischen Feldern erkannt werden und nicht durch ein eindeutiges
    # Primaerfeld verdeckt werden.
    found: set[str] = set()
    for f in fields:
        fname = (f.get("feld") or "").lower()
        if (
            fname == (primary_field or "").lower()
            or "person" in fname
            or "kind" in fname
            or "name" in fname
        ):
            role = resolve_role_constrained(f.get("value"), "kinderbetreuung")
            if role != ROLE_UNKNOWN:
                found.add(role)
    if len(found) == 1:
        return next(iter(found))
    if len(found) > 1:
        return ROLE_UNKNOWN

    # Kein relevantes Feld traf zu — letzter Versuch ueber das Primaerfeld
    # allein (deckt Primaerfeld-Namen ab, die keinen der Keywords enthalten).
    return resolve_role_constrained(fmap.get(primary_field), "kinderbetreuung")


# Adress-/Nicht-Namens-Fragmente, die ab ihrem Auftreten abgeschnitten werden.
_ADDRESS_CUT_RE = re.compile(
    r"\b(?:Musterstrasse|Maneggplatz|Musterhausen|Musterort|Postfach|"
    r"[A-ZÀ-Þ][\wÀ-ÿ]*strasse|[A-ZÀ-Þ][\wÀ-ÿ]*platz|\d{3,})\b",
    flags=re.IGNORECASE,
)
_SALUTATION_RE = re.compile(r"^(?:Herr|Frau|Familie|Fam\.?)\s+", flags=re.IGNORECASE)

# Label-Präfixe, die das LLM mit ins Personen-Feld zieht ("Kundennummer: …").
# Werden VOR dem Adress-Cut entfernt — sonst bleibt nach dem Cut der nackte
# Label-Text als vermeintlicher Name stehen.
_LABEL_PREFIX_RE = re.compile(
    r"^(?:Kunden(?:nummer|-?Nr\.?)|Vertrags(?:nummer|-?Nr\.?)|"
    r"Police(?:n-?Nr\.?)?|Referenz(?:-?Nr\.?)?|Versicherten-?Nr\.?)\s*:?\s*",
    flags=re.IGNORECASE,
)


def clean_display_name(raw: str | None) -> str | None:
    """Bereinigt einen extrahierten Personen-String zu einem reinen Namen.

    Entfernt Geburtsdatum-/Listen-Anhänge (clean_person_name), Label-Präfixe
    (Kundennummer:, Vertrags-Nr. …), schneidet Adressfragmente ab (Strasse,
    PLZ, Ort) und strippt Anreden (Herr/Frau).
    In anonymisierten Samples sind dies Fantasy-Namen (Hans Muster etc.) —
    kein PII-Leak (echte Namen stünden in family.yaml).
    """
    from extractors.name_cleanup import clean_person_name
    if not raw or not isinstance(raw, str):
        return None
    if is_manual_review_marker(raw):
        return None
    s = clean_person_name(raw)
    # Klammer-Anhänge mit Nummern/Labels entfernen: "Muster, Lina
    # (Kundennummer: 954-95-594)" → "Muster, Lina". Auch unvollständig
    # geschlossene Klammern (Truncation) werden erfasst.
    s = re.sub(
        r"\s*\([^)]*(?:\d|Kunden|Vertrag|Police|Referenz)[^)]*\)?",
        " ",
        s,
        flags=re.IGNORECASE,
    )
    while True:
        s2 = _LABEL_PREFIX_RE.sub("", s.strip())
        if s2 == s:
            break
        s = s2
    m = _ADDRESS_CUT_RE.search(s)
    if m:
        s = s[:m.start()]
    s = _SALUTATION_RE.sub("", s.strip())
    s = s.strip(" ,;.:")
    # Ohne mindestens ein namensartiges Token (>=2 Buchstaben) ist es kein Name.
    if not re.search(r"[A-Za-zÀ-ÿ]{2}", s):
        return None
    return s or None


def canonical_name_for_role(role: str) -> str | None:
    """Kanonischer Vorname für eine Rolle (Fantasy-Pool bzw. family.yaml).

    Sample-Pfad: invertierte Fantasy-Map (Hans/Maria/Lina/Tim). Produktiver
    Pfad: invertierte Real-Namen-Map (Vornamen bleiben lokal/gitignored).
    """
    for name, r in _FANTASY_FIRSTNAME_TO_ROLE.items():
        if r == role:
            return name
    for tok, roles in _REAL_FIRSTNAME_TO_ROLE.items():
        if roles == {role}:
            return tok.capitalize()
    return None


def format_person_display(person_raw: str | None, belegtyp: str | None = None) -> str:
    """Anzeige-Wert für die Person/Rolle-Spalte: Name, optional + Rolle.

    Primär der (anonymisierte) Name; die Familienrolle wird als Zusatz in
    Klammern angehängt, wenn eindeutig bestimmbar. Kein Name → Rollen-Label.

    ``belegtyp`` (optional): wird durchgereicht an
    :func:`resolve_role_constrained`, damit die Rolle dieselben Familien-
    Constraints respektiert wie die übrigen Pfade (MD↔xlsx-Konsistenz,
    Finding 3). ``None`` = altes Verhalten (resolve_role_constrained fällt bei
    ``belegtyp=None`` auf :func:`map_person_to_role` zurück).
    """
    name = clean_display_name(person_raw)
    role = resolve_role_constrained(person_raw, belegtyp)
    if name and role not in (ROLE_UNKNOWN, ROLE_FAMILIE):
        return f"{name} ({role})"
    if name:
        return name
    # Kein brauchbarer Name, aber eindeutige Rolle → kanonischer Vorname
    # statt Label-Garbage ("Kundennummer: …").
    if role not in (ROLE_UNKNOWN, ROLE_FAMILIE):
        canon = canonical_name_for_role(role)
        if canon:
            return f"{canon} ({role})"
    return role


def _normalize_amount(s: str | None) -> str:
    if s is None:
        return ""
    return s.replace("'", "").replace(" ", "").replace(",", ".")


def detect_dupe_amount_misuse(entry: dict) -> tuple[bool, str]:
    """True wenn Bestand==Ertrag im selben Beleg (Extraktions-Verdacht).

    Returns (suspicious, marker_reason).
    """
    fmap = {f["feld"]: f.get("value") for f in entry.get("fields", [])}
    bestand = fmap.get("bestand_3112") or fmap.get("vermoegensstand_3112")
    ertrag = fmap.get("bruttoertrag") or fmap.get("bruttoertrag_total")
    if not bestand or not ertrag:
        return False, ""
    if not isinstance(bestand, str) or not isinstance(ertrag, str):
        return False, ""
    if bestand.startswith("manual_review") or ertrag.startswith("manual_review"):
        return False, ""
    nb, ne = _normalize_amount(bestand), _normalize_amount(ertrag)
    if nb == ne and nb not in ("0", "0.0", "0.00", ""):
        return True, "manual_review:bestand_eq_ertrag_suspicious"
    return False, ""


# Mapping: belegtyp -> Liste von Row-Definitionen
# Jede Row-Definition: (field_name, beschreibung, ziffer_suffix_optional)
#
# ``ziffer_suffix`` wird nur gesetzt wenn er sich von ``spec.zhprivatetax_ziffer``
# unterscheidet (z.B. VRS-Zeile bei Wertschriften zeigt "VRS 35%" als Detail).
# Umbenannte Zielwerte — EINE Tabelle, in extractors/korrekturen.py.
#
# Hier stand eine zweite Kopie. Als die erste um fuenf Eintraege wuchs, blieb
# diese zurueck: der Tabellenbau fand die gespeicherten Korrekturen nicht
# mehr, sechs Personenzuordnungen verschwanden, und es sah wieder nach
# Datenverlust aus. Zwei Tabellen fuer dieselbe Sache sind zwei
# Gelegenheiten, auseinanderzulaufen (260904-rmx).
from extractors.korrekturen import ALTE_BEZEICHNUNGEN


TRANSFER_MAP: dict[str, list[tuple[str, str, str | None]]] = {
    "lohnausweis": [
        # Zielwert Nettolohn Ziffer 11 (steuerrelevanter Hauptwert).
        # Bruttolohn/AHV/BVG sind Kontrollfelder, keine primären Steuerzeilen.
        # Quellensteuer (Ziffer 12) NICHT enthalten: Familie ist ordentlich
        # veranlagt (Schweizer/C-Bewilligung), nicht quellenbesteuert.
        ("nettolohn_pos11", "Nettolohn (Pos. 11)", None),
    ],
    "bank_zinsausweis": [
        ("vermoegensstand_3112", "Vermögensstand 31.12.", None),
        # Multi-Konto: zusätzliche Konten aus demselben Dokument
        ("vermoegensstand_3112_konto2", "Vermögensstand 31.12. (Konto 2)", None),
        ("vermoegensstand_3112_konto3", "Vermögensstand 31.12. (Konto 3)", None),
        ("bruttoertrag", "Bruttoertrag ohne Verrechnungssteuer", None),
        ("verrechnungssteuer", "Bruttoertrag mit Verrechnungssteuer", None),
    ],
    "hypothek_zinsbestaetigung": [
        ("schuldzinsen", "Schuldzinsen Hypothek", None),
        ("schuldsaldo_3112", "Hypothekarschuld 31.12.", None),
    ],
    "kk_praemienbescheinigung": [
        ("praemie_kvg_total", "Prämie KVG (Grundversicherung)", None),
        ("praemie_vvg_total", "Prämie VVG (Zusatzversicherung)", None),
        # Prämie Total nur wenn im Dokument KVG/VVG NICHT getrennt ausgewiesen
        ("praemie_total", "Prämie Total (nicht aufgeteilt)", None),
        # Selbstgetragene Kosten (KK-C "Total von Ihnen bezahlte Kosten")
        ("selbstgetragene_kosten",
         "Selbst getragene Krankheits- und Unfallkosten", None),
    ],
    "saeule_3a": [
        ("einzahlung_betrag", "Einzahlung Säule 3a", None),
    ],
    "wertschriftenverzeichnis": [
        ("bestand_3112", "Vermögensstand 31.12.", None),
        ("bruttoertrag_total", "Bruttoertrag ohne Verrechnungssteuer", None),
        ("verrechnungssteuer_total", "Bruttoertrag mit Verrechnungssteuer", None),
    ],
    "kinderbetreuung": [
        ("betrag", "Kosten Kinderbetreuung", None),
    ],
    "krankheitskosten": [
        # Nur der Selbstanteil. Die beiden Praemien-Eintraege, die hier
        # standen, zeigten ins Leere: KrankheitskostenRaw kennt keine
        # Praemienfelder. Kombinierte Dokumente (Praemie + Kosten) werden als
        # kk_praemienbescheinigung klassifiziert und dort vollstaendig
        # abgebildet (260904-rmx).
        ("betrag", "Selbst getragene Krankheits- und Unfallkosten", None),
    ],
}


# Steuerbereich-Mapping: Belegtyp -> kurzer Steuerbereich-Label
STEUERBEREICH: dict[str, str] = {
    "lohnausweis": "Einkommen",
    "bank_zinsausweis": "Vermögen & Erträge",
    "wertschriftenverzeichnis": "Vermögen & Erträge",
    "saeule_3a": "Abzüge",
    "kk_praemienbescheinigung": "Abzüge",
    "kinderbetreuung": "Abzüge",
    # Der Zins ist ein Abzug, der Saldo eine Schuld — die Zeilen tragen ihre
    # Ziffer, und die entscheidet. Als Bereich reicht „Abzüge & Schulden".
    "hypothek_zinsbestaetigung": "Abzüge & Schulden",
    "krankheitskosten": "Abzüge",
    "spenden": "Abzüge",
    "parteibeitrag": "Abzüge",
}


# Ein Namenssystem, nicht zwei.
#
# TRANSFER_MAP trug eigene Beschriftungen ("Vermögensstand 31.12."), der
# Feldvertrag andere ("Saldo 31.12.") — fuer dasselbe Feld. Jede Pruefung,
# die beide vergleicht, war blind: die Vollstaendigkeitspruefung fand keine
# Praemie, die Feld-Matrix meldete "12 erwartet, 0 geliefert", und
# Korrekturen hingen an der einen, Regeln an der anderen Bezeichnung.
#
# CLAUDE.md sagt seit jeher, der Feldvertrag sei die einzige Quelle. Hier
# wird das durchgesetzt, statt es zu behaupten: wo ein Zielwert existiert,
# gewinnt seine Bezeichnung. Beschriftungen ohne Zielwert (Multi-Konto,
# Kontrollfelder) bleiben unveraendert (260904-rmx).
# "<grundfeld>_kontoN" — ein weiteres Konto desselben Belegs.
_KONTO_FELD_RE = re.compile(r"^(.*)_konto(\d+)$")


def _vertrag_gewinnt() -> None:
    from extractors.zielwerte import ZIELWERTE

    def bezeichnung(vertrag: dict[str, str], feld: str, label: str) -> str:
        if feld in vertrag:
            return vertrag[feld]
        # Zusaetzliche Konten desselben Belegs sind kein anderer Zielwert,
        # sondern eine weitere POSITION desselben.
        #
        # Sie trugen eine eigene Bezeichnung ("Vermögensstand 31.12.
        # (Konto 2)"), weil der Schluessel frueher nur eine Position je
        # Zielwert fassen konnte. In der Tabelle standen dadurch zwei Namen
        # fuer dieselbe Sache nebeneinander — vom Nutzer bemerkt, zu Recht.
        # Seit es Positionsnummern gibt, erben sie den Namen des Grundfelds
        # (260923-dua).
        treffer = _KONTO_FELD_RE.match(feld)
        if treffer and treffer.group(1) in vertrag:
            return vertrag[treffer.group(1)]
        return label

    for belegtyp, eintraege in TRANSFER_MAP.items():
        vertrag = {z.feld: z.bezeichnung for z in ZIELWERTE.get(belegtyp, [])}
        TRANSFER_MAP[belegtyp] = [
            (feld, bezeichnung(vertrag, feld, label), extra)
            for feld, label, extra in eintraege
        ]


_vertrag_gewinnt()


# E7 Parteibeitrag: Spenden-Belege mit Mitgliederbeitrag-Charakter an eine
# Partei sind in ZH ein eigener Abzug (Parteispenden/-beiträge), nicht eine
# gemeinnützige Spende. Generischer "Partei"-Anker plus bekannte Kürzel.
_PARTEI_KEYWORD_RE = re.compile(
    r"\bPartei\b|\b(?:GLP|SP|SVP|FDP|EVP|EDU|BDP)\b|\bDie\s+Mitte\b"
    r"|\bGr[üu]ne\b|\bGr[üu]nliberale\b",
    re.IGNORECASE,
)
_MITGLIEDERBEITRAG_RE = re.compile(r"Mitglieder?beitrag", re.IGNORECASE)


def _entry_text_blob(entry: dict) -> str:
    """Sammelt alle durchsuchbaren Strings eines Entries (Feldwerte + Snippets)."""
    parts: list[str] = [str(entry.get("pdf_name", ""))]
    for f in entry.get("fields", []):
        parts.append(str(f.get("value", "") or ""))
        parts.append(str(f.get("snippet", "") or ""))
    return " ".join(parts)


def is_parteibeitrag(entry: dict) -> bool:
    """True, wenn ein spenden-Entry ein Partei-Mitgliederbeitrag ist (E7).

    Bedingung: Belegtyp ``spenden`` UND der Text enthält "Mitgliederbeitrag"
    UND einen Partei-Kontext (generisch "Partei" oder bekanntes Parteikürzel).
    """
    if entry.get("belegtyp") != "spenden":
        return False
    blob = _entry_text_blob(entry)
    return bool(_MITGLIEDERBEITRAG_RE.search(blob) and _PARTEI_KEYWORD_RE.search(blob))


PLACEHOLDER_VALUES = {"nicht angegeben", "not specified", "n/a", "null", ""}


# Marker für eine gepflegte, verifizierte Feld-Abwesenheit (field_exceptions):
# das Feld kommt by-design NICHT im Beleg vor. Erscheint nie als roher String
# in der Übertragungstabelle (Betrag/Beschreibung).
NOT_IN_BELEG_BY_DESIGN = "not_in_beleg_by_design"


def is_manual_review_marker(value: str | None) -> bool:
    """True wenn der Wert ein ``manual_review:*``-Marker ist."""
    return isinstance(value, str) and value.startswith("manual_review:")


def is_by_design(value: str | None) -> bool:
    """True wenn der Wert der by-design-Abwesenheits-Marker ist.

    Zentrale Quelle der Wahrheit — von ``build_steueraufstellung`` und
    ``build_basis_tabelle`` importiert, damit ``not_in_beleg_by_design`` nirgends
    roh gerendert wird und keinen echten Zweitwert verdeckt (Finding 4).
    """
    return value == NOT_IN_BELEG_BY_DESIGN


def display_value_or_none(value: str | None) -> str | None:
    """Anzeigewert oder ``None`` (Caller rendert ``None`` als '—').

    ``None`` bei leer/Placeholder/manual_review-Marker/by-design, sonst der
    Wert. Schlanker Wrapper um :func:`fmt_display_value`, der zusätzlich
    manual_review-Marker auf ``None`` abbildet (statt '⚠ …'), weil die xlsx-
    und Basis-Pfade keinen Markertext in Wert-Zellen wollen.
    """
    if not is_real_value(value):
        return None
    if is_by_design(value):
        return None
    return value


def is_real_value(value: str | None) -> bool:
    """True wenn Wert vorhanden, kein Placeholder, kein manual_review-Marker."""
    if value is None or value == "":
        return False
    if is_placeholder(value):
        return False
    if is_manual_review_marker(value):
        return False
    return True


_ROH_RE = re.compile(r"^-?\d+(\.\d+)?$")


def roher_betrag(value: str | None) -> str:
    """Betrag ohne Tausendertrenner, Punkt als Dezimaltrenner.

    Die Dokumente schreiben durcheinander: ``1'976.05``, ``1 976.05``,
    ``1976,05``, ``1976.00``. Ins Steuerformular gehört davon nur die nackte
    Zahl — alles andere muss beim Übertragen von Hand weggeputzt werden.

    Nicht-Zahlen (Marker, Freitext) bleiben unverändert; hier wird formatiert,
    nicht interpretiert.
    """
    if value is None:
        return ""
    t = str(value).strip()
    t = t.replace("'", "").replace("’", "").replace(" ", "")
    t = "".join(t.split())
    # Komma als Dezimaltrenner ("1976,05"), sonst als Tausendertrenner.
    if re.search(r"\d,\d{1,2}$", t):
        t = t[::-1].replace(",", ".", 1)[::-1]
    t = t.replace(",", "")
    return t if _ROH_RE.match(t) else str(value)


def fmt_amount(value: str | None) -> str:
    """Betrag für die Übertragungstabelle. Leere Werte → '—'.

    Bewusst **unformatiert**: die Tabelle ist zum Abtippen beziehungsweise
    Kopieren ins Formular da, nicht zum Lesen. Ein Apostroph darin ist genau
    ein Handgriff zu viel, mal 50 Positionen.
    """
    # by-design abwesendes Feld ist kein Betrag → '—' (kein roher Marker-String).
    if value == NOT_IN_BELEG_BY_DESIGN:
        return "—"
    if not is_real_value(value):
        return "—"
    return roher_betrag(value).replace("|", "\\|")


def fmt_display_value(value: str | None) -> str | None:
    """Sanitisiert User-sichtbaren Wert: manual_review:* → '⚠ <grund>',
    Placeholder/leer → None (Caller rendert als '—')."""
    if value is None or value == "":
        return None
    # by-design abwesendes Feld nie als roher String anzeigen.
    if value == NOT_IN_BELEG_BY_DESIGN:
        return None
    if is_manual_review_marker(value):
        return f"⚠ {value.removeprefix('manual_review:')}"
    if is_placeholder(value):
        return None
    return value


def md_escape(s: str | None) -> str:
    if s is None:
        return "—"
    s = s.replace("|", "\\|").replace("\n", " ")
    return s if s else "—"


def truncate(s: str, n: int = 60) -> str:
    if not s:
        return ""
    s = s.replace("\n", " ").replace("|", "\\|")
    return s if len(s) <= n else s[: n - 1] + "…"


def classify_row_status(
    field: dict | None,
    has_plausibility_fail: bool,
    is_conditional: bool,
) -> str:
    """Klassifiziert eine Row als 'auto' / 'manual_review' / '—' (fehlend)."""
    if field is None:
        return "—" if is_conditional else "manual_review"
    value = field.get("value")
    # Bewusste, verifizierte Feld-Abwesenheit (field_exceptions): Zeile
    # weglassen — der Wert kommt by-design nicht im Beleg vor und darf weder als
    # roher String noch als manual_review erscheinen.
    if value == NOT_IN_BELEG_BY_DESIGN:
        return "—"
    # Conditional-Feld mit "Abwesenheits"-Marker (Wert nicht im Beleg) → Zeile
    # weglassen statt manual_review. DoD: "Keine Quellensteuer-Zeile, wenn
    # Ziffer 12 leer ist." Gilt für value_not_in_document / vrs_not_reported.
    _ABSENCE_MARKERS = ("value_not_in_document", "vrs_not_reported",
                        "bruttoertrag_transaction_line")
    if is_conditional and is_manual_review_marker(value):
        if any(m in str(value) for m in _ABSENCE_MARKERS):
            return "—"
    if is_manual_review_marker(value):
        return "manual_review"
    if not is_real_value(value):
        # Conditional ohne Wert ist legitim leer
        return "—" if is_conditional else "manual_review"
    if not field.get("anchor_valid", False):
        return "manual_review"
    if has_plausibility_fail:
        return "manual_review"
    return "auto"


def fmt_quelle(pdf_name: str, page: int | None, inference_source: str | None = None) -> str:
    """Quelle als 'pdf_name S.X' oder 'pdf_name (text-regex)' für Regex-Felder."""
    short = truncate(pdf_name, 50)
    if page is not None:
        return f"`{short}` S.{page}"
    if inference_source and inference_source.startswith("regex:"):
        return f"`{short}` (text-regex)"
    if inference_source and "string_occurrence" in inference_source:
        return f"`{short}` (text-match)"
    return f"`{short}`"


def build_rows(entry: dict) -> list[dict]:
    """Wandelt einen _results_full-Entry in 1..N Übertragungs-Rows um."""
    belegtyp = entry.get("belegtyp", "unknown")
    spec = get_spec(belegtyp)
    transfers = TRANSFER_MAP.get(belegtyp, [])

    # E7 Parteibeitrag: ein spenden-Beleg mit Mitgliederbeitrag-Charakter an eine
    # Partei wird als eigene Kategorie "parteibeitrag" gerendert (ZH-Parteispenden-
    # Abzug), nicht als gemeinnützige Spende.
    if is_parteibeitrag(entry):
        fmap = {f["feld"]: f for f in entry.get("fields", [])}
        betrag_f = fmap.get("betrag", {})
        empf_f = fmap.get("empfaenger", {})
        return [{
            "steuerbereich": STEUERBEREICH.get("parteibeitrag", "Abzüge"),
            "ziffer": "Parteibeiträge/-spenden",
            "person": "familie",
            "aussteller": empf_f.get("value"),
            "beschreibung": "Parteimitgliederbeitrag",
            "betrag": betrag_f.get("value"),
            "jahr": fmap.get("jahr", {}).get("value"),
            "pdf_name": entry.get("pdf_name", "?"),
            "page": betrag_f.get("page"),
            "bbox": betrag_f.get("bbox"),
            "snippet": betrag_f.get("snippet", ""),
            "status": "manual_review",
            "field_name": "betrag",
            "plaus_errs": [],
            "manual_review_marker": "parteibeitrag_pruefen",
        }]

    if spec is None or not transfers:
        # Belegtyp klassifiziert, aber kein Transfer-Mapping definiert
        # (z.B. vermoegensverwaltungskosten, corporate_action_einzeltransaktion).
        # Eine Manual-Review-Row erzeugen, damit der Beleg nicht stillschweigend
        # verloren geht.
        return [{
            "steuerbereich": "—",
            "ziffer": "—",
            "person": None,
            "aussteller": None,
            "beschreibung": f"Belegtyp `{belegtyp}` — kein Transfer-Mapping definiert",
            "betrag": None,
            "jahr": None,
            "pdf_name": entry.get("pdf_name", "?"),
            "page": None,
            "bbox": None,
            "snippet": "",
            "status": "manual_review",
            "field_name": "—",
            "plaus_errs": [],
            "manual_review_marker": None,
        }]

    fmap = {f["feld"]: f for f in entry.get("fields", [])}
    values = {k: v.get("value", "") for k, v in fmap.items()}
    # VRS-Label-Pflicht (E2/E4): den Anker-Snippet der VRS-Felder in den
    # values-Dict legen, damit rule_verrechnungssteuer_label_pflicht prüfen kann,
    # ob ein extrahierter VRS-Wert tatsächlich Label-Nähe zur Verrechnungssteuer-
    # Spalte hat (statt aus einer Ertragsspalte gegriffen zu sein).
    for _vfield in ("verrechnungssteuer", "verrechnungssteuer_total"):
        if _vfield in fmap:
            values[f"{_vfield}_snippet"] = fmap[_vfield].get("snippet", "") or ""
    plaus_errs = run_plausibility(belegtyp, values, skip_arithmetic=True)
    has_plaus_fail = bool(plaus_errs)

    cond_set = set(spec.conditional_field_names())

    aussteller_raw = fmap.get(spec.aussteller_field or "", {}).get("value") if spec.aussteller_field else None
    person_raw = fmap.get(spec.person_field or "", {}).get("value") if spec.person_field else None
    datum = fmap.get(spec.datum_field or "", {}).get("value") if spec.datum_field else None

    # Person: anonymisierter Name (+ Rolle als Zusatz). Rolle separat für die
    # MR-Kaskade-Logik (person_unknown_in_sensitive).
    person_role = resolve_role_constrained(person_raw, belegtyp)
    person = format_person_display(person_raw, belegtyp)

    # Aussteller: nur den Marker-Stamm zeigen ("BANK-A", "BROKER-A", "INST-K").
    # LLM extrahiert oft Adress-/MWST-Fragmente mit ("BROKER-A, CHE-489 MWST").
    # Privacy + Lesbarkeit → harten Marker isolieren.
    aussteller = aussteller_raw
    aussteller_address_only = False
    if isinstance(aussteller, str) and aussteller:
        # Manual-Review-Marker als solche behalten (werden später im
        # context_has_mr_marker-Pfad in 'manual_review' Status verschoben)
        if not is_manual_review_marker(aussteller):
            m = re.match(
                r"^(BANK|BROKER|KK|STIFTUNG_3A|ARBEITGEBER|FIRMA|ANBIETER|INSTITUT|LEISTUNGSERBRINGER)-([A-Z][A-Z0-9_]*)",
                aussteller,
            )
            if m:
                aussteller = f"{m.group(1)}-{m.group(2)}"
            else:
                # Reine Adresse statt Firma extrahiert ("Musterstrasse 87,
                # 8000 Musterhausen")? Dann nichts anzeigen und die Zeilen
                # via MR-Kaskade demotieren (aussteller_unbestimmt).
                madr = _ADDRESS_CUT_RE.search(aussteller)
                if madr and not aussteller[:madr.start()].strip(" ,;."):
                    aussteller = None
                    aussteller_address_only = True

    # Wenn AUSSTELLER (Pflicht-Anker für Zuordnung) ein MR-Marker ist,
    # sind alle Rows betroffen. Bei Person ODER Datum nur dann, wenn die
    # Row dieses Feld als Pflichtanker hat (Lohn = Person, Säule3a = Person).
    # Person/Datum-MR allein KASKADIERT NICHT auf die Beträge — Beträge mit
    # eigenem Anker dürfen Auto bleiben.
    context_has_mr_marker = is_manual_review_marker(aussteller_raw) or aussteller_address_only

    # Bestand/Ertrag-Duplikat im Beleg → ALLE relevanten Rows als manual_review
    dupe_suspicious, dupe_marker = detect_dupe_amount_misuse(entry)
    if dupe_suspicious and not plaus_errs:
        plaus_errs = [f"Extraktions-Verdacht: bestand_3112 == bruttoertrag im "
                      f"selben Beleg ({dupe_marker})"]
        has_plaus_fail = True

    # Person/Rolle in personensensitiven Belegtypen: wenn weder Name noch Rolle
    # bestimmbar → MR-Cascade. (Name vorhanden reicht; Rolle ist Zusatz.)
    person_unknown_in_sensitive = (
        person_role == ROLE_UNKNOWN
        and clean_display_name(person_raw) is None
        and belegtyp in PERSON_REQUIRED_BELEGTYPEN
    )

    # Multi-Konto-Felder (konto2, konto3 etc.) sind immer optional
    _MULTI_KONTO_OPTIONAL = re.compile(r"vermoegensstand_3112_konto\d+")
    # KK-Prämien-/Kosten-Felder: ein Beleg weist nur die tatsächlich vorhandenen
    # Komponenten aus (mal nur KVG, mal nur VVG, mal nur Total). Fehlende/leere
    # Komponenten werden weggelassen (Zeile droppen), nicht als MR gezeigt.
    _DROP_IF_EMPTY = {
        "praemie_kvg_total", "praemie_vvg_total", "praemie_total",
        "selbstgetragene_kosten",
    }

    rows: list[dict] = []
    for fname, beschreibung, suffix in transfers:
        f = fmap.get(fname)
        is_conditional = (
            fname in cond_set
            or bool(_MULTI_KONTO_OPTIONAL.fullmatch(fname))
            or fname in _DROP_IF_EMPTY
        )
        status = classify_row_status(f, has_plaus_fail, is_conditional)
        mr_reason: str | None = None
        if status == "auto" and context_has_mr_marker:
            status = "manual_review"
            mr_reason = "aussteller_unbestimmt"
        if status == "auto" and person_unknown_in_sensitive:
            status = "manual_review"
            mr_reason = "person_rolle_unbestimmt"
        if status == "—":
            # Conditional ohne Wert — wir lassen die Row weg (kein Tax-Eintrag nötig).
            continue
        value = f.get("value") if f else None
        page = f.get("page") if f else None
        bbox = f.get("bbox") if f else None
        snippet = f.get("snippet", "") if f else ""
        inference_source = f.get("inference_source") if f else None
        # Berechnete (derived) Werte: bbox zeigt aufs Subtotal, nicht auf einen
        # Original-VRS-Wert. Markierung in den Nachweis-Anhang durchreichen.
        derived = bool(f.get("derived")) if f else False
        derivation = f.get("derivation") if f else None
        # Herkunft und Plausi-Hinweis speisen die Konfidenzstufe.
        herkunft = f.get("herkunft", "modell") if f else "modell"
        plaus_hinweis = f.get("plaus_hinweis") if f else None

        # Ziffer pro ZIELWERT, nicht pro Belegtyp (260904-rmx): ein
        # Krankenkassen-Auszug speist zwei verschiedene Stellen der
        # Steuererklaerung — die Praemien Ziffer 15, die selbst getragenen
        # Behandlungskosten Ziffer 22.1. Eine Ziffer pro Dokument waere
        # zwangslaeufig fuer eine der beiden Zeilen falsch.
        # Mehrkonto-Felder erben die Ziffer des Basisfelds.
        _basis = re.sub(r"_konto\d+$", "", fname)
        _zw = next((z for z in ZIELWERTE.get(belegtyp, ())
                    if z.feld in (fname, _basis)), None)
        ziffer = _zw.ziffer if _zw else spec.zhprivatetax_ziffer
        if suffix:
            ziffer = f"{ziffer} — {suffix}"

        rows.append({
            "steuerbereich": STEUERBEREICH.get(belegtyp, "—"),
            # Belegtyp mitgeben: die Review-Oberflaeche gruppiert danach
            # (alle Lohnausweise zusammen, alle Bankbelege zusammen).
            "belegtyp": belegtyp,
            "ziffer": ziffer,
            "herkunft": herkunft,
            "plaus_hinweis": plaus_hinweis,
            "anchor_valid": bool(f.get("anchor_valid")) if f else False,
            "person": person,
            # Die aufgeloeste Rolle mitfuehren. ``person`` ist ein Anzeigetext
            # („Vorname Nachname (elternteil_1)"), und bei Rolle „familie"
            # oder unbekannter Rolle steht dort nur der Name — aus ihm laesst
            # sich die Rolle dann nicht mehr zurueckgewinnen. Wer sie braucht,
            # soll sie nicht aus einem Anzeigetext parsen muessen
            # (260904-rmx).
            "person_rolle": person_role,
            "aussteller": aussteller,
            "beschreibung": beschreibung,
            # Welches Konto — die Information stand frueher in der
            # Bezeichnung ("… (Konto 2)"). Seit alle Konten denselben
            # Zielwert tragen, gehoert sie in die Unterscheidung, sonst waeren
            # drei Zeilen "Saldo 31.12." nicht auseinanderzuhalten
            # (260923-dua).
            "unterscheidung": (
                f"Konto {_KONTO_FELD_RE.match(fname).group(2)}"
                if _KONTO_FELD_RE.match(fname) else ""),
            "betrag": value if not is_manual_review_marker(value) else None,
            "jahr": datum,
            "pdf_name": entry.get("pdf_name", "?"),
            "page": page,
            "bbox": bbox,
            "snippet": snippet,
            "status": status,
            "field_name": fname,
            "plaus_errs": plaus_errs,
            "manual_review_marker": value if is_manual_review_marker(value) else mr_reason,
            "inference_source": inference_source,
            "derived": derived,
            "derivation": derivation,
        })
    return rows


# ---------------------------------------------------------------------------
# Ausgabe-Hygiene (260904-rmx)
# ---------------------------------------------------------------------------
#
# Drei Befunde aus der Analyse eines echten Laufs, alle rein darstellungsseitig
# — die Extraktion hatte die Werte korrekt gelesen:
#
# 1. 13 von 51 Auto-Zeilen trugen den Betrag 0.00. Ursache sind u.a. die leeren
#    USD-/EUR-Währungsblöcke einer BANK-P-Zinsabrechnung. Eine
#    Verrechnungssteuer von 0.00 gehört nicht in eine Übertragungstabelle.
# 2. Ein Institut erschien unter drei Schreibweisen, wodurch kein Abgleich griff.
# 3. Dieselbe Kontoposition tauchte doppelt auf, weil BANK-P für ein Konto
#    sowohl eine Zinsabrechnung (REP_P) als auch einen Steuerauszug (TAX_P)
#    ausstellt. Bewusst NICHT entfernt — nur markiert, damit sichtbar bleibt,
#    dass zwei Dokumente vorliegen.

# Schreibvarianten → kanonischer Name. Bewusst als explizite Liste statt als
# Heuristik: ein falsch zusammengeführter Aussteller wäre schlimmer als zwei
# getrennte Zeilen.
AUSSTELLER_ALIASE: dict[str, str] = {
    "bank-p": "BANK-P",
    "bank-p ag": "BANK-P",
    "post ch ag": "BANK-P",
}


def normalize_aussteller(name: str | None) -> str | None:
    """Führt bekannte Schreibvarianten eines Ausstellers zusammen."""
    if not name:
        return name
    return AUSSTELLER_ALIASE.get(name.strip().lower(), name)


def is_zero_amount(value: str | None) -> bool:
    """True, wenn der Wert ein Betrag ist und exakt null beträgt."""
    if not is_real_value(value):
        return False
    try:
        return parse_swiss_amount(str(value)) == 0
    except (ValueError, TypeError):
        return False


def drop_zero_rows(rows: list[dict]) -> tuple[list[dict], int]:
    """Entfernt Zeilen mit Betrag 0.00. Gibt (Restzeilen, Anzahl entfernt).

    **Kein** Betrag ist etwas anderes als Betrag null: die Zeile bleibt. Sie
    zeigt, dass ein Dokument diesen Wert fuehren sollte und keine Zahl dazu
    lieferte — eine Luecke, die man sehen muss. Ein Versuch, solche Zeilen
    stillschweigend wegzulassen, wurde zurueckgenommen: alles soll vorhanden
    sein und alles soll zu einem Dokument fuehren (260923-dua).
    """
    behalten = [r for r in rows if not is_zero_amount(r.get("betrag"))]
    return behalten, len(rows) - len(behalten)


# Kontokennung aus dem Dateinamen: IBAN oder eine laufende Kontonummer.
# BANK-P benennt REP_P/TAX_P nach derselben IBAN — daran hängt die
# Doppelerkennung.
_KONTO_RE = re.compile(r"(CH\d{2}[0-9A-Z]{10,})|(?<!\d)(\d{6,})(?!\d)")


def konto_key(pdf_name: str) -> str | None:
    """Extrahiert eine Kontokennung aus dem Dateinamen, falls vorhanden."""
    m = _KONTO_RE.search(pdf_name or "")
    if not m:
        return None
    return (m.group(1) or m.group(2)).upper()


def mark_duplicate_positions(rows: list[dict]) -> int:
    """Markiert Positionen, die aus zwei Dokumenten desselben Kontos stammen.

    Kriterium: gleiche Kontokennung, gleiche Beschreibung, gleicher Betrag,
    aber unterschiedliche Quelldatei. Die Zeilen bleiben erhalten — sie
    bekommen nur ``dupe_hinweis`` gesetzt.

    Returns: Anzahl markierter Zeilen.
    """
    gruppen: dict[tuple, list[dict]] = {}
    for r in rows:
        k = konto_key(r.get("pdf_name", ""))
        if not k or not is_real_value(r.get("betrag")):
            continue
        gruppen.setdefault(
            (k, r.get("beschreibung"), _normalize_amount(r.get("betrag"))), []
        ).append(r)

    markiert = 0
    for (k, _besch, _betrag), gruppe in gruppen.items():
        quellen = {r.get("pdf_name") for r in gruppe}
        if len(gruppe) < 2 or len(quellen) < 2:
            continue
        for r in gruppe:
            andere = sorted(q for q in quellen if q != r.get("pdf_name"))
            r["dupe_hinweis"] = (
                f"gleiches Konto {k} wie {', '.join(andere)} — nur einmal übertragen"
            )
            markiert += 1
    return markiert


# ---------------------------------------------------------------------------
# Konfidenz (260904-rmx)
# ---------------------------------------------------------------------------
#
# Drei Stufen statt zwei, weil das entscheidende Signal bisher fehlte: hat eine
# beschriftete Regel bzw. die Formulargeometrie den Wert geliefert, oder hat
# das Sprachmodell ihn unter mehreren Zahlen ausgewaehlt? Beides war im
# Ergebnis ununterscheidbar — und genau daran lagen die teuersten Fehler
# (AHV-Nummer als Abzug, Bruttorechnung als Selbstanteil).

KONFIDENZ_SICHER = "sicher"
KONFIDENZ_WAHRSCHEINLICH = "wahrscheinlich"
KONFIDENZ_UNSICHER = "unsicher"

_KONFIDENZ_SYMBOL = {
    KONFIDENZ_SICHER: "●●●",
    KONFIDENZ_WAHRSCHEINLICH: "●●○",
    KONFIDENZ_UNSICHER: "●○○",
}


def beschreibung_mit_unterscheidung(r: dict) -> str:
    """Der Zielwert, bei mehreren Positionen um die Unterscheidung ergänzt.

    Drei Hypotheken auf einem Beleg heissen alle „Hypothekarschuld 31.12." —
    nebeneinander in der Tabelle ist nicht zu sehen, welche welche ist. Die
    Unterscheidung ist ein kurzer Zusatz des Menschen („Festhypothek 0.95 %").

    Sie wird **angehängt**, nicht eingesetzt: der Zielwert des Feldvertrags
    bleibt als Teilzeichenkette erhalten, damit der Feldvertrag-Test und
    Grader-Check 18 ihn weiterhin finden.
    """
    name = str(r.get("beschreibung") or "")
    zusatz = str(r.get("unterscheidung") or "").strip()
    return f"{name} ({zusatz})" if zusatz else name


def nummeriere_positionen(rows: list[dict]) -> None:
    """Jede Zeile bekommt ihre laufende Nummer je Dokument und Zielwert.

    Ein Hypothekarbeleg nennt drei Hypotheken. Ohne Nummer teilen sie sich
    einen Schluessel: die zweite Bestaetigung ueberschreibt die erste, und
    eine von Hand erfasste Position fiel beim Tabellenbau ganz weg, weil der
    Schluessel schon belegt war (260923-dua).

    Zeilen, die ihre Nummer schon tragen — selbst erfasste Positionen kennen
    sie aus ihrem Schluessel —, behalten sie.
    """
    zaehler: dict[tuple[str, str], int] = {}
    for r in rows:
        k = (str(r.get("pdf_name") or ""), str(r.get("beschreibung") or ""))
        if r.get("pos"):
            zaehler[k] = max(zaehler.get(k, 0), int(r["pos"]))
            continue
        zaehler[k] = zaehler.get(k, 0) + 1
        r["pos"] = zaehler[k]


def wende_korrekturen_an(rows: list[dict], samples_dir: Path,
                         entfernen: bool = True) -> int:
    """Bestätigte und korrigierte Werte überschreiben die Extraktion.

    Ohne diesen Schritt bleibt der Zirkel offen: die Korrekturen landen in
    ``korrekturen.json``, dienen dem Grader als Sollwert — aber die Tabelle,
    die tatsächlich in die Steuererklärung wandert, zeigt weiter den alten,
    falschen Wert. Der Mensch müsste zweimal korrigieren.

    Ein korrigierter Wert ist das Verlässlichste, was es im ganzen System gibt:
    ein Mensch hat ihn im Original nachgesehen. Er bekommt deshalb
    ``herkunft="mensch"``, gilt als verankert und verdrängt jeden
    Plausibilitäts-Befund — der bezog sich auf den ersetzten Wert.
    """
    from extractors.korrekturen import (
        baue_schluessel, ist_zurueckgenommen, lade_alle,
    )

    korrekturen = lade_alle(samples_dir)
    if not korrekturen:
        # Auch ohne Korrekturen muessen die Positionsnummern stehen — sonst
        # traegt keine Zeile eine, und `ergaenze_eigene_zeilen` haelt jede
        # zweite Position derselben Art faelschlich fuer schon vorhanden.
        nummeriere_positionen(rows)
        return 0

    nummeriere_positionen(rows)

    # Als „irrelevant" abgehakte Dokumente fallen ganz aus der Tabelle.
    #
    # Vorher blieben ihre Werte stehen: der Mensch hatte gesagt „das gehoert
    # nicht in die Steuererklaerung", und die Zeilen standen weiter da
    # (260923-dua).
    from extractors.korrekturen import ausgeschlossene_dokumente
    ausgeschlossen = {b for b, art in ausgeschlossene_dokumente(
        korrekturen).items() if art == "irrelevant"}

    angewandt = 0
    gestrichen: list[int] = []
    for i, r in enumerate(rows):
        if r.get("pdf_name") in ausgeschlossen:
            r["gestrichen"] = True
            if entfernen:
                gestrichen.append(i)
            angewandt += 1
            continue
        pos = r.get("pos") or 1
        schluessel = baue_schluessel(r.get("pdf_name", ""),
                                     r.get("beschreibung", ""), pos)
        eintrag = korrekturen.get(schluessel)
        if eintrag is None:
            # Frueher hiess diese Zeile anders. Ohne das verloere jede
            # Bestaetigung ihren Bezug, sobald eine Bezeichnung im
            # Feldvertrag praeziser gefasst wird — und der Mensch muesste
            # seine Durchsicht wiederholen.
            for alt_bez in ALTE_BEZEICHNUNGEN.get(r.get("beschreibung", ""), ()):
                eintrag = korrekturen.get(
                    baue_schluessel(r.get("pdf_name", ""), alt_bez, pos))
                if eintrag is not None:
                    break
        if not isinstance(eintrag, dict) or ist_zurueckgenommen(eintrag):
            continue
        # Gestrichene Position: ein Mensch hat im Original nachgesehen und
        # festgestellt, dass dieses Dokument den Wert gar nicht ausweist.
        # Nicht zu verwechseln mit „gehört nicht in die Steuererklärung" —
        # der Wert kann in einem anderen Dokument stehen und dort völlig
        # richtig sein. Ein falsch ausgelesenes Feld liess sich bisher nur
        # überschreiben, nicht entfernen; es blieb in der Tabelle stehen,
        # egal was man eintrug (260904-rmx).
        if eintrag.get("entfernt"):
            # ``entfernen=False`` haelt die Zeile fuer die Review fest: dort
            # muss sie sichtbar bleiben, sonst laesst sich eine versehentliche
            # Streichung nie wieder aufheben. In der Tabelle faellt sie weg.
            r["gestrichen"] = True
            if entfernen:
                gestrichen.append(i)
            angewandt += 1
            continue
        # Person, Aussteller, Zielwert und Ziffer unabhaengig vom Betrag
        # uebernehmen — es gibt Zeilen, bei denen nur die Zuordnung falsch war.
        geaendert = False
        for schluessel_feld, zeilen_feld in (("person", "person"),
                                             ("aussteller", "aussteller"),
                                             ("zielwert_neu", "beschreibung"),
                                             ("unterscheidung", "unterscheidung"),
                                             ("ziffer_neu", "ziffer")):
            v = eintrag.get(schluessel_feld)
            if v and str(r.get(zeilen_feld) or "") != str(v):
                r[zeilen_feld] = str(v)
                geaendert = True

        # Zielwert geaendert, aber keine Ziffer mitgeschickt: der Feldvertrag
        # weiss sie. Sie dem Browser zu ueberlassen hiess, sie zu verlieren.
        if not str(r.get("ziffer") or "").strip() or r.get("ziffer") == "—":
            from extractors.zielwerte import ziffer_fuer
            aus_vertrag = ziffer_fuer(r.get("beschreibung"))
            if aus_vertrag:
                r["ziffer"] = aus_vertrag
                geaendert = True

        # Bestaetigte Werte wiederherstellen, bevor der Sollwert greift.
        #
        # Ein Mensch hat diesen Wert im Original nachgesehen und fuer richtig
        # befunden. Liefert ein spaeterer Lauf etwas anderes — das Modell ist
        # nicht deterministisch —, gilt weiterhin das Geprüfte. Ohne das war
        # jede Durchsicht nur bis zum naechsten Lauf haltbar: nach einem
        # erneuten `make productive` waren die Personenzuordnungen der
        # Krankenkassenbelege weg, obwohl niemand etwas geaendert hatte
        # (260904-rmx).
        for _feld, _zeilen_feld in (("wert_betrag", "betrag"),
                                    ("wert_person", "person")):
            _geprueft = eintrag.get(_feld)
            if not _geprueft:
                continue
            if str(r.get(_zeilen_feld) or "") == str(_geprueft):
                continue
            r[_zeilen_feld] = str(_geprueft)
            r["herkunft"] = "mensch"
            geaendert = True

        soll = eintrag.get("soll")
        if not soll:
            if geaendert:
                r["herkunft"] = "mensch"
                angewandt += 1
            continue
        if str(r.get("betrag") or "") == str(soll) and not geaendert:
            continue
        r["betrag"] = str(soll)
        r["herkunft"] = "mensch"
        r["anchor_valid"] = True
        r["plaus_errs"] = []
        r["plaus_hinweis"] = None
        r["manual_review_marker"] = None
        r["status"] = "auto"
        angewandt += 1

    if gestrichen:
        weg = set(gestrichen)
        rows[:] = [r for i, r in enumerate(rows) if i not in weg]
    return angewandt


def ergaenze_eigene_zeilen(rows: list[dict], samples_dir: Path) -> int:
    """Fügt Positionen hinzu, die der Mensch selbst erfasst hat.

    Nicht jeder Zielwert lässt sich aus dem Beleg herauslesen — manche stehen
    dort in einer Form, für die es kein Feld gibt. Beobachtet beim
    Wertschriftenverzeichnis: es weist Ertrag **mit** und **ohne**
    Verrechnungssteuer getrennt aus, der Feldvertrag kannte aber nur einen
    Bruttoertrag. Zwei Schubladen für drei Dinge.

    Statt dafür das Schema zu erweitern und die Extraktion anzufassen, kann der
    Mensch in der Oberfläche eine Zeile anlegen. Sie kommt hier in die Tabelle.
    Erkennbar an ``"neu": true`` im Korrektur-Eintrag.
    """
    from extractors.korrekturen import (
        baue_schluessel, ist_gestrichen, ist_zurueckgenommen, lade_alle,
        zerlege,
    )

    korrekturen = lade_alle(samples_dir)
    if not korrekturen:
        # Die Ablage gilt trotzdem. Hier stand ein blosses `return 0` — ohne
        # Durchsicht waere die Ablage wirkungslos gewesen und der Lauf haette
        # seine eigenen Zeilen in die Tabelle geschrieben, obwohl gepruefte
        # Werte vorliegen (260923-dua).
        return _aus_ablage(rows, samples_dir)

    # Vollstaendige Schluessel vergleichen, Positionsnummer inbegriffen.
    #
    # Vorher stand hier "<Beleg>|<Zielwert>" ohne Nummer. Eine von Hand
    # erfasste zweite Hypothek galt damit als „schon vorhanden", sobald die
    # Extraktion die erste geliefert hatte — und verschwand aus der Tabelle,
    # ohne dass irgendwo etwas gemeldet wurde (260923-dua).
    nummeriere_positionen(rows)
    vorhanden = {baue_schluessel(r.get("pdf_name", ""),
                                 r.get("beschreibung", ""), r.get("pos") or 1)
                 for r in rows}
    ergaenzt = 0
    for schluessel, eintrag in sorted(korrekturen.items()):
        if not isinstance(eintrag, dict) or schluessel in vorhanden:
            continue
        # Zurueckgenommene und gestrichene eigene Positionen kamen beim
        # naechsten Aufbau zurueck: die Zeile wurde geloescht, der Eintrag
        # blieb, und hier wurde daraus wieder eine Zeile (260923-dua).
        if ist_zurueckgenommen(eintrag) or ist_gestrichen(eintrag):
            continue
        soll = eintrag.get("soll")
        if not soll or not eintrag.get("neu"):
            continue
        beleg, beschreibung, pos = zerlege(schluessel)
        # Die Ziffer kommt aus dem Feldvertrag, nicht aus dem, was der Browser
        # mitgeschickt hat. Sonst steht eine selbst erfasste Position ohne
        # Ziffer da, obwohl der Vertrag sie kennt (260923-dua).
        from extractors.zielwerte import ziffer_fuer
        ziffer = (str(eintrag.get("ziffer") or "").strip()
                  or ziffer_fuer(beschreibung) or "—")
        rows.append({
            "pos": pos,
            "steuerbereich": eintrag.get("steuerbereich", "—"),
            "belegtyp": eintrag.get("belegtyp", ""),
            "ziffer": ziffer,
            # Auch die BESTAETIGTE Person und der bestaetigte Aussteller
            # gelten.
            #
            # Hier stand nur ``person``. Das Feld traegt aber allein eine
            # *Aenderung* per Dropdown; wer die vorgeschlagene Person bloss
            # abhakt, landet in ``wert_person``. Eine selbst erfasste Zeile
            # verlor ihre Person deshalb bei jedem Tabellenbau, und die
            # Pruefung meldete „Position ohne Person" fuer etwas, das der
            # Mensch laengst zugeordnet hatte (260923-dua). Fuer den
            # Aussteller gibt es keine Entsprechung — er wird nicht
            # abgehakt, nur gesetzt.
            "person": (eintrag.get("person")
                       or eintrag.get("wert_person") or ""),
            "aussteller": eintrag.get("aussteller") or "",
            "beschreibung": beschreibung,
            "unterscheidung": eintrag.get("unterscheidung") or "",
            "betrag": str(soll),
            "jahr": eintrag.get("jahr") or "",
            "pdf_name": beleg,
            "page": None, "bbox": None, "snippet": "",
            "status": "auto", "field_name": "manuell",
            "plaus_errs": [], "plaus_hinweis": None,
            "manual_review_marker": None, "inference_source": "mensch",
            "derived": False, "herkunft": "mensch", "anchor_valid": True,
        })
        ergaenzt += 1

    # Und zuletzt: was gepruefte Positionen in der Ablage sind, kommt von dort
    # — woertlich.
    ergaenzt += _aus_ablage(rows, samples_dir)
    return ergaenzt


def _aus_ablage(rows: list[dict], samples_dir: Path) -> int:
    """Geprüfte Positionen ersetzen alles, was der Lauf für sie errechnet hat.

    Die Ablage führt (Dokument, Zielwert)-Gruppen. Für jede davon fliegen die
    Zeilen des Laufs raus und die gespeicherten kommen rein — nicht Zeile für
    Zeile abgeglichen, sondern die ganze Gruppe. Das ist Absicht: jede
    Wiedererkennung einzelner Positionen über Name und Nummer ist in diesem
    Projekt schon schiefgegangen, und zwar mehrfach. Eine Gruppe, die der
    Mensch geprüft hat, gehört ihm vollständig.

    Gibt es keine Ablage, passiert nichts — der bisherige Weg bleibt gültig.
    """
    from extractors.ablage import gruppen_in_ablage, lade_geprueft

    gruppen = gruppen_in_ablage(samples_dir)
    if not gruppen:
        return 0
    # Nicht je (Dokument, Zielwert), sondern je DOKUMENT.
    #
    # Die enge Fassung liess Zeilen stehen, deren Zielwert die Ablage unter
    # einem anderen Namen fuehrt: ein Lauf lieferte „Vermögensstand 31.12.",
    # die Ablage kennt „Saldo 31.12." — also wurde nichts ersetzt und die alte
    # Zeile stand zusaetzlich in der Tabelle. Dasselbe passiert bei jedem Wert,
    # den der Mensch inzwischen gestrichen hat: der Lauf liefert ihn weiter.
    #
    # Ein Dokument, das der Mensch durchgesehen hat, gehoert ihm vollstaendig.
    # Was ein Lauf darin neu findet, ist ein Vorschlag und gehoert nicht
    # ungefragt in die Tabelle (260923-dua).
    dokumente = {d for d, _ in gruppen}
    vorher = len(rows)
    # Steuerbereich und Belegart sind Darstellung, kein Wert — sie haengen am
    # Dokument, nicht an der einzelnen Position. Deshalb werden sie von den
    # Zeilen des Laufs uebernommen, statt in der Ablage zu liegen: ein
    # Dokument hat genau eine Belegart, diese Zuordnung ist eindeutig.
    bereich_je_dokument: dict[str, str] = {}
    typ_je_dokument: dict[str, str] = {}
    for r in rows:
        dok = str(r.get("pdf_name") or "")
        if dok and r.get("steuerbereich"):
            bereich_je_dokument.setdefault(dok, str(r["steuerbereich"]))
        if dok and r.get("belegtyp"):
            typ_je_dokument.setdefault(dok, str(r["belegtyp"]))

    rows[:] = [r for r in rows
               if str(r.get("pdf_name") or "") not in dokumente]
    gespeichert = lade_geprueft(samples_dir)
    for r in gespeichert:
        dok = str(r.get("pdf_name") or "")
        r["steuerbereich"] = bereich_je_dokument.get(dok, "—")
        r["belegtyp"] = typ_je_dokument.get(dok, "")
    rows.extend(gespeichert)
    # Wie viele Zeilen die Ablage beigesteuert hat — nicht die Differenz.
    #
    # Vorher stand hier eine Rechnung, die je nach Vorzeichen etwas anderes
    # bedeutete. Auf der Konsole erschien sie als „63 selbst erfasste
    # Position(en)" bei 56 Werten und liess mich eine Vervielfachung
    # vermuten, die es nicht gab (260923-dua, Code-Review).
    return len(gespeichert)


def relevante_befunde(row: dict) -> list[str]:
    """Plausibilitäts-Befunde, die WIRKLICH diese Zeile betreffen.

    ``run_plausibility`` arbeitet pro Beleg, und das Ergebnis hängt an jeder
    Zeile des Belegs. Für die Konfidenz eines einzelnen Werts ist das zu grob:
    ein Befund zur Verrechnungssteuer sagt nichts über die Verlässlichkeit des
    Vermögensstands. Genau dadurch galt die Hälfte aller unsicheren Zeilen zu
    Unrecht als unsicher.

    Regel: nennt ein Befund ein Feld in Klammern, gilt er nur für dieses Feld.
    Nennt er keines, wird er als belegweit behandelt und zählt für alle Zeilen.
    """
    feld = row.get("field_name") or ""
    out: list[str] = []
    for e in row.get("plaus_errs") or []:
        text = str(e)
        genannte = set(re.findall(r"\(([a-z0-9_]+)\)", text))
        if not genannte or feld in genannte or feld in text:
            out.append(text)
    return out


def konfidenz_grund(row: dict) -> str:
    """Sagt in einem Satz, warum eine Zeile die Stufe hat, die sie hat.

    Ohne diesen Grund ist "unsicher" eine Behauptung statt einer Information —
    man weiss nicht, ob der Betrag falsch, die Person unklar oder nur der
    Nachweis nicht auffindbar ist. Das sind drei sehr verschiedene Aufgaben.
    """
    marker = row.get("manual_review_marker")
    if marker:
        stamm = str(marker).replace("manual_review:", "")
        return f"Marker: {stamm}"
    if not row.get("betrag"):
        return "kein Betrag extrahiert"
    if row.get("plaus_hinweis"):
        return str(row["plaus_hinweis"])
    _bef = relevante_befunde(row)
    if _bef:
        return f"Plausibilität: {', '.join(_bef[:2])}"
    if not row.get("anchor_valid", True):
        return "Wert nicht wortwörtlich im Beleg auffindbar (kein Anker)"
    if row.get("derived"):
        return "berechnet, nicht abgelesen"
    if row.get("herkunft") == "mensch":
        return "von dir im Original geprüft"
    if row.get("herkunft") == "regel":
        return "beschriftete Regel + Anker"
    return "Modell hat den Wert unter mehreren Zahlen ausgewählt"


def konfidenz_fuer(row: dict) -> str:
    """Leitet die Konfidenzstufe aus den vorhandenen Signalen ab.

    * ``sicher`` — von einer Regel/Geometrie geliefert, verankert, ohne
      Plausibilitaets-Befund. Der Wert steht wortwoertlich im Beleg und eine
      beschriftete Regel hat ihn dort identifiziert.
    * ``wahrscheinlich`` — verankert und ohne Befund, aber das Sprachmodell
      hat ihn ausgewaehlt. Stichprobe lohnt sich.
    * ``unsicher`` — kein Anker, Marker, oder Plausibilitaetsregel verletzt.
    """
    if row.get("manual_review_marker") or not row.get("betrag"):
        return KONFIDENZ_UNSICHER
    if relevante_befunde(row) or row.get("plaus_hinweis"):
        return KONFIDENZ_UNSICHER
    if not row.get("anchor_valid", True):
        return KONFIDENZ_UNSICHER
    if row.get("derived"):
        # Berechnet statt abgelesen — nie "sicher".
        return KONFIDENZ_WAHRSCHEINLICH
    if row.get("herkunft") in ("regel", "mensch"):
        return KONFIDENZ_SICHER
    return KONFIDENZ_WAHRSCHEINLICH


def _sichere_ausgefuellte_csv(ziel: Path) -> None:
    """Eine ausgefuellte korrektur.csv nicht kommentarlos ueberschreiben.

    Der Tabellenbau schreibt diese Datei bei jedem Lauf als leere Vorlage neu.
    Wer sie von Hand ausgefuellt und noch nicht eingespielt hatte, verlor die
    Eintraege — und mit ihnen eine der Quellen, aus denen sich die Durchsicht
    wiederherstellen laesst (260904-rmx).
    """
    if not ziel.exists():
        return
    import csv as _csv
    from datetime import datetime
    try:
        with ziel.open(encoding="utf-8", newline="") as fh:
            gefuellt = any(
                (r.get("korrektur") or r.get("notiz") or r.get("bestaetigt")
                 or r.get("person_neu") or r.get("streichen") or "").strip()
                for r in _csv.DictReader(fh))
    except (OSError, ValueError):
        gefuellt = True          # im Zweifel sichern
    if not gefuellt:
        return
    ordner = ziel.parent / "korrekturen-verlauf"
    try:
        ordner.mkdir(exist_ok=True)
        marke = datetime.now().strftime("%Y%m%d-%H%M%S")
        (ordner / f"korrektur-{marke}.csv").write_bytes(ziel.read_bytes())
    except OSError:
        pass


def schreibe_korrektur_csv(rows: list[dict], ziel: Path) -> int:
    """Schreibt die Tabelle in korrigierbarer Form.

    Der Optimierungszirkel: der Anwender traegt in ``korrektur`` den richtigen Wert
    ein, wo einer fehlt oder falsch ist, und in ``notiz`` optional die
    Beschriftung, unter der der Wert im Beleg steht. ``apply_korrekturen.py``
    liest die Datei zurueck, schreibt Truth-Dateien fuer die Regressionspruefung
    und meldet, welche Regel ergaenzt werden muss.

    Bewusst CSV: oeffnet sich in Numbers/Excel, bleibt aber diffbar.
    """
    import csv

    with ziel.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["nr", "beleg", "ziffer", "zielwert", "person", "aussteller",
                    "betrag", "konfidenz", "grund", "herkunft",
                    "korrektur", "notiz"])
        for i, r in enumerate(sorted(rows, key=lambda x: (
            konfidenz_fuer(x) != KONFIDENZ_UNSICHER, x["ziffer"])), start=1):
            w.writerow([
                i,
                r.get("pdf_name", ""),
                r.get("ziffer", ""),
                r.get("beschreibung", ""),
                fmt_display_value(r.get("person")) or "",
                fmt_display_value(r.get("aussteller")) or "",
                r.get("betrag") or "",
                konfidenz_fuer(r),
                konfidenz_grund(r),
                r.get("herkunft", ""),
                "",   # <- hier korrigieren
                "",   # <- hier die Beschriftung im Beleg notieren
            ])
    return len(rows)


def _gruppenschluessel(r: dict) -> tuple:
    """Was beim Ausfüllen **zusammen** eingegeben wird.

    Im Wertschriftenverzeichnis ist das ein Konto: ZHprivateTax legt unter
    „Wertschriften → Wertschriftenverzeichnis → Bankkonto hinzufügen" ein Konto
    an, und Saldo und Ertrag gehören in dieselbe Eingabe. Liegen dafür zwei
    Dokumente vor (Zinsabrechnung *und* Steuerauszug), gehören sie trotzdem in
    einen Block — sonst sucht man an zwei Stellen. ``konto_key`` erkennt das an
    der Nummer im Dateinamen.

    Die Nummer selbst wird **nie ausgegeben** — sie ist eine Kontonummer und
    hat in der Übertragungstabelle nichts verloren. Sie dient hier
    ausschliesslich als Schlüssel.
    """
    konto = konto_key(r.get("pdf_name", ""))
    return (konto or str(r.get("aussteller") or ""),
            str(r.get("person") or ""))


def _gruppentitel(rows: list[dict]) -> str:
    """Die Überschrift eines Blocks — Aussteller und Person, sonst nichts."""
    aussteller = next((fmt_display_value(r.get("aussteller")) for r in rows
                       if r.get("aussteller")), "")
    person = next((fmt_display_value(r.get("person")) for r in rows
                   if r.get("person")), "")
    teile = [t for t in (aussteller, person) if t]
    return " · ".join(teile) if teile else "Ohne Zuordnung"


def render_uebertragung(
    auto_rows: list[dict],
    manual_rows: list[dict],
    oos_entries: list[dict],
    total_belege: int,
    extracted: int,
    samples_dir: Path,
) -> str:
    """Die Tabelle in der Reihenfolge, in der sie ausgefüllt wird.

    Früher war sie nach Bearbeitungsstand gegliedert — automatisch übertragbar,
    manuell prüfen, out of scope. Das beschreibt die Arbeit des Programms, nicht
    die des Menschen. Wer sie überträgt, arbeitet Formular für Formular und
    darin Konto für Konto; er will alles für **einen Eingabeschritt** an einer
    Stelle und nicht an mehreren suchen (260923-dua).

    Deshalb: Formular in der Reihenfolge des Ausfüllens → je Konto bzw. Person
    ein Block → darin die Werte mit ihrer Ziffer → am Ende die Summen je Ziffer.
    """
    from extractors.schlusspruefung import betrag_oder_nichts
    from extractors.zielwerte import (formular_fuer, formular_sortierung,
                                      ziffer_name, ziffer_sortierung)

    quelle_rel = (samples_dir.relative_to(ROOT)
                  if samples_dir.is_relative_to(ROOT) else samples_dir)
    lines: list[str] = [
        "# Steuer-Übertragungstabelle — ZHprivateTax",
        "",
        f"Quelle: `{quelle_rel}/_results_full.json` "
        f"({total_belege} Dokumente, {extracted} extrahiert, "
        f"{len(oos_entries)} out-of-scope).",
        "",
        "Gegliedert nach **Formular** in der Reihenfolge des Ausfüllens, darin "
        "je Konto bzw. Person ein Block — alles für einen Eingabeschritt an "
        "einer Stelle. Der Steuerbereich ergibt sich aus dem Formular und "
        "steht deshalb nicht mehr in jeder Zeile.",
        "",
        "Spalten je Block: Ziffer · Beschreibung · Betrag CHF · Jahr · "
        "Quelle · Status. Anker und Nachweis pro Zeile stehen in "
        "`nachweis_anhang.md`.",
        "",
        "**⧉** markiert Positionen, für die zwei Dokumente desselben Kontos "
        "vorliegen (z.B. Zinsabrechnung *und* Steuerauszug). Beide Zeilen "
        "bleiben stehen — übertragen wird der Betrag **einmal**. Zeilen mit "
        "Betrag 0.00 werden nicht aufgeführt.",
        "",
    ]

    def zeile(r: dict) -> str:
        konf = konfidenz_fuer(r)
        status_md = f"{_KONFIDENZ_SYMBOL[konf]} {konf}"
        if r.get("dupe_hinweis"):
            status_md += " ⧉"
        return (f"| {md_escape(r['ziffer'])} "
                f"| {md_escape(beschreibung_mit_unterscheidung(r))} "
                f"| {fmt_amount(r['betrag'])} "
                f"| {md_escape(fmt_display_value(r['jahr']))} "
                f"| {fmt_quelle(r['pdf_name'], r['page'], r.get('inference_source'))} "
                f"| {status_md} |")

    alle = list(auto_rows) + list(manual_rows)
    nach_formular: dict[str, list[dict]] = {}
    for r in alle:
        nach_formular.setdefault(
            formular_fuer(str(r.get("beschreibung") or "")) or "Übrige",
            []).append(r)

    # Decimal, nicht float: Steuerbeträge werden summiert und abgetippt, und
    # eine Rundungsabweichung im Rappen faellt genau dort auf.
    gesamt_je_ziffer: dict[str, Decimal] = {}
    for formular in sorted(nach_formular, key=formular_sortierung):
        zeilen_f = nach_formular[formular]
        lines += [f"## {formular} ({len(zeilen_f)} Positionen)", ""]

        gruppen: dict[tuple, list[dict]] = {}
        for r in zeilen_f:
            gruppen.setdefault(_gruppenschluessel(r), []).append(r)

        for schluessel in sorted(gruppen, key=lambda k: (str(k[1]), str(k[0]))):
            block = sorted(gruppen[schluessel],
                           key=lambda r: (ziffer_sortierung(r["ziffer"]),
                                          str(r.get("beschreibung") or "")))
            lines += [f"### {md_escape(_gruppentitel(block))}", "",
                      "| Ziffer | Beschreibung | Betrag CHF | Jahr | Quelle "
                      "| Status |",
                      "|---|---|---:|---|---|---|"]
            lines += [zeile(r) for r in block]
            lines.append("")

        summen: dict[str, Decimal] = {}
        for r in zeilen_f:
            wert = betrag_oder_nichts(r.get("betrag"))
            if wert is not None and str(r.get("ziffer") or "").strip():
                summen[r["ziffer"]] = (
                    summen.get(r["ziffer"], Decimal(0)) + Decimal(str(wert)))
                gesamt_je_ziffer[r["ziffer"]] = (
                    gesamt_je_ziffer.get(r["ziffer"], Decimal(0))
                    + Decimal(str(wert)))
        if summen:
            lines += [f"**Summen für {formular}:**", "",
                      "| Ziffer | | Summe CHF |", "|---|---|---:|"]
            for ziffer in sorted(summen, key=ziffer_sortierung):
                lines.append(f"| {md_escape(ziffer)} "
                             f"| {md_escape(ziffer_name(ziffer))} "
                             f"| {fmt_amount(f'{summen[ziffer]:.2f}')} |")
            lines.append("")

    if gesamt_je_ziffer:
        lines += ["## Alle Ziffern auf einen Blick", "",
                  "Zum Abhaken beim Ausfüllen.", "",
                  "| Ziffer | | Summe CHF |", "|---|---|---:|"]
        for ziffer in sorted(gesamt_je_ziffer, key=ziffer_sortierung):
            lines.append(f"| {md_escape(ziffer)} "
                         f"| {md_escape(ziffer_name(ziffer))} "
                         f"| {fmt_amount(f'{gesamt_je_ziffer[ziffer]:.2f}')} |")
        lines.append("")

    lines += [f"## ⚪ Out of Scope ({len(oos_entries)} Dokumente)", ""]
    if oos_entries:
        lines += ["Dokumente, die das Tool versteht, für diese Familie aber "
                  "**keine Steuer-Übertragung benötigen** (z.B. "
                  "Bestandes-Reports, Korrespondenz).", "",
                  "| Datei | Belegtyp-Hinweis | Grund |", "|---|---|---|"]
        for e in oos_entries:
            lines.append(
                f"| `{md_escape(truncate(e.get('pdf_name', '?'), 60))}` "
                f"| {md_escape(e.get('belegtyp', ''))} "
                f"| {md_escape(e.get('out_of_scope_reason', '—'))} |")
    else:
        lines.append("_Alles wird für die Steuer ausgewertet._")
    lines.append("")
    return "\n".join(lines) + "\n"


def _by_design_exceptions(entry: dict) -> list[tuple[str, str]]:
    """Angewandte field_exceptions eines Belegs: (feldname, grund).

    Erkennt Felder mit ``inference_source`` ``user_verified:<grund>`` — die
    bewusst verifizierte Feld-Abwesenheit (Finding 5). Liefert den Grund ohne
    das ``user_verified:``-Präfix.
    """
    out: list[tuple[str, str]] = []
    for f in entry.get("fields", []) or []:
        src = f.get("inference_source")
        if isinstance(src, str) and src.startswith("user_verified:"):
            out.append((f.get("feld", "?"), src.removeprefix("user_verified:")))
    return out


def render_anhang(
    all_rows: list[dict],
    samples_dir: Path,
    entries: list[dict] | None = None,
) -> str:
    """Nachweis-Anhang: pro Beleg ein Block mit allen Rows + Anker/Snippet.

    ``entries`` (optional): die rohen ``_results_full``-Belege, damit angewandte
    field_exceptions (bewusste Abwesenheit) pro Beleg dokumentiert werden
    (Finding 5) — diese Felder erscheinen nicht als Übertragungs-Row.
    """
    by_design_by_pdf: dict[str, list[tuple[str, str]]] = {}
    if entries:
        for e in entries:
            exc = _by_design_exceptions(e)
            if exc:
                by_design_by_pdf[e.get("pdf_name", "?")] = exc
    lines: list[str] = []
    lines.append("# Nachweis-Anhang — Quelle, Anker, Snippet pro Position")
    lines.append("")
    lines.append(
        f"Quelle: `{samples_dir.relative_to(ROOT) if samples_dir.is_relative_to(ROOT) else samples_dir}/_results_full.json`. "
        "Pro Beleg ein Block; jede Zeile entspricht einer Position der "
        "Übertragungstabelle und enthält Page, BBox (x0, top, x1, bottom) und "
        "den Text-Snippet aus der Quell-PDF."
    )
    lines.append("")

    by_pdf: dict[str, list[dict]] = defaultdict(list)
    for r in all_rows:
        by_pdf[r["pdf_name"]].append(r)

    # Belege mit ausschliesslich by-design-Feldern haben evtl. keine
    # Übertragungs-Row, sollen aber trotzdem einen Block bekommen.
    all_pdfs = set(by_pdf) | set(by_design_by_pdf)
    for pdf in sorted(all_pdfs):
        rows = by_pdf.get(pdf, [])
        lines.append(f"## `{md_escape(pdf)}`")
        lines.append("")
        lines.append("| Feld | Beschreibung | Status | Seite | BBox | Snippet |")
        lines.append("|---|---|---|---:|---|---|")
        for r in rows:
            bbox_str = "—"
            if r["bbox"] and len(r["bbox"]) == 4:
                bbox_str = (
                    f"({r['bbox'][0]:.0f}, {r['bbox'][1]:.0f}, "
                    f"{r['bbox'][2]:.0f}, {r['bbox'][3]:.0f})"
                )
            page = str(r["page"]) if r["page"] is not None else "—"
            status_md = "🟢 auto" if r["status"] == "auto" else "🟡 prüfen"
            # Berechnete Werte: bbox zeigt aufs Subtotal, nicht auf einen
            # verankerten Originalwert → ehrlich kennzeichnen.
            feld_md = f"`{r['field_name']}`"
            if r.get("derived"):
                feld_md += " 🧮 berechnet"
            lines.append(
                f"| {feld_md} "
                f"| {md_escape(beschreibung_mit_unterscheidung(r))} "
                f"| {status_md} "
                f"| {page} "
                f"| {bbox_str} "
                f"| {md_escape(truncate(r['snippet'], 80))} |"
            )
            # Plausibility-Errors als Sub-Bullets unter der Tabelle (außerhalb Markdown-Row)
        # Plausibility-Errors pro Beleg (einmal, nicht pro Row)
        plaus_set: set[str] = set()
        for r in rows:
            for e in r.get("plaus_errs", []):
                plaus_set.add(e)
        if plaus_set:
            lines.append("")
            lines.append("**Plausibility-Befunde:**")
            for e in sorted(plaus_set):
                lines.append(f"- ⚠ {md_escape(truncate(e, 200))}")
        # Manual-Review-Marker-Erklärungen
        mr_markers = {r["manual_review_marker"] for r in rows if r["manual_review_marker"]}
        if mr_markers:
            lines.append("")
            lines.append("**Manual-Review-Marker:**")
            for m in sorted(mr_markers):
                lines.append(f"- 🟡 `{md_escape(m)}`")
        # Nicht-ok-Belege (empty/unknown/extract_failed/pipeline_error): die
        # synthetische Begründung im Nachweis ausweisen, damit der Mensch weiss,
        # warum nichts automatisch erfasst wurde.
        synth_begruendungen = {
            r["synth_status_begruendung"] for r in rows
            if r.get("synth_status_begruendung")
        }
        if synth_begruendungen:
            lines.append("")
            lines.append("**Nicht automatisch verarbeitet:**")
            for b in sorted(synth_begruendungen):
                lines.append(f"- ⚠ {md_escape(truncate(b, 200))}")
        # Derived-Werte (berechnet, nicht im PDF verankert) ausweisen.
        derived_rows = [r for r in rows if r.get("derived")]
        if derived_rows:
            lines.append("")
            lines.append("**Berechnete Werte (kein verankerter Originalbetrag):**")
            for r in derived_rows:
                deriv = r.get("derivation") or "berechnet"
                lines.append(
                    f"- 🧮 `{md_escape(r['field_name'])}` = {md_escape(str(deriv))}; "
                    "BBox zeigt auf das Subtotal, nicht auf einen ausgewiesenen "
                    "VRS-Wert. Im Original prüfen."
                )
        # Bewusst verifizierte Feld-Abwesenheit (field_exceptions) ausweisen.
        by_design = by_design_by_pdf.get(pdf)
        if by_design:
            lines.append("")
            lines.append("**Bewusste Abwesenheit (user-verifiziert):**")
            for feld, grund in sorted(by_design):
                lines.append(f"- `{md_escape(feld)}`: {md_escape(grund)}")
        lines.append("")
    return "\n".join(lines) + "\n"


# Menschenlesbare Begründung pro Nicht-ok-Status (Vollständigkeits-Constraint).
_STATUS_BEGRUENDUNG: dict[str, str] = {
    "empty": "Leerer Beleg — keine Wörter extrahierbar (Scan ohne Text-Layer?)",
    "unknown_belegtyp": "Belegtyp nicht erkannt — manuell klassifizieren und erfassen",
    "extract_failed": "Extraktion fehlgeschlagen — Beleg manuell erfassen",
    "pipeline_error": "Pipeline-Fehler — Beleg manuell erfassen",
}


def _synth_failed_row(entry: dict) -> dict:
    """Synthetische 🟡-Manuell-prüfen-Zeile für Belege mit Nicht-ok-Status.

    Damit verschwindet kein eingelesener Beleg stillschweigend aus der
    Übertragungstabelle (CLAUDE.md-Constraint #3, Vollständigkeit). Die Zeile
    trägt keinen Betrag (nichts extrahiert), nur Dateiname + maschinenlesbare
    Statusbegründung; der `text_truncated`-Marker wird mitberücksichtigt.
    """
    status = entry.get("status", "?")
    pdf_name = entry.get("pdf_name", "?")
    belegtyp = entry.get("belegtyp")
    begruendung = _STATUS_BEGRUENDUNG.get(status, f"Status `{status}` — manuell prüfen")
    if entry.get("text_truncated"):
        begruendung += " · Text abgeschnitten (>Limit), unvollständig extrahiert"
    if entry.get("error"):
        begruendung += f" · Fehler: {truncate(str(entry['error']), 80)}"
    beschreibung = "Beleg nicht automatisch verarbeitbar"
    if belegtyp and belegtyp != "unknown":
        beschreibung = f"Beleg `{belegtyp}` nicht automatisch verarbeitbar"
    return {
        "steuerbereich": "—",
        "ziffer": "—",
        "person": None,
        "aussteller": None,
        "beschreibung": beschreibung,
        "betrag": None,
        "jahr": None,
        "pdf_name": pdf_name,
        "page": None,
        "bbox": None,
        "snippet": "",
        "status": "manual_review",
        "field_name": "—",
        "plaus_errs": [],
        # Maschinenlesbare Ursache (Grader-Check 17: jede MR-Zeile braucht einen
        # Marker oder Plausi-Hinweis im Nachweis-Anhang).
        "manual_review_marker": f"pipeline_status:{status}",
        "inference_source": None,
        "derived": False,
        "derivation": None,
        "synth_status_begruendung": begruendung,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Steuer-Übertragungstabelle + Nachweis-Anhang")
    parser.add_argument(
        "--samples", type=Path, default=ROOT / "evals" / "samples_real_2022",
        help="Verzeichnis mit _results_full.json",
    )
    args = parser.parse_args()

    samples_dir = args.samples if args.samples.is_absolute() else ROOT / args.samples
    results = samples_dir / "_results_full.json"
    if not results.exists():
        print(f"FEHLT: {results} — erst process_samples_full laufen lassen.", file=sys.stderr)
        return 1

    data = json.loads(results.read_text())
    auto_rows: list[dict] = []
    manual_rows: list[dict] = []
    oos_entries: list[dict] = []
    extracted = 0
    for entry in data:
        status = entry.get("status")
        if status == "out_of_scope":
            oos_entries.append(entry)
            continue
        if status != "ok":
            # Klassifiziert/eingelesen, aber Extraktion gescheitert oder leer.
            # Vollständigkeits-Constraint (CLAUDE.md #3): der Beleg darf NICHT
            # stillschweigend verschwinden. Als 🟡-Manuell-prüfen-Zeile aufnehmen,
            # damit der Mensch ihn von Hand erfasst.
            manual_rows.append(_synth_failed_row(entry))
            continue
        extracted += 1
        _truncated = bool(entry.get("text_truncated"))
        for r in build_rows(entry):
            # Text-Truncation: der LLM sah nur den Anfang des Belegs, am Ende
            # stehende Beträge fehlen ggf. → keine Auto-Übernahme, manuell prüfen.
            if _truncated and r["status"] == "auto":
                r["status"] = "manual_review"
                if not r.get("manual_review_marker"):
                    r["manual_review_marker"] = "text_truncated"
            # Aussteller-Schreibvarianten zusammenführen (260904-rmx).
            r["aussteller"] = normalize_aussteller(r.get("aussteller"))
            if r["status"] == "auto":
                auto_rows.append(r)
            else:
                manual_rows.append(r)

    # Korrekturen zuerst: ein vom Menschen geprüfter Wert schlägt alles.
    #
    # Die Zeilen werden in EINER Liste uebergeben und danach wieder getrennt.
    # Frueher stand hier ``auto_rows + manual_rows`` — eine neue Liste. Die
    # Werte in den Zeilen wurden korrigiert (dieselben dicts), aber das
    # Entfernen gestrichener Zeilen traf nur die Kopie: was der Mensch als
    # „nicht im Dokument" markiert hatte, verschwand aus der Review und stand
    # trotzdem in steuer_uebertragung.md (260904-rmx).
    _alle = auto_rows + manual_rows
    _korr_n = wende_korrekturen_an(_alle, samples_dir)
    _uebrig = {id(r) for r in _alle}
    auto_rows = [r for r in auto_rows if id(r) in _uebrig]
    manual_rows = [r for r in manual_rows if id(r) in _uebrig]
    _eigene_n = ergaenze_eigene_zeilen(auto_rows, samples_dir)
    # Zeilen, die durch eine Korrektur zu 'auto' wurden, umhängen.
    _neu_auto = [r for r in manual_rows if r["status"] == "auto"]
    if _neu_auto:
        manual_rows = [r for r in manual_rows if r["status"] != "auto"]
        auto_rows += _neu_auto

    # Nullbetrags-Zeilen verwerfen — nichts zu übertragen (260904-rmx).
    auto_rows, _null_auto = drop_zero_rows(auto_rows)
    manual_rows, _null_manual = drop_zero_rows(manual_rows)

    # Gleiche Kontoposition aus zwei Dokumenten markieren, nicht entfernen.
    _dupes = mark_duplicate_positions(auto_rows + manual_rows)

    uebertragung = samples_dir / "steuer_uebertragung.md"
    anhang = samples_dir / "nachweis_anhang.md"
    uebertragung.write_text(render_uebertragung(
        auto_rows, manual_rows, oos_entries, len(data), extracted, samples_dir
    ))
    anhang.write_text(render_anhang(auto_rows + manual_rows, samples_dir, data))
    # Optimierungszirkel: korrigierbare Fassung derselben Tabelle.
    _korr = samples_dir / "korrektur.csv"
    _sichere_ausgefuellte_csv(_korr)
    _n_korr = schreibe_korrektur_csv(auto_rows + manual_rows, _korr)

    print(f"Geschrieben: {uebertragung}")
    print(f"  🟢 auto: {len(auto_rows)}")
    print(f"  🟡 manual: {len(manual_rows)}")
    print(f"  ⚪ oos:    {len(oos_entries)}")
    print(f"  ⌀ Nullbetrags-Zeilen verworfen: {_null_auto + _null_manual}")
    print(f"  ⧉ doppelte Kontopositionen markiert: {_dupes}")
    if _korr_n:
        print(f"  ✍ eigene Korrekturen uebernommen: {_korr_n}")
    if _eigene_n:
        print(f"  ＋ selbst erfasste Positionen ergaenzt: {_eigene_n}")
    for _stufe in (KONFIDENZ_SICHER, KONFIDENZ_WAHRSCHEINLICH, KONFIDENZ_UNSICHER):
        _c = sum(1 for r in auto_rows + manual_rows if konfidenz_fuer(r) == _stufe)
        print(f"  {_KONFIDENZ_SYMBOL[_stufe]} {_stufe}: {_c}")
    # Woher kommt die Unsicherheit? Aggregiert, damit die haeufigste
    # Ursache sichtbar wird statt Zeile fuer Zeile gesucht werden muss.
    from collections import Counter as _Counter
    _gruende = _Counter(
        konfidenz_grund(r) for r in auto_rows + manual_rows
        if konfidenz_fuer(r) == KONFIDENZ_UNSICHER
    )
    if _gruende:
        print("\nUrsachen der unsicheren Zeilen:")
        for _g, _c in _gruende.most_common():
            print(f"  {_c:3}×  {_g[:78]}")
    print(f"\nGeschrieben: {_korr}  ({_n_korr} Zeilen zum Korrigieren)")
    print(f"Geschrieben: {anhang}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
