"""Erzwingt den Feldvertrag aus `extractors/zielwerte.py`.

Diese Tests hätten die drei Fehler dieser Session sofort gemeldet:

* `selbstgetragene_kosten` stand in TRANSFER_MAP, fehlte aber im Schema — der
  per Regex erkannte Wert verfiel lautlos (`test_zielwert_feld_existiert_im_schema`).
* Die Übertragungstabelle enthielt Felder, die kein Zielwert sind
  (`test_transfer_map_fuehrt_nur_zielwerte`).
* Die Extraktion lieferte den Bruttorechnungsbetrag, den die Spezifikation
  namentlich ausschliesst (`test_kein_zielwert_steht_auf_der_ausschlussliste`).
"""
from __future__ import annotations

import pytest

from extractors.zielwerte import (
    AUSSCHLUSS,
    ZIELWERTE,
    ist_ausgeschlossen,
    zielwert_felder,
)
from scripts.build_tax_output import TRANSFER_MAP
from scripts.process_samples_full import SCHEMA_DISPATCH

# Felder, die nicht im pydantic-Schema stehen, sondern erst in
# process_samples_full dynamisch erzeugt werden (Mehrkonto-Dokumente).
DYNAMISCHE_FELDER = {
    "vermoegensstand_3112_konto2",
    "vermoegensstand_3112_konto3",
}


@pytest.mark.parametrize("belegtyp", sorted(ZIELWERTE))
def test_zielwert_feld_existiert_im_schema(belegtyp):
    """Kein Zielwert ohne Schemafeld — sonst verfaellt der Wert bei setattr."""
    cls = SCHEMA_DISPATCH.get(belegtyp)
    assert cls is not None, f"kein Schema fuer {belegtyp} registriert"
    felder = set(cls.model_fields)
    for z in ZIELWERTE[belegtyp]:
        assert z.feld in felder, (
            f"{belegtyp}: Zielwert '{z.feld}' (Ziffer {z.ziffer}) fehlt in "
            f"{cls.__name__} — der extrahierte Wert wuerde lautlos verworfen"
        )


@pytest.mark.parametrize("belegtyp", sorted(TRANSFER_MAP))
def test_transfer_map_fuehrt_nur_zielwerte(belegtyp):
    """Die Tabelle zeigt nur, was laut Vertrag in die Steuererklaerung gehoert."""
    erlaubt = zielwert_felder(belegtyp) | DYNAMISCHE_FELDER
    for feld, _bezeichnung, _z in TRANSFER_MAP[belegtyp]:
        assert feld in erlaubt, (
            f"{belegtyp}: '{feld}' steht in der Uebertragungstabelle, ist aber "
            f"kein Zielwert. Entweder in zielwerte.py aufnehmen oder aus "
            f"TRANSFER_MAP entfernen."
        )


@pytest.mark.parametrize("belegtyp", sorted(ZIELWERTE))
def test_kein_zielwert_steht_auf_der_ausschlussliste(belegtyp):
    """Ausgeschlossene Felder duerfen nie als Zielwert deklariert werden."""
    for z in ZIELWERTE[belegtyp]:
        assert not ist_ausgeschlossen(belegtyp, z.feld), (
            f"{belegtyp}: '{z.feld}' ist als Zielwert deklariert, steht aber "
            f"auf der Ausschlussliste"
        )


@pytest.mark.parametrize("belegtyp", sorted(AUSSCHLUSS))
def test_ausgeschlossene_felder_stehen_nicht_in_der_tabelle(belegtyp):
    """Der Bruttorechnungsbetrag darf nie in der Uebertragungstabelle landen."""
    for feld, _bezeichnung, _z in TRANSFER_MAP.get(belegtyp, ()):
        assert not ist_ausgeschlossen(belegtyp, feld), (
            f"{belegtyp}: '{feld}' steht auf der Ausschlussliste, wird aber "
            f"uebertragen"
        )


def test_jeder_zielwert_hat_eine_ziffer():
    """Ohne Ziffer weiss niemand, wohin der Wert in ZHprivateTax gehoert."""
    for belegtyp, werte in ZIELWERTE.items():
        for z in werte:
            assert z.ziffer, f"{belegtyp}/{z.feld}: keine Ziffer hinterlegt"
            assert z.bezeichnung, f"{belegtyp}/{z.feld}: keine Bezeichnung"


def test_vertrag_deckt_die_drei_prioritaeren_typen_ab():
    """Bank, Versicherung und Lohnausweis sind der Kern des Auftrags."""
    for belegtyp in ("lohnausweis", "bank_zinsausweis", "kk_praemienbescheinigung"):
        assert ZIELWERTE.get(belegtyp), f"{belegtyp} fehlt im Vertrag"


def test_kk_trennt_grund_und_zusatzversicherung():
    """User-Entscheid: KVG und VVG bleiben getrennte Zeilen."""
    felder = zielwert_felder("kk_praemienbescheinigung")
    assert {"praemie_kvg_total", "praemie_vvg_total"} <= felder


def test_kk_fuehrt_selbstgetragene_kosten_in_ziffer_22_1():
    """Praemien (Ziffer 15) und Selbstkosten (Ziffer 22.1) sind zwei Abzuege."""
    nach_feld = {z.feld: z for z in ZIELWERTE["kk_praemienbescheinigung"]}
    assert nach_feld["praemie_kvg_total"].ziffer == "15"
    assert nach_feld["selbstgetragene_kosten"].ziffer == "22.1"


def test_zusatzkonten_heissen_wie_der_feldvertrag():
    """Zwei Namen fuer dieselbe Sache: die Tabelle zeigte "Saldo 31.12." und
    daneben "Vermögensstand 31.12. (Konto 2)" (260923-dua, vom Nutzer
    bemerkt).

    Ein zweites Konto desselben Belegs ist kein anderer Zielwert, sondern eine
    weitere POSITION desselben. Die eigene Bezeichnung war nur noetig, solange
    ein Schluessel nur eine Position fassen konnte.
    """
    namen = {feld: bez for feld, bez, _ in TRANSFER_MAP["bank_zinsausweis"]}
    assert namen["vermoegensstand_3112"] == "Saldo 31.12."
    assert namen["vermoegensstand_3112_konto2"] == "Saldo 31.12."
    assert namen["vermoegensstand_3112_konto3"] == "Saldo 31.12."


def test_welches_konto_geht_nicht_verloren():
    """Drei Zeilen "Saldo 31.12." waeren sonst nicht auseinanderzuhalten."""
    from scripts.build_tax_output import build_rows

    zeilen = build_rows({
        "pdf_name": "b.pdf", "status": "ok", "belegtyp": "bank_zinsausweis",
        "fields": [{"feld": "vermoegensstand_3112", "value": "1000"},
                   {"feld": "vermoegensstand_3112_konto2", "value": "2000"},
                   {"feld": "vermoegensstand_3112_konto3", "value": "3000"}],
    })
    nach_betrag = {r["betrag"]: r for r in zeilen}
    assert nach_betrag["1000"]["unterscheidung"] == ""
    assert nach_betrag["2000"]["unterscheidung"] == "Konto 2"
    assert nach_betrag["3000"]["unterscheidung"] == "Konto 3"


def test_alte_bestaetigung_eines_zusatzkontos_findet_ihre_zeile():
    """Sonst verwaist jede Bestaetigung, die unter dem alten Namen liegt."""
    from extractors.korrekturen import finde, zerlege

    assert zerlege("b.pdf|Vermögensstand 31.12. (Konto 2)") \
        == ("b.pdf", "Saldo 31.12.", 2)
    korr = {"b.pdf|Vermögensstand 31.12. (Konto 2)": {"soll": "2000"}}
    assert finde(korr, "b.pdf", "Saldo 31.12.", 2)["soll"] == "2000"


# --- Die Ziffer kommt aus dem Vertrag, nicht aus dem Browser (260923-dua) ---

def test_ziffer_fuer_kennt_die_bezeichnungen():
    from extractors.zielwerte import ziffer_fuer
    assert ziffer_fuer("Prämie Zusatzversicherung VVG") == "15"
    assert ziffer_fuer("Nettolohn") == "1.1"
    assert ziffer_fuer("Hypothekarschuld 31.12.") == "34"
    assert ziffer_fuer("Schuldzinsen Hypothek") == "12"


def test_ziffer_fuer_raet_nicht():
    """Unbekannt oder mehrdeutig: keine Antwort. Raten waere schlimmer als
    die Luecke."""
    from extractors.zielwerte import ziffer_fuer
    assert ziffer_fuer("Gibt es nicht") == ""
    assert ziffer_fuer("") == ""


def test_jede_vertragsbezeichnung_findet_ihre_ziffer():
    """Sonst gibt es Zielwerte, deren Ziffer sich nicht herleiten laesst."""
    from extractors.zielwerte import ZIELWERTE, ziffer_fuer
    bezeichnungen = {z.bezeichnung for werte in ZIELWERTE.values()
                     for z in werte}
    ohne = sorted(b for b in bezeichnungen if not ziffer_fuer(b))
    assert not ohne, f"keine eindeutige Ziffer: {ohne}"


def test_selbst_erfasste_position_bekommt_ihre_ziffer(tmp_path):
    """Eine Zeile mit Betrag, Person und gueltigem Zielwert stand ohne Ziffer
    da, weil der Browser sie nicht mitgeschickt hatte."""
    import json as _json

    from scripts.build_tax_output import ergaenze_eigene_zeilen

    samples = tmp_path / "json"
    samples.mkdir()
    (samples / "korrekturen.json").write_text(_json.dumps({
        # ohne "ziffer" — genau der gemeldete Fall
        "k.pdf|Prämie Zusatzversicherung VVG": {"soll": "1850", "neu": True,
                                                "person": "kind_1"},
    }))
    rows: list[dict] = []
    assert ergaenze_eigene_zeilen(rows, samples) == 1
    assert rows[0]["ziffer"] == "15"


def test_umbenennung_ohne_ziffer_holt_sie_aus_dem_vertrag(tmp_path):
    import json as _json

    from scripts.build_tax_output import wende_korrekturen_an

    samples = tmp_path / "json"
    samples.mkdir()
    (samples / "korrekturen.json").write_text(_json.dumps({
        "k.pdf|Prämie Total (nicht aufgeteilt)": {
            "zielwert_neu": "Prämie Zusatzversicherung VVG"},
    }))
    rows = [{"pdf_name": "k.pdf", "beschreibung": "Prämie Total (nicht aufgeteilt)",
             "betrag": "1850", "ziffer": ""}]
    wende_korrekturen_an(rows, samples)
    assert rows[0]["beschreibung"] == "Prämie Zusatzversicherung VVG"
    assert rows[0]["ziffer"] == "15"
