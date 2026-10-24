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

Aufruf::

    PYTHONPATH=. .venv/bin/python scripts/pflichtfelder_check.py \\
        --samples evals/samples_real_2025
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
                    and saldo > 0 and zins / saldo > Decimal("0.05")):
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
    """Kann der Wert so stimmen? Grenzen aus der Wegleitung ZH 2025."""
    aus = []
    if typ == "lohnausweis":
        brutto, netto = _zahl(felder.get("bruttolohn_pos8")), _zahl(felder.get("nettolohn_pos11"))
        abzug = (_zahl(felder.get("ahv_alv_nbu_abzug_pos9")) or 0) + \
                (_zahl(felder.get("bvg_abzug_pos10a")) or 0)
        if brutto and netto:
            if netto > brutto:
                aus.append("Netto > Brutto")
            elif abzug and abs(brutto - abzug - netto) > 2:
                aus.append(f"Brutto − Abzüge ≠ Netto (Differenz {brutto - abzug - netto})")
            elif netto < brutto * Decimal("0.6"):
                aus.append("Netto unter 60 % von Brutto")
    elif typ == "saeule_3a":
        e = _zahl(felder.get("einzahlung_betrag"))
        if e is not None and e > Decimal("7258"):
            aus.append(f"Einzahlung über Maximum 7'258 ({e})")
    elif typ == "kinderbetreuung":
        b = _zahl(felder.get("betrag"))
        if b is not None and b > Decimal("25000"):
            aus.append(f"über Maximum 25'000 je Kind ({b})")
    elif typ == "kk_praemienbescheinigung":
        for k in ("praemie_kvg_total", "praemie_vvg_total", "praemie_total"):
            v = _zahl(felder.get(k))
            # 0.00 ist erlaubt: manche Übersicht sagt ausdrücklich „Prämien
            # 0.00", weil ein anderer Versicherer abrechnet. Ob die Person
            # irgendwo eine Prämie hat, prüft praemien_je_person.
            if v is not None and not (Decimal("0") <= v <= Decimal("12000")):
                aus.append(f"{k} ausserhalb 0–12'000 ({v})")
    elif typ == "wertschriftenverzeichnis":
        b = _zahl(felder.get("bestand_3112"))
        e = _zahl(felder.get("bruttoertrag_total"))
        if b is not None and e is not None and b > 0 and e / b > Decimal("0.15"):
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


def _leiste(samples: Path) -> dict:
    """Leistendaten wie ``make extension-daten``, aber mit Rollen statt Namen."""
    from unittest import mock

    from scripts import export_extension
    with mock.patch("scripts.build_review_html.personennamen", return_value={}):
        return export_extension.baue(samples)


def _feld(block: dict, anfang: str) -> dict:
    return next((f for f in block.get("felder") or []
                 if str(f.get("titel", "")).startswith(anfang)), {})


def _gefuellt(f: dict) -> bool:
    return bool(str(f.get("wert") or "").strip())


def _ordner_von(samples: Path, dok: str) -> str:
    ordner = getattr(_ordner_von, "_cache", None)
    if ordner is None:
        ordner = {}
        for pfad in samples.glob("*.json"):
            if pfad.name.startswith("_"):
                continue
            try:
                d = json.loads(pfad.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(d, dict):
                from extractors.eintraege import dok_name
                ordner[dok_name(d.get("pdf_name"))] = str(d.get("ordner") or "")
        _ordner_von._cache = ordner
    from extractors.eintraege import dok_name
    return ordner.get(dok_name(dok), "")


def pruefe_leiste(samples: Path) -> list[dict]:
    """Je Dokument: steht alles in der Leiste, was sein Typ verlangt?"""
    from extractors import bankobjekte
    from extractors.eintraege import betrag

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
        erlaubt = erlaubte_typen(_ordner_von(samples, dok))
        if erlaubt and typ not in erlaubt:
            fehlt.append(f"Typ {typ} passt nicht zum Ordner {_ordner_von(samples, dok)}")
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
        elif typ == "lohnausweis":
            werte = [w for _, b in eigene for w in b.get("werte") or []
                     if w.get("quelle") == dok]
            if not any(w["bezeichnung"].startswith("Nettolohn") and w["betrag"]
                       for w in werte):
                fehlt.append("Nettolohn")
            angaben = next((a["werte"] for _, b in eigene for a in b.get("angaben") or []
                            if a.get("quelle") == dok), [])
            for titel in ("Arbeitgeber", "Anstellung von", "Anstellung bis"):
                if not any(str(a.get("titel", "")).startswith(titel) and a.get("wert")
                           for a in angaben):
                    fehlt.append(titel)
            if any(b.get("person", "") in UNBEKANNT for _, b in eigene):
                fehlt.append("Person")
        elif typ in ("saeule_3a", "kinderbetreuung"):
            if not any(w.get("quelle") == dok and w.get("betrag")
                       for _, b in eigene for w in b.get("werte") or []):
                fehlt.append("Betrag")
            if any(b.get("person", "") in UNBEKANNT for _, b in eigene):
                fehlt.append("Person")
        elif typ == "wertschriftenverzeichnis":
            for form, b in eigene:
                if form != "Wertschriftenverzeichnis (400)":
                    continue
                for titel in ("Bank", "Total Steuerwert", "Konto-/Depot-Nr."):
                    if not _gefuellt(_feld(b, titel)):
                        fehlt.append(titel)
                if not (_gefuellt(_feld(b, "Ertrag ohne")) or _gefuellt(_feld(b, "Ertrag mit"))):
                    fehlt.append("Ertrag (0 ist erlaubt, leer nicht)")
                if str(_feld(b, "Zugehörigkeit").get("wert") or "") in UNBEKANNT:
                    fehlt.append("Person")
        elif typ == "bank_zinsausweis":
            konten = [o for o in objekte_von.get(dok, []) if o.art == "Konto"
                      and not (bankobjekte.einwert(o.werte.get("saldo"))[0] is None
                               and not bankobjekte.einwert(o.werte.get("zins"))[0])]
            if _ordner_von(samples, dok) and len(konten) > 1:
                fehlt.append(f"{len(konten)} Konten in einem Kontodokument — "
                             "laut Ordner gehört eines hinein")
            leiste = [b for form, b in eigene if b.get("maske") == "Bankkonto"]
            if len(leiste) != len(konten):
                fehlt.append(f"{len(konten)} Konto/Konten im Beleg, {len(leiste)} in der Leiste")
            for form, b in eigene:
                if form != "Wertschriftenverzeichnis (400)":
                    continue
                for titel in ("Bank", "Konto-/Depot-Nr.", "Total Guthaben"):
                    if not _gefuellt(_feld(b, titel)):
                        fehlt.append(titel)
                if not (_gefuellt(_feld(b, "Ertrag ohne")) or _gefuellt(_feld(b, "Ertrag mit"))):
                    fehlt.append("Ertrag (0 ist erlaubt, leer nicht)")
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
                s = bankobjekte.einwert(o.werte.get("saldo"))[0]
                if s is not None and s not in saldi:
                    fehlt.append(f"Saldo {s} aus dem Beleg fehlt in der Leiste")
        elif typ == "hypothek_zinsbestaetigung":
            hyp = [o for o in objekte_von.get(dok, []) if o.art == "Hypothek"]
            leiste = [b for form, b in eigene if form == "Schuldenverzeichnis"]
            if len(leiste) != len(hyp):
                fehlt.append(f"{len(hyp)} Hypothek(en) im Beleg, {len(leiste)} in der Leiste")
            paare = {(betrag(_feld(b, "Schuld am").get("wert")),
                      betrag(_feld(b, "Schuldzinsen").get("wert"))) for b in leiste}
            for o in hyp:
                paar = (bankobjekte.einwert(o.werte.get("schuld"))[0],
                        bankobjekte.einwert(o.werte.get("zins"))[0])
                if paar not in paare:
                    fehlt.append(f"Schuld/Zins {paar[0]}/{paar[1]} nicht als Paar in der Leiste")
            for b in leiste:
                if _feld(b, "Name, Vorname und Adresse").get("warnung"):
                    fehlt.append("Gläubiger-Adresse")
        for form, b in eigene:
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
    rollen = ("elternteil_1", "elternteil_2", "kind_1", "kind_2")
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
                                "fehlt": [f"Prämie ohne Person ({p or 'leer'})"]})
                continue
            if aus_name in rollen and aus_name != p:
                befunde.append({"dokument": dok, "typ": "kk_person",
                                "fehlt": [f"Dateiname sagt {aus_name}, Leiste sagt {p}"]})
            ziel = je_rolle[p]
            ziel["dokumente"].add(dok)
            bez = str(w.get("bezeichnung", ""))
            if "verbilligung" in bez.lower():
                ziel["verbilligung"] += wert
            elif "VVG" in bez or "Zusatz" in bez:
                ziel["vvg"] += wert
            else:
                ziel["kvg"] += wert      # KVG oder nicht aufgeteiltes Total
    for r, z in je_rolle.items():
        # Nur fast leere Prämien melden: Modell, Franchise und Verbilligung
        # drücken die Grundprämie stark (der Anwender: „die Werte sind alle da").
        minimum = Decimal(300) if r.startswith("eltern") else Decimal(150)
        if not z["dokumente"]:
            befunde.append({"dokument": f"Krankenkasse {r}", "typ": "kk_person",
                            "fehlt": ["keine Prämie in irgendeinem Dokument"]})
        elif z["kvg"] < minimum:
            befunde.append({"dokument": f"Krankenkasse {r}", "typ": "kk_person",
                            "fehlt": [f"Grundversicherung {z['kvg']} unter {minimum} im Jahr "
                                      "— fast leer, bitte prüfen"]})
    # Dokumentmodell (der Anwender, 261024-jga): Erwachsene je zwei Dokumente von
    # zwei Versicherern, Kinder je eines mit KVG und VVG. Jedes Dokument
    # liefert eine Prämie — sonst ist ein Wert verloren gegangen.
    kk_typen = ("kk_praemienbescheinigung", "krankheitskosten")
    erwartet = {"elternteil_1": 2, "elternteil_2": 2, "kind_1": 1, "kind_2": 1}
    dok_rolle: dict[str, str] = {}
    for dok, typ in typ_von.items():
        if typ not in kk_typen:
            continue
        stamm = dok.rsplit(".", 1)[0].replace("_-_", " - ").replace("_", " ")
        name = re.sub(r"\s*(19|20)\d{2}(-\d+)?$", "", stamm.rsplit(" - ", 1)[-1])
        dok_rolle[dok] = resolve_role_constrained(name, "kk_praemienbescheinigung")
    for r, n in erwartet.items():
        doks = [d for d, x in dok_rolle.items() if x == r]
        if len(doks) != n:
            befunde.append({"dokument": f"Krankenkasse {r}", "typ": "kk_person",
                            "fehlt": [f"{len(doks)} Dokument(e), erwartet {n}"]})
        for d in doks:
            praemie = sum((betrag(w.get("betrag")) or Decimal(0))
                          for form, b in bloecke if form == "Versicherungsprämien"
                          for w in b.get("werte") or []
                          if w.get("quelle") == d
                          and "verbilligung" not in str(w.get("bezeichnung", "")).lower())
            kosten = any(dok_name(w.get("quelle")) == dok_name(d) and w.get("betrag")
                         for form, b in bloecke if form == "Hilfsblatt 320"
                         for w in b.get("werte") or [])
            if praemie <= 0 and not kosten:
                befunde.append({"dokument": d, "typ": "kk_person",
                                "fehlt": [f"liefert weder Prämie noch Kosten ({r})"]})
        if n == 1 and doks:
            z = je_rolle[r]
            if z["kvg"] <= 0 or z["vvg"] <= 0:
                befunde.append({"dokument": doks[0], "typ": "kk_person",
                                "fehlt": ["Kinderdokument: KVG und VVG erwartet"]})
    pruefe_leiste.kk = je_rolle
    return befunde


def bilanz(samples: Path) -> list[dict]:
    """Je Dokument: jeder Wert der Leiste und wie er abgesichert ist.

    gesichert — fester Leser/Regel mit Anker, keine Warnung
    modell    — nur vom Sprachmodell gelesen (Anker, aber keine Regel)
    warnung   — Gegenprobe geht nicht auf, Widerspruch oder Hinweis
    fehlt     — Pflichtwert leer
    """
    from extractors.eintraege import dok_name as _dn

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
                if w.get("gerechnet") and not w.get("einsetzen"):
                    continue
                st = ("fehlt" if not w.get("betrag") else
                      "warnung" if w.get("warnung") or w.get("hinweis") else
                      "gesichert" if w.get("herkunft") in ("regel", "ablage", "mensch")
                      else "modell")
                aus.setdefault(_dn(w.get("quelle", "")), {}).setdefault(st, []).append(
                    f"{f['formular'][:14]}: {w['bezeichnung'][:30]}")
    return [{"dokument": d, **v} for d, v in sorted(aus.items())]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--samples", type=Path, required=True)
    args = ap.parse_args()

    leiste = pruefe_leiste(args.samples)
    offen_leiste = [b for b in leiste if b["fehlt"]]
    print(f"Leiste (was du abträgst): {len(leiste)} Dokumente/Personen")
    for b in leiste:
        zeichen = "✓" if not b["fehlt"] else "✗"
        print(f"  {zeichen} {b['typ'][:22]:22} {str(b['dokument'])[:55]:55} {b['fehlt'] or ''}")
    print(f"\n{len(offen_leiste)} mit Befund in der Leiste.\n")
    kk = getattr(pruefe_leiste, "kk", {})
    if kk:
        print("Krankenkasse je Person (Grundversicherung inkl. nicht aufgeteilt | "
              "Zusatz | Verbilligung | Dokumente):")
        for r, z in kk.items():
            print(f"  {r:13} {z['kvg']:>10} | {z['vvg']:>9} | {z['verbilligung']:>8} | "
                  f"{len(z['dokumente'])}")
        print()

    angaben = []
    for f in _leiste(args.samples)["formulare"]:
        for blk in f["bloecke"]:
            for x in blk.get("felder") or []:
                if x.get("warnung") and x["titel"].startswith((
                        "Bank", "Konto-/Depot", "Name, Vorname", "Zinssatz", "Zugehörigkeit")):
                    grund = re.sub(r"\s+", " ", re.sub(r"[\d'’.,]+", "…", x.get("hinweis", "")))
                    angaben.append(f"  · {', '.join(d[:45] for d in blk.get('dokumente') or [])}: "
                                   f"{x['titel'][:24]} ({grund[:70]})")
    print(f"Angaben ohne Betrag mit Warnung: {len(angaben)}")
    print("\n".join(angaben))
    print()

    print("Schulden (ohne Beträge):")
    for f in _leiste(args.samples)["formulare"]:
        if f["formular"] != "Schuldenverzeichnis":
            continue
        for i, blk in enumerate(f["bloecke"], start=1):
            fs = {x["titel"][:12]: x for x in blk.get("felder") or []}
            schuld, zins = fs.get("Schuld am 31", {}), fs.get("Schuldzinsen", {})
            herkunft = "Dokument" if schuld.get("herkunft") == "beleg" else "Durchsicht"
            hinweise = "; ".join(sorted({h.split(":")[0].split("—")[0].strip()
                                         for h in (schuld.get("hinweis", ""), zins.get("hinweis", ""))
                                         if h}))
            print(f"  {i:2}. aus {herkunft:10} Schuld {'✓' if schuld.get('wert') else '–'} "
                  f"Zins {'✓' if zins.get('wert') else '–'}  {len(blk.get('dokumente') or [])} Dok.  "
                  f"{hinweise}")
    print()

    b = bilanz(args.samples)
    zaehl = {k: sum(len(x.get(k, [])) for x in b)
             for k in ("gesichert", "modell", "warnung", "fehlt")}
    gesamt = sum(zaehl.values()) or 1
    print("Absicherung je Wert (Beträge der Leiste):")
    for k, n in zaehl.items():
        print(f"  {k:10} {n:4}  ({n / gesamt * 100:.0f} %)")
    for x in b:
        offen = x.get("modell", []) + x.get("warnung", []) + x.get("fehlt", [])
        if offen:
            print(f"  · {str(x['dokument'])[:55]:55} "
                  + "; ".join(f"{k}: {', '.join(x[k])}" for k in ("modell", "warnung", "fehlt") if x.get(k)))
    print()

    kette = pruefe_kette(args.samples)
    # Was die Leiste schon abdeckt (ALV-Bescheinigung), ist kein offener
    # Typ mehr — die Zweitlesung unten meldet es sonst doppelt.

    print(f"Konten/Hypotheken (Kette): {len(kette)} Objekte")
    for b in kette:
        zeichen = "✓" if not b["fehlt"] and not b["unrealistisch"] else "✗"
        quellen = ", ".join(f"{d[:40]} S{s}" for d, s in b["quellen"][:3])
        print(f"  {zeichen} {b['art']:8} {b['bezeichnung'][:20]:20} "
              f"fehlt={b['fehlt']} unreal={b['unrealistisch']}  ({quellen})")
    offen = sum(1 for b in kette if b["fehlt"] or b["unrealistisch"])
    print(f"\n{offen} Objekt(e) der Kette mit Befund.")
    return 0 if offen == 0 and not offen_leiste and not angaben else 1


if __name__ == "__main__":
    raise SystemExit(main())
