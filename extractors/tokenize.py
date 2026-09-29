"""Tag-Injection-Tokenizer: Wort-Listen mit BBoxes → tagged_text + tag_map.

Schreibt vor jedes Wort einen ``[T<id>]``-Präfix und liefert eine
``tag_map: dict[int, TaggedWord]``, die der :mod:`extractors.anchor_resolver`
für die BBox-Auflösung verwendet (RESEARCH §Pattern 1).

Das Tag-Format ``[T<int>] <wort>`` ist Single-Source-of-Truth für
Tokenizer und Anker-Resolver — wenn das Format hier geändert wird,
muss auch der Resolver angepasst werden.

Multi-Page-Support (ING-05): Jedes Wort trägt ``page`` mit, sodass
auch bei mehrseitigen PDFs die Anker-BBoxes korrekt der Seite
zugeordnet werden können.

Reines Modul — kein I/O.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class TaggedWord:
    """Unveränderliche Repräsentation eines pdfplumber-Wortes mit Tag-ID."""

    tag_id: int
    page: int
    bbox: tuple[float, float, float, float]
    text: str


def tokenize(words: Iterable[dict]) -> tuple[str, dict[int, TaggedWord]]:
    """Erzeugt ``tagged_text`` und ``tag_map`` aus einer Wort-Liste.

    Argumente:

    * ``words`` — pdfplumber-``extract_words()``-Output, Liste von Dicts mit
      den Schlüsseln ``page``, ``x0``, ``top``, ``x1``, ``bottom``, ``text``.

    Rückgabe:

    * ``tagged_text`` — alle Wörter im Format ``[T0] foo [T1] bar …``,
      Leerzeichen-getrennt. Geeignet als LLM-Input.
    * ``tag_map`` — Dict ``tag_id -> TaggedWord`` für die Anker-Auflösung.

    Wörter mit nur-Whitespace-Text werden übersprungen (kein Eintrag in
    ``tag_map``, kein Tag-Token im ``tagged_text``); das ist robust
    gegenüber pdfplumber-Edge-Cases mit eingebettetem Whitespace.
    Tag-IDs sind monoton steigend ab 0 für die tatsächlich emittierten
    Wörter.
    """
    parts: list[str] = []
    tag_map: dict[int, TaggedWord] = {}
    next_id = 0
    for w in words:
        text = w["text"].strip()
        if not text:
            continue
        tw = TaggedWord(
            tag_id=next_id,
            page=int(w["page"]),
            bbox=(w["x0"], w["top"], w["x1"], w["bottom"]),
            text=text,
        )
        tag_map[next_id] = tw
        parts.append(f"[T{next_id}] {text}")
        next_id += 1
    return " ".join(parts), tag_map
