"""Klassifikator: Form-11-Kopf schlägt ALV, gültige IBAN schlägt Prämie (261025-kme).
Alle Werte erfunden.

Ziffer 9 des Lohnausweises nennt „ALV", die Bemerkungen nennen oft
„Krankentaggeld" — beides traf bisher den ALV-Frühausstieg, und ein
Lohnausweis wurde „unknown". Ein Kontoauszug mit einer Belastung
„Prämie Hausrat" stand im Gleichstand Bank/Krankenkasse und wurde verworfen.
"""
from __future__ import annotations

from unittest import mock

import pytest

from extractors.classifier import classify
from extractors.schema import ReasonCode


def worte(text):
    return [{"text": t} for t in text.split()]


@pytest.fixture(autouse=True)
def ohne_sprachmodell():
    with mock.patch("extractors.llm_classifier.classify_via_llm", side_effect=Exception):
        yield


def test_lohnausweis_mit_alv_in_ziffer_9_und_krankentaggeld():
    text = ("Lohnausweis Musterfirma AG 8. Bruttolohn total 75600 "
            "9. Beiträge AHV/IV/EO/ALV/NBUV 4008 11. Nettolohn 71592 "
            "Bemerkungen Krankentaggeld")
    assert classify(worte(text)) == ("lohnausweis", None)


def test_lohnausweis_mit_arbeitslosenversicherung_in_den_bemerkungen():
    text = "Lohnausweis Musterfirma AG Bemerkungen: Beiträge an die Arbeitslosenversicherung"
    assert classify(worte(text)) == ("lohnausweis", None)


def test_alv_bescheinigung_bleibt_unknown():
    assert classify(worte("Leistungen der Arbeitslosenversicherung Taggelder"))[0] == "unknown"
    text = ("Certificato Arbeitslosenkasse Muster Leistungen der "
            "Arbeitslosenversicherung Taggelder 2025")
    assert classify(worte(text))[0] == "unknown"


def test_certificato_di_salario_ist_lohnausweis():
    text = "Certificato di salario Musterfirma Arbeitslosenversicherung"
    assert classify(worte(text)) == ("lohnausweis", None)


def test_kontoauszug_mit_gueltiger_iban_ist_bank():
    from evals.test_hypotheken import _iban

    # IBAN zur Laufzeit gebildet (erfundene BBAN, gültige Prüfziffer).
    text = f"Kontoauszug {_iban('00000123412341234')} Belastung Prämie Hausrat"
    assert classify(worte(text)) == ("bank_zinsausweis", None)
    ohne = "Kontoauszug Belastung Prämie Hausrat"
    assert classify(worte(ohne)) == ("unknown", ReasonCode.MULTIPLE_BELEGTYP_MATCH)
