"""Tests der Vollständigkeitsprüfung (260904-rmx).

Der Anker-Mechanismus prüft gefundene Werte. Diese Regeln prüfen das, was gar
nicht gefunden wurde — bei einer Steuererklärung der teurere Fehler.
"""
from __future__ import annotations

from extractors.vollstaendigkeit import (
    Befund,
    pruefe_alles,
    pruefe_dokument_konsistenz,
    pruefe_familienabdeckung,
    pruefe_person_zuordnung,
    rollen_aus_family,
    sammle_abdeckung,
)

ROLLEN = ("elternteil_1", "elternteil_2", "kind_1", "kind_2")


def z(person, beschreibung, pdf="a.pdf") -> dict:
    return {"person": person, "beschreibung": beschreibung, "pdf_name": pdf}


def codes(befunde: list[Befund]) -> list[str]:
    return [b.code for b in befunde]


# --- Familienabdeckung ------------------------------------------------------

def test_vollstaendige_familie_ohne_befund():
    zeilen = [z(r, "Prämie Grundversicherung KVG", f"{r}.pdf") for r in ROLLEN]
    assert pruefe_familienabdeckung(zeilen, ROLLEN) == []


def test_fehlende_kvg_beim_kind_wird_gemeldet():
    """KVG ist auch fuer Kinder obligatorisch."""
    zeilen = [z(r, "Prämie Grundversicherung KVG") for r in ROLLEN[:3]]
    b = pruefe_familienabdeckung(zeilen, ROLLEN)
    assert codes(b) == ["kvg_fehlt"]
    assert b[0].betrifft == "kind_2"


def test_alle_fehlen_ergibt_vier_befunde():
    assert len(pruefe_familienabdeckung([], ROLLEN)) == 4


def test_zusatzversicherung_wird_nicht_eingefordert():
    """VVG ist freiwillig — ihr Fehlen ist kein Befund."""
    zeilen = [z(r, "Prämie Grundversicherung KVG") for r in ROLLEN]
    assert pruefe_familienabdeckung(zeilen, ROLLEN) == []


def test_nur_vvg_zaehlt_nicht_als_kvg():
    zeilen = [z(r, "Prämie Zusatzversicherung VVG") for r in ROLLEN]
    assert len(pruefe_familienabdeckung(zeilen, ROLLEN)) == 4


# --- Dokument-Konsistenz ----------------------------------------------------

def test_selbstkosten_ohne_praemie_ist_unstimmig():
    """Der vom Nutzer genannte Fall."""
    zeilen = [z("elternteil_1", "Selbst getragene Krankheits- und Unfallkosten",
                "kk.pdf")]
    b = pruefe_dokument_konsistenz(zeilen)
    assert codes(b) == ["selbstkosten_ohne_praemie"]
    assert b[0].betrifft == "kk.pdf"
    assert b[0].schwere == "unstimmig"


def test_selbstkosten_mit_praemie_ist_stimmig():
    zeilen = [
        z("elternteil_1", "Prämie Grundversicherung KVG", "kk.pdf"),
        z("elternteil_1", "Selbst getragene Krankheits- und Unfallkosten", "kk.pdf"),
    ]
    assert pruefe_dokument_konsistenz(zeilen) == []


def test_praemie_ohne_selbstkosten_ist_kein_befund():
    """Wer keine Arztrechnung hatte, hat keine Selbstkosten — voellig normal."""
    zeilen = [z("elternteil_1", "Prämie Grundversicherung KVG", "kk.pdf")]
    assert pruefe_dokument_konsistenz(zeilen) == []


def test_praemie_in_anderem_dokument_hilft_nicht():
    """Die Konsistenz gilt je Dokument, nicht ueber alle hinweg."""
    zeilen = [
        z("elternteil_1", "Prämie Grundversicherung KVG", "andere.pdf"),
        z("elternteil_1", "Selbst getragene Krankheits- und Unfallkosten", "kk.pdf"),
    ]
    assert codes(pruefe_dokument_konsistenz(zeilen)) == ["selbstkosten_ohne_praemie"]


# --- Personenzuordnung ------------------------------------------------------

def test_positionen_ohne_person_werden_gemeldet():
    zeilen = [z("", "Prämie KVG"), z("unbekannt/manuell", "Nettolohn")]
    b = pruefe_person_zuordnung(zeilen)
    assert codes(b) == ["person_fehlt"]
    assert "2 Position" in b[0].text


def test_zugeordnete_positionen_ohne_befund():
    assert pruefe_person_zuordnung([z("elternteil_1", "Prämie KVG")]) == []


# --- Zusammenspiel ----------------------------------------------------------

def test_abdeckung_sammelt_pro_person():
    zeilen = [
        z("elternteil_1", "Prämie Grundversicherung KVG"),
        z("elternteil_1", "Prämie Zusatzversicherung VVG"),
        z("elternteil_1", "Selbst getragene Krankheits- und Unfallkosten"),
    ]
    a = sammle_abdeckung(zeilen)["elternteil_1"]
    assert (a.kvg, a.vvg, a.selbstkosten) == (True, True, True)


def test_pruefe_alles_sortiert_fehlendes_nach_vorn():
    zeilen = [
        z("elternteil_1", "Prämie Grundversicherung KVG"),
        z("elternteil_2", "Selbst getragene Krankheits- und Unfallkosten", "x.pdf"),
    ]
    b = pruefe_alles(zeilen, ROLLEN)
    assert b[0].schwere == "fehlt"
    assert "unstimmig" in {x.schwere for x in b}


def test_rollen_aus_family_faellt_zurueck():
    assert rollen_aus_family(None) == ROLLEN


# --- Erwerbstaetige: Lohnausweis + Saeule 3a --------------------------------

from extractors.vollstaendigkeit import ERWERBS_ROLLEN, pruefe_erwerbstaetige


def test_beide_erwachsenen_brauchen_lohn_und_3a():
    assert len(pruefe_erwerbstaetige([], ERWERBS_ROLLEN)) == 4


def test_vollstaendige_erwerbstaetige_ohne_befund():
    zeilen = []
    for r in ERWERBS_ROLLEN:
        zeilen += [z(r, "Nettolohn"), z(r, "Einzahlung Säule 3a")]
    assert pruefe_erwerbstaetige(zeilen, ERWERBS_ROLLEN) == []


def test_fehlende_3a_bei_einem_elternteil():
    zeilen = [z("elternteil_1", "Nettolohn"), z("elternteil_1", "Einzahlung Säule 3a"),
              z("elternteil_2", "Nettolohn")]
    b = pruefe_erwerbstaetige(zeilen, ERWERBS_ROLLEN)
    assert codes(b) == ["saeule_3a_fehlt"]
    assert b[0].betrifft == "elternteil_2"


def test_kinder_brauchen_keinen_lohnausweis():
    """Die Erwerbsregeln gelten nur fuer die Erwachsenen."""
    zeilen = [z(r, "Nettolohn") for r in ERWERBS_ROLLEN]
    zeilen += [z(r, "Einzahlung Säule 3a") for r in ERWERBS_ROLLEN]
    zeilen += [z(r, "Prämie Grundversicherung KVG") for r in ROLLEN]
    b = pruefe_alles(zeilen, ROLLEN)
    # Optionale Hinweise (VVG, Selbstkosten) duerfen kommen — aber kein
    # Lohnausweis oder 3a fuer die Kinder.
    erwerbsbefunde = [x for x in b
                      if x.code in ("lohnausweis_fehlt", "saeule_3a_fehlt")]
    assert erwerbsbefunde == []
    assert all(x.schwere == "hinweis" for x in b)


# --- Optionales als abhakbarer Hinweis --------------------------------------

from extractors.vollstaendigkeit import pruefe_erwartetes_optionales


def test_optionales_wird_als_hinweis_gemeldet():
    """Grundsatz: lieber aktiv 'gibt es nicht' als etwas vergessen."""
    b = pruefe_erwartetes_optionales([], ("elternteil_1",))
    assert codes(b) == ["vvg_fehlt", "selbstkosten_fehlen"]
    assert all(x.schwere == "hinweis" for x in b)


def test_vorhandenes_optionales_ohne_hinweis():
    zeilen = [z("elternteil_1", "Prämie Zusatzversicherung VVG"),
              z("elternteil_1", "Selbst getragene Krankheits- und Unfallkosten")]
    assert pruefe_erwartetes_optionales(zeilen, ("elternteil_1",)) == []


def test_hinweise_stehen_hinter_fehlendem():
    b = pruefe_alles([], ("elternteil_1",))
    schweren = [x.schwere for x in b]
    assert schweren == sorted(schweren, key=lambda s: {"fehlt":0,"unstimmig":1,"hinweis":2}[s])


# --- Regelwerk fuer die Oberflaeche ----------------------------------------

def test_regelwerk_liefert_rollen_und_muster():
    """Die Oberflaeche prueft live mit denselben Regeln — sonst laufen die
    Pruefung beim Erzeugen und die im Browser auseinander."""
    from extractors.vollstaendigkeit import MUSTER, regelwerk
    rw = regelwerk(ROLLEN)
    assert rw["rollen"] == list(ROLLEN)
    assert rw["erwerbsRollen"] == ["elternteil_1", "elternteil_2"]
    assert set(rw["muster"]) == set(MUSTER)
    assert "KVG" in rw["muster"]["kvg"]


def test_regelwerk_ist_serialisierbar():
    import json
    from extractors.vollstaendigkeit import regelwerk
    json.dumps(regelwerk())


def test_muster_decken_die_zielwerte_ab():
    """Jede Bezeichnung aus dem Vertrag muss von einem Muster gefunden werden."""
    from extractors.vollstaendigkeit import MUSTER
    from extractors.zielwerte import ZIELWERTE
    alle = [z.bezeichnung for w in ZIELWERTE.values() for z in w]
    fuer = lambda t: any(m in t for muster in MUSTER.values() for m in muster)
    ohne = [t for t in alle if not fuer(t)]
    # Nicht jeder Zielwert braucht ein Muster: die Pruefung fragt „ist fuer
    # diese Person da, was da sein muss?". Vermoegenswerte, Ertraege und
    # Schulden haengen nicht an einer Person, fuer sie gibt es nichts
    # einzufordern.
    ohne_muster_noetig = ("Saldo", "Vermögensstand", "Bruttozins", "Steuerwert",
                          "Verrechnungssteuer", "Bruttoertrag",
                          "Kinderbetreuung", "Schuldzinsen",
                          "Hypothekarschuld")
    assert ohne == [] or all(any(m in t for m in ohne_muster_noetig)
                             for t in ohne), ohne
