"""Der Feldvertrag: welcher Wert aus welchem Belegtyp in welche Ziffer gehört.

Einzige Quelle der Wahrheit — abgeleitet **rückwärts aus dem Formular**, nicht
aus dem, was die Dokumente hergeben. Ziffern und Hilfsblatt-Nummern stammen aus
der Wegleitung zur Steuererklärung 2025 des kantonalen Steueramtes Zürich.

Warum als Code und nicht als weiteres Markdown: es gab bereits vier
Spezifikationsdokumente, und nichts hat geprüft, ob der Code ihnen folgt. Vier
Monate lang lieferte die Krankenkassen-Extraktion den Bruttorechnungsbetrag,
obwohl die Spezifikation ``Rechnungsbetrag brutto`` namentlich ausschliesst.
Ein Vertrag, den keine Maschine liest, ist eine Absichtserklärung.

`evals/test_zielwerte_vertrag.py` erzwingt:
  1. jedes Zielwert-Feld existiert im Schema des Belegtyps
  2. die Übertragungstabelle führt nur Zielwert-Felder
  3. kein Feldname aus AUSSCHLUSS taucht als Zielwert auf
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Zielwert:
    """Ein Wert, der aus einem Beleg in die Steuererklärung wandert.

    Attributes:
        feld: Feldname im pydantic-Schema des Belegtyps.
        bezeichnung: Klartext für die Übertragungstabelle.
        ziffer: Ziffer im Hauptformular (Wegleitung ZH 2025).
        formular: Ergänzendes Formular/Hilfsblatt, falls eines verlangt ist.
        pflicht: True, wenn der Beleg ohne diesen Wert unvollständig ist.
    """

    feld: str
    bezeichnung: str
    ziffer: str
    formular: str | None = None
    pflicht: bool = True


# ---------------------------------------------------------------------------
# Der Vertrag
# ---------------------------------------------------------------------------

ZIELWERTE: dict[str, list[Zielwert]] = {
    "lohnausweis": [
        # Zielwert ist der Nettolohn (Ziffer 11 des Lohnausweises). Brutto,
        # AHV/ALV/NBU und BVG sind Kontrollwerte fuer die Gegenprobe, keine
        # Uebertragungszeilen.
        Zielwert("nettolohn_pos11", "Nettolohn", "1.1", "Lohnausweis (100)"),
    ],
    # Das Wertschriftenverzeichnis kennt zwei Abteilungen: Ertraege MIT
    # Verrechnungssteuer (A) und solche OHNE (B). Was uebertragen wird, ist
    # der Bruttoertrag — in die eine oder die andere Abteilung. Die
    # Verrechnungssteuer selbst ist keine Uebertragungszeile: sie betraegt
    # 35 % der Abteilung A und wird vom Steuerprogramm gerechnet.
    #
    # Vorher standen hier "Bruttoertrag" und "Verrechnungssteuer 35 %" —
    # zwei Schubladen fuer drei Dinge, und keine davon war die, die man
    # ausfuellen muss (260904-rmx).
    #
    # Die Zuordnung der beiden Felder folgt der Erfassung des Nutzers: er
    # hat die zwei vorhandenen Schubladen fuer seine zwei Betraege benutzt.
    #   ``bruttoertrag``        -> Abteilung B, ohne Verrechnungssteuer
    #   ``verrechnungssteuer``  -> Abteilung A, mit Verrechnungssteuer
    #
    # Das ist nicht, was die Feldnamen vermuten lassen, und deshalb steht es
    # hier: bei einem frisch extrahierten, nicht durchgesehenen Dokument
    # enthaelt das zweite Feld den Steuerbetrag, nicht den Ertrag. Werte aus
    # dem Modell gehoeren in dieser Zeile also geprueft, bevor sie ins
    # Formular wandern.
    "bank_zinsausweis": [
        Zielwert("vermoegensstand_3112", "Saldo 31.12.", "30.1",
                 "Wertschriftenverzeichnis (400)"),
        Zielwert("bruttoertrag", "Bruttoertrag ohne Verrechnungssteuer", "4",
                 "Wertschriftenverzeichnis (400)", pflicht=False),
        Zielwert("verrechnungssteuer", "Bruttoertrag mit Verrechnungssteuer", "4",
                 "Wertschriftenverzeichnis (400)", pflicht=False),
    ],
    "wertschriftenverzeichnis": [
        Zielwert("bestand_3112", "Steuerwert 31.12.", "30.1",
                 "Wertschriftenverzeichnis (400)"),
        Zielwert("bruttoertrag_total", "Bruttoertrag ohne Verrechnungssteuer", "4",
                 "Wertschriftenverzeichnis (400)", pflicht=False),
        Zielwert("verrechnungssteuer_total", "Bruttoertrag mit Verrechnungssteuer", "4",
                 "Wertschriftenverzeichnis (400)", pflicht=False),
    ],
    "kk_praemienbescheinigung": [
        # Grund- und Zusatzversicherung bleiben getrennt (User-Entscheid
        # 2026-09-05) — beide fliessen in Ziffer 15.
        Zielwert("praemie_kvg_total", "Prämie Grundversicherung KVG", "15",
                 "Versicherungsprämien"),
        Zielwert("praemie_vvg_total", "Prämie Zusatzversicherung VVG", "15",
                 "Versicherungsprämien", pflicht=False),
        # Fallback fuer Dokumente, die KVG und VVG nicht trennen. Wird
        # unterdrueckt, sobald eines der beiden sauber ausgewiesen ist —
        # sonst erschiene dieselbe Praemie zweimal.
        Zielwert("praemie_total", "Prämie Total (nicht aufgeteilt)", "15",
                 "Versicherungsprämien", pflicht=False),
        # Selbst getragene Behandlungskosten sind ein EIGENER Abzug und
        # greifen erst, soweit sie 5 % des Reineinkommens uebersteigen.
        Zielwert("selbstgetragene_kosten",
                 "Selbst getragene Krankheits- und Unfallkosten", "22.1",
                 "Hilfsblatt 320", pflicht=False),
    ],
    "krankheitskosten": [
        Zielwert("betrag", "Selbst getragene Krankheits- und Unfallkosten",
                 "22.1", "Hilfsblatt 320"),
    ],
    # NUR die Einzahlung — das Guthaben gehoert bewusst NICHT dazu.
    #
    # Wegleitung ZH 2025, S. 24: "Ansprueche an Bankstiftungen aus
    # anerkannten Formen der gebundenen Selbstvorsorge (3. Saeule a) sind bis
    # zur Faelligkeit der Leistungen steuerfrei und nicht im
    # Wertschriftenverzeichnis aufzufuehren." Fuer Policen statt Konten sagt
    # S. 21 dasselbe. Besteuert wird erst die Auszahlung, gesondert.
    #
    # Praktische Folge: ein 3a-Beleg ohne Einzahlung im Steuerjahr enthaelt
    # nichts Deklarierbares. Er ist dann vollstaendig, nicht luechenhaft
    # (260904-rmx, vom Nutzer angestossen und an der Wegleitung geprueft).
    "saeule_3a": [
        Zielwert("einzahlung_betrag", "Einzahlung Säule 3a", "14",
                 "Bescheinigung der Vorsorgeeinrichtung"),
    ],
    "kinderbetreuung": [
        Zielwert("betrag", "Kosten Kinderbetreuung", "16.6",
                 "Aufstellung fremdbetreute Kinder"),
    ],
    # Ziffern aus der Wegleitung ZH 2025, Hauptformular:
    #   12  Schuldzinsen (soweit nicht schon unter Ziff. 2 abgezogen)
    #   34  Schulden
    # Beide verlangen das Schuldenverzeichnis. Der Zins ist ein Abzug vom
    # Einkommen, der Saldo eine Schuld im Vermoegensteil — zwei Zeilen aus
    # einem Beleg, in verschiedenen Teilen des Formulars.
    "hypothek_zinsbestaetigung": [
        Zielwert("schuldzinsen", "Schuldzinsen Hypothek", "12", "Schuldenverzeichnis"),
        Zielwert("schuldsaldo_3112", "Hypothekarschuld 31.12.", "34", "Schuldenverzeichnis"),
    ],
}


# ---------------------------------------------------------------------------
# Die Ziffern der Steuererklaerung
# ---------------------------------------------------------------------------
#
# Reihenfolge und Bezeichnung wie im ZH-Steuerformular. Wer die Erklaerung
# ausfuellt, arbeitet sie von oben nach unten ab — eine Uebersicht, die nach
# Belegen sortiert ist, zwingt zum Hin- und Herspringen.
#
# Die Zahl vor dem Punkt ordnet, nicht der String: "16.6" muss zwischen "15"
# und "22.1" stehen, nicht zwischen "1.1" und "4".

ZIFFERN: dict[str, str] = {
    "1.1": "Einkünfte aus unselbständiger Erwerbstätigkeit",
    "4": "Erträge aus Wertschriften und Guthaben",
    "12": "Schuldzinsen",
    "14": "Beiträge an die Säule 3a",
    "15": "Versicherungsprämien und Sparzinsen",
    "16.6": "Kosten für die Drittbetreuung von Kindern",
    "22.1": "Krankheits- und Unfallkosten",
    "30.1": "Wertschriften und Guthaben (Vermögen)",
    "34": "Schulden",
}


def ziffer_sortierung(ziffer: str) -> tuple:
    """Sortierschluessel einer Ziffer — numerisch, nicht alphabetisch."""
    teile = [t for t in str(ziffer or "").strip().split(".") if t != ""]
    try:
        return tuple(int(t) for t in teile) or (999,)
    except ValueError:
        return (999,)


def ziffer_name(ziffer: str) -> str:
    """Bezeichnung der Ziffer, oder die Ziffer selbst wenn unbekannt."""
    z = str(ziffer or "").strip()
    return ZIFFERN.get(z, "")


# Felder, die auf den Belegen stehen, aber NIEMALS Zielwert sein duerfen.
# Aus STEUER-ZIELWERTE-FOKUS.md — die Liste, die vier Monate lang niemand
# durchgesetzt hat.
AUSSCHLUSS: dict[str, tuple[str, ...]] = {
    "kk_praemienbescheinigung": (
        "rechnungsbetrag",        # Bruttorechnung, nicht der Selbstanteil
        "rechnungsbetrag_brutto",
        "anteil_versicherer",     # was die Kasse zahlt, nicht der Versicherte
        "kundennummer",
        "police",
    ),
    "krankheitskosten": (
        "rechnungsbetrag",
        "rechnungsbetrag_brutto",
        "anteil_versicherer",
    ),
    "lohnausweis": (
        # Kontrollwerte, keine Uebertragungszeilen — sonst erscheint der Lohn
        # mehrfach in der Steuererklaerung.
        "bruttolohn_pos8",
        "ahv_alv_nbu_abzug_pos9",
        "bvg_abzug_pos10a",
    ),
}


def zielwert_felder(belegtyp: str) -> set[str]:
    """Feldnamen, die fuer diesen Belegtyp in die Tabelle duerfen."""
    return {z.feld for z in ZIELWERTE.get(belegtyp, ())}


def ist_ausgeschlossen(belegtyp: str, feld: str) -> bool:
    """True, wenn das Feld fuer diesen Belegtyp nie Zielwert sein darf."""
    return feld in AUSSCHLUSS.get(belegtyp, ())
