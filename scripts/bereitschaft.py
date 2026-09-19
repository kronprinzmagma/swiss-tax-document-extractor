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
  8. Treffen die bestätigten Sollwerte noch zu?

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

    @property
    def marke(self) -> str:
        return "✓" if self.ok else "✗"


def _laden(samples: Path) -> list[dict]:
    pfad = samples / "_results_full.json"
    return json.loads(pfad.read_text()) if pfad.exists() else []


def _zeilen(samples: Path):
    """Die Zeilen, wie sie in der Übertragungstabelle landen."""
    from scripts.build_tax_output import (
        build_rows, drop_zero_rows, ergaenze_eigene_zeilen,
        wende_korrekturen_an,
    )
    roh: list[dict] = []
    for e in _laden(samples):
        if e.get("status") == "ok":
            roh.extend(build_rows(e))
    wende_korrekturen_an(roh, samples)
    ergaenze_eigene_zeilen(roh, samples)
    behalten, verworfen = drop_zero_rows(roh)
    return behalten, verworfen


def _abgehakt(samples: Path) -> set[str]:
    """Dokumente, die der Mensch ausdrücklich als erledigt markiert hat."""
    from extractors.korrekturen import lade_alle
    from scripts.build_review_html import BELEGTYP_WAHL
    aus: set[str] = set()
    for schluessel, eintrag in lade_alle(samples).items():
        beleg, _, zielwert = schluessel.partition("|")
        if zielwert in BELEGTYP_WAHL and isinstance(eintrag, dict):
            aus.add(beleg)
    return aus


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

    def zaehle(rs) -> dict[str, int]:
        aus: dict[str, int] = {}
        for r in rs:
            aus[art(r)] = aus.get(art(r), 0) + 1
        return dict(sorted(aus.items(), key=lambda kv: -kv[1]))


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

    # 3 — Betrag
    ohne_betrag_r = [r for r in zeilen
                     if not str(r.get("betrag") or "").strip()
                     or is_manual_review_marker(r.get("betrag"))]
    punkte.append(Punkt(
        "Jede Position hat einen Betrag", not ohne_betrag_r,
        f"{len(zeilen) - len(ohne_betrag_r)} von {len(zeilen)}",
        "In der Review den Betrag im Seitenbild anklicken oder eintragen.",
        [f"{r.get('pdf_name','?')} — {r.get('beschreibung','?')}"
         for r in ohne_betrag_r],
        zaehle(ohne_betrag_r)))

    # 4 — Person, wo sie zählt
    persoenlich = ("KVG", "Grundversicherung", "VVG", "Zusatzversicherung",
                   "Nettolohn", "Säule 3a", "Selbst getragene",
                   "Kinderbetreuung")

    def zugeordnet(r) -> bool:
        # „unbekannt/manuell" ist eine gueltige Rolle im Datenmodell, aber
        # keine Zuordnung — sie heisst gerade „hier fehlt sie noch".
        return rolle_aus_person(r.get("person")) not in ("", "unbekannt/manuell")

    ohne_person_r = [r for r in zeilen
                     if any(m in str(r.get("beschreibung") or "")
                            for m in persoenlich)
                     and not zugeordnet(r)]
    punkte.append(Punkt(
        "Personenbezogene Positionen sind zugeordnet", not ohne_person_r,
        f"{len(ohne_person_r)} ohne Rolle",
        "In der Review die Person wählen — „↧ alle\" überträgt sie auf das "
        "ganze Dokument.",
        [f"{r.get('pdf_name','?')} — {r.get('beschreibung','?')}"
         for r in ohne_person_r],
        zaehle(ohne_person_r)))

    # 5 — Ziffer aus dem Feldvertrag
    falsche_r = [r for r in zeilen
                 if str(r.get("ziffer") or "") not in ZIFFERN]
    punkte.append(Punkt(
        "Jede Position hat eine gültige Ziffer", not falsche_r,
        f"{len(zeilen) - len(falsche_r)} von {len(zeilen)}",
        "Zielwert in der Review wählen — die Ziffer zieht dann mit.",
        [f"{r.get('pdf_name','?')} — {r.get('beschreibung','?')} "
         f"[{r.get('ziffer') or 'keine'}]" for r in falsche_r],
        zaehle(falsche_r)))

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
        [f"{b.text}" for b in befunde]))

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
    from extractors.korrekturen import lade_alle, vergleiche
    korr = lade_alle(samples)
    abweichungen = [a for a in vergleiche(korr, zeilen)
                    if a.zustand != "stimmt"]
    punkte.append(Punkt(
        "Bestätigte Werte stimmen weiterhin", not abweichungen,
        f"{len(korr)} Eintrag/Einträge geprüft, {len(abweichungen)} Abweichung(en)",
        "Ein bestätigter Wert hat sich geändert — das ist ein Rückschritt und "
        "gehört angeschaut.",
        [f"{a.beleg} — {a.zielwert} ({a.zustand})"
         for a in abweichungen],
        # Nach Belegart und Zustand, damit sichtbar wird, ob es Drift des
        # Modells ist oder eine ganze Belegart weggebrochen ist.
        {f"{a.zustand}": sum(1 for b in abweichungen if b.zustand == a.zustand)
         for a in abweichungen}))

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
