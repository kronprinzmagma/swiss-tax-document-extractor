"""Tests fuer die lokale Review-Oberflaeche (260904-rmx)."""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from scripts.build_review_html import baue_html

ROOT = Path(__file__).resolve().parent.parent


def _zeilen():
    return [
        {"nr": 1, "beleg": "a.pdf", "belegtyp": "kk_praemienbescheinigung",
         "ziffer": "22.1", "zielwert": "Selbstkosten", "person": "", "aussteller": "K",
         "betrag": "412.60", "konfidenz": "unsicher", "grund": "kein Anker",
         "herkunft": "modell", "seite": 1, "snippet": "Selbstbehalt"},
        {"nr": 2, "beleg": "a.pdf", "belegtyp": "kk_praemienbescheinigung",
         "ziffer": "15", "zielwert": "Prämie KVG", "person": "elternteil_1",
         "aussteller": "K", "betrag": "3120.40", "konfidenz": "sicher",
         "grund": "beschriftete Regel + Anker", "herkunft": "regel",
         "seite": 1, "snippet": ""},
    ]


def test_html_laedt_nichts_aus_dem_netz():
    """Privacy: die Seite darf keine externe Ressource anfordern."""
    doc = baue_html(_zeilen(), ["elternteil_1"])
    assert not re.search(r'(?:src|href)\s*=\s*["\']https?://', doc)
    assert "cdn" not in doc.lower()
    assert "fonts.googleapis" not in doc


def test_html_enthaelt_die_werte_und_die_gruende():
    doc = baue_html(_zeilen(), ["elternteil_1"])
    assert "412.60" in doc
    assert "kein Anker" in doc
    assert "Selbstkosten" in doc


def test_rollen_landen_im_dropdown():
    doc = baue_html(_zeilen(), ["elternteil_1", "kind_1"])
    assert "kind_1" in doc


def test_schreiben_unter_evals_wird_verweigert(tmp_path):
    """Die Seite traegt echte Namen — im geprueften Korpus hat sie nichts verloren."""
    samples = ROOT / "evals" / "samples_real_2023"
    if not (samples / "_results_full.json").exists():
        import pytest
        pytest.skip("Sample-Korpus nicht vorhanden")
    r = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "build_review_html.py"),
         "--samples", str(samples)],
        capture_output=True, text=True,
        env={"PYTHONPATH": str(ROOT), "PATH": "/usr/bin:/bin"},
    )
    assert r.returncode == 2
    assert "ABBRUCH" in r.stderr
    assert not (samples / "review.html").exists()


def test_ziffern_ansicht_und_kopierknopf_vorhanden():
    """Formular-Reihenfolge und Kopieren sind der Zweck der Seite."""
    doc = baue_html(_zeilen(), ["elternteil_1"])
    assert "setGrp(" in doc and "nach Ziffer" in doc
    assert "function kopieren(" in doc
    assert "navigator.clipboard" in doc


def test_javascript_ist_syntaktisch_gueltig():
    import re as _re, shutil, subprocess, sys as _sys, tempfile, pathlib
    if not shutil.which("node"):
        import pytest
        pytest.skip("node nicht verfuegbar")
    js = _re.search(r"<script>(.*?)</script>", baue_html(_zeilen(), ["e1"]), _re.S).group(1)
    p = pathlib.Path(tempfile.mkdtemp()) / "t.js"
    p.write_text(js)
    r = subprocess.run(["node", "--check", str(p)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_ansichten_vorhanden():
    """Dokumente (Einstieg), nach Beleg, nach Ziffer, Uebertragen.

    „nach Belegart" ist keine Ansicht mehr, sondern ein Filter — eine
    Gruppierung beantwortet „zeig mir nur die Hypotheken" nicht (260923-dua).
    """
    doc = baue_html(_zeilen(), ["elternteil_1"])
    for modus in ("Dokumente", "nach Beleg", "nach Ziffer", "Übertragen"):
        assert modus in doc, modus
    assert "function tabelle(" in doc
    assert "function renderDokumente(" in doc


def test_belegart_ist_ein_filter():
    doc = baue_html(_zeilen(), ["elternteil_1"])
    assert 'id="artfilter"' in doc
    assert "function setArt(" in doc
    assert "function passtZurArt(" in doc


def test_uebersicht_kennt_alle_fuenf_zustaende():
    """Jeder Zustand traegt ein Wort, nicht nur eine Farbe."""
    doc = baue_html(_zeilen(), ["elternteil_1"])
    for wort in ("geprüft", "offen", "ohne Fund", "ausgeschlossen",
                 "nicht eingelesen"):
        assert wort in doc, wort


def test_position_laesst_sich_aus_der_uebersicht_erfassen():
    """Drei Hypotheken in einem Dokument: der Knopf muss dort sein, wo man das
    Dokument sieht — nicht nur in einer bestimmten Gruppierung."""
    doc = baue_html(_zeilen(), ["elternteil_1"])
    assert "＋ Position" in doc


def test_bestaetigung_pro_feld_und_gespeichert():
    doc = baue_html(_zeilen(), ["elternteil_1"])
    assert "okBetrag" in doc and "okPerson" in doc
    assert "localStorage" in doc, "Fortschritt muss einen Reload ueberleben"


# --- Nicht zugeordnete Dokumente -------------------------------------------

def _offen():
    return [{"beleg": "unbekannt.pdf", "status": "unknown_belegtyp",
             "grund": "Belegtyp nicht erkannt", "pdflink": "file:///x.pdf",
             "seite": {"bild": "data:image/png;base64,AA", "breite": 827,
                       "hoehe": 1169,
                       "worte": [{"t": "Immobilie", "x": 10, "y": 20, "w": 60, "h": 12},
                                 {"t": "450000", "x": 300, "y": 20, "w": 40, "h": 12}]}}]


def test_offene_dokumente_erscheinen():
    """Sie fehlten bisher ganz — und konnten damit vergessen werden."""
    doc = baue_html(_zeilen(), ["e1"], [], _offen())
    assert "unbekannt.pdf" in doc
    assert "Nicht zugeordnete Dokumente" in doc
    assert "function renderOffen(" in doc


def test_irrelevant_ist_eine_gueltige_wahl():
    """Aktiv 'gehoert nicht in die Steuererklaerung' festhalten."""
    from scripts.build_review_html import BELEGTYP_WAHL
    assert "irrelevant" in BELEGTYP_WAHL
    assert baue_html(_zeilen(), ["e1"], [], _offen()).count("irrelevant") >= 1


def test_woerter_sind_anklickbar():
    """Der markierte Wert kommt exakt aus dem PDF, nicht aus einem Screenshot."""
    doc = baue_html(_zeilen(), ["e1"], _offen_befunde(), _offen())
    assert "function wortKlick(" in doc
    assert "Immobilie" in doc and "450000" in doc


def _offen_befunde():
    return []


def test_ohne_offene_dokumente_leere_liste():
    """Die Ueberschrift steht als Vorlage im Skript — entscheidend sind die Daten."""
    doc = baue_html(_zeilen(), ["e1"], [], [])
    assert "window.__OFFEN__ = [];" in doc


# --- Streichen und Belegart nachtraeglich aendern ---------------------------

def test_jede_zeile_laesst_sich_streichen():
    """Nicht jeder falsche Wert ist ein falscher Betrag — manchen weist das
    Dokument gar nicht aus."""
    doc = baue_html(_zeilen(), ["elternteil_1"])
    assert "function streichen(" in doc
    # Die Tabelle entsteht im Browser — geprueft wird die Vorlage.
    assert "streichen(${r.nr})" in doc
    assert "gestrichen" in doc
    # Die Beschriftung muss sagen, was gemeint ist: nicht "gehoert nicht in
    # die Steuererklaerung", sondern "steht nicht in diesem Dokument".
    assert "nicht im Dokument" in doc


def test_streichen_taucht_in_der_csv_spalte_auf():
    doc = baue_html(_zeilen(), ["elternteil_1"])
    kopf = re.search(r"const kopf = \[(.*?)\];", doc, re.S).group(1)
    assert "'streichen'" in kopf


def test_export_schreibt_so_viele_spalten_wie_der_kopf():
    """Die offenen Dokumente schrieben 12 Werte in eine 22-spaltige Datei —
    die Notiz landete in person_neu.

    Geprueft wird die Breite, nicht der Wortlaut der Rechnung: eine neue
    Spalte verschiebt sie, und ein Test, der an der Zahl 12 klebt, verlangt
    dann eine Aenderung, ohne einen Fehler gefunden zu haben (260923-dua).
    Die vollstaendige Pruefung jeder Exportzeile steht in
    ``evals/test_export_spalten.py``.
    """
    doc = baue_html(_zeilen(), ["elternteil_1"])
    m = re.search(r"leer\(kopf\.length - (\d+)\), notiz((?:, '')*)\]", doc)
    assert m, "Die Zeile fuer nicht zugeordnete Dokumente fehlt"
    # Die Rechnung geht auf, wenn der Abzug genau die Werte zaehlt, die nicht
    # aus der Fuellung kommen: 11 vorangestellte plus Notiz (plus die
    # Positionsnummer, falls vorhanden).
    fest = 11 + 1 + m.group(2).count("''")
    assert int(m.group(1)) == fest


def test_belegart_laesst_sich_auch_nachtraeglich_aendern():
    """Ein als Bankbeleg erkannter 3a-Ausweis war eine Sackgasse: der
    Feldvertrag bot dann nur Bank-Zielwerte an."""
    doc = baue_html(_zeilen(), ["elternteil_1"])
    assert "function typWahl(" in doc
    assert "typSetzen(" in doc
    assert "saeule_3a" in doc


def test_belegart_wechsel_zeigt_den_noetigen_befehl():
    """Der Browser kann nicht neu extrahieren — er sagt, was zu tun ist."""
    doc = baue_html(_zeilen(), ["elternteil_1"])
    assert "make reextract" in doc


# --- Uebertragungsansicht ---------------------------------------------------

def test_uebertragungsansicht_existiert():
    """Beim Abschreiben will man lesen, nicht aus Versehen etwas aendern."""
    doc = baue_html(_zeilen(), ["elternteil_1"])
    assert "function renderUebertragen(" in doc
    assert "Übertragen (ohne Eingabefelder)" in doc


def test_uebertragungsansicht_kennt_die_ziffernamen():
    doc = baue_html(_zeilen(), ["elternteil_1"])
    assert "Versicherungsprämien und Sparzinsen" in doc
    assert "Krankheits- und Unfallkosten" in doc


def test_uebertragungsansicht_laesst_gestrichene_weg():
    doc = baue_html(_zeilen(), ["elternteil_1"])
    assert "R.filter(r => !istGestrichen(r))" in doc
    # Auch der gespeicherte Stand zaehlt, nicht nur die lokale Wahl.
    assert "function istGestrichen(" in doc


def test_ziffern_sortieren_numerisch_im_browser():
    """16.6 gehoert zwischen 15 und 22.1 — alphabetisch stuende es vorn."""
    doc = baue_html(_zeilen(), ["elternteil_1"])
    assert "function zsort(" in doc


def test_live_pruefung_ignoriert_gestrichene_zeilen():
    """Eine gestrichene VVG heisst nicht 'gibt es keine' — der Befund bleibt."""
    doc = baue_html(_zeilen(), ["elternteil_1"])
    assert "R.filter(r => !istGestrichen(r)).forEach" in doc


# --- Zeilen ergaenzen -------------------------------------------------------

def test_eigene_zeilen_werden_vor_dem_zustand_eingehaengt():
    """Sonst findet das Laden sie nicht und ihre Eingaben sind nach jedem
    Neuladen weg."""
    doc = baue_html(_zeilen(), ["elternteil_1"])
    einhaengen = doc.index("function eigeneEinhaengen(")
    laden = doc.index("function ladeZustand(")
    assert einhaengen < laden


def test_eigene_zeile_kommt_nicht_doppelt_zurueck():
    """Der Server liefert selbst erfasste Positionen inzwischen selbst. Kommt
    die lokale Kopie dazu, steht jede Zeile zweimal da — fuenf
    Betreuungskosten, wo es zwei gibt (260923-dua, vom Nutzer gemeldet)."""
    doc = baue_html(_zeilen(), ["elternteil_1"])
    # Verglichen wird, woran man die Zeile erkennt, nicht die laufende Nummer.
    assert "const vomServer = new Set(" in doc
    assert "if (vomServer.has(" in doc
    assert "R.some(x => x.nr === z.nr)) R.push(z); });" not in doc


def test_zwei_neue_zeilen_im_selben_beleg_teilen_den_schluessel_nicht():
    """Der Fall Kinderbetreuung: zwei Betraege aus einem Dokument."""
    doc = baue_html(_zeilen(), ["elternteil_1"])
    assert "'|#' + r.nr" in doc


def test_neue_nummer_kollidiert_nicht_nach_dem_entfernen():
    doc = baue_html(_zeilen(), ["elternteil_1"])
    assert "Math.max(10000, ...R.map(x => x.nr || 0))" in doc


# --- Bedienung --------------------------------------------------------------

def test_zeile_hinzufuegen_in_jeder_ansicht():
    """'+ Zeile' stand nur in der Kopfzeile der Beleg-Gruppierung. Wer nach
    Ziffer sortiert hatte — also gerade dann, wenn ihm ein zweiter Wert
    auffaellt — hatte keinen Knopf."""
    doc = baue_html(_zeilen(), ["elternteil_1"])
    assert "zeileDazu(${r.nr})" in doc


def test_zielwert_laesst_sich_anklicken_statt_tippen():
    """<input list=…> zeigt seine Vorschlaege erst beim Tippen; der Pfeil
    oeffnet eine leere Liste."""
    doc = baue_html(_zeilen(), ["elternteil_1"])
    assert "function zielWaehlen(" in doc
    assert 'class="zielwahl"' in doc


def test_person_laesst_sich_auf_den_ganzen_beleg_uebertragen():
    doc = baue_html(_zeilen(), ["elternteil_1"])
    assert "function personAufBeleg(" in doc
    assert "x.beleg === r.beleg" in doc


def test_weitere_position_erbt_ihren_kontext():
    """Der Fall Kinderbetreuung: zwei Kinder, ein Beleg.

    Ohne geerbte Ziffer landete die neue Zeile in der Ziffer-Ansicht in einer
    eigenen Gruppe weit weg — es sah aus, als tue der Knopf nichts."""
    doc = baue_html(_zeilen(), ["elternteil_1"])
    assert "function zeileDazu(" in doc
    assert "zeileDazu(${r.nr})" in doc
    # Betrag und Person unterscheiden die Positionen und bleiben leer.
    for feld in ("zielwert", "ziffer", "aussteller"):
        assert f"z.{feld} =" in doc
    assert "z.betrag" not in doc
    assert "scrollIntoView" in doc, "die neue Zeile muss sichtbar werden"


# --- Suche ------------------------------------------------------------------

def test_suchfeld_vorhanden():
    """Bei 32 Dokumenten hiess 'finden' bisher scrollen."""
    doc = baue_html(_zeilen(), ["elternteil_1"])
    assert 'id="suche"' in doc
    assert "function setSuche(" in doc


def test_suche_versteht_prozentkodierung():
    """Ein aus einem PDF-Link kopierter Name bringt sein %20 mit."""
    doc = baue_html(_zeilen(), ["elternteil_1"])
    assert "/%20/g" in doc


def test_suche_greift_in_allen_ansichten():
    doc = baue_html(_zeilen(), ["elternteil_1"])
    assert "&& passtZurSuche(r)" in doc          # Zeilenansichten
    assert "!istGestrichen(r) && passtZurArt(r.beleg)" in doc   # Übertragen
    assert "OFFEN.filter(d => passtZurSuche(" in doc       # offene Dokumente
    assert "passtZurSuche({beleg: d.beleg})" in doc        # Dokumentuebersicht


def test_belegart_gruppierung_folgt_dem_zielwert():
    """Eine auf VVG umgestellte Zeile gehoert unter Krankenkasse — nicht unter
    die Belegart des Dokuments, aus dem sie zufaellig stammt."""
    from scripts.build_review_html import ZIELWERT_TYP
    assert ZIELWERT_TYP["Prämie Zusatzversicherung VVG"] == "kk_praemienbescheinigung"
    assert ZIELWERT_TYP["Kosten Kinderbetreuung"] == "kinderbetreuung"
    doc = baue_html(_zeilen(), ["elternteil_1"])
    assert "function typVonZeile(" in doc
    assert "ZIELWERT_TYP[ziel]" in doc


# --- Der Browser-Speicher darf nichts wegwerfen -----------------------------

def test_speicher_wird_ergaenzt_statt_neu_gebaut():
    """sichern() baute den ganzen Speicher aus den aktuellen Zeilen neu. Jeder
    Eintrag, dessen Schluessel gerade nicht passte — nach einer Umbenennung,
    einem umbenannten PDF, einem anderen Korpus — war beim naechsten
    Tastendruck weg. Dasselbe Muster wie der Verlust auf der Serverseite."""
    doc = baue_html(_zeilen(), ["e1"])
    assert "let rohSpeicher" in doc
    assert "rohSpeicher[skey(r)] = state[r.nr]" in doc
    # Der alte, zerstoererische Aufbau darf nicht zurueckkommen — und geprueft
    # wird das IN `sichern()`, nicht irgendwo auf der Seite. Die erste Fassung
    # suchte im ganzen Dokument nach einem Variablennamen und schlug an, als
    # eine ganz andere Funktion ihn zufaellig auch benutzte (260923-dua).
    rumpf = doc[doc.index("function sichern()"):]
    rumpf = rumpf[:rumpf.index("\nfunction ")]
    assert "= {}" not in rumpf, (
        "sichern() baut den Speicher wieder neu auf:\n" + rumpf)


def test_speicher_ist_je_korpus_getrennt():
    """Produktiver Lauf und Sample-Korpus laufen unter derselben Origin."""
    doc = baue_html(_zeilen(), ["e1"])
    for schluessel in ("SKEY", "NKEY", "GKEY", "TKEY", "BKEY", "OKEY"):
        zeile = [z for z in doc.splitlines()
                 if z.startswith(f"const {schluessel} =")]
        assert zeile, schluessel
        assert "+ KORPUS" in zeile[0], (schluessel, zeile[0])


def test_zwei_korpora_ergeben_zwei_schluessel(tmp_path):
    from scripts.build_review_html import baue_aus_samples
    import json as _json
    a, b = tmp_path / "a", tmp_path / "b"
    for d in (a, b):
        d.mkdir()
        (d / "_results_full.json").write_text("[]")
    ka = baue_aus_samples(a, None, mit_crops=False)
    kb = baue_aus_samples(b, None, mit_crops=False)
    hole = lambda doc: _json.loads(
        [z for z in doc.splitlines() if z.startswith("window.__KORPUS__")][0]
        .split("= ", 1)[1].rstrip(";"))
    assert hole(ka) != hole(kb)


def test_lokaler_zustand_trennt_positionen_gleicher_art():
    """Drei Hypotheken eines Belegs teilten sich einen Speichereintrag: was
    man bei der zweiten eintrug, landete bei der ersten (260923-dua)."""
    doc = baue_html(_zeilen(), ["elternteil_1"])
    assert "(r.pos || 1) > 1 ? '#' + r.pos" in doc


def test_vergebene_positionsnummer_wird_festgeschrieben():
    """Wird sie bei jedem Rendern neu gerechnet, ruecken nach einer Streichung
    alle folgenden auf — ein bestaetigter Sollwert zeigte danach auf eine
    andere Position."""
    doc = baue_html(_zeilen(), ["elternteil_1"])
    assert "if (s.posNr) return s.posNr;" in doc
    assert "posNr = n;" in doc


def test_bestaetigen_zeichnet_nicht_die_ganze_seite_neu():
    """Wer die Person bestaetigte, landete wieder oben und musste zur selben
    Zeile zurueckscrollen, um auch den Betrag zu bestaetigen (260923-dua)."""
    doc = baue_html(_zeilen(), ["elternteil_1"])
    assert "button[data-b]" in doc and "button[data-p]" in doc
    assert "function zaehlerNeu(" in doc
    assert "function listenzeileNeu(" in doc


def test_spalten_scrollen_fuer_sich():
    """Die rechte Spalte klebte oben fest — ist sie hoeher als das Fenster,
    kommt man an ihr unteres Ende gar nicht heran."""
    doc = baue_html(_zeilen(), ["elternteil_1"])
    assert "position:sticky;top:8px" not in doc
    assert "max-height:calc(100vh - 150px)" in doc
    assert "function _scrollMerken(" in doc


def test_dokumentliste_kann_umbrechen():
    """Eine vierspaltige Tabelle passt nicht in eine schmale Seitenleiste —
    lange Dateinamen schoben sie auf und der Rest wurde abgeschnitten."""
    doc = baue_html(_zeilen(), ["elternteil_1"])
    assert '<ul class="dokliste">' in doc
    assert "overflow-wrap:anywhere" in doc


def test_irrelevant_verlangt_kein_neu_einlesen():
    """"Irrelevant" ist keine Belegart mit Regeln, sondern das Gegenteil
    davon — der Befehl `make reextract ... TYP=irrelevant` war Unsinn."""
    doc = baue_html(_zeilen(), ["elternteil_1"])
    assert "soll === 'irrelevant'" in doc
    assert "nicht steuerrelevant" in doc
    assert "function aussortieren(" in doc


def test_nicht_zugeordnete_stehen_in_der_liste_statt_oben():
    """Sie standen in einem eigenen Kasten ganz oben und waren damit das
    Erste, was man sah — obwohl sie meist das Letzte sind, worum man sich
    kuemmert (260923-dua)."""
    doc = baue_html(_zeilen(), ["elternteil_1"])
    assert "function offenBlock(" in doc
    assert "if (grpModus === 'dokumente'){ host.innerHTML = ''; return; }" in doc
    # und ganz ans Ende der Liste sortiert
    assert "ohne_fund: 2, nicht_eingelesen: 3" in doc


def test_befunde_sind_eingeklappt():
    """Aufgeklappt schoben sie die beiden Arbeitsspalten aus dem Bild."""
    doc = baue_html(_zeilen(), ["elternteil_1"])
    assert "befunde-klapp" in doc
    assert "function bKlappStand(" in doc


def test_spalten_enden_am_fensterrand():
    """Mit fester Rechnung bleibt Platz uebrig oder fehlt — dann scrollt die
    Seite als dritter Bereich mit."""
    doc = baue_html(_zeilen(), ["elternteil_1"])
    assert "function hoehenSetzen(" in doc
    assert "window.addEventListener('resize', hoehenSetzen)" in doc
