"""Die Browser-Erweiterung: Aufbau, Privacy und die eine kritische Stelle.

Der Rest der Erweiterung ist Darstellung — diese drei Dinge sind es nicht:

1. **Sie darf nichts senden.** Kein `fetch`, kein `XMLHttpRequest`, keine
   Berechtigung ausser `storage` und `sidePanel`. Die Werte sind echte
   Steuerbeträge.
2. **Der Wert muss ankommen.** Ein blosses ``el.value = …`` trägt ihn sichtbar
   ein, aber eine Oberfläche, die auf Ereignisse horcht, kennt ihn nicht —
   beim Speichern wäre er weg. Deshalb nativer Setter plus ``input`` und
   ``change``.
3. **Summen, die das Formular selbst rechnet, dürfen nicht angeboten werden.**
   Wer sie zusätzlich einträgt, verdoppelt sie.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

WURZEL = Path(__file__).resolve().parent.parent / "extension"
PANEL = WURZEL / "panel.js"
SEITE = WURZEL / "seite.js"
SKRIPTE = (PANEL, SEITE, WURZEL / "hintergrund.js")
NODE = shutil.which("node")


def test_manifest_fordert_nur_speicher():
    m = json.loads((WURZEL / "manifest.json").read_text(encoding="utf-8"))
    assert m["manifest_version"] == 3
    # sidePanel: das Seitenteil des Browsers macht die Seite schmaler, statt
    # sie zu überdecken (261028). Mehr ist nicht zu rechtfertigen.
    assert m["permissions"] == ["storage", "sidePanel"], m["permissions"]
    assert m["side_panel"]["default_path"] == "panel.html"
    assert "host_permissions" not in m
    for muster in m["content_scripts"][0]["matches"]:
        assert "zh.ch" in muster or "zhprivatetax.ch" in muster, muster


def test_keine_netzwerkaufrufe():
    quelle = "".join(p.read_text(encoding="utf-8") for p in SKRIPTE)
    for verboten in ("fetch(", "XMLHttpRequest", "navigator.sendBeacon",
                     "new WebSocket", "import("):
        assert verboten not in quelle, (
            f"{verboten} in der Erweiterung — sie darf nichts senden")


def test_der_wert_wird_mit_ereignissen_gesetzt():
    """Die Stelle, an der naive Erweiterungen scheitern — im Seiten-Skript."""
    quelle = SEITE.read_text(encoding="utf-8")
    assert "getOwnPropertyDescriptor" in quelle, (
        "ohne nativen Setter merkt eine Angular-/React-Oberflaeche nichts")
    for ereignis in ("'input'", "'change'"):
        assert f"new Event({ereignis}" in quelle, ereignis


def test_gerechnete_summen_bekommen_keinen_knopf():
    quelle = PANEL.read_text(encoding="utf-8")
    # Der Knopf haengt an der Bedingung `w.gerechnet || … ? '' : …`.
    assert "w.gerechnet || !(w.einsetzen || w.betrag) ? '' :" in quelle
    assert "s.gerechnet || s.kontrolle ? '' :" in quelle


@pytest.mark.skipif(NODE is None, reason="node nicht vorhanden")
def test_das_skript_laesst_sich_laden():
    for skript in SKRIPTE:
        fertig = subprocess.run([NODE, "--check", str(skript)],
                                capture_output=True, text=True, timeout=30)
        assert fertig.returncode == 0, (skript.name, fertig.stderr[-1500:])


def test_der_export_kennzeichnet_gerechnete_ziffern():
    """Ohne dieses Kennzeichen wuerde die Leiste zum Verdoppeln einladen."""
    from extractors.zielwerte import wird_gerechnet

    for ziffer in ("4", "12", "30.1", "34"):
        assert wird_gerechnet(ziffer), ziffer
    for ziffer in ("1.1", "15", "16.6", "22.1", "14"):
        assert not wird_gerechnet(ziffer), ziffer


def test_ein_konto_ist_eine_zeile_mit_spalten():
    """Ein Kontoeintrag im Verzeichnis ist mehrere Felder, nicht eines.

    Bezeichnung, Steuerwert, Bruttoertrag A, Bruttoertrag B — in dieser
    Reihenfolge fragt das Formular 340 sie ab. Wer nur den Saldo einsetzt, hat
    die Zeile nicht ausgefüllt (260923-dua, vom Nutzer gemeldet).
    """
    from extractors.zielwerte import SPALTEN_340, spalte_340

    assert [s for s, _ in SPALTEN_340] == [
        "bezeichnung", "steuerwert", "ertrag_a", "ertrag_b"]
    assert spalte_340("Saldo 31.12.") == "steuerwert"
    assert spalte_340("Steuerwert 31.12.") == "steuerwert"
    assert spalte_340("Bruttoertrag mit Verrechnungssteuer") == "ertrag_a"
    assert spalte_340("Bruttoertrag ohne Verrechnungssteuer") == "ertrag_b"
    assert spalte_340("Nettolohn") == ""


def test_die_leiste_zeigt_eintraege_in_maskenreihenfolge():
    """Konto und Schuld als Eintrag der Online-Maske (261024-jga)."""
    quelle = PANEL.read_text(encoding="utf-8")
    assert "b.felder" in quelle, "die Feldfolge je Eintrag fehlt"
    assert "x.einsetzen" in quelle, "eingesetzt wird die reine Zahl"


def test_einzelpositionen_sind_keine_gerechneten_summen():
    """Der Unterschied, der beim ersten Wurf falsch war.

    `wird_gerechnet` gilt der Summe auf dem Hauptformular (400, 150), nicht
    der Zeile im Verzeichnis. Waeren Einzelpositionen als gerechnet markiert,
    verweigerte die Leiste fuer jedes Konto den Knopf.
    """
    quelle = (WURZEL.parent / "scripts" / "export_extension.py").read_text(
        encoding="utf-8")
    assert '"gerechnet": False,' in quelle
    assert '"gerechnet": wird_gerechnet(ziffer),' not in quelle


def test_lohnblock_bringt_arbeitgeber_und_anstellungsdauer():
    """Das Lohnformular fragt mehr als den Betrag (261024-jga, gemeldet).

    Wer zwei Lohnausweise hat, musste fuer Arbeitgeber und Daten der
    Anstellung die PDFs oeffnen. Jetzt steht beides im Block — mit Herkunft.
    """
    from scripts.export_extension import lohnangaben

    felder = {"periode_von": {"value": "01.02.2025", "anchor_valid": True},
              "periode_bis": {"value": "manual_review:periode_bis"}}
    aus = lohnangaben(felder, "Muster AG")
    assert [(a["titel"], a["wert"], a["herkunft"]) for a in aus] == [
        ("Arbeitgeber", "Muster AG", "tabelle"),
        ("Anstellung von", "01.02.2025", "belegt"),
        ("Anstellung bis", "", "offen"),
    ]
    # Ein Marker ist kein Arbeitgeber.
    assert lohnangaben({}, "⚠ arbeitgeber")[0]["wert"] == ""


def test_lohnangaben_im_anonymisierten_export():
    pfad = WURZEL.parent / "evals" / "samples_real_2023"
    if not (pfad / "_results_full.json").exists():
        pytest.skip("Korpus fehlt")
    from scripts.export_extension import baue

    daten = baue(pfad)
    lohn = [a for f in daten["formulare"] for b in f["bloecke"]
            for a in b.get("angaben") or []]
    assert len(lohn) == 3
    von = {a["quelle"]: a["werte"][1]["wert"] for a in lohn}
    assert von["Lohnausweis INST-A.pdf"] == "01.02.2023"


def test_die_leiste_zeigt_die_lohnangaben():
    quelle = PANEL.read_text(encoding="utf-8")
    assert "b.angaben" in quelle
    assert "nicht verankert" in quelle


def test_die_leiste_ist_das_seitenteil_nicht_auf_der_seite():
    """Kein Overlay, keine Leiste auf der Seite (der Anwender 261028): die Seite wurde
    nicht schmaler, man musste seitwärts scrollen. Das Seitenteil des Browsers
    verkleinert die Seite wie ein kleineres Fenster — rechts."""
    m = json.loads((WURZEL / "manifest.json").read_text(encoding="utf-8"))
    assert m["content_scripts"][0]["js"] == ["seite.js"]
    assert "css" not in m["content_scripts"][0], "auf der Seite wird nichts gezeichnet"
    seite = SEITE.read_text(encoding="utf-8")
    assert "createElement('div')" not in seite and "margin-left" not in seite
    assert "document.body.appendChild(el)" in PANEL.read_text(encoding="utf-8")
    assert '<script src="panel.js">' in (WURZEL / "panel.html").read_text(encoding="utf-8")


def test_einsetzen_geht_per_nachricht_nur_von_der_eigenen_erweiterung():
    seite = SEITE.read_text(encoding="utf-8")
    assert "absender.id !== chrome.runtime.id" in seite
    panel = PANEL.read_text(encoding="utf-8")
    assert "chrome.tabs.sendMessage(tab.id, { art: 'einsetzen', wert })" in panel
    # Ohne Feld oder ohne ZH-Seite wird es gesagt, nicht verschluckt.
    assert "kein_feld" in seite and "keine_seite" in panel


def test_werte_lassen_sich_neu_laden():
    """Nach make extension-daten: ⟳ wählt die Datei neu — vorher ging das nur,
    solange noch gar nichts geladen war."""
    js = PANEL.read_text(encoding="utf-8")
    assert "querySelector('.neu').addEventListener('click', dateiWaehlen)" in js


def test_jeder_betrag_mit_apostroph_kennungen_nicht():
    """der Anwender 261028: „nicht 64613, sondern 64'613" — auch ein Betrag, der an
    anzeige() vorbeikam. Eine Depot-Nr. aus Ziffern bleibt, wie sie ist."""
    from scripts.export_extension import _durchgehend_mit_apostroph

    roh = [{"bloecke": [{"felder": [
        {"titel": "Total Guthaben", "wert": "64613", "einsetzen": "64613"},
        {"titel": "Konto-/Depot-Nr.", "wert": "123456", "einsetzen": "123456"},
        {"titel": "Zinssatz", "wert": "1.45 %"}],
        "werte": [{"bezeichnung": "Nettolohn", "betrag": "64613.5", "einsetzen": ""},
                  {"bezeichnung": "Prämie", "betrag": "1'200.00", "einsetzen": "1'200.00"}]}]}]
    aus = _durchgehend_mit_apostroph(roh)[0]["bloecke"][0]
    assert aus["felder"][0]["wert"] == aus["felder"][0]["einsetzen"] == "64'613.00"
    assert aus["felder"][1]["wert"] == "123456"
    assert aus["felder"][2]["wert"] == "1.45 %"
    assert aus["werte"][0]["betrag"] == "64'613.50"
    assert aus["werte"][1]["betrag"] == "1'200.00"


@pytest.mark.skipif(NODE is None, reason="node nicht vorhanden")
def test_die_leiste_formatiert_auch_alte_daten():
    """Eine früher geladene Datei hatte Beträge ohne Apostroph."""
    import re as _re
    quelle = PANEL.read_text(encoding="utf-8")
    chf = _re.search(r"function chf\(s\) \{[\s\S]*?\n  \}", quelle).group(0)
    prog = chf + "\nconsole.log([chf('64613'), chf('1000.5'), chf(\"12'000.00\"), chf('Bank')].join('|'))"
    r = subprocess.run([NODE, "-e", prog], capture_output=True, text=True, timeout=30)
    assert r.stdout.strip() == "64'613.00|1'000.50|12'000.00|Bank"
    assert "KEIN_BETRAG.test(x.titel" in quelle


def test_die_leiste_laesst_sich_ausblenden():
    quelle = PANEL.read_text(encoding="utf-8")
    assert "querySelector('.schliessen').addEventListener('click', () => window.close())" in quelle


def test_kategorien_aufklappbar_immer_nur_eine_offen():
    """der Anwender 261028: „nur Krankenkassenangaben auf einmal offen sehen"."""
    quelle = PANEL.read_text(encoding="utf-8")
    assert "<details class=\"kat" in quelle and "SCHLUESSEL_OFFEN" in quelle
    # Öffnen schliesst die anderen; beim Suchen bleiben alle Treffer offen.
    assert "if (x !== d) x.open = false;" in quelle
    assert "filter || offen === name ? ' open' : ''" in quelle


def _praemien_formulare():
    w = lambda b, n, **k: {"bezeichnung": b, "betrag": n, "einsetzen": n, **k}
    return [
        {"formular": "Wertschriftenverzeichnis (400)", "bloecke": [
            {"maske": "Bankkonto", "felder": [
                {"titel": "Ertrag ohne Verrechnungssteuer", "wert": "12.40"},
                {"titel": "Ertrag mit Verrechnungssteuer", "wert": ""}]}]},
        {"formular": "Versicherungsprämien", "bloecke": [
            {"titel": "KK-A · Person 1", "werte": [
                w("Prämie Grundversicherung KVG", "4'800.00"),
                w("Prämie Zusatzversicherung VVG", "900.00")]},
            {"titel": "KK-B · Kind 1", "werte": [
                w("Prämie Grundversicherung KVG", "1'200.00"),
                w("Abzüglich erhaltene Prämienverbilligung", "300.00")]}]},
        {"formular": "Hilfsblatt 320", "bloecke": [
            {"titel": "Arzt", "werte": [w("Selbst getragene Krankheits- und Unfallkosten", "250.00"),
                                        w("Selbst getragene Krankheits- und Unfallkosten", "1'000.00",
                                          warnung=True)]}]}]


def test_praemien_und_krankheitskosten_ein_total_fuer_alle():
    """der Anwender 261028: „Ich gebe für alle einmal Prämien und einmal Krankheits-
    und Unfallkosten an." Feldliste Abschnitt 5 und 7 (Form. 365 / 370)."""
    from scripts.export_extension import _familien_totale

    formulare = _praemien_formulare()
    summen = [{"ziffer": "15"}, {"ziffer": "22.1"}, {"ziffer": "1.1"}]
    _familien_totale(formulare, summen)
    pr = formulare[1]["bloecke"]
    felder = {x["titel"]: x for x in pr[0]["felder"]}
    assert list(felder) == ["Private Krankenversicherungsprämien",
                            "Private Unfallversicherungsprämien",
                            "Private Lebens- und Rentenversicherungsprämien",
                            "Zinsen von Sparkapitalien",
                            "Abzüglich erhaltene Prämienverbilligungen"]
    assert felder["Private Krankenversicherungsprämien"]["einsetzen"] == "6'900.00"
    assert felder["Abzüglich erhaltene Prämienverbilligungen"]["wert"] == "300.00"
    assert felder["Zinsen von Sparkapitalien"]["wert"] == "12.40"
    assert not felder["Private Unfallversicherungsprämien"]["einsetzen"]
    # Einzelwerte: Aufschlüsselung ohne Knopf
    assert all(w["im_total"] for b in pr[1:] for w in b["werte"])
    kk = {x["titel"]: x for x in formulare[2]["bloecke"][0]["felder"]}
    total = kk["Selbst getragene Krankheits- und Unfallkosten"]
    assert total["wert"] == "1'250.00" and total["warnung"]
    assert [s.get("kontrolle") for s in summen] == [True, True, None]


def test_die_leiste_bietet_einzelpraemien_nicht_mehr_an():
    quelle = PANEL.read_text(encoding="utf-8")
    assert "w.kontrolle || w.im_total ? '' :" in quelle
    assert "s.gerechnet || s.kontrolle ? '' :" in quelle


def test_totale_auf_dem_korpus():
    """Auf den anonymisierten Belegen: genau ein Prämien-Total, Einzelwerte
    ohne eigenen Eintrag."""
    from unittest import mock
    from scripts import export_extension

    ordner = sorted(p for p in (WURZEL.parent / "evals").glob("samples_real_*")
                    if (p / "_results_full.json").exists())
    if not ordner:
        pytest.skip("kein Korpus")
    with mock.patch("scripts.build_review_html.personennamen", return_value={}):
        d = export_extension.baue(ordner[-1])
    for f in d["formulare"]:
        if f["formular"] in ("Versicherungsprämien", "Hilfsblatt 320"):
            assert f["bloecke"][0]["person"] == "alle Personen"
            assert all(w.get("im_total") for b in f["bloecke"][1:] for w in b.get("werte") or [])


def test_make_extension_daten_laeuft_durch(tmp_path, capsys, monkeypatch):
    """Der ganze Befehl, nicht nur baue() — die Zusammenfassung am Ende
    brach am gemischten Block (Total + Einzelwerte) ab (261028)."""
    import sys as _sys
    from unittest import mock
    from scripts import export_extension

    ordner = sorted(p for p in (WURZEL.parent / "evals").glob("samples_real_*")
                    if (p / "_results_full.json").exists())
    if not ordner:
        pytest.skip("kein Korpus")
    monkeypatch.setattr(_sys, "argv", ["x", "--samples", str(ordner[-1]),
                                       "--out", str(tmp_path / "steuerwerte.json")])
    with mock.patch("scripts.build_review_html.personennamen", return_value={}):
        assert export_extension.main() == 0
    aus = capsys.readouterr().out
    assert "Versicherungsprämien:" in aus and "im Total" in aus
    assert (tmp_path / "steuerwerte.json").exists()
