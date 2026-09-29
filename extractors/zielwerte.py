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


# Die amtlichen Feldcodes des Formulars 303 (Steuererklaerung Kanton Zuerich).
#
# Neben jeder Ziffer steht im Formular eine dreistellige Zahl — 100 fuer den
# Lohn, 400 fuer die Wertschriften, 470 fuer die Schulden. Das ist die Nummer,
# die das Feld eindeutig benennt; ZHprivateTax fuehrt sie mit. Ohne sie zeigt
# die Uebertragungstabelle zwar die Ziffer, aber nicht das Feld, in das der
# Wert gehoert — und man sucht (260923-dua).
#
# Quelle: Formular 303 STE L ZH 2025, Seiten 2-4.
FELDCODES: dict[str, str] = {
    "1.1": "100",     # Person 1; Person 2 traegt 101
    "4": "150",       # Ertrag aus Wertschriften, Guthaben und Lotterien
    "12": "250",      # Schuldzinsen
    "14": "260",      # Saeule 3a Person 1; Person 2 traegt 261
    "15": "270",      # Versicherungspraemien, Zinsen von Sparkapitalien
    "16.6": "376",    # Abzug fuer fremdbetreute Kinder
    "22.1": "320",    # Krankheits- und Unfallkosten (Hilfsblatt 320)
    "30.1": "400",    # Wertschriften und Guthaben
    "34": "470",      # Schulden
}

# Feldcode der zweiten Person, wo das Formular zwei getrennte Felder fuehrt.
FELDCODES_PERSON_2: dict[str, str] = {
    "1.1": "101",
    "14": "261",
}

# Ziffern, deren Betrag NICHT von Hand eingetragen wird.
#
# Wertschriften und Schulden werden im jeweiligen Verzeichnis Position fuer
# Position erfasst; die Summe in 150/400 bzw. 250/470 rechnet das Formular
# selbst. Wer die Summe dort eintippt, verdoppelt sie. Fuer diese Ziffern ist
# die ausgewiesene Summe eine **Kontrollzahl**, kein Eingabewert.
SUMME_RECHNET_DAS_FORMULAR: frozenset[str] = frozenset({
    "4", "12", "30.1", "34",
})


# Die Spalten einer Zeile im Wertschriften- und Guthabenverzeichnis.
#
# Ein Konto ist dort keine einzelne Eingabe, sondern eine Zeile mit mehreren
# Spalten. Wer nur den Saldo einsetzt, hat die Zeile nicht ausgefuellt — die
# Bezeichnung fehlt, und der Ertrag steht in der falschen Kolonne.
#
# Kolonne A und B sind die entscheidende Unterscheidung: A sind Werte, deren
# Ertrag um 35% Verrechnungssteuer gekuerzt wurde, B die ohne Abzug. Das
# Formular rechnet daraus den Rueckerstattungsanspruch (35% von Total A).
# Steht ein Ertrag in der falschen Kolonne, stimmt die Rueckerstattung nicht.
#
# Quelle: Formular 340 WV ZH, Seiten 2-3.
SPALTEN_340: tuple[tuple[str, str], ...] = (
    ("bezeichnung", "Genaue Bezeichnung (bei Konto inkl. Nummer)"),
    ("steuerwert", "Steuerwert am 31.12."),
    ("ertrag_a", "Bruttoertrag A — mit Verrechnungssteuerabzug"),
    ("ertrag_b", "Bruttoertrag B — ohne Verrechnungssteuerabzug"),
)

# Welcher Zielwert gehoert in welche Spalte dieser Zeile?
SPALTE_FUER_ZIELWERT: dict[str, str] = {
    "Saldo 31.12.": "steuerwert",
    "Steuerwert 31.12.": "steuerwert",
    "Bruttoertrag mit Verrechnungssteuer": "ertrag_a",
    "Bruttoertrag ohne Verrechnungssteuer": "ertrag_b",
}


def spalte_340(bezeichnung: str) -> str:
    """In welche Spalte der Verzeichniszeile gehoert dieser Wert?"""
    return SPALTE_FUER_ZIELWERT.get(str(bezeichnung or "").strip(), "")


def feldcode(ziffer: str, person: str = "") -> str:
    """Die amtliche Feldnummer — leer, wenn der Vertrag keine kennt."""
    z = str(ziffer or "").strip()
    if str(person or "").strip() == "elternteil_2" and z in FELDCODES_PERSON_2:
        return FELDCODES_PERSON_2[z]
    return FELDCODES.get(z, "")


def wird_gerechnet(ziffer: str) -> bool:
    """Rechnet das Formular diese Summe selbst aus den Einzelpositionen?"""
    return str(ziffer or "").strip() in SUMME_RECHNET_DAS_FORMULAR


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


def ziffer_fuer(bezeichnung: str) -> str:
    """Welche ZHprivateTax-Ziffer gehört zu diesem Zielwert?

    Der Feldvertrag weiss das — und ist laut CLAUDE.md die einzige Quelle
    dafür. Trotzdem hing die Ziffer einer selbst erfassten Position daran, ob
    der Browser sie mitgeschickt hatte: eine Zeile mit Betrag, Person und dem
    Zielwert „Prämie Zusatzversicherung VVG" stand ohne Ziffer da, obwohl der
    Vertrag dafür 15 vorsieht (260923-dua, vom Nutzer gemeldet).

    Ist eine Bezeichnung mehreren Ziffern zugeordnet, gibt es keine Antwort —
    raten wäre schlimmer als die Lücke.
    """
    gesucht = str(bezeichnung or "").strip()
    if not gesucht:
        return ""
    treffer = {z.ziffer for werte in ZIELWERTE.values() for z in werte
               if z.bezeichnung == gesucht}
    return treffer.pop() if len(treffer) == 1 else ""


# Reihenfolge der Formulare beim Ausfuellen. Nicht alphabetisch, sondern so,
# wie man die Steuererklaerung durchgeht: erst das Einkommen, dann das
# Vermoegen, dann die Abzuege.
FORMULAR_REIHENFOLGE: tuple[str, ...] = (
    "Lohnausweis (100)",
    "Wertschriftenverzeichnis (400)",
    "Schuldenverzeichnis",
    "Versicherungsprämien",
    "Bescheinigung der Vorsorgeeinrichtung",
    "Aufstellung fremdbetreute Kinder",
    "Hilfsblatt 320",
)


def formular_fuer(bezeichnung: str) -> str:
    """In welches Formular gehört dieser Wert?

    Beim Ausfüllen zählt nicht die Ziffer, sondern das Blatt: das
    Wertschriftenverzeichnis wird Konto für Konto erfasst, das
    Schuldenverzeichnis Schuld für Schuld. Der Feldvertrag weiss das je
    Zielwert; die Übertragungstabelle kann danach gruppieren (260923-dua).

    Mehrdeutig oder unbekannt: leer — raten hilft beim Abtippen nicht.
    """
    gesucht = str(bezeichnung or "").strip()
    if not gesucht:
        return ""
    treffer = {z.formular or "" for werte in ZIELWERTE.values() for z in werte
               if z.bezeichnung == gesucht}
    return treffer.pop() if len(treffer) == 1 else ""


def formular_sortierung(formular: str) -> tuple:
    """Formulare in der Reihenfolge, in der man sie ausfüllt."""
    f = str(formular or "")
    if f in FORMULAR_REIHENFOLGE:
        return (FORMULAR_REIHENFOLGE.index(f), f)
    return (len(FORMULAR_REIHENFOLGE), f)


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
