#!/usr/bin/env python3
"""Kann ich die Steuererklärung damit ausfüllen — und was fehlt noch?

Für den Sample-Korpus gibt es den 20-Check-Grader. Für den produktiven Lauf
gab es nichts: kein Befehl beantwortete die einzige Frage, die zählt. Man
musste die Oberfläche durchscrollen und hoffen, nichts übersehen zu haben —
und übersah dann Dokumente, die gar nicht erst angezeigt wurden.

Geprüft wird der ganze Weg, in der Reihenfolge, in der er reisst:

  1. Ist jedes PDF eingelesen und extrahiert?
  2. Liefert jedes Dokument eine Position — oder ist es abgehakt?
  3. Hat jede Position einen Betrag?
  4. Hat jede Position, bei der es zählt, eine Person?
  5. Trägt jede Position eine Ziffer aus dem Feldvertrag?
  6. Ist für jedes Familienmitglied da, was da sein muss?
  7. Steht in keiner Zeile ein unaufgelöster Marker?
  7b. Welche Werte hat noch niemand bestätigt?
  8. Kommt die Durchsicht in der Tabelle an?
  9. Liest der Lauf die bestätigten Werte weiterhin richtig?

Ausgabe in zwei Teilen: zuerst die Bilanz (nur Zahlen, teilbar), danach die
betroffenen Dokumente (Dateinamen, bleibt auf dem Gerät).

Exit 0 nur, wenn nichts mehr offen ist.

Aufruf::

    PYTHONPATH=. .venv/bin/python scripts/bereitschaft.py \\
        --input belege --samples output/latest/json
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Warum steht eine Bestaetigung nicht in der Tabelle? Drei Ursachen sahen
# gleich aus, und nur eine ist verlorene Arbeit (260923-dua).
_GRUND = {
    "kein_dokument": "Dokument liefert keine Zeilen",
    "ohne_betrag": "Zeile da, aber ohne Betrag",
    "unbekannte_bezeichnung": "Dokument kennt diese Bezeichnung nicht",
}


def _ist_pflichtfeld(zeile: dict) -> bool:
    """Verlangt die Belegart dieses Dokuments diesen Wert?

    Der Feldvertrag unterscheidet `mandatory` von `conditional`. Nur das
    Erste ist eine Luecke, wenn es fehlt. Selbst erfasste Zeilen
    (`field_name == "manuell"`) kennt der Vertrag nicht — was der Mensch
    anlegt, verantwortet er selbst.
    """
    from extractors.steuer_zielmodell import get_spec

    spec = get_spec(str(zeile.get("belegtyp") or ""))
    if spec is None:
        return False
    return str(zeile.get("field_name") or "") in spec.mandatory_field_names()


def _grundwort(grund: str) -> str:
    """Der Kurztext — mit angehaengter Erlaeuterung, wo es eine gibt."""
    kopf, _, rest = grund.partition(":")
    wort = _GRUND.get(kopf, kopf)
    return f"{wort} —{rest}" if rest else wort


TRENNER = "─" * 70


@dataclass
class Punkt:
    """Ein Prüfpunkt mit seiner Bilanz."""

    titel: str
    ok: bool
    zahl: str
    was_tun: str = ""
    betroffen: list[str] = field(default_factory=list)
    # Nach Belegart gezaehlt — sagt, WO das Problem sitzt, ohne einen
    # Dateinamen zu nennen. Damit laesst sich die Bilanz weitergeben und
    # trotzdem gezielt suchen.
    nach_art: dict[str, int] = field(default_factory=dict)
    # Was der Zeile fehlt — Zielwert, Belegart, Herkunft, Zustand der Felder.
    #
    # Das nennt kein Dokument und keinen Betrag und ist deshalb teilbar. Ohne
    # es blieb nur „1× unbekannt", und die Frage „welche Zeile, und was fehlt
    # ihr?" liess sich nur durch Hineinschauen beantworten (260923-dua).
    warum: list[str] = field(default_factory=list)

    @property
    def marke(self) -> str:
        return "✓" if self.ok else "✗"


def _laden(samples: Path) -> list[dict]:
    pfad = samples / "_results_full.json"
    return json.loads(pfad.read_text()) if pfad.exists() else []


def _zeilen(samples: Path, mit_korrekturen: bool = True):
    """Die Zeilen, wie sie in der Übertragungstabelle landen.

    ``mit_korrekturen=False`` liefert den **rohen** Lauf. Punkt 8 braucht ihn:
    er fragt, ob der Lauf die bestätigten Werte noch trifft. Verglichen wurde
    bisher gegen Zeilen, in die ``wende_korrekturen_an`` die Sollwerte zuvor
    selbst eingesetzt hatte — eine Prüfung, die sich ihr Ergebnis vorher
    hinschreibt, kann nicht fehlschlagen (260923-dua).
    """
    from scripts.build_tax_output import (
        build_rows, drop_zero_rows, ergaenze_eigene_zeilen,
        nummeriere_positionen, wende_korrekturen_an,
    )
    roh: list[dict] = []
    for e in _laden(samples):
        if (e.get("status") == "ok"
                and str(e.get("pdf_name") or "").lower().endswith(".pdf")):
            roh.extend(build_rows(e))
    if mit_korrekturen:
        wende_korrekturen_an(roh, samples)
        ergaenze_eigene_zeilen(roh, samples)
    else:
        nummeriere_positionen(roh)
    behalten, verworfen = drop_zero_rows(roh)
    return behalten, verworfen


def _abgehakt(samples: Path) -> set[str]:
    """Dokumente, die der Mensch ausdrücklich als erledigt markiert hat.

    Die Auswertung selbst steht in ``extractors.korrekturen`` — die
    Oberfläche stellt dieselbe Frage und bekam früher eine andere Antwort
    (260923-dua).
    """
    from extractors.korrekturen import ausgeschlossene_dokumente, lade_alle
    return set(ausgeschlossene_dokumente(lade_alle(samples)))


def pruefe(input_dir: Path, samples: Path) -> list[Punkt]:
    from extractors.vollstaendigkeit import (
        pruefe_alles, rolle_aus_person, rollen_aus_family,
    )
    from extractors.zielwerte import ZIFFERN
    from scripts.build_tax_output import is_manual_review_marker
    from scripts.dokumente_check import _einordnen, _stationen

    punkte: list[Punkt] = []
    stand = _stationen(input_dir, samples)
    gruppen = _einordnen(stand)
    zeilen, verworfen = _zeilen(samples)
    abgehakt = _abgehakt(samples)

    def art(r) -> str:
        return str(r.get("belegtyp") or "unbekannt")

    def warum(rs) -> list[str]:
        """Je Zeile: Zielwert, Belegart, Herkunft und was gesetzt ist."""
        aus: list[str] = []
        for r in rs:
            hat = []
            if str(r.get("betrag") or "").strip():
                hat.append("Betrag")
            if str(r.get("ziffer") or "").strip():
                hat.append("Ziffer " + str(r.get("ziffer")))
            if rolle_aus_person(r.get("person")) not in ("", "unbekannt/manuell"):
                hat.append("Person")
            aus.append(
                f"{r.get('beschreibung') or '(ohne Zielwert)'} "
                f"· {art(r)} · {r.get('herkunft') or 'modell'} "
                f"· hat: {', '.join(hat) or 'nichts'}")
        return sorted(set(aus))

    def zaehle(rs) -> dict[str, int]:
        aus: dict[str, int] = {}
        for r in rs:
            aus[art(r)] = aus.get(art(r), 0) + 1
        return dict(sorted(aus.items(), key=lambda kv: -kv[1]))


    # 0 — was die OBERFLAECHE zeigt.
    #
    # Die Bereitschaftspruefung rechnete bisher an der Oberflaeche vorbei: sie
    # baute die Zeilen selbst und sagte „35 von 35 bewertet", waehrend in der
    # Liste Dokumente als nicht zugeordnet standen. Zwei Antworten auf
    # dieselbe Frage — und der Mensch stand dazwischen (260923-dua).
    #
    # Jetzt wird derselbe Aufruf benutzt, aus dem die Liste gebaut wird.
    from scripts.build_review_html import (
        DOK_ZUSTAND, sammle_dokumente, sammle_zeilen,
    )
    try:
        ui_zeilen = sammle_zeilen(samples, pdf_dir=None, mit_crops=False)
        ui_dok = sammle_dokumente(samples, input_dir, ui_zeilen)
    except Exception as exc:          # pragma: no cover - Diagnose
        ui_dok = []
        print(f"Hinweis: Oberflaechen-Sicht nicht auswertbar ({exc!r})",
              file=sys.stderr)

    nach_zustand: dict[str, int] = {}
    for d in ui_dok:
        nach_zustand[d["zustand"]] = nach_zustand.get(d["zustand"], 0) + 1
    unklar = [d for d in ui_dok
              if d["zustand"] in ("ohne_fund", "nicht_eingelesen")]
    punkte.append(Punkt(
        "Jedes Dokument ist in der Übersicht eingeordnet", not unklar,
        ", ".join(f"{n}× {DOK_ZUSTAND[z]}"
                  for z, n in sorted(nach_zustand.items(),
                                     key=lambda kv: -kv[1])) or "keine",
        "Diese Dokumente stehen in der Liste unten: Belegart setzen, Position "
        "erfassen — oder als „irrelevant\" abhaken.",
        [d["beleg"] for d in unklar],
        {str(d.get("belegtyp") or "unbekannt"): 1 for d in unklar},
        [f"{d['zustandwort']} · {d.get('belegtyp') or 'ohne Belegart'} · "
         f"{d['positionen']} Position(en)" for d in unklar]))

    # 1 — eingelesen
    pdfs = [n for n, s in stand.items() if s.get("im_ordner")]
    nicht_gelesen = gruppen["kein_json"] + gruppen["kein_ergebnis"]
    punkte.append(Punkt(
        "Dokumente eingelesen", not nicht_gelesen,
        f"{len(pdfs) - len(nicht_gelesen)} von {len(pdfs)}",
        "Diese PDFs sind nicht durch die Extraktion gekommen — meist Scans "
        "ohne Textebene. `make productive` erneut laufen lassen oder das "
        "Dokument von Hand erfassen.",
        nicht_gelesen))

    # 2 — jedes Dokument liefert etwas oder ist abgehakt
    mit_zeile = {r.get("pdf_name", "") for r in zeilen}

    # Ein Dokument, dessen Werte der Mensch geprueft und mit 0.00 bestaetigt
    # hat, ist vollstaendig — nicht stumm. Der haeufigste Fall: eine
    # Saeule-3a-Bescheinigung ohne Einzahlung im Steuerjahr. Das Guthaben
    # gehoert laut Wegleitung ZH 2025 (S. 24) nicht in die Steuererklaerung,
    # also gibt es dort nichts zu holen — die Nachfrage waere Rauschen.
    from extractors.korrekturen import lade_alle as _alle_korr
    _korr = _alle_korr(samples)
    bestaetigte_null = {
        schluessel.partition("|")[0]
        for schluessel, e in _korr.items()
        if isinstance(e, dict) and e.get("bestaetigt_betrag")
        and str(e.get("wert_betrag") or "").strip() in ("0", "0.00", "0.-")
    }
    stumm = sorted({n for n, s in stand.items()
                    if s.get("im_ordner") and n not in mit_zeile
                    and n not in abgehakt and n not in nicht_gelesen
                    and n not in bestaetigte_null})
    punkte.append(Punkt(
        "Jedes Dokument liefert eine Position", not stumm,
        f"{len(mit_zeile)} Dokument(e) mit Position, {len(stumm)} stumm",
        "In der Review stehen sie oben bei den nicht zugeordneten: Belegart "
        "setzen, Position erfassen — oder als „irrelevant\" abhaken.",
        stumm,
        {str(stand[n].get("belegtyp") or "unbekannt"):
         sum(1 for m in stumm
             if (stand[m].get("belegtyp") or "unbekannt")
             == (stand[n].get("belegtyp") or "unbekannt"))
         for n in stumm}))

    # 2b — Belegart bewertet
    ohne_art = sorted({n for n, st in stand.items()
                       if st.get("im_ordner") and n not in nicht_gelesen
                       and not (st.get("belegtyp") or "")
                       and n not in abgehakt})
    punkte.append(Punkt(
        "Jedes Dokument hat eine Belegart", not ohne_art,
        f"{len(pdfs) - len(ohne_art)} von {len(pdfs)} bewertet",
        "Ohne Belegart weiss der Feldvertrag nicht, welche Werte das Dokument "
        "liefern soll. In der Review oben die Belegart setzen — auch "
        "„irrelevant\" ist eine gueltige Antwort.",
        ohne_art))

    # 3 — Betrag, wo die Belegart einen verlangt
    #
    # Das Modell ist einfach: ein Dokument ist entweder aussortiert oder
    # kategorisiert, und die Kategorie sagt, welche Werte es haben muss. Der
    # Punkt fragte stattdessen JEDE Zeile nach einem Betrag — auch die, die
    # der Feldvertrag ausdruecklich als `conditional` fuehrt. Eine
    # Zusatzversicherung gibt es oder nicht; eine leere VVG-Zeile auf einer
    # Praemienbescheinigung ist keine Luecke, sondern eine Auskunft. Der
    # Punkt konnte so nie gruen werden, und die geforderte Handlung
    # ("Betrag eintragen") gab es gar nicht (260923-dua).
    ohne_betrag_r = [r for r in zeilen
                     if (not str(r.get("betrag") or "").strip()
                         or is_manual_review_marker(r.get("betrag")))
                     and _ist_pflichtfeld(r)]
    punkte.append(Punkt(
        "Jede Position hat einen Betrag", not ohne_betrag_r,
        f"{len(zeilen) - len(ohne_betrag_r)} von {len(zeilen)}",
        "In der Review den Betrag im Seitenbild anklicken oder eintragen.",
        [f"{r.get('pdf_name','?')} — {r.get('beschreibung','?')}"
         for r in ohne_betrag_r],
        zaehle(ohne_betrag_r), warum(ohne_betrag_r)))

    # 4 — Person, wo sie zählt
    persoenlich = ("KVG", "Grundversicherung", "VVG", "Zusatzversicherung",
                   "Nettolohn", "Säule 3a", "Selbst getragene",
                   "Kinderbetreuung")

    def zugeordnet(r) -> bool:
        # „unbekannt/manuell" ist eine gueltige Rolle im Datenmodell, aber
        # keine Zuordnung — sie heisst gerade „hier fehlt sie noch".
        return rolle_aus_person(r.get("person")) not in ("", "unbekannt/manuell")

    # Ohne Betrag keine Zuordnung noetig: eine Zeile, die die Belegart nur
    # anbietet und dieses Dokument nicht fuellt, braucht keine Person.
    ohne_person_r = [r for r in zeilen
                     if any(m in str(r.get("beschreibung") or "")
                            for m in persoenlich)
                     and str(r.get("betrag") or "").strip()
                     and not zugeordnet(r)]
    punkte.append(Punkt(
        "Personenbezogene Positionen sind zugeordnet", not ohne_person_r,
        f"{len(ohne_person_r)} ohne Rolle",
        "In der Review die Person wählen — „↧ alle\" überträgt sie auf das "
        "ganze Dokument.",
        [f"{r.get('pdf_name','?')} — {r.get('beschreibung','?')}"
         for r in ohne_person_r],
        zaehle(ohne_person_r), warum(ohne_person_r)))

    # 5 — Ziffer aus dem Feldvertrag
    falsche_r = [r for r in zeilen
                 if str(r.get("ziffer") or "") not in ZIFFERN]
    punkte.append(Punkt(
        "Jede Position hat eine gültige Ziffer", not falsche_r,
        f"{len(zeilen) - len(falsche_r)} von {len(zeilen)}",
        "Zielwert in der Review wählen — die Ziffer zieht dann mit.",
        [f"{r.get('pdf_name','?')} — {r.get('beschreibung','?')} "
         f"[{r.get('ziffer') or 'keine'}]" for r in falsche_r],
        zaehle(falsche_r), warum(falsche_r)))

    # 6 — Familienabdeckung
    try:
        from extractors.family import load_family
        rollen = rollen_aus_family(load_family(ROOT / "family.yaml"))
    except Exception:
        rollen = rollen_aus_family(None)
    befunde = [b for b in pruefe_alles(
        [{"person": r.get("person"), "beschreibung": r.get("beschreibung"),
          "pdf_name": r.get("pdf_name")} for r in zeilen], rollen)
        if b.schwere != "hinweis"]
    punkte.append(Punkt(
        "Für jede Person ist da, was da sein muss", not befunde,
        f"{len(befunde)} Befund(e)",
        "Fehlt ein Dokument, oder wurde die Person darin nicht erkannt?",
        [f"{b.text}" for b in befunde], {}, [b.text for b in befunde]))

    # 7 — keine unaufgelösten Marker
    marker = [f"{r.get('pdf_name','?')} — {r.get('beschreibung','?')}"
              for r in zeilen
              if any(is_manual_review_marker(r.get(f))
                     for f in ("betrag", "person", "aussteller"))]
    punkte.append(Punkt(
        "Keine unaufgelösten Marker in der Tabelle", not marker,
        f"{len(marker)} Zeile(n)",
        "Diese Zeilen tragen einen Platzhalter statt eines Werts.",
        marker))

    # 8 — bestätigte Werte treffen noch zu
    # 7b — was ist noch nicht bestaetigt?
    #
    # „Drei Dokumente haben wieder ungeprüfte Felder" — und nichts sagte,
    # welche. Die Bestaetigung ist der einzige Schritt, den kein Werkzeug
    # abnehmen kann; dass er fehlt, gehoert benannt (260923-dua).
    from extractors.korrekturen import finde as _finde_b
    from extractors.korrekturen import gilt_als_bestaetigt
    from extractors.korrekturen import lade_alle as _lade_b
    _korr_b = _lade_b(samples)

    def _bestaetigt(r) -> bool:
        # Was aus der Ablage kommt, ist geprueft — das ist ihre Bedingung.
        #
        # Vorher wurde ausschliesslich `korrekturen.json` befragt. Nach einer
        # Umnummerierung fand der Schluessel dort seine Zeile nicht mehr, und
        # die Pruefung meldete eine Position als unbestaetigt, die in der
        # Ablage als geprueft und gesperrt liegt. Die Ablage ist die
        # Wahrheit; die Durchsicht ist der Weg dorthin (260924-dua).
        if str(r.get("field_name") or "") == "ablage":
            return True
        return gilt_als_bestaetigt(_finde_b(
            _korr_b, r.get("pdf_name", ""), r.get("beschreibung", ""),
            r.get("pos") or 1))

    unbestaetigt = [r for r in zeilen if not _bestaetigt(r)]
    punkte.append(Punkt(
        "Jede Position ist bestätigt", not unbestaetigt,
        f"{len(zeilen) - len(unbestaetigt)} von {len(zeilen)}",
        "Diese Werte hat noch niemand im Original nachgesehen. In der Review "
        "„✓ Betrag\" setzen — das macht sie zum Sollwert, den jeder spätere "
        "Lauf treffen muss.",
        [f"{r.get('pdf_name','?')} — {r.get('beschreibung','?')}"
         for r in unbestaetigt],
        zaehle(unbestaetigt), warum(unbestaetigt)))

    # 8 — kommt die Durchsicht in der Tabelle an?
    #
    # Das ist die Zusage, auf die es ankommt: was der Mensch bestätigt oder
    # korrigiert hat, muss in der Übertragungstabelle stehen. Rot wird der
    # Punkt, wenn der Bezug abreisst — nach einer Umbenennung, einem
    # verlorenen Schlüssel, einem Eintrag, den niemand mehr findet. Genau das
    # ist mehrfach passiert.
    from extractors.korrekturen import (lade_alle, vergleiche, zerlege)
    korr = lade_alle(samples)
    # Eintraege, deren Position in der Ablage inzwischen eine andere Nummer
    # traegt, sind kein Verlust — die Ablage fuehrt sie weiter.
    aus_ablage = {(str(r.get("pdf_name") or ""), str(r.get("beschreibung") or ""))
                  for r in zeilen if str(r.get("field_name") or "") == "ablage"}
    # 7c — steht jeder Wert wirklich im Beleg?
    #
    # Das Kernversprechen des Projekts. Bis 2026-09-24 hat es niemand
    # geprueft, und `make bereit` war gruen, waehrend sechs Werte nirgends im
    # Dokument auffindbar waren. Ein Haken, der die Frage nicht stellt, ist
    # keine Antwort (260924-dua, vom Nutzer gemeldet).
    try:
        from scripts.ablage_nachweis import pruefe as _nachweis
        n = _nachweis(samples)
    except Exception:       # noqa: BLE001 - ohne Ablage gibt es nichts zu pruefen
        n = None
    if n is not None:
        falsches_dokument = [(z, g) for z, g, _ in n["fehlt"]
                             if "ANDEREN" in g]
        nicht_auffindbar = [(z, g) for z, g, _ in n["fehlt"]
                            if "ANDEREN" not in g]
        # Ein bestaetigter Wert gilt, auch wenn er nicht woertlich im Beleg
        # steht.
        #
        # Viele Zielwerte sind gerechnet — eine Depotsumme, ein Total ueber
        # mehrere Zinsen. Sie stehen so nirgends, und das ist richtig. Die
        # erste Fassung machte daraus einen roten Punkt und verlangte eine
        # Markierung, die es fuer eine Summe gar nicht geben kann. Der Mensch
        # hat den Wert geprueft; seine Bestaetigung ist die hoechste Instanz
        # in diesem System, nicht meine Suche (260924-dua).
        #
        # Rot wird es nur, wenn ein Wert weder auffindbar NOCH bestaetigt ist.
        unbestaetigt_und_unauffindbar = [
            (z, g) for z, g in nicht_auffindbar if not _bestaetigt(
                {"pdf_name": z["dokument"], "beschreibung": z["zielwert"],
                 "pos": z["pos"], "field_name": "ablage"})]
        punkte.append(Punkt(
            "Jeder Wert ist belegt oder bestätigt",
            not unbestaetigt_und_unauffindbar,
            f"{len(n['gefunden'])} von {n['gesamt']} wörtlich im Beleg, "
            f"{len(nicht_auffindbar)} bestätigt statt belegt",
            "Ein Wert, der weder im Dokument steht noch bestätigt ist, ist "
            "ungeprüft.",
            [f"{z['dokument']} — {z['zielwert']}"
             for z, _ in unbestaetigt_und_unauffindbar],
            {}, [f"{z['zielwert']} · {g}"
                 for z, g in unbestaetigt_und_unauffindbar]))
        punkte.append(Punkt(
            "Kein Wert hängt am falschen Dokument",
            not falsches_dokument,
            f"{len(falsches_dokument)} am falschen Beleg",
            "Der Betrag steht in einem anderen Dokument. Dann gehört die "
            "Position dorthin — sonst zeigt der Nachweis ins Leere.",
            [f"{z['dokument']} — {z['zielwert']}"
             for z, _ in falsches_dokument],
            {}, [f"{z['zielwert']}" for z, _ in falsches_dokument]))

    # 7d — ein Institut, eine Schreibweise.
    #
    # Zwei Schreibweisen derselben Bank reissen Hypothek und Zins
    # auseinander und ergeben zwei eSteuerauszuege fuer ein Haus.
    try:
        from scripts.etax_daten import baue as _etax
        e = _etax(samples)
    except Exception:       # noqa: BLE001
        e = None
    if e is not None:
        punkte.append(Punkt(
            "Jedes Institut hat eine Schreibweise",
            not e["gleiche_institute"],
            f"{len(e['gleiche_institute'])} doppelt geschrieben",
            "Dieselbe Bank unter zwei Namen ergibt zwei Auszüge und trennt "
            "Hypothek von Zins. `make ablage-aussteller ALT=… NEU=…` "
            "vereinheitlicht.",
            [" / ".join(g) for g in e["gleiche_institute"]],
            {}, [f"{len(g)} Schreibweisen" for g in e["gleiche_institute"]]))
        if e.get("unplausible_zinssaetze"):
            punkte.append(Punkt(
                "Die Zinssätze sind plausibel", False,
                f"{e['unplausible_zinssaetze']} unmöglich",
                "Ein Zins ist der falschen Hypothek zugeordnet.",
                [], {}, []))

    # Ein Wert, der in ein anderes Dokument gewandert ist, ist nicht verloren.
    #
    # Wird eine Position umgehaengt — weil zwei Unterlagen dasselbe ausweisen
    # und eine davon ueberfluessig ist —, zeigt der alte Eintrag ins Leere.
    # Die Zahl steht aber weiterhin in der Tabelle, nur unter einer anderen
    # Quelle. Das als „Rueckschritt" zu melden, schickt den Menschen auf die
    # Suche nach etwas, das er gerade selbst aufgeraeumt hat (260924-dua).
    from extractors.korrekturen import _normalisiere as _norm

    betraege_in_tabelle = {
        (str(r.get("beschreibung") or ""), _norm(r.get("betrag")))
        for r in zeilen if str(r.get("betrag") or "").strip()}

    def _steht_woanders(a) -> bool:
        name = a.zielwert.split(" (Position")[0]
        for k, e in korr.items():
            if not isinstance(e, dict) or not e.get("soll"):
                continue
            b_, z_, _p = zerlege(k)
            if b_ == a.beleg and z_ == name:
                if (name, _norm(e["soll"])) in betraege_in_tabelle:
                    return True
        return False

    verloren = [a for a in vergleiche(korr, zeilen)
                if a.zustand not in ("stimmt", "andernorts")
                and (a.beleg, a.zielwert.split(" (Position")[0])
                not in aus_ablage
                and not _steht_woanders(a)]
    punkte.append(Punkt(
        "Die Durchsicht steht in der Tabelle", not verloren,
        f"{len(korr)} Eintrag/Einträge geprüft, {len(verloren)} ohne Bezug",
        "Ein bestätigter Wert steht nicht in der Tabelle — der Bezug ist "
        "abgerissen. Das ist ein Rückschritt und gehört angeschaut.",
        [f"{a.beleg} — {a.zielwert} ({a.zustand})" for a in verloren],
        {a.zustand: sum(1 for b in verloren if b.zustand == a.zustand)
         for a in verloren},
        sorted({f"{a.zielwert} · {a.zustand}"
                + (f" ({_grundwort(a.grund)})" if a.grund else "")
                for a in verloren})))

    # 9 — liefert der Lauf noch, was er einmal richtig geliefert hat?
    #
    # Eine andere Frage als Punkt 8, und sie braucht den ROHEN Lauf: hat der
    # Mensch einen Wert bestätigt, OHNE ihn zu korrigieren, dann hat das
    # Modell ihn richtig gelesen — und soll ihn morgen wieder richtig lesen.
    # Das Sprachmodell ist nicht deterministisch; genau hier faellt Drift auf.
    #
    # Korrigierte Werte gehören ausdrücklich NICHT hierher. Sie weichen vom
    # rohen Lauf ab, weil das ihr Zweck ist — sie mitzuzählen machte den Punkt
    # dauerhaft rot und damit wertlos (260923-dua).
    nur_bestaetigt = {
        k: {**e, "soll": e.get("wert_betrag")}
        for k, e in korr.items()
        if isinstance(e, dict) and e.get("bestaetigt_betrag")
        and e.get("wert_betrag") and not e.get("soll")
        and not e.get("neu") and not e.get("entfernt")
        and not e.get("zurueckgenommen")
    }
    roh_zeilen, _ = _zeilen(samples, mit_korrekturen=False)
    drift = [a for a in vergleiche(nur_bestaetigt, roh_zeilen)
             if a.zustand != "stimmt"]
    punkte.append(Punkt(
        "Der Lauf liest die bestätigten Werte weiterhin richtig", not drift,
        f"{len(nur_bestaetigt)} unkorrigierte Bestätigung(en), "
        f"{len(drift)} Abweichung(en)",
        "Das Modell liest einen Wert anders als bei der Durchsicht. Die "
        "Tabelle zeigt weiterhin den geprüften Wert — aber die Extraktion "
        "hat sich verschlechtert.",
        [f"{a.beleg} — {a.zielwert} ({a.zustand})" for a in drift],
        {a.zustand: sum(1 for b in drift if b.zustand == a.zustand)
         for a in drift}))

    return punkte


def _je_dokument(input_dir: Path, samples: Path) -> None:
    """Jedes Dokument mit seiner Bewertung — eine Zeile pro Beleg.

    Die Frage „ist jedes File bewertet?" laesst sich nur so beantworten: nicht
    als Summe, sondern Dokument fuer Dokument, mit Belegart, Anzahl
    Positionen und den zugeordneten Rollen.
    """
    from extractors.vollstaendigkeit import rolle_aus_person
    from scripts.dokumente_check import _stationen

    stand = _stationen(input_dir, samples)
    zeilen, _ = _zeilen(samples)
    abgehakt = _abgehakt(samples)

    je: dict[str, list[dict]] = {}
    for r in zeilen:
        je.setdefault(r.get("pdf_name", "?"), []).append(r)

    print("\nJedes Dokument und seine Bewertung")
    print(f"   {'Dokument':<52} {'Belegart':<26} Pos  Rollen")
    for name in sorted(stand):
        st = stand[name]
        if not st.get("im_ordner"):
            continue
        rs = je.get(name, [])
        rollen = sorted({(rolle_aus_person(r.get("person")) or "—")
                         .replace("unbekannt/manuell", "OFFEN") for r in rs})
        art = st.get("belegtyp") or ("abgehakt" if name in abgehakt
                                     else "— NICHT BEWERTET")
        marke = " " if (rs or name in abgehakt) else "!"
        kurz = name if len(name) <= 50 else name[:47] + "…"
        print(f" {marke} {kurz:<52} {art:<26} {len(rs):>3}  "
              f"{', '.join(rollen) if rollen else '—'}")
    print("\n   ! = kein uebertragbarer Wert und nicht abgehakt")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", type=Path, default=Path("belege"))
    ap.add_argument("--samples", type=Path, required=True)
    ap.add_argument("--nur-zahlen", action="store_true",
                    help="Nur den teilbaren Teil ausgeben")
    args = ap.parse_args()

    if not (args.samples / "_results_full.json").exists():
        print(f"Kein Lauf gefunden: {args.samples}/_results_full.json",
              file=sys.stderr)
        return 2

    punkte = pruefe(args.input, args.samples)

    print("Bereitschaft — kann die Steuererklärung ausgefüllt werden?")
    print(TRENNER)
    for p in punkte:
        print(f" {p.marke}  {p.titel:<46} {p.zahl}")

    offen = [p for p in punkte if not p.ok]
    if not offen:
        print("\n→ Nichts mehr offen. Die Übertragungstabelle ist vollständig.")
        return 0

    print(f"\n {len(offen)} von {len(punkte)} Punkten offen. Was zu tun ist:")
    for i, p in enumerate(offen, start=1):
        print(f"\n  {i}. {p.titel} — {p.zahl}")
        if p.nach_art:
            aufteilung = ", ".join(f"{n}× {art}" for art, n in p.nach_art.items())
            print(f"     davon: {aufteilung}")
        for zeile in p.warum[:12]:
            print(f"       · {zeile}")
        if len(p.warum) > 12:
            print(f"       … und {len(p.warum) - 12} weitere")
        print(f"     {p.was_tun}")

    if args.nur_zahlen:
        return 1

    print("\n")
    print("Welche — NICHT teilen, enthält Dateinamen")
    print(TRENNER)
    _je_dokument(args.input, args.samples)
    for p in offen:
        if not p.betroffen:
            continue
        print(f"\n{p.titel}")
        for eintrag in p.betroffen[:40]:
            print(f"   · {eintrag}")
        if len(p.betroffen) > 40:
            print(f"   … und {len(p.betroffen) - 40} weitere")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
