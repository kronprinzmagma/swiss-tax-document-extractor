"""Jedes eingelesene Dokument muss in der Review auffindbar sein (260904-rmx).

Ein Dokument kann erfolgreich extrahiert werden und trotzdem ohne sichtbare
Zeile enden: alle Betraege null, alles gestrichen, alles out-of-scope. Bisher
verschwand es dann vollstaendig — man konnte dort weder etwas nachtragen noch
es als irrelevant abhaken. Es war nicht einmal auffindbar, um zu merken, dass
etwas fehlt.
"""
from __future__ import annotations

import json

from scripts.build_review_html import _typ_label, sammle_offene_dokumente


def lege(tmp_path, eintraege):
    (tmp_path / "_results_full.json").write_text(json.dumps(eintraege))
    return tmp_path


def test_dokument_ohne_zeile_erscheint_oben(tmp_path):
    s = lege(tmp_path, [{"pdf_name": "a.pdf", "status": "ok",
                         "belegtyp": "saeule_3a", "fields": []}])
    offen = sammle_offene_dokumente(s, None, mit_zeilen=set())
    assert [d["beleg"] for d in offen] == ["a.pdf"]
    assert "keine übertragbare Position" in offen[0]["grund"]


def test_dokument_mit_zeile_erscheint_nicht(tmp_path):
    s = lege(tmp_path, [{"pdf_name": "a.pdf", "status": "ok",
                         "belegtyp": "saeule_3a", "fields": []}])
    assert sammle_offene_dokumente(s, None, mit_zeilen={"a.pdf"}) == []


def test_ohne_angabe_bleibt_das_alte_verhalten(tmp_path):
    """Aufrufer, die nicht wissen, welche Dokumente Zeilen haben, bekommen
    weiterhin nur die gescheiterten."""
    s = lege(tmp_path, [{"pdf_name": "a.pdf", "status": "ok",
                         "belegtyp": "saeule_3a", "fields": []},
                        {"pdf_name": "b.pdf", "status": "unknown_belegtyp",
                         "fields": []}])
    assert [d["beleg"] for d in sammle_offene_dokumente(s, None)] == ["b.pdf"]


def test_gescheiterte_behalten_ihren_grund(tmp_path):
    s = lege(tmp_path, [{"pdf_name": "b.pdf", "status": "unknown_belegtyp",
                         "fields": []}])
    offen = sammle_offene_dokumente(s, None, mit_zeilen=set())
    assert offen[0]["grund"] == "Belegtyp nicht erkannt"


# --- Gruppierung ------------------------------------------------------------

def test_bekannte_belegart_gewinnt():
    assert _typ_label({"belegtyp": "kk_praemienbescheinigung",
                       "ziffer": "15"}) == "Krankenkasse"


def test_ohne_belegart_greift_die_ziffer():
    """Eine auf VVG umgestellte Zeile unter 'Übrige' zu finden ist irritierend."""
    assert _typ_label({"belegtyp": "", "ziffer": "15"}) == \
        "Versicherungsprämien und Sparzinsen"


def test_ohne_beides_bleibt_uebrige():
    assert _typ_label({"belegtyp": "", "ziffer": ""}) == "Übrige"
