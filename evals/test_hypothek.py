"""Hypothek: aus out-of-scope in den Feldvertrag (260904-rmx).

Der Belegtyp war seit jeher als „Miete-Familie" ausgeschlossen — Schema und
Klassifikator kannten ihn, aber es gab keine Spec und keinen Zielwert. Drei
Hypothekarbelege im Ordner haetten damit keinen einzigen Wert geliefert, und
Schuldzinsen sind ein Abzug, den man nicht vergisst.

Ziffern aus der Wegleitung ZH 2025, Hauptformular:

    12  Schuldzinsen (soweit nicht schon unter Ziff. 2 abgezogen)
    34  Schulden

Beide verlangen das Schuldenverzeichnis. Gegengeprueft an derselben
Wegleitung: Ziffer 15 (Versicherungspraemien) und 30.1 (Wertschriften und
Guthaben) stimmen mit dem bestehenden Vertrag ueberein.
"""
from __future__ import annotations

from extractors.schema import MANDATORY_FIELDS_FOR_BELEGTYP
from extractors.steuer_zielmodell import SPECS
from extractors.zielwerte import ZIELWERTE, ZIFFERN, ziffer_sortierung

TYP = "hypothek_zinsbestaetigung"


def test_belegtyp_ist_in_der_pipeline():
    """Ohne Spec laeuft keine Extraktion — der Beleg bliebe stumm."""
    assert TYP in SPECS


def test_zwei_zielwerte_in_zwei_teilen_des_formulars():
    ziffern = {z.ziffer for z in ZIELWERTE[TYP]}
    assert ziffern == {"12", "34"}


def test_zins_geht_in_ziffer_12():
    zins = next(z for z in ZIELWERTE[TYP] if z.feld == "schuldzinsen")
    assert zins.ziffer == "12"
    assert zins.formular == "Schuldenverzeichnis"


def test_saldo_geht_in_ziffer_34():
    saldo = next(z for z in ZIELWERTE[TYP] if z.feld == "schuldsaldo_3112")
    assert saldo.ziffer == "34"
    assert saldo.formular == "Schuldenverzeichnis"


def test_beide_ziffern_haben_einen_namen():
    assert ZIFFERN["12"] == "Schuldzinsen"
    assert ZIFFERN["34"] == "Schulden"


def test_ziffern_sortieren_an_die_richtige_stelle():
    """12 vor 14, 34 nach 30.1 — sonst steht die Schuld mitten im Einkommen."""
    sortiert = sorted(ZIFFERN, key=ziffer_sortierung)
    assert sortiert == ["1.1", "4", "12", "14", "15", "16.6", "22.1",
                        "30.1", "34"]


def test_zielwertfelder_existieren_im_schema():
    felder = set(MANDATORY_FIELDS_FOR_BELEGTYP[TYP])
    for z in ZIELWERTE[TYP]:
        assert z.feld in felder, z.feld


def test_die_tabelle_kennt_beide_zeilen():
    from scripts.build_tax_output import STEUERBEREICH, TRANSFER_MAP
    felder = {f for f, _, _ in TRANSFER_MAP[TYP]}
    assert felder == {"schuldzinsen", "schuldsaldo_3112"}
    assert TYP in STEUERBEREICH


def test_die_review_hat_eine_ueberschrift():
    from scripts.build_review_html import TYP_LABEL, ZIELWERT_TYP
    assert TYP_LABEL[TYP] == "Hypotheken"
    assert ZIELWERT_TYP["Schuldzinsen Hypothek"] == TYP
