"""Feldvertrag und Übertragungstabelle benennen gleich (260904-rmx).

Beide trugen eigene Beschriftungen für dasselbe Feld: der Vertrag „Saldo
31.12.", die Tabelle „Vermögensstand 31.12."; „Prämie Grundversicherung KVG"
gegen „Prämie KVG (Grundversicherung)". Fünf Felder waren betroffen.

Jede Prüfung, die beide vergleicht, war damit blind — die Feld-Matrix meldete
„12 erwartet, 0 geliefert", obwohl alle zwölf da waren. Und Korrekturen
hängen an der Beschriftung: zwei Namen heissen zwei Schlüssel für dieselbe
Sache.

CLAUDE.md sagt seit jeher, der Feldvertrag sei die einzige Quelle dafür,
welcher Wert wohin gehört. Dieser Test setzt das durch, statt es zu
behaupten.
"""
from __future__ import annotations

from extractors.korrekturen import ALTE_BEZEICHNUNGEN
from extractors.zielwerte import ZIELWERTE
from scripts.build_tax_output import TRANSFER_MAP


def tabelle(belegtyp: str) -> dict[str, str]:
    return {feld: label for feld, label, _ in TRANSFER_MAP.get(belegtyp, [])}


def test_kein_feld_traegt_zwei_namen():
    """Der eigentliche Vertrag: eine Sache, ein Name."""
    abweichend = []
    for belegtyp, werte in ZIELWERTE.items():
        labels = tabelle(belegtyp)
        for z in werte:
            in_tabelle = labels.get(z.feld)
            if in_tabelle is not None and in_tabelle != z.bezeichnung:
                abweichend.append((belegtyp, z.feld, z.bezeichnung, in_tabelle))
    assert abweichend == [], abweichend


def test_jeder_zielwert_steht_auch_in_der_tabelle():
    """Was der Vertrag verlangt, muss die Tabelle fuehren — sonst ist der
    Wert nirgends abzuschreiben."""
    fehlend = []
    for belegtyp, werte in ZIELWERTE.items():
        labels = tabelle(belegtyp)
        for z in werte:
            if z.feld not in labels:
                fehlend.append((belegtyp, z.feld))
    assert fehlend == [], fehlend


def test_umbenennungen_haben_einen_alias():
    """Jede Bezeichnung, die einmal anders hiess, braucht ihren Alias —
    sonst verlieren gespeicherte Korrekturen ihren Bezug."""
    for neu in ("Saldo 31.12.", "Steuerwert 31.12.",
                "Prämie Grundversicherung KVG",
                "Prämie Zusatzversicherung VVG", "Nettolohn",
                "Bruttoertrag ohne Verrechnungssteuer",
                "Bruttoertrag mit Verrechnungssteuer"):
        assert neu in ALTE_BEZEICHNUNGEN, neu
        assert ALTE_BEZEICHNUNGEN[neu], neu


def test_alias_zeigt_nie_auf_eine_aktuelle_bezeichnung():
    """Ein Alias, der auf einen heute gueltigen Namen zeigt, wuerde zwei
    verschiedene Felder verschmelzen."""
    aktuell = {z.bezeichnung for w in ZIELWERTE.values() for z in w}
    for neu, alte in ALTE_BEZEICHNUNGEN.items():
        for a in alte:
            assert a not in aktuell, (neu, a)
