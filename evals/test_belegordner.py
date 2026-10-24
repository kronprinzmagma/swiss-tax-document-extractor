"""Unterordner im Eingangsordner (2026-10-24, vom Nutzer angelegt).

Vorher sah jedes Skript nur eine Ebene; ein Dokument in „Konten/" war für
Einlesen, Bereitschaft und Review unsichtbar — ohne Meldung.
"""
from __future__ import annotations

from pathlib import Path

from extractors.belegordner import (aussortierte_pdfs, alle_pdfs,
                                    doppelte_namen, finde_pdf)


def baum(tmp_path: Path) -> Path:
    for rel in ("ohne_ordner.pdf", "Konten/zins.pdf", "Konten/2025/saldo.pdf",
                "Wertschriften/depot.PDF", "irrelevant/weg.pdf",
                "Konten/Irrelevant/auch_weg.pdf", ".versteckt/nein.pdf",
                "Konten/notiz.txt"):
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"%PDF-1.4\n")
    return tmp_path


def test_alle_unterordner_ohne_irrelevant_und_versteckte(tmp_path):
    namen = [p.name for p in alle_pdfs(baum(tmp_path))]
    assert sorted(namen) == ["depot.PDF", "ohne_ordner.pdf", "saldo.pdf", "zins.pdf"]


def test_irrelevant_auf_jeder_ebene(tmp_path):
    namen = sorted(p.name for p in aussortierte_pdfs(baum(tmp_path)))
    assert namen == ["auch_weg.pdf", "weg.pdf"]


def test_finde_pdf_im_baum_und_nicht_ausserhalb(tmp_path):
    ordner = baum(tmp_path)
    assert finde_pdf(ordner, "saldo.pdf") == ordner / "Konten/2025/saldo.pdf"
    assert finde_pdf(ordner, "weg.pdf") == ordner / "irrelevant/weg.pdf"
    assert finde_pdf(ordner, "../../etc/passwd") is None
    assert finde_pdf(ordner, "gibtsnicht.pdf") is None


def test_doppelte_namen_werden_gemeldet(tmp_path):
    ordner = baum(tmp_path)
    (ordner / "Wertschriften/zins.pdf").write_bytes(b"%PDF-1.4\n")
    assert set(doppelte_namen(ordner)) == {"zins.pdf"}


def test_dokumente_check_sieht_unterordner(tmp_path):
    from scripts.dokumente_check import _stationen

    ordner = baum(tmp_path / "eingang")
    samples = tmp_path / "json"
    samples.mkdir()
    stand = _stationen(ordner, samples)
    assert stand["saldo.pdf"]["im_ordner"] is True
    assert stand["weg.pdf"]["aussortiert"] is True


def test_aussortieren_und_zurueck_in_den_unterordner(tmp_path):
    from scripts import aussortieren

    ordner = baum(tmp_path / "eingang")
    samples = tmp_path / "json"
    samples.mkdir()
    getan = aussortieren.fuehre_aus(samples, ordner, ["saldo.pdf"])
    assert (ordner / "irrelevant/saldo.pdf").is_file()
    assert getan[0]["von"] == "Konten/2025"
    aussortieren.zurueck(samples, ordner)
    assert (ordner / "Konten/2025/saldo.pdf").is_file()


def test_review_server_liefert_pdf_aus_unterordner(tmp_path):
    from scripts import review_server

    ordner = baum(tmp_path / "eingang")

    class Antwort(Exception):
        pass

    class Handler:
        zustand = type("Z", (), {"pdf_dir": ordner})()
        status = None

        def _antwort(self, code, *_):
            self.status = code
            raise Antwort

        def send_response(self, code):
            self.status = code

        def send_header(self, *_):
            pass

        def end_headers(self):
            pass

        wfile = type("W", (), {"write": staticmethod(lambda b: None)})()

    h = Handler()
    review_server.Handler._pdf(h, "saldo.pdf")
    assert h.status == 200
    try:
        review_server.Handler._pdf(h, "..%2F..%2Fgeheim.pdf")
    except Antwort:
        pass
    assert h.status == 404


def test_abgleich_haelt_genau_ein_json_je_pdf(tmp_path):
    """Ein JSON unter einem frueheren Namen zum selben PDF fliegt raus —
    sonst wird das Dokument doppelt extrahiert (261024-jga)."""
    import json

    from scripts.pdf_to_word_json import pdf_fingerabdruck
    from scripts.run_productive_local import _json_abgleich

    eingang = baum(tmp_path / "eingang")
    pdf = eingang / "Konten/zins.pdf"
    js = tmp_path / "json"
    js.mkdir()
    inhalt = {"pdf_name": "zins.pdf", "pdf_fingerabdruck": pdf_fingerabdruck(pdf),
              "pages": []}
    (js / "zins.json").write_text(json.dumps(inhalt))
    (js / "zins_alt_name.json").write_text(json.dumps(inhalt))
    entfernt, behalten = _json_abgleich(eingang, js)
    assert behalten == 1 and entfernt == 1
    assert (js / "zins.json").exists() and not (js / "zins_alt_name.json").exists()


def test_klassifikation_ganzes_dokument_und_ocr_tolerant():
    """Begleitbrief auf S. 1, Formular 21 auf S. 2; OCR-„L®hnausweis";
    „Zinsabrechnung" mit Festhypothek ist eine Hypothek (261024-jga)."""
    from unittest import mock

    from extractors.classifier import classify

    def worte(text):
        return [{"text": t} for t in text.split()]

    brief = " ".join(["Sehr geehrter Herr, in der Beilage die Unterlagen."] * 40)
    with mock.patch("extractors.llm_classifier.classify_via_llm", side_effect=Exception):
        assert classify(worte(brief + " Form. 21 Bescheinigung Vorsorgebeiträge"))[0] == "saeule_3a"
        assert classify(worte("A X L®hnausweis Bruttolohn total"))[0] == "lohnausweis"
        assert classify(worte("Festhypothek Zinsabrechnung vom 01.07.2025"))[0] == "hypothek_zinsbestaetigung"
        assert classify(worte("IBAN Zinsabrechnung vom 01.01.2025 Habenzins"))[0] == "bank_zinsausweis"
        assert classify(worte("Kanton Zürich Steuerauszug in CHF 31.12.2025"))[0] == "wertschriftenverzeichnis"
        assert classify(worte("Leistungen der Arbeitslosenversicherung Taggelder"))[0] == "unknown"


def test_zweizeilige_praemien_beschriftung_und_keine_kopie_als_kvg():
    """„Prämie" über „Total CHF 321.00": gilt als beschriftet; der gleiche
    Betrag vom Modell als KVG ist eine Kopie und fällt weg (261024-jga).
    Werte erfunden."""
    from scripts.process_samples_full import harden_kk_praemien

    words = [{"text": "Prämie", "x0": 68, "x1": 100, "top": 509, "page": 1},
             {"text": "Total", "x0": 68, "x1": 90, "top": 533, "page": 1},
             {"text": "CHF", "x0": 98, "x1": 115, "top": 533, "page": 1},
             {"text": "321.00", "x0": 172, "x1": 214, "top": 533, "page": 1}]
    box = [172, 533, 214, 544]
    felder = [{"feld": "praemie_total", "value": "321.00", "bbox": box, "page": 1,
               "inference_source": "regex:kk_praemie_total", "anchor_valid": True},
              {"feld": "praemie_kvg_total", "value": "321.00", "bbox": box,
               "page": 1, "inference_source": "text:bbox_anchor",
               "anchor_valid": True}]
    harden_kk_praemien(felder, words)
    assert felder[0]["value"] == "321.00"
    assert felder[1]["value"] == ""


def test_personenname_ohne_wohnort():
    from extractors.name_cleanup import clean_person_name

    assert clean_person_name("Maria Muster, 8000 Musterhausen") == "Maria Muster"
    assert clean_person_name("Muster, Maria") == "Muster, Maria"
