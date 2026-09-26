"""Unit-Tests für extractors/anchor_resolver.py — Apostroph-Äquivalenz und Mismatch-Detection.

Diese Tests sind das Gate für F2-Anchor-Validity: wenn die Apostroph- und
NBSP-Äquivalenzen hier scheitern, kollabiert die F2-Quellensteuer-Fixture
(U+2019-Beträge) im End-to-End-Lauf.
"""
from extractors.anchor_resolver import join_space_thousands, resolve_field
from extractors.tokenize import TaggedWord


def _tw(tag_id: int, text: str) -> TaggedWord:
    return TaggedWord(tag_id=tag_id, page=1, bbox=(0.0, 0.0, 1.0, 1.0), text=text)


def _twb(tag_id: int, text: str, bbox) -> TaggedWord:
    return TaggedWord(tag_id=tag_id, page=1, bbox=bbox, text=text)


def test_resolve_simple_match():
    tm = {0: _tw(0, "ACME"), 1: _tw(1, "AG")}
    valid, snippet, bboxes = resolve_field("ACME AG", [0, 1], tm)
    assert valid
    assert snippet == "ACME AG"
    assert len(bboxes) == 2


def test_resolve_apostroph_equivalence():
    # PDF hat U+2019, LLM-Output hat U+0027 — MUSS valid resolven
    tm = {5: _tw(5, "1’234.50")}
    valid, _, _ = resolve_field("1'234.50", [5], tm)
    assert valid


def test_resolve_nbsp_equivalence():
    tm = {0: _tw(0, "1 234.50")}  # NBSP
    assert resolve_field("1'234.50", [0], tm)[0]


def test_resolve_nnbsp_equivalence():
    tm = {0: _tw(0, "1 234.50")}  # NNBSP (U+202F)
    assert resolve_field("1'234.50", [0], tm)[0]


def test_resolve_mismatch():
    tm = {0: _tw(0, "ACME")}
    valid, snippet, bboxes = resolve_field("anderer Wert", [0], tm)
    assert not valid
    assert snippet == "ACME"
    assert bboxes == [(0.0, 0.0, 1.0, 1.0)]


def test_resolve_empty_refs():
    valid, snippet, bboxes = resolve_field("X", [], {})
    assert not valid
    assert snippet == ""
    assert bboxes == []


def test_resolve_unknown_tag():
    tm = {0: _tw(0, "ACME")}
    valid, snippet, bboxes = resolve_field("X", [99], tm)
    assert not valid
    assert snippet == ""
    assert bboxes == []


def test_resolve_case_insensitive():
    tm = {0: _tw(0, "ACME"), 1: _tw(1, "AG")}
    valid, _, _ = resolve_field("acme ag", [0, 1], tm)
    assert valid


# --- Plan 06 / Iteration B: Backward-Match für under-tagged Anker -----------


def test_resolve_undertagged_first_token_missing():
    """LLM lässt erstes Wort weg ("BORDER" → tag_refs nur auf "GmbH, 4051 Basel").
    Backward-Match: snippet ist Substring von value, ≥50%-Schwelle erfüllt.
    """
    tm = {0: _tw(0, "GmbH,"), 1: _tw(1, "4051"), 2: _tw(2, "Basel")}
    valid, snippet, _ = resolve_field("BORDER GmbH, 4051 Basel", [0, 1, 2], tm)
    assert valid
    assert snippet == "GmbH, 4051 Basel"


def test_resolve_undertagged_last_token_missing():
    """LLM lässt letztes Wort weg ("Muster" → tag_refs nur auf "Hans und Maria").
    Backward-Match: snippet ist Substring von value, ≥50%-Schwelle erfüllt.
    """
    tm = {0: _tw(0, "Hans"), 1: _tw(1, "und"), 2: _tw(2, "Maria")}
    valid, _, _ = resolve_field("Hans und Maria Muster", [0, 1, 2], tm)
    assert valid


def test_resolve_backward_match_too_short_rejected():
    """Halluzinations-Filter: snippet zu kurz für Backward-Match (< 50% des Werts).
    Z.B. value=`'BORDER GmbH AG'` (15 norm), snippet=`'AG'` (2 norm) → 2 < 7 (50%-Hälfte).
    """
    tm = {0: _tw(0, "AG")}
    valid, _, _ = resolve_field("BORDER GmbH AG", [0], tm)
    assert not valid


def test_resolve_backward_match_min_3_chars():
    """Mindest-Schwelle 3 Zeichen, damit 1-2-Zeichen-Snippets nicht trivial valid sind."""
    tm = {0: _tw(0, "X")}
    valid, _, _ = resolve_field("XY", [0], tm)
    assert not valid  # snippet len 1 < threshold 3


# --------------------------------------------------------------------------- #
# E1 — Space-Tausender-Join (Quick 260612-l2g). Synthetische TaggedWord-Listen. #
# --------------------------------------------------------------------------- #


def test_join_space_thousands_joint_24_652():
    """'24' (x0=100) + '652.55' (x0=120, gleiche Zeile) → 24652.55."""
    tm = {
        0: _twb(0, "24", (100.0, 50.0, 110.0, 60.0)),
        1: _twb(1, "652.55", (120.0, 50.0, 145.0, 60.0)),
    }
    res = join_space_thousands("652.55", tm)
    assert res is not None
    value, bboxes = res
    assert value == "24652.55"
    assert len(bboxes) == 2


def test_join_space_thousands_weit_entfernter_vorgaenger_kein_join():
    """Präfix in anderer Spalte (grosser x-Abstand) → kein Join, bleibt 652.55."""
    tm = {
        0: _twb(0, "24", (10.0, 50.0, 20.0, 60.0)),
        1: _twb(1, "652.55", (300.0, 50.0, 325.0, 60.0)),  # 280pt Lücke
    }
    assert join_space_thousands("652.55", tm) is None


def test_join_space_thousands_andere_zeile_kein_join():
    """Präfix in anderer Zeile (top-Differenz > 3pt) → kein Join."""
    tm = {
        0: _twb(0, "24", (100.0, 20.0, 110.0, 30.0)),   # andere Zeile
        1: _twb(1, "652.55", (120.0, 50.0, 145.0, 60.0)),
    }
    assert join_space_thousands("652.55", tm) is None


def test_join_space_thousands_kein_dreistelliger_rumpf_kein_join():
    """Rumpf hat nicht exakt 3 Ganzzahlstellen ('52.55') → kein Join."""
    tm = {
        0: _twb(0, "24", (100.0, 50.0, 110.0, 60.0)),
        1: _twb(1, "52.55", (120.0, 50.0, 145.0, 60.0)),
    }
    assert join_space_thousands("52.55", tm) is None


def test_join_space_thousands_kein_ziffern_praefix():
    """Vorgänger ist kein Ziffern-Präfix ('Total') → kein Join."""
    tm = {
        0: _twb(0, "Total", (100.0, 50.0, 115.0, 60.0)),
        1: _twb(1, "652.55", (120.0, 50.0, 145.0, 60.0)),
    }
    assert join_space_thousands("652.55", tm) is None


# --- Zerteilte Betraege an Kerning-Luecken (260904-rmx) --------------------
#
# pdfplumber teilt rechtsbuendige Betraege gelegentlich mitten in der Zahl.
# Beobachtet in BANK-P-Zinsabschluessen: "3" + "48.09" wurde als 48.09
# gemeldet — eine Groessenordnung zu klein, verankert und gruen. Die
# Tausender-Logik griff nicht, weil sie exakt 3 Ganzzahlstellen verlangte.

def test_join_zerteilter_betrag_zwei_stellen():
    tm = {
        1: TaggedWord(1, 1, (527.0, 500.0, 532.0, 510.0), "3"),
        2: TaggedWord(2, 1, (535.0, 500.0, 560.0, 510.0), "71.88"),
    }
    res = join_space_thousands("71.88", tm)
    assert res is not None, "zerteilter Betrag muss zusammengefuegt werden"
    assert res[0].replace("'", "") == "371.88"


def test_join_zerteilter_betrag_eine_stelle():
    tm = {
        1: TaggedWord(1, 1, (522.0, 400.0, 532.0, 410.0), "29"),
        2: TaggedWord(2, 1, (535.0, 400.0, 555.0, 410.0), "9.27"),
    }
    res = join_space_thousands("9.27", tm)
    assert res is not None
    assert res[0].replace("'", "") == "299.27"


def test_telefonnummer_wird_nicht_zum_betrag():
    """Reine Ziffernblocks ohne Dezimalstellen duerfen nie zusammenwachsen."""
    tm = {
        1: TaggedWord(1, 1, (100.0, 700.0, 115.0, 710.0), "044"),
        2: TaggedWord(2, 1, (118.0, 700.0, 133.0, 710.0), "000"),
    }
    assert join_space_thousands("000", tm) is None


def test_echte_tausendergruppe_weiterhin():
    tm = {
        1: TaggedWord(1, 1, (522.0, 300.0, 532.0, 310.0), "24"),
        2: TaggedWord(2, 1, (535.0, 300.0, 570.0, 310.0), "652.55"),
    }
    res = join_space_thousands("652.55", tm)
    assert res is not None
    assert res[0].replace("'", "") == "24652.55"


def test_vollstaendiger_betrag_wird_nicht_angefasst():
    """Ein Betrag mit Tausendergruppe im Token ist fertig."""
    tm = {
        1: TaggedWord(1, 1, (100.0, 300.0, 130.0, 310.0), "Saldo"),
        2: TaggedWord(2, 1, (535.0, 300.0, 570.0, 310.0), "1'234.50"),
    }
    assert join_space_thousands("1'234.50", tm) is None
