"""Anker-Resolver: ``tag_refs`` → BBox + normalisierter Wert-Match.

Nimmt die vom LLM gelieferten ``tag_refs`` (Liste von Tag-IDs aus dem
``[T<id>]``-präfixierten Eingabetext), schlägt in der ``tag_map`` die
zugehörigen Bounding-Boxes nach und prüft, ob der extrahierte Wert
nach Normalisierung tatsächlich dem verbundenen Wort-Snippet entspricht.

Anker-Validität ist Teil der Konfidenz-Komposition (D-B5): valid==False
führt nicht zu einem harten Fehler, sondern routet das Feld in den
``unverified``-Bucket.

Reines Modul — kein I/O. Single-Source-of-Truth für die Normalisierung
ist :func:`extractors.numbers.normalize`.
"""
from __future__ import annotations

import re

from extractors.numbers import normalize, parse_swiss_amount
from extractors.tokenize import TaggedWord

BBox = tuple[float, float, float, float]

# Maximaler Zeilen-Versatz (top-Differenz in pt), bei dem zwei Tokens noch als
# "gleiche Zeile" gelten — für den Space-Tausender-Join (E1).
_SAME_ROW_DY = 3.0
# Maximaler horizontaler Abstand (in pt) zwischen Tausender-Präfix und Rumpf.
# Space-getrennte Tausender ("24 652.55") stehen eng beieinander; ein weit
# entfernter Vorgänger gehört zu einer anderen Spalte und wird NICHT gejoint.
_THOUSANDS_MAX_DX = 30.0

# Rumpf eines space-getrennten Betrags: exakt 3 Ganzzahl-Stellen, optional mit
# Dezimalteil ("652.55", "000.00"). Tausenderblöcke haben immer 3 Stellen.
# Rumpf eines zerteilten Betrags. Zwei Formen:
#   \d{3}(\.\d{1,2})?  — echte Tausendergruppe ("24 652.55")
#   \d{1,2}\.\d{1,2}   — pdfplumber hat die Zahl an einer Kerning-Luecke
#                        zerteilt ("3" + "48.09" = 348.09). Beobachtet in
#                        PostFinance-Zinsabschluessen: der Saldo wurde dort um
#                        eine Groessenordnung zu klein gemeldet, verankert und
#                        gruen. Dezimalstellen sind Pflicht, damit reine
#                        Ziffernblocks (Telefonnummern "044 000") nicht als
#                        Betrag zusammenwachsen.
_THOUSANDS_BODY_RE = re.compile(r"^(?:\d{3}(?:[.,]\d{1,2})?|\d{1,2}[.,]\d{1,2})$")

# Echte Tausendergruppe (drei Ganzzahlstellen) — darf den weiten Abstand nutzen.
_THOUSANDS_FULL_GROUP_RE = re.compile(r"^\d{3}(?:[.,]\d{1,2})?$")

# Kerning-Luecke innerhalb einer Zahl. Gemessen an echten Dokumenten: 2.67pt.
# Bewusst knapp — eine Nachbarspalte liegt Dutzende Punkte entfernt.
_KERNING_MAX_DX = 5.0
# Präfix: 1-3 Ziffern ("24", "1", "999").
# Praefix eines zerteilten Betrags. Keine fuehrende Null: eine Tausendergruppe
# schreibt man 44'000, nie 044'000. Ohne diese Bedingung wuchsen
# Telefonnummern ("044 000 00 00") zu Betraegen zusammen — 044+000 ergab 44000.
_THOUSANDS_PREFIX_RE = re.compile(r"^(?:0|[1-9]\d{0,2})$")

# Maximale Fensterbreite (in Wörtern) für den Verbatim-Fallback. Mehr-Wort-Werte
# wie "BORDER GmbH, 4051 Basel" brauchen ein Fenster, einzelne Beträge passen
# typischerweise in ein einziges Wort. 12 deckt empirisch alle Schema-Felder ab.
_VERBATIM_MAX_WINDOW = 12


def _find_label_spans(
    items: list[tuple[int, TaggedWord]],
    label: str,
) -> list[BBox]:
    """Sucht alle BBoxes, in denen ``label`` (case-insensitive Substring)
    vorkommt — single-token oder als Mehr-Wort-Span (bis 4 Wörter).

    Symmetrisch zur Label-Suche in ``scripts/eval_anchors.py``: erst
    Einzelwort, sonst joinen von 2-4 konsekutiven Wörtern. Single-Token-
    Treffer haben den engsten BBox-Span; nur wenn der nicht reicht (Label
    ist Mehr-Wort wie "Saldo zu Ihren Gunsten"), wird zum Multi-Token
    gegriffen.
    """
    label_lower = label.lower()
    spans: list[BBox] = []
    n = len(items)
    for i in range(n):
        _, w = items[i]
        if label_lower in w.text.lower():
            spans.append(w.bbox)
            continue
        for span_size in range(2, 5):
            if i + span_size > n:
                break
            # Cross-Row-Join verwerfen: konsekutive Wörter aus unterschiedlichen
            # Zeilen (top-Range > 5pt) sind kein echter Label-Span — der LLM-
            # Reading-Order joint sonst über Zeilengrenzen und "findet" Labels,
            # die nur künstlich zusammengehören.
            tops = [items[i + k][1].bbox[1] for k in range(span_size)]
            if max(tops) - min(tops) > 5:
                continue
            joined = " ".join(items[i + k][1].text for k in range(span_size)).lower()
            if label_lower in joined:
                x0 = min(items[i + k][1].bbox[0] for k in range(span_size))
                top = min(tops)
                x1 = max(items[i + k][1].bbox[2] for k in range(span_size))
                bot = max(items[i + k][1].bbox[3] for k in range(span_size))
                spans.append((x0, top, x1, bot))
                break
    return spans


def _select_by_label_priors(
    hits: list[TaggedWord],
    items: list[tuple[int, TaggedWord]],
    label_priors: list[str],
) -> TaggedWord | None:
    """Wählt aus mehreren Single-Token-Hits den, der räumlich am nächsten zu
    einem Label-Prior sitzt. Scoring (lexikographisch, kleiner = besser):
    ``(prior_index, abs(dy), abs(dx))``.

    Spezifische Priors stehen in der Liste vorne und gewinnen automatisch —
    "Kontostand nach Zinsabschluss" schlägt das generische "Kontostand".
    Kein Match → ``None``; Aufrufer fällt dann auf den ersten Hit zurück.
    """
    best: tuple[int, float, float, TaggedWord] | None = None
    for prior_idx, label in enumerate(label_priors):
        label_spans = _find_label_spans(items, label)
        if not label_spans:
            continue
        for (lx0, ltop, lx1, lbot) in label_spans:
            for hit in hits:
                vx0, vtop, vx1, vbot = hit.bbox
                dy = abs(vtop - ltop)
                dx = abs(vx0 - lx0)
                score = (prior_idx, dy, dx)
                if best is None or score < best[:3]:
                    best = (prior_idx, dy, dx, hit)
    return best[3] if best is not None else None


def _verbatim_fallback(
    value_str: str,
    tag_map: dict[int, TaggedWord],
    label_priors: list[str] | None = None,
) -> tuple[str, list[BBox]] | None:
    """Sucht ``value_str`` verbatim in der ``tag_map`` und liefert MINIMAL-Anker.

    Wird aufgerufen, wenn der LLM keine ``tag_refs`` gesetzt hat oder die
    gelieferten Tag-IDs ins Leere zeigen. Strategie in zwei Stufen:

    1. **Single-Token-Match** — wenn ein einzelnes Wort nach Normalisierung
       exakt dem Wert entspricht, dieses Wort zurückgeben. Bei mehreren
       Treffern UND ``label_priors``: wähle den räumlich nächsten Treffer
       zu einem priorisierten Label (Codex-Befund: bei Bank-Kontoauszügen
       mit running balance saß der erste Hit oft in der Saldovortrag-Spalte
       statt in der Endsaldo-Zeile).

    2. **Minimales Mehr-Wort-Fenster** — sonst über alle Startpositionen
       iterieren und für jede das kleinste Fenster finden, dessen
       normalisierte Konkatenation den Wert enthält. Aus allen Fundstellen
       wird das *insgesamt kleinste* Fenster gewählt (lokalisiert den
       Anker präzise).

    Wichtig: das frühere "first hit"-Verhalten lieferte zwar einen valid-
    Anker, aber die erste BBox des Fensters lag oft mehrere Wörter VOR
    dem tatsächlichen Wert (Codex-Review: "Anker zeigt auf irgendein Wort
    in der Nähe statt auf den Wert selbst"). Mit minimalem Fenster zeigt
    ``bboxes[0]`` jetzt auf das erste Wort des Werts.
    """
    norm_value = normalize(value_str)
    if not norm_value:
        return None

    # Sortierte Wort-Liste (Tag-ID = Lese-Reihenfolge).
    items = sorted(tag_map.items(), key=lambda kv: kv[0])
    n = len(items)
    if n == 0:
        return None

    # Stufe 1: Single-Token-Exakt-Match (häufigster Fall für Beträge/Zahlen).
    # Alle Treffer sammeln; bei Mehrfach + Label-Priors räumlich auswählen.
    single_hits: list[TaggedWord] = [
        tw for _, tw in items if normalize(tw.text) == norm_value
    ]
    if single_hits:
        if len(single_hits) == 1 or not label_priors:
            chosen = single_hits[0]
        else:
            chosen = _select_by_label_priors(single_hits, items, label_priors)
            if chosen is None:
                chosen = single_hits[0]
        return chosen.text, [chosen.bbox]

    # Stufe 2: Minimales Mehr-Wort-Fenster suchen.
    # Über alle Startpositionen iterieren; pro Start das kleinste Fenster
    # finden, das den Wert enthält. Aus den Kandidaten den global kleinsten
    # Fund (kleinste Anzahl Wörter) wählen. Bei Gleichstand der erste —
    # das ist die kanonische Lesereihenfolge.
    best_start = -1
    best_size = n + 1
    for start in range(n):
        snippet_parts: list[str] = []
        for offset in range(_VERBATIM_MAX_WINDOW):
            idx = start + offset
            if idx >= n:
                break
            _, tw = items[idx]
            snippet_parts.append(tw.text)
            window_text = " ".join(snippet_parts)
            norm_window = normalize(window_text)
            if not norm_window:
                continue
            if norm_value in norm_window:
                size = offset + 1
                if size < best_size:
                    best_size = size
                    best_start = start
                break  # innere Schleife: dieses Fenster ist minimal für diesen Start

    if best_start < 0:
        # Stufe 3 (defensiv): Backward-Match — Fenster IST substanzieller Teil
        # des Werts (LLM-Klartext enthält Zusatz, z.B. "ACME AG, 8000 Zürich"
        # vs. Tag "ACME AG"). Selten gebraucht; nur 50%-Schwelle.
        for start in range(n):
            snippet_parts = []
            bboxes_acc: list[BBox] = []
            for offset in range(_VERBATIM_MAX_WINDOW):
                idx = start + offset
                if idx >= n:
                    break
                _, tw = items[idx]
                snippet_parts.append(tw.text)
                bboxes_acc.append(tw.bbox)
                window_text = " ".join(snippet_parts)
                norm_window = normalize(window_text)
                threshold = max(3, len(norm_value) // 2)
                if norm_window and norm_window in norm_value and len(norm_window) >= threshold:
                    return window_text, bboxes_acc
        return None

    # Trimming (defensiv): Sollte bei minimaler Suche schon optimal sein,
    # aber lassen wir's stehen für den Fall, dass Normalisierung Whitespace
    # frisst und mehrere Größen "gleich" sind.
    snippet_words = [items[best_start + i][1].text for i in range(best_size)]
    bboxes = [items[best_start + i][1].bbox for i in range(best_size)]
    snippet = " ".join(snippet_words)
    return snippet, bboxes


def join_space_thousands(
    value_str: str,
    tag_map: dict[int, TaggedWord],
) -> tuple[str, list[BBox]] | None:
    """Korrigiert einen space-getrennten Tausenderbetrag (Finding E1).

    Schweizer PDFs trennen Tausender oft mit (NB)Space statt Apostroph; der
    LLM/Single-Token-Match erfasst dann nur den Rumpf ("652.55") und verliert
    den Tausenderblock ("24"). Diese Funktion sucht den als ``value_str``
    gelieferten Rumpf als Single-Token in der ``tag_map`` und prüft, ob das in
    Lese-Reihenfolge UNMITTELBAR vorangehende Token ein 1-3-stelliges
    Ziffern-Präfix in derselben Zeile (|dy| < 3pt) und mit kleinem x-Abstand
    ist. Wenn ja, wird der gejointe Betrag ("24 652.55" = 24652.55) als
    korrigierter Wert mit den BBoxes beider Tokens zurückgegeben.

    Rückgabe ``(korrigierter_wert, [prefix_bbox, body_bbox])`` oder ``None``,
    wenn kein gültiger Join möglich ist (kein Rumpf-Match, Präfix fehlt, andere
    Zeile, zu grosser Abstand, oder Ergebnis nicht parsbar).

    Konservativ: greift nur, wenn der Rumpf exakt 3 Ganzzahlstellen hat
    (``_THOUSANDS_BODY_RE``) — ein bereits vollständiger Betrag ("652.5") wird
    nicht angefasst.
    """
    body_norm = normalize(value_str)
    if not body_norm:
        return None
    # Der Rumpf muss die 3-Stellen-Tausenderblock-Form haben.
    if not _THOUSANDS_BODY_RE.match(value_str.strip()):
        return None

    items = sorted(tag_map.items(), key=lambda kv: kv[0])
    n = len(items)
    for idx in range(1, n):
        _, body_tw = items[idx]
        if normalize(body_tw.text) != body_norm:
            continue
        _, prefix_tw = items[idx - 1]
        if not _THOUSANDS_PREFIX_RE.match(prefix_tw.text.strip()):
            continue
        # Gleiche Zeile? Kleiner horizontaler Abstand?
        dy = abs(body_tw.bbox[1] - prefix_tw.bbox[1])
        dx = body_tw.bbox[0] - prefix_tw.bbox[2]  # Lücke zwischen Präfix-Ende und Rumpf-Start
        if dy > _SAME_ROW_DY:
            continue
        if dx < 0 or dx > _THOUSANDS_MAX_DX:
            continue
        # Ein Rumpf mit weniger als drei Ganzzahlstellen ist keine
        # Tausendergruppe, sondern eine von pdfplumber an einer Kerning-Luecke
        # zerteilte Zahl. Solche Luecken sind winzig (beobachtet: 2.67pt),
        # waehrend echte Spalten weit auseinanderliegen. Deshalb hier eine
        # enge Schranke — sonst wuerden Nachbarspalten zusammenwachsen.
        if not _THOUSANDS_FULL_GROUP_RE.match(body_tw.text.strip()) \
                and dx > _KERNING_MAX_DX:
            continue
        joined_text = f"{prefix_tw.text.strip()} {body_tw.text.strip()}"
        try:
            parsed = parse_swiss_amount(joined_text)
        except ValueError:
            continue
        if parsed is None:
            continue
        return str(parsed), [prefix_tw.bbox, body_tw.bbox]
    return None


def resolve_field(
    value_str: str,
    tag_refs: list[int],
    tag_map: dict[int, TaggedWord],
    label_priors: list[str] | None = None,
) -> tuple[bool, str, list[BBox]]:
    """Löst Tag-Referenzen in BBoxes auf und prüft Wert-Snippet-Match.

    Argumente:

    * ``value_str`` — der vom LLM extrahierte Wert (String).
    * ``tag_refs`` — Liste der Tag-IDs, die der LLM für diesen Wert belegt hat.
    * ``tag_map`` — vom Tokenizer gelieferte Map ``tag_id -> TaggedWord``.

    Rückgabe ``(anchor_valid, snippet, bboxes)``:

    * ``anchor_valid: bool`` — ``True`` wenn der Anker den Wert nachweisbar
      belegt. Zwei Akzeptanz-Pfade nach Normalisierung (Plan 06 / Iteration B):

      1. **Forward-Match**: ``normalize(value)`` ist Substring von
         ``normalize(snippet)``. Beispiel: value ``"95'400.00"`` mit
         snippet ``"Bruttolohn total 95'400.00"`` — der LLM hat zusätzlich
         Label-Wörter getagged, der Wert steht IN der getaggten Region.

      2. **Backward-Match (under-tagged)**: ``normalize(snippet)`` ist
         Substring von ``normalize(value)`` UND der Snippet deckt
         mindestens 50 % der Wert-Länge ab (mindestens 3 Zeichen). Beispiel:
         value ``"BORDER GmbH, 4051 Basel"`` mit snippet ``"GmbH, 4051 Basel"``
         — der LLM hat ein Wort weggelassen, aber die getaggten BBoxes zeigen
         immer noch unstrittig auf die richtige Region.

      Strikte Exact-Equality wäre für die Anker-Semantik zu eng — Anker
      bedeutet "der Wert steht hier", nicht "der Span ist exakt der Wert".
      Die 50 %-Schwelle filtert Halluzinationen (LLM erfindet Wert,
      tag_refs zeigen auf irrelevantes Wort) heraus.

    * ``snippet: str`` — die durch die Tag-IDs verbundenen Original-Wörter
      (Leerzeichen-getrennt). Bei leerem oder unbekanntem ``tag_refs`` ist
      der Snippet ``""``.
    * ``bboxes: list[BBox]`` — die zugehörigen Bounding-Boxes in der
      Reihenfolge der ``tag_refs``.

    Edge-Cases:

    * Leere ``tag_refs`` oder eine Tag-ID, die nicht in ``tag_map``
      vorkommt → ``(False, "", [])``. Aufrufer setzen den Reason-Code
      ``anchor_resolution_failed``.
    * Leerer ``value_str`` (nach Normalisierung) → ``valid=False``.
    """
    # Leere tag_refs (vereinfachter Modus ohne Tags) → direkt zum Verbatim-Fallback.
    # Ungültige tag_refs (IDs fehlen in tag_map) → ebenfalls Verbatim-Fallback.
    norm_value = normalize(value_str)
    if not norm_value:
        return False, "", []

    if not tag_refs or any(t not in tag_map for t in tag_refs):
        # Vereinfachter Modus: keine Tags geliefert → Verbatim-Fallback direkt.
        fb = _verbatim_fallback(value_str, tag_map, label_priors)
        if fb is not None:
            fb_snippet, fb_bboxes = fb
            return True, fb_snippet, fb_bboxes
        return False, "", []

    snippet_words = [tag_map[t].text for t in tag_refs]
    snippet = " ".join(snippet_words)
    bboxes: list[BBox] = [tag_map[t].bbox for t in tag_refs]
    norm_snippet = normalize(snippet)
    forward_match = norm_value in norm_snippet
    # Backward-Match: snippet ist substantieller Teil des Werts (≥50% Länge, ≥3 Zeichen).
    # Filtert Halluzinationen heraus (1-2-Zeichen-Snippets, die zufällig in fast jedem Wert vorkommen).
    threshold = max(3, len(norm_value) // 2)
    backward_match = (
        norm_snippet in norm_value and len(norm_snippet) >= threshold
    )
    valid = forward_match or backward_match
    return valid, snippet, bboxes
