#!/usr/bin/env python3
"""Erzeugt eine lokale HTML-Oberfläche zum Bestätigen und Korrigieren.

Warum lokal und nicht als geteilte Seite: die Datei enthält echte Beträge,
Namen und Aussteller. Sie wird in ``output/`` geschrieben (gitignored), öffnet
sich per Doppelklick im Browser und **baut keine einzige Netzwerkverbindung
auf** — kein CDN, keine Schrift von aussen, kein Bild. Alles ist in die Datei
eingebettet. Sie darf nicht hochgeladen und nicht veröffentlicht werden.

Was die Seite kann:

* Zeilen nach Konfidenz filtern — unsichere zuerst, denn dort lohnt der Blick.
* Belegtyp und Person per Dropdown korrigieren (die häufigsten Lücken).
* Beträge direkt überschreiben oder mit einem Klick bestätigen.
* Die Beschriftung notieren, unter der der Wert im Beleg steht — daraus wird
  später eine Extraktionsregel.
* Am Ende eine CSV herunterladen, die ``apply_korrekturen.py`` einliest.

Aufruf::

    PYTHONPATH=. .venv/bin/python scripts/build_review_html.py \
        --samples output/latest/json --out output/latest/review.html
"""
from __future__ import annotations

from decimal import Decimal
import argparse
import html
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from extractors.vollstaendigkeit import (
    ALLE_ROLLEN, pruefe_alles, regelwerk, rolle_aus_person,
    rollen_aus_family,
)
from extractors.zielwerte import ZIELWERTE, ZIFFERN

BELEGTYPEN = sorted(ZIELWERTE)
# "irrelevant" ist eine gueltige Antwort: nicht jedes Dokument im Ordner
# gehoert in die Steuererklaerung. Aktiv festhalten schlaegt vergessen.
BELEGTYP_WAHL = BELEGTYPEN + ["irrelevant"]

# Bezeichnungen, die als Zielwert gewaehlt werden koennen. Aus dem Vertrag,
# ergaenzt um Faelle, fuer die es (noch) kein Extraktionsfeld gibt — etwa die
# getrennten Ertragsspalten des Wertschriftenverzeichnisses.
ZIELWERT_WAHL: list[str] = sorted({z.bezeichnung
                                   for werte in ZIELWERTE.values()
                                   for z in werte})

# Welche Ziffer gehoert zu welchem Zielwert? Aus dem Feldvertrag, nicht aus
# einer zweiten Liste — sonst driften Tabelle und Oberflaeche auseinander.
#
# Ohne das war der Zielwert zwar aenderbar, die Ziffer aber nicht: wer eine
# falsch erkannte Zeile auf "Einzahlung Säule 3a" umstellte, liess sie unter
# Ziffer 30.1 stehen. Der Wert landete damit im Vermoegen statt bei den
# Abzuegen (260904-rmx).
ZIELWERT_ZIFFER: dict[str, str] = {z.bezeichnung: z.ziffer
                                   for werte in ZIELWERTE.values()
                                   for z in werte}

# Welcher Belegart gehoert ein Zielwert an? Damit die Gruppierung dem folgt,
# was der Mensch gesetzt hat: wer eine Zeile auf „Prämie Zusatzversicherung
# VVG" stellt, sucht sie unter Krankenkasse — nicht unter der Belegart des
# Dokuments, aus dem sie zufaellig stammt (260904-rmx).
ZIELWERT_TYP: dict[str, str] = {z.bezeichnung: belegtyp
                                for belegtyp, werte in ZIELWERTE.items()
                                for z in werte}


# Lesbare Ueberschriften fuer die Gruppierung nach Belegtyp.
TYP_LABEL: dict[str, str] = {
    "lohnausweis": "Lohnausweise",
    "bank_zinsausweis": "Bankbelege",
    "wertschriftenverzeichnis": "Wertschriften & Depots",
    "kk_praemienbescheinigung": "Krankenkasse",
    "hypothek_zinsbestaetigung": "Hypotheken",
    "krankheitskosten": "Krankheitskosten",
    "saeule_3a": "Säule 3a",
    "kinderbetreuung": "Kinderbetreuung",
}


def rollen_optionen() -> list[str]:
    """Erlaubte Rollen, wenn moeglich mit den echten Vornamen beschriftet."""
    basis = ["elternteil_1", "elternteil_2", "kind_1", "kind_2", "familie",
             "unbekannt/manuell"]
    try:
        from extractors.family import load_family
        fmly = load_family(ROOT / "family.yaml")
    except Exception:
        return basis
    if fmly is None:
        return basis
    rollen_map = {"mann": "elternteil_1", "frau": "elternteil_2",
                  "kind1": "kind_1", "kind2": "kind_2"}
    labels: dict[str, str] = {}
    for m in fmly.members:
        r = rollen_map.get(m.role)
        if r:
            labels[r] = f"{r} ({m.first_name})"
    return [labels.get(r, r) for r in basis]


def _crop_datauri(pdf_pfad: Path, seite: int, bbox, rand: int = 7,
                  dpi: int = 110) -> str | None:
    """Rendert die Zeile um den Wert als PNG-Data-URI, Wert rot umrandet.

    Statt „Dokument öffnen und Betrag suchen" sieht man den Wert direkt in
    seiner Originalzeile — mitsamt dem Text links davon, an dem sich erkennen
    lässt, ob es die richtige Zeile ist. Das ersetzt das Suchen durch Hinsehen.
    """
    if not pdf_pfad.exists() or not bbox or len(bbox) < 4:
        return None
    try:
        import base64
        import io as _io

        import pdfplumber
        x0, top, x1, bottom = (float(v) for v in bbox[:4])
        with pdfplumber.open(pdf_pfad) as pdf:
            if seite < 1 or seite > len(pdf.pages):
                return None
            pg = pdf.pages[seite - 1]
            oben = max(0, top - rand)
            unten = min(float(pg.height), bottom + rand)
            aus = pg.crop((0, oben, float(pg.width), unten))
            bild = aus.to_image(resolution=dpi)
            bild.draw_rect((x0, top, x1, bottom), stroke="red",
                           stroke_width=2, fill=None)
            puffer = _io.BytesIO()
            bild.save(puffer, format="PNG")
            return ("data:image/png;base64,"
                    + base64.b64encode(puffer.getvalue()).decode("ascii"))
    except Exception:
        return None


def _pdf_link(pdf_dir: Path, pdf_name: str, seite, live: bool = False) -> str:
    """Link auf das Original, wenn möglich mit Seitensprung.

    Zwei Formen, je nachdem woher die Seite kommt:

    * Als Datei geöffnet (``file://``) zeigt der Link direkt aufs PDF.
    * Hinter dem lokalen Server muss er über den Server gehen. Browser
      verweigern jede Navigation von einer ``http://``-Seite zu ``file://`` —
      das ist eine Sicherheitsregel, kein Fehler. Seit die Review über
      localhost läuft, waren die Links deshalb tot (260904-rmx).
    """
    if not pdf_name:
        return ""
    p = pdf_dir / pdf_name
    if not p.exists():
        return ""
    if live:
        from urllib.parse import quote
        ziel = "/pdf/" + quote(pdf_name)
    else:
        ziel = p.resolve().as_uri()
    try:
        return f"{ziel}#page={int(seite)}" if seite else ziel
    except (TypeError, ValueError):
        return ziel


def _seite_mit_wortboxen(json_doc: dict, pdf_pfad: Path, seiten_nr: int = 1,
                         dpi: int = 100) -> dict | None:
    """Seitenbild plus die Wortboxen, auf das Bild skaliert.

    Damit lassen sich die Wörter im Browser direkt anklicken: der markierte
    Wert ist dann exakt der Text aus dem PDF, nicht abgetippt und nicht aus
    einem Screenshot erraten. Die Koordinaten liegen ohnehin vor — sie sind
    die Grundlage der Anker-Prüfung.
    """
    if not pdf_pfad.exists():
        return None
    try:
        import base64
        import io as _io

        import pdfplumber
        seite = next((p for p in json_doc.get("pages", [])
                      if p.get("page_num") == seiten_nr), None)
        if seite is None:
            return None
        with pdfplumber.open(pdf_pfad) as pdf:
            if seiten_nr > len(pdf.pages):
                return None
            pg = pdf.pages[seiten_nr - 1]
            bild = pg.to_image(resolution=dpi)
            puffer = _io.BytesIO()
            bild.save(puffer, format="PNG")
            roh = base64.b64encode(puffer.getvalue()).decode("ascii")
        faktor = dpi / 72.0
        worte = [
            {
                "t": w["text"],
                "x": round(float(w["x0"]) * faktor, 1),
                "y": round(float(w["top"]) * faktor, 1),
                "w": round((float(w["x1"]) - float(w["x0"])) * faktor, 1),
                "h": round((float(w["bottom"]) - float(w["top"])) * faktor, 1),
            }
            for w in seite.get("words", [])
        ]
        return {
            "bild": f"data:image/png;base64,{roh}",
            "breite": round(float(seite.get("width", 595)) * faktor),
            "hoehe": round(float(seite.get("height", 842)) * faktor),
            "worte": worte,
        }
    except Exception:
        return None


def sammle_offene_dokumente(samples_dir: Path,
                            pdf_dir: Path | None = None,
                            mit_zeilen: set[str] | None = None,
                            live: bool = False) -> list[dict]:
    """Dokumente ohne verwertbares Ergebnis — bisher unsichtbar.

    ``sammle_zeilen`` nimmt nur ``status == "ok"``. Alles andere —
    Belegtyp nicht erkannt, Extraktion gescheitert, leer — tauchte in der
    Oberfläche nirgends auf und konnte damit stillschweigend vergessen werden.
    Das verletzt den Vollständigkeits-Constraint.
    """
    daten = json.loads((samples_dir / "_results_full.json").read_text())
    offen: list[dict] = []
    for eintrag in daten:
        status = eintrag.get("status")
        # Nur echte Dokumente. Ein Lauf, der versehentlich korrekturen.json
        # als Sample nahm, hinterlaesst einen Eintrag ohne PDF-Namen — der
        # darf nicht als „nicht zugeordnetes Dokument" auftauchen.
        if not str(eintrag.get("pdf_name") or "").lower().endswith(".pdf"):
            continue
        # Auch ein erfolgreich extrahiertes Dokument kann am Ende ohne
        # sichtbare Zeile dastehen: alle Betraege null, alles gestrichen,
        # alles out-of-scope. Dann war es in der Oberflaeche gar nicht mehr
        # auffindbar — man konnte dort weder etwas nachtragen noch es als
        # erledigt abhaken. Genau das ist der Fall „ich sehe nicht alle
        # Dokumente, die eingelesen wurden" (260904-rmx).
        ohne_zeile = (mit_zeilen is not None
                      and eintrag.get("pdf_name", "") not in mit_zeilen)
        if status in ("ok", "out_of_scope") and not ohne_zeile:
            continue
        pdf_name = eintrag.get("pdf_name", "")
        doc = {
            "beleg": pdf_name,
            "status": status,
            "grund": ("Eingelesen, aber keine übertragbare Position — hier "
                      "nachtragen oder als irrelevant abhaken"
                      if status in ("ok", "out_of_scope") else {
                          "unknown_belegtyp": "Belegtyp nicht erkannt",
                          "extract_failed": "Extraktion fehlgeschlagen",
                          "empty": "Kein Text im Dokument gefunden",
                      }.get(status, str(status))),
            "pdflink": (_pdf_link(pdf_dir, pdf_name, 1, live)
                        if pdf_dir else ""),
            "seite": None,
        }
        if pdf_dir:
            json_pfad = next(
                (p for p in sorted(samples_dir.glob("*.json"))
                 if not p.name.startswith("_")
                 and _pdf_name_of(p) == pdf_name),
                None,
            )
            if json_pfad is not None:
                doc["seite"] = _seite_mit_wortboxen(
                    json.loads(json_pfad.read_text()), pdf_dir / pdf_name)
        offen.append(doc)
    return offen


DOK_ZUSTAND = {
    "geprueft": "geprüft",
    "offen": "offen",
    "ohne_fund": "ohne Fund",
    "ausgeschlossen": "ausgeschlossen",
    "nicht_eingelesen": "nicht eingelesen",
}


def sammle_dokumente(samples_dir: Path, pdf_dir: Path | None,
                     zeilen: list[dict]) -> list[dict]:
    """Eine Zeile je PDF im Ordner — mit Zustand, Belegart und Fortschritt.

    Die Oberfläche begann bisher bei den Werten: sie zeigte, was die
    Extraktion gefunden hatte, und oben separat die Dokumente ohne Ergebnis.
    Die Frage „welche meiner Belege sind eigentlich schon durch?" liess sich
    damit nicht beantworten — man musste die ganze Seite durchscrollen und
    hoffen, nichts übersehen zu haben (260923-dua).

    Deshalb beginnt die Liste beim **Ordner**, nicht beim Ergebnis: nur so
    existiert der Zustand „liegt da, wurde nie eingelesen" überhaupt.

    Fünf Zustände, jeder mit einem Wort benannt — Farbe allein genügt nicht:

    ``geprueft``
        Alle Positionen haben eine bestätigte Zahl.
    ``offen``
        Es gibt Positionen, aber nicht alle sind bestätigt.
    ``ohne_fund``
        Eingelesen, aber keine übertragbare Position — hier fehlt am ehesten
        etwas.
    ``ausgeschlossen``
        Der Mensch hat das Dokument abgehakt.
    ``nicht_eingelesen``
        Meist ein Scan ohne Textebene.

    Die Bestätigungszahlen kommen aus ``korrekturen.json``, nicht aus dem
    Browser: derselbe Fortschritt erscheint auf jedem Gerät.
    """
    from extractors.korrekturen import (
        ausgeschlossene_dokumente, lade_alle, zerlege,
    )
    from scripts.dokumente_check import _stationen

    korr = lade_alle(samples_dir)
    ausgeschlossen = ausgeschlossene_dokumente(korr)

    # Ein Dokument, dessen Wert der Mensch mit 0.00 bestaetigt hat, ist
    # geprueft — nicht „ohne Fund".
    #
    # Der haeufigste Fall: eine Saeule-3a-Bescheinigung ohne Einzahlung im
    # Steuerjahr. Die Bereitschaftspruefung kannte die Regel, die Uebersicht
    # nicht — zwei Antworten auf dieselbe Frage, und in der Liste stand ein
    # Dokument als nicht zugeordnet, das laut Kommandozeile erledigt war
    # (260923-dua, vom Nutzer gemeldet).
    bestaetigte_null = {
        zerlege(schluessel)[0]
        for schluessel, e in korr.items()
        if isinstance(e, dict) and e.get("bestaetigt_betrag")
        and str(e.get("wert_betrag") or "").strip() in ("0", "0.00", "0.-")
    }

    # Dieselbe Rückwärtsrechnung wie `make dokumente` — zwei Antworten auf
    # dieselbe Frage würden auseinanderdriften.
    stand = _stationen(pdf_dir, samples_dir) if pdf_dir and pdf_dir.is_dir() \
        else {}

    je_beleg: dict[str, list[dict]] = {}
    for z in zeilen:
        je_beleg.setdefault(z.get("beleg", ""), []).append(z)

    # Der sprechende Dateiname, sobald Aussteller und Dokumenttyp feststehen.
    # Nur ein Vorschlag — umbenannt wird erst auf Knopfdruck.
    namensvorschlag: dict[str, dict] = {}
    if pdf_dir and pdf_dir.is_dir():
        try:
            from scripts.umbenennen import plane
            namensvorschlag = {p["alt"]: p for p in plane(
                samples_dir, pdf_dir,
                zeilen=[{"pdf_name": z.get("beleg"),
                         "beschreibung": z.get("zielwert"),
                         "aussteller": z.get("aussteller"),
                         "person": z.get("person"),
                         "belegtyp": z.get("belegtyp"),
                         "gestrichen": z.get("gestrichen"),
                         "jahr": z.get("jahr")} for z in zeilen])}
        except Exception:
            namensvorschlag = {}

    namen = set(stand) | set(je_beleg) | set(ausgeschlossen)
    aus: list[dict] = []
    for name in sorted(namen):
        if not name:
            continue
        st = stand.get(name, {})
        rs = je_beleg.get(name, [])
        bestaetigt = sum(1 for r in rs if r.get("vorabBetrag"))
        gestrichen = sum(1 for r in rs if r.get("gestrichen"))
        offen = len(rs) - bestaetigt - gestrichen

        if name in ausgeschlossen:
            zustand = "ausgeschlossen"
        elif stand and not st.get("json") and st.get("im_ordner"):
            zustand = "nicht_eingelesen"
        elif not rs and name in bestaetigte_null:
            zustand = "geprueft"
        elif not rs:
            zustand = "ohne_fund"
        elif offen <= 0:
            zustand = "geprueft"
        else:
            zustand = "offen"

        # Belegart: die des Dokuments, sonst die der Positionen — eine auf
        # „Einzahlung Säule 3a" umgestellte Zeile soll unter Säule 3a stehen,
        # auch wenn der Beleg als Bankauszug erkannt wurde.
        arten = sorted({str(r.get("typlabel") or "") for r in rs} - {""})
        vorschlag = namensvorschlag.get(name, {})
        aus.append({
            "beleg": name,
            "zustand": zustand,
            "zustandwort": DOK_ZUSTAND[zustand],
            "belegtyp": st.get("belegtyp") or ausgeschlossen.get(name, ""),
            "arten": arten,
            "positionen": len(rs),
            "bestaetigt": bestaetigt,
            "gestrichen": gestrichen,
            "offen": max(offen, 0),
            "im_ordner": bool(st.get("im_ordner")),
            # Sprechender Name, sobald Aussteller und Dokumenttyp feststehen.
            "neuername": vorschlag.get("neu", ""),
            "namengrund": vorschlag.get("grund", ""),
            "grund": {
                "nicht_eingelesen": "Kein Text gefunden — meist ein Scan ohne "
                                    "Textebene",
                "ohne_fund": "Eingelesen, aber keine übertragbare Position",
            }.get(zustand, ""),
        })
    return aus


def _pdf_name_of(json_pfad: Path) -> str:
    try:
        return json.loads(json_pfad.read_text()).get("pdf_name", "")
    except (OSError, json.JSONDecodeError):
        return ""


def sammle_seiten(samples_dir: Path, pdf_dir: Path | None,
                  belege_seiten: set[tuple[str, int]]) -> dict[str, dict]:
    """Seitenbilder mit Wortboxen — einmal je Dokument+Seite, nicht je Zeile.

    Damit laesst sich in JEDER Zeile der richtige Wert im Dokument anklicken,
    nicht nur bei den nicht zugeordneten. Ein Bild pro Seite statt pro Wert
    haelt die Datei klein genug.
    """
    if not pdf_dir:
        return {}
    out: dict[str, dict] = {}
    for pdf_name, seite in sorted(belege_seiten):
        json_pfad = next(
            (p for p in sorted(samples_dir.glob("*.json"))
             if not p.name.startswith("_") and _pdf_name_of(p) == pdf_name),
            None,
        )
        if json_pfad is None:
            continue
        try:
            doc = json.loads(json_pfad.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        daten = _seite_mit_wortboxen(doc, pdf_dir / pdf_name, seite)
        if daten:
            out[f"{pdf_name}|{seite}"] = daten
    return out


def _typ_label(r: dict) -> str:
    """Überschrift für die Gruppierung nach Belegart.

    Zuerst die Belegart. Ist sie unbekannt — etwa weil der Mensch die Zeile
    auf einen anderen Zielwert umgestellt hat —, dann die Ziffer: „VVG" unter
    „Übrige" zu finden ist irritierend, wenn die Zeile klar in Ziffer 15
    gehört (260904-rmx).
    """
    label = TYP_LABEL.get(r.get("belegtyp") or "")
    if label:
        return label
    from extractors.zielwerte import ziffer_name
    name = ziffer_name(str(r.get("ziffer") or ""))
    return name or "Übrige"


def _rolle_der_zeile(r: dict) -> str:
    """Welche Rolle gilt fuer diese Zeile?

    Drei Quellen, in dieser Reihenfolge:

    1. Der Wert in ``person`` — nach einer Korrektur steht dort die vom
       Menschen gesetzte Rolle, und die schlaegt alles.
    2. Die beim Extrahieren aufgeloeste Rolle ``person_rolle``. Sie kennt
       auch die Faelle, in denen der Anzeigetext nur den Namen enthaelt.
    3. Nichts — dann ist die Zeile nicht zugeordnet, und das gehoert
       sichtbar zu sein statt geraten.
    """
    aus_text = rolle_aus_person(r.get("person"))
    if aus_text:
        return aus_text
    rolle = str(r.get("person_rolle") or "").strip()
    return rolle if rolle in ALLE_ROLLEN else ""


def sammle_zeilen(samples_dir: Path, pdf_dir: Path | None = None,
                  mit_crops: bool = True, live: bool = False) -> list[dict]:
    """Baut die Reviewzeilen aus dem Extraktionsergebnis."""
    from scripts.build_tax_output import (
        build_rows, drop_zero_rows, ergaenze_eigene_zeilen, konfidenz_fuer,
        konfidenz_grund, normalize_aussteller, fmt_display_value,
        wende_korrekturen_an,
    )

    # Ohne --pdf-dir gibt es keine Ausschnitte und keine Links. Bewusst kein
    # fest verdrahteter Default: der Ordner mit den Originalen wird explizit
    # uebergeben (Makefile-Target tut das).
    # Welche Positionen stehen nicht woertlich im Beleg?
    #
    # Einmal je Aufbau, nicht je Zeile: die Pruefung liest die Wort-JSONs aller
    # Dokumente. Schlaegt sie fehl — etwa ohne Ablage —, bleibt die Menge leer
    # und die Oberflaeche verhaelt sich wie bisher.
    try:
        from scripts.ablage_nachweis import pruefe as _nachweis_pruefe
        _bericht = _nachweis_pruefe(samples_dir)
        _ohne_nachweis = {(str(z["dokument"]), str(z["zielwert"]),
                           int(z["pos"])) for z, _g, _w in _bericht["fehlt"]}
    except Exception:      # noqa: BLE001 - die Markierung ist ein Zusatz
        _ohne_nachweis = set()

    data = json.loads((samples_dir / "_results_full.json").read_text())
    # Nur echte Dokumente. Ein frueherer Lauf hat versehentlich
    # korrekturen.json als Sample verarbeitet; der Eintrag blieb im Ergebnis
    # stehen und erschien seither in der Uebersicht als Dokument „ohne Fund",
    # das es nie gab (260923-dua, vom Nutzer gemeldet).
    data = [e for e in data
            if str(e.get("pdf_name") or "").lower().endswith(".pdf")]
    rows: list[dict] = []
    for entry in data:
        if entry.get("status") != "ok":
            continue
        for r in build_rows(entry):
            r["aussteller"] = normalize_aussteller(r.get("aussteller"))
            rows.append(r)
    # Rohwerte festhalten, BEVOR die Korrekturen daraufkommen.
    #
    # Die Oberfläche muss unterscheiden können zwischen „so steht es im Beleg"
    # und „so hat es ein Mensch korrigiert". Ohne diesen Bezugspunkt verglich
    # sie den Eingabewert gegen den bereits korrigierten und hielt jede
    # übernommene Korrektur für „unverändert" — beim Zurückschreiben fiel sie
    # dann aus dem Eintrag heraus und die Zuordnung war weg (260904-rmx).
    for r in rows:
        r["_roh"] = {
            "betrag": r.get("betrag") or "",
            "person": r.get("person") or "",
            "aussteller": r.get("aussteller") or "",
            "beschreibung": r.get("beschreibung") or "",
            "ziffer": r.get("ziffer") or "",
        }

    # Bereits geprüfte Werte zuerst zurückspielen. Ohne diesen Schritt zeigt
    # die Oberfläche nach jedem Lauf wieder die alten, unkorrigierten Werte —
    # die ganze Durchsicht wäre erneut zu machen. Der Rückfluss war nur in
    # build_tax_output verdrahtet, nicht hier (260904-rmx).
    #
    # ``entfernen=False``: gestrichene Zeilen bleiben hier stehen (markiert).
    # Verschwänden sie, liesse sich eine versehentliche Streichung nie wieder
    # aufheben — die Zeile wäre aus der Oberfläche fort und der Eintrag in
    # korrekturen.json unerreichbar.
    korrigiert = wende_korrekturen_an(rows, samples_dir, entfernen=False)
    # Selbst erfasste Positionen kommen hier als gewoehnliche Zeilen dazu.
    # Sie muessen als solche erkennbar bleiben: sonst schickt die Oberflaeche
    # sie ohne die NEU-Markierung zurueck, der Eintrag verliert ``neu: true``,
    # und beim naechsten Aufbau legt sie niemand mehr an — die Position waere
    # lautlos weg (260904-rmx).
    _vorher = len(rows)
    ergaenzt = ergaenze_eigene_zeilen(rows, samples_dir)
    for r in rows[_vorher:]:
        r["ist_eigene"] = True
        r.setdefault("_roh", {"betrag": "", "person": "", "aussteller": "",
                              "beschreibung": r.get("beschreibung") or "",
                              "ziffer": r.get("ziffer") or ""})

    # Bereits bestaetigte Zeilen vormerken, damit die Haken gesetzt sind und
    # die Durchsicht nicht wiederholt werden muss.
    from extractors.korrekturen import finde as _finde_korr
    from extractors.korrekturen import lade_alle as _lade_korr
    _korr = _lade_korr(samples_dir)
    bestaetigt = 0
    for r in rows:
        # Was aus der Ablage kommt, ist geprueft — dort steht es als
        # `geprueft` und gesperrt.
        #
        # Vorher wurde ausschliesslich `korrekturen.json` befragt. Nach einer
        # Umnummerierung passte der Schluessel dort nicht mehr, und die
        # Oberflaeche zeigte laengst bestaetigte Positionen wieder als offen.
        # Der Mensch sollte sie ein zweites Mal bestaetigen — muessig und
        # verwirrend (260924-dua, vom Nutzer gemeldet).
        if str(r.get("field_name") or "") == "ablage":
            r["vorab_betrag"] = True
            r["vorab_person"] = bool(str(r.get("person") or "").strip())
            bestaetigt += 1
            continue
        # Ueber ``finde`` statt direkt: sonst verlieren umbenannte Zielwerte
        # ihre Haekchen, die Oberflaeche zeigt sie als ungeprueft, und beim
        # naechsten Speichern war die Bestaetigung weg (260904-rmx).
        e = _finde_korr(_korr, r.get("pdf_name", ""),
                        r.get("beschreibung", ""), r.get("pos") or 1)
        if not isinstance(e, dict):
            continue
        from extractors.korrekturen import gilt_als_bestaetigt
        if gilt_als_bestaetigt(e):
            r["vorab_betrag"] = True
            bestaetigt += 1
        if e.get("bestaetigt_person"):
            r["vorab_person"] = True
    if korrigiert or ergaenzt or bestaetigt:
        print(f"  ✍ {korrigiert} Korrektur(en), {bestaetigt} Bestaetigung(en) "
              f"und {ergaenzt} selbst erfasste Position(en) uebernommen")

    rows, _ = drop_zero_rows(rows)

    out: list[dict] = []
    for i, r in enumerate(rows, start=1):
        out.append({
            "nr": i,
            "beleg": r.get("pdf_name", ""),
            # Laufende Nummer dieser Position im Dokument. Drei Hypotheken auf
            # einem Beleg teilten sich sonst einen Schluessel (260923-dua).
            "pos": r.get("pos") or 1,
            "belegtyp": r.get("belegtyp", ""),
            "typlabel": _typ_label(r),
            "ziffer": r.get("ziffer", ""),
            "zielwert": r.get("beschreibung", ""),
            # Kurzer Zusatz, der mehrere Positionen gleicher Art im selben
            # Dokument auseinanderhaelt ("Festhypothek 0.95 %").
            "unterscheidung": r.get("unterscheidung") or "",
            # Das Dropdown kennt nur Rollen — der Anzeigetext „Name (rolle)"
            # passte zu keiner Option und liess das Feld leer. Der Name geht
            # trotzdem nicht verloren: er steht als Herkunftshinweis daneben.
            "person": _rolle_der_zeile(r),
            "personName": fmt_display_value(r.get("person")) or "",
            "aussteller": fmt_display_value(r.get("aussteller")) or "",
            "betrag": r.get("betrag") or "",
            # Fuer den sprechenden Dateinamen: "… - Erika Muster 2025.pdf".
            "jahr": str(r.get("jahr") or ""),
            # Steht dieser Betrag woertlich im Beleg?
            #
            # Ohne diese Angabe sieht man der Zeile nicht an, dass ihr Wert
            # nirgends im Dokument auffindbar ist — und weiss nicht, wo man
            # nachbessern soll (260924-dua, vom Nutzer gefragt).
            "ohne_nachweis": (str(r.get("pdf_name") or ""),
                              str(r.get("beschreibung") or ""),
                              int(r.get("pos") or 1)) in _ohne_nachweis,
            "konfidenz": konfidenz_fuer(r),
            "grund": konfidenz_grund(r),
            "herkunft": r.get("herkunft", ""),
            "seite": r.get("page") or "",
            "snippet": (r.get("snippet") or "")[:120],
            "vorabBetrag": bool(r.get("vorab_betrag")),
            "vorabPerson": bool(r.get("vorab_person")),
            "gestrichen": bool(r.get("gestrichen")),
            "neu": bool(r.get("ist_eigene")),
            # Der Stand aus dem Beleg — Bezugspunkt fuer „was ist geaendert?"
            "rohBetrag": r.get("_roh", {}).get("betrag", ""),
            "rohPerson": r.get("_roh", {}).get("person", ""),
            "rohAussteller": r.get("_roh", {}).get("aussteller", ""),
            "rohZielwert": r.get("_roh", {}).get("beschreibung", ""),
            "rohZiffer": r.get("_roh", {}).get("ziffer", ""),
            "seitenkey": (f'{r.get("pdf_name","")}|{int(r.get("page") or 0)}'
                          if r.get("page") else ""),
            "pdflink": (_pdf_link(pdf_dir, r.get("pdf_name", ""),
                                  r.get("page"), live)
                        if pdf_dir else ""),
            "crop": (_crop_datauri(pdf_dir / r.get("pdf_name", ""),
                                   int(r.get("page") or 0), r.get("bbox"))
                     if pdf_dir and mit_crops and r.get("page") and r.get("bbox")
                     else None),
        })
    # Unsichere zuoberst.
    rang = {"unsicher": 0, "wahrscheinlich": 1, "sicher": 2}
    out.sort(key=lambda z: (rang.get(z["konfidenz"], 9), z["ziffer"]))
    return out


# Belegtyp fehlt in build_rows — aus dem Zielwert ableiten reicht fuer das
# Dropdown-Vorbelegen nicht immer; deshalb bleibt das Feld notfalls leer.

_CSS = """
:root{--bg:#fbfbfa;--fg:#1a1a18;--mut:#6b6b66;--li:#e4e4e0;--card:#fff;
--sicher:#1a7f45;--wahr:#b8860b;--unsicher:#b3261e;--akz:#2b5fd9}
@media(prefers-color-scheme:dark){:root{--bg:#17171a;--fg:#ececea;--mut:#9a9a95;
--li:#2e2e33;--card:#1f1f23;--sicher:#4ec27f;--wahr:#e0b34a;--unsicher:#f2836f;
--akz:#7aa2f7}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
header{position:sticky;top:0;background:var(--bg);border-bottom:1px solid var(--li);
padding:14px 20px;z-index:5}
h1{font-size:17px;margin:0 0 4px}
.sub{color:var(--mut);font-size:12.5px}
.bar{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:10px}
button{font:inherit;padding:6px 12px;border:1px solid var(--li);border-radius:7px;
background:var(--card);color:var(--fg);cursor:pointer}
button:hover{border-color:var(--akz)}
button.on{background:var(--akz);color:#fff;border-color:var(--akz)}
button.pri{background:var(--akz);color:#fff;border-color:var(--akz);font-weight:600}
main{padding:16px 20px 60px}
.doc{margin-bottom:22px}
.doch{font-weight:600;font-size:13px;color:var(--mut);margin:0 0 6px;
word-break:break-all}
table{width:100%;border-collapse:collapse;background:var(--card);
border:1px solid var(--li);border-radius:9px;overflow:hidden}
th{text-align:left;font-size:11.5px;text-transform:uppercase;letter-spacing:.04em;
color:var(--mut);padding:8px 10px;border-bottom:1px solid var(--li);font-weight:600}
td{padding:8px 10px;border-bottom:1px solid var(--li);vertical-align:top}
tr:last-child td{border-bottom:0}
tr.ok{opacity:.5}
tr.gestrichen{opacity:.45;background:#fff5f5}
tr.gestrichen td input,tr.gestrichen td select{text-decoration:line-through;color:#a33}
button.weggestrichen{background:#a33;color:#fff;border-color:#a33}
.typwahl{font-weight:400;font-size:12px;color:var(--mut);margin-left:8px}
/* Dokumentuebersicht: Zustand als Wort UND als Farbe. Farbe allein genuegt
   nicht — sie faellt bei Sehschwaeche und im Ausdruck weg. */
/* Links die Dokumente, rechts eines davon.
   Jede Spalte scrollt FUER SICH. Vorher klebte die rechte oben fest
   (position:sticky) — ist sie hoeher als das Fenster, kommt man an ihr
   unteres Ende gar nicht heran. Und beim Scrollen der Seite verschwand die
   Dokumentliste nach oben weg (260923-dua). */
.zwei{display:grid;grid-template-columns:minmax(300px,34%) 1fr;gap:16px;
  align-items:start}
.zwei .liste,.zwei .detail{min-width:0;overflow:auto;
  max-height:calc(100vh - 150px);overscroll-behavior:contain}
/* Die 150px sind nur der Startwert — die genaue Hoehe setzt `hoehenSetzen()`
   aus der tatsaechlichen Lage, sonst scrollt die Seite als dritter Bereich
   zusaetzlich mit. */
.zwei .liste{padding-right:4px}
@media (max-width:1000px){
  .zwei{grid-template-columns:1fr}
  .zwei .liste,.zwei .detail{max-height:none;overflow:visible}
}
tr.dok.aktiv{background:#eef4ff}
tr.dok.aktiv .dokname .alslink{text-decoration:underline}
.zsum{display:inline-block;margin-right:14px}
/* Eine Tabelle mit vier Spalten passt nicht in eine schmale Seitenleiste —
   lange Dokumentnamen schoben sie auf, und der Rest wurde abgeschnitten.
   Deshalb Bloecke, die umbrechen duerfen. */
.dokliste{list-style:none;margin:0;padding:0}
.dokzeile{border:1px solid var(--li);border-radius:9px;background:var(--card);
  padding:8px 10px;margin-bottom:7px;display:grid;
  grid-template-columns:1fr auto;gap:4px 8px;align-items:start}
.dokzeile.aktiv{border-color:var(--akz);box-shadow:inset 0 0 0 1px var(--akz)}
.dokzeile .kopf{grid-column:1;min-width:0}
.dokzeile .tat{grid-column:2;grid-row:1/span 3;align-self:center}
.dokzeile .meta{grid-column:1;font-size:12px;color:var(--mut)}
.doktab{width:100%}
.doktab td{vertical-align:middle}
.dokname{font-weight:600;overflow-wrap:anywhere}
.alslink{background:none;border:none;padding:0;font:inherit;font-weight:600;
  color:var(--akz);cursor:pointer;text-align:left}
.alslink:hover{text-decoration:underline}
.zw{display:inline-block;padding:2px 9px;border-radius:11px;font-size:12px;
  white-space:nowrap;border:1px solid transparent}
.zw.geprueft{background:#e6f4ea;color:#1a6b34;border-color:#b7e0c4}
.zw.offen{background:#fdf3d7;color:#7a5a06;border-color:#eed9a0}
.zw.ohne_fund{background:#fde9e4;color:#8a3622;border-color:#f2c4b7}
.zw.ausgeschlossen{background:#ececec;color:#5d5d5d;border-color:#d4d4d4}
.zw.nicht_eingelesen{background:#f3e3f7;color:#6b2a7c;border-color:#e0c2e9}
tr.dok.ausgeschlossen .dokname .alslink{text-decoration:line-through;
  color:var(--dim)}
.veraltet{background:#fdf3d7;border:1px solid #eed9a0;color:#7a5a06;
  border-radius:9px;padding:10px 14px;margin:0 0 14px}
@media(prefers-color-scheme:dark){
  .veraltet{background:#3a3116;border-color:#6b5a22;color:#e8cf8a}
}
.befunde-klapp summary{cursor:pointer;list-style:revert}
.befunde-klapp summary::marker{color:var(--mut)}
.unt{font:inherit;font-size:12px;width:100%;margin-top:4px;padding:3px 6px;
  border:1px dashed var(--li);border-radius:5px;color:var(--dim)}
.unt:focus{outline:none;border-style:solid;border-color:var(--akz);
  color:inherit}
.neuname{font-size:12px;color:var(--dim);font-weight:400;margin-top:3px;
  word-break:break-word}
.neuname button{font-size:11px;padding:2px 8px;margin-left:6px}
.dim{color:var(--dim);font-weight:400}
.chip{font:inherit;font-size:12px;padding:3px 10px;margin-left:6px;
  border:1px solid var(--li);border-radius:11px;background:#fff;cursor:pointer}
.chip.on{border-color:var(--akz);box-shadow:inset 0 0 0 1px var(--akz)}
.artwahl{font-size:13px;color:var(--dim);display:inline-flex;align-items:center;
  gap:6px}
.artwahl select{font:inherit;padding:5px 8px;border:1px solid var(--li);
  border-radius:7px}
.suche{font:inherit;padding:6px 10px;border:1px solid var(--li);border-radius:7px;
background:var(--card);color:var(--fg);min-width:270px}
.suche:focus{outline:none;border-color:var(--akz)}
.zielwahl{margin-top:4px;font:12px inherit;max-width:190px}
button.mini{padding:2px 6px;font-size:11.5px;margin-top:3px}
.k.obn{background:#fdecea;color:#b3261e;border:1px solid #f3b7ae}
.live{font-size:12px;padding:1px 7px;border-radius:9px;margin-left:6px}
.schutzbanner{background:#fdecea;color:#b3261e;border:2px solid #b3261e;border-radius:6px;padding:10px 14px;margin:0 0 12px;font-weight:600}
.live.ok{background:#e6f4ec;color:#1a7f45}
.live.fehler{background:#fdecea;color:#b3261e}
@media(prefers-color-scheme:dark){.live.ok{background:#12301f;color:#4ec27f}
.live.fehler{background:#3a1a17;color:#f2836f}}
.uhint{color:var(--mut);font-size:12.5px;margin:0 0 14px}
.uebertrag .uh{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;
margin:0 0 6px;padding-bottom:5px;border-bottom:2px solid var(--li)}
.uz{font-weight:700;font-size:14px}
.un{color:var(--mut);font-size:13px;flex:1}
.us{font-variant-numeric:tabular-nums;font-weight:700;font-size:15px}
.uq{display:block;color:var(--mut);font-size:11.5px}
td.ub{font-variant-numeric:tabular-nums;font-weight:600;font-size:15px}
.uebertrag td{padding:7px 8px}
.typhinweis{margin:4px 0 0;font-size:12px;color:#a33}
.typhinweis code{background:#f4f4f4;padding:1px 4px;border-radius:3px}
.k{font-weight:600;white-space:nowrap}
.k.sicher{color:var(--sicher)}.k.wahrscheinlich{color:var(--wahr)}
.k.unsicher{color:var(--unsicher)}
.grund{color:var(--mut);font-size:12px;margin-top:2px}
.snip{color:var(--mut);font-size:11.5px;font-family:ui-monospace,Menlo,monospace;
margin-top:3px;word-break:break-word}
input,select{font:inherit;padding:5px 7px;border:1px solid var(--li);
border-radius:6px;background:var(--bg);color:var(--fg);max-width:100%}
input.betrag{width:118px;text-align:right;font-variant-numeric:tabular-nums}
input.notiz{width:100%;min-width:130px}
td.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.wrap{overflow-x:auto}
.befunde{border:1px solid var(--unsicher);border-left:4px solid var(--unsicher);
border-radius:8px;padding:10px 14px;margin:0 0 14px;background:var(--card)}
.befunde .bt{font-weight:700;margin:0 0 6px}
.befunde ul{margin:0;padding-left:18px}
.befunde li{margin:3px 0}
.sw{display:inline-block;font-size:11px;text-transform:uppercase;font-weight:700;
padding:1px 6px;border-radius:4px;margin-right:6px}
.sw.fehlt{background:var(--unsicher);color:#fff}
.sw.unstimmig{background:var(--wahr);color:#fff}
.sw.hinweis{background:var(--mut);color:#fff}
.befunde li.erledigt{opacity:.4;text-decoration:line-through}
button.weg{margin-left:8px;padding:2px 8px;font-size:11px}
.ok-box{border:1px solid var(--sicher);border-left:4px solid var(--sicher);
border-radius:8px;padding:10px 14px;margin:0 0 14px;background:var(--card);
color:var(--mut);font-size:12.5px}
input.ausst{width:100%;min-width:110px}
input.ziel{width:100%;min-width:150px;font-weight:500}
input.ziffer{width:56px;text-align:right}
.offen{border:1px solid var(--li);border-radius:8px;padding:10px 12px;
margin-bottom:10px;background:var(--card)}
.offen.weg{opacity:.45}
.ozeile{display:flex;gap:12px;flex-wrap:wrap;align-items:flex-end;margin-top:6px}
.ozeile label{display:flex;flex-direction:column;font-size:11.5px;color:var(--mut);gap:3px}
.hint2{font-size:12px;color:var(--mut);margin:8px 0 4px}
.seite{position:relative;max-width:100%;overflow:auto;border:1px solid var(--li);
border-radius:6px}
.seite img{display:block;max-width:none}
.wort{position:absolute;cursor:pointer;border-radius:2px}
.wort:hover{background:rgba(43,95,217,.22);outline:1px solid var(--akz)}
.wort.an{background:rgba(43,95,217,.38);outline:2px solid var(--akz)}
.typh{font-size:15px;font-weight:700;color:var(--fg);margin:0 0 8px;
padding-bottom:5px;border-bottom:2px solid var(--akz)}
.typh .anz{font-weight:400;font-size:12px;color:var(--mut);margin-left:8px}
.doch.sub{margin:12px 0 4px;font-weight:500}
td.chk{white-space:nowrap}
td.chk button{padding:4px 8px;font-size:12px;margin:0 2px 2px 0}
.lnk{display:inline-block;margin-top:3px;font-size:12px;color:var(--akz);
text-decoration:none}
.lnk:hover{text-decoration:underline}
img.crop{max-width:100%;border:1px solid var(--li);border-radius:5px;
display:block;background:#fff;margin:2px 0 6px}
tr.zusatz td{padding:0 10px 8px;border-bottom:1px solid var(--li);background:var(--bg)}
tr.zusatz td:empty{display:none}
.zeilenaktion{display:flex;gap:8px;align-items:center;margin-top:4px;flex-wrap:wrap}
.zeilenaktion button{padding:3px 8px;font-size:11.5px}
.seitenhalter:empty{display:none}
.hint{color:var(--mut);font-size:12.5px;margin:0 0 14px;padding:10px 12px;
border:1px solid var(--li);border-radius:8px;background:var(--card)}
"""

_JS = r"""
const R = window.__ROWS__, ROLLEN = window.__ROLLEN__, TYPEN = window.__TYPEN__;
const ZIFFERN = window.__ZIFFERN__ || {};
const ZIELWERT_ZIFFER = window.__ZIELWERT_ZIFFER__ || {};
const ZIELWERT_TYP = window.__ZIELWERT_TYP__ || {};
let filter = 'alle';
// Einstieg ist die Dokumentliste, nicht die Werteliste. Die erste Frage beim
// Hinsetzen lautet „was ist noch offen?" — nicht „welcher Betrag steht in
// Zeile 37?" (260923-dua).
let grpModus = 'dokumente';
// Belegart als FILTER, nicht bloss als Gruppierung: „zeig mir nur die
// Hypotheken" war vorher nicht moeglich.
let artFilter = '';
let zustandFilter = '';
const DOKUMENTE = window.__DOKUMENTE__ || [];

// Der Zustand haengt an (Beleg|Zielwert), nicht an der Zeilennummer.
//
// Erste Fassung: Schluessel 'steuer-review-' + R.length, Zustand nach nr.
// Beides zerbrach, sobald sich die Zeilenzahl aenderte — ein neues Dokument,
// eine gestrichene Zeile —: anderer Schluessel, also leerer Speicher, und die
// nr zeigte auf eine andere Zeile als beim Sichern. Der Beleg samt Zielwert
// ist dagegen dieselbe Sache, egal an welcher Stelle sie steht.
// Der Schluessel traegt die Herkunft. Zwei Laeufe (produktiv und
// Sample-Korpus) laufen unter derselben Browser-Origin; ohne Trennung
// ueberschreibt der eine den Speicher des anderen.
const KORPUS = (window.__KORPUS__ || 'standard');
const SKEY = 'steuer-review:' + KORPUS;
const NKEY = 'steuer-neu:' + KORPUS;

// Umbenannte Dokumente auch im Browser-Speicher nachziehen.
//
// Die Umbenennung zieht `korrekturen.json` mit — aber nicht, was hier liegt.
// Die selbst erfassten Zeilen trugen danach weiter den ALTEN Dateinamen und
// wurden beim naechsten Speichern unter diesem Namen zurueckgeschrieben.
// Daraus entstand ein zweites Dokument, das es im Ordner gar nicht gibt
// (260923-dua, vom Nutzer gemeldet).
//
// Laeuft einmal beim Laden, bevor irgendetwas den Speicher benutzt.
(function umbenennungenNachziehen(){
  const journal = window.__UMBENANNT__ || [];
  if (!journal.length) return;
  const neuerName = {};
  journal.forEach(e => { if (e.alt && e.neu) neuerName[e.alt] = e.neu; });
  // Ketten aufloesen: a -> b -> c soll a direkt auf c zeigen.
  Object.keys(neuerName).forEach(alt => {
    let ziel = neuerName[alt], tiefe = 0;
    while (neuerName[ziel] && neuerName[ziel] !== ziel && tiefe++ < 20)
      ziel = neuerName[ziel];
    neuerName[alt] = ziel;
  });
  const neuer = (name) => neuerName[name] || name;

  const listen = ['steuer-neu:', 'steuer-geloescht:'];
  listen.forEach(praefix => {
    const k = praefix + KORPUS;
    try {
      const daten = JSON.parse(localStorage.getItem(k) || '[]');
      if (!Array.isArray(daten)) return;
      let beruehrt = false;
      daten.forEach(e => {
        if (e && e.beleg && neuerName[e.beleg]){ e.beleg = neuer(e.beleg);
                                                 beruehrt = true; }
      });
      if (beruehrt) localStorage.setItem(k, JSON.stringify(daten));
    } catch(e){}
  });

  // Speicher, deren SCHLUESSEL den Dokumentnamen tragen.
  [['steuer-review:', true], ['steuer-belegtyp:', false],
   ['steuer-offen:', false]].forEach(([praefix, mitZielwert]) => {
    const k = praefix + KORPUS;
    try {
      const daten = JSON.parse(localStorage.getItem(k) || '{}');
      if (!daten || typeof daten !== 'object') return;
      const umgehaengt = {};
      let beruehrt = false;
      Object.keys(daten).forEach(schluessel => {
        const teil = mitZielwert ? schluessel.split('|')[0] : schluessel;
        if (neuerName[teil]){
          const rest = mitZielwert ? schluessel.slice(teil.length) : '';
          umgehaengt[neuer(teil) + rest] = daten[schluessel];
          beruehrt = true;
        } else {
          umgehaengt[schluessel] = daten[schluessel];
        }
      });
      if (beruehrt) localStorage.setItem(k, JSON.stringify(umgehaengt));
    } catch(e){}
  });

  try {
    const g = localStorage.getItem('steuer-gewaehlt:' + KORPUS);
    if (g && neuerName[g])
      localStorage.setItem('steuer-gewaehlt:' + KORPUS, neuer(g));
  } catch(e){}
})();

// Welches Dokument gerade offen ist.
//
// Der Name gehoert NICHT in den URL-Hash: Belegnamen tragen Institute und
// Personen, und die Browser-History ist nicht der Ort dafuer. Ein Index waere
// ueber Neuaufbauten instabil. Also ein eigener Speicher je Korpus.
const WKEY = 'steuer-gewaehlt:' + KORPUS;
let gewaehlt = '';
try { gewaehlt = localStorage.getItem(WKEY) || ''; } catch(e) { gewaehlt = ''; }
function waehle(beleg){
  gewaehlt = beleg || '';
  try { gewaehlt ? localStorage.setItem(WKEY, gewaehlt)
                 : localStorage.removeItem(WKEY); } catch(e){}
  render();
}



// Selbst erfasste Zeilen zuerst wieder einhaengen — sonst findet das Laden
// des Zustands sie nicht, und was man an ihnen eingetragen hat, ist nach
// jedem Neuladen weg. Sie tragen ihre eigene nr im Schluessel: zwei neue
// Zeilen im selben Dokument haetten sonst denselben und wuerden sich
// gegenseitig ueberschreiben — genau der Fall „zwei Betreuungsbetraege aus
// einem Beleg" (260904-rmx).
let neue = [];
try { neue = JSON.parse(localStorage.getItem(NKEY) || '[]'); } catch(e) { neue = []; }
function neueSichern(){ try { localStorage.setItem(NKEY, JSON.stringify(neue)); } catch(e){} }
// Eine selbst erfasste Zeile, die der Server inzwischen selbst liefert, darf
// NICHT noch einmal aus dem lokalen Speicher dazukommen.
//
// Bis heute Morgen verschluckte der Tabellenbau die zweite Position derselben
// Art — die lokale Kopie war die einzige, und sie musste bleiben. Seit die
// Positionsnummern stimmen, kommt dieselbe Zeile vom Server zurueck, und die
// Kopie machte sie doppelt: fuenf Betreuungskosten, wo es zwei gibt
// (260923-dua, vom Nutzer gemeldet).
//
// Verglichen wird nicht die `nr` — die vergibt der Server anders —, sondern
// woran man die Zeile erkennt: Dokument, Zielwert und Position.
(function eigeneEinhaengen(){
  // Den Speicher hier direkt lesen: `rohSpeicher` wird weiter unten
  // deklariert, und ein Zugriff von hier aus liefe in die temporale
  // Todeszone — das Skript braeche ab und die Seite bliebe leer.
  let gespeichert = {};
  try { gespeichert = JSON.parse(localStorage.getItem(SKEY) || '{}'); }
  catch(e) { gespeichert = {}; }
  const vomServer = new Set(
    R.filter(x => x.neu)
     .map(x => `${x.beleg}|${x.zielwert || ''}|${x.pos || 1}`));
  const behalten = [];
  neue.forEach(z => {
    const eigenerStand = (gespeichert[(z.beleg || '') + '|#' + z.nr] || {});
    const ziel = eigenerStand.zielwert !== undefined
      ? eigenerStand.zielwert : (z.zielwert || '');
    const pos = eigenerStand.posNr || 1;
    if (vomServer.has(`${z.beleg}|${ziel}|${pos}`)) return;   // schon da
    behalten.push(z);
    if (!R.some(x => x.nr === z.nr)) R.push(z);
  });
  if (behalten.length !== neue.length){
    neue = behalten;
    try { localStorage.setItem(NKEY, JSON.stringify(neue)); } catch(e){}
  }
})();

// Der Schluessel des lokalen Zustands traegt die Positionsnummer mit.
//
// Ohne sie teilen sich drei Hypotheken eines Belegs einen Eintrag: was man
// bei der zweiten eintraegt, landet bei der ersten. Position 1 bleibt ohne
// Zusatz — bestehende Speicher behalten damit ihre Gueltigkeit (260923-dua).
const skey = (r) => r.neu
  ? (r.beleg || '') + '|#' + r.nr
  : (r.beleg || '') + '|' + (r.rohZielwert || r.zielwert || '')
      + ((r.pos || 1) > 1 ? '#' + r.pos : '');
let state = {};   // nr -> {betrag, person, notiz, ok}

// Der rohe Speicher bleibt erhalten, auch was gerade zu keiner Zeile passt.
//
// Erste Fassung baute beim Sichern den ganzen Speicher aus den AKTUELLEN
// Zeilen neu. Jeder Eintrag, dessen Schluessel gerade nicht getroffen wurde —
// weil eine Bezeichnung sich geaendert hat, ein PDF umbenannt wurde oder ein
// anderer Korpus geladen ist — war beim naechsten Tastendruck weg. Dasselbe
// Muster wie der Verlust auf der Serverseite: Schweigen als Loeschen gelesen
// (260904-rmx).
let rohSpeicher = {};
try { rohSpeicher = JSON.parse(localStorage.getItem(SKEY) || '{}'); } catch(e) { rohSpeicher = {}; }

(function ladeZustand(){
  R.forEach(r => { if (rohSpeicher[skey(r)]) state[r.nr] = rohSpeicher[skey(r)]; });
})();

function sichern(){
  livesichern();
  // Nur die Schluessel der sichtbaren Zeilen aktualisieren; alles andere
  // bleibt unangetastet stehen.
  R.forEach(r => {
    if (state[r.nr]) rohSpeicher[skey(r)] = state[r.nr];
    else delete rohSpeicher[skey(r)];
  });
  try { localStorage.setItem(SKEY, JSON.stringify(rohSpeicher)); } catch(e){}
}

function esc(s){const d=document.createElement('div');d.textContent=s==null?'':s;
  return d.innerHTML;}

function opts(list, sel){
  return ['<option value=""></option>'].concat(list.map(v=>{
    const val = String(v).split(' (')[0];
    return `<option value="${esc(val)}"${val===sel?' selected':''}>${esc(v)}</option>`;
  })).join('');
}

const SEITEN = window.__SEITEN__ || {};
const REGELN = window.__REGELN__ || {rollen:[], erwerbsRollen:[], muster:{}};

function passt(text, art){
  return (REGELN.muster[art] || []).some(m => (text||'').includes(m));
}

function pruefeVollstaendigkeit(){
  const rolleVon = r => {
    const s = state[r.nr] || {};
    return String(s.person !== undefined ? s.person : (r.person||'')).split(' (')[0];
  };
  const zielVon = r => {
    const s = state[r.nr] || {};
    return s.zielwert !== undefined ? s.zielwert : r.zielwert;
  };
  const hat = {};
  const jeDok = {};
  // Gestrichene Zeilen zaehlen nicht als vorhanden: „steht nicht in diesem
  // Dokument" heisst gerade nicht „gibt es nicht". Der Wert muss weiter
  // eingefordert werden, sonst verschwiegen wir mit der falschen Zeile auch
  // den echten Abzug (260904-rmx).
  R.filter(r => !istGestrichen(r)).forEach(r => {
    const rolle = rolleVon(r), z = zielVon(r);
    if (rolle && rolle !== 'unbekannt/manuell'){
      const a = hat[rolle] = hat[rolle] || {};
      ['kvg','vvg','selbstkosten','lohn','saeule_3a'].forEach(k => {
        if (passt(z,k)) a[k] = true;
      });
    }
    const d = jeDok[r.beleg] = jeDok[r.beleg] || {};
    if (passt(z,'praemie')) d.praemie = true;
    if (passt(z,'selbstkosten')) d.selbstkosten = true;
  });

  const b = [];
  REGELN.rollen.forEach(rolle => {
    const a = hat[rolle] || {};
    if (!a.kvg) b.push(['fehlt', `Keine Prämie Grundversicherung (KVG) für ${rolle}.`, rolle]);
  });
  REGELN.erwerbsRollen.forEach(rolle => {
    const a = hat[rolle] || {};
    if (!a.lohn) b.push(['fehlt', `Kein Nettolohn für ${rolle}.`, rolle]);
    if (!a.saeule_3a) b.push(['fehlt', `Keine Säule-3a-Einzahlung für ${rolle}.`, rolle]);
  });
  Object.keys(jeDok).sort().forEach(dok => {
    const d = jeDok[dok];
    if (d.selbstkosten && !d.praemie)
      b.push(['unstimmig', 'Selbst getragene Kosten ohne Prämie im selben Beleg.', dok]);
  });
  const ohne = R.filter(r => !rolleVon(r) || rolleVon(r) === 'unbekannt/manuell').length;
  if (ohne) b.push(['fehlt', `${ohne} Position(en) ohne zugeordnete Person.`, '']);
  REGELN.rollen.forEach(rolle => {
    const a = hat[rolle] || {};
    if (!a.vvg) b.push(['hinweis', `Keine Zusatzversicherung (VVG) für ${rolle}.`, rolle]);
    if (!a.selbstkosten) b.push(['hinweis', `Keine selbst getragenen Krankheitskosten für ${rolle}.`, rolle]);
  });
  const rang = {fehlt:0, unstimmig:1, hinweis:2};
  return b.sort((x,y) => rang[x[0]] - rang[y[0]]);
}

// Ob der Befundkasten offen steht, merkt sich die Seite je Korpus.
const BOKEY = 'steuer-befunde-offen:' + KORPUS;
let bOffen = false;
try { bOffen = localStorage.getItem(BOKEY) === 'ja'; } catch(e) { bOffen = false; }
function bKlappStand(el){
  bOffen = !!el.open;
  try { localStorage.setItem(BOKEY, bOffen ? 'ja' : 'nein'); } catch(e){}
  hoehenSetzen();
}

function befundeNeu(){
  const b = pruefeVollstaendigkeit().filter(x => !bWeg[`${x[1]}|${x[2]}`]);
  const kasten = document.getElementById('befundkasten');
  if (!kasten) return;
  // Eingeklappt. Die Befunde sind wichtig, aber sie sind kein Arbeitsschritt
  // — sie stehen am Ende der Durchsicht, nicht am Anfang. Aufgeklappt
  // schoben sie die beiden Arbeitsspalten aus dem Bild (260923-dua).
  kasten.innerHTML = b.length
    ? `<details class="befunde-klapp"${bOffen ? ' open' : ''}
                ontoggle="bKlappStand(this)">
       <summary class="bt">Vollständigkeitsprüfung — ${b.length} Befund(e)</summary>
       <p><button onclick="befundeNeu()">erneut prüfen</button></p>
       <ul>${b.map(x=>`<li data-bef="${esc(x[1])}|${esc(x[2])}">
         <span class="sw ${x[0]}">${x[0]}</span> ${esc(x[1])}
         ${x[2]?`<em>(${esc(x[2])})</em>`:''}
         <button class="weg" onclick="befundWeg(this)">gibt es nicht</button></li>`).join('')}</ul>
       </details>`
    : `<p class="bt">Vollständigkeitsprüfung ohne Befund
         <button onclick="befundeNeu()">erneut prüfen</button></p>`;
}
const ZIELWERTE_WAHL = window.__ZIELWERTE__ || [];

// Vorschlagsliste einmal fuellen.
(function(){
  const dl = document.getElementById('zielwerte');
  if (dl) dl.innerHTML = ZIELWERTE_WAHL.map(z=>`<option value="${esc(z)}">`).join('');
})();

function zeileHinzu(beleg){
  // Fortlaufend ueber alle je erfassten Zeilen, nicht ueber die aktuelle
  // Anzahl: nach dem Entfernen einer Zeile gaebe es die nr sonst zweimal.
  const nr = 1 + Math.max(10000, ...R.map(x => x.nr || 0));
  neue.push({nr, beleg, zielwert: '', ziffer: '', person: '', aussteller: '',
             betrag: '', belegtyp: '', typlabel: 'Selbst erfasst',
             ohne_nachweis: false,
             konfidenz: 'unsicher', grund: 'selbst erfasst', herkunft: 'mensch',
             seite: '', snippet: '', pdflink: '', crop: null, seitenkey: '',
             neu: true});
  R.push(neue[neue.length - 1]);
  neueSichern(); render();
  return nr;
}

// Belegtyp eines bereits erkannten Dokuments korrigieren.
//
// Bisher liess sich der Typ nur bei den *nicht zugeordneten* Dokumenten
// setzen. War ein Beleg falsch erkannt — eine Saeule-3a-Bescheinigung als
// Bankbeleg —, gab es keinen Weg zurueck: der Feldvertrag bietet dann nur
// Bank-Zielwerte an, und "Einzahlung Säule 3a" laesst sich nicht vergeben.
//
// Der Typ entscheidet, welche Extraktionsregeln laufen. Das kann der Browser
// nicht nachholen; er zeigt deshalb den Befehl, der das Dokument mit dem
// richtigen Typ neu einliest (260904-rmx).
const TKEY = 'steuer-belegtyp:' + KORPUS;
let tState = {};
try { tState = JSON.parse(localStorage.getItem(TKEY) || '{}'); } catch(e) { tState = {}; }
function tSichern(){ try { localStorage.setItem(TKEY, JSON.stringify(tState)); } catch(e){} }

function typVon(rs){ return (rs.find(r => r.belegtyp) || {}).belegtyp || ''; }

function typSetzen(beleg, typ){
  if (typ) tState[beleg] = typ; else delete tState[beleg];
  tSichern(); render();
}

function typWahl(beleg, rs){
  const ist = typVon(rs), soll = tState[beleg] !== undefined ? tState[beleg] : ist;
  return `<label class="typwahl">Belegart
    <select onchange="typSetzen('${esc(beleg)}',this.value)">
      ${opts(TYPEN, soll)}</select></label>`;
}

function typHinweis(beleg, rs){
  const ist = typVon(rs), soll = tState[beleg];
  if (soll === undefined || soll === ist) return '';
  // „Irrelevant" ist keine Belegart mit Regeln, sondern das Gegenteil davon.
  //
  // Vorher stand hier der Befehl zum Neu-Einlesen mit TYP=irrelevant — ein
  // Unsinn: es gibt nichts zu extrahieren, und der Mensch hat gerade gesagt,
  // dass ihn das Dokument nicht interessiert (260923-dua).
  if (soll === 'irrelevant'){
    return `<p class="typhinweis">Als <strong>nicht steuerrelevant</strong>
      abgehakt. Das Dokument zählt nicht mehr als offen, und seine Werte
      fallen aus der Übertragungstabelle.
      ${LIVE ? `<button onclick="aussortieren('${esc(beleg)}',this)">aus dem
        Eingangsordner nehmen</button>` : `Mit <code>make aussortieren
        JETZT=1</code> wandert es aus dem Eingangsordner und wird künftig gar
        nicht mehr eingelesen.`}
      Rückgängig: <code>make aussortieren-zurueck</code>.</p>`;
  }
  const cmd = `make reextract PDF="${beleg}" TYP=${soll}`;
  return `<p class="typhinweis">Andere Belegart gewählt — das Dokument muss mit
    den Regeln dieses Typs neu eingelesen werden:
    <code>${esc(cmd)}</code>
    <button onclick="kopieren('${esc(cmd)}',this)">Befehl kopieren</button>
    Danach <code>make tabelle</code> und <code>make review</code>.</p>`;
}

// Ein abgehaktes Dokument aus dem Eingangsordner nehmen.
async function aussortieren(beleg, knopf){
  if (!LIVE) return;
  if (knopf) knopf.disabled = true;
  try {
    await sendeKorrekturen();
    const antwort = await fetch('/api/aussortieren', {
      method: 'POST',
      headers: {'Content-Type':'application/json','X-Steuer-Token': LIVE},
      body: JSON.stringify({beleg}),
    });
    const daten = await antwort.json();
    if (!antwort.ok || daten.fehler) throw new Error(daten.fehler || 'Fehler');
    livestatus(`${daten.anzahl} Dokument(e) aussortiert — Seite wird neu geladen`,
               'ok');
    setTimeout(() => location.reload(), 900);
  } catch (e) {
    if (knopf) knopf.disabled = false;
    livestatus('Aussortieren fehlgeschlagen — nichts geändert', 'fehler');
  }
}

// Faelschlich erkannte Felder streichen.
//
// Ein Wert, den die Extraktion in einem Dokument gefunden hat, das ihn gar
// nicht enthaelt — die PLZ als Praemie, die AHV-Nummer als Abzug, eine
// Zusatzversicherung in der Abrechnung der Grundversicherung. Bisher liess
// sich so etwas nur ueberschreiben; die Zeile blieb.
//
// Streichen sagt weder "der Betrag ist falsch" noch "das gehoert nicht in
// die Steuererklaerung", sondern: DIESES Dokument weist diesen Wert nicht
// aus. Der Wert kann anderswo sehr wohl existieren — die Zusatzversicherung
// liegt bei einer anderen Kasse und in einem anderen Dokument. Deshalb
// verschwindet die Zeile aus der Tabelle, und die Vollstaendigkeitspruefung
// fordert den Wert weiterhin ein, bis er woanders auftaucht (260904-rmx).
// Gilt die Zeile aktuell als gestrichen? Lokale Wahl schlaegt den
// gespeicherten Stand — sonst liesse sich eine Streichung nicht aufheben.
function istGestrichen(r){
  const s = state[r.nr] || {};
  return s.streichen !== undefined ? !!s.streichen : !!r.gestrichen;
}

// Ein Dokument gehoert meist einer Person. Die Extraktion erkennt sie nicht
// in jeder Zeile — dann steht dort „unbekannt/manuell". Einmal richtig
// setzen und uebertragen ist schneller als zwoelf Dropdowns.
function personAufBeleg(nr){
  const r = R.find(x => x.nr === nr);
  if (!r) return;
  const s = state[nr] || {};
  const rolle = s.person !== undefined ? s.person : (r.person || '');
  if (!rolle){
    livestatus('Erst eine Person wählen, dann übertragen', 'fehler');
    return;
  }
  let n = 0;
  R.filter(x => x.beleg === r.beleg).forEach(x => {
    (state[x.nr] = state[x.nr] || {}).person = rolle;
    n++;
  });
  sichern(); render(); befundeNeu();
  livestatus(`Person auf ${n} Zeile(n) dieses Dokuments übertragen`, 'ok');
}

function streichen(nr){
  const r = R.find(x => x.nr === nr) || {};
  const s = state[nr] = state[nr] || {};
  s.streichen = !istGestrichen(r);
  if (s.streichen){ s.okBetrag = false; s.okPerson = false; }
  sichern(); render(); befundeNeu();
}

// Entfernte eigene Zeilen — gemerkt, bis der Server sie los ist.
const GKEY = 'steuer-geloescht:' + KORPUS;
let geloescht = [];
try { geloescht = JSON.parse(localStorage.getItem(GKEY) || '[]'); } catch(e) { geloescht = []; }
function geloeschtSichern(){
  try { localStorage.setItem(GKEY, JSON.stringify(geloescht)); } catch(e){} }

// Eine weitere Position derselben Art — der haeufige Fall ist „zwei Kinder,
// ein Betreuungsbeleg".
//
// Die neue Zeile erbt Beleg, Zielwert, Ziffer und Aussteller der Zeile, aus
// der heraus sie angelegt wird. Ohne das bekam sie keine Ziffer und landete
// in der Ziffer-Ansicht in einer eigenen Gruppe weit weg — es sah aus, als
// waere der Knopf wirkungslos (260904-rmx). Betrag und Person bleiben leer:
// die unterscheiden die beiden Positionen ja gerade.
function zeileDazu(nr){
  const r = R.find(x => x.nr === nr);
  if (!r) return;
  const s = state[nr] || {};
  const neuNr = zeileHinzu(r.beleg);
  const z = state[neuNr] = state[neuNr] || {};
  z.zielwert = s.zielwert !== undefined ? s.zielwert : (r.zielwert || '');
  z.ziffer = s.ziffer !== undefined ? s.ziffer : (r.ziffer || '');
  z.aussteller = s.aussteller !== undefined ? s.aussteller : (r.aussteller || '');
  const zeile = R.find(x => x.nr === neuNr);
  if (zeile){ zeile.zielwert = z.zielwert; zeile.ziffer = z.ziffer; }
  sichern(); render();
  // Hinschauen, wo sie gelandet ist — sonst sucht man sie.
  const el = document.getElementById('z' + neuNr);
  if (el){
    el.scrollIntoView({block: 'center'});
    const feld = el.querySelector('.betrag');
    if (feld) feld.focus();
  }
  const pz = positionVon(R.find(x => x.nr === neuNr) || {});
  livestatus(`Position ${pz} dieser Art angelegt — Betrag und Person eintragen`,
             'ok');
}

function zeileEntfernen(nr){
  const r0 = R.find(x => x.nr === nr);
  if (r0 && r0.beleg){
    const s0 = state[nr] || {};
    const zw = s0.zielwert || r0.zielwert || '';
    const pz = positionVon(r0);
    if (zw && !geloescht.some(g => g.beleg === r0.beleg && g.zielwert === zw
                                   && (g.pos || 1) === pz)){
      geloescht.push({beleg: r0.beleg, zielwert: zw, pos: pz});
      geloeschtSichern();
    }
  }
  const i = R.findIndex(x => x.nr === nr);
  if (i >= 0) R.splice(i, 1);
  neue = neue.filter(x => x.nr !== nr);
  delete state[nr];
  neueSichern(); sichern(); render();
}

// (Das Einhaengen passiert oben, vor dem Laden des Zustands.)

function seiteZeigen(nr, knopf){
  const halter = document.getElementById('sh' + nr);
  if (!halter) return;
  if (halter.innerHTML){ halter.innerHTML = ''; knopf.textContent = 'im Dokument wählen'; return; }
  const r = R.find(x => x.nr === nr);
  const sd = SEITEN[r.seitenkey];
  if (!sd) return;
  knopf.textContent = 'Ansicht schliessen';
  const s = state[nr] || {};
  halter.innerHTML = `
    <p class="hint2">Wort anklicken, um den Betrag zu übernehmen — der Text kommt
      exakt aus dem PDF. Mehrere Klicks verbinden Wörter.</p>
    <div class="seite" style="width:${sd.breite}px">
      <img src="${sd.bild}" width="${sd.breite}" height="${sd.hoehe}" alt="Seite">
      ${sd.worte.map((w,i)=>`<span class="wort${(s.markiert||[]).includes(i)?' an':''}"
        style="left:${w.x}px;top:${w.y}px;width:${w.w}px;height:${w.h}px"
        onclick="wortWaehlen(${nr},${i})" title="${esc(w.t)}"></span>`).join('')}
    </div>`;
}

function wortWaehlen(nr, i){
  const r = R.find(x => x.nr === nr);
  const sd = SEITEN[r.seitenkey];
  if (!sd) return;
  const s = state[nr] = state[nr] || {};
  s.markiert = s.markiert || [];
  const pos = s.markiert.indexOf(i);
  if (pos >= 0) s.markiert.splice(pos, 1); else s.markiert.push(i);
  s.markiert.sort((a,b)=>a-b);
  s.betrag = s.markiert.map(j => sd.worte[j].t).join(' ');

  // Position und Zeilenkontext festhalten — daraus entsteht spaeter die Regel.
  // Der Kontext wird AUS DER GEOMETRIE gelesen, nicht abgetippt: alle Woerter
  // links des markierten, in derselben Zeile.
  if (s.markiert.length){
    const erstes = sd.worte[s.markiert[0]];
    const zeile = sd.worte
      .filter(x => Math.abs(x.y - erstes.y) <= 4 && x.x < erstes.x)
      .sort((a,b) => a.x - b.x).map(x => x.t).join(' ');
    s.pos = {
      x: +(erstes.x / sd.breite).toFixed(3),
      y: +(erstes.y / sd.hoehe).toFixed(3),
      seite: r.seite || 1,
      kontext: zeile.slice(-70),
    };
  } else {
    delete s.pos;
  }
  sichern();
  render();
  const knopf = document.querySelector(`#z${nr} .zeilenaktion button`);
  if (knopf) seiteZeigen(nr, knopf);
}

function summe(rs){
  let t = 0, ok = true;
  rs.forEach(r => { const s = state[r.nr]||{};
    const n = parseFloat(roh(s.betrag!==undefined?s.betrag:r.betrag));
    if (isNaN(n)) ok = false; else t += n; });
  return ok ? t.toFixed(2) : null;
}

// Formularfaehig: ohne Tausendertrenner, Punkt als Dezimaltrenner.
// Die Dokumente schreiben 1'976.05, 1 976.05, 1976,05 und 1976.00
// durcheinander — ins Steuerformular gehoert davon nur die nackte Zahl.
function roh(v){
  let t = String(v==null?'':v).trim()
    .replace(/[’'\u00a0\s]/g, '')
    .replace(/(\d),(\d{2})$/, '$1.$2')
    .replace(/,/g, '');
  return /^-?\d+(\.\d+)?$/.test(t) ? t : String(v==null?'':v);
}

let rohAnzeige = false;
function rohUmschalten(el){
  rohAnzeige = !rohAnzeige;
  el.classList.toggle('on', rohAnzeige);
  el.textContent = rohAnzeige ? 'Beträge formatiert' : 'Beträge unformatiert';
  render();
}

function anzeige(v){ return rohAnzeige ? roh(v) : (v==null?'':v); }

function kopieren(txt, el){
  navigator.clipboard.writeText(roh(txt)).then(()=>{
    const alt = el.textContent; el.textContent = 'kopiert'; el.classList.add('on');
    setTimeout(()=>{el.textContent = alt; el.classList.remove('on');}, 900);
  });
}

function tabelle(rs){
  return `<table><thead><tr>
        <th>Ziffer</th><th>Zielwert</th><th>Aussteller</th><th>Person</th><th>Betrag</th>
        <th>Konfidenz &amp; Grund</th><th>Notiz (optional)</th><th>Geprüft</th>
      </tr></thead><tbody>
      ${rs.map(r => {
        const s = state[r.nr] || {};
        // Bereits geprueft? Dann Haken vorbelegen (lokale Wahl hat Vorrang).
        const okB = s.okBetrag !== undefined ? s.okBetrag : !!r.vorabBetrag;
        const okP = s.okPerson !== undefined ? s.okPerson : !!r.vorabPerson;
        const weg = istGestrichen(r);
        return `<tr id="z${r.nr}" class="${weg?'gestrichen':((okB&&okP)?'ok':'')}">
          <td class="num"><input class="ziffer"
              value="${esc(s.ziffer!==undefined?s.ziffer:r.ziffer)}"
              placeholder="4" oninput="setv(${r.nr},'ziffer',this.value)"></td>
          <td>
            <input class="ziel" list="zielwerte" id="ziel${r.nr}"
                   value="${esc(s.zielwert!==undefined?s.zielwert:r.zielwert)}"
                   oninput="setZiel(${r.nr},this.value)">
            <select class="zielwahl" title="Zielwert aus dem Feldvertrag wählen"
                    onchange="zielWaehlen(${r.nr},this)">
              <option value="">▾ wählen</option>
              ${ZIELWERTE_WAHL.map(z=>`<option value="${esc(z)}">${esc(z)}</option>`).join('')}
            </select>
            <div class="zeilenaktion">
              ${SEITEN[r.seitenkey]?`<button onclick="seiteZeigen(${r.nr},this)">im Dokument wählen</button>`:''}
              ${r.pdflink?`<a class="lnk" href="${esc(r.pdflink)}" target="_blank">PDF, S.${esc(r.seite)} ↗</a>`:''}
            </div>
            ${mehrfach(r) ? `<input class="unt" maxlength="40"
                 placeholder="unterscheiden, z.B. Festhypothek 0.95 %"
                 value="${esc(s.unterscheidung!==undefined?s.unterscheidung:(r.unterscheidung||''))}"
                 oninput="setv(${r.nr},'unterscheidung',this.value)"
                 title="Position ${positionVon(r)} von mehreren gleicher Art in diesem Dokument">` : ''}
            </td>
          <td><input class="ausst" value="${esc(s.aussteller!==undefined?s.aussteller:r.aussteller)}"
              oninput="setv(${r.nr},'aussteller',this.value)"></td>
          <td><select onchange="setv(${r.nr},'person',this.value)">
              ${opts(ROLLEN, s.person!==undefined?s.person:r.person)}</select>
              <button class="mini" title="Diese Person für alle Zeilen dieses Dokuments übernehmen"
                      onclick="personAufBeleg(${r.nr})">↧ alle</button>
              ${r.personName && r.personName !== r.person
                ? `<div class="grund">aus dem Beleg: ${esc(r.personName)}</div>` : ''}</td>
          <td class="num"><input class="betrag" value="${esc(anzeige(s.betrag!==undefined?s.betrag:r.betrag))}"
              oninput="setv(${r.nr},'betrag',this.value)">
              <button onclick="kopieren(document.querySelector('#z${r.nr} .betrag').value,this)"
                      title="Betrag in die Zwischenablage">kopieren</button></td>
          <td>${r.ohne_nachweis?`<span class="k obn"
                 title="Dieser Betrag steht so nicht im Dokument — im Beleg wählen"
                 >nicht im Beleg</span> `:''}<span class="k ${esc(r.konfidenz)}">${esc(r.konfidenz)}</span>
              <div class="grund">${esc(r.grund)}</div></td>
          <td><input class="notiz" placeholder="nur falls nötig"
              value="${esc(s.notiz||'')}" oninput="setv(${r.nr},'notiz',this.value)"></td>
          <td class="chk">
            <button data-b class="${okB?'on':''}" title="Betrag stimmt"
                    onclick="bestaetigen(${r.nr},'okBetrag')">${okB?'✓ ':''}Betrag</button>
            <button data-p class="${okP?'on':''}" title="Person stimmt"
                    onclick="bestaetigen(${r.nr},'okPerson')">${okP?'✓ ':''}Person</button>
            <button title="Weitere Position gleicher Art aus diesem Dokument"
                    onclick="zeileDazu(${r.nr})">＋ Zeile</button>
            ${r.neu
              ? `<button onclick="zeileEntfernen(${r.nr})" title="Zeile entfernen">✕</button>`
              : `<button class="${weg?'weggestrichen':''}"
                   title="Dieser Wert steht nicht in diesem Dokument — er wurde faelschlich erkannt. Steht er in einem anderen Dokument, fordert die Vollstaendigkeitspruefung ihn weiterhin ein."
                   onclick="streichen(${r.nr})">${weg?'↺ zurück':'✕ nicht im Dokument'}</button>`}
          </td>
        </tr>
        <tr class="zusatz" id="zz${r.nr}">
          <td colspan="8">
            ${r.crop?`<img class="crop" src="${r.crop}" alt="Ausschnitt">`:''}
            <div class="seitenhalter" id="sh${r.nr}"></div>
          </td>
        </tr>`;}).join('')}
      </tbody></table>`;
}

// Übertragungsansicht: das Formular, nicht die Belege.
//
// Beim Ausfuellen arbeitet man die Steuererklaerung von oben nach unten ab.
// Eine nach Dokumenten sortierte Liste zwingt dann zum Hin- und Herspringen,
// und die Eingabefelder sind im Weg — beim Abschreiben will man lesen, nicht
// aus Versehen etwas veraendern. Deshalb: keine Felder, nur Werte, in der
// Reihenfolge des Formulars (260904-rmx).
function uWert(r, feld){
  const s = state[r.nr] || {};
  return s[feld] !== undefined ? s[feld] : r[feld];
}

function renderUebertragen(){
  const aktiv = R.filter(r => !istGestrichen(r) && passtZurArt(r.beleg)
                              && passtZurSuche(r));
  const gruppen = {};
  aktiv.forEach(r => (gruppen[uWert(r,'ziffer')||''] = gruppen[uWert(r,'ziffer')||'']||[]).push(r));
  const ziffern = Object.keys(gruppen).sort((a,b)=>{
    const A = zsort(a), B = zsort(b);
    for (let i=0;i<Math.max(A.length,B.length);i++){
      if ((A[i]??0) !== (B[i]??0)) return (A[i]??0)-(B[i]??0);
    }
    return 0;
  });
  const gestrichen = R.length - aktiv.length;
  const kopf = `<p class="uhint">So, wie das Formular aufgebaut ist. Beträge
    unformatiert — direkt zum Kopieren. Keine Eingabefelder: zum Ändern zurück
    auf „nach Beleg".${gestrichen?` <strong>${gestrichen} Position(en)</strong>
    sind als „nicht im Dokument" markiert und hier nicht enthalten.`:''}</p>`;
  if (!aktiv.length) return kopf + '<p class="hint">Keine Positionen.</p>';
  return kopf + ziffern.map(z => {
    const rs = gruppen[z];
    const sum = summe(rs);
    const name = ZIFFERN[z] || '';
    return `<section class="doc uebertrag">
      <p class="uh"><span class="uz">${z ? 'Ziffer '+esc(z) : 'ohne Ziffer'}</span>
        ${name?`<span class="un">${esc(name)}</span>`:''}
        ${sum!==null?`<span class="us">${sum}</span>
          <button onclick="kopieren('${sum}',this)">Summe kopieren</button>`:''}</p>
      <div class="wrap"><table><thead><tr>
        <th>Person</th><th>Aussteller</th><th>Beschreibung</th><th>Betrag</th><th></th>
      </tr></thead><tbody>
      ${rs.map(r => {
        const b = roh(uWert(r,'betrag'));
        return `<tr>
          <td>${esc(uWert(r,'person')||'—')}</td>
          <td>${esc(uWert(r,'aussteller')||'—')}</td>
          <td>${esc(uWert(r,'zielwert')||'')}${
                uWert(r,'unterscheidung')
                  ? ' (' + esc(uWert(r,'unterscheidung')) + ')' : ''}
              <span class="uq">${esc(r.beleg)}</span></td>
          <td class="num ub">${esc(b)}</td>
          <td><button onclick="kopieren('${esc(b)}',this)">kopieren</button></td>
        </tr>`;}).join('')}
      </tbody></table></div></section>`;
  }).join('');
}

// Ziffern numerisch sortieren: 16.6 gehoert zwischen 15 und 22.1.
function zsort(z){
  const t = String(z||'').trim().split('.').filter(x=>x!=='');
  const n = t.map(x=>parseInt(x,10));
  return (n.length && n.every(x=>!isNaN(x))) ? n : [999];
}

// Unter welcher Belegart steht diese Zeile gerade?
//
// Der gesetzte Zielwert schlaegt die Belegart des Dokuments. Sonst bleibt
// eine auf VVG umgestellte Zeile unter „Bankbelege" oder „Übrige" stehen,
// und die Gruppierung sieht kaputt aus, obwohl sie nur veraltet ist.
function typVonZeile(r){
  const s = state[r.nr] || {};
  const ziel = s.zielwert !== undefined ? s.zielwert : r.zielwert;
  return ZIELWERT_TYP[ziel] || r.typlabel || 'Übrige';
}

function renderNachTyp(zeilen){
  const typen = {};
  zeilen.forEach(r => {
    const t = typVonZeile(r);
    (typen[t] = typen[t] || []).push(r);
  });
  return Object.keys(typen).sort().map(typ => {
    const rs = typen[typ];
    const docs = {};
    rs.forEach(r => (docs[r.beleg] = docs[r.beleg] || []).push(r));
    const sum = summe(rs);
    return `<section class="doc">
      <p class="typh">${esc(typ)} <span class="anz">${rs.length} Werte aus
        ${Object.keys(docs).length} Dokument(en)</span>
        ${sum!==null?`<button onclick="kopieren('${sum}',this)">Summe ${sum} kopieren</button>`:''}</p>
      ${Object.keys(docs).sort().map(d => `
        <p class="doch sub">${esc(d)}</p>
        <div class="wrap">${tabelle(docs[d])}</div>`).join('')}
    </section>`;
  }).join('') || '<p class="hint">Keine Zeilen in dieser Ansicht.</p>';
}

function zaehlerSetzen(zeilen){
  const wahl = (r,f,v) => (state[r.nr]||{})[f] !== undefined
    ? (state[r.nr]||{})[f] : !!r[v];
  const b = R.filter(r => wahl(r,'okBetrag','vorabBetrag')).length;
  const pn = R.filter(r => wahl(r,'okPerson','vorabPerson')).length;
  document.getElementById('zaehler').textContent =
    `${zeilen.length} von ${R.length} Zeilen${suche ? ' (gesucht)' : ''} · `
    + `${b} Beträge und ${pn} Personen bestätigt`;
}

// --- Dokumentübersicht ------------------------------------------------------
//
// Der Einstieg: eine Zeile je Dokument im Ordner, mit Zustand. Vorher zeigte
// die Seite nur die gefundenen Werte und oben separat die Dokumente ohne
// Ergebnis — die Frage „welche sind schon durch?" liess sich nicht
// beantworten, ohne alles durchzuscrollen (260923-dua).

// Unter welchen Belegarten läuft dieses Dokument gerade? Eine auf „Einzahlung
// Säule 3a" umgestellte Zeile soll unter Säule 3a auffindbar sein, auch wenn
// der Beleg als Bankauszug erkannt wurde.
function artenVon(beleg){
  const aus = new Set();
  R.filter(x => x.beleg === beleg).forEach(x => aus.add(typVonZeile(x)));
  const d = DOKUMENTE.find(x => x.beleg === beleg);
  if (!aus.size && d) (d.arten || []).forEach(a => aus.add(a));
  return aus;
}

function passtZurArt(beleg){
  if (!artFilter) return true;
  return artenVon(beleg).has(artFilter);
}

function artenListe(){
  const zaehler = {};
  DOKUMENTE.forEach(d => artenVon(d.beleg).forEach(a => {
    zaehler[a] = (zaehler[a] || 0) + 1;
  }));
  return Object.keys(zaehler).sort().map(a => [a, zaehler[a]]);
}

function artfilterFuellen(){
  const el = document.getElementById('artfilter');
  if (!el) return;
  const jetzt = artFilter;
  el.innerHTML = ['<option value="">alle</option>'].concat(
    artenListe().map(([a, n]) =>
      `<option value="${esc(a)}"${a===jetzt?' selected':''}>${esc(a)} (${n})</option>`)
  ).join('');
}

function setArt(wert){ artFilter = wert; render(); }

function setZustand(z){
  zustandFilter = (zustandFilter === z) ? '' : z;
  render();
}

// Der Zustand wird live nachgerechnet: eine gerade gesetzte Bestätigung soll
// sich sofort in der Liste zeigen, nicht erst nach einem Neuaufbau.
function zustandVon(d){
  if (d.zustand === 'ausgeschlossen' || d.zustand === 'nicht_eingelesen')
    return d.zustand;
  const rs = R.filter(x => x.beleg === d.beleg);
  if (!rs.length) return 'ohne_fund';
  const wahl = (r,f,v) => (state[r.nr]||{})[f] !== undefined
    ? (state[r.nr]||{})[f] : !!r[v];
  const offen = rs.filter(r => !istGestrichen(r) && !wahl(r,'okBetrag','vorabBetrag'));
  return offen.length ? 'offen' : 'geprueft';
}

const ZUSTANDWORT = {geprueft:'geprüft', offen:'offen', ohne_fund:'ohne Fund',
                     ausgeschlossen:'ausgeschlossen',
                     nicht_eingelesen:'nicht eingelesen'};

function zumDokument(beleg){
  waehle(beleg);
  const el = document.getElementById('detail');
  if (el) el.scrollIntoView({block: 'start', behavior: 'smooth'});
}

// Rechte Spalte: genau ein Dokument.
//
// Die Tabelle aller Werte nebeneinander war bei 35 Dokumenten und 53
// Positionen nicht mehr zu bedienen — man scrollte und verlor die Stelle.
// Links steht, WAS zu tun ist; rechts arbeitet man an EINEM Beleg
// (260923-dua).
function renderDetail(){
  if (!gewaehlt)
    return `<p class="hint">Links ein Dokument wählen — dann steht hier alles
      dazu: die gefundenen Werte, das Seitenbild, und ＋ Position für einen
      weiteren Betrag im selben Beleg.</p>`;

  const rs = R.filter(r => r.beleg === gewaehlt
                           && passtFilter(r));
  const d = DOKUMENTE.find(x => x.beleg === gewaehlt);
  const off = OFFEN.find(x => x.beleg === gewaehlt);

  const kopf = `<p class="doch">${esc(gewaehlt)}
      <button onclick="waehle('')" title="Zurück zur Übersicht">✕</button>
      ${typWahl(gewaehlt, rs)}
      <button onclick="zeileHinzu('${esc(gewaehlt)}')">＋ Position</button>
      ${weiterKnopf()}</p>
    ${typHinweis(gewaehlt, rs)}`;

  if (!rs.length){
    return kopf + `<p class="hint">Für dieses Dokument gibt es keine
      übertragbare Position. Belegart setzen — auch „irrelevant" ist eine
      Antwort — oder mit ＋ Position einen Wert von Hand erfassen.</p>`
      + (off ? offenBlock(off) : '');
  }
  // Summe je Ziffer als Gegenprobe zum Total im Beleg.
  const jeZiffer = {};
  rs.filter(r => !istGestrichen(r)).forEach(r => {
    const z = uWert(r,'ziffer') || '—';
    (jeZiffer[z] = jeZiffer[z] || []).push(r);
  });
  const summen = Object.keys(jeZiffer).sort().map(z =>
    `<span class="zsum">Ziffer ${esc(z)}: <strong>${summe(jeZiffer[z])}</strong></span>`
  ).join(' ');
  return kopf + (summen ? `<p class="uhint">${summen}</p>` : '')
       + `<div class="wrap">${tabelle(rs)}</div>`;
}

// „Weiter zum nächsten offenen Dokument" — damit die Durchsicht eine Kette
// bleibt und man nicht nach jedem Beleg wieder suchen muss.
function naechstesOffenes(){
  const liste = DOKUMENTE.map(d => d.beleg);
  const ab = liste.indexOf(gewaehlt);
  for (let i = 1; i <= liste.length; i++){
    const name = liste[(ab + i) % liste.length];
    const d = DOKUMENTE.find(x => x.beleg === name);
    if (d && zustandVon(d) === 'offen') return name;
  }
  return '';
}

function weiterKnopf(){
  const n = naechstesOffenes();
  return n && n !== gewaehlt
    ? `<button class="pri" onclick="waehle('${esc(n)}')">nächstes offenes →</button>`
    : '';
}

function renderDokumente(){
  const mit = DOKUMENTE.map(d => ({...d, jetzt: zustandVon(d)}));
  const zaehler = {};
  mit.forEach(d => { zaehler[d.jetzt] = (zaehler[d.jetzt]||0) + 1; });
  const chips = ['geprueft','offen','ohne_fund','ausgeschlossen','nicht_eingelesen']
    .filter(z => zaehler[z])
    .map(z => `<button class="chip ${z} ${zustandFilter===z?'on':''}"
                 onclick="setZustand('${z}')">${zaehler[z]} ${ZUSTANDWORT[z]}</button>`)
    .join(' ');

  const sichtbar = mit.filter(d =>
    (!zustandFilter || d.jetzt === zustandFilter)
    && passtZurArt(d.beleg)
    && passtZurSuche({beleg: d.beleg}));

  // Erst die Arbeit, dann das Erledigte, zuletzt die nicht zugeordneten.
  //
  // Sie standen frueher in einem eigenen Kasten ganz oben. Dort waren sie das
  // Erste, was man sah — obwohl sie meist das Letzte sind, worum man sich
  // kuemmert (260923-dua).
  const RANG = {offen: 0, geprueft: 1, ohne_fund: 2, nicht_eingelesen: 3,
                ausgeschlossen: 4};
  sichtbar.sort((a, b) => (RANG[a.jetzt] ?? 9) - (RANG[b.jetzt] ?? 9)
                          || a.beleg.localeCompare(b.beleg, 'de'));

  const kopf = `<p class="uhint"><strong>${mit.length} Dokument(e)</strong>
    im Ordner. ${chips}
    ${(zustandFilter||artFilter||suche)
      ? `<button onclick="filterWeg()">Filter zurücksetzen</button>` : ''}</p>`;

  if (!sichtbar.length)
    return kopf + '<p class="hint">Kein Dokument passt zu dieser Auswahl.</p>';

  const zeilen = sichtbar.map(d => {
    const rs = R.filter(x => x.beleg === d.beleg);
    const arten = [...artenVon(d.beleg)].sort().join(', ') || '—';
    const fortschritt = rs.length
      ? `${rs.length} Position(en)` +
        (d.jetzt === 'offen'
          ? `, ${rs.filter(r => !istGestrichen(r)
              && !((state[r.nr]||{}).okBetrag !== undefined
                   ? (state[r.nr]||{}).okBetrag : !!r.vorabBetrag)).length} offen`
          : '')
      : (d.grund || '—');
    // Sprechender Dateiname: erst zeigen, dann auf Knopfdruck umbenennen.
    const name = d.neuername
      ? `<div class="neuname">→ ${esc(d.neuername)}
           ${LIVE ? `<button onclick="umbenennen('${esc(d.beleg)}',this)"
                       title="Originaldatei so benennen">umbenennen</button>` : ''}</div>`
      : (d.namengrund && d.namengrund !== 'heisst schon so'
          ? `<div class="grund">Name: ${esc(d.namengrund)}</div>` : '');
    return `<li class="dokzeile ${d.jetzt} ${d.beleg === gewaehlt ? 'aktiv' : ''}">
      <div class="kopf">
        <span class="zw ${d.jetzt}">${ZUSTANDWORT[d.jetzt]}</span>
        <span class="dokname"><button class="alslink"
          onclick="zumDokument('${esc(d.beleg)}')">${esc(d.beleg)}</button></span>
      </div>
      <div class="meta">${esc(arten)} · ${esc(fortschritt)}</div>
      ${name ? `<div class="meta">${name}</div>` : ''}
      <div class="tat"><button onclick="zeileHinzu('${esc(d.beleg)}')"
          title="Weitere Position in diesem Dokument erfassen">＋</button></div>
    </li>`;
  }).join('');

  const umb = sichtbar.filter(d => d.neuername).length;
  return kopf
    + (LIVE && umb
        ? `<p class="uhint">${umb} Dokument(e) könnten sprechend heissen.
            <button class="pri" onclick="umbenennenAlle()">alle umbenennen</button>
            <span class="dim">Die Durchsicht wandert mit; rückgängig mit
            <code>make umbenennen-zurueck</code>.</span></p>` : '')
    + `<ul class="dokliste">${zeilen}</ul>`;
}

// Umbenennen — nur im Live-Betrieb, und immer erst nach dem Speichern.
//
// Der Dateiname ist der Schluessel, unter dem die ganze Durchsicht haengt.
// Wuerde umbenannt, bevor die offenen Aenderungen beim Server sind, zeigten
// sie danach auf ein Dokument, das es nicht mehr gibt.
async function umbenennen(beleg, knopf){
  if (!LIVE) return;
  if (knopf) knopf.disabled = true;
  try {
    await sendeKorrekturen();
    const antwort = await fetch('/api/umbenennen', {
      method: 'POST',
      headers: {'Content-Type':'application/json','X-Steuer-Token': LIVE},
      body: JSON.stringify({beleg}),
    });
    const daten = await antwort.json();
    if (!antwort.ok || daten.fehler) throw new Error(daten.fehler || 'Fehler');
    livestatus(`${daten.anzahl} Datei(en) umbenannt — Seite wird neu geladen`,
               'ok');
    setTimeout(() => location.reload(), 900);
  } catch (e) {
    if (knopf) knopf.disabled = false;
    livestatus('Umbenennen fehlgeschlagen — nichts geändert', 'fehler');
  }
}

function umbenennenAlle(){ return umbenennen(null, null); }

// Beide Spalten enden genau am Fensterrand.
//
// Mit einer festen Rechnung (100vh minus X) bleibt darunter Platz uebrig oder
// es fehlt welcher — und dann scrollt die Seite als DRITTER Bereich mit. Drei
// Scrollbalken sind einer zu viel: man weiss nie, welcher gerade dran ist
// (260923-dua).
function hoehenSetzen(){
  document.querySelectorAll('.zwei .liste, .zwei .detail').forEach(el => {
    if (window.innerWidth <= 1000){ el.style.maxHeight = ''; return; }
    const oben = el.getBoundingClientRect().top + window.scrollY;
    el.style.maxHeight = Math.max(240, window.innerHeight - oben - 16) + 'px';
  });
}

window.addEventListener('resize', hoehenSetzen);

function filterWeg(){
  zustandFilter = ''; artFilter = ''; suche = '';
  const f = document.getElementById('suche'); if (f) f.value = '';
  const a = document.getElementById('artfilter'); if (a) a.value = '';
  render(); renderOffen();
}

// Scrollposition beider Spalten ueber ein Neuzeichnen retten.
//
// `render()` ersetzt das ganze innerHTML; ohne das hier steht man danach
// wieder oben. Das ist selbst dann stoerend, wenn es sein muss — etwa beim
// Streichen, wo eine Zeile verschwindet (260923-dua).
function _scrollMerken(){
  const w = {};
  document.querySelectorAll('.zwei .liste, .zwei .detail').forEach(el => {
    w[el.className] = el.scrollTop;
  });
  return {spalten: w, seite: window.scrollY};
}

function _scrollZurueck(stand){
  if (!stand) return;
  document.querySelectorAll('.zwei .liste, .zwei .detail').forEach(el => {
    if (stand.spalten[el.className] !== undefined)
      el.scrollTop = stand.spalten[el.className];
  });
  window.scrollTo(0, stand.seite);
}

function render(){
  const _stand = _scrollMerken();
  const host = document.getElementById('inhalt');
  artfilterFuellen();
  if (grpModus==='dokumente'){
    host.innerHTML = `<div class="zwei">
        <div class="liste">${renderDokumente()}</div>
        <div class="detail" id="detail">${renderDetail()}</div>
      </div>`;
    zaehlerSetzen(R.filter(r => passtZurArt(r.beleg) && passtZurSuche(r)));
    hoehenSetzen();
    _scrollZurueck(_stand);
    return;
  }
  const zeilen = R.filter(r => passtFilter(r)
                              && passtZurArt(r.beleg)
                              && passtZurSuche(r));
  const gruppen = {};
  zeilen.forEach(r => {
    const k = grpModus==='ziffer' ? ('Ziffer '+r.ziffer) : r.beleg;
    (gruppen[k] = gruppen[k] || []).push(r);
  });
  if (grpModus==='uebertragen'){ host.innerHTML = renderUebertragen();
    zaehlerSetzen(zeilen); _scrollZurueck(_stand); return; }
  if (grpModus==='typ'){ host.innerHTML = renderNachTyp(zeilen);
    zaehlerSetzen(zeilen); _scrollZurueck(_stand); return; }
  host.innerHTML = Object.keys(gruppen).sort().map(beleg => {
    const rs = gruppen[beleg];
    const sum = grpModus==='ziffer' ? summe(rs) : null;
    const kopf = sum!==null
      ? `${esc(beleg)} — Summe <strong>${sum}</strong>
         <button onclick="kopieren('${sum}',this)">Summe kopieren</button>`
      : esc(beleg);
    const dok = grpModus==='beleg' ? beleg : null;
    return `<section class="doc"><p class="doch">${kopf}
      ${dok?`<button onclick="zeileHinzu('${esc(dok)}')">+ Zeile</button>`:''}
      ${dok?typWahl(dok, rs):''}</p>
      ${dok?typHinweis(dok, rs):''}
      <div class="wrap">${tabelle(rs)}</div></section>`;
  }).join('') || '<p class="hint">Keine Zeilen in dieser Ansicht.</p>';
  const wahl = (r,f,v) => (state[r.nr]||{})[f] !== undefined
    ? (state[r.nr]||{})[f] : !!r[v];
  const b = R.filter(r => wahl(r,'okBetrag','vorabBetrag')).length;
  const pn = R.filter(r => wahl(r,'okPerson','vorabPerson')).length;
  document.getElementById('zaehler').textContent =
    `${zeilen.length} von ${R.length} Zeilen${suche ? ' (gesucht)' : ''} · `
    + `${b} Beträge und ${pn} Personen bestätigt`;
  _scrollZurueck(_stand);
}

const BKEY = 'steuer-befunde:' + KORPUS;
let bWeg = {};
try { bWeg = JSON.parse(localStorage.getItem(BKEY) || '{}'); } catch(e) { bWeg = {}; }

function befundWeg(el){
  const li = el.closest('li'), k = li.dataset.bef;
  bWeg[k] = !bWeg[k];
  li.classList.toggle('erledigt', !!bWeg[k]);
  el.textContent = bWeg[k] ? 'wieder zeigen' : 'gibt es nicht';
  try { localStorage.setItem(BKEY, JSON.stringify(bWeg)); } catch(e){}
}

document.querySelectorAll('.befunde li[data-bef]').forEach(li => {
  if (bWeg[li.dataset.bef]) {
    li.classList.add('erledigt');
    const b = li.querySelector('.weg'); if (b) b.textContent = 'wieder zeigen';
  }
});

function setv(nr, feld, wert){ (state[nr] = state[nr]||{})[feld] = wert; sichern(); }

// Zielwert geaendert — die Ziffer kommt aus dem Feldvertrag mit.
//
// Sie bleibt danach von Hand ueberschreibbar; automatisch gesetzt wird sie,
// weil sonst genau der Fehler passiert, den man vermeiden wollte: der Wert
// heisst "Einzahlung Säule 3a" und steht trotzdem unter Ziffer 30.1.
// Kein render() — das wuerde den Cursor aus dem Feld werfen.
// Aus dem Auswahlfeld in das Textfeld — der Zielwert bleibt frei
// ueberschreibbar, aber der haeufige Fall geht mit zwei Klicks.
function zielWaehlen(nr, el){
  const wert = el.value;
  el.selectedIndex = 0;
  if (!wert) return;
  const feld = document.getElementById('ziel' + nr);
  if (feld) feld.value = wert;
  setZiel(nr, wert);
  render();
  befundeNeu();
}

function setZiel(nr, wert){
  setv(nr, 'zielwert', wert);
  const z = ZIELWERT_ZIFFER[wert];
  if (!z) return;
  setv(nr, 'ziffer', z);
  const feld = document.querySelector('#z' + nr + ' .ziffer');
  if (feld) feld.value = z;
}
// Bestaetigen zeichnet NICHT die ganze Seite neu.
//
// Es tat es, und dabei ging die Scrollposition verloren: wer die Person
// bestaetigte, landete wieder oben und musste zur selben Zeile zurueck, um
// auch den Betrag zu bestaetigen (260923-dua, vom Nutzer gemeldet). An der
// Zeile aendert sich nur ein Haken — den setzen wir an Ort und Stelle.
function bestaetigen(nr, feld){
  const s = state[nr] = state[nr]||{};
  const r = R.find(x => x.nr === nr) || {};
  const vorab = feld === 'okBetrag' ? !!r.vorabBetrag : !!r.vorabPerson;
  s[feld] = s[feld] !== undefined ? !s[feld] : !vorab;
  sichern();

  const zeile = document.getElementById('z' + nr);
  const knopf = zeile && zeile.querySelector(
    feld === 'okBetrag' ? '.chk button[data-b]' : '.chk button[data-p]');
  if (knopf){
    knopf.classList.toggle('on', !!s[feld]);
    knopf.textContent = (s[feld] ? '✓ ' : '') +
      (feld === 'okBetrag' ? 'Betrag' : 'Person');
    zaehlerNeu();
    listenzeileNeu();
    return;
  }
  // Findet sich die Zeile nicht (andere Ansicht), bleibt der volle Weg.
  render();
}

// Nur die Zaehlzeile im Kopf nachrechnen.
function zaehlerNeu(){
  const el = document.getElementById('zaehler');
  if (!el) return;
  const wahl = (r,f,v) => (state[r.nr]||{})[f] !== undefined
    ? (state[r.nr]||{})[f] : !!r[v];
  const b = R.filter(r => wahl(r,'okBetrag','vorabBetrag')).length;
  const pn = R.filter(r => wahl(r,'okPerson','vorabPerson')).length;
  const sichtbar = R.filter(r => passtZurArt(r.beleg) && passtZurSuche(r));
  el.textContent = `${sichtbar.length} von ${R.length} Zeilen`
    + `${suche ? ' (gesucht)' : ''} · ${b} Beträge und ${pn} Personen bestätigt`;
}

// Den Zustand des gewaehlten Dokuments links nachziehen, ohne die Liste neu
// zu bauen — sonst verliert auch sie ihre Scrollposition.
function listenzeileNeu(){
  if (!gewaehlt) return;
  const d = DOKUMENTE.find(x => x.beleg === gewaehlt);
  if (!d) return;
  const jetzt = zustandVon(d);
  document.querySelectorAll('.dokzeile.aktiv').forEach(li => {
    li.className = `dokzeile ${jetzt} aktiv`;
    const marke = li.querySelector('.zw');
    if (marke){ marke.className = 'zw ' + jetzt;
                marke.textContent = ZUSTANDWORT[jetzt]; }
  });
}

function setGrp(m, el){
  grpModus = m;
  document.querySelectorAll('.bar button[data-g]').forEach(b=>b.classList.remove('on'));
  el.classList.add('on');
  render();
}

// Suche ueber Dokumentname, Zielwert, Aussteller und Person.
//
// Bei 32 Dokumenten und 60 Zeilen hiess „ein Dokument finden" bisher
// scrollen. Wer einen Namen aus einem PDF-Link kopiert, bringt ausserdem
// dessen Prozentkodierung mit — „Kontoauszug%20-%202025" findet sonst
// nichts, obwohl das Dokument da ist (260904-rmx).
let suche = '';

function suchNorm(t){
  return String(t == null ? '' : t)
    .replace(/%20/g, ' ')
    .replace(/[_\-]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .toLowerCase();
}

function passtZurSuche(r){
  if (!suche) return true;
  const heu = [r.beleg, r.zielwert, r.aussteller, r.person, r.personName,
               r.ziffer].map(suchNorm).join(' ');
  return suche.split(' ').every(w => heu.includes(w));
}

function setSuche(t){
  suche = suchNorm(t);
  render();
  renderOffen();
}

/* Ein Filter, zwei Arten von Wert: Konfidenz oder „ohne Nachweis". */
function passtFilter(r){
  if (filter === 'alle') return true;
  if (filter === 'ohne_nachweis') return !!r.ohne_nachweis;
  return r.konfidenz === filter;
}

function setFilter(f, el){
  filter = f;
  document.querySelectorAll('.bar button[data-f]').forEach(b=>b.classList.remove('on'));
  el.classList.add('on');
  render();
}

function csvFeld(v){
  v = v==null ? '' : String(v);
  return /[",\n]/.test(v) ? '"'+v.replace(/"/g,'""')+'"' : v;
}

// Welche Position derselben Art ist diese Zeile in ihrem Dokument?
//
// Ein Hypothekarbeleg nennt drei Hypotheken und drei Schuldzinsen. Ohne
// laufende Nummer teilen sie sich den Schluessel "<Beleg>|<Zielwert>": die
// zweite ueberschrieb die erste, und eine selbst erfasste Position fiel beim
// Tabellenbau ganz weg, weil der Schluessel schon belegt war (260923-dua).
//
// Zeilen aus dem Lauf behalten die Nummer, die beim Tabellenbau vergeben
// wurde. Selbst erfasste reihen sich dahinter ein, in der Reihenfolge ihrer
// Anlage — `nr` waechst monoton, die Zuordnung bleibt damit ueber Neuaufbauten
// stabil.
function zielVon(r){
  const s = state[r.nr] || {};
  return s.zielwert !== undefined ? s.zielwert : (r.zielwert || '');
}

// Gibt es in diesem Dokument mehrere Positionen dieser Art?
//
// Nur dann ist das Unterscheidungsfeld sinnvoll — bei einer einzigen Zeile
// waere es eine leere Spalte mehr in einer ohnehin breiten Tabelle.
function mehrfach(r){
  const zw = zielVon(r);
  return R.filter(x => x.beleg === r.beleg && zielVon(x) === zw
                       && !istGestrichen(x)).length > 1;
}

function positionVon(r){
  if (!r.neu) return r.pos || 1;
  const s = state[r.nr] || {};
  if (s.posNr) return s.posNr;
  // Einmal vergeben, dann festgeschrieben. Wuerde die Nummer bei jedem
  // Rendern neu gerechnet, ruecken nach dem Streichen der zweiten Hypothek
  // alle folgenden auf — ein bestaetigter Sollwert zeigte danach auf eine
  // ANDERE Hypothek, und die Pruefung bliebe gruen, obwohl die Zahl falsch
  // ist. Luecken sind das kleinere Uebel als Verschiebung (260923-dua).
  const zw = zielVon(r);
  const gleiche = R.filter(x => x.beleg === r.beleg && zielVon(x) === zw);
  const belegt = new Set(gleiche.map(x => x.nr === r.nr ? 0
    : (x.neu ? ((state[x.nr] || {}).posNr || 0) : (x.pos || 1))));
  let n = 1;
  while (belegt.has(n)) n++;
  (state[r.nr] = state[r.nr] || {}).posNr = n;
  return n;
}

function korrekturZeilen(alle){
  const kopf = ['nr','beleg','ziffer','zielwert','person','aussteller','betrag',
                'konfidenz','grund','herkunft',
                'korrektur','person_neu','aussteller_neu','zielwert_neu','ziffer_neu',
                'bestaetigt','streichen','zuruecknehmen',
                'pos_x','pos_y','pos_seite','zeilenkontext','notiz','position',
                'unterscheidung'];
  const zeilen = [kopf.join(',')];
  // Welche Schluessel diese Nutzlast traegt — damit eine Ruecknahme nicht
  // eine gerade wieder angelegte Position trifft.
  const gesendet = new Set();
  let n = 0;
  R.forEach(r => {
    const s = state[r.nr] || {};
    // Korrektur nur, wenn der Betrag geaendert wurde.
    // Verglichen wird gegen den Stand AUS DEM BELEG, nicht gegen den bereits
    // korrigierten. Sonst gilt jede uebernommene Korrektur als „unveraendert",
    // faellt beim Zurueckschreiben aus dem Eintrag und ist beim naechsten Lauf
    // verschwunden — so gingen die Personenzuordnungen verloren (260904-rmx).
    const jetzt = (f, rf) => {
      const v = s[f] !== undefined ? s[f] : r[f];
      const basis = r[rf] !== undefined ? r[rf] : r[f];
      return (v !== undefined && String(v||'') !== String(basis||'')) ? v : '';
    };
    const korr = jetzt('betrag', 'rohBetrag');
    const person = jetzt('person', 'rohPerson');
    const ausst = jetzt('aussteller', 'rohAussteller');
    const ziel2 = jetzt('zielwert', 'rohZielwert');
    const ziff2 = jetzt('ziffer', 'rohZiffer');
    const eB = s.okBetrag !== undefined ? s.okBetrag : !!r.vorabBetrag;
    const eP = s.okPerson !== undefined ? s.okPerson : !!r.vorabPerson;
    if (r.neu){
      const w = s.betrag!==undefined?s.betrag:r.betrag;
      if (!w) return;
      n++;
      const pn = s.pos || {};
      gesendet.add(`${r.beleg}|${s.zielwert||r.zielwert}|${positionVon(r)}`);
      zeilen.push([r.nr, r.beleg, s.ziffer||r.ziffer, s.zielwert||r.zielwert,
                   s.person||'', s.aussteller||'', '', 'manuell', 'selbst erfasst',
                   'mensch', w, s.person||'', s.aussteller||'',
                   s.zielwert||r.zielwert, s.ziffer||r.ziffer, '', '', '',
                   pn.x??'', pn.y??'', pn.seite??'', pn.kontext??'',
                   ['NEU', s.notiz||''].filter(Boolean).join(' | '),
                   positionVon(r), s.unterscheidung||'']
                  .map(csvFeld).join(','));
      return;
    }
    const best = [eB?'Betrag':'', eP?'Person':''].filter(Boolean).join('+');
    const gestr = istGestrichen(r) ? 'ja' : '';
    // Im Live-Betrieb geht JEDE Zeile mit, auch die unveraenderte: der Server
    // nimmt den Stand dann als vollstaendig und kann einen zurueckgenommenen
    // Eintrag loeschen. Beim CSV-Weg waere das nur Ballast — dort nur, was
    // sich geaendert hat.
    if (!alle && !korr && !person && !ausst && !ziel2 && !ziff2 && !best
        && !gestr && !s.notiz) return;
    if (korr || person || ausst || ziel2 || ziff2 || best || gestr || s.notiz) n++;
    const pos = s.pos || {};
    gesendet.add(`${r.beleg}|${r.zielwert}|${positionVon(r)}`);
    zeilen.push([r.nr, r.beleg, r.ziffer, r.zielwert, r.person, r.aussteller,
                 roh(r.betrag), r.konfidenz, r.grund, r.herkunft,
                 korr?roh(korr):'', person, ausst, ziel2, ziff2, best, gestr,
                 '', pos.x??'', pos.y??'', pos.seite??'', pos.kontext??'',
                 s.notiz||'', positionVon(r), s.unterscheidung||'']
                .map(csvFeld).join(','));
  });
  OFFEN.forEach(d => {
    const s = oState[d.beleg] || {};
    if (!s.typ) return;
    n++;
    const notiz = s.typ === 'irrelevant'
      ? 'als irrelevant markiert'
      : ['Belegart: '+s.typ, s.person?('Person: '+s.person):''].filter(Boolean).join(' | ');
    // Spaltenzahl muss zum Kopf passen. Fruehere Fassung schrieb 12 Werte in
    // eine 22-spaltige Datei: die Notiz landete in person_neu, und damit
    // wurde "Belegart: kk_praemienbescheinigung" als Personenname
    // zurueckgelesen (260904-rmx).
    const leer = (k) => Array(k).fill('');
    zeilen.push(['', d.beleg, '', s.typ, s.person||'', '', '', 'manuell',
                 d.grund, 'mensch', s.wert||'',
                 ...leer(kopf.length - 14), notiz, '', ''].map(csvFeld).join(','));
  });
  // Selbst erfasste Zeilen, die der Mensch wieder entfernt hat: ohne
  // ausdrueckliche Meldung bliebe ihr Eintrag stehen und die Zeile kaeme beim
  // naechsten Aufbau zurueck.
  // Ausdrueckliches Zuruecknehmen: ohne diese Spalte war der Loeschknopf
  // wirkungslos — die Zeile ging als "nichts geaendert" durch und kam beim
  // naechsten Aufbau zurueck (260904-rmx).
  // Auch im CSV-Weg: ohne die Ruecknahmezeile war der Loeschknopf dort
  // wirkungslos, und `exportieren()` brach mit "Nichts geändert" ab, obwohl
  // eine Zeile geloescht war (260923-dua).
  geloescht.forEach(g => {
    // Lebt der Schluessel in derselben Nutzlast wieder, ist die Ruecknahme
    // ueberholt — sonst loescht sie eine gerade neu angelegte Position
    // sofort wieder.
    if (gesendet.has(`${g.beleg}|${g.zielwert}|${g.pos || 1}`)) return;
    n++;
    const zeile = kopf.map(k => k === 'beleg' ? g.beleg
                              : k === 'zielwert' ? g.zielwert
                              : k === 'position' ? (g.pos || 1)
                              : k === 'zuruecknehmen' ? 'ja' : '');
    zeilen.push(zeile.map(csvFeld).join(','));
  });
  return {kopf, zeilen, anzahl: n};
}

function exportieren(){
  const {zeilen, anzahl} = korrekturZeilen(false);
  if (!anzahl){ alert('Nichts geändert — es gibt nichts zu exportieren.'); return; }
  const blob = new Blob([zeilen.join('\n')+'\n'], {type:'text/csv;charset=utf-8'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'korrektur.csv';
  a.click();
  URL.revokeObjectURL(a.href);
}

// --- Live-Betrieb ---------------------------------------------------------
//
// Liegt ein Token vor, laeuft die Seite hinter dem lokalen Server und kann
// zurueckschreiben. Dann entfaellt der ganze Umweg ueber CSV-Export und
// apply_korrekturen: jede Aenderung geht sofort an 127.0.0.1 und landet in
// korrekturen.json.
//
// Verschickt wird immer der vollstaendige Stand, nicht ein Delta. Das ist
// mehr Bytes und dafuer eine Sorge weniger: es gibt keinen Zustand, der beim
// Server anders aussieht als im Browser, auch nicht nach einem verlorenen
// Request.
const LIVE = window.__LIVE__ || '';
let sendeZeit = null, sendeLaeuft = false, nochmal = false;

// Der Server hat eine Speicherung verweigert, weil sie Bestaetigtes
// geloescht haette. Das darf nicht als Statuszeile verpuffen: es bleibt
// stehen, bis die Seite neu geladen wird (260923-dua).
function schutzbanner(j){
  let el = document.getElementById('schutzbanner');
  if (!el){
    el = document.createElement('div');
    el.id = 'schutzbanner';
    el.className = 'schutzbanner';
    const m = document.querySelector('main');
    if (m && m.firstChild) m.insertBefore(el, m.firstChild);
    else if (m) m.appendChild(el);
  }
  const liste = (j && j.verloren || []).join(', ');
  el.textContent = 'Nicht gespeichert. ' + ((j && j.fehler) || '')
    + (liste ? ' Betroffen: ' + liste : '')
    + ' Deine bisherigen Werte auf der Platte sind unveraendert.';
}

function livestatus(text, art){
  const el = document.getElementById('livestatus');
  if (!el) return;
  el.textContent = text;
  el.className = 'live ' + (art || '');
}

async function sendeKorrekturen(){
  if (!LIVE) return;
  if (sendeLaeuft){ nochmal = true; return; }
  sendeLaeuft = true;
  const {kopf, zeilen, anzahl} = korrekturZeilen(true);
  // Aus den CSV-Zeilen wieder Objekte machen — derselbe Spaltenvertrag wie
  // im Datei-Weg, damit der Server nur eine Auswertung braucht.
  const daten = zeilen.slice(1).map(z => {
    const f = csvZerlegen(z), o = {};
    kopf.forEach((k,i) => { o[k] = f[i] !== undefined ? f[i] : ''; });
    return o;
  });
  try {
    livestatus('speichert …');
    const antwort = await fetch('/api/korrekturen', {
      method: 'POST',
      headers: {'Content-Type':'application/json','X-Steuer-Token': LIVE},
      body: JSON.stringify({zeilen: daten}),
    });
    const j = await antwort.json();
    if (antwort.status === 409){ schutzbanner(j); }
    livestatus(antwort.ok
      ? `${anzahl} Änderung(en) gespeichert`
      : 'nicht gespeichert: ' + (j.fehler || antwort.status),
      antwort.ok ? 'ok' : 'fehler');
  } catch (e) {
    livestatus('nicht gespeichert — Server erreichbar?', 'fehler');
  } finally {
    sendeLaeuft = false;
    if (nochmal){ nochmal = false; sendeKorrekturen(); }
  }
}

// Tippen soll nicht bei jedem Zeichen einen Request ausloesen.
function livesichern(){
  if (!LIVE) return;
  clearTimeout(sendeZeit);
  sendeZeit = setTimeout(sendeKorrekturen, 600);
}

function csvZerlegen(zeile){
  const out = []; let feld = '', quote = false;
  for (let i = 0; i < zeile.length; i++){
    const c = zeile[i];
    if (quote){
      if (c === '"'){ if (zeile[i+1] === '"'){ feld += '"'; i++; } else quote = false; }
      else feld += c;
    }
    else if (c === '"') quote = true;
    else if (c === ','){ out.push(feld); feld = ''; }
    else feld += c;
  }
  out.push(feld);
  return out;
}

async function tabelleBauen(el){
  if (!LIVE) return;
  const alt = el.textContent;
  el.textContent = 'baut …'; el.disabled = true;
  try {
    await sendeKorrekturen();
    const antwort = await fetch('/api/tabelle', {
      method: 'POST',
      headers: {'Content-Type':'application/json','X-Steuer-Token': LIVE},
      body: JSON.stringify({}),
    });
    const j = await antwort.json();
    livestatus(antwort.ok
      ? 'Übertragungstabelle neu gebaut in ' + (j.ordner || '')
      : 'Fehler beim Bauen — siehe Terminal', antwort.ok ? 'ok' : 'fehler');
  } catch (e) {
    livestatus('Server nicht erreichbar', 'fehler');
  } finally {
    el.textContent = alt; el.disabled = false;
  }
}


// --- Offene Dokumente: Typ setzen, Werte im Dokument markieren -------------
const OFFEN = window.__OFFEN__ || [];
const OKEY = 'steuer-offen:' + KORPUS;
let oState = {};
try { oState = JSON.parse(localStorage.getItem(OKEY) || '{}'); } catch(e) { oState = {}; }
function oSichern(){ try { localStorage.setItem(OKEY, JSON.stringify(oState)); } catch(e){} }

// In der Dokumentansicht steht der Block rechts im Detail, sonst im eigenen
// Kasten — neu gezeichnet wird das, was gerade sichtbar ist.
function offenNeu(){
  if (grpModus === 'dokumente') render(); else renderOffen();
}

function oSetv(beleg, feld, wert){
  (oState[beleg] = oState[beleg]||{})[feld] = wert;
  oSichern();
  if (feld === 'typ') offenNeu();
}

function wortKlick(beleg, i){
  const d = OFFEN.find(x => x.beleg === beleg);
  if (!d || !d.seite) return;
  const s = oState[beleg] = oState[beleg]||{};
  s.markiert = s.markiert || [];
  const pos = s.markiert.indexOf(i);
  if (pos >= 0) s.markiert.splice(pos, 1); else s.markiert.push(i);
  s.markiert.sort((a,b)=>a-b);
  s.wert = s.markiert.map(j => d.seite.worte[j].t).join(' ');
  oSichern(); offenNeu();
}

function befehlKopieren(beleg, typ, el){
  const t = (typ && typ !== 'irrelevant') ? ` TYP=${typ}` : '';
  kopieren(`make reextract PDF="${beleg}"${t}`, el);
}

// Die Bearbeitung eines nicht zugeordneten Dokuments — Belegart, Person,
// Wert und das Seitenbild zum Anklicken.
//
// Stand frueher in einem eigenen Kasten ganz oben. Die Dokumente sind aber
// laengst in der Liste links (Zustand „ohne Fund" bzw. „nicht eingelesen");
// der Kasten war eine Dopplung und nahm den Arbeitsspalten Platz weg. Jetzt
// erscheint er rechts, wenn man das Dokument waehlt (260923-dua).
function offenBlock(d){
      const s = oState[d.beleg] || {};
      const typ = s.typ || '';
      const irrelevant = typ === 'irrelevant';
      return `<div class="offen ${irrelevant?'weg':''}">
        <p class="doch">${esc(d.beleg)}
          <span class="anz">${esc(d.grund)}</span>
          ${d.pdflink?`<a class="lnk" href="${esc(d.pdflink)}" target="_blank">öffnen ↗</a>`:''}</p>
        <div class="ozeile">
          <label>Belegart
            <select onchange="oSetv('${esc(d.beleg)}','typ',this.value)">
              ${opts(TYPEN, typ)}</select></label>
          ${(typ && !irrelevant) ? `
            <label>Person
              <select onchange="oSetv('${esc(d.beleg)}','person',this.value)">
                ${opts(ROLLEN, s.person||'')}</select></label>
            <label>Wert
              <input value="${esc(s.wert||'')}" placeholder="markieren oder eintippen"
                oninput="oSetv('${esc(d.beleg)}','wert',this.value)"></label>
            <button onclick="befehlKopieren('${esc(d.beleg)}','${esc(typ)}',this)">
              Befehl für neuen Versuch kopieren</button>` : ''}
        </div>
        ${(typ && !irrelevant && d.seite) ? `
          <p class="hint2">Im Dokument anklicken, um den Wert zu übernehmen —
            der Text kommt exakt aus dem PDF.</p>
          <div class="seite" style="width:${d.seite.breite}px">
            <img src="${d.seite.bild}" width="${d.seite.breite}" height="${d.seite.hoehe}" alt="Seite 1">
            ${d.seite.worte.map((w,i)=>`<span class="wort${(s.markiert||[]).includes(i)?' an':''}"
              style="left:${w.x}px;top:${w.y}px;width:${w.w}px;height:${w.h}px"
              onclick="wortKlick('${esc(d.beleg)}',${i})" title="${esc(w.t)}"></span>`).join('')}
          </div>` : ''}
      </div>`;
}

// In den uebrigen Ansichten (nach Beleg, nach Ziffer, Uebertragen) gibt es
// keine Detailspalte — dort bleibt der Kasten, sonst waeren die Dokumente
// dort unerreichbar.
function renderOffen(){
  const host = document.getElementById('offen');
  if (!host) return;
  if (grpModus === 'dokumente'){ host.innerHTML = ''; return; }
  const sichtbar = OFFEN.filter(d => passtZurSuche({beleg: d.beleg}));
  if (!sichtbar.length){ host.innerHTML = ''; return; }
  host.innerHTML = `<section class="doc"><p class="typh">Nicht zugeordnete Dokumente
    <span class="anz">${sichtbar.length} — hier geht sonst etwas verloren</span></p>
    ${sichtbar.map(offenBlock).join('')}</section>`;
}

renderOffen();
render();
befundeNeu();

// Im Live-Betrieb ist der CSV-Download nur noch der Notausgang.
if (LIVE){
  const b = document.getElementById('bauen');
  if (b) b.hidden = false;
  // Der Absatz beschreibt den CSV-Rueckweg — im Live-Betrieb gibt es den
  // nicht, und er nimmt den Arbeitsspalten eine Bildschirmhoehe weg.
  const h = document.getElementById('csvhinweis');
  if (h) h.remove();
  const t = document.getElementById('tabtab');
  if (t) t.hidden = false;
  document.querySelectorAll('.bar button.pri').forEach(x => {
    if (x.id !== 'bauen') x.classList.remove('pri');
  });
  livestatus('bereit — Änderungen werden gespeichert', 'ok');
}
"""


def _befunde_html(befunde: list) -> str:
    """Vollstaendigkeits- und Konsistenzbefunde als Kasten oben auf der Seite."""
    if not befunde:
        return ('<div class="ok-box" id="befundkasten"><p class="bt">Vollständigkeitsprüfung ohne Befund — '
                'für alle Familienmitglieder liegt eine Grundversicherung vor, '
                'und kein Beleg widerspricht sich.</p></div>')
    zeilen = "".join(
        f'<li data-bef="{html.escape(b.code)}|{html.escape(b.betrifft)}">'
        f'<span class="sw {html.escape(b.schwere)}">{html.escape(b.schwere)}</span> '
        f'{html.escape(b.text)}'
        + (f' <em>({html.escape(b.betrifft)})</em>' if b.betrifft else '')
        + f'<button class="weg" onclick="befundWeg(this)">gibt es nicht</button>'
        + '</li>'
        for b in befunde
    )
    return (f'<div class="befunde" id="befundkasten"><p class="bt">Vollständigkeitsprüfung — '
            f'{len(befunde)} Befund(e) '
            f'<button onclick="befundeNeu()">erneut prüfen</button>'
            f'</p><ul>{zeilen}</ul></div>')


def baue_html(zeilen: list[dict], rollen: list[str],
              befunde: list | None = None,
              offen: list[dict] | None = None,
              seiten: dict[str, dict] | None = None,
              regeln: dict | None = None,
              live_token: str | None = None,
              korpus: str | None = None,
              dokumente: list[dict] | None = None,
              umbenannt: list[dict] | None = None) -> str:
    daten = json.dumps(zeilen, ensure_ascii=False)
    dokumente_json = json.dumps(dokumente or [], ensure_ascii=False)
    umbenannt_json = json.dumps(umbenannt or [], ensure_ascii=False)
    befund_html = _befunde_html(befunde or [])
    offen_json = json.dumps(offen or [], ensure_ascii=False)
    seiten_json = json.dumps(seiten or {}, ensure_ascii=False)
    zielwerte_json = json.dumps(ZIELWERT_WAHL, ensure_ascii=False)
    regeln_json = json.dumps(regeln or regelwerk(), ensure_ascii=False)
    ziffern_json = json.dumps(ZIFFERN, ensure_ascii=False)
    zz_json = json.dumps(ZIELWERT_ZIFFER, ensure_ascii=False)
    zt_json = json.dumps(
        {b: TYP_LABEL.get(t, t) for b, t in ZIELWERT_TYP.items()},
        ensure_ascii=False)
    live_json = json.dumps(live_token or "", ensure_ascii=False)
    korpus_json = json.dumps(korpus or "standard", ensure_ascii=False)
    # Ohne Server bleibt es beim CSV-Weg; die Seite sagt dann auch, dass sie
    # eine blosse Datei ist.
    quelle_text = ("Lokaler Server auf diesem Ger\u00e4t — jede \u00c4nderung wird "
                   "sofort gespeichert."
                   if live_token else
                   "Lokale Datei — keine Netzwerkverbindung, nichts verl\u00e4sst "
                   "dieses Ger\u00e4t.")
    return f"""<!doctype html>
<html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Steuerbelege prüfen</title>
<style>{_CSS}</style></head><body>
<header>
  <h1>Steuerbelege prüfen</h1>
  <div class="sub">{quelle_text}
    <span id="zaehler"></span> <span id="livestatus" class="live"></span></div>
  <div class="bar">
    <input id="suche" class="suche" type="search" placeholder="Dokument, Zielwert, Aussteller suchen…"
           oninput="setSuche(this.value)">
    <button data-f class="on" onclick="setFilter('alle',this)">alle</button>
    <button data-f onclick="setFilter('unsicher',this)">nur unsicher</button>
    <button data-f onclick="setFilter('wahrscheinlich',this)">nur wahrscheinlich</button>
    <button data-f onclick="setFilter('sicher',this)">nur sicher</button>
    <button data-f onclick="setFilter('ohne_nachweis',this)"
            title="Werte, die so nicht im Dokument stehen">nur ohne Nachweis</button>
    <span style="width:14px"></span>
    <button data-g class="on" onclick="setGrp('dokumente',this)">Dokumente</button>
    <button data-g onclick="setGrp('beleg',this)">nach Beleg</button>
    <button data-g onclick="setGrp('ziffer',this)">nach Ziffer</button>
    <button data-g onclick="setGrp('uebertragen',this)">Übertragen (ohne Eingabefelder)</button>
    <span style="width:14px"></span>
    <label class="artwahl">Belegart
      <select id="artfilter" onchange="setArt(this.value)"></select></label>
    <span style="width:14px"></span>
    <button onclick="rohUmschalten(this)">Beträge unformatiert</button>
    <button class="pri" onclick="exportieren()">korrektur.csv herunterladen</button>
    <a id="tabtab" class="lnk" hidden href="/uebertragung" target="_blank"
       rel="noopener">Tabelle im eigenen Tab ↗</a>
    <button id="bauen" class="pri" hidden onclick="tabelleBauen(this)">Übertragungstabelle neu bauen</button>
  </div>
</header>
<main>
{befund_html}
<p class="hint" id="csvhinweis">Ändere nur, was nicht stimmt. In <em>Beschriftung im Beleg</em>
klickst du den richtigen Wert direkt im Dokument an — Position und Zeilentext
werden automatisch mitgeschrieben und ergeben später die Extraktionsregel.
Die Notiz brauchst du nur, wenn etwas Ungewöhnliches zu sagen ist. Danach die
CSV herunterladen und <code>apply_korrekturen.py</code> darauf laufen lassen.</p>
<datalist id="zielwerte"></datalist>
<div id="offen"></div>
<div id="inhalt"></div>
</main>
<script>
window.__ROWS__ = {daten};
window.__ROLLEN__ = {json.dumps(rollen, ensure_ascii=False)};
window.__TYPEN__ = {json.dumps(BELEGTYP_WAHL, ensure_ascii=False)};
window.__OFFEN__ = {offen_json};
window.__DOKUMENTE__ = {dokumente_json};
window.__UMBENANNT__ = {umbenannt_json};
window.__SEITEN__ = {seiten_json};
window.__ZIELWERTE__ = {zielwerte_json};
window.__ZIFFERN__ = {ziffern_json};
window.__ZIELWERT_ZIFFER__ = {zz_json};
window.__ZIELWERT_TYP__ = {zt_json};
window.__LIVE__ = {live_json};
window.__KORPUS__ = {korpus_json};
window.__REGELN__ = {regeln_json};
{_JS}
</script></body></html>
"""


def baue_aus_samples(samples: Path, pdf_dir: Path | None = None,
                     mit_crops: bool = True,
                     live_token: str | None = None) -> str:
    """Die fertige Seite aus einem Sample-Ordner — ohne sie zu schreiben.

    Herausgezogen, damit der Live-Server (``review_server.py``) genau
    dieselbe Seite ausliefert, die das CLI in eine Datei schreibt. Zwei
    Aufbauwege waeren zwei Gelegenheiten, sich zu unterscheiden — und die
    Unterschiede faenden sich zuverlaessig erst mitten in der Durchsicht.
    """
    zeilen = sammle_zeilen(samples, pdf_dir=pdf_dir, mit_crops=mit_crops,
                           live=bool(live_token))
    try:
        from extractors.family import load_family
        rollen_pflicht = rollen_aus_family(load_family(ROOT / "family.yaml"))
    except Exception:
        rollen_pflicht = rollen_aus_family(None)
    # Gestrichene Zeilen zaehlen fuer die Vollstaendigkeit nicht als
    # vorhanden: „steht nicht in diesem Dokument" heisst gerade nicht „gibt es
    # nicht" — der Wert muss weiter eingefordert werden.
    befunde = pruefe_alles([z for z in zeilen if not z.get("gestrichen")],
                           rollen_pflicht)
    offen = sammle_offene_dokumente(
        samples, pdf_dir, {z["beleg"] for z in zeilen if z.get("beleg")},
        live=bool(live_token))
    seiten = sammle_seiten(
        samples, pdf_dir,
        {(z["beleg"], int(z["seite"])) for z in zeilen
         if z.get("beleg") and z.get("seite")},
    ) if mit_crops else {}
    dokumente = sammle_dokumente(samples, pdf_dir, zeilen)
    try:
        from scripts.umbenennen import journal_lesen
        umbenannt = [{"alt": e.get("alt"), "neu": e.get("neu")}
                     for e in journal_lesen(samples)
                     if e.get("alt") and e.get("neu")]
    except Exception:
        umbenannt = []
    return baue_html(zeilen, rollen_optionen(), befunde, offen, seiten,
                     regelwerk(rollen_pflicht), live_token=live_token,
                     korpus=str(samples.resolve()), dokumente=dokumente,
                     umbenannt=umbenannt)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--samples", type=Path, required=True,
                    help="Verzeichnis mit _results_full.json")
    ap.add_argument("--out", type=Path, default=None,
                    help="Zieldatei (Default: review.html neben den Samples)")
    ap.add_argument("--force", action="store_true",
                    help="Schreiben auch unterhalb von evals/ erlauben")
    ap.add_argument("--pdf-dir", type=Path, default=None,
                    help="Ordner mit den Original-PDFs. Nur damit gibt es "
                         "Ausschnitte und Links zum Dokument.")
    ap.add_argument("--ohne-crops", action="store_true",
                    help="Keine Bildausschnitte einbetten (kleinere Datei)")
    args = ap.parse_args()

    quelle = args.samples / "_results_full.json"
    if not quelle.exists():
        print(f"Nicht gefunden: {quelle}", file=sys.stderr)
        return 1

    zeilen = sammle_zeilen(args.samples, pdf_dir=args.pdf_dir,
                           mit_crops=not args.ohne_crops)

    # Vollstaendigkeit pruefen: was fehlt, obwohl es da sein muesste.
    try:
        from extractors.family import load_family
        rollen_pflicht = rollen_aus_family(load_family(ROOT / "family.yaml"))
    except Exception:
        rollen_pflicht = rollen_aus_family(None)
    # Gestrichene Zeilen zaehlen fuer die Vollstaendigkeit nicht als
    # vorhanden: „steht nicht in diesem Dokument" heisst gerade nicht „gibt es
    # nicht" — der Wert muss weiter eingefordert werden.
    befunde = pruefe_alles([z for z in zeilen if not z.get("gestrichen")],
                           rollen_pflicht)
    offen = sammle_offene_dokumente(
        args.samples, args.pdf_dir,
        {z["beleg"] for z in zeilen if z.get("beleg")})
    # Seitenbilder fuer die Wortauswahl — einmal je Dokument+Seite.
    seiten = sammle_seiten(
        args.samples, args.pdf_dir,
        {(z["beleg"], int(z["seite"])) for z in zeilen
         if z.get("beleg") and z.get("seite")},
    ) if not args.ohne_crops else {}
    ziel = args.out or args.samples / "review.html"

    # Die Seite traegt die echten Vornamen aus family.yaml in den Dropdowns
    # und die Betraege in den Feldern. Landet sie im geprueften Sample-Korpus,
    # schlaegt der Privacy-Gate an (verifiziert). Deshalb hier hart sperren:
    # sie gehoert nach output/, das gitignored ist.
    if not args.force and "evals" in ziel.resolve().parts:
        print(
            f"ABBRUCH: {ziel} liegt unter evals/. Die Seite enthaelt echte "
            f"Namen und Betraege und wuerde den Privacy-Gate ausloesen. "
            f"Schreib sie nach output/ — oder --force, wenn du weisst warum.",
            file=sys.stderr,
        )
        return 2
    ziel.write_text(
        baue_html(zeilen, rollen_optionen(), befunde, offen, seiten,
                  regelwerk(rollen_pflicht)),
        encoding="utf-8")

    from collections import Counter
    verteilung = Counter(z["konfidenz"] for z in zeilen)
    print(f"Geschrieben: {ziel}")
    if seiten:
        print(f"  {len(seiten)} Seitenbild(er) fuer die Wortauswahl eingebettet")
    print(f"  {len(zeilen)} Zeilen — " + ", ".join(
        f"{k}: {v}" for k, v in verteilung.most_common()))
    if befunde:
        print(f"\nVollstaendigkeitspruefung — {len(befunde)} Befund(e):")
        for b in befunde:
            # Nie der rohe Dokumentname: Steuerbelege heissen nach den
            # Menschen, um die es geht. Die Oberflaeche zeigt ihn, die
            # Konsole nicht — sie wird kopiert und weitergegeben.
            zusatz = f" ({b.betrifft_neutral})" if b.betrifft else ""
            print(f"  [{b.schwere}] {b.text}{zusatz}")
    else:
        print("  Vollstaendigkeitspruefung: ohne Befund")
    if offen:
        print(f"\n{len(offen)} nicht zugeordnete(s) Dokument(e) — in der Oberflaeche oben zu bearbeiten.")
    print("\nDie Datei enthaelt echte Werte. Sie gehoert nach output/ "
          "(gitignored) und darf nicht hochgeladen werden.")
    print(f"Oeffnen mit:  open {ziel}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


# ---------------------------------------------------------------------------
# Die reine Übertragungsansicht — ein eigener Tab, der mitzieht
# ---------------------------------------------------------------------------
#
# Beim Ausfüllen arbeitet man die Steuererklärung von oben nach unten ab und
# will dabei nur lesen. Die Arbeitsseite ist dafür das falsche Werkzeug: sie
# ist voller Eingabefelder, und man verstellt versehentlich etwas.
#
# Es gibt die Ansicht „Übertragen" schon — aber in derselben Seite. Wer
# nebeneinander arbeiten will, braucht sie als eigenen Tab, der sich
# aktualisiert, sobald nebenan etwas gespeichert wird (260923-dua).
#
# Statisch heisst hier: kein einziges Eingabefeld. Nur Werte, Summen und
# Kopierknöpfe.

_TABELLE_CSS = """
:root{--bg:#fbfbfa;--fg:#1a1a18;--mut:#6b6b66;--li:#e4e4e0;--card:#fff;
--akz:#2b5fd9}
@media(prefers-color-scheme:dark){:root{--bg:#17171a;--fg:#ececea;--mut:#9a9a95;
--li:#2e2e33;--card:#1f1f23;--akz:#7aa2f7}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
header{position:sticky;top:0;background:var(--bg);z-index:3;
border-bottom:1px solid var(--li);padding:12px 20px}
h1{font-size:16px;margin:0 0 3px}
.sub{color:var(--mut);font-size:12.5px}
main{padding:14px 20px 60px}
section{margin-bottom:20px}
.zh{display:flex;gap:10px;align-items:baseline;flex-wrap:wrap;margin:0 0 6px}
.zz{font-weight:600}
.zn{color:var(--mut);font-size:12.5px}
.zs{margin-left:auto;font-weight:600;font-variant-numeric:tabular-nums}
table{width:100%;border-collapse:collapse;background:var(--card);
border:1px solid var(--li);border-radius:9px;overflow:hidden}
th{text-align:left;font-size:11.5px;text-transform:uppercase;
letter-spacing:.04em;color:var(--mut);padding:7px 10px;font-weight:600;
border-bottom:1px solid var(--li)}
td{padding:7px 10px;border-bottom:1px solid var(--li);vertical-align:top}
tr:last-child td{border-bottom:0}
.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.q{display:block;color:var(--mut);font-size:11.5px;overflow-wrap:anywhere}
button{font:inherit;font-size:12px;padding:3px 9px;border:1px solid var(--li);
border-radius:6px;background:var(--card);color:var(--fg);cursor:pointer}
button:hover{border-color:var(--akz)}
.check{border:1px solid var(--li);border-radius:9px;background:var(--card);
padding:10px 14px;margin-bottom:18px}
.check.fehler{border-color:#e0b4a8;background:#fdf6f4}
.check.gut{border-color:#b7e0c4;background:#f2faf5}
@media(prefers-color-scheme:dark){
.check.fehler{background:#2e1e1a;border-color:#6b3a2c}
.check.gut{background:#16241b;border-color:#2f5c3f}}
.ch{font-weight:600;margin:0;cursor:pointer}
.check.gut .ch{cursor:default}
.gruppen{margin:10px 0 0;padding-left:18px}
.gruppen > li{margin-bottom:8px}
.gruppen > li.ok{color:var(--mut);list-style:none;margin-left:-18px}
.punkte{margin:4px 0 0;padding-left:16px}
.punkte li{margin-bottom:3px}
.sw{display:inline-block;font-size:10.5px;text-transform:uppercase;
letter-spacing:.04em;padding:1px 6px;border-radius:9px;margin-right:6px;
vertical-align:1px}
.sw.fehlt,.sw.unstimmig{background:#c5372c;color:#fff}
.sw.hinweis{background:#e8e8e6;color:#5d5d5d}
.bar{display:flex;gap:8px;align-items:center;margin-top:9px}
.bar button.on{background:var(--akz);color:#fff;border-color:var(--akz)}
.lnk{font-size:12px;color:var(--akz);text-decoration:none;margin-left:auto}
.lnk:hover{text-decoration:underline}
.zi{color:var(--mut);white-space:nowrap}
.uq{color:var(--mut);font-size:11.5px;font-weight:400;overflow-wrap:anywhere}
.zumbeleg{font-size:11px;padding:0 5px;margin-left:5px;line-height:1.4}
section{scroll-margin-top:90px}
.zh{position:sticky;top:0;background:var(--bg);padding:4px 0;z-index:2}
.hk{width:26px;padding-right:0}
.hk input{cursor:pointer}
tr.erledigt{opacity:.45}
tr.erledigt td:not(.hk){text-decoration:line-through}
#fortschritt{margin-left:10px}
.stand{color:var(--mut);font-size:12px}
.frisch{color:#1a7f45;font-weight:600}
"""


def _tabelle_daten(zeilen: list[dict]) -> list[dict]:
    """Die Zeilen nach ZHprivateTax-Ziffer gruppiert, in Formularreihenfolge."""
    from extractors.schlusspruefung import betrag_oder_nichts
    from extractors.zielwerte import ziffer_name, ziffer_sortierung

    gruppen: dict[str, list[dict]] = {}
    for z in zeilen:
        if z.get("gestrichen"):
            continue
        gruppen.setdefault(str(z.get("ziffer") or ""), []).append(z)

    aus: list[dict] = []
    for ziffer in sorted(gruppen, key=ziffer_sortierung):
        rs = gruppen[ziffer]
        betraege = [betrag_oder_nichts(r.get("betrag")) for r in rs]
        summe = sum(b for b in betraege if b is not None)
        # Nur summieren, wenn jeder Betrag lesbar ist — eine Summe, die
        # stillschweigend eine Position auslaesst, ist schlimmer als keine.
        vollstaendig = all(b is not None for b in betraege)
        aus.append({
            "ziffer": ziffer,
            "name": ziffer_name(ziffer) if ziffer else "",
            "summe": f"{summe:.2f}" if vollstaendig else "",
            "zeilen": rs,
        })
    return aus


def schlusscheck_html(zeilen: list[dict]) -> str:
    """Der Kontrollblock zuoberst — drei Fragen, bevor die Zahlen ins Formular
    wandern."""
    from extractors.schlusspruefung import GRUPPEN, pruefe_schluss
    from extractors.vollstaendigkeit import rollen_aus_family

    try:
        from extractors.family import load_family
        rollen = rollen_aus_family(load_family(ROOT / "family.yaml"))
    except Exception:
        rollen = rollen_aus_family(None)

    befunde = pruefe_schluss(zeilen, rollen)
    fehler = [b for b in befunde if b.ist_fehler]
    hinweise = [b for b in befunde if not b.ist_fehler]

    if not befunde:
        return ('<section class="check gut"><p class="ch">✓ Schlusscheck ohne '
                'Befund — alles da, vollständig und plausibel.</p></section>')

    kopf = []
    if fehler:
        kopf.append(f'<strong>{len(fehler)} Punkt(e) ansehen</strong>')
    if hinweise:
        kopf.append(f'{len(hinweise)} Hinweis(e)')

    teile = []
    for gruppe, frage in GRUPPEN.items():
        dazu = [b for b in befunde if b.gruppe == gruppe]
        if not dazu:
            teile.append(f'<li class="ok">✓ {html.escape(frage)}</li>')
            continue
        punkte = "".join(
            f'<li class="{b.schwere}"><span class="sw {b.schwere}">'
            f'{"prüfen" if b.ist_fehler else "Hinweis"}</span> '
            f'{html.escape(b.text)}</li>' for b in dazu)
        teile.append(f'<li><strong>{html.escape(frage)}</strong>'
                     f'<ul class="punkte">{punkte}</ul></li>')

    offen = " · ".join(kopf)
    return (f'<section class="check{" fehler" if fehler else ""}">'
            f'<details{" open" if fehler else ""}>'
            f'<summary class="ch">Schlusscheck — {offen}</summary>'
            f'<ul class="gruppen">{"".join(teile)}</ul></details></section>')


def personennamen() -> dict[str, str]:
    """Rolle -> Klarname. „elternteil_1" sagt beim Abtippen nichts."""
    try:
        from scripts.umbenennen import _personennamen
        return _personennamen()
    except Exception:
        return {}


def _person_anzeige(rolle, namen: dict[str, str]) -> str:
    r = str(rolle or "").strip()
    if not r:
        return "—"
    if r == "familie":
        return "Familie"
    return namen.get(r, r)


def _konten_daten(zeilen: list[dict]) -> list[dict]:
    """Ein Block je Dokument — so, wie man ein Konto ins Formular einträgt.

    Im Wertschriftenverzeichnis wird nicht Ziffer für Ziffer erfasst, sondern
    Konto für Konto: Saldo, Bruttoertrag ohne Verrechnungssteuer, Bruttoertrag
    mit. Eine nach Ziffern sortierte Liste zwingt dann zum Hin- und
    Herspringen zwischen drei Abschnitten (260923-dua).

    Innerhalb eines Dokuments stehen die Werte in der Reihenfolge, in der das
    Formular sie abfragt — erst der Bestand, dann der Ertrag.
    """
    from extractors.zielwerte import ziffer_sortierung

    reihenfolge = ["Saldo 31.12.", "Steuerwert 31.12.",
                   "Bruttoertrag ohne Verrechnungssteuer",
                   "Bruttoertrag mit Verrechnungssteuer"]
    rang = {b: i for i, b in enumerate(reihenfolge)}

    # Ein Konto, nicht ein Dokument.
    #
    # Fuer dasselbe Konto liegen oft zwei Unterlagen vor — die Zinsabrechnung
    # und der Steuerauszug. Als zwei Bloecke gezeigt, sucht man den Ertrag an
    # anderer Stelle als den Saldo, obwohl beides in dieselbe Zeile des
    # Formulars gehoert. `konto_key` erkennt die Kontonummer im Dateinamen;
    # ohne sie bleibt es beim einzelnen Dokument (260923-dua).
    from scripts.build_tax_output import konto_key

    je_konto: dict[str, list[dict]] = {}
    for z in zeilen:
        if z.get("gestrichen"):
            continue
        name = str(z.get("beleg") or "")
        je_konto.setdefault(konto_key(name) or name, []).append(z)

    aus: list[dict] = []
    for _schluessel, rs in je_konto.items():
        beleg = str(rs[0].get("beleg") or "")
        rs = sorted(rs, key=lambda r: (
            rang.get(str(r.get("zielwert") or ""), 99),
            ziffer_sortierung(str(r.get("ziffer") or "")),
            int(r.get("pos") or 1)))
        aussteller = next((str(r.get("aussteller") or "").strip()
                           for r in rs if str(r.get("aussteller") or "").strip()),
                          "")
        quellen = sorted({str(r.get("beleg") or "") for r in rs})
        aus.append({"beleg": beleg, "quellen": quellen,
                    "aussteller": aussteller, "zeilen": rs})
    # Nach Aussteller sortieren — so liegen die Konten derselben Bank beisammen.
    aus.sort(key=lambda g: (g["aussteller"].lower(), g["beleg"].lower()))
    return aus


def _kopierbar(betrag: str) -> str:
    """Der Wert, wie er ins Formular gehört — unformatiert."""
    return html.escape(str(betrag or "").strip())


def _angezeigt(betrag: str) -> str:
    """Der Wert, wie er sich lesen lässt — mit Tausendertrennzeichen."""
    from extractors.schlusspruefung import _chf, betrag_oder_nichts

    wert = betrag_oder_nichts(betrag)
    if wert is None:
        return html.escape(str(betrag or ""))
    text = _chf(wert)
    text = text if "." in text else text + ".00"
    # `quote=False`: der Apostroph ist hier das Tausendertrennzeichen, kein
    # Anführungszeichen. Maskiert stünde im Quelltext „92&#x27;500.00" — im
    # Browser richtig, aber unlesbar und in Tests nicht wiederzufinden.
    # Der Text besteht nur aus Ziffern, Apostroph und Punkt.
    return html.escape(text, quote=False)


def _wert_zelle(betrag: str) -> str:
    """Angezeigt formatiert, kopiert unformatiert."""
    roh = _kopierbar(betrag)
    return (f'<td class="num">{_angezeigt(betrag)}</td>'
            f'<td><button onclick="kopieren(\'{roh}\',this)">kopieren</button>'
            f'</td>')


def _zeilen_id(r: dict) -> str:
    """Kennung einer Zeile für „schon eingetragen" — rein lokal im Tab."""
    return html.escape(f'{r.get("beleg") or ""}|{r.get("zielwert") or ""}'
                       f'|{r.get("pos") or 1}')


def _haken() -> str:
    """Ein Kästchen zum Abhaken beim Abtippen.

    Beim Übertragen ins Formular verliert man die Stelle — fünfzig Zeilen,
    und man weiss nicht mehr, welche schon drin sind. Der Haken lebt nur in
    diesem Tab und rührt die Daten nicht an.
    """
    return ('<td class="hk"><input type="checkbox" '
            'onchange="hakenSetzen(this)" title="schon eingetragen"></td>')


def _zum_beleg(beleg: str) -> str:
    """Link in den Arbeits-Tab, mit diesem Dokument ausgewählt."""
    return (f'<button class="zumbeleg" onclick="zumBeleg(\'{html.escape(beleg)}\')" '
            f'title="Im Arbeits-Tab öffnen">↗</button>')


def konten_html(zeilen: list[dict]) -> str:
    """Nach Formular, darin je Dokument — die Sicht zum Ausfüllen.

    Beim Ausfüllen zählt nicht die Ziffer, sondern das Blatt: das
    Wertschriftenverzeichnis wird Konto für Konto erfasst, das
    Schuldenverzeichnis Schuld für Schuld. Eine Sortierung nach Aussteller
    allein hilft dabei nicht — sie sagt nicht, was zusammen ins selbe Formular
    gehört (260923-dua, vom Nutzer gemeldet).
    """
    from extractors.zielwerte import formular_fuer, formular_sortierung

    namen = personennamen()
    bloecke = _konten_daten(zeilen)
    if not bloecke:
        return '<p class="stand">Noch keine übertragbaren Positionen.</p>'

    # Ein Dokument gehört zu dem Formular, in das seine Werte wandern.
    # Verteilen sich sie auf mehrere, erscheint es unter jedem — man trägt es
    # ja auch mehrfach ein.
    nach_formular: dict[str, list[dict]] = {}
    for g in bloecke:
        je_form: dict[str, list[dict]] = {}
        for r in g["zeilen"]:
            form = formular_fuer(str(r.get("zielwert") or "")) or "Übrige"
            je_form.setdefault(form, []).append(r)
        for form, rs in je_form.items():
            nach_formular.setdefault(form, []).append(
                {**g, "zeilen": rs})

    teile = []
    gesamt: dict[str, Decimal] = {}
    for form in sorted(nach_formular, key=formular_sortierung):
        teile.append(f'<h2 class="form">{html.escape(form)}</h2>')
        teile.extend(_konto_block(g, namen) for g in nach_formular[form])
        summen = _summen_je_ziffer(
            [r for g in nach_formular[form] for r in g["zeilen"]])
        for ziffer, wert in summen.items():
            gesamt[ziffer] = gesamt.get(ziffer, Decimal(0)) + wert
        teile.append(_summen_block(f"Summen für {form}", summen))
    teile.append(_summen_block("Alle Ziffern auf einen Blick", gesamt,
                               hinweis="Zum Abhaken beim Ausfüllen."))
    return "".join(teile)


def _summen_je_ziffer(zeilen: list[dict]) -> dict:
    """Was je Ziffer zusammenzuzaehlen ist — in der Reihenfolge des Formulars.

    Decimal, nicht float: die Summe wird abgetippt, und eine
    Rundungsabweichung im Rappen faellt genau dort auf.
    """
    from extractors.schlusspruefung import betrag_oder_nichts
    from extractors.zielwerte import ziffer_sortierung

    roh: dict[str, Decimal] = {}
    for r in zeilen:
        ziffer = str(r.get("ziffer") or "").strip()
        wert = betrag_oder_nichts(r.get("betrag"))
        if ziffer and wert is not None:
            roh[ziffer] = roh.get(ziffer, Decimal(0)) + Decimal(str(wert))
    return {z: roh[z] for z in sorted(roh, key=ziffer_sortierung)}


def _summen_block(titel: str, summen: dict, hinweis: str = "") -> str:
    """Die Summen als eigene Tabelle — jede Zahl einzeln kopierbar."""
    if not summen:
        return ""
    from extractors.zielwerte import ziffer_name

    reihen = []
    for ziffer, wert in summen.items():
        betrag = f"{wert:.2f}"
        reihen.append(
            f'<tr><td class="zi">{html.escape(ziffer)}</td>'
            f'<td>{html.escape(ziffer_name(ziffer))}</td>'
            f'{_wert_zelle(betrag)}</tr>')
    kopf = (f'<p class="zh"><span class="zz">{html.escape(titel)}</span>'
            + (f'<span class="zn">{html.escape(hinweis)}</span>'
               if hinweis else "") + '</p>')
    return (f'<section class="summen">{kopf}'
            f'<table><thead><tr><th>Ziffer</th><th></th>'
            f'<th class="num">Summe CHF</th><th></th></tr></thead>'
            f'<tbody>{"".join(reihen)}</tbody></table></section>')


def _konto_block(g: dict, namen: dict[str, str]) -> str:
    """Ein Dokument mit seinen Werten — der Block, den man am Stück einträgt."""
    for g in (g,):
        rs = g["zeilen"]
        person = _person_anzeige(
            next((r.get("person") for r in rs if r.get("person")), ""), namen)
        titel = html.escape(g["aussteller"] or g["beleg"])
        reihen = []
        for r in rs:
            bez = html.escape(str(r.get("zielwert") or ""))
            unt = str(r.get("unterscheidung") or "").strip()
            if unt:
                bez += f' <span class="zn">({html.escape(unt)})</span>'
            reihen.append(
                f'<tr data-k="{_zeilen_id(r)}">{_haken()}'
                f'<td class="zi">{html.escape(str(r.get("ziffer") or "—"))}'
                f'</td><td>{bez}</td>'
                f'<td>{html.escape(_person_anzeige(r.get("person"), namen))}</td>'
                f'{_wert_zelle(r.get("betrag"))}</tr>')
        return (
            f'<section><p class="zh"><span class="zz">{titel}</span>'
            f'<span class="zn">{html.escape(person)}</span>'
            f'{"".join(_zum_beleg(q) for q in g.get("quellen") or [g["beleg"]])}'
            f'<span class="uq">'
            f'{html.escape(" · ".join(g.get("quellen") or [g["beleg"]]))}'
            f'</span></p>'
            f'<table><thead><tr><th class="hk"></th><th>Ziffer</th>'
            f'<th>Beschreibung</th><th>Person</th>'
            f'<th class="num">Betrag CHF</th><th></th></tr>'
            f'</thead><tbody>{"".join(reihen)}</tbody></table></section>')


def tabelle_html(zeilen: list[dict]) -> str:
    """Nur der Inhalt — der Tab lädt ihn nach, ohne die Seite neu zu bauen."""
    namen = personennamen()
    gruppen = _tabelle_daten(zeilen)
    if not gruppen:
        return '<p class="stand">Noch keine übertragbaren Positionen.</p>'
    teile: list[str] = []
    for g in gruppen:
        kopf = (f'<span class="zz">Ziffer {html.escape(g["ziffer"])}</span>'
                if g["ziffer"] else '<span class="zz">ohne Ziffer</span>')
        name = (f'<span class="zn">{html.escape(g["name"])}</span>'
                if g["name"] else "")
        summe = (f'<span class="zs">{_angezeigt(g["summe"])}'
                 f' <button onclick="kopieren(\'{_kopierbar(g["summe"])}\',this)">'
                 f'Summe kopieren</button></span>' if g["summe"] else "")
        reihen = []
        for r in g["zeilen"]:
            bez = html.escape(str(r.get("zielwert") or ""))
            unt = str(r.get("unterscheidung") or "").strip()
            if unt:
                bez += f' <span class="zn">({html.escape(unt)})</span>'
            reihen.append(
                f'<tr data-k="{_zeilen_id(r)}">{_haken()}'
                f'<td>{html.escape(_person_anzeige(r.get("person"), namen))}'
                f'</td>'
                f'<td>{html.escape(str(r.get("aussteller") or "—"))}</td>'
                f'<td>{bez}<span class="q">'
                f'{html.escape(str(r.get("beleg") or ""))}'
                f'{_zum_beleg(str(r.get("beleg") or ""))}</span></td>'
                f'{_wert_zelle(r.get("betrag"))}</tr>')
        teile.append(
            f'<section><p class="zh">{kopf}{name}{summe}</p>'
            f'<table><thead><tr><th class="hk"></th><th>Person</th>'
            f'<th>Aussteller</th><th>Beschreibung</th>'
            f'<th class="num">Betrag CHF</th><th></th>'
            f'</tr></thead><tbody>{"".join(reihen)}</tbody></table></section>')
    return "".join(teile)


def tabelle_seite(zeilen: list[dict], stand: int = 0,
                  korpus: str = "") -> str:
    """Die ganze Seite für den eigenen Tab — lesen, kopieren, sonst nichts."""
    korpus_json = json.dumps(korpus or "standard", ensure_ascii=False)
    return f"""<!doctype html>
<html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Übertragungstabelle</title>
<style>{_TABELLE_CSS}</style></head><body>
<header>
  <h1>Übertragungstabelle</h1>
  <div class="sub">Nur lesen und kopieren — geändert wird nebenan.
    Angezeigt mit Tausendertrennzeichen, kopiert wird der reine Wert.
    <span id="stand" class="stand"></span>
    <span id="fortschritt" class="stand"></span></div>
  <div class="bar">
    <button id="bziffer" class="on" onclick="setSicht('ziffer')">nach Ziffer</button>
    <button id="bkonto" onclick="setSicht('konto')">nach Konto / Beleg</button>
    <a class="lnk" href="/" target="review">Arbeits-Tab ↗</a>
  </div>
</header>
<main>
  <div id="check">{schlusscheck_html(zeilen)}</div>
  <div id="inhalt">{tabelle_html(zeilen)}</div>
  <div id="inhaltkonto" hidden>{konten_html(zeilen)}</div>
</main>
<script>
let stand = {stand};
const KORPUS = {korpus_json};

// In den Arbeits-Tab springen, mit diesem Dokument ausgewaehlt.
//
// Der Name geht NICHT ueber die Adresszeile: Belegnamen tragen Institute und
// Personen, und die Browser-History ist nicht der Ort dafuer. Beide Tabs
// teilen sich denselben localStorage — die Auswahl wird dort abgelegt, der
// Arbeits-Tab liest sie beim Laden (260923-dua).
function zumBeleg(beleg){{
  try {{ localStorage.setItem('steuer-gewaehlt:' + KORPUS, beleg); }} catch(e){{}}
  window.open('/', 'review');
}}

// Was schon ins Formular eingetragen ist — nur in diesem Tab, nur zum
// Mitzaehlen. Beruehrt keine Daten.
const HKEY = 'steuer-eingetragen:' + KORPUS;
let erledigt = {{}};
try {{ erledigt = JSON.parse(localStorage.getItem(HKEY) || '{{}}'); }}
catch(e) {{ erledigt = {{}}; }}

function hakenSetzen(el){{
  const k = el.closest('tr').dataset.k;
  if (el.checked) erledigt[k] = true; else delete erledigt[k];
  try {{ localStorage.setItem(HKEY, JSON.stringify(erledigt)); }} catch(e){{}}
  // Auf ALLE Darstellungen anwenden: dieselbe Zeile steht in der
  // Ziffern- und in der Konto-Sicht. Nur die angeklickte zu aendern hiess,
  // dass der Haken beim Umschalten verschwindet (260923-dua).
  hakenAnwenden();
}}

function hakenAnwenden(){{
  document.querySelectorAll('tr[data-k]').forEach(tr => {{
    const an = !!erledigt[tr.dataset.k];
    const box = tr.querySelector('.hk input');
    if (box) box.checked = an;
    tr.classList.toggle('erledigt', an);
  }});
  hakenZaehlen();
}}

function hakenZaehlen(){{
  const sichtbar = document.querySelectorAll(
    (sicht === 'konto' ? '#inhaltkonto' : '#inhalt') + ' tr[data-k]');
  const n = [...sichtbar].filter(tr => erledigt[tr.dataset.k]).length;
  const el = document.getElementById('fortschritt');
  if (el) el.textContent = sichtbar.length
    ? `${{n}} von ${{sichtbar.length}} eingetragen` : '';
}}


let sicht = 'ziffer';
try {{ sicht = localStorage.getItem('steuer-tabellensicht:' + KORPUS) || 'ziffer'; }}
catch(e) {{ sicht = 'ziffer'; }}

function setSicht(s){{
  sicht = s;
  try {{ localStorage.setItem('steuer-tabellensicht:' + KORPUS, s); }} catch(e){{}}
  document.getElementById('inhalt').hidden = (s !== 'ziffer');
  document.getElementById('inhaltkonto').hidden = (s !== 'konto');
  document.getElementById('bziffer').classList.toggle('on', s === 'ziffer');
  document.getElementById('bkonto').classList.toggle('on', s === 'konto');
  hakenZaehlen();
}}
setSicht(sicht);


function kopieren(text, el){{
  navigator.clipboard.writeText(text).then(() => {{
    const alt = el.textContent;
    el.textContent = 'kopiert';
    setTimeout(() => {{ el.textContent = alt; }}, 900);
  }});
}}

// Mitziehen, ohne die Arbeit nebenan zu stoeren.
//
// Der Tab fragt nur nach einer Zahl; aendert sie sich, holt er den Inhalt neu.
// Kein Neuladen der ganzen Seite — sonst springt man bei jedem Tastendruck
// nebenan an den Anfang zurueck.
async function schauen(){{
  try {{
    const a = await fetch('/api/status', {{cache: 'no-store'}});
    const d = await a.json();
    if (d.stand !== undefined && d.stand !== stand){{
      stand = d.stand;
      const b = await fetch('/api/uebertragung', {{cache: 'no-store'}});
      const frisch = JSON.parse(await b.text());
      document.getElementById('check').innerHTML = frisch.check;
      document.getElementById('inhalt').innerHTML = frisch.ziffer;
      document.getElementById('inhaltkonto').innerHTML = frisch.konto;
      setSicht(sicht);
      hakenAnwenden();
      const s = document.getElementById('stand');
      s.textContent = 'aktualisiert';
      s.className = 'stand frisch';
      setTimeout(() => {{ s.textContent = ''; s.className = 'stand'; }}, 2500);
    }}
  }} catch (e) {{ /* Server weg — beim naechsten Mal wieder versuchen. */ }}
}}
setInterval(schauen, 2000);
</script></body></html>
"""
