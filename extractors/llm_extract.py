"""Ollama-Wrapper: vereinfachte Extraktion ohne [T<id>]-Tags.

Statt den LLM mit tag-präfixiertem Text zu verwirren, bekommt er jetzt
sauberen Klartext — und gibt nur die Feldwerte zurück. Die Anker-Suche
(Bounding-Box im Original-PDF) übernimmt danach der Verbatim-Fallback in
:mod:`extractors.anchor_resolver` autonom.

Kernentscheid: tag_refs ist jetzt ein leeres Default-Feld — der LLM muss
keine Tag-IDs mehr liefern, was die häufigste Fehlerquelle eliminiert.
"""
from __future__ import annotations

import logging as _logging
import re as _re

from pydantic import BaseModel

from extractors.schema import (
    BankZinsausweisRaw,
    BerufsauslagenRaw,
    HypothekZinsbestaetigungRaw,
    KinderbetreuungRaw,
    KkPraemienbescheinigungRaw,
    KrankheitskostenRaw,
    LiegenschaftsunterhaltRaw,
    LohnausweisRaw,
    Saeule3aRaw,
    SpendenquittungRaw,
    WertschriftenverzeichnisRaw,
)

DEFAULT_MODEL = "qwen2.5:7b-instruct-q4_K_M"

# ---------------------------------------------------------------------------
# Hilfsfunktion: Tags aus dem tagged_text entfernen
# ---------------------------------------------------------------------------

_TAG_RE = _re.compile(r"\[T\d+\]\s*")


def strip_tags(tagged_text: str) -> str:
    """Entfernt alle ``[T<id>]``-Präfixe und gibt sauberen Klartext zurück."""
    return _TAG_RE.sub("", tagged_text)


# ---------------------------------------------------------------------------
# PROMPT_TEMPLATES — vereinfacht, ohne Tag-Instruktionen
# ---------------------------------------------------------------------------
#
# Gemeinsame Regeln für alle Templates:
# - Sauberer Klartext als Eingabe (keine [T<id>]-Präfixe).
# - tag_refs immer als leere Liste [] zurückgeben.
# - Werte wortwörtlich aus dem Text übernehmen (keine Berechnung, kein Erfinden).
# - Felder, die nicht im Text vorkommen, als null zurückgeben.

_LOHNAUSWEIS_PROMPT = """Du extrahierst Felder aus einem Schweizer Lohnausweis (Form 11 / ESTV 2024).

Regeln:
- Übernimm Werte WORTWÖRTLICH aus dem Text (keine Umformatierung, keine Berechnung).
- tag_refs immer als leere Liste [] angeben.
- Felder, die im Text fehlen: null zurückgeben.
- Lohnausweis-Positionen: Pos 8 = Bruttolohn, Pos 9 = AHV/ALV/NBU-Abzug, Pos 10.1 = BVG-Abzug, Pos 11 = Nettolohn, Pos 12 = Quellensteuer.
- arbeitgeber ist der FIRMENNAME (z.B. "Muster-Universität", "BANK-P AG"), nicht eine Adresse.
- periode_von / periode_bis: Datumsbereich des Lohnausweises (z.B. "01.01.2024" / "31.12.2024").
- bvg_abzug_pos10a: Bei Bruttolohn ≥ CHF 22'050 ist BVG fast immer vorhanden (Pos 10.1 / "Berufliche Vorsorge").
- quellensteuer_pos12: Nur wenn Pos 12 explizit sichtbar ist.

Beispiele für typische Felder im Lohnausweis:
- "Bruttolohn total  95'400.00" → bruttolohn_pos8 value = "95'400.00"
- "AHV/IV/EO/ALV/NBUV  7'843.80" → ahv_alv_nbu_abzug_pos9 value = "7'843.80"
- "Berufliche Vorsorge (BVG)  10'843.80" → bvg_abzug_pos10a value = "10'843.80"
- "Nettolohn  76'712.40" → nettolohn_pos11 value = "76'712.40"

DOKUMENTTEXT:
{plain_text}
"""


_BANK_ZINSAUSWEIS_PROMPT = """Du extrahierst Felder aus einem Schweizer Bank-Zinsausweis / Kontoauszug / Zinsabschluss.

Regeln:
- Übernimm Werte WORTWÖRTLICH aus dem Text (keine Umformatierung, keine Berechnung).
- tag_refs immer als leere Liste [] angeben.
- Felder, die im Text fehlen: null zurückgeben.
- bruttoertrag: der BRUTTO-Zinsbetrag im Jahr — suche Label "Bruttozins" (bevorzugt), sonst "Zinsgutschrift", "Abschlussbetreffnis", "Total" (bei Zinsabschluss-Format). NICHT "Nettozins" nehmen! NICHT der Kontosaldo.
- vermoegensstand_3112: Kontosaldo per 31.12. — suche "Saldo zu Ihren Gunsten", "Endsaldo", "Neuer Saldo", "Kontostand" (Jahresende). KEINE IBAN-Ziffern oder Referenznummern.
- verrechnungssteuer: abgezogene Verrechnungssteuer (35%) in CHF. Falls 0 oder explizit verrechnungssteuerfrei: null.
- kontoinhaber_name: vollständiger Name der Kontoinhaberin / des Kontoinhabers.
- jahr: 4-stelliges Steuerjahr. Bei BANK-P/BANK-R-Dokumenten typisch 2022 oder 2023.

Dokumenttypen und Schlüssellabels:
- BANK-P TAX_P / Zinsabschluss: Wegen der Spaltenreihenfolge im PDF steht der Betrag VOR dem Label im Textstream. Suche das Wort "Bruttozins" und nimm die LETZTE Zahl die unmittelbar davor steht (ohne andere Wörter dazwischen). Alle Ziffern/Zahlen NACH dem Wort "Bruttozins" sind NICHT der Bruttozins-Wert. Ignoriere "Nettozins" vollständig. Der letzte Kontostand → vermoegensstand_3112.
- BANK-R Kontoauszug: "Saldo zu Ihren Gunsten" → vermoegensstand_3112; "Abschlussbetreffnis" → bruttoertrag
- Zins- und Saldoverzeichnis BANK-R: "Endsaldo" → vermoegensstand_3112; "Zinszahlung" → bruttoertrag
- BANK-P ZINSABSCHLUSS (REP_P): "Kontostand" nach "Total" → vermoegensstand_3112
- BANK-H/BANK-N Kapitalbescheinigung: "Guthabensaldo" → vermoegensstand_3112

Beispiele:
- "500.00 19198.11 Bruttozins 17841.04 Zins ... Nettozins 17841.04" → bruttoertrag = "19198.11" (letzte Zahl VOR "Bruttozins" ist 19198.11; NICHT 500.00; NICHT 17841.04 nach "Bruttozins"!)
- "100.00 200.00 300.00 Bruttozins 200.00 Zins" → bruttoertrag = "300.00" (immer die Zahl DIREKT vor "Bruttozins")
- "Bruttozins  320.00" → bruttoertrag value = "320.00"  (Standard: Betrag NACH Label)
- "Saldo zu Ihren Gunsten  38'196.09" → vermoegensstand_3112 value = "38'196.09"
- "Endsaldo  25'983.46" → vermoegensstand_3112 value = "25'983.46"
- "Guthabensaldo am 31.12.2022  11'056" → vermoegensstand_3112 value = "11'056"
- "Verrechnungssteuer (35%)  CHF  112.00" → verrechnungssteuer value = "112.00"

DOKUMENTTEXT:
{plain_text}
"""


_KK_PRAEMIE_PROMPT = """Du extrahierst Felder aus einer Schweizer Krankenkassen-Prämienbescheinigung (Ziffer 16).

Regeln:
- Übernimm Werte WORTWÖRTLICH aus dem Text (keine Umformatierung, keine Berechnung).
- tag_refs immer als leere Liste [] angeben.
- praemie_kvg_total: Jahres-Total der KVG-Grundversicherung — KEINE Hochrechnung Monatsprämie × 12.
- praemie_vvg_total: Jahres-Total Zusatzversicherung (nur wenn explizit auf der Bescheinigung).
- versicherte_person_name: vollständiger Name der versicherten Person.
- kasse: Name der Krankenkasse (z.B. "KK-C", "KK-S", "KK-H", "KK-W").
- jahr: 4-stelliges Steuerjahr.

Labels nach Kasse:
- KK-C / KK-H: "Grundversicherung (KVG)  [Betrag]" → praemie_kvg_total
- KK-C / KK-H: "Zusatzversicherung (VVG)  [Betrag]" → praemie_vvg_total
- KK-S: "VVG (Januar–Dezember)  Total  [Betrag]" → praemie_vvg_total (KVG oft nicht im Dokument)
- Alle: "Prämien- und Kostenübersicht für das Steuerjahr [Jahr]" → jahr

Selbstgetragene Behandlungskosten (steuerlich ein EIGENER Abzug, nicht Prämie):
- selbstgetragene_kosten: das ausgewiesene TOTAL der Kosten, die du selbst
  getragen hast — Franchise, Selbstbehalt, Spitalbeitrag, nicht versicherte
  Behandlungen. NIEMALS eine Prämie hier eintragen.
- Labels je nach Kasse: "Total von Ihnen bezahlte Kosten", "Nichtversicherte
  Behandlungskosten Total", "Total der von der Kasse nicht getragenen
  Krankheitskosten", "Ihre Kostenbeteiligung", "Total selbstgetragene
  Krankheits- und Unfallkosten".
- Nur ein ausgewiesenes Total übernehmen — Einzelpositionen NICHT addieren.
- Steht kein solches Total im Dokument: null.

- praemie_total: nur wenn KVG und VVG NICHT getrennt ausgewiesen sind.

Beispiele:
- "Grundversicherung (KVG)  4'200.00" → praemie_kvg_total value = "4'200.00"
- "Zusatzversicherung (VVG)  1'800.00" → praemie_vvg_total value = "1'800.00"
- "VVG (Januar–Dezember)  Total  3'600.00" → praemie_vvg_total value = "3'600.00"
- "Versicherte Person: Muster, Hans" → versicherte_person_name value = "Muster, Hans"

DOKUMENTTEXT:
{plain_text}
"""


_SAEULE_3A_PROMPT = """Du extrahierst Felder aus einer Schweizer Säule-3a-Bescheinigung (Ziffer 14).

Regeln:
- Übernimm Werte WORTWÖRTLICH aus dem Text (keine Umformatierung, keine Berechnung).
- tag_refs immer als leere Liste [] angeben.
- einzahlung_betrag: Total der Einzahlungen im Jahr — nur das Jahrestotal, keine Einzelbuchungen.
- stiftung: Name der Vorsorgestiftung / des Anbieters (z.B. "STIFTUNG-Z", "STIFTUNG-V", "STIFTUNG-F").
- kontoinhaber_name: vollständiger Name der kontoinhabenden Person (aus dem Adressblock).
- jahr: 4-stelliges Steuerjahr — aus der Vertrags-/Jahresspalte, nicht aus dem Druckdatum.

Dokumenttypen:
- Form. 21 EDP dfi (ESTV-Standard, alle Schweizer Stiftungen):
  Feld "r Total Beiträge an die Säule 3a" → einzahlung_betrag (steht direkt nach dem Label).
  Feld "a Name und Sitz der Vorsorgeeinrichtung" → stiftung.
  Feld "n Vorsorgevereinbarungen ... Jahr [Jahreszahl]" → jahr.
  Dreisprachig: Label erscheint auch als "r Total des cotisations au pilier 3a" / "Totale contributi".
- BANK-P Vorsorge 3a / STIFTUNG-V / STIFTUNG-F: abweichendes Format — "Einzahlung [Datum] CHF [Betrag]".

Beispiele:
- "r Total Beiträge an die Säule 3a  7'056" → einzahlung_betrag value = "7'056"
- "r Total Beiträge an die Säule 3a - Total des cotisations...  12'413" → einzahlung_betrag value = "12'413"
- "Einzahlung 2024  CHF  7'056.00" → einzahlung_betrag value = "7'056.00"

DOKUMENTTEXT:
{plain_text}
"""


_WERTSCHRIFTEN_PROMPT = """Du extrahierst Felder aus einem Schweizer Wertschriftenverzeichnis / Depot-Auszug / Steuerausweis.

Regeln:
- Übernimm Werte WORTWÖRTLICH aus dem Text (keine Umformatierung, keine Berechnung).
- tag_refs immer als leere Liste [] angeben.
- Felder, die im Text fehlen: null zurückgeben.
- bestand_3112: Depot-/Portfolio-Gesamtwert per 31.12. — suche "Total Steuerwert", "Gesamtvermögen", "Bestand per 31.12.", "Total Wertschriften + Bankkonten". Das GESAMT-Total, nicht Einzelpositionen.
- bruttoertrag_total: Summe aller Erträge im Jahr — suche "Total Ertrag", "Bruttoertrag total", "Subtotal Ertrag". Bei BROKER-S/BROKER-X: Summe von Ertrag A + Ertrag B.
- verrechnungssteuer_total: abgezogene Verrechnungssteuer auf CH-Ausschüttungen. Oft null bei ausländischen ETFs.
- kontoinhaber_name: vollständiger Name der depot-haltenden Person(en).
- institut: Bank / Broker / Vermögensverwalter (z.B. "BROKER-S AG", "BANK-P AG", "BANK-R").
- jahr: 4-stelliges Steuerjahr.

Dokumenttypen:
- BROKER-S Steuerausweis (Custodian: BROKER-X): "Total Steuerwert [Betrag]" → bestand_3112; "Total Ertrag [Betrag]" → bruttoertrag_total; "Vermögensverwalter BROKER-S AG" → institut.
- BANK-P Portfolio-Wertentwicklung: "GESAMTVERMÖGEN IN CHF [Betrag]" (GROSSBUCHSTABEN!) → bestand_3112. Kein bruttoertrag_total (Performance-Dokument, kein Steuerausweis).
- BROKER-T AG Steuerauszug: "[Betrag](1) (1) Davon A [A] und B [B]" → bestand_3112 ist [Betrag]; "Bruttoertrag ... A [A]" + "Bruttoertrag ... B [B]" → bruttoertrag_total = A + B summieren.
- BANK-P Corporate-Action (Dividende): "Betrag CHF [Brutto]" → bruttoertrag_total; "Verrechnungssteuer 35% (CH) CHF [Betrag]" → verrechnungssteuer_total.
- BANK-R Freies Vermögen: "Total Steuerwert" → bestand_3112.

Beispiele:
- "Total Steuerwert  125'000.00" → bestand_3112 value = "125'000.00"
- "Total Ertrag  3'200.00" → bruttoertrag_total value = "3'200.00"
- "Verrechnungssteuer  1'120.00" → verrechnungssteuer_total value = "1'120.00"

DOKUMENTTEXT:
{plain_text}
"""


_SPENDEN_PROMPT = """Du extrahierst Felder aus einer Schweizer Spendenquittung / Zuwendungsbestätigung.

Regeln:
- Übernimm Werte WORTWÖRTLICH aus dem Text (keine Umformatierung, keine Berechnung).
- tag_refs immer als leere Liste [] angeben.
- empfaenger: Name der gemeinnützigen Organisation / Stiftung (z.B. "WWF Schweiz", "Rotes Kreuz").
- spender_name: vollständiger Name der spendenden Person.
- betrag: Spendenbetrag in CHF.
- jahr: 4-stelliges Steuerjahr.

Beispiel:
- "Spendenbetrag  CHF  500.00" → betrag value = "500.00"

DOKUMENTTEXT:
{plain_text}
"""


_BERUFSAUSLAGEN_PROMPT = """Du extrahierst Felder aus einer Schweizer Berufsauslagen- / Weiterbildungsbestätigung.

Regeln:
- Übernimm Werte WORTWÖRTLICH aus dem Text (keine Umformatierung, keine Berechnung).
- tag_refs immer als leere Liste [] angeben.
- anbieter: Kurs- / Weiterbildungsanbieter (Schule, Hochschule — z.B. "EB Zürich", "Universität St. Gallen").
- person_name: vollständiger Name der teilnehmenden Person.
- betrag: Kurskosten gesamt in CHF.
- jahr: 4-stelliges Steuerjahr.
- kursart: optional (z.B. "MAS", "CAS", "Sprachkurs", "Weiterbildung").

Beispiel:
- "Kurskosten gesamt  CHF  1'850.00" → betrag value = "1'850.00"

DOKUMENTTEXT:
{plain_text}
"""


_KINDERBETREUUNG_PROMPT = """Du extrahierst Felder aus einer Schweizer Kinderbetreuungsbestätigung (Kita / Hort / Tagesfamilie).

Regeln:
- Übernimm Werte WORTWÖRTLICH aus dem Text (keine Umformatierung, keine Berechnung).
- tag_refs immer als leere Liste [] angeben.
- betrag: Jahresbetrag der Betreuungskosten in CHF — KEINE Hochrechnung Monatsbeitrag × 12.
- anbieter: Kita / Hort / Tagesfamilie (z.B. "Kita Sonnenschein", "Hort am Berg").
- kind_name: vollständiger Name des betreuten Kindes.
- jahr: 4-stelliges Steuerjahr.
- betreuungs_typ: optional (z.B. "Kita", "Hort", "Tagesfamilie", "Mittagstisch").

Dokumenttypen und Schlüssellabels:
- Zahlungsbestätigung (Krippe / Hort): "einen Elternbeitrag von CHF [Betrag]" → betrag.
  Der Krippenname steht im Briefkopf UND im Satz "an die [Name] in [Ort]".
- Steuernachweis-Brief (Kita): Tabelle "Nr. Status Leistungszeitraum Betrag" mit Abschlusszelle "Total [Betrag]" → betrag.
  Das Jahr steht im Brieftext: "im Jahr [Jahr] folgende Rechnungen".

Beispiele:
- "einen Elternbeitrag von CHF 9'699.62 an die" → betrag value = "9'699.62"
- "Total  23'269.32" (am Tabellenende im Steuernachweis-Brief) → betrag value = "23'269.32"
- "Betreuungskosten 2024  CHF  18'500.00" → betrag value = "18'500.00" (alternatives Format)

DOKUMENTTEXT:
{plain_text}
"""


_HYPOTHEK_PROMPT = """Du extrahierst Felder aus einer Schweizer Hypothek-Zinsbestätigung / Schuldzinsausweis.

Regeln:
- Übernimm Werte WORTWÖRTLICH aus dem Text (keine Umformatierung, keine Berechnung).
- tag_refs immer als leere Liste [] angeben.
- schuldzinsen: Jahresbetrag der Hypothekarzinsen in CHF.
- schuldsaldo_3112: Schuldsaldo per 31.12. in CHF.
- kontoinhaber_name: vollständiger Name der verschuldeten Person(en); bei Gemeinschaftshypothek beide Namen.
- institut: Bank (z.B. "BANK-U", "BANK-Z", "BANK-R").
- liegenschaft: Adresse der Liegenschaft.
- jahr: 4-stelliges Steuerjahr.

Beispiele:
- "Schuldzinsen gesamt  CHF  8'400.00" → schuldzinsen value = "8'400.00"
- "Schuldsaldo per 31.12.2024  420'000.00" → schuldsaldo_3112 value = "420'000.00"

DOKUMENTTEXT:
{plain_text}
"""


_LIEGENSCHAFTSUNTERHALT_PROMPT = """Du extrahierst Felder aus einer Schweizer Handwerkerrechnung / Liegenschaftsunterhalt-Bestätigung.

Regeln:
- Übernimm Werte WORTWÖRTLICH aus dem Text (keine Umformatierung, keine Berechnung).
- tag_refs immer als leere Liste [] angeben.
- betrag: Rechnungsbetrag in CHF (Total / Endbetrag der Rechnung).
- handwerker_anbieter: Handwerker / Firma (z.B. "Maler Müller AG", "BauPlan Schweiz GmbH").
- eigentuemer_name: vollständiger Name der Eigentümerin / des Eigentümers.
- liegenschaft: Adresse der Liegenschaft.
- jahr: 4-stelliges Steuerjahr (aus Rechnungsdatum).
- werterhaltend: IMMER null lassen — wird vom System nachträglich bestimmt.

Beispiel:
- "Total brutto  CHF  42'500.00" → betrag value = "42'500.00"

DOKUMENTTEXT:
{plain_text}
"""


_KRANKHEITSKOSTEN_PROMPT = """Du extrahierst Felder aus einem Schweizer Krankheitskosten-Dokument.

Das Dokument kann sein:
A) Eine Einzelrechnung (Arzt / Apotheke / Klinik / Therapie / Zahnarzt)
B) Ein KK-"Auszug für Ihre Steuererklärung" (KK-H, KK-C, KK-V etc.) — jährliche Kostenübersicht

Regeln:
- Übernimm Werte WORTWÖRTLICH aus dem Text (keine Umformatierung, keine Berechnung).
- tag_refs immer als leere Liste [] angeben.
- Felder, die im Text fehlen: null zurückgeben.

Feldregeln je Dokumenttyp:
Typ A (Einzelrechnung):
  - betrag: Patientenanteil / Rechnungsbetrag in CHF (nach Kassenabzug falls vorhanden).
  - leistungserbringer: Name des Arztes / der Apotheke / Klinik.
  - person_name: Name der behandelten Person.

Typ B (KK-Jahresübersicht / "Auszug für Steuererklärung"):
  - betrag: "Total selbstgetragene Krankheits- und Unfallkosten" — das ist der steuerrelevante Betrag.
  - leistungserbringer: Name der Krankenversicherung (z.B. "KK-H", "KK-C", "KK-V", "KK-W").
    NICHT einzelne Apotheken-/Arzt-Namen aus den Detailaufstellungen!
  - person_name: Name des Versicherten.
  - kategorie: "Krankheitskosten" (für Typ B immer dieser Wert).

- jahr: 4-stelliges Steuerjahr.

Beispiele:
- "Patientenanteil  CHF  87.50" → betrag value = "87.50"
- "Total selbstgetragene Krankheits- und Unfallkosten  450.00" → betrag value = "450.00"
- "Total CHF  1'357.00" (bei Jahresübersicht KK-V/KK-H) → betrag value = "1'357.00"

DOKUMENTTEXT:
{plain_text}
"""


PROMPT_TEMPLATES: dict[type[BaseModel], str] = {
    LohnausweisRaw: _LOHNAUSWEIS_PROMPT,
    BankZinsausweisRaw: _BANK_ZINSAUSWEIS_PROMPT,
    KkPraemienbescheinigungRaw: _KK_PRAEMIE_PROMPT,
    Saeule3aRaw: _SAEULE_3A_PROMPT,
    WertschriftenverzeichnisRaw: _WERTSCHRIFTEN_PROMPT,
    SpendenquittungRaw: _SPENDEN_PROMPT,
    BerufsauslagenRaw: _BERUFSAUSLAGEN_PROMPT,
    KinderbetreuungRaw: _KINDERBETREUUNG_PROMPT,
    HypothekZinsbestaetigungRaw: _HYPOTHEK_PROMPT,
    LiegenschaftsunterhaltRaw: _LIEGENSCHAFTSUNTERHALT_PROMPT,
    KrankheitskostenRaw: _KRANKHEITSKOSTEN_PROMPT,
}


def get_model_digest(model: str = DEFAULT_MODEL) -> dict[str, str]:
    """Gibt ``{model, digest}`` zurück — für Drift-Protection (D-B4).

    Quelle ist ``ollama.list()``: ältere Ollama-Client-Versionen lieferten den
    Digest unter ``ollama.show().digest`` zurück, neuere (>= 0.5) exponieren
    ihn ausschliesslich auf den ``ListResponse.models[*]``-Einträgen. Diese
    Implementierung ist gegen beide Varianten robust und bevorzugt die
    Voll-SHA aus ``list()`` — fällt auf ``show()`` zurück, falls das Modell
    in der Liste fehlt.
    """
    import ollama

    def _attr(obj: object, key: str) -> object:
        if isinstance(obj, dict):
            return obj.get(key)
        return getattr(obj, key, None)

    try:
        listed = ollama.list()
        models = _attr(listed, "models") or []
        for m in models:
            tag = _attr(m, "model") or _attr(m, "name")
            if tag == model:
                digest = _attr(m, "digest")
                if digest:
                    return {"model": model, "digest": str(digest)}
    except Exception:
        pass

    info = ollama.show(model)
    digest = (
        _attr(info, "digest")
        or _attr(_attr(info, "details") or {}, "digest")
        or _attr(_attr(info, "modelinfo") or {}, "general.digest")
        or _attr(_attr(info, "model_info") or {}, "general.digest")
    )
    if not digest:
        keys = list(info.keys()) if isinstance(info, dict) else dir(info)
        raise KeyError(
            f"Kein digest-Feld weder in ollama.list() noch ollama.show({model!r}) gefunden: keys={keys}"
        )
    return {"model": model, "digest": str(digest)}


# Maximale Klartext-Länge vor Truncation (Finding E6: 8'000 → 12'000). Als
# Modul-Konstante exponiert, damit Tests sie ohne Ollama-Call prüfen können.
_MAX_PLAIN_TEXT_LIMIT_FOR_TEST = 12_000

# Total-/Summen-Labels eines Wertschriftenverzeichnisses (E6). Die Summenseite
# steht typischerweise am Ende eines langen WSV und geht bei reiner Head-
# Truncation verloren.
_WSV_SUMMEN_LABELS: tuple[str, ...] = (
    "Steuerwert der A- und B-Werte",
    "Steuerwert",
    "Bruttoertrag",
    "Total Ertrag",
    "Subtotal Ertrag",
)
# Fenstergrösse (Zeichen) rund um ein gefundenes Summen-Label.
_SUMMEN_FENSTER_RADIUS = 400


def _extract_summen_fenster(full_text: str) -> str:
    """Extrahiert die Summenseiten-Fenster eines WSV (Finding E6).

    Sucht im UNGEKÜRZTEN Text nach Total-/Summen-Labels und liefert die
    umgebenden Fenster (Label + nachfolgende Beträge) als zusammengefügten
    String. So bleibt die Ertrags-/Steuerwert-Summe erhalten, auch wenn der
    Haupttext gekürzt wurde. Leerer String, wenn kein Label gefunden wird.
    """
    if not full_text:
        return ""
    fenster: list[str] = []
    gesehen: set[int] = set()
    low = full_text.lower()
    for label in _WSV_SUMMEN_LABELS:
        start = 0
        ll = label.lower()
        while True:
            idx = low.find(ll, start)
            if idx < 0:
                break
            lo = max(0, idx - 40)
            hi = min(len(full_text), idx + _SUMMEN_FENSTER_RADIUS)
            # Doppel-Fenster vermeiden (überlappende Labels).
            if not any(abs(idx - g) < _SUMMEN_FENSTER_RADIUS for g in gesehen):
                fenster.append(full_text[lo:hi].strip())
                gesehen.add(idx)
            start = idx + len(ll)
    if not fenster:
        return ""
    return "--- Summenseite (E6) ---\n" + "\n".join(fenster)


def extract(
    tagged_text: str,
    schema_class: type[BaseModel] = LohnausweisRaw,
    model: str = DEFAULT_MODEL,
) -> BaseModel:
    """Ruft Ollama im JSON-Schema-Mode auf und validiert gegen ``schema_class``.

    Vereinfachte Pipeline (ohne [T<id>]-Tags):
    1. ``tagged_text`` wird von Tags bereinigt → sauberer Klartext.
    2. Einfacher, domänen-spezifischer Prompt ohne Tag-Instruktionen.
    3. LLM liefert nur Werte zurück (tag_refs = [] per Default).
    4. Anker-Suche erfolgt via Verbatim-Fallback in anchor_resolver.

    Args:
        tagged_text: tag-präfixierter Text (Output von tokenize) — Tags werden
            intern entfernt, bevor der Text an den LLM übergeben wird.
        schema_class: Pydantic-Raw-Klasse für den Belegtyp.
        model: Ollama-Modell-Tag.

    Returns:
        Pydantic-validierte Instanz von ``schema_class``.
    """
    if schema_class not in PROMPT_TEMPLATES:
        raise KeyError(
            f"Kein Prompt-Template für {schema_class.__name__} registriert — "
            f"verfügbar: {[c.__name__ for c in PROMPT_TEMPLATES]}"
        )

    import ollama

    # Tags entfernen → LLM bekommt sauberen Klartext.
    plain_text = strip_tags(tagged_text)

    # Eingabe kürzen. Limit von 8'000 auf 12'000 erhöht (Finding E6) — lange
    # Wertschriftenverzeichnisse verloren sonst die Summenseite am Ende.
    _MAX_PLAIN_TEXT_CHARS = _MAX_PLAIN_TEXT_LIMIT_FOR_TEST
    _text_truncated = False
    if len(plain_text) > _MAX_PLAIN_TEXT_CHARS:
        # Stille Truncation ist gefährlich: der LLM sieht nur den Anfang des
        # Belegs, am Ende stehende Beträge fehlen. Maschinenlesbaren Marker
        # setzen + warnen, damit nachgelagert (build_tax_output) als
        # manuell-prüfen gekennzeichnet wird (Korrektheits-Constraint).
        _text_truncated = True
        _logging.warning(
            "Beleg-Text auf %d Zeichen gekürzt (Original %d) — Extraktion ggf. "
            "unvollständig, wird als text_truncated markiert.",
            _MAX_PLAIN_TEXT_CHARS, len(plain_text),
        )
        # E6 WSV-Summenfenster: bei Wertschriftenverzeichnissen die Summenseite
        # priorisieren. Aus dem UNGEKÜRZTEN Text ein Label-Fenster um die
        # Total-Labels extrahieren und an den gekürzten Text ANHÄNGEN, damit
        # Ertrags-B/Steuerwert nicht verloren gehen.
        _summen_fenster = ""
        if schema_class is WertschriftenverzeichnisRaw:
            _summen_fenster = _extract_summen_fenster(plain_text)
        plain_text = plain_text[:_MAX_PLAIN_TEXT_CHARS]
        if _summen_fenster:
            plain_text = plain_text + "\n\n" + _summen_fenster

    prompt = PROMPT_TEMPLATES[schema_class].format(plain_text=plain_text)
    response = ollama.chat(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        format=schema_class.model_json_schema(),
        options={"temperature": 0, "num_ctx": 8192, "num_predict": 8192},
    )
    msg = response["message"] if isinstance(response, dict) else response.message
    content = msg["content"] if isinstance(msg, dict) else msg.content
    result = schema_class.model_validate_json(content)

    # Regex-Overrides: zuverlässigere Extraktion für klar beschriftete Felder.
    # Schemaklasse → Belegtyp-String für Dispatcher in regex_extract.py.
    from extractors.regex_extract import regex_overrides
    from extractors.schema import TaggedField
    _SCHEMA_TO_BELEGTYP: dict[type[BaseModel], str] = {
        LohnausweisRaw: "lohnausweis",
        BankZinsausweisRaw: "bank_zinsausweis",
        KkPraemienbescheinigungRaw: "kk_praemienbescheinigung",
        Saeule3aRaw: "saeule_3a",
        WertschriftenverzeichnisRaw: "wertschriftenverzeichnis",
        KrankheitskostenRaw: "krankheitskosten",
        KinderbetreuungRaw: "kinderbetreuung",
    }
    belegtyp = _SCHEMA_TO_BELEGTYP.get(schema_class, "")
    if belegtyp:
        for field_name, value in regex_overrides(plain_text, belegtyp).items():
            if hasattr(result, field_name):
                setattr(result, field_name, TaggedField(value=value, tag_refs=[]))

    # Truncation-Marker an die Result-Instanz hängen (maschinenlesbar für die
    # Pipeline). pydantic-Modelle erlauben Extra-Attribute via object.__setattr__
    # nicht zuverlässig — daher als reguläres Attribut setzen.
    try:
        object.__setattr__(result, "_text_truncated", _text_truncated)
    except (AttributeError, TypeError):
        pass

    return result


def extract_cloud(redacted_doc: str, model: str = "claude-haiku") -> LohnausweisRaw:
    """Stub für Cloud-Engine (Phase 4, PRV-02)."""
    raise NotImplementedError(
        "Cloud-Engine ist Phase 4 — siehe PRV-02 in REQUIREMENTS.md"
    )
