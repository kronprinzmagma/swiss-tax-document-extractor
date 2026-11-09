"""Die Ziffern der Steuererklaerung (260904-rmx).

Die Uebertragungsansicht ist nach Ziffern sortiert, weil das Formular so
aufgebaut ist. Zwei Dinge muessen dafuer stimmen: jede Ziffer aus dem
Feldvertrag hat einen Namen, und "16.6" sortiert zwischen "15" und "22.1"
statt zwischen "1.1" und "4".
"""
from __future__ import annotations

from extractors.zielwerte import (
    ZIELWERTE,
    ZIFFERN,
    ziffer_name,
    ziffer_sortierung,
)


def test_jede_ziffer_aus_dem_vertrag_hat_einen_namen():
    benutzt = {z.ziffer for w in ZIELWERTE.values() for z in w}
    assert benutzt <= set(ZIFFERN), benutzt - set(ZIFFERN)


def test_keine_namen_fuer_ziffern_die_niemand_benutzt():
    """Sonst waechst hier eine zweite, unvollstaendige Wahrheit."""
    benutzt = {z.ziffer for w in ZIELWERTE.values() for z in w}
    assert set(ZIFFERN) == benutzt


def test_sortierung_ist_numerisch():
    ziffern = ["22.1", "4", "16.6", "1.1", "15", "30.1", "14"]
    assert sorted(ziffern, key=ziffer_sortierung) == [
        "1.1", "4", "14", "15", "16.6", "22.1", "30.1"]


def test_unbekannte_ziffer_faellt_ans_ende():
    assert ziffer_sortierung("") > ziffer_sortierung("30.1")
    assert ziffer_sortierung("keine") > ziffer_sortierung("30.1")


def test_name_einer_bekannten_ziffer():
    assert "Wertschriften" in ziffer_name("30.1")


def test_unbekannte_ziffer_ohne_namen():
    assert ziffer_name("99") == ""
