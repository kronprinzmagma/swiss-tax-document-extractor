#!/usr/bin/env python3
"""Liest die korrigierte ``korrektur.csv`` zurück — der Rückweg des Zirkels.

Ablauf:

1. ``build_tax_output.py`` schreibt ``korrektur.csv`` mit einer leeren Spalte
   ``korrektur`` und einer leeren Spalte ``notiz``.
2. Der Mensch trägt dort den richtigen Wert ein, wo einer fehlt oder falsch
   ist — und in ``notiz`` optional die Beschriftung, unter der der Wert im
   Beleg steht ("Ihr Anteil", "Franchise", …).
3. Dieses Skript schreibt daraus ``korrekturen.json``: der bestätigte Sollwert
   pro (Beleg, Zielwert). Künftige Läufe können dagegen prüfen, und eine
   Abweichung faellt sofort auf.

Warum Korrekturen wirken, ohne dass ein Modell trainiert wird: die ``notiz``
verraet die *Beschriftung*, unter der der richtige Wert steht. Daraus wird eine
Regel in ``regex_extract.py``, die ab dann bei jedem Dokument dieses Formats
greift — nachpruefbar und ohne dass Daten das Geraet verlassen.

Privacy: die Zusammenfassung auf der Konsole enthaelt nur Zaehlungen,
Feldnamen und die notierten Beschriftungen — niemals einen Betrag. Die
``korrekturen.json`` enthaelt Werte und gehoert deshalb neben die uebrigen
Outputs in ein gitignoriertes Verzeichnis.

Aufruf::

    PYTHONPATH=. .venv/bin/python scripts/apply_korrekturen.py \
        --file output/latest/korrektur.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def lies_korrekturen(csv_pfad: Path) -> list[dict]:
    """Zeilen mit ausgefuellter Korrektur- oder Notiz-Spalte."""
    # Jede Aenderung hat eine eigene Spalte. Fruehere Fassungen packten
    # Person und Aussteller als Fliesstext in die Notiz — beim Zurueckspielen
    # ging das verloren, es kam nur der Betrag an.
    aenderbar = ("korrektur", "person_neu", "aussteller_neu", "zielwert_neu",
                 "ziffer_neu", "bestaetigt", "streichen", "zuruecknehmen",
                 "unterscheidung", "notiz")
    text = csv_pfad.read_text(encoding="utf-8")

    # Rettung fuer CSVs, die als eine einzige Zeile mit literalen "\n"
    # herauskamen. Der Export schrieb den Zeilentrenner eine Zeit lang als
    # Backslash-n statt als Umbruch; die Datei sah heil aus, hatte aber genau
    # eine Zeile — den Kopf. Ergebnis: "nichts zu tun", obwohl die ganze
    # Durchsicht darin stand (260904-rmx).
    if text.count("\n") <= 1 and "\\n" in text:
        text = text.replace("\\n", "\n")
        print("Hinweis: CSV enthielt literale \\n als Zeilentrenner — repariert.",
              file=sys.stderr)

    zeilen = list(csv.DictReader(text.splitlines(True)))
    return [r for r in zeilen
            if any((r.get(sp) or "").strip() for sp in aenderbar)]


def _beide_ablagen(ziel: Path) -> list[Path]:
    """Die zwei Orte, an denen ``korrekturen.json`` liegen kann.

    ``output/latest/korrekturen.json`` (CSV-Weg) und
    ``output/latest/json/korrekturen.json`` (Live-Server). Welcher von beiden
    ``ziel`` ist, haengt vom Aufrufer ab — die Liste enthaelt immer beide, in
    fester Reihenfolge: erst der aeussere, dann der innere. Der innere gewinnt
    beim Zusammenfuehren, weil dort die laufende Sitzung schreibt.
    """
    name = "korrekturen.json"
    if ziel.parent.name == "json":
        return [ziel.parent.parent / name, ziel.parent / name]
    return [ziel.parent / name, ziel.parent / "json" / name]


def _zusammenfuehren(ablagen: list[Path]) -> dict[str, dict]:
    """Beide Ablagen feldweise zu einem Stand — dieselbe Regel wie lade_alle."""
    from extractors.korrekturen import lade

    zusammen: dict[str, dict] = {}
    for pfad in ablagen:
        for schluessel, eintrag in lade(pfad).items():
            if not isinstance(eintrag, dict):
                continue
            vorher = zusammen.get(schluessel)
            if not isinstance(vorher, dict):
                zusammen[schluessel] = dict(eintrag)
                continue
            neu = dict(vorher)
            for feld, wert in eintrag.items():
                if wert in (None, "") and feld in neu:
                    continue
                neu[feld] = wert
            zusammen[schluessel] = neu
    return zusammen


def _sichere(ziel: Path, behalten: int = 20, *,
             dauerhaft: bool = False) -> None:
    """Eine Kopie zur Seite legen, bevor korrekturen.json ueberschrieben wird.

    Diese Datei ist die gesamte Durchsicht — Stunden Arbeit, und sie wird bei
    jedem Speichern neu geschrieben. Ein Fehler darin hat sie einmal komplett
    entleert. Backups kosten Kilobytes und haetten das aufgefangen.

    Aeltere Staende bleiben liegen, die letzten ``behalten`` Stueck; der Rest
    wird aufgeraeumt.

    ``dauerhaft=True`` nimmt diesen Stand vom Aufraeumen aus. Der Server sagt
    beim Start zu, man koenne auf den Stand von vorher zurueck — und genau
    dieser Punkt wurde danach von den eigenen Auto-Saves aus dem Ringpuffer
    gedraengt: zwanzig Tastendruecke, und die Zusage war nichts mehr wert
    (260923-dua). An ``behalten`` zu drehen verschiebt das nur; jede Zahl
    wird irgendwann erreicht.

    Zwei Sicherungen in derselben Sekunde ueberschrieben einander — beim
    Live-Speichern ist das der Normalfall, nicht die Ausnahme. Deshalb bekommt
    die zweite ein Suffix.
    """
    if not ziel.exists():
        return
    from datetime import datetime
    ordner = ziel.parent / "korrekturen-verlauf"
    try:
        ordner.mkdir(exist_ok=True)
        marke = datetime.now().strftime("%Y%m%d-%H%M%S")
        if dauerhaft:
            marke += "-start"
        pfad = ordner / f"korrekturen-{marke}.json"
        n = 1
        while pfad.exists():
            n += 1
            pfad = ordner / f"korrekturen-{marke}_{n}.json"
        pfad.write_bytes(ziel.read_bytes())
        # Markierte Staende zaehlen nicht gegen das Kontingent und werden nie
        # weggeraeumt.
        alt = sorted(p for p in ordner.glob("korrekturen-*.json")
                     if "-start" not in p.stem)
        for weg in alt[:-behalten]:
            weg.unlink()
    except OSError:
        pass   # Ein fehlgeschlagenes Backup darf das Speichern nie verhindern


def uebernehme(zeilen: list[dict], ziel: Path,
               ersetzen: bool = False) -> dict:
    """Traegt die Zeilen in ``korrekturen.json`` ein und schreibt sie.

    Eigene Funktion, weil es zwei Wege hierher gibt: die ausgefuellte CSV und
    die Live-Oberflaeche, die jede Aenderung sofort schickt. Beide muessen
    dasselbe tun — zwei Auswertungen derselben Spalten waeren zwei
    Gelegenheiten, unterschiedlich zu sein.

    ``ersetzen`` entscheidet, was eine Zeile ueber die Felder aussagt, die sie
    NICHT mitbringt:

    * ``False`` (CSV-Weg) — sie sagt nichts darueber. Vorhandene Felder
      bleiben stehen. Anders herum ging Arbeit verloren: eine Zeile, die nur
      eine Bestaetigung mitbrachte, loeschte die zuvor gespeicherte Person,
      weil der Eintrag komplett neu geschrieben wurde.
    * ``True`` (Live-Weg) — sie ist vollstaendig. Die Oberflaeche schickt
      jede Zeile mit ihrem ganzen Zustand; was fehlt, wurde zurueckgenommen.
      Nur so lassen sich eine Korrektur, eine Bestaetigung oder eine
      Streichung ueberhaupt wieder aufheben.

    Returns eine Zusammenfassung (nur Zaehlungen und Feldnamen, keine
    Betraege) fuer die Konsole beziehungsweise die Antwort an den Browser.
    """
    # Die Durchsicht liegt an zwei Orten: der CSV-Weg schreibt neben die CSV,
    # der Live-Server in den Sample-Ordner. Gelesen wurde bisher nur EINER —
    # ein Eintrag, den nur die andere Ablage kannte, wurde beim Speichern als
    # „neu" behandelt und verlor dabei alles, was die Oberflaeche nicht
    # mitschickt: die notierte Beschriftung, den Ursprung, aeltere
    # Bestaetigungen (260923-dua).
    #
    # Deshalb: aus BEIDEN zusammenfuehren, in BEIDE schreiben. Danach sind sie
    # Zeichen fuer Zeichen gleich, und die ganze Klasse von Fehlern, die aus
    # ihrem Auseinanderlaufen entstand, ist weg.
    ablagen = _beide_ablagen(ziel)
    bestand = dict(_zusammenfuehren(ablagen))
    # Der Stand VOR dieser Aenderung — Vergleichsgrundlage fuer die Schranke
    # weiter unten. Eine flache Kopie genuegt: die Eintraege werden ersetzt,
    # nicht im Innern veraendert.
    bestand_vorher = dict(bestand)
    # Schluessel, die unterwegs planmaessig umgehaengt werden (Umbenennung
    # eines Zielwerts). Sie verschwinden an der alten Stelle und tauchen an
    # der neuen auf — kein Verlust, aber ohne diese Liste meldet die Schranke
    # einen (260923-dua).
    verschoben: set[str] = set()

    neu = geaendert = 0
    labels: Counter[str] = Counter()
    felder: Counter[str] = Counter()

    from extractors.korrekturen import (SchutzVerletzt, baue_schluessel,
                                        pruefe_schutz,
                                        schluessel_kandidaten)

    for r in zeilen:
        # Unter dem Schluessel schreiben, unter dem der Eintrag schon liegt.
        #
        # Die Oberflaeche schickt die AKTUELLE Bezeichnung. Wurde ein Zielwert
        # umbenannt, entstuende darunter ein zweiter Eintrag, waehrend der
        # alte liegenbleibt — und `ergaenze_eigene_zeilen` haengt dann beide
        # an die Tabelle: derselbe Betrag zweimal in der Steuererklaerung
        # (260904-rmx).
        #
        # ``position`` unterscheidet mehrere Positionen derselben Art in einem
        # Dokument — drei Hypotheken auf einem Beleg (260923-dua). Die Spalte
        # heisst bewusst nicht ``pos``: ``pos_x``/``pos_y`` halten daneben die
        # angeklickte Stelle im Seitenbild fest.
        pos = r.get("position") or r.get("pos") or 1
        schluessel = baue_schluessel(r["beleg"], r["zielwert"], pos)
        if schluessel not in bestand:
            for kandidat in schluessel_kandidaten(r["beleg"], r["zielwert"],
                                                  pos):
                if kandidat in bestand:
                    schluessel = kandidat
                    break

        # Umbenennung: den Eintrag MITNEHMEN, nicht einen zweiten anlegen.
        #
        # Bisher blieb der alte Schluessel liegen. Er zeigte danach auf eine
        # Bezeichnung, die es im Lauf nicht mehr gibt, und meldete sich in
        # Bereitschafts-Punkt 8 und Grader-Check 20 fuer immer als „fehlt" —
        # zwei Pruefungen, die nach jeder Umbenennung nie wieder gruen wurden,
        # obwohl der Mensch die Arbeit erledigt hatte (260923-dua).
        neue_bez = (r.get("zielwert_neu") or "").strip()
        if neue_bez and neue_bez != r["zielwert"]:
            ziel_schluessel = baue_schluessel(r["beleg"], neue_bez, pos)
            if ziel_schluessel != schluessel:
                vorher = bestand.pop(schluessel, None)
                verschoben.add(schluessel)
                if isinstance(vorher, dict):
                    zusammen = dict(vorher)
                    zusammen.update(bestand.get(ziel_schluessel) or {})
                    bestand[ziel_schluessel] = zusammen
                schluessel = ziel_schluessel
        wert = (r.get("korrektur") or "").strip()
        notiz = (r.get("notiz") or "").strip()
        eintrag = {"zielwert": r["zielwert"], "ziffer": r.get("ziffer", "")}
        # Gestrichen: DIESES Dokument weist den Wert nicht aus — er wurde
        # faelschlich darin erkannt. Das ist weder eine Korrektur des Betrags
        # noch die Aussage, der Wert gehoere nicht in die Steuererklaerung:
        # eine Zusatzversicherung, die in der Abrechnung der Grundversicherung
        # auftaucht, existiert durchaus — nur bei einer anderen Kasse und in
        # einem anderen Dokument. Die Vollstaendigkeitspruefung fordert sie
        # deshalb weiter ein.
        if (r.get("streichen") or "").strip():
            # Ergaenzen, nicht ersetzen. Die erste Fassung schrieb hier ein
            # frisches dict — eine Streichung loeschte damit den bestaetigten
            # Betrag, die Person und die notierte Beschriftung gleich mit. Und
            # das Zuruecknehmen ("↺ zurueck") liess einen leeren Eintrag
            # uebrig, weil es nichts mehr zu ergaenzen gab (260904-rmx).
            zusammen = dict(bestand.get(schluessel) or {})
            zusammen.update(eintrag)
            zusammen["entfernt"] = True
            zusammen["grund"] = "nicht_im_dokument"
            if schluessel not in bestand:
                neu += 1
            elif bestand[schluessel] != zusammen:
                geaendert += 1
            bestand[schluessel] = zusammen
            felder[r["zielwert"]] += 1
            continue
        if ersetzen:
            # Nicht gestrichen und die Zeile ist vollstaendig — also ist eine
            # fruehere Streichung aufgehoben. Ausdruecklich als ``False``
            # festhalten, nicht durch Weglassen: Abwesenheit ueberlebt keine
            # Zusammenfuehrung. Eine aeltere Sicherung, die die Streichung
            # noch kennt, haette sie sonst wieder auferweckt (260923-dua).
            eintrag["entfernt"] = False
            eintrag["grund"] = ""
        if wert:
            eintrag["soll"] = wert
        if notiz:
            eintrag["label"] = notiz
            labels[notiz] += 1
        # Selbst erfasste Position: die Oberflaeche markiert sie mit "NEU".
        # Sie hat keine Entsprechung im Beleg-Ergebnis und muss beim naechsten
        # Tabellenbau als eigene Zeile angelegt werden.
        # Position und automatisch abgeleiteter Zeilenkontext: das eigentliche
        # Material fuer eine neue Extraktionsregel. Wo im Dokument steht der
        # richtige Wert, und was steht links davon? Beides kommt aus dem Klick
        # im Seitenbild, nicht aus Tipparbeit.
        for feld, spalte in (("pos_x", "pos_x"), ("pos_y", "pos_y"),
                             ("pos_seite", "pos_seite"),
                             ("kontext", "zeilenkontext")):
            wert_ = (r.get(spalte) or "").strip()
            if wert_:
                eintrag[feld] = wert_
                if feld == "kontext":
                    labels[wert_] += 1
        # Bestaetigungen sind genauso wertvoll wie Korrekturen: sie halten
        # fest, dass ein Mensch den Wert im Original geprueft und fuer richtig
        # befunden hat. Gingen sie verloren, muesste die ganze Durchsicht bei
        # jedem Lauf wiederholt werden — bei 33 von 47 Eintraegen der
        # Loewenanteil der Arbeit.
        # Eine Bestaetigung haelt den WERT fest, nicht nur die Tatsache.
        #
        # Vorher stand hier nur ein Flag, und das wurde ausschliesslich
        # benutzt, um in der Oberflaeche das Haekchen vorzusetzen. Auf die
        # Tabelle wirkte es nicht. Da das Sprachmodell nicht deterministisch
        # ist, lieferte derselbe Beleg im naechsten Lauf einen anderen Wert
        # oder gar keinen — und 46 Bestaetigungen verhinderten davon nichts:
        # die Personenzuordnungen der Krankenkassenbelege waren nach einem
        # erneuten `make productive` weg, obwohl niemand etwas geaendert
        # hatte (260904-rmx).
        best = (r.get("bestaetigt") or "") + " " + notiz
        if "Betrag" in best:
            eintrag["bestaetigt_betrag"] = True
            gepruefter_betrag = (r.get("betrag") or "").strip()
            if gepruefter_betrag:
                eintrag["wert_betrag"] = gepruefter_betrag
        if "Person" in best:
            eintrag["bestaetigt_person"] = True
            gepruefte_person = (r.get("person_neu") or r.get("person") or "").strip()
            if gepruefte_person:
                eintrag["wert_person"] = gepruefte_person
        # Strukturierte Aenderungen — je eine eigene Spalte.
        for feld, spalte in (("person", "person_neu"),
                             ("aussteller", "aussteller_neu"),
                             ("zielwert_neu", "zielwert_neu"),
                             ("ziffer_neu", "ziffer_neu")):
            v = (r.get(spalte) or "").strip()
            if v:
                eintrag[feld] = v

        # Unterscheidung: der kurze Zusatz, der drei Hypotheken eines Belegs
        # auseinanderhaelt ("Festhypothek 0.95 %").
        #
        # Bewusst NICHT im Feld ``label`` — das haelt die notierte Beschriftung
        # aus dem Beleg fest und ist der Eingang des Optimierungszirkels.
        # Begrenzt und gegen die PII-Muster geprueft: der Zusatz landet in der
        # Uebertragungstabelle, und dort hat eine Policennummer nichts
        # verloren (260923-dua).
        unt = (r.get("unterscheidung") or "").strip()[:40]
        if unt:
            from extractors.pii_patterns import enthaelt_pii
            if enthaelt_pii(unt):
                print("Hinweis: Unterscheidung enthält personenbezogene Daten "
                      "und wurde nicht übernommen.", file=sys.stderr)
            else:
                eintrag["unterscheidung"] = unt

        # Rueckwaertskompatibel: aeltere Fassungen der Oberflaeche schrieben
        # Person und Aussteller als Fliesstext in die Notiz ("Person: xyz").
        # Wer eine solche CSV noch hat, soll seine Arbeit nicht verlieren.
        for feld, praefix in (("person", "Person:"), ("aussteller", "Aussteller:")):
            if feld in eintrag:
                continue
            for teil in notiz.split("|"):
                teil = teil.strip()
                if teil.startswith(praefix):
                    wert_alt = teil[len(praefix):].strip()
                    if wert_alt:
                        eintrag[feld] = wert_alt
                    break
        if notiz.startswith("NEU"):
            eintrag["neu"] = True
            for feld, spalte in (("person", "person"),
                                 ("aussteller", "aussteller")):
                if (r.get(spalte) or "").strip():
                    eintrag[feld] = r[spalte].strip()
        # IMMER feldweise ergaenzen — auch im Live-Betrieb.
        #
        # ``ersetzen`` hiess urspruenglich „die Zeile ist vollstaendig, was
        # fehlt, wurde zurueckgenommen". In der Praxis schickt die Oberflaeche
        # jede Zeile bei jedem Speichern, und eine Zeile ohne Haekchen sieht
        # genauso aus wie eine, deren Haekchen der Mensch entfernt hat. Aus
        # dieser Verwechslung wurde geloescht — die ganze Durchsicht, beim
        # blossen Oeffnen der Seite (260904-rmx).
        #
        # Der Preis der neuen Regel: eine zurueckgenommene Bestaetigung muss
        # ausdruecklich gemeldet werden (Spalte ``zuruecknehmen``). Das ist
        # ein kleiner Preis gegen den Verlust von Stunden Arbeit.
        if schluessel in bestand and isinstance(bestand[schluessel], dict):
            zusammen = dict(bestand[schluessel])
            zusammen.update(eintrag)
            if ersetzen:
                # Nicht gestrichen und ausdruecklich geschickt → Streichung
                # weg. Als Wert, nicht als Luecke (siehe oben).
                zusammen["entfernt"] = False
                zusammen["grund"] = ""
            eintrag = zusammen

        # Zuruecknehmen nur auf ausdrueckliche Ansage.
        #
        # Hier stand: „ein Eintrag, der nichts mehr festhaelt, gehoert weg".
        # Gedacht war das fuer eine zurueckgenommene Korrektur. Tatsaechlich
        # hat es die Durchsicht geloescht: nachdem eine Zielwert-Bezeichnung
        # umbenannt wurde, fanden die Haekchen ihren Eintrag nicht mehr, die
        # Oberflaeche schickte die Zeile als leer — und die Regel entfernte
        # den Eintrag. Beim blossen OEFFNEN der Seite, ohne dass jemand etwas
        # angeruehrt hatte (260904-rmx).
        #
        # Eine Regel, die aus Schweigen „loeschen" macht, ist in einem System
        # falsch, dessen Kern „nichts darf vergessen gehen" ist. Geloescht
        # wird nur noch, wenn die Zeile es ausdruecklich sagt.
        if (r.get("zuruecknehmen") or "").strip():
            # Einen Grabstein setzen statt den Schluessel zu entfernen.
            #
            # Ein entfernter Schluessel ist Abwesenheit, und Abwesenheit
            # ueberlebt keine Zusammenfuehrung: die zweite Ablage oder eine
            # Sicherung im Verlauf brachte den Eintrag zurueck, und
            # `make korrekturen-zurueck` machte jede Ruecknahme zunichte
            # (260923-dua). Der Grabstein sagt ausdruecklich „das wollte ich
            # nicht mehr" und gewinnt damit gegen jede aeltere Quelle.
            if schluessel in bestand:
                geaendert += 1
            bestand[schluessel] = {"zurueckgenommen": True,
                                   "zielwert": r["zielwert"]}
            continue

        # Eine Zeile ohne jeden Inhalt legt keinen Eintrag an.
        #
        # Im Live-Betrieb schickt die Oberflaeche JEDE Zeile, auch die
        # unberuehrte. Daraus entstand fuer jede davon eine leere Huelse mit
        # nichts als Zielwert und Ziffer — der Bauplan der Tabellenzeile, den
        # die Tabelle ohnehin kennt. Sie blaehten die Datei auf und liessen
        # `make bereit` mehr „Eintraege geprueft" melden, als es Durchsicht
        # gab (260923-dua).
        traegt_etwas = any(
            eintrag.get(f) not in (None, "", False)
            for f in ("soll", "person", "aussteller", "zielwert_neu",
                      "ziffer_neu", "bestaetigt_betrag", "bestaetigt_person",
                      "wert_betrag", "wert_person", "label", "kontext",
                      "unterscheidung", "neu", "entfernt"))
        if not traegt_etwas and schluessel not in bestand:
            continue

        if schluessel in bestand and bestand[schluessel] != eintrag:
            geaendert += 1
        elif schluessel not in bestand:
            neu += 1
        bestand[schluessel] = eintrag
        felder[r["zielwert"]] += 1

    # Die Schranke: nichts Bestaetigtes geht still verloren.
    #
    # Das ist die einzige Stelle, an der Korrekturen auf die Platte gehen.
    # Faellt hier etwas Bestaetigtes weg, das niemand zurueckgenommen hat, ist
    # das ein Fehler im Programm — und wird nicht geschrieben. Lieber ein
    # abgebrochener Speichervorgang als eine Tabelle, die stillschweigend
    # kuerzer wird (260923-dua).
    verloren = [k for k in pruefe_schutz(bestand_vorher, bestand)
                if k not in verschoben]
    if verloren:
        raise SchutzVerletzt(verloren)

    text = json.dumps(bestand, indent=2, ensure_ascii=False,
                      sort_keys=True) + "\n"
    for ablage in ablagen:
        # Nur anlegen, wo schon eine liegt — oder am eigentlichen Ziel. Sonst
        # streute jeder Lauf eine zweite Datei in einen Ordner, der sie nie
        # hatte.
        if ablage == ziel or ablage.exists():
            _sichere(ablage)
            ablage.write_text(text)

    return {"gelesen": len(zeilen), "neu": neu, "geaendert": geaendert,
            "gesamt": len(bestand), "ziel": str(ziel),
            "felder": dict(felder), "labels": dict(labels)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--file", type=Path, required=True,
                    help="Die ausgefuellte korrektur.csv")
    ap.add_argument("--out", type=Path, default=None,
                    help="Zieldatei (Default: korrekturen.json neben der CSV)")
    args = ap.parse_args()

    if not args.file.exists():
        print(f"Nicht gefunden: {args.file}", file=sys.stderr)
        return 1

    zeilen = lies_korrekturen(args.file)
    if not zeilen:
        print("Keine Korrekturen eingetragen — nichts zu tun.")
        return 0

    bericht = uebernehme(zeilen, args.out or args.file.parent / "korrekturen.json")
    felder = Counter(bericht["felder"])
    labels = Counter(bericht["labels"])

    print(f"{bericht['gelesen']} Korrektur(en) gelesen — {bericht['neu']} neu, "
          f"{bericht['geaendert']} geaendert.")
    print(f"Geschrieben: {bericht['ziel']}\n")
    print("Betroffene Zielwerte (nur Feldnamen, keine Betraege):")
    for feld, n in felder.most_common():
        print(f"   {n:3}×  {feld}")
    if labels:
        print("\nZeilenkontexte der markierten Werte — daraus werden Regeln:")
        for label, n in labels.most_common():
            print(f"   {n:3}×  {label!r}")
        print("\nDiese Liste ist teilbar: sie enthaelt keine Betraege und keine Namen.")
    else:
        print("\nHinweis: ohne Markierung im Dokument laesst sich keine Regel "
              "ableiten — nur der Sollwert fuer die Regressionspruefung.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
