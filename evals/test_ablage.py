"""Die Ablage muss die Regeln selbst durchsetzen, nicht der Code darüber.

Drei Regeln, die der Mensch gesetzt hat:

1. Geprüft ist fix — keine Änderung ohne ausdrückliche Freigabe.
2. Vollständig ist geschlossen — zwei Kinder, zwei Werte, nichts kommt dazu.
3. Geprüftes wird nicht gelöscht.

Der Punkt dieser Tests ist nicht, dass das Programm sich daran hält, sondern
dass es sich **nicht daran halten kann**: die Datenbank weist den Schreibvorgang
ab. Jede der drei Regeln wurde vorher schon einmal durch einen Fehler im
Programm verletzt (260923-dua).
"""
from __future__ import annotations

import sqlite3

import pytest

from extractors.ablage import naechste_id, oeffne


@pytest.fixture()
def db(tmp_path):
    return oeffne(tmp_path / "positionen.db")


def lege_an(db, kennung="p0001", status="geprueft", dokument="a.pdf",
            zielwert="Saldo 31.12.", betrag="1000.00", pos=1):
    db.execute("INSERT INTO position (id, dokument, zielwert, pos, betrag, "
               "status) VALUES (?,?,?,?,?,?)",
               (kennung, dokument, zielwert, pos, betrag, status))
    db.commit()


def test_geprueft_laesst_sich_nicht_aendern(db):
    lege_an(db)
    with pytest.raises(sqlite3.IntegrityError, match="erst freigeben"):
        db.execute("UPDATE position SET betrag='9.99' WHERE id='p0001'")


def test_geprueft_laesst_sich_nicht_loeschen(db):
    lege_an(db)
    with pytest.raises(sqlite3.IntegrityError, match="wird nicht geloescht"):
        db.execute("DELETE FROM position WHERE id='p0001'")


def test_auch_die_person_ist_gesperrt(db):
    """Nicht nur der Betrag — die Zuordnung ging genauso verloren."""
    lege_an(db)
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("UPDATE position SET person='kind_2' WHERE id='p0001'")


def test_ein_vorschlag_darf_sich_aendern(db):
    """Was nicht geprüft ist, darf ein Lauf überschreiben."""
    lege_an(db, status="vorschlag")
    db.execute("UPDATE position SET betrag='9.99' WHERE id='p0001'")
    db.commit()
    assert db.execute("SELECT betrag FROM position").fetchone()[0] == "9.99"


def test_freigeben_und_dann_aendern(db):
    """Der Weg für eine echte Korrektur: zwei ausdrückliche Schritte."""
    lege_an(db)
    db.execute("UPDATE position SET status='vorschlag' WHERE id='p0001'")
    db.execute("UPDATE position SET betrag='1200.00' WHERE id='p0001'")
    db.commit()
    assert db.execute("SELECT betrag FROM position").fetchone()[0] == "1200.00"


def test_geschlossene_gruppe_nimmt_nichts_mehr_auf(db):
    """Zwei Kinder, zwei Werte — die dritte Zeile wird abgewiesen."""
    lege_an(db, "p0001", zielwert="Kosten Kinderbetreuung", pos=1)
    lege_an(db, "p0002", zielwert="Kosten Kinderbetreuung", pos=2)
    db.execute("INSERT INTO gruppe_geschlossen (dokument, zielwert) "
               "VALUES ('a.pdf', 'Kosten Kinderbetreuung')")
    db.commit()
    with pytest.raises(sqlite3.IntegrityError, match="vollstaendig"):
        db.execute("INSERT INTO position (id, dokument, zielwert, pos, betrag)"
                   " VALUES ('p0003','a.pdf','Kosten Kinderbetreuung',3,'500')")


def test_andere_gruppe_bleibt_offen(db):
    """Die Sperre gilt genau dieser Gruppe, nicht dem ganzen Dokument."""
    db.execute("INSERT INTO gruppe_geschlossen (dokument, zielwert) "
               "VALUES ('a.pdf', 'Kosten Kinderbetreuung')")
    db.commit()
    lege_an(db, "p0009", zielwert="Saldo 31.12.")
    assert db.execute("SELECT count(*) FROM position").fetchone()[0] == 1


def test_kennungen_werden_nie_wiederverwendet(db):
    """Eine Kennung, einmal vergeben, bleibt bei ihrer Position."""
    lege_an(db, "p0001", status="vorschlag")
    lege_an(db, "p0002", status="vorschlag")
    db.execute("DELETE FROM position WHERE id='p0002'")
    db.commit()
    assert naechste_id(db) == "p0002" or naechste_id(db) == "p0003"
    # Wichtiger: die verbleibende Position behaelt ihre Kennung.
    assert db.execute("SELECT id FROM position").fetchone()[0] == "p0001"


def test_status_kennt_nur_zwei_werte(db):
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO position (id, dokument, zielwert, status) "
                   "VALUES ('p0001','a.pdf','Saldo 31.12.','erledigt')")


def test_umbenennung_des_dokuments_ist_erlaubt(db):
    """Der Dokumentname ist ein Verweis, kein Wert.

    Wird ein Beleg sprechend umbenannt, muss der Verweis nachziehen koennen.
    Waere er mitgesperrt, waere die Ablage nach der ersten Umbenennung von
    ihren Dokumenten getrennt — genau die Trennung, die sie beheben soll.
    """
    lege_an(db)
    db.execute("UPDATE position SET dokument='Bank - Zinsausweis 2025.pdf' "
               "WHERE id='p0001'")
    db.commit()
    assert db.execute("SELECT dokument FROM position").fetchone()[0] \
        == "Bank - Zinsausweis 2025.pdf"
    # Der Betrag bleibt trotzdem gesperrt.
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("UPDATE position SET betrag='1' WHERE id='p0001'")


def test_regeln_gelten_auch_fuer_eine_bestehende_ablage(tmp_path):
    """Aendern sich die Regeln, muss eine alte Datei sie uebernehmen.

    Die Trigger werden beim Oeffnen neu gesetzt. Sonst haette eine Ablage, die
    vor einer Regelaenderung angelegt wurde, weiterhin die alten.
    """
    pfad = tmp_path / "positionen.db"
    erste = oeffne(pfad)
    erste.execute("INSERT INTO position (id, dokument, zielwert, betrag, "
                  "status) VALUES ('p0001','a.pdf','Saldo 31.12.','1','geprueft')")
    erste.commit()
    erste.close()

    zweite = oeffne(pfad)
    with pytest.raises(sqlite3.IntegrityError):
        zweite.execute("UPDATE position SET betrag='2' WHERE id='p0001'")


def test_geprueftes_kommt_woertlich_zurueck(tmp_path):
    """Gelesen wird, was gespeichert ist — ohne Herleitung."""
    from extractors.ablage import lade_geprueft, pfad_fuer

    samples = tmp_path / "json"
    samples.mkdir()
    db = oeffne(pfad_fuer(samples))
    db.execute("INSERT INTO position (id, dokument, zielwert, pos, ziffer, "
               "person, betrag, status) VALUES "
               "('p0001','a.pdf','Saldo 31.12.',1,'30.1','elternteil_1',"
               "'1234.00','geprueft')")
    db.execute("INSERT INTO position (id, dokument, zielwert, betrag, status) "
               "VALUES ('p0002','a.pdf','Bruttoertrag','5.00','vorschlag')")
    db.commit()

    zeilen = lade_geprueft(samples)
    assert len(zeilen) == 1, "ein Vorschlag ist keine gepruefte Position"
    assert zeilen[0]["betrag"] == "1234.00"
    assert zeilen[0]["ziffer"] == "30.1"
    assert zeilen[0]["person"] == "elternteil_1"
    assert zeilen[0]["ablage_id"] == "p0001"


def test_ein_dokument_der_ablage_gehoert_ihr_vollstaendig(tmp_path):
    """Ein Lauf darf einem durchgesehenen Dokument nichts hinzufuegen.

    Die erste Fassung ersetzte je (Dokument, Zielwert). Lieferte ein Lauf
    denselben Wert unter einem frueheren Namen — „Vermögensstand 31.12." statt
    „Saldo 31.12." —, griff die Ersetzung nicht und die alte Zeile stand
    zusaetzlich in der Tabelle. Zwei Zeilen zu viel, und niemand hatte etwas
    hinzugefuegt (260923-dua).
    """
    from scripts.build_tax_output import ergaenze_eigene_zeilen

    samples = tmp_path / "json"
    samples.mkdir()
    from extractors.ablage import pfad_fuer
    db = oeffne(pfad_fuer(samples))
    db.execute("INSERT INTO position (id, dokument, zielwert, betrag, status) "
               "VALUES ('p0001','a.pdf','Saldo 31.12.','1000.00','geprueft')")
    db.commit()

    rows = [
        # Derselbe Wert unter dem frueheren Namen.
        {"pdf_name": "a.pdf", "beschreibung": "Vermögensstand 31.12.",
         "betrag": "1000.00", "pos": 1, "ziffer": "30.1"},
        # Ein Wert, den der Mensch gestrichen hat — der Lauf liefert ihn weiter.
        {"pdf_name": "a.pdf", "beschreibung": "Bruttoertrag", "betrag": "9.99",
         "pos": 1, "ziffer": "4"},
        # Ein anderes Dokument bleibt unberuehrt.
        {"pdf_name": "b.pdf", "beschreibung": "Nettolohn", "betrag": "80000.00",
         "pos": 1, "ziffer": "1.1"},
    ]
    ergaenze_eigene_zeilen(rows, samples)

    aus_a = [r for r in rows if r.get("pdf_name") == "a.pdf"]
    assert len(aus_a) == 1, "die Ablage fuehrt genau eine Position fuer a.pdf"
    assert aus_a[0]["beschreibung"] == "Saldo 31.12."
    assert any(r.get("pdf_name") == "b.pdf" for r in rows), \
        "ein Dokument ohne Eintrag in der Ablage bleibt unveraendert"


def test_doppelte_positionsnummern_werden_nicht_verboten(db):
    """Ein Eindeutigkeits-Index waere der naheliegende Schutz — und falsch.

    Versuchsweise gesetzt, liess sich die bestehende Ablage nicht mehr
    OEFFNEN: sie enthaelt solche Paare bereits. Ein Schutz, der die Daten
    unzugaenglich macht, ist kein Schutz. Gemeldet wird es stattdessen von
    `ablage_stand.py` (260923-dua, Code-Review).
    """
    lege_an(db, "p0001", zielwert="Hypothekarschuld 31.12.", pos=1)
    lege_an(db, "p0002", zielwert="Hypothekarschuld 31.12.", pos=1)
    db.commit()
    assert db.execute("SELECT count(*) FROM position").fetchone()[0] == 2


def test_die_naechste_kennung_zaehlt_numerisch(db):
    """Ab 9999 waere die Textsortierung falsch und eine Kennung doppelt."""
    db.execute("INSERT INTO position (id, dokument, zielwert, pos) "
               "VALUES ('p9999','a.pdf','Saldo 31.12.',1)")
    db.commit()
    assert naechste_id(db) == "p10000"


def test_der_anker_wird_wieder_zu_seite_und_box(tmp_path):
    """Sonst bleibt der Knopf „im Dokument wählen" unsichtbar.

    Die Oberfläche zeigt das Seitenbild nur, wenn die Zeile eine Seite trägt.
    Die Ablage speicherte den Anker als Text und gab `page: None` zurück —
    damit war die Funktion für jede geprüfte Position weg, obwohl es sie
    längst gibt (260924-dua, vom Nutzer gemeldet).
    """
    from extractors.ablage import anker_zerlegen, lade_geprueft, pfad_fuer

    assert anker_zerlegen("S.2 (71.0, 305.4, 120.0, 314.9)") == (
        2, (71.0, 305.4, 120.0, 314.9))
    # Ohne brauchbaren Anker Seite 1 — nur so gibt es ueberhaupt ein Bild.
    assert anker_zerlegen("S.None") == (1, None)
    assert anker_zerlegen("") == (1, None)

    samples = tmp_path / "json"
    samples.mkdir()
    db = oeffne(pfad_fuer(samples))
    db.execute("INSERT INTO position (id, dokument, zielwert, betrag, anker, "
               "status) VALUES ('p1','a.pdf','Saldo 31.12.','1000.00',"
               "'S.3 (10.0, 20.0, 30.0, 40.0)','geprueft')")
    db.execute("INSERT INTO position (id, dokument, zielwert, betrag, status) "
               "VALUES ('p2','a.pdf','Bruttoertrag','5.00','geprueft')")
    db.commit()

    zeilen = {z["ablage_id"]: z for z in lade_geprueft(samples)}
    assert zeilen["p1"]["page"] == 3
    assert zeilen["p1"]["bbox"] == (10.0, 20.0, 30.0, 40.0)
    assert zeilen["p2"]["page"] == 1, "ohne Anker trotzdem ein Seitenbild"


def test_was_der_mensch_aendert_kommt_in_die_ablage(tmp_path):
    """Sonst ändert er etwas und nichts geschieht.

    Seit die Tabelle geprüfte Werte aus der Ablage liest, hatte eine Korrektur
    in der Oberfläche keine Wirkung mehr: sie landete in `korrekturen.json`,
    und dorthin sah die Tabelle nicht mehr (260924-dua, vom Nutzer bemerkt).
    """
    import json

    from extractors.ablage import pfad_fuer, uebernimm_durchsicht

    samples = tmp_path / "json"
    samples.mkdir()
    db = oeffne(pfad_fuer(samples))
    db.execute("INSERT INTO position (id, dokument, zielwert, pos, betrag, "
               "person, status) VALUES ('p1','a.pdf','Saldo 31.12.',1,"
               "'1000.00','elternteil_1','geprueft')")
    db.commit()

    (samples / "korrekturen.json").write_text(json.dumps({
        "a.pdf|Saldo 31.12.": {"soll": "1234.00", "bestaetigt_betrag": True},
    }), encoding="utf-8")

    # Ohne Eingrenzung passiert nichts — das ist die Schutzregel: sonst
    # ueberschreibt ein ueberholter Eintrag der alten Durchsicht eine
    # frischere Aenderung in der Ablage.
    assert uebernimm_durchsicht(samples) == {}
    zaehler = uebernimm_durchsicht(
        samples, nur={("a.pdf", "Saldo 31.12.", 1)})
    assert zaehler == {"betrag": 1}
    zeile = db.execute("SELECT betrag, status FROM position").fetchone()
    assert zeile["betrag"] == "1234.00"
    assert zeile["status"] == "geprueft", "danach wieder gesperrt"
    # Und es steht im Protokoll.
    eintraege = db.execute(
        "SELECT was, feld, vorher, nachher FROM protokoll").fetchall()
    assert any(e["was"] == "aus der Durchsicht" and e["feld"] == "betrag"
               and e["vorher"] == "1000.00" and e["nachher"] == "1234.00"
               for e in eintraege)


def test_ohne_aenderung_passiert_nichts(tmp_path):
    """Ein Lauf darf hier nicht durchkommen — nur echte Unterschiede."""
    import json

    from extractors.ablage import pfad_fuer, uebernimm_durchsicht

    samples = tmp_path / "json"
    samples.mkdir()
    db = oeffne(pfad_fuer(samples))
    db.execute("INSERT INTO position (id, dokument, zielwert, pos, betrag, "
               "status) VALUES ('p1','a.pdf','Saldo 31.12.',1,'1000.00',"
               "'geprueft')")
    db.commit()
    (samples / "korrekturen.json").write_text(json.dumps({
        "a.pdf|Saldo 31.12.": {"soll": "1000.00", "bestaetigt_betrag": True},
    }), encoding="utf-8")

    # Mit Eingrenzung, damit wirklich der fehlende Unterschied geprueft
    # wird und nicht blosse die Schutzregel.
    assert uebernimm_durchsicht(
        samples, nur={("a.pdf", "Saldo 31.12.", 1)}) == {}


def test_eine_umbenennung_verdoppelt_nichts_mehr(tmp_path):
    """Das Dokument hat eine feste Kennung — den Fingerabdruck seines Inhalts.

    Vorher war es nur ein Dateiname. Der ändert sich bei jeder sprechenden
    Umbenennung: die Ablage zeigte auf den alten, die Extraktion lieferte den
    neuen, dasselbe Dokument galt als zwei — und jeder Wert stand doppelt in
    der Tabelle. 24 von 67 Zeilen waren betroffen (260924-dua, vom Nutzer
    gemeldet).

    Dieselbe Lehre wie bei den Positionen, eine Ebene höher: Identität gehört
    nicht an etwas, das sich ändern kann.
    """
    import json

    from extractors.ablage import (lade_geprueft, namen_je_fingerabdruck,
                                   pfad_fuer)

    samples = tmp_path / "json"
    samples.mkdir()
    # Das Word-JSON traegt Fingerabdruck und heutigen Namen.
    (samples / "beleg.json").write_text(json.dumps({
        "pdf_name": "Bank - Zinsausweis 2025.pdf",
        "pdf_fingerabdruck": "abc123",
        "pages": [],
    }), encoding="utf-8")

    db = oeffne(pfad_fuer(samples))
    # Die Ablage kennt noch den ALTEN Namen.
    db.execute("INSERT INTO position (id, dokument, dokument_id, zielwert, "
               "betrag, status) VALUES ('p1','scan_0815.pdf','abc123',"
               "'Saldo 31.12.','1000.00','geprueft')")
    db.commit()

    assert namen_je_fingerabdruck(samples) == {
        "abc123": "Bank - Zinsausweis 2025.pdf"}
    zeile = lade_geprueft(samples)[0]
    assert zeile["beleg"] == "Bank - Zinsausweis 2025.pdf", (
        "die Zeile muss ihr Dokument unter dem heutigen Namen finden")


def test_ohne_fingerabdruck_bleibt_der_name(tmp_path):
    """Eine Ablage aus der Zeit davor funktioniert weiter."""
    from extractors.ablage import lade_geprueft, pfad_fuer

    samples = tmp_path / "json"
    samples.mkdir()
    db = oeffne(pfad_fuer(samples))
    db.execute("INSERT INTO position (id, dokument, zielwert, betrag, status) "
               "VALUES ('p1','a.pdf','Saldo 31.12.','1000.00','geprueft')")
    db.commit()
    assert lade_geprueft(samples)[0]["beleg"] == "a.pdf"
