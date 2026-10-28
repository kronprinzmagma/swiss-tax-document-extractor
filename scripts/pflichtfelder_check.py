#!/usr/bin/env python3
"""Sind pro Dokument alle Pflichtfelder da — und sind die Werte realistisch?

Für den anonymisierten Korpus gedacht: dort darf geprüft werden, ohne die
echten Dokumente zu lesen (261024-jga, „prüf zuerst selbst").

Zwei Quellen, je nach Dokumenttyp:

* **Konten und Hypotheken** — die Kette Rohtabelle → Objekte (eine Seite,
  ein Objekt; dieselbe IBAN/Nummer = dasselbe Objekt). Pflicht je Objekt:
  Kennung, Währung, Saldo bzw. Schuld und Zins. Realistisch: IBAN-Prüfsumme,
  Zins höchstens 5 % des Saldos, Nachrechnung Kapital × Satz × Tage / 360.
* **Alle anderen** — das Extraktionsergebnis (`_results_full.json`) gegen
  die Pflichtfelder des Zielmodells (`extractors/steuer_zielmodell.py`).

Die Pflichtwerte je Dokumenttyp stehen in `extractors/datenmodell.yaml`
(``pflicht_je_typ``); hier steht nur, wo sie in der Leiste zu finden sind
(``PFLICHT_IN_DER_LEISTE``). Ein Pflichtwert ohne Zuordnung ist ein Fehler,
kein stilles Auslassen (261025-jm6, A1).

„Im Ordner" heisst: im echten PDF-Ordner (``--pdf-dir``). Ohne ihn hiesse es
nur „im letzten Lauf" (261025-jm6, A3).

Aufruf für den echten Lauf (``make pruefen``, PDFDIR wie im Makefile)::

    PYTHONPATH=. .venv/bin/python scripts/pflichtfelder_check.py \\
        --samples output/latest/json --pdf-dir "$PDFDIR"

Für den anonymisierten Korpus (ohne PDFs)::

    PYTHONPATH=. .venv/bin/python scripts/pflichtfelder_check.py \\
        --samples evals/samples_real_2025 --ohne-ordner
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

KETTE = ("bank_zinsausweis", "hypothek_zinsbestaetigung")

# Grenzen, die NICHT im Datenmodell stehen (261025-jm6, A1). Säule-3a- und
# Kita-Maximum stehen dort und werden über extractors/datenmodell.py gelesen.
# Die Meldungstexte nennen die Werte wörtlich — wer eine Grenze ändert,
# ändert den Text mit.
ZINS_HOECHSTENS_ANTEIL_SALDO = Decimal("0.05")    # nicht im Datenmodell; Vorschlag an der Anwender
NETTO_MINDESTENS_ANTEIL_BRUTTO = Decimal("0.6")   # nicht im Datenmodell; Vorschlag an der Anwender
LOHN_TOLERANZ_FRANKEN = Decimal("2")              # nicht im Datenmodell; Vorschlag an der Anwender
PRAEMIE_HOECHSTENS = Decimal("12000")             # nicht im Datenmodell; Vorschlag an der Anwender
DEPOT_ERTRAG_HOECHSTENS_ANTEIL = Decimal("0.15")  # nicht im Datenmodell; Vorschlag an der Anwender
ZINSSATZ_MINDESTENS = Decimal("0.1")              # nicht im Datenmodell; Vorschlag an der Anwender
ZINSSATZ_HOECHSTENS = Decimal("6")                # nicht im Datenmodell; Vorschlag an der Anwender


def _wert_da(feld: dict | None) -> bool:
    if not feld:
        return False
    v = str(feld.get("value") or "").strip()
    return bool(v) and not v.startswith("manual_review")


def pruefe_kette(samples: Path) -> list[dict]:
    from extractors import bankobjekte, rohtabelle

    dokumente = []
    for pfad in sorted(samples.glob("*.json")):
        if pfad.name.startswith("_") or pfad.name.endswith(".truth.json"):
            continue
        try:
            d = json.loads(pfad.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(d, dict) or "pages" not in d:
            continue
        tab = rohtabelle.baue(d)
        hypothek = any(bankobjekte._HYPOTHEK_RE.search(z.text) for z in tab.zeilen)
        if d.get("belegtyp") in KETTE or hypothek:
            dokumente.append((str(d.get("pdf_name") or pfad.name), tab))
    befunde = []
    for o in bankobjekte.objekte(dokumente):
        fehlt, unreal = [], []
        # Nebenkonto ohne Guthaben und Zins: für die Erklärung leer.
        if (o.art == "Konto" and bankobjekte.einwert(o.werte.get("saldo"))[0] is None
                and not bankobjekte.einwert(o.werte.get("zins"))[0]):
            continue
        def w(k):
            return bankobjekte.einwert(o.werte.get(k))
        # Im anonymisierten Korpus ist die IBAN maskiert („CH00"): vorhanden,
        # aber die Prüfsumme lässt sich nur am echten Lauf testen.
        maskiert = (o.werte.get("_iban_ungueltig") or [None])[0]
        maskiert = maskiert is not None and "maskiert" in str(maskiert.wert)
        if o.art == "Konto":
            if not o.kennung or (o.kennung.herkunft == "annahme" and not maskiert):
                fehlt.append("Konto-Nr./IBAN")
            if w("saldo")[0] is None:
                fehlt.append("Saldo")
            elif w("saldo")[1] == "widerspruch":
                unreal.append("Saldo widersprüchlich")
            saldo, zins = w("saldo")[0], w("zins")[0]
            if (isinstance(saldo, Decimal) and isinstance(zins, Decimal)
                    and saldo > 0 and zins / saldo > ZINS_HOECHSTENS_ANTEIL_SALDO):
                unreal.append(f"Zins {zins / saldo * 100:.1f} % des Saldos")
        else:
            if not o.werte.get("nummer"):
                fehlt.append("Hypothekar-Nr.")
            for k, name in (("schuld", "Schuld"), ("zins", "Schuldzins")):
                if w(k)[0] is None:
                    fehlt.append(name)
            if not all(x.wert for x in o.werte.get("_kontrolle", [])):
                unreal.append("Nachrechnung geht nicht auf")
            if o.werte.get("_summe_stimmt") and not o.werte["_summe_stimmt"][0].wert:
                unreal.append("Summe ≠ Rechnungsbetrag")
        if o.waehrung.herkunft == "annahme":
            fehlt.append("Währung")
        if "_iban_ungueltig" in o.werte and not maskiert:
            unreal.append(f"IBAN {o.werte['_iban_ungueltig'][0].wert}")
        befunde.append({"art": o.art, "quellen": o.quellen,
                        "bezeichnung": o.bezeichnung.wert if o.bezeichnung else "",
                        "fehlt": fehlt, "unrealistisch": unreal})
    return befunde


def _zahl(feld: dict | None):
    from extractors.eintraege import betrag
    return betrag((feld or {}).get("value")) if _wert_da(feld) else None


def realismus(typ: str, felder: dict) -> list[str]:
    """Kann der Wert so stimmen? Grenzen aus der Wegleitung ZH 2025.

    Säule-3a- und Kita-Maximum aus dem Datenmodell, ohne stille
    Vorgabewerte (261025-jm6, A1).
    """
    from extractors.datenmodell import kita_maximum_je_kind, saeule_3a_maximum
    from extractors.eintraege import anzeige

    aus = []
    if typ == "lohnausweis":
        brutto, netto = _zahl(felder.get("bruttolohn_pos8")), _zahl(felder.get("nettolohn_pos11"))
        abzug = (_zahl(felder.get("ahv_alv_nbu_abzug_pos9")) or 0) + \
                (_zahl(felder.get("bvg_abzug_pos10a")) or 0)
        if brutto and netto:
            if netto > brutto:
                aus.append("Netto > Brutto")
            elif abzug and abs(brutto - abzug - netto) > LOHN_TOLERANZ_FRANKEN:
                aus.append(f"Brutto − Abzüge ≠ Netto (Differenz {brutto - abzug - netto})")
            elif netto < brutto * NETTO_MINDESTENS_ANTEIL_BRUTTO:
                aus.append("Netto unter 60 % von Brutto")
    elif typ == "saeule_3a":
        e = _zahl(felder.get("einzahlung_betrag"))
        maximum = saeule_3a_maximum()
        if e is not None and e > maximum:
            aus.append(f"Einzahlung über Maximum {anzeige(maximum)} ({e})")
    elif typ == "kinderbetreuung":
        b = _zahl(felder.get("betrag"))
        maximum = kita_maximum_je_kind()
        if b is not None and b > maximum:
            aus.append(f"über Maximum {anzeige(maximum)} je Kind ({b})")
    elif typ == "kk_praemienbescheinigung":
        for k in ("praemie_kvg_total", "praemie_vvg_total", "praemie_total"):
            v = _zahl(felder.get(k))
            # 0.00 ist erlaubt: manche Übersicht sagt ausdrücklich „Prämien
            # 0.00", weil ein anderer Versicherer abrechnet. Ob die Person
            # irgendwo eine Prämie hat, prüft praemien_je_person.
            if v is not None and not (Decimal("0") <= v <= PRAEMIE_HOECHSTENS):
                aus.append(f"{k} ausserhalb 0–12'000 ({v})")
    elif typ == "wertschriftenverzeichnis":
        b = _zahl(felder.get("bestand_3112"))
        e = _zahl(felder.get("bruttoertrag_total"))
        if (b is not None and e is not None and b > 0
                and e / b > DEPOT_ERTRAG_HOECHSTENS_ANTEIL):
            aus.append(f"Ertrag {e / b * 100:.0f} % des Bestands")
    return aus


def pruefe_extraktion(samples: Path) -> list[dict]:
    from extractors.steuer_zielmodell import get_spec

    pfad = samples / "_results_full.json"
    if not pfad.exists():
        return []
    aus = []
    for e in json.loads(pfad.read_text(encoding="utf-8")):
        typ = e.get("belegtyp") or "unknown"
        if typ in KETTE:
            continue
        spec = get_spec(typ)
        if spec is None:
            aus.append({"dokument": e.get("pdf_name"), "typ": typ,
                        "fehlt": ["Dokumenttyp nicht erkannt"], "ohne_anker": [],
                        "unrealistisch": []})
            continue
        felder = {f["feld"]: f for f in e.get("fields") or []}
        fehlt = [n for n in spec.mandatory_field_names() if not _wert_da(felder.get(n))]
        ohne = [n for n in spec.mandatory_field_names()
                if _wert_da(felder.get(n)) and not felder[n].get("anchor_valid")]
        aus.append({"dokument": e.get("pdf_name"), "typ": typ,
                    "fehlt": fehlt, "ohne_anker": ohne,
                    "unrealistisch": realismus(typ, felder)})
    return aus


def praemien_je_person(samples: Path) -> dict[str, Decimal]:
    """Prämiensumme je versicherter Person über alle Dokumente.

    Die Regel gilt der Person, nicht dem Dokument (der Anwender): „Wenn es Prämien
    gibt, dann reicht es — egal in welchem Dokument."
    """
    pfad = samples / "_results_full.json"
    if not pfad.exists():
        return {}
    summe: dict[str, Decimal] = {}
    for e in json.loads(pfad.read_text(encoding="utf-8")):
        if e.get("belegtyp") not in ("kk_praemienbescheinigung", "krankheitskosten"):
            continue
        felder = {f["feld"]: f for f in e.get("fields") or []}
        person = str((felder.get("versicherte_person_name") or felder.get("person_name")
                      or {}).get("value") or "?").replace(",", "").strip()
        person = " ".join(sorted(person.split()))
        for k in ("praemie_kvg_total", "praemie_vvg_total", "praemie_total"):
            v = _zahl(felder.get(k))
            summe[person] = summe.get(person, Decimal(0)) + (v or Decimal(0))
    return summe


# ---------------------------------------------------------------------------
# Prüfung auf der AUSGABE (261024-jga, der Anwender: „jeder Dokumenttyp muss gewisse
# Felder haben. IMMER"). Die Prüfung oben las Kette und Lauf je für sich;
# die Leiste nahm aber einen dritten Weg. Darum zählt hier nur, was in der
# Leiste ankommt — dort trägt der Mensch ab.

UNBEKANNT = ("", "unbekannt", "unbekannt/manuell", "manuell")


def _leiste(samples: Path, pdf_dir: Path | None = None) -> dict:
    """Leistendaten wie ``make extension-daten``, aber mit Rollen statt Namen.

    ``pdf_dir``: der echte Ordner bestimmt, was verwaist ist (261025-jm6, A3).
    """
    from unittest import mock

    from scripts import export_extension
    with mock.patch("scripts.build_review_html.personennamen", return_value={}):
        return export_extension.baue(samples, pdf_dir=pdf_dir)


# Feste Marker für Personen — Code-Konstanten, keine Personendaten. Alles
# andere (Freitext aus der Durchsicht) ist in der teilbaren Fassung
# „‹Person›" (261025-jm6, A6).
FESTE_PERSONEN = ("leer", "Familie", "familie", "ohne Person", "–")


def _person_marke(p) -> str:
    """Rolle oder fester Marker bleibt; ein Name wird „‹Person›"."""
    from extractors.datenmodell import rollen

    p = str(p or "").strip()
    if not p:
        return "leer"
    if p in rollen() or p in FESTE_PERSONEN or p in UNBEKANNT:
        return p
    return "‹Person›"


# Wo die Pflichtwerte aus `pflicht_je_typ` (extractors/datenmodell.yaml) in der Leiste
# stehen: Typ → {YAML-Name → (Art, Titelanfänge, Meldung)}. Die YAML-Namen
# stehen hier wörtlich. Ein Pflichtwert ohne Eintrag ist ein lauter Fehler
# (DatenmodellFehler), nie ein still ausgelassener Befund (261025-jm6, A1).
#
# Arten:
#   wert               ein Wert dieses Dokuments, Bezeichnung beginnt mit Titel, mit Betrag
#   angabe             eine Lohnangabe dieses Dokuments mit Titel und Wert
#   feld               je Block ein gefülltes Feld mit einem der Titelanfänge
#   feld_ohne_warnung  dasselbe, zusätzlich ohne Warnung
#   zugehoerigkeit     je Block ein Feld mit einer Person (nicht unbekannt)
#   betrag             irgendein Wert dieses Dokuments mit Betrag
#   person             kein eigener Block ohne Person
#   praemie_oder_kosten  Prämie > 0 (ohne Verbilligung) oder Kosten im Hilfsblatt 320
_ERTRAG = ("feld", ("Ertrag ohne", "Ertrag mit"), "Ertrag (0 ist erlaubt, leer nicht)")
PFLICHT_IN_DER_LEISTE: dict[str, dict[str, tuple[str, tuple[str, ...], str]]] = {
    "lohnausweis": {
        "Nettolohn": ("wert", ("Nettolohn",), "Nettolohn"),
        "Arbeitgeber": ("angabe", ("Arbeitgeber",), "Arbeitgeber"),
        "Anstellung von": ("angabe", ("Anstellung von",), "Anstellung von"),
        "Anstellung bis": ("angabe", ("Anstellung bis",), "Anstellung bis"),
    },
    "bank_zinsausweis": {
        "Bank": ("feld", ("Bank",), "Bank"),
        "Konto-/Depot-Nr.": ("feld", ("Konto-/Depot-Nr.",), "Konto-/Depot-Nr."),
        "Total Guthaben": ("feld", ("Total Guthaben",), "Total Guthaben"),
        "Ertrag": _ERTRAG,
    },
    "wertschriftenverzeichnis": {
        "Bank": ("feld", ("Bank",), "Bank"),
        "Konto-/Depot-Nr.": ("feld", ("Konto-/Depot-Nr.",), "Konto-/Depot-Nr."),
        "Total Steuerwert": ("feld", ("Total Steuerwert",), "Total Steuerwert"),
        "Ertrag": _ERTRAG,
        "Zugehörigkeit": ("zugehoerigkeit", ("Zugehörigkeit",), "Person"),
    },
    "hypothek_zinsbestaetigung": {
        "Gläubiger mit Adresse": ("feld_ohne_warnung", ("Name, Vorname und Adresse",),
                                  "Gläubiger-Adresse"),
        "Zinssatz": ("feld", ("Zinssatz",), "Zinssatz"),
        "Schuld am 31.12.": ("feld", ("Schuld am",), "Schuld am 31.12."),
        "Schuldzinsen": ("feld", ("Schuldzinsen",), "Schuldzinsen"),
    },
    "kk_praemienbescheinigung": {
        "Prämie oder Kosten": ("praemie_oder_kosten", (), "liefert weder Prämie noch Kosten"),
    },
    "saeule_3a": {
        "Einzahlung": ("betrag", (), "Einzahlung"),
        "Person": ("person", (), "Person"),
    },
    "kinderbetreuung": {
        "Betrag je Kind": ("betrag", (), "Betrag je Kind"),
        "Person": ("person", (), "Person"),
    },
}

# Welche Blöcke eines Dokuments die Felder (Arten feld …) tragen.
_FELDBLOECKE = {
    "bank_zinsausweis": lambda form, b: form == "Wertschriftenverzeichnis (400)",
    # Nur der Depot-Block; ein zugeordnetes Kontokorrent-Konto desselben
    # Dokuments ist ein Konto, kein Depot.
    "wertschriftenverzeichnis": lambda form, b: (form == "Wertschriftenverzeichnis (400)"
                                                 and b.get("maske") != "Bankkonto"),
    "hypothek_zinsbestaetigung": lambda form, b: form == "Schuldenverzeichnis",
}
_ARTEN = ("wert", "angabe", "feld", "feld_ohne_warnung", "zugehoerigkeit", "betrag",
          "person", "praemie_oder_kosten")


def _pflicht_zuordnung_pruefen() -> dict[str, list[str]]:
    """Jeder Pflichtwert des Datenmodells hat eine Zuordnung — sonst laut."""
    from extractors.datenmodell import DatenmodellFehler, pflicht_je_typ

    pflicht = pflicht_je_typ()
    for typ, namen in pflicht.items():
        zuordnung = PFLICHT_IN_DER_LEISTE.get(typ)
        if zuordnung is None:
            raise DatenmodellFehler(
                f"pflicht_je_typ.{typ} ({', '.join(namen)}): Dokumenttyp ohne Zuordnung in "
                "PFLICHT_IN_DER_LEISTE (scripts/pflichtfelder_check.py) — "
                "seine Pflichtwerte würden still nicht geprüft")
        for name in namen:
            if name not in zuordnung:
                raise DatenmodellFehler(
                    f"pflicht_je_typ.{typ}: Pflichtwert „{name}“ ohne Zuordnung in "
                    "PFLICHT_IN_DER_LEISTE (scripts/pflichtfelder_check.py) — "
                    "er würde still nicht geprüft")
            if zuordnung[name][0] not in _ARTEN:
                raise DatenmodellFehler(
                    f"pflicht_je_typ.{typ}: Pflichtwert „{name}“ mit unbekannter Art "
                    f"{zuordnung[name][0]}")
    return pflicht


def _pflicht_fehlt(typ: str, eigene: list[tuple[str, dict]], dok: str) -> list[str]:
    """Meldungen der Pflichtwerte aus dem Datenmodell, die in der Leiste fehlen.

    In der Reihenfolge des YAML-Blocks. ``eigene``: (Formular, Block) des
    Dokuments.
    """
    from extractors.eintraege import betrag, dok_name

    pflicht = _pflicht_zuordnung_pruefen()
    zuordnung = PFLICHT_IN_DER_LEISTE[typ]
    d = dok_name(dok)
    werte = [(form, w) for form, b in eigene for w in b.get("werte") or []
             if dok_name(w.get("quelle")) == d]
    angaben = [v for _, b in eigene for a in b.get("angaben") or []
               if dok_name(a.get("quelle")) == d for v in a.get("werte") or []]
    feldbloecke = [b for form, b in eigene
                   if _FELDBLOECKE.get(typ, lambda f, x: False)(form, b)]

    fehlt: list[str] = []
    for name in pflicht[typ]:
        art, titel, meldung = zuordnung[name]
        if art in ("feld", "feld_ohne_warnung", "zugehoerigkeit"):
            for b in feldbloecke:
                fs = [x for x in b.get("felder") or []
                      if str(x.get("titel", "")).startswith(titel)]
                if art == "feld":
                    da = any(_gefuellt(x) for x in fs)
                elif art == "feld_ohne_warnung":
                    da = any(_gefuellt(x) and not x.get("warnung") for x in fs)
                else:
                    da = any(str(x.get("wert") or "") not in UNBEKANNT for x in fs)
                if not da:
                    fehlt.append(meldung)
            continue
        if art == "wert":
            da = any(str(w.get("bezeichnung", "")).startswith(titel) and w.get("betrag")
                     for _, w in werte)
        elif art == "angabe":
            da = any(str(a.get("titel", "")).startswith(titel) and a.get("wert")
                     for a in angaben)
        elif art == "betrag":
            da = any(w.get("betrag") for _, w in werte)
        elif art == "person":
            da = not any(b.get("person", "") in UNBEKANNT for _, b in eigene)
        else:   # praemie_oder_kosten — dieselbe Rechnung wie im KK-Abschnitt
            praemie = sum((betrag(w.get("betrag")) or Decimal(0)) for form, w in werte
                          if form == "Versicherungsprämien"
                          and "verbilligung" not in str(w.get("bezeichnung", "")).lower())
            kosten = any(w.get("betrag") for form, w in werte if form == "Hilfsblatt 320")
            da = praemie > 0 or kosten
        if not da:
            fehlt.append(meldung)
    return fehlt


def _feld(block: dict, anfang: str) -> dict:
    return next((f for f in block.get("felder") or []
                 if str(f.get("titel", "")).startswith(anfang)), {})


def _gefuellt(f: dict) -> bool:
    return bool(str(f.get("wert") or "").strip())


def _ordner_je_dokument(samples: Path) -> dict[str, str]:
    """Dokument → Unterordner, bei jedem Aufruf frisch aus den Word-JSONs.

    Kein prozessweiter Cache mehr (261025-jm6, A4c): der Server prüft
    nacheinander, Dokumente werden neu eingelesen — ein Cache sah den alten
    Stand oder den Ordner eines anderen Laufs.
    """
    from extractors.eintraege import dok_name

    ordner: dict[str, str] = {}
    for pfad in samples.glob("*.json"):
        if pfad.name.startswith("_"):
            continue
        try:
            d = json.loads(pfad.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(d, dict):
            ordner[dok_name(d.get("pdf_name"))] = str(d.get("ordner") or "")
    return ordner


def _ordner_von(samples: Path, dok: str) -> str:
    """Dünne Hülle für Einzelabfragen — ohne Cache."""
    from extractors.eintraege import dok_name
    return _ordner_je_dokument(samples).get(dok_name(dok), "")


def pruefe_leiste(samples: Path, daten: dict | None = None) -> list[dict]:
    """Je Dokument: steht alles in der Leiste, was sein Typ verlangt?

    Die Pflichtwerte je Typ kommen aus dem Datenmodell (``pflicht_je_typ``);
    fehlt einem eine Zuordnung, ist das ein DatenmodellFehler — geprüft
    wird das zuerst, für alle Typen (261025-jm6, A1). ``daten``: die schon
    gerechnete Leiste; sonst wird sie hier gerechnet.
    """
    pruefe_leiste.hinweise = []
    from extractors import bankobjekte
    from extractors.eintraege import betrag

    pflicht = _pflicht_zuordnung_pruefen()
    if daten is None:
        daten = _leiste(samples)
    bloecke = [(f["formular"], b) for f in daten["formulare"] for b in f["bloecke"]]

    from extractors.eintraege import dok_name

    def bloecke_von(dok: str):
        dok = dok_name(dok)
        return [(form, b) for form, b in bloecke
                if dok in {dok_name(d) for d in b.get("dokumente") or []}
                or any(dok_name(w.get("quelle")) == dok for w in b.get("werte") or [])
                or any(dok_name(a.get("quelle")) == dok for a in b.get("angaben") or [])]

    eintraege = json.loads((samples / "_results_full.json").read_text(encoding="utf-8"))
    from extractors.eintraege import dok_name as _n
    typ_von = {_n(e.get("pdf_name")): e.get("belegtyp") or "unknown" for e in eintraege}
    # Einmal je Aufruf lesen, nicht je Dokument (261025-jm6, A4c).
    ordner_je = _ordner_je_dokument(samples)

    # Konten und Hypotheken je Dokument aus der Kette — sie zählt, wie viele
    # Objekte ein Dokument hat und welche Werte sie tragen.
    from extractors import rohtabelle
    dokumente = []
    for pfad in sorted(samples.glob("*.json")):
        if pfad.name.startswith("_") or pfad.name.endswith(".truth.json"):
            continue
        try:
            d = json.loads(pfad.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(d, dict) and "pages" in d and typ_von.get(
                _n(d.get("pdf_name"))) in KETTE:
            dokumente.append((_n(d.get("pdf_name")), rohtabelle.baue(d)))
    objekte_von: dict[str, list] = {}
    for o in bankobjekte.objekte(dokumente):
        for dok in dict.fromkeys(q[0] for q in o.quellen):
            objekte_von.setdefault(dok, []).append(o)

    befunde = []
    for dok, typ in sorted(typ_von.items()):
        fehlt: list[str] = []
        eigene = bloecke_von(dok)
        from extractors.belegordner import erlaubte_typen
        # nicht im Datenmodell als Pflicht: Ordnertyp (ordner_typen ja, aber
        # als Zuordnung, nicht als Pflichtwert)
        ordner = ordner_je.get(dok_name(dok), "")
        erlaubt = erlaubte_typen(ordner)
        if erlaubt and typ not in erlaubt:
            fehlt.append(f"Typ {typ} passt nicht zum Ordner {ordner}")
        alv = [w for form, b in eigene if form.startswith("Erwerbsausfall")
               for w in b.get("werte") or [] if w.get("ziffer") == "3.3"]
        als_lohn = [w for form, b in eigene if form == "Lohnausweis (100)"
                    for w in b.get("werte") or []
                    if dok_name(w.get("quelle")) == dok_name(dok)]
        if typ == "unknown" and als_lohn:
            # In der Durchsicht als Lohnausweis erfasst (ALV, der Anwender: „ALV ist
            # ein Lohnausweis") — dann gilt die Pflicht des Lohnausweises.
            typ = "lohnausweis (ALV)"
            if not any(w.get("betrag") for w in als_lohn):
                fehlt.append("Nettolohn")
            # Die Bescheinigung nennt keine Anstellungsdauer — kein Fehler,
            # aber sagen (Leiste: „7 von 9 Lohnangaben").
            pruefe_leiste.hinweise = getattr(pruefe_leiste, "hinweise", []) + [
                "ALV-Lohnausweis: Anstellung von/bis steht nicht im Dokument — "
                "falls die Maske danach fragt, von Hand eintragen"]
            fehlt += [w["hinweis"] for w in als_lohn if w.get("warnung") and w.get("hinweis")]
        elif typ == "unknown" and alv:
            typ = "alv_bescheinigung"
            if not alv[0].get("betrag"):
                fehlt.append("steuerbarer Betrag")
            if not alv[0].get("feld"):
                fehlt.append("Person")
            if alv[0].get("hinweis"):
                fehlt.append(alv[0]["hinweis"])
        elif typ == "unknown":
            fehlt.append("Dokumenttyp nicht erkannt — von Hand")
        elif not eigene:
            fehlt.append("kommt in der Leiste nicht vor")
        else:
            if typ in pflicht:
                fehlt += _pflicht_fehlt(typ, eigene, dok)
            # Ab hier Prüfungen über das Datenmodell hinaus — nicht im
            # Datenmodell (Vorschläge an der Anwender in der SUMMARY 261025-jm6).
            if typ == "lohnausweis":
                # nicht im Datenmodell: Person beim Lohnausweis
                if any(b.get("person", "") in UNBEKANNT for _, b in eigene):
                    fehlt.append("Person")
            elif typ == "bank_zinsausweis":
                # nicht im Datenmodell: Konten-Anzahl und Saldo je Seite
                konten = [o for o in objekte_von.get(dok, []) if o.art == "Konto"
                          and not (bankobjekte.einwert(o.werte.get("saldo"))[0] is None
                                   and not bankobjekte.einwert(o.werte.get("zins"))[0])]
                # „Pro IBAN ein Konto" (der Anwender): mehrere Konten in einem Dokument
                # sind richtig, wenn jedes eine eigene gültige IBAN hat.
                ibans = {o.kennung.wert for o in konten
                         if o.kennung and o.kennung.herkunft != "annahme"}
                if ordner and len(konten) > 1 and len(ibans) < len(konten):
                    fehlt.append(f"{len(konten)} Konten in einem Kontodokument — "
                                 "laut Ordner gehört eines hinein")
                leiste = [b for form, b in eigene if b.get("maske") == "Bankkonto"]
                if len(leiste) != len(konten):
                    fehlt.append(f"{len(konten)} Konto/Konten im Beleg, "
                                 f"{len(leiste)} in der Leiste")
                # Diagnose ohne Beträge: welche Seite, was wurde gelesen?
                for o in konten:
                    if bankobjekte.einwert(o.werte.get("saldo"))[0] is None:
                        seiten = ",".join(str(sn) for d_, sn in o.quellen if d_ == dok)
                        zins = bankobjekte.einwert(o.werte.get("zins"))[0]
                        fehlt.append(f"Seite {seiten}: kein Saldo gelesen "
                                     f"(Zins {'gelesen' if zins is not None else 'keiner'}, "
                                     f"IBAN {'ja' if o.kennung else 'nein'})")
                saldi = {betrag(_feld(b, "Total Guthaben").get("wert")) for b in leiste}
                for o in konten:
                    s_ = bankobjekte.einwert(o.werte.get("saldo"))[0]
                    if s_ is not None and s_ not in saldi:
                        fehlt.append(f"Saldo {s_} aus dem Beleg fehlt in der Leiste")
            elif typ == "hypothek_zinsbestaetigung":
                # nicht im Datenmodell: Hypothek-Paare (Schuld und Zins zusammen)
                hyp = [o for o in objekte_von.get(dok, []) if o.art == "Hypothek"]
                leiste = [b for form, b in eigene if form == "Schuldenverzeichnis"]
                if len(leiste) != len(hyp):
                    fehlt.append(f"{len(hyp)} Hypothek(en) im Beleg, "
                                 f"{len(leiste)} in der Leiste")
                paare = {(betrag(_feld(b, "Schuld am").get("wert")),
                          betrag(_feld(b, "Schuldzinsen").get("wert"))) for b in leiste}
                for o in hyp:
                    paar = (bankobjekte.einwert(o.werte.get("schuld"))[0],
                            bankobjekte.einwert(o.werte.get("zins"))[0])
                    if paar not in paare:
                        fehlt.append(f"Schuld/Zins {paar[0]}/{paar[1]} "
                                     "nicht als Paar in der Leiste")
        for form, b in eigene:
            # nicht im Datenmodell: Position ohne Formular
            if form == "Übrige":
                for w in b.get("werte") or []:
                    if dok_name(w.get("quelle")) == dok_name(dok):
                        fehlt.append(f"Position ohne Formular: "
                                     f"„{w.get('bezeichnung')}\" aus der Durchsicht")
        befunde.append({"dokument": dok, "typ": typ, "fehlt": fehlt})

    # Prämienverbilligung: nennt der Beleg eine, muss sie in der Leiste stehen.
    from extractors import praemienverbilligung
    from extractors.eintraege import tabellen_fuer
    for dok, tab in tabellen_fuer(samples, ("kk_praemienbescheinigung",
                                            "krankheitskosten")).items():
        v = praemienverbilligung.lese(tab)
        if v is None:
            continue
        da = [w for _, b in bloecke for w in b.get("werte") or []
              if w.get("quelle") == dok
              and "verbilligung" in str(w.get("bezeichnung")).lower()]
        if not da or betrag(da[0].get("betrag")) != v.betrag:
            befunde.append({"dokument": dok, "typ": "kk_praemienverbilligung",
                            "fehlt": [f"Prämienverbilligung {v.betrag}"]})
        elif da[0].get("hinweis"):
            befunde.append({"dokument": dok, "typ": "kk_praemienverbilligung",
                            "fehlt": [da[0]["hinweis"]]})

    # Krankenkasse: vier Personen, je eine Rolle, realistische Grundprämie
    # (der Anwender: „wir sind 4 Leute und das geht irgendwie nicht auf").
    from scripts.build_tax_output import resolve_role_constrained
    from scripts.export_extension import dokument_fehlt
    from extractors.datenmodell import kvg_fast_leer, rollen as _rollen
    rollen = _rollen()
    je_rolle = {r: {"kvg": Decimal(0), "vvg": Decimal(0), "verbilligung": Decimal(0),
                    "dokumente": set()} for r in rollen}
    for form, b in bloecke:
        if form != "Versicherungsprämien":
            continue
        p = b.get("person", "")
        for w in b.get("werte") or []:
            dok = w.get("quelle", "")
            wert = betrag(w.get("betrag")) or Decimal(0)
            name = dok.rsplit(" - ", 1)[-1].rsplit(".", 1)[0]
            name = re.sub(r"\s*(19|20)\d{2}(-\d+)?$", "", name)
            aus_name = resolve_role_constrained(name, "kk_praemienbescheinigung")
            if p not in rollen:
                befunde.append({"dokument": dok, "typ": "kk_person",
                                "fehlt": [f"Prämie ohne Person ({_person_marke(p)})"]})
                continue
            if aus_name in rollen and aus_name != p:
                befunde.append({"dokument": dok, "typ": "kk_person",
                                "fehlt": [f"Dateiname sagt {aus_name}, Leiste sagt {p}"]})
            ziel = je_rolle[p]
            bez = str(w.get("bezeichnung", ""))
            if dokument_fehlt(w):
                # Nur aus der Durchsicht, Dokument fehlt im Ordner: zählt
                # nicht als vorhanden (sonst übersieht die Prüfung es). Ein
                # „behalten" zählt als vorhanden — dieselbe Regel wie die
                # Stand-Seite (261025-jm6, A4a).
                ziel.setdefault("nur_durchsicht", []).append(bez)
                continue
            ziel["dokumente"].add(dok)
            if "verbilligung" in bez.lower():
                ziel["verbilligung"] += wert
            elif "VVG" in bez or "Zusatz" in bez:
                ziel["vvg"] += wert
            else:
                ziel["kvg"] += wert      # KVG oder nicht aufgeteiltes Total
    for r, z in je_rolle.items():
        # Nur fast leere Prämien melden: Modell, Franchise und Verbilligung
        # drücken die Grundprämie stark (der Anwender: „die Werte sind alle da").
        minimum = Decimal(kvg_fast_leer(r))
        if not z["dokumente"]:
            befunde.append({"dokument": f"Krankenkasse {r}", "typ": "kk_person",
                            "fehlt": ["keine Prämie in irgendeinem Dokument"]})
        elif z.get("nur_durchsicht"):
            befunde.append({"dokument": f"Krankenkasse {r}", "typ": "kk_person",
                            "fehlt": [f"Dokument fehlt im Ordner — nur aus der Durchsicht: "
                                      f"{', '.join(z['nur_durchsicht'])}"]})
        elif z["kvg"] < minimum:
            befunde.append({"dokument": f"Krankenkasse {r}", "typ": "kk_person",
                            "fehlt": [f"Grundversicherung {z['kvg']} unter {minimum} im Jahr "
                                      "— fast leer, bitte prüfen"]})
    # Dokumentmodell (der Anwender, 261024-jga): Erwachsene je zwei Dokumente von
    # zwei Versicherern, Kinder je eines mit KVG und VVG. Jedes Dokument
    # liefert eine Prämie — sonst ist ein Wert verloren gegangen.
    kk_typen = ("kk_praemienbescheinigung", "krankheitskosten")
    from extractors.datenmodell import kk_dokumente_je_rolle
    erwartet = kk_dokumente_je_rolle()
    dok_rolle: dict[str, str] = {}
    for dok, typ in typ_von.items():
        if typ not in kk_typen:
            continue
        stamm = dok.rsplit(".", 1)[0].replace("_-_", " - ").replace("_", " ")
        name = re.sub(r"\s*(19|20)\d{2}(-\d+)?$", "", stamm.rsplit(" - ", 1)[-1])
        dok_rolle[dok] = resolve_role_constrained(name, "kk_praemienbescheinigung")
    def praemie_von(d: str) -> Decimal:
        return sum((betrag(w.get("betrag")) or Decimal(0))
                   for form, b in bloecke if form == "Versicherungsprämien"
                   for w in b.get("werte") or []
                   if w.get("quelle") == d
                   and "verbilligung" not in str(w.get("bezeichnung", "")).lower())

    for r, n in erwartet.items():
        doks = [d for d, x in dok_rolle.items() if x == r]
        # Gezählt werden Prämiendokumente — eine reine Kostenübersicht
        # derselben Kasse kommt dazu, ohne die Regel zu verletzen.
        mit_praemie = [d for d in doks if praemie_von(d) > 0]
        if len(mit_praemie) != n:
            befunde.append({"dokument": f"Krankenkasse {r}", "typ": "kk_person",
                            "fehlt": [f"{len(mit_praemie)} Prämiendokument(e), erwartet {n}"]})
        for d in doks:
            praemie = sum((betrag(w.get("betrag")) or Decimal(0))
                          for form, b in bloecke if form == "Versicherungsprämien"
                          for w in b.get("werte") or []
                          if w.get("quelle") == d
                          and "verbilligung" not in str(w.get("bezeichnung", "")).lower())
            kosten = any(dok_name(w.get("quelle")) == dok_name(d) and w.get("betrag")
                         for form, b in bloecke if form == "Hilfsblatt 320"
                         for w in b.get("werte") or [])
            # Prämienbescheinigungen deckt die Pflicht „Prämie oder Kosten"
            # aus dem Datenmodell ab — auch ohne aufgelöste Rolle (261025-jm6).
            if (praemie <= 0 and not kosten
                    and typ_von.get(d) != "kk_praemienbescheinigung"):
                befunde.append({"dokument": d, "typ": "kk_person",
                                "fehlt": [f"liefert weder Prämie noch Kosten ({r})"]})
        if n == 1 and doks:
            # nicht im Datenmodell: Kinderdokument mit KVG und VVG
            z = je_rolle[r]
            if z["kvg"] <= 0 or z["vvg"] <= 0:
                befunde.append({"dokument": doks[0], "typ": "kk_person",
                                "fehlt": ["Kinderdokument: KVG und VVG erwartet"]})
    pruefe_leiste.kk = je_rolle
    return befunde


def bilanz(samples: Path, daten: dict | None = None) -> list[dict]:
    """Je Dokument: jeder Wert der Leiste und wie er abgesichert ist.

    gesichert — fester Leser/Regel mit Anker, keine Warnung
    modell    — nur vom Sprachmodell gelesen (Anker, aber keine Regel)
    warnung   — Gegenprobe geht nicht auf, Widerspruch oder Hinweis
    fehlt     — Pflichtwert leer
    """
    from extractors.eintraege import dok_name as _dn

    if daten is None:
        daten = _leiste(samples)
    aus: dict[str, dict] = {}
    for f in daten["formulare"]:
        for b in f["bloecke"]:
            for x in b.get("felder") or []:
                if x["titel"].startswith(("Staat", "Währung", "Bezeichnung",
                                          "Zugehörigkeit")) or x.get("herkunft") == "leer":
                    continue
                if x["titel"].startswith(("Bank", "Konto-/Depot", "Name, Vorname", "Zinssatz")):
                    continue        # Angaben, keine Beträge — zählen unten nicht
                st = ("fehlt" if not x.get("wert") else
                      "warnung" if x.get("warnung") else
                      "gesichert" if x.get("herkunft") in ("beleg", "ablage", "berechnet")
                      else "modell")
                grund = ""
                if st == "warnung" and x.get("hinweis"):
                    # Grund ohne Beträge — die Ausgabe darf geteilt werden.
                    grund = " (" + re.sub(r"\s+", " ", re.sub(r"[\d'’.,]+", "…",
                                                               x["hinweis"]))[:70] + ")"
                for dok in b.get("dokumente") or []:
                    aus.setdefault(_dn(dok), {}).setdefault(st, []).append(
                        f"{f['formular'][:14]}: {x['titel'][:30]}{grund}")
            for w in b.get("werte") or []:
                if (w.get("gerechnet") and not w.get("einsetzen")) or w.get("kontrolle"):
                    continue        # Kontrollzahl: wird nicht eingetragen
                # Gesichert ist, was der Mensch geprüft hat, oder eine Regel
                # mit Anker. „regel" ohne Anker und ein vom Modell gelesener,
                # nur bestätigter Wert zählen als Modell (261025-hbv, B5).
                st = ("fehlt" if not w.get("betrag") else
                      "warnung" if w.get("warnung") or w.get("hinweis") else
                      "gesichert" if w.get("herkunft") in ("ablage", "mensch")
                      or (w.get("herkunft") == "regel" and w.get("anker"))
                      else "modell")
                aus.setdefault(_dn(w.get("quelle", "")), {}).setdefault(st, []).append(
                    f"{f['formular'][:14]}: {w['bezeichnung'][:30]}")
    return [{"dokument": d, **v} for d, v in sorted(aus.items())]


def _formen_zeigen(samples: Path, zeile: str, maske: "Maske | None" = None) -> None:
    from extractors.eintraege import dok_name, tabellen_fuer

    voll = maske is None or maske.voll
    praefix = zeile.split(": Konto-/Depot-Nr.")[0].lstrip(" ·").strip()
    alle = ("bank_zinsausweis", "wertschriftenverzeichnis", "hypothek_zinsbestaetigung",
            "unknown")
    for dok, tab in tabellen_fuer(samples, alle).items():
        if dok_name(dok) != dok_name(praefix):
            continue
        print(f"    Form der Kandidatenzeilen:")
        for z in tab.zeilen:
            m = re.search(r"(konto|depot|iban|kunde|kd|portfolio|vertrag|nr)", z.text, re.I)
            if not m or z.seite > 2:
                continue
            kopf, rest = z.text[:m.end()], z.text[m.end():]
            form = re.sub(r"\d", "9", re.sub(r"[A-Za-zÀ-ÿ]", "A", rest))[:60]
            # Teilbar: nur das Stichwort, nicht der Text davor — dort steht
            # oft der Bankname (261025-hbv, B1).
            print(f"      S{z.seite}: {kopf[-20:] if voll else m.group(0)}{form}")


def _inventur(samples: Path, maske: "Maske", daten: dict | None = None,
              befunde: list[dict] | None = None, pdf_dir: Path | None = None) -> dict:
    """Anzahl, Typ, Person, Pflichtfelder — die einfachen Fragen zuerst.

    der Anwender (261024-jga): „Anzahl Dokumente, Typ der Dokumente, Personen pro
    Dokument, Felder pro Dokument". Personen als Rolle, Dokumente als Kürzel.

    Rückgabe (261025-hbv, B2): gleich (dieselben Dokumente, nicht nur
    dieselbe Anzahl), eingelesen, extrahiert, ohne_ergebnis, ohne_word.
    Mit ``pdf_dir`` (261025-jm6, A3) auch gegen den echten Ordner:
    nicht_eingelesen (im Ordner, ohne Word-JSON) und nicht_mehr_im_ordner
    (eingelesen oder extrahiert, aber nicht mehr im Ordner).

    Je Dokument stehen ALLE Befunde (Pflichtfelder, Prämienverbilligung,
    Person …) — vorher gewann der letzte (261025-jm6, A5).
    """
    from collections import Counter

    from extractors.eintraege import dok_name

    word = []
    for pfad in samples.glob("*.json"):
        if pfad.name.startswith("_"):
            continue
        try:
            d = json.loads(pfad.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(d, dict) and "pages" in d:
            word.append(dok_name(d.get("pdf_name")))
    ergebnis = json.loads((samples / "_results_full.json").read_text(encoding="utf-8"))
    typ_von = {dok_name(e.get("pdf_name")): e.get("belegtyp") or "unknown" for e in ergebnis}

    if daten is None:
        daten = _leiste(samples)
    # Person je Dokument aus der Leiste (dort ist sie aufgelöst).
    personen: dict[str, set[str]] = {}
    for f in daten["formulare"]:
        for b in f["bloecke"]:
            p = b.get("person") or "ohne Person"
            for d in (b.get("dokumente") or []) + [w.get("quelle") for w in b.get("werte") or []]:
                if d:
                    personen.setdefault(dok_name(d), set()).add(p)
    if befunde is None:
        befunde = pruefe_leiste(samples, daten=daten)
    je_dok: dict[str, list[str]] = {}
    for b in befunde:
        je_dok.setdefault(dok_name(b["dokument"]), []).extend(b.get("fehlt") or [])

    print("INVENTUR")
    ohne_ergebnis = sorted(set(word) - set(typ_von))
    ohne_word = sorted(set(typ_von) - set(word))
    if pdf_dir is not None:
        from extractors.belegordner import alle_pdfs
        ordner = {dok_name(p.name) for p in alle_pdfs(Path(pdf_dir))}
        nicht_eingelesen = sorted(ordner - set(word))
        nicht_mehr_im_ordner = sorted((set(word) | set(typ_von)) - ordner)
        gleich = ordner == set(word) == set(typ_von)
        kopf = f"  Dokumente im Ordner: {len(ordner)}   eingelesen: {len(set(word))}"
    else:
        nicht_eingelesen, nicht_mehr_im_ordner = [], []
        gleich = set(word) == set(typ_von)
        kopf = (f"  Dokumente im Ordner: – (ohne Ordnervergleich)   "
                f"eingelesen: {len(set(word))}")
    print(f"{kopf}   extrahiert: {len(typ_von)}"
          f"   {'✓ gleich' if gleich else '✗ ungleich'}")

    def kurz(doks) -> str:
        return ", ".join(maske.dok(d).split()[0] for d in doks)

    if nicht_eingelesen:
        print(f"  ✗ im Ordner, nicht eingelesen: {len(nicht_eingelesen)}  "
              + kurz(nicht_eingelesen))
    if nicht_mehr_im_ordner:
        print(f"  ✗ eingelesen, nicht mehr im Ordner: {len(nicht_mehr_im_ordner)}  "
              + kurz(nicht_mehr_im_ordner))
    if ohne_ergebnis:
        print(f"  ✗ ohne Extraktionsergebnis: {len(ohne_ergebnis)}  " + kurz(ohne_ergebnis))
    if ohne_word:
        print(f"  ✗ Ergebnis ohne Word-JSON: {len(ohne_word)}  " + kurz(ohne_word))
    print("  Je Typ:   " + ", ".join(f"{t} {n}" for t, n in Counter(typ_von.values()).most_common()))
    je_person = Counter(maske.person(p) for d in typ_von for p in personen.get(d, {"ohne Person"}))
    print("  Je Person: " + ", ".join(f"{p} {n}" for p, n in sorted(je_person.items())))
    print("  Je Dokument (Typ · Person · Pflichtfelder):")
    for d in sorted(typ_von):
        fehlt = list(dict.fromkeys(je_dok.get(d, [])))
        pflicht = "✓" if not fehlt else "✗ " + maske.text("; ".join(fehlt))[:80]
        wer = ", ".join(sorted({maske.person(p) for p in personen.get(d, {"–"})}))
        print(f"    {maske.dok(d):40} {typ_von[d][:24]:24} {wer[:24]:24} {pflicht}")
    print()
    return {"gleich": gleich, "eingelesen": len(set(word)), "extrahiert": len(typ_von),
            "ohne_ergebnis": ohne_ergebnis, "ohne_word": ohne_word,
            "nicht_eingelesen": nicht_eingelesen,
            "nicht_mehr_im_ordner": nicht_mehr_im_ordner}


def _form(text) -> str:
    """„12'345.60" → „99'999.99" — Form ohne Wert."""
    return re.sub(r"\d", "9", str(text or ""))


def _diagnose(samples: Path, dok, daten: dict | None = None) -> None:
    """Form statt Wert: je Dokument jeder Wert der Leiste — Form, Herkunft,
    gleicher Betrag (ab 100) in einem anderen Dokument? IBAN gültig? Keine
    Beträge, keine Namen (Vorgabe der Anwender 2026-10-25: mehr sehen, nicht alles bei
    Anthropic)."""
    from extractors.eintraege import betrag, dok_name

    if daten is None:
        daten = _leiste(samples)
    zeilen = []          # (dok, titel, form, herkunft, wert)
    for f in daten["formulare"]:
        for b in f["bloecke"]:
            for x in b.get("felder") or []:
                titel = str(x.get("titel", "")).split(" (")[0]
                if not x.get("wert") or titel.startswith(("Staat", "Währung")):
                    continue
                angabe = titel.startswith(("Bank", "Name, Vorname", "Zugehörigkeit",
                                           "Bezeichnung", "Konto-/Depot", "Zinssatz"))
                if titel.startswith("Konto-/Depot"):
                    form = ("IBAN gültig" if x.get("herkunft") == "beleg"
                            and str(x["wert"])[:2].isalpha() else _form(x["wert"])[:24])
                elif angabe:
                    form = "vorhanden"
                else:
                    form = _form(x["wert"])
                for d in b.get("dokumente") or []:
                    zeilen.append((dok_name(d), titel, form, x.get("herkunft", ""),
                                   None if angabe else betrag(x["wert"])))
            for w in b.get("werte") or []:
                zeilen.append((dok_name(w.get("quelle")), w.get("bezeichnung", ""),
                               _form(w.get("betrag")), w.get("herkunft", ""),
                               betrag(w.get("betrag"))))
    wo: dict = {}
    for d, _, _, _, wert in zeilen:
        if wert is not None and abs(wert) >= 100:
            wo.setdefault(wert, set()).add(d)
    print("DIAGNOSE (Form statt Wert)")
    aktuell = None
    for d, titel, form, herkunft, wert in sorted(zeilen, key=lambda z: z[:4]):
        if d != aktuell:
            print(f"  {dok(d)}")
            aktuell = d
        andere = sorted(dok(x).split()[0] for x in wo.get(wert, set()) if x != d) \
            if wert is not None else []
        zusatz = f"  gleicher Betrag wie {', '.join(andere)}" if andere else ""
        print(f"      {titel[:34]:34} {form[:16]:16} {herkunft or '–':10}{zusatz}")
    print()


def plausibilitaet(samples: Path, daten: dict | None = None) -> list[tuple[str, str]]:
    """Können die Werte so stimmen? (Dokument, Befund) — auf den Werten der
    Leiste, also inklusive Durchsicht; Lohn aus der Extraktion (Brutto steht
    nicht in der Leiste). Grenzen aus Wegleitung und extractors/datenmodell.yaml."""
    from extractors.datenmodell import kita_maximum_je_kind, saeule_3a_maximum
    from extractors.eintraege import betrag, dok_name

    # Aus dem Datenmodell, ohne stille Vorgabewerte (261025-jm6, A1).
    max_3a = saeule_3a_maximum()
    max_kita = kita_maximum_je_kind()
    if daten is None:
        daten = _leiste(samples)
    aus: list[tuple[str, str]] = []

    def feld(b, anfang):
        return betrag(next((x.get("wert") for x in b.get("felder") or []
                            if str(x.get("titel", "")).startswith(anfang)), None))

    for f in daten["formulare"]:
        for b in f["bloecke"]:
            doks = b.get("dokumente") or []
            d = dok_name(doks[0]) if doks else ""
            maske = b.get("maske")
            if maske == "Bankkonto":
                saldo = feld(b, "Total Guthaben")
                ertrag = (feld(b, "Ertrag ohne") or 0) + (feld(b, "Ertrag mit") or 0)
                if saldo and saldo > 0 and ertrag / saldo > ZINS_HOECHSTENS_ANTEIL_SALDO:
                    aus.append((d, "Konto: Zins über 5 % des Saldos"))
                if saldo is not None and saldo < 0 and ertrag > 0:
                    aus.append((d, "Konto: Zinsertrag bei negativem Saldo"))
            elif maske == "Steuerauszug/Depot":
                sw = feld(b, "Total Steuerwert")
                ertrag = (feld(b, "Ertrag ohne") or 0) + (feld(b, "Ertrag mit") or 0)
                if sw is not None and sw < 0:
                    aus.append((d, "Depot: negativer Steuerwert"))
                if sw and sw > 0 and ertrag / sw > DEPOT_ERTRAG_HOECHSTENS_ANTEIL:
                    aus.append((d, "Depot: Ertrag über 15 % des Steuerwerts"))
            elif maske == "Schuld":
                satz = next((x.get("wert") for x in b.get("felder") or []
                             if str(x.get("titel", "")).startswith("Zinssatz")), "")
                for teil in str(satz or "").replace("%", "").split("/"):
                    w = betrag(teil.strip())
                    if w is not None and not (ZINSSATZ_MINDESTENS <= w <= ZINSSATZ_HOECHSTENS):
                        aus.append((d, "Schuld: Zinssatz ausserhalb 0.1–6 %"))
            for w in b.get("werte") or []:
                wert = betrag(w.get("betrag"))
                quelle = dok_name(w.get("quelle"))
                if wert is None:
                    continue
                if wert < 0 and "verbilligung" not in str(w.get("bezeichnung", "")).lower():
                    aus.append((quelle, f"{w.get('bezeichnung', '')[:30]}: negativ"))
                if f["formular"].startswith("Bescheinigung der Vorsorge") and wert > max_3a:
                    aus.append((quelle, "Säule 3a über dem Maximum"))
                if f["formular"].startswith("Aufstellung fremdbetreute") and wert > max_kita:
                    aus.append((quelle, "Kinderbetreuung über dem Maximum je Kind"))
                if f["formular"] == "Versicherungsprämien" and wert > PRAEMIE_HOECHSTENS:
                    aus.append((quelle, "Prämie über 12'000 im Jahr"))
    for e in pruefe_extraktion(samples):
        if e["typ"] == "lohnausweis":
            for u in e["unrealistisch"]:
                aus.append((dok_name(e["dokument"]), f"Lohn: {u}"))
    return aus


def _word_namen(samples: Path) -> list[str]:
    """pdf_name aller eingelesenen Dokumente (Word-JSON mit „pages")."""
    from extractors.eintraege import dok_name

    aus = []
    for pfad in samples.glob("*.json"):
        if pfad.name.startswith("_") or pfad.name.endswith(".truth.json"):
            continue
        try:
            d = json.loads(pfad.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(d, dict) and "pages" in d:
            aus.append(dok_name(d.get("pdf_name")))
    return aus


def _kuerzel(samples: Path, pdf_dir: Path | None = None) -> dict[str, str]:
    """Dokument → „D07 Konten · bank_zinsausweis" — ohne Bank, ohne Person.

    Auch eingelesene Dokumente ohne Extraktionsergebnis bekommen ein Kürzel
    (Typ „ohne Ergebnis") — sonst stünde dort „nicht im Ordner" (B2). Mit
    ``pdf_dir`` auch die PDFs des Ordners ohne Word-JSON (Typ „nicht
    eingelesen", 261025-jm6, A3).
    """
    from extractors.eintraege import dok_name

    try:
        eintraege = json.loads((samples / "_results_full.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        eintraege = []
    typ = {dok_name(e.get("pdf_name")): e.get("belegtyp") or "unknown" for e in eintraege}
    for n in _word_namen(samples):
        typ.setdefault(n, "ohne Ergebnis")
    im_ordner: dict[str, str] = {}
    if pdf_dir is not None and Path(pdf_dir).is_dir():
        from extractors.belegordner import alle_pdfs, ordner_von
        for p in alle_pdfs(Path(pdf_dir)):
            n = dok_name(p.name)
            im_ordner.setdefault(n, ordner_von(p, Path(pdf_dir)))
            typ.setdefault(n, "nicht eingelesen")
    namen = sorted(typ)
    ordner_je = _ordner_je_dokument(samples)
    return {n: f"D{i:02} {ordner_je.get(n) or im_ordner.get(n) or '–'} · {typ[n]}"
            for i, n in enumerate(namen, start=1)}


class Maske:
    """Eine Maskierung für jede Zeile der teilbaren Fassung (261025-hbv, B1).

    Vorher maskierte main() mit zwei inneren Funktionen, die nicht jeden
    Abschnitt erreichten: Inventur, Formen und „Dokumente, die fehlen"
    druckten Pflichtfeld-Texte, Kopftexte und Blocktitel roh — mit Bank-
    namen und Beträgen. Jetzt läuft jede Ausgabe hier durch.

    ``kuerzel``: Dokument → „D07 Konten · …"; ``namen``: Aussteller-,
    Bank- und Gläubigernamen der Leiste; ``voll``: nichts maskieren.
    """

    def __init__(self, kuerzel: dict, namen: set, voll: bool):
        self.kuerzel = kuerzel
        self.voll = voll
        self.namen = sorted({n for n in (str(x).strip() for x in namen) if len(n) >= 3},
                            key=lambda n: -len(n))
        self.fremde: dict[str, str] = {}

    def dok(self, name) -> str:
        from extractors.eintraege import dok_name

        if self.voll:
            return str(name)[:55]
        n = dok_name(name)
        if n in self.kuerzel:
            return self.kuerzel[n]
        # Dokumente ausserhalb des Laufs (nicht mehr im Ordner): X1, X2 …
        self.fremde.setdefault(n, f"X{len(self.fremde) + 1} nicht im Ordner")
        return self.fremde[n]

    def person(self, p) -> str:
        """Rolle oder fester Marker; Freitext als „‹Person›" (261025-jm6, A6)."""
        return str(p) if self.voll else _person_marke(p)

    def text(self, t) -> str:
        """Hinweis/Befund ohne Dokumentnamen, ohne Ausstellernamen, ohne Ziffern."""
        from extractors.eintraege import dok_name

        t = str(t)
        if self.voll:
            return t
        reihe = sorted(self.kuerzel.items(), key=lambda x: -len(x[0]))
        for n, k in reihe:
            t = t.replace(n, k.split()[0])
        t = dok_name(t)
        for n, k in reihe:
            t = t.replace(n, k.split()[0])
        # Dokumente ausserhalb des Laufs haben kein Kürzel — Namen trotzdem weg.
        t = re.sub(r"[^,;()]*\.(?:pdf|PDF)", "Dokument ausserhalb des Laufs", t)
        # Erst nach den Dokumentnamen: sie enthalten oft den Banknamen.
        # Ziffern im Namen (Hausnummer, PLZ) dürfen schon zu „…" geworden
        # sein — manche Gründe kommen vorgekürzt an (bilanz).
        for n in self.namen:
            muster = "".join(r"(?:\d[\d'’.,]*|…)" if teil.isdigit() else re.escape(teil)
                             for teil in re.split(r"(\d+)", n) if teil)
            t = re.sub(muster, "‹Aussteller›", t, flags=re.I)
        return re.sub(r"\s+", " ", re.sub(r"\d[\d'’.,]*", "…", t))


def _namen_der_leiste(daten: dict) -> set[str]:
    """Aussteller-, Bank- und Gläubigernamen aus der Leiste — für die Maske.

    Gläubiger tragen die Adresse im selben Feld („Bank, Strasse 1, 8000 Ort");
    jeder durch Komma getrennte Teil zählt einzeln, so fallen auch Adressteile.
    """
    namen: set[str] = set()
    for f in daten.get("formulare") or []:
        for b in f.get("bloecke") or []:
            werte = [b.get("aussteller")]
            for x in b.get("felder") or []:
                if str(x.get("titel", "")).startswith(("Bank", "Name, Vorname")):
                    werte.append(x.get("wert"))
                    werte += str(x.get("wert") or "").split(",")
            for w in werte:
                w = str(w or "").strip()
                if len(w) >= 3:
                    namen.add(w)
    return namen


def main() -> int:
    from extractors.datenmodell import DatenmodellFehler

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--samples", type=Path, required=True)
    ap.add_argument("--pdf-dir", type=Path, default=None,
                    help="echter PDF-Ordner — „im Ordner“ heisst dann: dort liegt das PDF")
    ap.add_argument("--ohne-ordner", action="store_true",
                    help="ausdrücklich ohne Ordnervergleich (anonymisierter Korpus ohne PDFs)")
    ap.add_argument("--diagnose", action="store_true",
                    help="je Dokument jeder Wert: Form, Herkunft, gleicher Betrag anderswo")
    ap.add_argument("--voll", action="store_true",
                    help="mit Dokumentnamen und Beträgen — nur für dich, NICHT teilen")
    args = ap.parse_args()
    from extractors.entscheide import EntscheideDefekt
    try:
        return _pruefe(args)
    except DatenmodellFehler as e:
        # Laut statt still (261025-jm6, A1): eine Regel ohne Datenmodell ist aus.
        print(f"Datenmodell: {e}")
        return 1
    except EntscheideDefekt as e:
        # Die Meldung nennt nur Dateinamen (261025-jm6, A2b).
        print(f"Entscheide: {e}")
        return 1


def _pruefe(args) -> int:
    if not (args.samples / "_results_full.json").exists():
        # Ohne Extraktionsergebnis ist jeder Befund falsch („alles fehlt").
        print("Kein Extraktionsergebnis gefunden — zuerst `make productive` laufen "
              "lassen. Ohne Ergebnis ist keine Prüfung aussagekräftig.")
        return 2

    # „Im Ordner" heisst der echte Ordner (261025-jm6, A3). Ohne ihn nur mit
    # ausdrücklichem --ohne-ordner (Korpus) — sonst wäre jeder Abgleich mit
    # „dem Ordner" in Wahrheit einer mit dem letzten Lauf.
    pdf_dir = None
    if not args.ohne_ordner:
        if args.pdf_dir is None or not args.pdf_dir.is_dir():
            # Teilbar: ohne den Pfad — er kann den Benutzernamen enthalten.
            print("Kein PDF-Ordner (--pdf-dir) — ohne ihn hiesse „im Ordner“ nur "
                  "„im letzten Lauf“. make pruefen PDFDIR=… oder --ohne-ordner für "
                  "den Korpus."
                  + (f" (gesucht: {args.pdf_dir})" if args.voll and args.pdf_dir else ""))
            return 1
        pdf_dir = args.pdf_dir

    kuerzel = _kuerzel(args.samples, pdf_dir)
    if not args.voll:
        # Welche Nummer zu welchem Dokument gehört, steht nur lokal.
        ziel = args.samples.parent / "pruefen_namen.txt"
        ziel.write_text("\n".join(f"{k.split()[0]}  {n}" for n, k in kuerzel.items()) + "\n",
                        encoding="utf-8")

    # Die Leiste einmal rechnen und überall weiterreichen (261025-jm6, A4b) —
    # vorher rund zehnmal, und jede Rechnung konnte anders ausfallen.
    daten = _leiste(args.samples, pdf_dir=pdf_dir)
    maske = Maske(kuerzel, _namen_der_leiste(daten), args.voll)
    dok, text = maske.dok, maske.text

    print("Teilbare Fassung: Kürzel statt Namen, keine Beträge."
          if not args.voll else "VOLLE Fassung mit Namen und Beträgen — nicht teilen.")
    if not args.voll:
        print(f"Kürzel → Dokument: {args.samples.parent / 'pruefen_namen.txt'} (nur lokal)\n")
    if args.ohne_ordner:
        print("⚠ ohne Ordnervergleich (--ohne-ordner): „im Ordner“ heisst hier "
              "„im letzten Lauf“\n")

    leiste = pruefe_leiste(args.samples, daten=daten)
    inventur = _inventur(args.samples, maske, daten=daten, befunde=leiste, pdf_dir=pdf_dir)
    if inventur["extrahiert"] == 0:
        # Ohne Dokumente ist jeder weitere Abschnitt leer — und leer ist nicht grün.
        print("0 Dokumente extrahiert — keine Prüfung möglich")
        return 1
    if args.diagnose:
        _diagnose(args.samples, dok, daten=daten)

    # Zuerst: fehlen Dokumente? Ein Wert der Leiste, dessen Dokument nicht im
    # Ordner liegt, heisst: das Dokument fehlt (der Anwender: „wenn wir wieder
    # Dokumente suchen, können wir nicht fertig sein").
    # Eine Regel mit der Stand-Seite: verwaist und nicht entschieden
    # (261025-jm6, A4a) — ein „behalten" fehlt nicht.
    from scripts.export_extension import dokument_fehlt
    fehlende: dict[str, list[str]] = {}
    for f in daten["formulare"]:
        for blk in f["bloecke"]:
            if dokument_fehlt(blk):
                for d in blk.get("dokumente") or []:
                    # Teilbar: die Maske des Blocks, nicht sein Titel —
                    # der Titel nennt die Bank (B1).
                    was = blk.get("titel", "") if args.voll else (blk.get("maske") or "Eintrag")
                    fehlende.setdefault(d, []).append(f"{f['formular'][:22]}: {was[:30]}")
            for w in blk.get("werte") or []:
                if dokument_fehlt(w):
                    fehlende.setdefault(w.get("quelle", ""), []).append(
                        f"{f['formular'][:22]}: {w.get('bezeichnung', '')[:30]}")
    print(f"Dokumente, die fehlen (Werte nur aus der Durchsicht): {len(fehlende)}")
    for d, was in fehlende.items():
        print(f"  ✗ {dok(d)}: {text('; '.join(was))}")
    print()

    offen_leiste = [b for b in leiste if b["fehlt"]]
    print(f"Leiste (was du abträgst): {len(leiste)} Dokumente/Personen")
    for b in leiste:
        zeichen = "✓" if not b["fehlt"] else "✗"
        name = (b["dokument"] if str(b["dokument"]).startswith("Krankenkasse")
                else dok(b["dokument"]))
        befund = [text(x) for x in b["fehlt"]]
        print(f"  {zeichen} {b['typ'][:22]:22} {name:55} {befund or ''}")
    print(f"\n{len(offen_leiste)} mit Befund in der Leiste.")
    for h in dict.fromkeys(getattr(pruefe_leiste, "hinweise", [])):
        print(f"  ℹ {text(h)}")
    print()
    kk = getattr(pruefe_leiste, "kk", {})
    if kk:
        print("Krankenkasse je Person (Grundversicherung | Zusatz | Verbilligung | Dokumente):")
        for r, z in kk.items():
            if args.voll:
                print(f"  {r:13} {z['kvg']:>10} | {z['vvg']:>9} | {z['verbilligung']:>8} | "
                      f"{len(z['dokumente'])}")
            else:
                print(f"  {r:13} {'✓' if z['kvg'] > 0 else '–'} | {'✓' if z['vvg'] > 0 else '–'} | "
                      f"{'✓' if z['verbilligung'] > 0 else '–'} | {len(z['dokumente'])}"
                      + (f"  + nur Durchsicht: {text(', '.join(z['nur_durchsicht']))}"
                         if z.get("nur_durchsicht") else ""))
        print()

    angaben = []
    formen = []
    for f in daten["formulare"]:
        for blk in f["bloecke"]:
            for x in blk.get("felder") or []:
                if x.get("warnung") and x["titel"].startswith((
                        "Bank", "Konto-/Depot", "Name, Vorname", "Zinssatz", "Zugehörigkeit")):
                    grund = text(x.get("hinweis", ""))
                    doks = blk.get("dokumente") or []
                    angaben.append(f"  · {', '.join(dok(d) for d in doks)}: "
                                   f"{x['titel'][:24]} ({grund[:70]})")
                    if x["titel"].startswith("Konto-/Depot") and not x.get("wert"):
                        formen += doks
    print(f"Angaben ohne Betrag mit Warnung: {len(angaben)}")
    print("\n".join(angaben))
    # Fehlt eine Kennung, die Form der Kandidatenzeilen zeigen — Ziffern als
    # 9, Buchstaben nach dem Stichwort als A. Keine Nummer, kein Name.
    for d in formen:
        _formen_zeigen(args.samples, f"  · {d}: Konto-/Depot-Nr. (fehlt)", maske)
    print()

    print("Schulden:")
    for f in daten["formulare"]:
        if f["formular"] != "Schuldenverzeichnis":
            continue
        for i, blk in enumerate(f["bloecke"], start=1):
            fs = {x["titel"][:12]: x for x in blk.get("felder") or []}
            schuld, zins = fs.get("Schuld am 31", {}), fs.get("Schuldzinsen", {})
            herkunft = {"beleg": "Dokument", "ablage": "Durchsicht",
                        "widerspruch": "Widerspruch"}.get(schuld.get("herkunft"), "–")
            hinweise = "; ".join(sorted({text(h) for h in (schuld.get("hinweis", ""),
                                                           zins.get("hinweis", "")) if h}))
            print(f"  {i:2}. aus {herkunft:11} Schuld {'✓' if schuld.get('wert') else '–'} "
                  f"Zins {'✓' if zins.get('wert') else '–'}  {len(blk.get('dokumente') or [])} Dok.  "
                  f"{hinweise}")
    print()

    b = bilanz(args.samples, daten=daten)
    zaehl = {k: sum(len(x.get(k, [])) for x in b)
             for k in ("gesichert", "modell", "warnung", "fehlt")}
    gesamt = sum(zaehl.values()) or 1
    print("Absicherung je Wert (Beträge der Leiste):")
    for k, n in zaehl.items():
        print(f"  {k:10} {n:4}  ({n / gesamt * 100:.0f} %)")
    for x in b:
        offen = x.get("modell", []) + x.get("warnung", []) + x.get("fehlt", [])
        if offen:
            print(f"  · {dok(x['dokument']):55} "
                  + "; ".join(f"{k}: {text(', '.join(x[k]))}"
                              for k in ("modell", "warnung", "fehlt") if x.get(k)))
    print()

    plaus = plausibilitaet(args.samples, daten=daten)
    print(f"Plausibilität: {len(plaus)} Befund(e)")
    for d, befund in plaus:
        print(f"  ✗ {dok(d)}: {text(befund)}")
    print()

    kette = pruefe_kette(args.samples)
    print(f"Konten/Hypotheken (Kette): {len(kette)} Objekte")
    for b in kette:
        zeichen = "✓" if not b["fehlt"] and not b["unrealistisch"] else "✗"
        quellen = ", ".join(f"{dok(d).split()[0] if not args.voll else d[:40]} S{s}"
                            for d, s in b["quellen"][:3])
        bez = b["bezeichnung"][:20] if args.voll else ""
        print(f"  {zeichen} {b['art']:8} {bez:20} "
              f"fehlt={[text(u) for u in b['fehlt']]} "
              f"unreal={[text(u) for u in b['unrealistisch']]}  ({quellen})")
    offen = sum(1 for b in kette if b["fehlt"] or b["unrealistisch"])
    print(f"\n{offen} Objekt(e) der Kette mit Befund.")
    # Was nach der ganzen Kette übrig bleibt, entscheidet der Mensch — in der
    # Review, Seite „Zu entscheiden" (make review → „Zu entscheiden ↗").
    from scripts.entscheiden_seite import offene_punkte
    zu, schon, ohne_wert = offene_punkte(args.samples, daten=daten)
    print(f"Zu entscheiden in der Review: {len(zu)} offen, {len(schon)} entschieden.")
    if ohne_wert:
        # Nur die Anzahl — kein Name, kein Betrag; nicht im Exit-Code
        # (261025-jm6, A2d).
        print(f"  ℹ Entscheidungen ohne passenden Wert: {len(ohne_wert)} "
              "(Seite „Zu entscheiden“)")
    # Fail-closed (B2): grün nur, wenn jedes eingelesene Dokument ein Ergebnis
    # hat, Ordner, Word-JSON und Ergebnis dieselben Dokumente nennen
    # (261025-jm6, A3) und nichts mehr zu entscheiden ist.
    return 0 if (offen == 0 and not offen_leiste and not angaben and not fehlende
                 and not plaus and inventur["gleich"] and inventur["extrahiert"] > 0
                 and not inventur["ohne_ergebnis"] and not inventur["ohne_word"]
                 and not inventur["nicht_eingelesen"]
                 and not inventur["nicht_mehr_im_ordner"]
                 and len(zu) == 0) else 1


if __name__ == "__main__":
    raise SystemExit(main())
