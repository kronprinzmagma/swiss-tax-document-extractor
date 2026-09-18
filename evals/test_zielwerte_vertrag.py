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
