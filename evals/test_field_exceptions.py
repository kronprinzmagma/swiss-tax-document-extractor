"""Eval-Test für den field_exceptions-Mechanismus in ``process_samples_full``.

Deterministisch (kein ``llm_full``-Marker): die LLM-Extraktion wird durch ein
synthetisches ``BankZinsausweisRaw`` ersetzt (monkeypatch ``extract``), es läuft
kein Ollama. Nur Fantasy-Daten (Hans Muster, BANK-T) — Privacy.

Geprüft wird:
  - Ein gepflegtes ``field_exceptions.json`` setzt für gelistete Felder den Wert
    auf ``not_in_beleg_by_design`` und die ``inference_source`` auf
    ``user_verified:<grund>``.
  - Die Exception GEWINNT gegen die Bank-Ertrags-Heuristik (sie läuft danach):
    auch wenn die Heuristik sonst ``manual_review:bruttoertrag_transaction_line``
    setzen würde, ist der finale Wert ``not_in_beleg_by_design``.
"""
from __future__ import annotations

import json
from pathlib import Path

import scripts.process_samples_full as psf
from extractors.schema import BankZinsausweisRaw, TaggedField


def _word(text: str, idx: int) -> dict:
    """Ein synthetisches Wort mit BBox (eine Zeile, fortlaufend nach rechts)."""
    x0 = 10.0 + idx * 30.0
    return {"text": text, "x0": x0, "top": 10.0, "x1": x0 + 28.0, "bottom": 22.0}


def _bank_json(stem_text: str) -> dict:
    """Minimales bank_zinsausweis-Word-JSON. ``stem_text`` steuert den Plaintext
    (für die Klassifikation und die Bank-Ertrags-Heuristik)."""
    tokens = stem_text.split()
    return {
        "pdf_name": "synthetisch.pdf",
        "pages": [
            {
                "page_num": 1,
                "words": [_word(t, i) for i, t in enumerate(tokens)],
            }
        ],
    }


def _make_raw(**overrides) -> BankZinsausweisRaw:
    """Synthetisches LLM-Ergebnis (Fantasy-Daten)."""
    base = dict(
        institut=TaggedField(value="BANK-T", tag_refs=[]),
        kontoinhaber_name=TaggedField(value="Hans Muster", tag_refs=[]),
        bruttoertrag=TaggedField(value="", tag_refs=[]),
        vermoegensstand_3112=TaggedField(value="1234.50", tag_refs=[]),
        verrechnungssteuer=TaggedField(value="", tag_refs=[]),
        jahr=TaggedField(value="2022", tag_refs=[]),
    )
    base.update(overrides)
    return BankZinsausweisRaw(**base)


def test_exception_setzt_not_in_beleg_by_design(tmp_path, monkeypatch):
    # Plaintext OHNE Ertrags-Label → bruttoertrag bleibt sowieso leer; die
    # Exception macht daraus den bewussten by-design-Marker.
    json_doc = _bank_json("Zinsausweis Kapitalbescheinigung Saldo Hans Muster")
    json_path = tmp_path / "synthetisch.json"
    json_path.write_text(json.dumps(json_doc))
    (tmp_path / "field_exceptions.json").write_text(
        json.dumps({"synthetisch": {"bruttoertrag": "kein zinsausweis, nur kontosaldi"}})
    )

    monkeypatch.setattr(psf, "extract", lambda *a, **k: _make_raw())
    result = psf.process_json_sample(json_path)

    fmap = {f["feld"]: f for f in result["fields"]}
    assert fmap["bruttoertrag"]["value"] == "not_in_beleg_by_design"
    assert fmap["bruttoertrag"]["inference_source"] == (
        "user_verified:kein zinsausweis, nur kontosaldi"
    )
    # Kein Anker, keine bbox/page (by-design abwesend).
    assert fmap["bruttoertrag"]["anchor_valid"] is False
    assert fmap["bruttoertrag"]["bbox"] is None


def test_exception_gewinnt_gegen_heuristik(tmp_path, monkeypatch):
    # Plaintext MIT Ertrags-Label ("Dividende") und einem bruttoertrag-Wert, den
    # die Bank-Heuristik (kein Anker) sonst als
    # manual_review:value_not_in_document_* / *_transaction_line behandeln würde.
    # Die Exception muss trotzdem gewinnen.
    json_doc = _bank_json("Zinsausweis Dividende Ertrag Gutschrift Hans Muster")
    json_path = tmp_path / "synthetisch.json"
    json_path.write_text(json.dumps(json_doc))
    (tmp_path / "field_exceptions.json").write_text(
        json.dumps({"synthetisch": {"bruttoertrag": "genussscheine ohne zinsertrag"}})
    )

    monkeypatch.setattr(
        psf, "extract",
        lambda *a, **k: _make_raw(
            bruttoertrag=TaggedField(value="999.99", tag_refs=[])
        ),
    )
    result = psf.process_json_sample(json_path)

    fmap = {f["feld"]: f for f in result["fields"]}
    assert fmap["bruttoertrag"]["value"] == "not_in_beleg_by_design"
    assert fmap["bruttoertrag"]["inference_source"] == (
        "user_verified:genussscheine ohne zinsertrag"
    )


# --- Finding 5: stale-exception-Leitplanke + Anhang-Doku ---------------------

def test_stale_exception_wird_uebersprungen(tmp_path, monkeypatch, capsys):
    # bruttoertrag hat einen echten Wert MIT Anker (tag_refs) → die Exception
    # ist vermutlich veraltet und darf den echten Wert NICHT überschreiben.
    # Plaintext MIT Ertrags-Label, damit der echte bruttoertrag-Wert nicht von
    # der Halluzinations-Heuristik geblankt wird. tag_refs=[4] → echter Anker
    # auf das Token "500.00" (Index 4 im Plaintext).
    json_doc = _bank_json("Zinsausweis Bruttoertrag Hans Muster 500.00")
    json_path = tmp_path / "synthetisch.json"
    json_path.write_text(json.dumps(json_doc))
    (tmp_path / "field_exceptions.json").write_text(
        json.dumps({"synthetisch": {"bruttoertrag": "angeblich kein zins"}})
    )

    monkeypatch.setattr(
        psf, "extract",
        lambda *a, **k: _make_raw(
            bruttoertrag=TaggedField(value="500.00", tag_refs=[4])
        ),
    )
    result = psf.process_json_sample(json_path)
    err = capsys.readouterr().err

    fmap = {f["feld"]: f for f in result["fields"]}
    # Echter Wert bleibt erhalten, NICHT by-design überschrieben.
    assert fmap["bruttoertrag"]["value"] == "500.00"
    assert fmap["bruttoertrag"]["value"] != "not_in_beleg_by_design"
    assert "stale exception" in err
    # Skip-Zähler im Result durchgereicht.
    assert result["_field_exceptions"]["skipped"] == 1
    assert result["_field_exceptions"]["applied"] == 0


def test_applied_exception_zaehler(tmp_path, monkeypatch):
    json_doc = _bank_json("Zinsausweis Kapitalbescheinigung Hans Muster")
    json_path = tmp_path / "synthetisch.json"
    json_path.write_text(json.dumps(json_doc))
    (tmp_path / "field_exceptions.json").write_text(
        json.dumps({"synthetisch": {"bruttoertrag": "kein zins"}})
    )
    monkeypatch.setattr(psf, "extract", lambda *a, **k: _make_raw())
    result = psf.process_json_sample(json_path)
    assert result["_field_exceptions"]["applied"] == 1
    assert result["_field_exceptions"]["skipped"] == 0


def test_anhang_dokumentiert_angewandte_exception():
    # render_anhang dokumentiert by-design-Felder (user_verified:*) pro Beleg.
    import scripts.build_tax_output as bto
    entry = {
        "pdf_name": "synthetisch.pdf",
        "status": "ok",
        "belegtyp": "bank_zinsausweis",
        "fields": [
            {"feld": "bruttoertrag", "value": "not_in_beleg_by_design",
             "inference_source": "user_verified:kein zins", "anchor_valid": False},
        ],
    }
    md = bto.render_anhang([], Path("evals/samples_x"), entries=[entry])
    assert "Bewusste Abwesenheit (user-verifiziert)" in md
    assert "bruttoertrag" in md
    assert "kein zins" in md
