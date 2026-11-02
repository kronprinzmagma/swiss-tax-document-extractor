"""Tests für die Ausgabe-Hygiene der Übertragungstabelle (260904-rmx).

Alle drei Befunde stammen aus der Analyse eines echten Laufs und sind rein
darstellungsseitig — die Extraktion hatte die Werte korrekt gelesen.
"""
from __future__ import annotations

from scripts.build_tax_output import (
    drop_zero_rows,
    is_zero_amount,
    konto_key,
    mark_duplicate_positions,
    normalize_aussteller,
)


def zeile(pdf: str, besch: str, betrag: str | None, aussteller: str = "BANK-P") -> dict:
    return {"pdf_name": pdf, "beschreibung": besch, "betrag": betrag,
            "aussteller": aussteller, "status": "auto"}


# --- Aussteller-Normalisierung ---------------------------------------------

def test_aussteller_varianten_werden_zusammengefuehrt():
    for variante in ("BANK-P", "BANK-P AG", "BANK-P AG", "bank-p ag"):
        assert normalize_aussteller(variante) == "BANK-P"


def test_unbekannter_aussteller_bleibt_unveraendert():
    """Lieber zwei getrennte Zeilen als ein falsch zusammengeführter Aussteller."""
    assert normalize_aussteller("Bank Beispiel") == "Bank Beispiel"
    assert normalize_aussteller("Broker Beispiel AG") == "Broker Beispiel AG"


def test_leerer_aussteller_bleibt_leer():
    assert normalize_aussteller(None) is None
    assert normalize_aussteller("") == ""


# --- Nullbetrags-Zeilen -----------------------------------------------------

def test_null_erkennung():
    assert is_zero_amount("0.00") is True
    assert is_zero_amount("0") is True
    assert is_zero_amount("15.21") is False
    assert is_zero_amount(None) is False


def test_null_erkennung_ignoriert_marker():
    """manual_review-Marker sind keine Beträge und dürfen nicht als 0 gelten."""
    assert is_zero_amount("manual_review:nicht_parsebar") is False


def test_nullzeilen_werden_verworfen():
    rows = [
        zeile("a.pdf", "Verrechnungssteuer 35%", "0.00"),
        zeile("b.pdf", "Bruttozins / Bruttoertrag", "15.21"),
        zeile("c.pdf", "Bruttozins / Bruttoertrag", "0.00"),
    ]
    rest, entfernt = drop_zero_rows(rows)
    assert entfernt == 2
    assert [r["betrag"] for r in rest] == ["15.21"]


def test_zeilen_ohne_betrag_bleiben_erhalten():
    """Fehlender Betrag ist nicht dasselbe wie Betrag null."""
    rows = [zeile("a.pdf", "Vermögensstand 31.12.", None)]
    rest, entfernt = drop_zero_rows(rows)
    assert entfernt == 0
    assert len(rest) == 1


# --- Kontokennung + Duplikate ----------------------------------------------

def test_konto_key_aus_iban():
    assert konto_key("TAX_P_CH0009000000000000001_1000000001_0_2023.pdf") \
        == "CH0009000000000000001"  # gitleaks:allow


def test_konto_key_aus_kontonummer():
    assert konto_key("Zinsabrechnung_123456_20240113.pdf") == "123456"


def test_konto_key_ohne_treffer():
    assert konto_key("Lohnausweis Beispiel.pdf") is None


def test_gleiche_position_aus_zwei_dokumenten_wird_markiert():
    """REP_P und TAX_P beschreiben dasselbe Konto — beide Zeilen bleiben."""
    rows = [
        zeile("REP_P_CH0009000000000000001_1000000001_0_2023.pdf",
              "Bruttozins / Bruttoertrag", "15.21"),
        zeile("TAX_P_CH0009000000000000001_1000000001_0_2023.pdf",
              "Bruttozins / Bruttoertrag", "15.21"),
    ]
    markiert = mark_duplicate_positions(rows)
    assert markiert == 2
    assert len(rows) == 2, "Zeilen duerfen nicht entfernt werden"
    for r in rows:
        assert "gleiches Konto" in r["dupe_hinweis"]
        assert "nur einmal übertragen" in r["dupe_hinweis"]


def test_verschiedene_konten_werden_nicht_markiert():
    rows = [
        zeile("TAX_P_CH0009000000000000001_1000000001_0_2023.pdf", "Bruttozins", "15.21"),
        zeile("TAX_P_CH0009000000000000002_1000000002_0_2023.pdf", "Bruttozins", "15.21"),
    ]
    assert mark_duplicate_positions(rows) == 0
    assert all("dupe_hinweis" not in r for r in rows)


def test_zwei_positionen_im_selben_dokument_sind_kein_duplikat():
    """Ein Beleg mit zwei Konten erzeugt legitim zwei Zeilen."""
    rows = [
        zeile("Kontoauszug_123456_2024.pdf", "Vermögensstand 31.12.", "100.00"),
        zeile("Kontoauszug_123456_2024.pdf", "Vermögensstand 31.12.", "100.00"),
    ]
    assert mark_duplicate_positions(rows) == 0


def test_gleiches_konto_aber_andere_position_ist_kein_duplikat():
    rows = [
        zeile("REP_P_CH0009000000000000001_1_2023.pdf", "Bruttozins", "15.21"),
        zeile("TAX_P_CH0009000000000000001_1_2023.pdf", "Vermögensstand 31.12.", "15.21"),
    ]
    assert mark_duplicate_positions(rows) == 0


# --- Selbstgetragene Krankheitskosten: Komponenten-Summe -------------------
#
# Gemeldeter Fehler: aus dem Dokument wurde nur der Selbstbehalt uebernommen,
# obwohl Franchise und weitere selbst getragene Kosten dazugehoeren. Ursache
# waren zu enge Label-Muster (nur "Jahresfranchise", nicht "Franchise").

from extractors.regex_extract import kk_selbstkosten_aus_komponenten as _kk_sum


def test_franchise_ohne_jahres_praefix_wird_erkannt():
    """Der gemeldete Fehler: 'Franchise' statt 'Jahresfranchise'."""
    wert, befund = _kk_sum("Franchise 300.00 Selbstbehalt 412.60")
    assert wert == "712.60"
    assert set(befund["summiert"]) == {"franchise", "selbstbehalt"}


def test_einzelne_komponente_gilt_als_unbelegt():
    """Nur der Selbstbehalt und kein Total: vermutlich unvollstaendig."""
    wert, befund = _kk_sum("Selbstbehalt 412.60")
    assert wert == "412.60"
    assert befund["belegt"] is False, "unvollstaendige Summe muss auffallen"


def test_ausgewiesenes_total_hat_vorrang_und_belegt_die_summe():
    txt = ("Jahresfranchise 300.00 Selbstbehalt 412.60 Spitalbetrag 0.00 "
           "Nichtversicherte Leistungen 20.15 Nichtpflichtige Leistungen 0.00 "
           "Rechnungsbetrag Anteil Ihr Anteil Total CHF 3'980.25 3'247.50 732.75")
    wert, befund = _kk_sum(txt)
    assert wert == "732.75", "nicht der Bruttorechnungsbetrag"
    assert befund["belegt"] is True


def test_kostenbeteiligung_wird_nicht_doppelt_gezaehlt():
    """Bei manchen Kassen ist Kostenbeteiligung bereits Franchise+Selbstbehalt."""
    wert, _ = _kk_sum("Franchise 300.00 Selbstbehalt 200.00 Kostenbeteiligung 500.00")
    assert wert == "500.00"


def test_kostenbeteiligung_allein_zaehlt_mit():
    wert, _ = _kk_sum("Ihre Kostenbeteiligung 500.00 Nichtversicherte Kosten 80.00")
    assert wert == "580.00"


def test_ohne_komponenten_kein_wert():
    assert _kk_sum("Praemienuebersicht Total 565.81")[0] is None


# --- Konfidenzstufen + Korrektur-CSV (Optimierungszirkel) ------------------

from scripts.build_tax_output import (
    KONFIDENZ_SICHER, KONFIDENZ_UNSICHER, KONFIDENZ_WAHRSCHEINLICH,
    konfidenz_fuer, schreibe_korrektur_csv,
)


def _zeile(**kw) -> dict:
    basis = {"betrag": "100.00", "anchor_valid": True, "herkunft": "regel",
             "plaus_errs": [], "plaus_hinweis": None, "manual_review_marker": None,
             "derived": False, "ziffer": "15", "beschreibung": "Prämie KVG",
             "pdf_name": "a.pdf", "person": None, "aussteller": None,
             "field_name": "praemie_kvg_total"}
    basis.update(kw)
    return basis


def test_regel_plus_anker_ist_sicher():
    assert konfidenz_fuer(_zeile()) == KONFIDENZ_SICHER


def test_modellwert_ist_hoechstens_wahrscheinlich():
    """Das Modell waehlt unter mehreren Zahlen — das ist nie 'sicher'."""
    assert konfidenz_fuer(_zeile(herkunft="modell")) == KONFIDENZ_WAHRSCHEINLICH


def test_berechneter_wert_ist_nicht_sicher():
    assert konfidenz_fuer(_zeile(derived=True)) == KONFIDENZ_WAHRSCHEINLICH


def test_fehlender_anker_ist_unsicher():
    assert konfidenz_fuer(_zeile(anchor_valid=False)) == KONFIDENZ_UNSICHER


def test_plausibilitaets_hinweis_macht_unsicher():
    """Die unvollstaendige Selbstkosten-Summe darf nie 'sicher' aussehen."""
    z = _zeile(plaus_hinweis="Summe aus nur einer erkannten Komponente")
    assert konfidenz_fuer(z) == KONFIDENZ_UNSICHER


def test_marker_und_fehlender_betrag_sind_unsicher():
    assert konfidenz_fuer(_zeile(manual_review_marker="x")) == KONFIDENZ_UNSICHER
    assert konfidenz_fuer(_zeile(betrag=None)) == KONFIDENZ_UNSICHER


def test_korrektur_csv_hat_leere_korrekturspalten(tmp_path):
    ziel = tmp_path / "korrektur.csv"
    n = schreibe_korrektur_csv([_zeile(), _zeile(herkunft="modell")], ziel)
    assert n == 2
    zeilen = ziel.read_text(encoding="utf-8").splitlines()
    assert zeilen[0].endswith("korrektur,notiz")
    for z in zeilen[1:]:
        assert z.endswith(",,"), "Korrektur- und Notizspalte muessen leer sein"


def test_korrektur_csv_stellt_unsicheres_nach_oben(tmp_path):
    ziel = tmp_path / "k.csv"
    schreibe_korrektur_csv([_zeile(ziffer="15"), _zeile(ziffer="1.1", anchor_valid=False)], ziel)
    zeilen = ziel.read_text(encoding="utf-8").splitlines()[1:]
    assert "unsicher" in zeilen[0], "unsichere Zeilen zuerst — dort lohnt die Pruefung"


# --- Feldgenaue Plausibilitaets-Befunde ------------------------------------
#
# run_plausibility arbeitet pro Beleg; das Ergebnis hing an jeder Zeile. Ein
# Befund zur Verrechnungssteuer machte damit auch den Vermoegensstand
# "unsicher" — die Haelfte aller unsicheren Zeilen war so zu Unrecht markiert.

from scripts.build_tax_output import (
    konfidenz_grund, relevante_befunde,
)


def test_befund_zu_anderem_feld_zaehlt_nicht():
    z = _zeile(field_name="vermoegensstand_3112",
               plaus_errs=["VRS-Label fehlt: '0.00' (verrechnungssteuer) ohne Label"])
    assert relevante_befunde(z) == []
    assert konfidenz_fuer(z) == KONFIDENZ_SICHER


def test_befund_zum_eigenen_feld_zaehlt():
    z = _zeile(field_name="verrechnungssteuer",
               plaus_errs=["VRS-Label fehlt: '0.00' (verrechnungssteuer) ohne Label"])
    assert len(relevante_befunde(z)) == 1
    assert konfidenz_fuer(z) == KONFIDENZ_UNSICHER


def test_belegweiter_befund_ohne_feldnennung_zaehlt_fuer_alle():
    z = _zeile(field_name="nettolohn_pos11",
               plaus_errs=["Beleg wirkt unvollstaendig"])
    assert len(relevante_befunde(z)) == 1
    assert konfidenz_fuer(z) == KONFIDENZ_UNSICHER


def test_grund_nennt_die_ursache_im_klartext():
    assert "kein Anker" in konfidenz_grund(_zeile(anchor_valid=False))
    assert "Modell" in konfidenz_grund(_zeile(herkunft="modell"))
    assert "Regel" in konfidenz_grund(_zeile(herkunft="regel"))
    assert "Marker: xy" == konfidenz_grund(_zeile(manual_review_marker="manual_review:xy"))


# --- Unformatierte Betraege fuers Formular ---------------------------------
#
# Die Dokumente schreiben durcheinander: 3'120.40, 3 120.40, 3120,40, 1976.00.
# Ins Steuerformular gehoert davon nur die nackte Zahl — sonst ist jede
# Position ein Handgriff mehr.

from scripts.build_tax_output import fmt_amount, roher_betrag


def test_apostroph_und_leerzeichen_verschwinden():
    assert roher_betrag("3'120.40") == "3120.40"
    assert roher_betrag("3 120.40") == "3120.40"
    assert roher_betrag("85’420.00") == "85420.00"


def test_komma_als_dezimaltrenner_wird_punkt():
    assert roher_betrag("3120,40") == "3120.40"


def test_komma_als_tausendertrenner_faellt_weg():
    assert roher_betrag("3,120.40") == "3120.40"


def test_bereits_rohe_werte_bleiben():
    assert roher_betrag("24652.55") == "24652.55"
    assert roher_betrag("0.50") == "0.50"


def test_nichtzahlen_bleiben_unveraendert():
    """Hier wird formatiert, nicht interpretiert."""
    assert roher_betrag("manual_review:x") == "manual_review:x"
    assert roher_betrag(None) == ""


def test_tabelle_zeigt_rohe_betraege():
    assert fmt_amount("3'120.40") == "3120.40"
    assert fmt_amount(None) == "—"
