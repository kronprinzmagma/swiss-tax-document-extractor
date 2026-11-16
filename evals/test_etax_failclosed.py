"""eSteuerauszug fail-closed, Kunde, Ordner, Kern mit Wortgrenzen (261025-kme, B5).
Alle Werte erfunden.

Ein Konto ohne Steuerwert im eSteuerauszug unterschlüge Vermögen — ein
solches Institut wird zurückgehalten, mit Kürzel und Grund, Exit 1. Alte
Auszüge kommen in den Verlauf statt in den Papierkorb (Bestandsschutz 2025).

Läuft im Projekt-venv; nur der letzte Test braucht ``.venv-etax``.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PYETAX = ROOT / ".venv-etax" / "bin" / "python"


def konto(saldo="1000.00", mit="", ohne="", nummer="", bezeichnung="Privatkonto"):
    return {"dokumente": ["k.pdf"], "nummer": nummer, "bezeichnung": bezeichnung,
            "jahr": "2025", "saldo": saldo, "ertrag_mit_vst": mit, "ertrag_ohne_vst": ohne}


def schuld(betrag="100000", zins="900", satz=0.9):
    return {"bezeichnung": "Festhypothek", "jahr": "2025", "schuld": betrag,
            "zins": zins, "satz_beleg": "", "zinssatz": satz}


def eingabe(tmp_path, institute, **kopf):
    daten = {"institute": institute, "ohne_institut": 0,
             "unvollstaendige_schulden": 0, "gleiche_institute": [],
             "kunde": {"vorname": "", "nachname": ""},
             "unplausible_zinssaetze": 0, "konten_ohne_saldo": [], "konten_leer": 0}
    daten.update(kopf)
    pfad = tmp_path / "etax_eingabe.json"
    pfad.write_text(json.dumps(daten, ensure_ascii=False), encoding="utf-8")
    return pfad


def bauen(monkeypatch, pfad, ziel, *extra, vorname="Hans", nachname="Muster"):
    from scripts import etax_bauen

    monkeypatch.setattr(sys, "argv", ["x", "--eingabe", str(pfad), "--ziel", str(ziel),
                                      "--jahr", "2025", "--vorname", vorname,
                                      "--nachname", nachname, *extra])
    return etax_bauen.main()


def teilbar(text: str) -> str:
    return text.split("NICHT teilen")[0]


# --- B5a: unvollständige Institute werden zurückgehalten --------------------

@pytest.mark.parametrize("haus,kopf", [
    ({"institut": "Musterbank Nord", "person": "elternteil_1",
      "konten": [konto(saldo="", mit="12.30")], "schulden": [],
      "unvollstaendig": ["konto_ohne_saldo"]},
     {"konten_ohne_saldo": [konto(saldo="", mit="12.30")]}),
    ({"institut": "Musterbank Nord", "person": "elternteil_1", "konten": [],
      "schulden": [schuld(zins="", satz=None)], "unvollstaendig": ["schuld_ohne_zins"]},
     {"unvollstaendige_schulden": 1}),
    ({"institut": "Musterbank Nord", "person": "elternteil_1", "konten": [],
      "schulden": [schuld(zins="9000", satz=9.0)],
      "unvollstaendig": ["zinssatz_unplausibel"]},
     {"unplausible_zinssaetze": 1}),
])
def test_unvollstaendiges_institut_wird_zurueckgehalten(tmp_path, monkeypatch, capsys,
                                                        haus, kopf):
    pfad = eingabe(tmp_path, [haus], **kopf)
    ziel = tmp_path / "esteuerauszug"
    assert bauen(monkeypatch, pfad, ziel) == 1
    assert not list(ziel.glob("*.pdf"))
    aus = capsys.readouterr().out
    assert "zurückgehalten" in aus and "I01" in aus
    t = teilbar(aus)
    assert "Musterbank" not in t
    assert not any(b in t for b in ("12.30", "9000", "100000"))
    assert "Musterbank Nord" in aus.split("NICHT teilen")[1]


def test_alte_eingabe_und_alte_pdfs_bleiben(tmp_path, monkeypatch, capsys):
    """BS-9: alte Eingabe ohne „unvollstaendig" wird selbst geprüft; das alte
    PDF liegt danach byte-gleich im Verlauf; etax_daten kopiert die alte
    Eingabe vor dem Schreiben."""
    from extractors import eintraege
    from scripts import etax_daten

    haus = {"institut": "Musterbank Nord", "person": "elternteil_1",
            "konten": [konto(saldo="", mit="12.30")], "schulden": []}
    pfad = eingabe(tmp_path, [haus], konten_ohne_saldo=[konto(saldo="", mit="12.30")])
    ziel = tmp_path / "esteuerauszug"
    ziel.mkdir()
    altes_pdf = b"%PDF-1.4 erfunden alt"
    (ziel / "eSteuerauszug-Alt-2025.pdf").write_bytes(altes_pdf)
    assert bauen(monkeypatch, pfad, ziel) == 1
    assert not list(ziel.glob("*.pdf"))
    verlauf = [p for p in (ziel / "verlauf").rglob("*.pdf")]
    assert [p.read_bytes() for p in verlauf] == [altes_pdf]
    assert "I01 Konto ohne Steuerwert" in capsys.readouterr().out

    samples = tmp_path / "json"
    samples.mkdir()
    alte_eingabe = pfad.read_bytes()
    monkeypatch.setattr(etax_daten, "_zeilen_der_tabelle", lambda s: [])
    monkeypatch.setattr(eintraege, "alle_eintraege", lambda *a, **k: ([], []))
    monkeypatch.setattr(sys, "argv", ["x", "--samples", str(samples)])
    assert etax_daten.main() == 0
    kopien = list((tmp_path / "etax_eingabe-verlauf").glob("*.json"))
    assert [k.read_bytes() for k in kopien] == [alte_eingabe]


def test_widerspruechliche_eingabe_baut_nichts(tmp_path, monkeypatch, capsys):
    haus = {"institut": "Musterbank Nord", "person": "elternteil_1",
            "konten": [konto()], "schulden": []}
    pfad = eingabe(tmp_path, [haus], unvollstaendige_schulden=2)
    assert bauen(monkeypatch, pfad, tmp_path / "ziel") == 1
    assert "Eingabe widersprüchlich" in capsys.readouterr().out


# --- B5b: Kundenname -----------------------------------------------------------

def test_kunde_aus_argumenten_vor_den_daten():
    from scripts.etax_bauen import KundeFehlt, _kunde

    with pytest.raises(KundeFehlt, match="VORNAME"):
        _kunde("", "", {"kunde": {"vorname": "", "nachname": ""}})
    assert _kunde("Hans", "Muster", {"kunde": {"vorname": "Anna", "nachname": "Beispiel"}}) \
        == {"vorname": "Hans", "nachname": "Muster"}
    with pytest.raises(KundeFehlt, match="zusammen angeben"):
        _kunde("Hans", "", {"kunde": {"vorname": "Anna", "nachname": "Beispiel"}})
    assert _kunde("", "", {"kunde": {"vorname": "Anna", "nachname": "Beispiel"}}) \
        == {"vorname": "Anna", "nachname": "Beispiel"}


def test_ohne_kunde_bleibt_das_ziel_unberuehrt(tmp_path, monkeypatch, capsys):
    pfad = eingabe(tmp_path, [{"institut": "Musterbank Nord", "person": "elternteil_1",
                               "konten": [konto()], "schulden": []}])
    ziel = tmp_path / "ziel"
    ziel.mkdir()
    (ziel / "eSteuerauszug-Alt-2025.pdf").write_bytes(b"%PDF alt")
    assert bauen(monkeypatch, pfad, ziel, vorname="", nachname="") == 1
    assert (ziel / "eSteuerauszug-Alt-2025.pdf").read_bytes() == b"%PDF alt"
    assert not (ziel / "verlauf").exists()
    assert "VORNAME" in capsys.readouterr().out


# --- B5c: der echte Ordner -------------------------------------------------------

def test_etax_daten_reicht_den_ordner_durch(tmp_path, monkeypatch):
    from extractors import eintraege
    from scripts import etax_daten

    gesehen = {}

    def aufzeichnen(*a, **k):
        gesehen.update(k)
        return [], []

    monkeypatch.setattr(etax_daten, "_zeilen_der_tabelle", lambda s: [])
    monkeypatch.setattr(eintraege, "alle_eintraege", aufzeichnen)
    etax_daten.baue(tmp_path, pdf_dir=tmp_path / "belege")
    assert gesehen["pdf_dir"] == tmp_path / "belege"


def test_etax_daten_ohne_ordner_sagt_es(tmp_path, monkeypatch, capsys):
    from extractors import eintraege
    from scripts import etax_daten

    samples = tmp_path / "json"
    samples.mkdir()
    monkeypatch.setattr(etax_daten, "_zeilen_der_tabelle", lambda s: [])
    monkeypatch.setattr(eintraege, "alle_eintraege", lambda *a, **k: ([], []))
    monkeypatch.setattr(sys, "argv", ["x", "--samples", str(samples)])
    assert etax_daten.main() == 0
    assert "ohne PDF-Ordner" in capsys.readouterr().out


def test_etax_daten_fehlender_ordner_ohne_pfad(tmp_path, monkeypatch, capsys):
    from scripts import etax_daten

    samples = tmp_path / "json"
    samples.mkdir()
    fehlt = tmp_path / "GeheimOrdner"
    monkeypatch.setattr(etax_daten, "_zeilen_der_tabelle", lambda s: [])
    monkeypatch.setattr(sys, "argv", ["x", "--samples", str(samples), "--pdf-dir", str(fehlt)])
    assert etax_daten.main() == 1
    aus = capsys.readouterr()
    assert "GeheimOrdner" not in aus.out + aus.err
    assert not (tmp_path / "etax_eingabe.json").exists()


# --- B5d: Kern mit Wortgrenzen ------------------------------------------------------

def test_kern_nur_an_wortgrenzen():
    from scripts.etax_daten import kern

    assert kern("Musterbank Dielsdorf") == kern("Musterbank Dielsdorf Genossenschaft")
    assert kern("Bank Salvia") == "salvia"
    assert kern("Bank Sandmann") != kern("Bank Sanders")


def test_zwei_schreibweisen_werden_als_zusammengefuehrt_gemeldet(tmp_path):
    from evals.test_etax_daten import lege
    from extractors.ablage import oeffne, pfad_fuer
    from scripts.etax_daten import baue

    samples = tmp_path / "json"
    samples.mkdir()
    db = oeffne(pfad_fuer(samples))
    lege(db, "k1", "a.pdf", "Saldo 31.12.", "100", "Musterbank Dielsdorf")
    lege(db, "k2", "b.pdf", "Saldo 31.12.", "200", "Musterbank Dielsdorf Genossenschaft")
    db.commit()
    daten = baue(samples)
    assert [h["institut"] for h in daten["institute"]] == ["Musterbank Dielsdorf Genossenschaft"]
    assert daten["zusammengefuehrt"] == [["Musterbank Dielsdorf",
                                          "Musterbank Dielsdorf Genossenschaft"]]
    assert not daten["gleiche_institute"]
    assert daten["institute"][0]["unvollstaendig"] == []


# --- Mit .venv-etax: wirklich gebaut ------------------------------------------------

@pytest.mark.skipif(not PYETAX.exists(), reason=".venv-etax fehlt")
def test_nur_vollstaendige_institute_werden_gebaut(tmp_path):
    voll = {"institut": "Musterbank Nord", "person": "elternteil_1",
            "konten": [konto(nummer="", saldo="1000.00", mit="1.20")], "schulden": []}
    halb = {"institut": "Musterbank Sued", "person": "elternteil_1",
            "konten": [konto(saldo="", mit="12.30")], "schulden": [],
            "unvollstaendig": ["konto_ohne_saldo"]}
    pfad = eingabe(tmp_path, [voll, halb], konten_ohne_saldo=[konto(saldo="", mit="12.30")])

    def lauf(ziel, *extra):
        return subprocess.run(
            [str(PYETAX), str(ROOT / "scripts" / "etax_bauen.py"), "--eingabe", str(pfad),
             "--ziel", str(ziel), "--jahr", "2025", "--vorname", "Hans",
             "--nachname", "Muster", *extra],
            capture_output=True, text=True, timeout=300, cwd=ROOT)

    r = lauf(tmp_path / "a")
    assert r.returncode == 1, r.stderr[-2000:]
    assert len(list((tmp_path / "a").glob("*.pdf"))) == 1
    r = lauf(tmp_path / "b", "--trotzdem")
    assert r.returncode == 1, r.stderr[-2000:]
    assert len(list((tmp_path / "b").glob("*.pdf"))) == 2
    (txt,) = (tmp_path / "b").glob("*.unvollstaendig.txt")
    inhalt = txt.read_text(encoding="utf-8")
    assert "I02" in inhalt and "Konto ohne Steuerwert" in inhalt and "12.30" not in inhalt


# --- 261027: je Auszug der Name seiner Person ----------------------------------
#
# Die Auszüge gehören zwei Personen. Vorher trug jeder denselben Namen — einmal
# stand in allen elf „…", weil das Beispiel aus dem README wörtlich lief.

ZWEI = {"elternteil_1": {"vorname": "Anna", "nachname": "Beispiel"},
        "elternteil_2": {"vorname": "Ben", "nachname": "Muster"}}


def test_jeder_auszug_bekommt_den_namen_seiner_person():
    from scripts.etax_bauen import _kunde_fuer

    daten = {"kunden": ZWEI, "institute": [{"person": "elternteil_1"},
                                           {"person": "elternteil_2"}]}
    assert _kunde_fuer({"person": "elternteil_1"}, "", "", daten)["vorname"] == "Anna"
    assert _kunde_fuer({"person": "elternteil_2"}, "", "", daten)["vorname"] == "Ben"
    # Argumente überstimmen die Familie nicht — sie füllen nur Lücken.
    assert _kunde_fuer({"person": "elternteil_2"}, "Hans", "X", daten)["vorname"] == "Ben"


def test_platzhalter_werden_abgewiesen():
    from scripts.etax_bauen import KundeFehlt, _kunde_fuer

    daten = {"kunden": {}, "institute": [{"person": "elternteil_1"}]}
    with pytest.raises(KundeFehlt, match="Platzhalter"):
        _kunde_fuer({"person": "elternteil_1"}, "…", "…", daten)


def test_ohne_namen_fuer_eine_von_zwei_personen_kein_gemeinsamer():
    from scripts.etax_bauen import KundeFehlt, _kunde_fuer

    daten = {"kunden": {"elternteil_1": ZWEI["elternteil_1"]},
             "kunde": ZWEI["elternteil_1"],
             "institute": [{"person": "elternteil_1"}, {"person": "elternteil_2"}]}
    with pytest.raises(KundeFehlt, match="mehreren"):
        _kunde_fuer({"person": "elternteil_2"}, "", "", daten)


def test_etax_daten_liefert_namen_je_rolle(tmp_path, monkeypatch):
    from extractors import eintraege, family
    from scripts import etax_daten

    fam = family.Family(members=[
        family.FamilyMember(role="mann", first_name="Anna", last_name="Beispiel"),
        family.FamilyMember(role="frau", first_name="Ben", last_name="Muster")])
    monkeypatch.setattr(family, "load_family", lambda *a, **k: fam)
    monkeypatch.setattr(etax_daten, "_zeilen_der_tabelle", lambda s: [])
    monkeypatch.setattr(eintraege, "alle_eintraege", lambda *a, **k: ([], []))
    daten = etax_daten.baue(tmp_path)
    assert daten["kunden"]["elternteil_2"] == {"vorname": "Ben", "nachname": "Muster"}


@pytest.mark.skipif(not PYETAX.exists(), reason=".venv-etax fehlt")
def test_zwei_personen_zwei_namen_im_auszug(tmp_path):
    """Echter Bau: der Auszug von Person 2 trägt ihren Namen, schemakonform."""
    pfad = eingabe(tmp_path, [
        {"institut": "Musterbank Nord", "person": "elternteil_1",
         "konten": [konto(nummer="CH9300762011623852957")], "schulden": []},
        {"institut": "Musterbank Nord", "person": "elternteil_2",
         "konten": [konto(nummer="Privatkonto 2")], "schulden": []}],
        kunden=ZWEI)
    ziel = tmp_path / "ziel"
    r = subprocess.run([str(PYETAX), str(ROOT / "scripts" / "etax_bauen.py"),
                        "--eingabe", str(pfad), "--ziel", str(ziel), "--jahr", "2025"],
                       capture_output=True, text=True, cwd=ROOT,
                       env={"PYTHONPATH": str(ROOT), "PATH": "/usr/bin:/bin"})
    assert r.returncode == 0, r.stdout + r.stderr
    assert "2  Person(en) als Kunde" in r.stdout
    assert len(list(ziel.glob("*.pdf"))) == 2
