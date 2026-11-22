"""Person-Inferenz pro Beleg via Exact-Match (Plan 02-02).

Schaut sich die Tagged-Wort-Liste eines PDFs an und entscheidet anhand der
geladenen Familien-Konfiguration, welcher Person der Beleg zuzuordnen ist.
Decisions D-B1..B5 aus ``02-CONTEXT.md`` werden hier umgesetzt:

* D-B1 — Exact-Match auf Voll-Name (Vorname + Nachname) nach
  :func:`extractors.numbers.normalize`. Kein Levenshtein/Fuzzy in v1
  (PII-Risiko, false-positive-Kosten zu hoch).
* D-B2 — Algorithmus: pro Member ``needle = normalize(first + " " + last)``;
  Treffer wenn ``needle`` als Substring im normalisierten Joined-Text
  vorkommt. 0 Treffer → ``None``; 1 Treffer → ``role``; 2+ Treffer →
  ``"gemeinsam"`` (Joint-Account).
* D-B3 — Anker zeigt auf die Tag-ID des Vornamens des einzigen Treffers
  (Sliding-Window-Konkatenation der normalisierten Wörter).
* D-B4 — Nur-Nachname-Treffer (z. B. „Familie Muster") liefert ``None``,
  weil das Schema unter mehreren Members mit gleichem Nachnamen nicht
  disambiguieren kann.
* D-B5 — Rückgabe-Rolle ist eine der CSV-Werte
  ``mann | frau | kind1 | kind2 | gemeinsam`` oder ``None``.

Reine Funktion ohne I/O. Kein Ollama, kein PDF-Read.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel

from extractors.family import Family
from extractors.numbers import normalize
from extractors.tokenize import TaggedWord


#: Map ``Belegtyp → Pflichtfeld-Name`` für field-level Person-Match (D-D1,
#: 03-CONTEXT.md). Wird in Plan 03-02 (Phase 3, Wave 2) von der neuen
#: Funktion ``infer_person_field()`` konsumiert: pro Belegtyp wird der
#: angegebene Schema-Wert (z.B. ``raw.kontoinhaber_name.value`` für Bank-
#: Zinsausweis) als Suchraum für den Token-basierten Familien-Match
#: verwendet — robuster als der joined-text ``infer_person()`` aus
#: Phase 2 (löst die zwei xfail-Limits ``bank_zinsausweis_zkb`` und
#: ``kk_praemienbescheinigung_helsana``).
#:
#: Lohnausweis ist NICHT enthalten — er hat keine direkte Person-Spalte
#: (``arbeitgeber`` ist die Firma); Lohnausweis behält den joined-text
#: :func:`infer_person` aus Phase 2 (D-D4 Phase 1).
#:
#: Stub in Wave 1 (Plan 03-01) — Implementierung von
#: ``infer_person_field()`` folgt in Plan 03-02.
PERSON_RELEVANT_FIELD: dict[str, str] = {
    "bank_zinsausweis": "kontoinhaber_name",
    "kk_praemienbescheinigung": "versicherte_person_name",
    "saeule_3a": "kontoinhaber_name",
    "wertschriftenverzeichnis": "kontoinhaber_name",
    "spenden": "spender_name",
    "berufsauslagen": "person_name",
    "kinderbetreuung": "kind_name",
    "hypothek_zinsbestaetigung": "kontoinhaber_name",
    "liegenschaftsunterhalt": "eigentuemer_name",
    "krankheitskosten": "person_name",
}


def _fold_diacritics(s: str) -> str:
    """Folded NFKD-Form ohne Combining-Marks für Name-Match.

    :func:`extractors.numbers.normalize` macht NFKC + Apostroph-/Whitespace-
    Strip + Lower-Case und ist Single-Source-of-Truth für Beträge/Anker.
    Für Person-Name-Matching brauchen wir zusätzlich Diakritika-Insensitivität
    (RESEARCH-Pitfall: ``Müller`` ≡ ``Muller``, ``Hâns`` ≡ ``Hans``).

    Diese Hilfsfunktion ist bewusst lokal: sie verändert nicht die globale
    ``normalize``-Semantik (die für Beträge wichtig ist) und wird nur
    innerhalb von :func:`infer_person` aufgerufen.
    """
    decomposed = unicodedata.normalize("NFKD", s)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def _name_normalize(s: str) -> str:
    """Voll-Normalisierung für Name-Matching: ``normalize`` + Diakritika-Fold.

    ACHTUNG — diese Variante strippt ALLE Whitespace-Zeichen (NBSP/NNBSP/
    THINSP/Space) und ist deshalb nur für Substring-Match auf konkatenierten
    Strings geeignet (siehe :func:`infer_person`). Für Token-basiertes
    Matching (siehe :func:`infer_person_field`) muss ZUERST auf Whitespace
    gesplittet und DANN pro Token normalisiert werden — :func:`_name_tokens`.
    """
    return _fold_diacritics(normalize(s))


# Standard-Whitespace-Klasse inkl. NBSP/NNBSP/THINSP (spiegelt
# ``extractors.numbers._WHITESPACE_CLASS``). Wir splitten Namens-Strings
# AUF dieser Klasse, BEVOR :func:`_name_normalize` jedes Token einzeln
# weiter normalisiert — sonst würden NBSP/Apostroph-Strip die Wort-Grenzen
# vor dem Split auflösen und ``"Hans Muster"`` zu einem Single-Token
# ``"hansmuster"`` reduzieren (Token-Match-Bug).
_NAME_SPLIT_RE = re.compile(r"[ \xa0  \t\n\r]+")


def _name_tokens(s: str) -> set[str]:
    """Splittet einen Namens-String auf Whitespace und normalisiert pro Token.

    Schritte:

    1. Split auf Whitespace-Klasse (Space, NBSP U+00A0, NNBSP U+202F,
       THINSP U+2009, Tab, Newline) — vor jeder Normalisierung, damit die
       Token-Grenzen erhalten bleiben.
    2. Pro Token ``_name_normalize`` (NFKC + Apostroph-Strip + Whitespace-
       Strip + Lower + Diakritika-Fold).
    3. Leere Tokens (entstehen aus konsekutiven Whitespaces) werden verworfen.

    Liefert ein ``set`` — Reihenfolge ist für den Subset-Match irrelevant.
    """
    return {t for t in (_name_normalize(part) for part in _NAME_SPLIT_RE.split(s)) if t}


# Erweitertes Role-Literal: zusätzlich zu den 4 ``family.Role``-Werten gibt
# ``"gemeinsam"`` an, dass mehrere Familienmitglieder im Beleg vorkommen
# (typisch für Joint-Accounts). ``None`` als Rückgabe-Wert bedeutet
# „kein Match" (D-B4) und wird nicht über das Literal abgebildet.
PersonRole = Literal["mann", "frau", "kind1", "kind2", "gemeinsam"]


@dataclass(frozen=True)
class PersonResult:
    """Ergebnis der Person-Inferenz für einen einzelnen Beleg.

    * ``role`` — die zugeordnete Rolle oder ``None`` wenn kein eindeutiger
      Match möglich war.
    * ``anchor_tag_id`` — Tag-ID des Vornamens beim einzigen Treffer (D-B3);
      ``None`` bei ``role is None`` oder ``role == "gemeinsam"``.
    * ``matched_members`` — Liste der Roles aller gematchten Members
      (für Debugging und Run-Reports).
    """

    role: PersonRole | None
    anchor_tag_id: int | None
    matched_members: list[str] = field(default_factory=list)


def _find_anchor_tag_id(words: list[TaggedWord], needle: str) -> int | None:
    """Sliding-Window über ``words``: liefert tag_id des ersten Match-Worts.

    Geht alle Start-Positionen ``i`` durch und konkateniert iterativ die
    normalisierten Wort-Texte. Sobald die Konkatenation ``needle`` enthält
    (oder ``needle`` enthalten ist — Substring-Match analog zur Joined-Text-
    Match-Logik in :func:`infer_person`), liefert die Funktion die Tag-ID
    von ``words[i]``. Damit zeigt der Anker auf den **Vornamen** (D-B3).
    """
    n = len(words)
    for i in range(n):
        acc = ""
        for j in range(i, n):
            acc += _name_normalize(words[j].text)
            if needle in acc:
                return words[i].tag_id
            # Wenn der akkumulierte String länger ist als der needle und
            # ihn nicht enthält, lohnt sich Weiterlaufen für dieses ``i`` nicht.
            if len(acc) > len(needle) and needle not in acc:
                # Edge-Case: needle könnte den Start verfehlt haben — wir
                # brechen ab und versuchen das nächste i.
                if not needle.startswith(acc[: min(len(acc), len(needle))]):
                    break
    return None


def infer_person(words: list[TaggedWord], family: Family | None) -> PersonResult:
    """Inferiert die Person eines Belegs anhand der Familien-Konfiguration.

    Algorithmus (D-B1, D-B2, D-B3):

    1. Wenn ``family is None`` → früh ``PersonResult(None, None, [])``.
    2. Joined-Text aller Wörter normalisieren.
    3. Pro Member: ``needle = normalize(first_name + " " + last_name)``.
       Treffer wenn ``needle in normalized_joined_text``.
    4. 0 Treffer → ``role=None``. 1 Treffer → ``role=member.role``,
       Anker via :func:`_find_anchor_tag_id` auf den Vornamen. 2+ Treffer
       → ``role="gemeinsam"`` ohne Anker.

    Diese Funktion ist deterministisch und macht kein I/O.
    """
    if family is None:
        return PersonResult(role=None, anchor_tag_id=None, matched_members=[])

    joined = " ".join(w.text for w in words)
    norm_joined = _name_normalize(joined)

    matches: list[tuple[str, str]] = []  # (role, needle)
    for m in family.members:
        needle = _name_normalize(f"{m.first_name} {m.last_name}")
        if needle and needle in norm_joined:
            matches.append((m.role, needle))

    if not matches:
        return PersonResult(role=None, anchor_tag_id=None, matched_members=[])

    if len(matches) >= 2:
        return PersonResult(
            role="gemeinsam",
            anchor_tag_id=None,
            matched_members=[r for r, _ in matches],
        )

    # Genau ein Treffer — Anker auf Vornamen-Tag-ID auflösen (D-B3).
    role, needle = matches[0]
    anchor = _find_anchor_tag_id(words, needle)
    return PersonResult(role=role, anchor_tag_id=anchor, matched_members=[role])


def infer_person_field(
    belegtyp: str,
    raw: BaseModel,
    family: Family | None,
) -> PersonResult:
    """Field-level Person-Inferenz via Token-Match (D-D1, D-D2, D-D5).

    Schaut sich gezielt das person-relevante Schema-Feld des jeweiligen
    Belegtyps an (Map :data:`PERSON_RELEVANT_FIELD`) und entscheidet
    anhand der Familien-Konfiguration, welcher Person der Beleg
    zuzuordnen ist. Robuster als :func:`infer_person` (joined-text), weil
    der Suchraum auf den vom LLM strukturiert extrahierten Namens-String
    eingegrenzt ist — löst die zwei Phase-2-xfail-Limits
    ``bank_zinsausweis_zkb`` (Joint-Account ``Hans und Maria Muster``) und
    ``kk_praemienbescheinigung_helsana`` (Beitragszahler-vs-Versicherte-
    Ambiguität).

    Algorithmus:

    1. ``family is None`` → früh ``PersonResult(None, None, [])``.
    2. ``belegtyp`` nicht in :data:`PERSON_RELEVANT_FIELD` (z. B.
       ``lohnausweis``, ``unknown``) → ``PersonResult(None, None, [])``.
       Lohnausweis hat keine direkte Person-Spalte; Caller dispatcht auf
       :func:`infer_person` (D-D3, D-D4 Phase 1).
    3. Feld-Wert via ``getattr(raw, PERSON_RELEVANT_FIELD[belegtyp], None)``.
       Wenn das Feld fehlt, ``None`` ist oder leer → ``role=None``.
    4. Feld-Tokens = ``set(_name_normalize(field.value).split())``. Split
       erfolgt auf Whitespace nach Normalisierung — Normalize entfernt
       NBSP/NNBSP/THINSP, daher zerfällt ``"Hans Müller"`` in zwei Tokens.
    5. Pro Member: ``member_tokens = set(_name_normalize(first + " " + last).split())``.
       Match wenn ``member_tokens ⊆ field_tokens`` UND ``member_tokens``
       nicht leer.
    6. 0 Treffer → ``PersonResult(None, None, [])``.
       1 Treffer → ``PersonResult(role, tag_refs[0], [role])``.
       ≥2 Treffer → ``PersonResult("gemeinsam", None, [roles])``.

    Anker = ``tag_refs[0]`` des person-relevanten Feldes (das Feld als
    Ganzes ist der Anker; volle BBox-Verdrahtung kommt in Plan 03-06).

    Bekannte v1-Limits:

    * Initialen wie ``N. F. Muster`` matchen NICHT — Tokens nach Normalize
      sind ``{n, f, muster}``, das Member-Token ``hans`` ist kein Subset
      (RESEARCH-Pitfall 5).
    * Apostroph/Whitespace-only-Pattern wie ``Hans'Muster`` (kein Space)
      zerfällt zu einem einzigen Token ``hansmuster`` — Member-Tokens
      ``{hans, muster}`` sind dann nicht Subset. Akzeptiertes v1-Verhalten.
    * Diakritika ohne NFKD-Match: ``Hänsi`` vs ``Hans`` — Normalize macht
      NFKC + Lower, aber kein Diakritika-Strip. ``_name_normalize`` macht
      zusätzlich Diakritika-Fold, daher matched ``Hänsi`` → ``hansi`` ≠
      ``hans`` (Token-Unterschied, v1-Limit).

    Deterministische reine Funktion ohne I/O.
    """
    if family is None:
        return PersonResult(role=None, anchor_tag_id=None, matched_members=[])

    field_name = PERSON_RELEVANT_FIELD.get(belegtyp)
    if field_name is None:
        # ``lohnausweis``, ``unknown`` oder unbekannter belegtyp — Caller
        # dispatcht auf joined-text-Variante :func:`infer_person`.
        return PersonResult(role=None, anchor_tag_id=None, matched_members=[])

    tagged_field = getattr(raw, field_name, None)
    if tagged_field is None:
        return PersonResult(role=None, anchor_tag_id=None, matched_members=[])

    # ``tagged_field`` ist ein ``TaggedField[str]``; defensiv via getattr
    # auf .value, falls ein Caller einen rohen String reingibt.
    field_value = getattr(tagged_field, "value", tagged_field)
    if not isinstance(field_value, str) or not field_value:
        return PersonResult(role=None, anchor_tag_id=None, matched_members=[])

    field_tokens = _name_tokens(field_value)
    if not field_tokens:
        return PersonResult(role=None, anchor_tag_id=None, matched_members=[])

    matches: list[str] = []
    for m in family.members:
        member_tokens = _name_tokens(f"{m.first_name} {m.last_name}")
        if member_tokens and member_tokens <= field_tokens:
            matches.append(m.role)

    if not matches:
        return PersonResult(role=None, anchor_tag_id=None, matched_members=[])

    if len(matches) >= 2:
        return PersonResult(
            role="gemeinsam",
            anchor_tag_id=None,
            matched_members=matches,
        )

    # Genau ein Treffer — Anker = erste tag_ref des person-relevanten Feldes.
    anchor: int | None = None
    tag_refs = getattr(tagged_field, "tag_refs", None)
    if tag_refs:
        anchor = tag_refs[0]
    return PersonResult(role=matches[0], anchor_tag_id=anchor, matched_members=[matches[0]])
