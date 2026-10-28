#!/usr/bin/env python3
"""Die geprüften Werte als Datei für die Browser-Erweiterung.

Die Erweiterung liest diese Datei **lokal** — sie wird nirgends hochgeladen und
die Erweiterung baut keine Netzwerkverbindung auf. Sie enthält echte Beträge
und gehört deshalb nach ``output/`` (gitignored), wie jede andere Ausgabe mit
Werten.

Aufbau: nach Formular in der Reihenfolge des Ausfüllens, darin je Konto bzw.
Person ein Block, darin die Werte mit Ziffer und amtlicher Feldnummer. Also
genau die Gliederung der Übertragungstabelle — die Erweiterung zeigt dieselbe
Ordnung wie das Formular vor einem.

Eine Angabe je Wert ist für die Erweiterung wichtiger als alle anderen:
``gerechnet``. Für die Ziffern 4, 12, 30.1 und 34 rechnet ZHprivateTax die
Summe selbst aus den Einzelpositionen des Wertschriften- bzw.
Schuldenverzeichnisses. Wer sie zusätzlich einträgt, verdoppelt sie. Solche
Summen darf die Erweiterung nur als Kontrollzahl zeigen, nie zum Einsetzen
anbieten.

Aufruf::

    make extension-daten
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TRENNER = "─" * 70

# Was das Lohnformular neben dem Betrag abfragt — Arbeitgeber und die Dauer
# der Anstellung. Der Lohnausweis traegt beides im Kopf, die Extraktion liest
# es auch, aber in der Leiste kam nur der Nettolohn an. Wer zwei Lohnausweise
# hat, musste dafuer die PDFs oeffnen (261024-jga, vom Nutzer gemeldet).
#
# Der Arbeitgeber ist der Aussteller der Uebertragungstabelle (in der
# Durchsicht gesetzt), nicht das Feld des Modells: das las auf einem Beleg die Adresse des
# Arbeitnehmers als Arbeitgeber — verankert und trotzdem falsch.
LOHN_ANGABEN = (
    ("periode_von", "Anstellung von"),
    ("periode_bis", "Anstellung bis"),
)


def _periode_nach_regel(samples: Path) -> dict[str, dict]:
    """``pdf_name`` → Feld E (von – bis), geometrisch aus den Wortboxen.

    Regel vor Modell: Form 11 ist standardisiert, die Daten stehen an
    fester Stelle im Kopf. Die Wortboxen liegen ohnehin neben dem Lauf.
    """
    from extractors.layout_lohnausweis import extract_lohnausweis_periode

    aus: dict[str, dict] = {}
    for pfad in samples.glob("*.json"):
        if pfad.name.startswith("_") or pfad.name.endswith(".truth.json"):
            continue
        try:
            dok = json.loads(pfad.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(dok, dict) or dok.get("belegtyp") != "lohnausweis":
            continue
        seiten = dok.get("pages") or []
        if not seiten:
            continue
        seite = seiten[0]
        woerter = [dict(w, page=seite.get("page_num", 1))
                   for w in seite.get("words") or []]
        gefunden = extract_lohnausweis_periode(
            woerter, seite.get("width"), seite.get("height"))
        name = str(dok.get("pdf_name") or "")
        aus[name] = gefunden
        aus.setdefault(Path(name).stem, gefunden)
    return aus


def _lohnfelder(samples: Path) -> dict[str, dict]:
    """``pdf_name`` → Felder jedes Lohnausweises aus dem Extraktionslauf.

    Feld E kommt aus der Regel, wo sie eindeutig ist; das Modell fuellt nur
    die Luecken.
    """
    pfad = samples / "_results_full.json"
    try:
        eintraege = json.loads(pfad.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if isinstance(eintraege, dict):
        eintraege = eintraege.get("results") or eintraege.get("entries") or []
    regel = _periode_nach_regel(samples)
    aus: dict[str, dict] = {}
    for e in eintraege:
        if not isinstance(e, dict) or e.get("belegtyp") != "lohnausweis":
            continue
        felder = {f.get("feld"): f for f in e.get("fields") or []
                  if isinstance(f, dict)}
        name = str(e.get("pdf_name") or "")
        for feld, gefunden in (regel.get(name) or {}).items():
            if gefunden:
                felder[feld] = {"value": gefunden["value"],
                                "anchor_valid": True, "herkunft": "regel"}
        aus[name] = felder
        aus.setdefault(Path(name).stem, felder)
    return aus


def lohnangaben(felder: dict | None, arbeitgeber: str = "") -> list[dict]:
    """Arbeitgeber und Anstellungsdauer eines Lohnausweises, zum Einsetzen.

    Jede Angabe traegt, woher sie kommt: ``tabelle`` (Aussteller), ``belegt``
    (woertlich im Beleg verankert) oder ``offen``. Die Daten der Anstellung
    hat niemand in der Durchsicht bestaetigt; die Leiste zeigt das an, und
    ein Wert ohne Anker wird nicht als sicher ausgegeben.
    """
    if arbeitgeber.startswith(("⚠", "manual_review")):
        arbeitgeber = ""
    aus = [{"titel": "Arbeitgeber", "wert": arbeitgeber,
            "herkunft": "tabelle" if arbeitgeber else "offen"}]
    for feld, titel in LOHN_ANGABEN:
        f = (felder or {}).get(feld) or {}
        wert = str(f.get("value") or "").strip()
        if wert.startswith("manual_review"):
            wert = ""
        aus.append({"titel": titel, "wert": wert,
                    "herkunft": ("belegt" if wert and f.get("anchor_valid")
                                 else "offen")})
    return aus


def _inhaber(samples: Path) -> dict[str, str]:
    """Dokument → Kontoinhaber laut Extraktion (für die Zugehörigkeit)."""
    try:
        eintraege = json.loads((samples / "_results_full.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    aus = {}
    for e in eintraege if isinstance(eintraege, list) else []:
        for f in e.get("fields") or []:
            if f.get("feld") == "kontoinhaber_name" and f.get("value"):
                aus[str(e.get("pdf_name"))] = str(f["value"])
    return aus


def _verbilligung_ergaenzen(samples: Path, bloecke: list[dict]) -> None:
    """Prämienverbilligung je Person dazu; Gegenprobe mit dem Rechnungstotal.

    Das Formular will die Prämien abzüglich Verbilligung (261024-jga).
    """
    from extractors import praemienverbilligung
    from extractors.eintraege import anzeige, betrag, dok_name, eingabe, tabellen_fuer

    tabellen = tabellen_fuer(samples, ("kk_praemienbescheinigung", "krankheitskosten"))
    for dok, tab in tabellen.items():
        v = praemienverbilligung.lese(tab)
        if v is None:
            continue
        block = next((b for b in bloecke
                      if any(dok_name(w.get("quelle")) == dok for w in b.get("werte") or [])), None)
        if block is None:
            continue
        praemien = sum((betrag(w.get("betrag")) or 0) for w in block["werte"]
                       if dok_name(w.get("quelle")) == dok
                       and str(w.get("bezeichnung", "")).startswith("Prämie"))
        hinweis = ""
        if v.in_rechnung is not None and abs(praemien - v.betrag - v.in_rechnung) > 1:
            hinweis = (f"Prämien {anzeige(praemien)} − Verbilligung ≠ in Rechnung "
                       f"gestellt {anzeige(v.in_rechnung)} — bitte prüfen")
        block["werte"].append({
            "id": "", "ziffer": "", "ziffername": "", "feld": "",
            "bezeichnung": "Abzüglich erhaltene Prämienverbilligung",
            "unterscheidung": "", "betrag": anzeige(v.betrag),
            "einsetzen": eingabe(v.betrag), "jahr": "", "gerechnet": False,
            "quelle": dok, "hinweis": hinweis, "herkunft": "regel", "anker": v.anker})


def _bestaetigen(samples: Path, formulare: list[dict], jahr: str | None = None) -> None:
    """Modellwerte gegen eine Regel halten (261024-jga).

    Säule 3a: Formular 21 oder Beitragstabelle. Prämien: der Betrag muss auf
    einer Zeile mit Prämien-Beschriftung stehen. Stimmt es, gilt der Wert als
    gesichert; widerspricht die Regel, warnt die Leiste mit ihrem Wert.

    Seit 261025-hbv (B5): eine Prämienzeile macht einen Modellwert nur
    ``modell_bestaetigt`` (mit Anker der Zeile), nie ``regel`` — gelesen hat
    ihn das Modell, die Zeile bestätigt nur den Betrag. Eine Monatsprämie
    („pro Monat", „monatlich", „Monatsprämie") bestätigt nichts: sie ist
    nicht der Jahresbetrag, auch wenn er zufällig gleich lautet.

    ``jahr``: das Steuerjahr des Laufs (261025-kme, B1b) — kein fester
    Rückfall mehr. Kann die Regel das Formular nicht lesen (Befund ohne
    Wert), bleibt der Modellwert, ist aber markiert.
    """
    import re

    from extractors import saeule3a
    from extractors.eintraege import anzeige, betrag, dok_name, tabellen_fuer

    tabellen = tabellen_fuer(samples, ("saeule_3a", "kk_praemienbescheinigung",
                                       "krankheitskosten"))
    praemie = re.compile(r"Pr[äa]mie|KVG|VVG|Grundversicherung|Zusatzversicherung", re.I)
    monatlich = re.compile(r"pro\s+Monat|monatlich|Monatspr[äa]mie", re.I)
    for f in formulare:
        for b in f["bloecke"]:
            for w in b.get("werte") or []:
                if w.get("herkunft") not in ("modell", "mensch", "ablage"):
                    continue
                durchsicht = w.get("herkunft") != "modell"
                tab = tabellen.get(dok_name(w.get("quelle")))
                wert = betrag(w.get("betrag"))
                if tab is None or wert is None:
                    continue
                # Auch Werte der Durchsicht gegen das Dokument halten: steht
                # der bestätigte Betrag dort, wo er hingehört? (261024-jga)
                if f["formular"].startswith("Bescheinigung der Vorsorge"):
                    e = saeule3a.lese(tab, jahr or str(w.get("jahr") or "") or None)
                    regel, befunde = e.wert, e.befunde
                    if regel is None and befunde:
                        w["hinweis"] = "; ".join(x for x in (
                            w.get("hinweis", ""),
                            "Formular nicht lesbar: " + "; ".join(befunde)) if x)
                        w["warnung"] = True
                        continue
                    if regel is not None and regel == wert and not befunde:
                        if not durchsicht:
                            # Gelesen (Total/Jahreszeile) oder gerechnet
                            # (Summe der Verträge) — das Etikett sagt es.
                            w["herkunft"] = "regel" if e.herkunft == "dokument" else "berechnet"
                            w["anker"] = e.anker
                    elif regel is not None:
                        w["hinweis"] = (f"Formular sagt {anzeige(regel)}"
                                        + ("; " + "; ".join(befunde) if befunde else ""))
                        w["warnung"] = True
                elif f["formular"] == "Versicherungsprämien":
                    # Beschriftung auf der Zeile oder direkt darüber („Prämie"
                    # / „Total CHF …" — zweizeilig, wie beim Leser).
                    steht_da = None
                    vorher = None
                    for z in tab.zeilen:
                        if any(abs(v) == wert for _, v in z.betraege()):
                            oben = (vorher is not None and vorher.seite == z.seite
                                    and 0 < z.y - vorher.y <= 30 and praemie.search(vorher.text))
                            monat = monatlich.search(z.text) or (
                                oben and monatlich.search(vorher.text))
                            if (praemie.search(z.text) or oben) and not monat:
                                steht_da = z
                                break
                        vorher = z
                    if steht_da and not durchsicht:
                        w["herkunft"] = "modell_bestaetigt"
                        w["anker"] = steht_da.anker()
                    elif not steht_da and durchsicht:
                        w["hinweis"] = ("Wert aus der Durchsicht steht so auf keiner "
                                        "Prämienzeile des Dokuments — bitte prüfen")
                        w["warnung"] = True


def _entscheide_anwenden(samples: Path, formulare: list[dict],
                         laufdoks: set[str]) -> list[str]:
    """Entscheidungen aus der Review („Zu entscheiden") anwenden.

    Jeder Wert und jeder Eintrag bekommt seinen Schlüssel und ob sein
    Dokument fehlt — die Seite „Zu entscheiden" baut darauf. „behalten"
    nimmt die Warnung weg, „weglassen" nimmt den Wert aus der Leiste; die
    Ablage bleibt unberührt (extractors/entscheide.py).

    Ein Schlüssel, eine Position (261025-jm6, A2c): Werte und Einträge mit
    gleichem Dokument, gleicher Bezeichnung bzw. gleichem Titel und gleichem
    Betrag sind eine Gruppe. Durchgang 1: Werte mit ``pos`` (aus der Tabelle)
    beanspruchen ihre Nummer — eine in der Gruppe schon vergebene zählt als
    ohne. Durchgang 2: die übrigen bekommen in Leistenreihenfolge die
    kleinste freie Nummer. Getroffen wird zuerst der genaue Schlüssel; sonst,
    nur für das Mitglied mit der kleinsten Nummer, der alte Schlüssel ohne
    ``#`` — bestehende Schlüssel gelten weiter für die erste Position.

    Rückgabe: die gesetzten Entscheidungen, die keinen Wert und keinen
    Eintrag getroffen haben (A2d) — sortiert.
    """
    from extractors import entscheide
    from extractors.eintraege import dok_name

    gesetzt = entscheide.lade(samples)

    # Mitglieder in Leistenreihenfolge: (Eintrag, alter Schlüssel, Bauer, pos)
    mitglieder: list[list] = []
    for f in formulare:
        for b in f["bloecke"]:
            if b.get("felder"):
                doks, titel, felder = b.get("dokumente") or [], b.get("titel", ""), b["felder"]
                mitglieder.append([b, entscheide.schluessel_block(doks, titel, felder),
                                   (lambda n, d=doks, t=titel, x=felder:
                                    entscheide.schluessel_block(d, t, x, n)), None])
            for w in b.get("werte") or []:
                q, bez, wert = w.get("quelle", ""), w.get("bezeichnung", ""), w.get("betrag")
                pos = w.get("pos")
                mitglieder.append([w, entscheide.schluessel_wert(q, bez, wert),
                                   (lambda n, q=q, bez=bez, wert=wert:
                                    entscheide.schluessel_wert(q, bez, wert, n)),
                                   pos if isinstance(pos, int) and pos > 0 else None])
    vergeben: dict[str, set[int]] = {}
    nummer: dict[int, int] = {}
    for i, (_, gruppe, _, pos) in enumerate(mitglieder):          # Durchgang 1
        if pos is not None and pos not in vergeben.setdefault(gruppe, set()):
            vergeben[gruppe].add(pos)
            nummer[i] = pos
    for i, (_, gruppe, _, _) in enumerate(mitglieder):            # Durchgang 2
        if i in nummer:
            continue
        frei = vergeben.setdefault(gruppe, set())
        n = 1
        while n in frei:
            n += 1
        frei.add(n)
        nummer[i] = n
    erste = {g: min(ns) for g, ns in vergeben.items()}

    getroffen: set[str] = set()

    def schluessel_von(i: int) -> str:
        _, gruppe, bauer, _ = mitglieder[i]
        neu = bauer(nummer[i])
        if neu in gesetzt:
            return neu
        if nummer[i] == erste[gruppe] and gruppe in gesetzt:
            return gruppe
        return neu

    k_von = {id(m[0]): schluessel_von(i) for i, m in enumerate(mitglieder)}

    def erledigt(eintrag: dict, aktion: str) -> None:
        eintrag["entschieden"] = aktion

    for f in formulare:
        for b in f["bloecke"]:
            if b.get("felder"):
                k = k_von[id(b)]
                b["schluessel"] = k
                b["verwaist"] = bool(b.get("dokumente")) and not any(
                    dok_name(d) in laufdoks for d in b["dokumente"])
                aktion = (gesetzt.get(k) or {}).get("aktion")
                if aktion:
                    getroffen.add(k)
                if aktion == "behalten":
                    for x in b["felder"]:
                        if x.get("warnung"):
                            x["warnung"] = False
                            x["hinweis"] = (x.get("hinweis") or "") + " — von dir behalten"
                    erledigt(b, aktion)
                elif aktion == "weglassen":
                    b["weg"] = True
            behalten = []
            for w in b.get("werte") or []:
                k = k_von[id(w)]
                w["schluessel"] = k
                w["verwaist"] = bool(w.get("quelle")) and dok_name(w["quelle"]) not in laufdoks
                aktion = (gesetzt.get(k) or {}).get("aktion")
                if aktion:
                    getroffen.add(k)
                if aktion == "weglassen":
                    continue
                if aktion == "behalten" and w.get("warnung"):
                    w["warnung"] = False
                    w["info"] = "; ".join(x for x in (w.get("info"), w.get("hinweis"),
                                                       "von dir behalten") if x)
                    w["hinweis"] = ""
                    erledigt(w, aktion)
                behalten.append(w)
            b["werte"] = behalten
        f["bloecke"] = [b for b in f["bloecke"] if not b.get("weg")
                        and (b.get("werte") or b.get("felder") or b.get("angaben"))]
    return sorted(set(gesetzt) - getroffen)


def dokument_fehlt(eintrag: dict) -> bool:
    """Fehlt das Dokument dieses Werts bzw. Eintrags? — die eine Regel.

    Wahr genau dann, wenn er verwaist ist (sein PDF liegt nicht im Ordner)
    und der Mensch nicht entschieden hat. „behalten" heisst: der Wert gilt,
    das Dokument fehlt nicht mehr; „weglassen" steht ohnehin nicht mehr in
    der Leiste. Stand-Seite und make pruefen zählen beide hiermit
    (261025-jm6, A4a) — vorher die eine über den Hinweistext, die andere
    über das blosse verwaist-Flag.
    """
    return bool(eintrag.get("verwaist")) and not eintrag.get("entschieden")


def _anker_der_zeile(r: dict) -> dict | None:
    """Anker einer Tabellenzeile: der gespeicherte (Ablage), sonst Seite und
    Snippet — aber nur, wenn der Betrag im Beleg auch nachgewiesen ist."""
    if r.get("anker"):
        return r["anker"]
    if r.get("seite") and r.get("snippet") and not r.get("ohne_nachweis"):
        return {"seite": r["seite"], "snippet": r["snippet"]}
    return None


def _verwaiste_markieren(formulare: list[dict], laufdoks: set[str]) -> None:
    """Werte, deren Dokument nicht mehr im Ordner liegt: sichtbar machen.

    Sie können ein Doppel sein — oder der einzige Beleg (eine bestätigte
    Prämie, deren PDF gelöscht wurde). Entscheiden kann nur der Mensch;
    still entfernt wird nichts (261024-jga, make verwaiste).

    Als Doppel fällt ein Wert nur weg, wenn derselbe Betrag bei derselben
    Kasse/Bank für dieselbe Person aus einem Dokument im Ordner da ist — und
    der behaltene Wert nennt ihn (261025-hbv, B4). Vorher genügte der gleiche
    Betrag im selben Formular: die Prämie des zweiten Kindes verschwand.
    """
    from extractors.eintraege import _kern, betrag as _b, dok_name as _dn

    for f in formulare:
        aus_lauf: dict[tuple, dict] = {}
        for b in f["bloecke"]:
            kern = _kern(str(b.get("aussteller") or ""))
            for w in b.get("werte") or []:
                if w.get("quelle") and _dn(w["quelle"]) in laufdoks and kern:
                    aus_lauf.setdefault((_b(w.get("betrag")), kern, b.get("person", "")), w)
        for b in f["bloecke"]:
            kern = _kern(str(b.get("aussteller") or ""))
            behalten = []
            for w in b.get("werte") or []:
                if not w.get("quelle") or _dn(w["quelle"]) in laufdoks:
                    behalten.append(w)
                    continue
                gleich = aus_lauf.get((_b(w.get("betrag")), kern, b.get("person", ""))) \
                    if kern and _b(w.get("betrag")) is not None else None
                if gleich is not None:
                    zusatz = f"deckt auch Durchsicht-Wert aus {w['quelle']} ab"
                    gleich["info"] = "; ".join(x for x in (gleich.get("info"), zusatz) if x)
                    continue
                w["hinweis"] = "; ".join(x for x in (
                    w.get("hinweis", ""),
                    "einziger Beleg: Dokument nicht mehr im Ordner — PDF wieder "
                    "ablegen oder Wert bestätigen") if x)
                w["warnung"] = True
                behalten.append(w)
            b["werte"] = behalten
        f["bloecke"] = [b for b in f["bloecke"] if b.get("werte") or b.get("felder")
                        or b.get("angaben")]


ALV_FORMULAR = "Erwerbsausfallentschädigung ALV (3.3)"


def _alv_bloecke(samples: Path, person_text, jahr: str | None = None) -> list[dict]:
    """Bescheinigung der Arbeitslosenkasse → Ziffer 3.3, Feld 140/141.

    Eingesetzt wird der steuerbare Betrag der Bescheinigung; die Gegenprobe
    Taggelder + Zulagen − Beiträge muss aufgehen (261024-jga).

    ``jahr``: das Steuerjahr des Laufs; der Leser wählt damit die Zeile
    (261025-kme, B1a). Kann er keinen Wert wählen, steht ein Wert ohne
    Betrag mit Warnung da — nicht still nichts.
    """
    from extractors import alv
    from extractors.eintraege import anzeige, eingabe, tabellen_fuer
    from scripts.build_tax_output import resolve_role_constrained

    bloecke = []
    for dok, tab in tabellen_fuer(samples, ("unknown",)).items():
        text = " ".join(z.text for z in tab.zeilen[:80])
        if not alv.ALV_RE.search(text):
            continue
        b = alv.lese(tab, jahr=jahr)
        if b is None:
            continue
        person = dok.rsplit(" - ", 1)[-1].rsplit(".", 1)[0]
        rolle = resolve_role_constrained(person, "lohnausweis")
        feld = {"elternteil_1": "140", "elternteil_2": "141"}.get(rolle, "")
        titel_block = " · ".join(t for t in ("Arbeitslosenkasse", person_text(rolle)) if t)
        if "taggelder" not in b.werte:
            # Nichts gewählt (zwei Jahreszeilen, Jahr fehlt): ein Wert ohne
            # Betrag, damit die Lücke sichtbar ist (261025-kme, B1a).
            bloecke.append({"titel": titel_block, "aussteller": "Arbeitslosenkasse",
                            "person": person_text(rolle), "angaben": [], "werte": [{
                                "id": "", "ziffer": "3.3",
                                "ziffername": "Erwerbsausfallentschädigung ALV",
                                "feld": feld,
                                "bezeichnung": "Steuerbarer Betrag gemäss Bescheinigung",
                                "unterscheidung": "", "betrag": "", "einsetzen": "",
                                "jahr": b.jahr, "gerechnet": False, "quelle": dok,
                                "hinweis": "; ".join(b.befunde) or "kein Wert lesbar",
                                "herkunft": "fehlt", "warnung": True, "anker": b.anker}]})
            continue
        wert = b.werte.get("steuerbar")
        hinweis = "; ".join(b.befunde)
        herkunft = "regel"
        if wert is None:
            herkunft = "berechnet"      # nicht gedruckt — aus den Spalten (B6)
            wert = b.werte["taggelder"] + b.werte.get("familienzulagen", 0) - sum(
                b.werte.get(n, 0) for n in ("ahv", "nbuv", "bvg"))
            hinweis = "; ".join(x for x in (hinweis, "steuerbarer Betrag nicht "
                                            "gedruckt — aus den Spalten gerechnet") if x)
        werte = [{"id": "", "ziffer": "3.3", "ziffername": "Erwerbsausfallentschädigung ALV",
                  "feld": feld, "bezeichnung": "Steuerbarer Betrag gemäss Bescheinigung",
                  "unterscheidung": "", "betrag": anzeige(wert), "einsetzen": eingabe(wert),
                  "jahr": b.jahr, "gerechnet": False, "quelle": dok, "hinweis": hinweis,
                  "herkunft": herkunft, "warnung": bool(b.befunde), "anker": b.anker}]
        for name, titel in (("taggelder", "davon Taggelder (nur zur Kontrolle)"),
                            ("familienzulagen", "davon Familienzulagen (nur zur Kontrolle)")):
            if name in b.werte:
                # Kontrollzahl aus dem Beleg: gelesen, nicht gerechnet, und
                # nicht einzutragen — kein Knopf (261025-hbv, B6).
                werte.append({"id": "", "ziffer": "", "ziffername": "", "feld": "",
                              "bezeichnung": titel, "unterscheidung": "",
                              "betrag": anzeige(b.werte[name]), "einsetzen": "",
                              "jahr": b.jahr, "gerechnet": False, "kontrolle": True,
                              "herkunft": "beleg", "quelle": dok, "anker": b.anker})
        bloecke.append({"titel": titel_block,
                        "aussteller": "Arbeitslosenkasse", "person": person_text(rolle),
                        "angaben": [], "werte": werte})
    return bloecke


def _kinder_bloecke(samples: Path, person_text) -> list[dict]:
    """Fremdbetreuung je Kind aus der Tabelle des Ausweises (261024-jga)."""
    from decimal import Decimal

    from extractors import kinderbetreuung
    from extractors.eintraege import anzeige, eingabe, tabellen_fuer
    from scripts.build_tax_output import resolve_role_constrained

    bloecke = []
    for dok, tab in tabellen_fuer(samples, ("kinderbetreuung",)).items():
        a = kinderbetreuung.lese(tab)
        for k in a.kinder:
            rolle = resolve_role_constrained(k.name, "kinderbetreuung")
            bloecke.append({
                "titel": " · ".join(t for t in (dok.split(" - ")[0], person_text(rolle)) if t),
                "aussteller": dok.split(" - ")[0], "person": person_text(rolle),
                "angaben": [],
                "hinweis": "; ".join(a.befunde),
                "werte": [{"id": "", "ziffer": "", "ziffername": "", "feld": "376",
                           "bezeichnung": "Kosten Kinderbetreuung",
                           "unterscheidung": "", "betrag": anzeige(k.betrag),
                           "einsetzen": eingabe(k.betrag), "jahr": "",
                           "gerechnet": False, "quelle": dok, "herkunft": "regel",
                           "anker": k.anker, "hinweis": "; ".join(a.befunde),
                           "warnung": bool(a.befunde)}],
            })
    return bloecke


_BLOSSE_ZAHL = re.compile(r"-?\d+(?:\.\d+)?")
_BETRAGSFELDER = ("wert", "betrag", "einsetzen")
# Felder, deren Wert eine Kennung oder ein Text ist — auch wenn er nur aus
# Ziffern besteht (Depot-Nr. 123456 ist kein Betrag).
KEIN_BETRAG = re.compile(r"Nr\b|Nr\.|Bezeichnung|Staat|Währung|Zinssatz|Gläubiger|"
                         r"Zugehörigkeit|Bank|Arbeitgeber|Anstellung|Jahr", re.I)


def _durchgehend_mit_apostroph(x):
    """Jeder Betrag als ``64'613.00`` — auch einer, der an ``anzeige``
    vorbeikam (der Anwender 261028: „nicht 64613 — die richtigen Beträge sind so
    schwer zu erkennen"). Nur ganze Zahlenwerte in Betragsfeldern; Texte,
    Daten und Nummern bleiben, wie sie sind."""
    from extractors.eintraege import anzeige

    if isinstance(x, list):
        return [_durchgehend_mit_apostroph(v) for v in x]
    if not isinstance(x, dict):
        return x
    aus = {}
    kennung = bool(KEIN_BETRAG.search(str(x.get("titel") or "")))
    for k, v in x.items():
        if (k in _BETRAGSFELDER and not kennung and isinstance(v, str)
                and _BLOSSE_ZAHL.fullmatch(v.strip())):
            v = anzeige(Decimal(v.strip()))
        aus[k] = _durchgehend_mit_apostroph(v)
    return aus


def _familien_totale(formulare: list[dict], summen: list[dict]) -> None:
    """Prämien und Krankheitskosten: EIN Total für alle Personen, in der
    Reihenfolge der Formularfelder.

    der Anwender (261028): „Ich gebe für alle einmal Prämien und einmal Krankheits-
    und Unfallkosten an." So steht es auch in der Feldliste
    (FELDLISTE-ZHPRIVATETAX-2025.md, Abschnitt 5 und 7): Form. 365
    Teil A ist ein Total für die Familie, Form. 370 ebenso. Bisher bot die
    Leiste jede Prämie je Person und Kasse zum Einsetzen an.

    Oben steht ein Block mit den Feldern des Formulars; die Einzelwerte
    bleiben darunter als Aufschlüsselung, ohne Knopf (``im_total``). Die
    Summen je Ziffer 15 und 22.1 bekommen keinen zweiten Knopf.
    """
    from extractors.eintraege import anzeige, betrag

    def summe(werte):
        return sum((betrag(w.get("betrag")) or 0 for w in werte), Decimal("0"))

    def feld(titel, wert=None, hinweis="", warnung=False):
        if wert is None:
            return {"titel": titel, "wert": "", "einsetzen": "", "herkunft": "leer",
                    "hinweis": hinweis or "nicht erfasst", "warnung": False}
        return {"titel": titel, "wert": anzeige(wert), "einsetzen": anzeige(wert),
                "herkunft": "berechnet", "hinweis": hinweis, "warnung": warnung}

    def warnhinweis(werte):
        n = sum(1 for w in werte if w.get("warnung"))
        return (f"{n} Einzelwert(e) unten mit Hinweis — erst prüfen", True) if n else ("", False)

    nach_name = {f["formular"]: f for f in formulare}
    konten = [b for f in formulare for b in f.get("bloecke") or []
              if b.get("maske") == "Bankkonto"]
    sparzinsen = sum((betrag(x.get("wert")) or 0 for b in konten for x in b.get("felder") or []
                      if str(x.get("titel", "")).startswith("Ertrag")), Decimal("0"))

    f = nach_name.get("Versicherungsprämien")
    if f:
        alle = [w for b in f["bloecke"] for w in b.get("werte") or []]
        praemien = [w for w in alle if str(w.get("bezeichnung", "")).startswith("Prämie")]
        verbilligung = [w for w in alle if "Prämienverbilligung" in str(w.get("bezeichnung", ""))]
        hinweis, warnung = warnhinweis(alle)
        for w in alle:
            w["im_total"] = True
        f["bloecke"].insert(0, {
            "titel": "Form. 365, Teil A — ein Total für alle Personen",
            "maske": "Versicherungsprämien", "person": "alle Personen", "werte": [],
            "felder": [
                feld("Private Krankenversicherungsprämien", summe(praemien),
                     hinweis or f"Grund- und Zusatzversicherung, {len(praemien)} Prämie(n)",
                     warnung),
                feld("Private Unfallversicherungsprämien"),
                feld("Private Lebens- und Rentenversicherungsprämien"),
                feld("Zinsen von Sparkapitalien", sparzinsen if konten else None,
                     "Summe der Kontoerträge — prüfen, ob ZHprivateTax sie aus dem "
                     "Wertschriftenverzeichnis schon übernimmt"),
                feld("Abzüglich erhaltene Prämienverbilligungen",
                     summe(verbilligung) if verbilligung else None,
                     "" if verbilligung else "keine Verbilligung im Beleg gefunden"),
            ]})

    f = nach_name.get("Hilfsblatt 320")
    if f:
        alle = [w for b in f["bloecke"] for w in b.get("werte") or []]
        hinweis, warnung = warnhinweis(alle)
        for w in alle:
            w["im_total"] = True
        f["bloecke"].insert(0, {
            "titel": "Form. 370 — ein Total für alle Personen",
            "maske": "Krankheits- und Unfallkosten", "person": "alle Personen", "werte": [],
            "felder": [
                feld("Selbst getragene Krankheits- und Unfallkosten", summe(alle),
                     hinweis or f"{len(alle)} Beleg(e) — Abzug erst über 5 % des "
                     "Reineinkommens, das rechnet das Formular", warnung),
                feld("Vergütungen Dritter"),
            ]})

    for s_ in summen:
        if s_["ziffer"] in ("15", "22.1") and not s_.get("gerechnet"):
            s_["kontrolle"] = True


def baue(samples: Path, pdf_dir: Path | None = None, jahr: str | None = None) -> dict:
    """Die geprüften Positionen, gegliedert wie die Formulare.

    ``jahr``: Steuerjahr für die festen Leser (ALV, Säule 3a); ohne Angabe
    das Mehrheitsjahr des Laufs (``eintraege.steuerjahr_des_laufs``) — so
    rechnen Leiste, make pruefen, Stand-Seite und Server dasselbe Jahr
    (261025-kme, B1a/B1b).

    ``pdf_dir``: der echte PDF-Ordner. Mit ihm heisst „verwaist": das
    Dokument liegt nicht mehr im Ordner; ohne ihn: es fehlt im letzten Lauf
    (Rückfall für den Korpus und alte Aufrufer, 261025-jm6, A3).
    """
    from extractors.schlusspruefung import betrag_oder_nichts
    from extractors.zielwerte import (feldcode, formular_fuer,
                                      formular_sortierung,
                                      wird_gerechnet, ziffer_name,
                                      ziffer_sortierung)
    from scripts.build_review_html import personennamen
    from scripts.build_tax_output import konto_key

    from extractors.eintraege import steuerjahr_des_laufs
    steuerjahr = jahr or steuerjahr_des_laufs(samples)
    namen = personennamen()
    lohn = _lohnfelder(samples)
    # Dieselben Zeilen wie die Uebertragungstabelle, nicht nur die Ablage.
    #
    # Die Ablage fuehrt die gepruefte Grundmenge; was der Mensch danach in der
    # Oberflaeche ergaenzt, steht in der Tabelle, aber (noch) nicht dort. Wer
    # nur die Ablage exportiert, laesst die Leiste genau diese Positionen
    # nicht anbieten — der Mensch sucht sie und findet sie nicht
    # (260924-dua).
    from scripts.etax_daten import _zeilen_der_tabelle
    zeilen = _zeilen_der_tabelle(samples)

    def person_text(rolle: str) -> str:
        r = str(rolle or "").strip()
        if r == "familie":
            return "Familie"
        return namen.get(r, r)

    nach_formular: dict[str, dict[tuple, list[dict]]] = {}
    for z in zeilen:
        form = formular_fuer(str(z.get("zielwert") or "")) or "Übrige"
        dok = str(z.get("pdf_name") or "")
        schluessel = (konto_key(dok) or str(z.get("aussteller") or "") or dok,
                      str(z.get("person") or ""))
        nach_formular.setdefault(form, {}).setdefault(schluessel, []).append(z)

    from extractors.eintraege import (anzeige, dok_name, eingabe,
                                      iban_je_dokument, konten, schulden)

    # Summen je Ziffer aus allen Zeilen — auch denen, die unten zu
    # Eintraegen gebuendelt werden.
    gesamt: dict[str, Decimal] = {}
    for z in zeilen:
        ziffer = str(z.get("ziffer") or "")
        wert = betrag_oder_nichts(str(z.get("betrag") or "").strip())
        if wert is not None and ziffer:
            gesamt[ziffer] = gesamt.get(ziffer, Decimal(0)) + Decimal(str(wert))

    # Wertschriften und Schulden: je Eintrag der Online-Maske ein Block
    # (Bank + Kennung bzw. Glaeubiger), Felder in Maskenreihenfolge —
    # `FELDLISTE-ZHPRIVATETAX-2025.md` (261024-jga).
    def eintrag_bloecke(eintraege) -> list[dict]:
        return [{"titel": e.titel, "person": person_text(e.person),
                 "maske": e.maske, "dokumente": e.dokumente,
                 "felder": [dict(vars(f), wert=person_text(f.wert))
                            if f.titel.startswith("Zugehörigkeit")
                            else vars(f) for f in e.felder],
                 "werte": []}
                for e in eintraege]

    # Konten, Depots und Schulden aus derselben Quelle wie der Auszug.
    from extractors.eintraege import alle_eintraege
    k_alle, s_alle = alle_eintraege(samples, zeilen, _inhaber(samples), konto_key,
                                    pdf_dir=pdf_dir)

    gebuendelt = {
        "Wertschriftenverzeichnis (400)": lambda: eintrag_bloecke(k_alle),
        "Schuldenverzeichnis": lambda: eintrag_bloecke(s_alle),
    }

    formulare = []
    for form in sorted(nach_formular, key=formular_sortierung):
        if form in gebuendelt:
            formulare.append({"formular": form, "bloecke": gebuendelt[form]()})
            continue
        bloecke = []
        for schluessel in sorted(nach_formular[form],
                                 key=lambda k: (str(k[1]), str(k[0]))):
            rohzeilen = sorted(
                nach_formular[form][schluessel],
                key=lambda r: (ziffer_sortierung(str(r.get("ziffer") or "")),
                               str(r.get("zielwert") or "")))
            werte = []
            for r in rohzeilen:
                ziffer = str(r.get("ziffer") or "")
                roh = str(r.get("betrag") or "").strip()
                wert = betrag_oder_nichts(roh)
                # Gelesen, nicht durchgereicht: `21.083` ist 21'083.
                betrag = anzeige(Decimal(str(wert))) if wert is not None else ""
                werte.append({
                    "id": r.get("ablage_id") or "",
                    "ziffer": ziffer,
                    "ziffername": ziffer_name(ziffer),
                    "feld": feldcode(ziffer, str(r.get("person") or "")),
                    "bezeichnung": str(r.get("zielwert") or ""),
                    "unterscheidung": str(r.get("unterscheidung") or ""),
                    "betrag": betrag,
                    "einsetzen": (eingabe(Decimal(str(wert)))
                                  if wert is not None else ""),
                    "jahr": str(r.get("jahr") or ""),
                    # Position je (Dokument, Zielwert) aus der Tabelle — sie
                    # unterscheidet gleiche Beträge im Entscheid-Schlüssel
                    # (261025-jm6, A2c).
                    "pos": int(r.get("pos") or 0) or None,
                    # Eine Einzelposition wird IMMER eingetragen.
                    #
                    # `wird_gerechnet` gilt der Summe auf dem Hauptformular
                    # (400, 150, 250, 470), nicht der Zeile im Verzeichnis.
                    # Hier stand versehentlich dieselbe Markierung — damit
                    # haette die Leiste fuer jede Kontoposition den Knopf
                    # verweigert und behauptet, das Formular rechne sie
                    # selbst (260923-dua).
                    "gerechnet": False,
                    "quelle": str(r.get("pdf_name") or ""),
                    # regel | modell | ablage — woher der Wert kommt.
                    "herkunft": str(r.get("herkunft") or ""),
                    # Wo der Wert im Beleg steht (261025-hbv, B5).
                    "anker": _anker_der_zeile(r),
                })
            aussteller = next((str(r.get("aussteller") or "")
                               for r in rohzeilen if r.get("aussteller")), "")
            angaben = []
            if form == "Lohnausweis (100)":
                for dok in dict.fromkeys(str(r.get("pdf_name") or "")
                                         for r in rohzeilen):
                    angaben.append({
                        "quelle": dok,
                        "werte": lohnangaben(lohn.get(dok)
                                             or lohn.get(Path(dok).stem),
                                             aussteller),
                    })

            bloecke.append({
                "titel": " · ".join(
                    t for t in (aussteller, person_text(schluessel[1])) if t)
                or "Ohne Zuordnung",
                "aussteller": aussteller,
                "person": person_text(schluessel[1]),
                "angaben": angaben,
                "werte": werte,
            })
        if form in ("Versicherungsprämien", "Hilfsblatt 320"):
            # Eine leere Prämienzeile heisst: diese Komponente steht in einem
            # anderen Dokument (KVG hier, VVG dort) — kein Eintrag.
            for b in bloecke:
                b["werte"] = [w for w in b["werte"] if w.get("betrag")]
            bloecke = [b for b in bloecke if b["werte"]]
        if form == "Versicherungsprämien":
            _verbilligung_ergaenzen(samples, bloecke)
        if form == "Aufstellung fremdbetreute Kinder":
            kinder = _kinder_bloecke(samples, person_text)
            if kinder:
                gelesen = {w["quelle"] for b in kinder for w in b["werte"]}
                bloecke = [b for b in bloecke
                           if not any(dok_name(w.get("quelle")) in gelesen
                                      for w in b.get("werte") or [])] + kinder
        formulare.append({"formular": form, "bloecke": bloecke})

    _bestaetigen(samples, formulare, jahr=steuerjahr)
    alv = _alv_bloecke(samples, person_text, jahr=steuerjahr)
    # Ist das ALV-Dokument schon als Lohnausweis erfasst (Durchsicht; der Anwender:
    # „ALV ist ein Lohnausweis"), gibt es keinen zweiten Block — sonst stünde
    # der Betrag doppelt. Der Lohnblock bekommt die Gegenprobe.
    from extractors.eintraege import betrag as _b, dok_name as _dn
    lohn = next((f for f in formulare if f["formular"] == "Lohnausweis (100)"), None)
    if lohn:
        for a in list(alv):
            dok = _dn(a["werte"][0]["quelle"])
            treffer = [w for b in lohn["bloecke"] for w in b["werte"]
                       if _dn(w.get("quelle")) == dok]
            if not treffer:
                continue
            alv.remove(a)
            steuerbar = a["werte"][0]
            for w in treffer:
                info = "Wegleitung führt ALV unter Ziffer 3.3 (Feld 140/141)"
                if not steuerbar["betrag"]:
                    # Kein Betrag gelesen: kein Vergleich, sondern der Befund
                    # (261025-kme, B1a).
                    w["hinweis"] = "; ".join(x for x in (
                        w.get("hinweis", ""),
                        "ALV-Bescheinigung: steuerbarer Betrag nicht lesbar — "
                        + (steuerbar.get("hinweis") or "")) if x)
                    w["warnung"] = True
                    w["info"] = info
                elif _b(w.get("betrag")) == _b(steuerbar["betrag"]):
                    w["info"] = f"stimmt mit der ALV-Bescheinigung überein; {info}"
                else:
                    w["hinweis"] = "; ".join(x for x in (
                        w.get("hinweis", ""),
                        f"ALV-Bescheinigung: steuerbarer Betrag {steuerbar['betrag']} "
                        "— weicht ab, bitte prüfen") if x)
                    w["warnung"] = True
                    w["info"] = info
    if alv:
        formulare.append({"formular": ALV_FORMULAR, "bloecke": alv})

    # Werte, deren Dokument nicht mehr im Ordner liegt: sichtbar machen.
    # Sie können ein Doppel sein — oder der einzige Beleg (eine bestätigte
    # Prämie, deren PDF gelöscht wurde). Entscheiden kann nur der Mensch;
    # still entfernt wird nichts (261024-jga, make verwaiste).
    from extractors.eintraege import _laufdokumente
    laufdoks = _laufdokumente(samples, pdf_dir)
    _verwaiste_markieren(formulare, laufdoks)

    ohne_wert = _entscheide_anwenden(samples, formulare, laufdoks)

    summen = [{"ziffer": z, "ziffername": ziffer_name(z),
               "betrag": anzeige(gesamt[z]), "einsetzen": eingabe(gesamt[z]),
               "feld": feldcode(z),
               "gerechnet": wird_gerechnet(z)}
              for z in sorted(gesamt, key=ziffer_sortierung)]
    _familien_totale(formulare, summen)

    formulare, summen = _durchgehend_mit_apostroph(formulare), \
        _durchgehend_mit_apostroph(summen)
    return {
        "erzeugt": datetime.now().isoformat(timespec="seconds"),
        "positionen": len(zeilen),
        "formulare": formulare,
        "summen": summen,
        # Entscheidungen, die keinen Wert mehr treffen (261025-jm6, A2d).
        "entscheide_ohne_wert": ohne_wert,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--samples", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--pdf-dir", type=Path, default=None,
                    help="echter PDF-Ordner — „verwaist“ heisst dann: nicht mehr im Ordner")
    ap.add_argument("--jahr", default=None,
                    help="Steuerjahr; ohne Angabe das Mehrheitsjahr des Laufs")
    args = ap.parse_args()

    from extractors.entscheide import EntscheideDefekt
    try:
        daten = baue(args.samples, pdf_dir=args.pdf_dir, jahr=args.jahr)
    except FileNotFoundError:
        # Ohne Pfad: er kann den Benutzernamen enthalten.
        print("PDF-Ordner (--pdf-dir) nicht gefunden — PDFDIR=… setzen.")
        return 1
    except EntscheideDefekt as e:
        print(e)
        return 1
    ziel = args.out or args.samples.parent / "steuerwerte.json"
    # Bestandsschutz 2025 (261025-kme): der vorige Stand in den Verlauf.
    from scripts.verlauf import kopie_in_verlauf
    kopie_in_verlauf(ziel, ziel.parent / "steuerwerte-verlauf")
    ziel.write_text(json.dumps(daten, indent=2, ensure_ascii=False),
                    encoding="utf-8")

    print("Werte für die Erweiterung — nur Zahlen, dieser Teil ist teilbar")
    print(TRENNER)
    print(f"   {daten['positionen']:3}  Positionen")
    for f in daten["formulare"]:
        # Eine Kategorie kann beides haben: einen Formularblock mit Feldern
        # (Total für alle) und Einzelwerte darunter (261028).
        eintraege = [b for b in f["bloecke"] if b.get("felder")]
        einzeln = [b for b in f["bloecke"] if not b.get("felder")]
        teile = []
        if eintraege:
            luecken = sum(1 for b in eintraege for x in b["felder"]
                          if x.get("herkunft") in ("fehlt", "widerspruch"))
            teile.append(f"{len(eintraege)} Eintrag/Einträge, {luecken} "
                         "fehlende/widersprüchliche Felder")
        if einzeln:
            anzahl = sum(len(b.get("werte") or []) for b in einzeln)
            im_total = sum(1 for b in einzeln for w in b.get("werte") or []
                           if w.get("im_total"))
            teile.append(f"{anzahl} Wert(e) in {len(einzeln)} Block/Blöcken"
                         + (f", davon {im_total} im Total" if im_total else ""))
        print(f"      {f['formular']}:  " + "; ".join(teile))
    lohn = [w for f in daten["formulare"] for b in f["bloecke"]
            for a in b.get("angaben") or [] for w in a["werte"]]
    if lohn:
        print(f"   {sum(1 for w in lohn if w['wert']):3}  von {len(lohn)} "
              f"Lohnangaben (Arbeitgeber, Anstellung von/bis) gefunden, "
              f"{sum(1 for w in lohn if w['herkunft'] == 'offen' and w['wert'])}"
              f" davon ohne Nachweis")
    gerechnet = sum(1 for s in daten["summen"] if s["gerechnet"])
    print(f"   {len(daten['summen']):3}  Ziffern, davon {gerechnet} vom "
          f"Formular selbst gerechnet")
    print(f"\n→ {ziel}")
    print("\nDiese Datei enthält echte Beträge. Sie bleibt lokal; die "
          "Erweiterung\nliest sie vom Rechner und sendet nichts.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
