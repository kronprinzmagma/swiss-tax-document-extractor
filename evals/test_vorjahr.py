"""Steuerjahr trennen, abgeschlossenes Jahr sperren, Vorjahr vergleichen (261029).

der Anwender: „Jetzt ist klar, was jede Person für Arbeitgeber, Krankenkassen, Konten
etc. hat, und bei künftigen Läufen sollte das beachtet werden" — und: „bitte
nichts löschen". Werte erfunden bzw. aus dem anonymisierten Korpus.
"""
from __future__ import annotations

import io
import json
import sqlite3
import subprocess
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import pytest

from extractors import ablage, steuerjahr, vorjahr
from extractors.vorjahr import Posten

ROOT = Path(__file__).resolve().parent.parent
KORPUS = ROOT / "evals" / "samples_real_2025"


@pytest.fixture(scope="module")
def leiste():
    if not (KORPUS / "_results_full.json").exists():
        pytest.skip("Korpus 2025 fehlt")
    from scripts import export_extension
    with mock.patch("scripts.build_review_html.personennamen", return_value={}):
        return export_extension.baue(KORPUS, pdf_dir=None)


# --- Steuerjahr ----------------------------------------------------------------

def test_ausgabeordner_wie_im_makefile():
    for jahr in (2025, 2026):
        aus = subprocess.run(["make", "-s", "wo", f"STEUERJAHR={jahr}"], cwd=ROOT,
                             capture_output=True, text=True, check=True).stdout
        erwartet = steuerjahr.ausgabe_fuer(jahr).relative_to(ROOT)
        assert f"Ausgabe {erwartet}," in aus
    assert steuerjahr.ausgabe_fuer(2025).name == "latest"  # Altbestand bleibt


def test_schreibende_ziele_pruefen_das_jahr():
    text = (ROOT / "Makefile").read_text()
    for ziel in ("productive", "dokument", "einlesen", "reextract", "review", "korrekturen",
                 "streichen", "umbenennen", "zurueckrollen", "etax-bauen", "etax-weg"):
        koerper = text.split(f"\n{ziel}:", 1)[1].split("\n\n", 1)[0]
        assert "JAHR_OFFEN" in koerper, ziel


def test_abgeschlossen_sperrt_und_oeffnen_braucht_grund(tmp_path):
    ordner = tmp_path / "out"
    (ordner / "json").mkdir(parents=True)
    steuerjahr.pruefe_offen(ordner / "json", "Test")  # offen: nichts
    steuerjahr.schliesse_ab(ordner, 2025)
    with pytest.raises(steuerjahr.JahrAbgeschlossen, match="2025 ist abgeschlossen"):
        steuerjahr.pruefe_offen(ordner / "json", "der produktive Lauf")
    with pytest.raises(ValueError):
        steuerjahr.oeffne_wieder(ordner, "")
    ziel = steuerjahr.oeffne_wieder(ordner, "Nachtrag Arztrechnung")
    assert ziel.exists() and not (ordner / steuerjahr.MARKE).exists()  # verschoben, nicht weg
    steuerjahr.pruefe_offen(ordner, "Test")


def test_skripte_brechen_auf_abgeschlossenem_jahr_ab(tmp_path):
    ordner = tmp_path / "out"
    (ordner / "json").mkdir(parents=True)
    steuerjahr.schliesse_ab(ordner, 2025)
    aus = subprocess.run([str(ROOT / ".venv/bin/python"), "scripts/reextract.py",
                          "--samples", str(ordner / "json"), "--pdf", "x.pdf"],
                         cwd=ROOT, capture_output=True, text=True,
                         env={"PYTHONPATH": str(ROOT), "PATH": "/usr/bin:/bin"})
    assert aus.returncode != 0 and "abgeschlossen" in aus.stderr + aus.stdout


def test_ablage_ist_zu_bis_sie_mit_grund_geoeffnet_wird(tmp_path):
    samples = tmp_path / "json"
    samples.mkdir()
    db = ablage.oeffne(ablage.pfad_fuer(samples))
    db.execute("INSERT INTO position (id, dokument, zielwert, betrag, status) "
               "VALUES ('p0001', 'a.pdf', 'Nettolohn', '100.00', 'vorschlag')")
    db.commit()
    db.close()

    ablage.setze_jahr_zustand(samples, True, "abgeschlossen 2025")
    assert ablage.jahr_ist_zu(samples)
    db = ablage.oeffne(ablage.pfad_fuer(samples))
    for sql in ("INSERT INTO position (id, dokument, zielwert) VALUES ('p0002','b.pdf','x')",
                "UPDATE position SET betrag='1.00' WHERE id='p0001'",
                "DELETE FROM position WHERE id='p0001'",
                "DELETE FROM jahr_zustand"):
        with pytest.raises(sqlite3.DatabaseError):
            db.execute(sql)
    db.close()

    ablage.setze_jahr_zustand(samples, False, "Nachtrag")
    db = ablage.oeffne(ablage.pfad_fuer(samples))
    db.execute("UPDATE position SET betrag='1.00' WHERE id='p0001'")
    assert [r["zu"] for r in db.execute("SELECT zu FROM jahr_zustand ORDER BY lfd")] == [1, 0]
    db.close()


# --- Profil --------------------------------------------------------------------

def test_profil_aus_dem_korpus(leiste):
    posten = vorjahr.posten_der_leiste(leiste)
    formulare = {p.formular for p in posten}
    assert "Hilfsblatt 320" not in formulare
    assert {p.person for p in posten if p.formular == "Lohnausweis (100)"} == {
        "elternteil_1", "elternteil_2"}
    assert {p.person for p in posten if p.formular == "Versicherungsprämien"} == {
        "elternteil_1", "elternteil_2", "kind_1", "kind_2"}
    schulden = [p for p in posten if p.formular == "Schuldenverzeichnis"]
    assert len(schulden) == 3 and len({p.schluessel for p in schulden}) == 3
    assert all(p.person != "alle Personen" for p in posten)
    assert len({p.kurz for p in posten}) == len(posten)


def test_profil_schreiben_legt_das_alte_in_den_verlauf(tmp_path):
    p = [Posten("Lohnausweis (100)", "Lohnausweis (100)", "elternteil_1", "Firma A", "",
                {"Nettolohn": "80000.00"})]
    vorjahr.schreibe_profil(tmp_path, 2025, p)
    vorjahr.schreibe_profil(tmp_path, 2025, p)
    assert len(list((tmp_path / vorjahr.VERLAUF).glob("*"))) == 1
    assert vorjahr.lade_profil(tmp_path)[0].schluessel == p[0].schluessel


# --- Abgleich ------------------------------------------------------------------

def _lohn(person, firma, netto):
    return Posten("Lohnausweis (100)", "Lohnausweis (100)", person, firma, "",
                  {"Nettolohn": netto})


def _hypo(nr, satz, schuld="400000.00"):
    return Posten("Schuldenverzeichnis", "Schuld", "elternteil_1", "Musterbank", nr,
                  {"Zinssatz (gehört laut Wegleitung dazu)": satz, "Schuld am 31.12.": schuld})


def test_fehlt_neu_anders():
    alt = [_lohn("elternteil_1", "Firma A", "80000.00"), _lohn("elternteil_2", "Firma B", "60000.00"),
           _hypo("11 111.111.11", "1.40")]
    jetzt = [_lohn("elternteil_1", "Firma A", "130000.00"), _lohn("elternteil_2", "Firma C", "61000.00"),
             _hypo("11 111.111.11", "1.10")]
    b = {(x.art, x.posten.aussteller or x.posten.kennung, x.was) for x in
         vorjahr.vergleiche(alt, jetzt)}
    assert ("fehlt", "Firma B", "im Vorjahr da, jetzt nicht") in b
    assert ("neu", "Firma C", "im Vorjahr nicht da") in b
    assert ("anders", "Firma A", "Nettolohn: mehr als 50 % höher") in b
    assert ("anders", "Musterbank", "Zinssatz (gehört laut Wegleitung dazu): geändert") in b
    # Amortisation um wenige Prozent: kein Hinweis.
    assert not vorjahr.vergleiche([_hypo("1234 5", "1.4")],
                                  [_hypo("1234 5", "1.4", "390000.00")])


def test_gleiche_betraege_zwei_hypotheken_bleiben_zwei():
    alt = [_hypo("11 111.111.11", "1.40"), _hypo("22 222.222.22", "1.45")]
    jetzt = [_hypo("11 111.111.11", "1.40")]
    fehlt = [x for x in vorjahr.vergleiche(alt, jetzt) if x.art == "fehlt"]
    assert [x.posten.kennung for x in fehlt] == ["22 222.222.22"]


def test_quittung_erklaert_fehlt_und_ruecknahme_ist_ein_grabstein(tmp_path):
    alt = [_lohn("elternteil_2", "Firma B", "60000.00")]
    kurz = alt[0].kurz
    assert len(vorjahr.offen(vorjahr.vergleiche(alt, []))) == 1
    with pytest.raises(ValueError):
        vorjahr.quittiere(tmp_path, kurz, "")
    vorjahr.quittiere(tmp_path, kurz, "Stelle gewechselt")
    assert vorjahr.offen(vorjahr.vergleiche(alt, [], vorjahr.lade_quittungen(tmp_path))) == []
    vorjahr.quittiere(tmp_path, kurz, "", zurueck=True)
    assert len(vorjahr.offen(vorjahr.vergleiche(alt, [], vorjahr.lade_quittungen(tmp_path)))) == 1
    assert len(json.loads((tmp_path / vorjahr.QUITTUNGEN).read_text())) == 2  # nichts gelöscht


def test_pruefen_abschnitt_teilbar_und_rot_bei_fehlendem(leiste, tmp_path):
    from scripts.pflichtfelder_check import Maske, _namen_der_leiste, vorjahr_abschnitt

    posten = vorjahr.posten_der_leiste(leiste)
    extra = Posten("Bescheinigung der Vorsorgeeinrichtung", "x", "elternteil_1",
                   "Geheimstiftung Muster", "", {"Einzahlung Säule 3a": "7056.00"})
    vorjahr.schreibe_profil(tmp_path, 2024, posten + [extra])
    maske = Maske({}, _namen_der_leiste(leiste), voll=False)
    puffer = io.StringIO()
    with redirect_stdout(puffer):
        n = vorjahr_abschnitt(KORPUS, leiste, tmp_path, maske)
    aus = puffer.getvalue()
    assert n == 1
    assert extra.kurz in aus and "Geheimstiftung" not in aus and "7056" not in aus
    assert "vorjahr-quittieren" in aus

    with redirect_stdout(io.StringIO()):
        assert vorjahr_abschnitt(KORPUS, leiste, None, maske) == 0


def test_quittieren_nimmt_nur_kuerzel_aus_dem_vorjahr(tmp_path):
    """Das Beispiel V-1a2b3c aus der Anleitung wurde wörtlich quittiert (261029)."""
    jetzt, vor = tmp_path / "2026" / "json", tmp_path / "2025"
    jetzt.mkdir(parents=True)
    vor.mkdir()
    p = _lohn("elternteil_2", "Firma B", "60000.00")

    def lauf(*extra):
        return subprocess.run(
            [str(ROOT / ".venv/bin/python"), "scripts/vorjahr.py", "--samples", str(jetzt),
             "--jahr", "2026", "quittieren", *extra],
            cwd=ROOT, capture_output=True, text=True, env={"PYTHONPATH": str(ROOT)})

    assert lauf("--vorjahr", str(vor), "--kurz", p.kurz, "--grund", "x").returncode == 1
    vorjahr.schreibe_profil(vor, 2025, [p])
    falsch = lauf("--vorjahr", str(vor), "--kurz", "V-1a2b3c", "--grund", "Stelle gewechselt")
    assert falsch.returncode == 1 and "gibt es im Profil des Vorjahrs nicht" in falsch.stdout
    assert lauf("--vorjahr", str(vor), "--kurz", p.kurz, "--grund", "Stelle gewechselt").returncode == 0
    assert p.kurz in vorjahr.lade_quittungen(jetzt.parent)
