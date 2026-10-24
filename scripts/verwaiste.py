#!/usr/bin/env python3
"""Einträge ohne Dokument — finden, zuordnen, und erst dann entfernen.

In der Durchsicht kann ein Eintrag zurückbleiben, dessen Dokument es nicht
mehr gibt: nach einer Umbenennung, wenn der Browser noch den alten Namen
hielt, oder wenn ein PDF aus dem Ordner verschwunden ist. In der Übersicht
steht dann ein zweites Dokument, das nirgends eine Datei hat.

Die Reihenfolge ist wichtig, und sie ist hier festgelegt:

1. **Zuordnen, nicht wegwerfen.** Das Umbenennungs-Journal weiss, welcher alte
   Name auf welchen neuen zeigt. Was sich so auflösen lässt, wird umgehängt.
2. **Zeigen.** Was danach übrig bleibt, wird aufgelistet — mit dem, was daran
   hängt: bestätigte Beträge, Personen, selbst erfasste Positionen.
3. **Erst dann entfernen**, und nur mit ``--jetzt``. Vorher geht eine Kopie in
   den Verlauf; ``make staende`` holt sie zurück.

Ein Eintrag gilt als verwaist, wenn es zu seinem Dokument **nichts** gibt:
keine Datei im Eingangsordner, keine im Ordner der aussortierten, kein
Word-JSON und keinen Eintrag im Extraktionsergebnis.

Aufruf::

    PYTHONPATH=. .venv/bin/python scripts/verwaiste.py \\
        --input belege --samples output/latest/json
    …  --jetzt   wirklich entfernen
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TRENNER = "─" * 70

# Felder, die zeigen, dass an einem Eintrag Arbeit hängt.
INHALT = ("soll", "person", "aussteller", "zielwert_neu", "ziffer_neu",
          "bestaetigt_betrag", "bestaetigt_person", "wert_betrag",
          "wert_person", "neu", "entfernt", "label", "unterscheidung")


def bekannte_dokumente(samples: Path, input_dir: Path) -> set[str]:
    """Jeder Dokumentname, für den es irgendetwas gibt."""
    namen: set[str] = set()
    for ordner in (input_dir, input_dir / "irrelevant"):
        if ordner.is_dir():
            namen |= {p.name for p in ordner.glob("*.pdf")}

    for jp in sorted(samples.glob("*.json")):
        if jp.name.startswith("_") or jp.name == "korrekturen.json":
            continue
        try:
            doc = json.loads(jp.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(doc, dict) and doc.get("pdf_name"):
            namen.add(str(doc["pdf_name"]))

    ergebnisse = samples / "_results_full.json"
    if ergebnisse.exists():
        try:
            for e in json.loads(ergebnisse.read_text()):
                if isinstance(e, dict) and e.get("pdf_name"):
                    namen.add(str(e["pdf_name"]))
        except (OSError, ValueError):
            pass
    return namen


def finde_verwaiste(samples: Path, input_dir: Path) -> dict[str, list[str]]:
    """Dokumentname -> Schlüssel, die daran hängen."""
    from extractors.korrekturen import lade_alle, zerlege

    bekannt = bekannte_dokumente(samples, input_dir)
    aus: dict[str, list[str]] = {}
    for schluessel, eintrag in lade_alle(samples).items():
        if not isinstance(eintrag, dict):
            continue
        beleg = zerlege(schluessel)[0]
        if beleg and beleg not in bekannt:
            aus.setdefault(beleg, []).append(schluessel)
    return aus


def finde_doppelte(samples: Path) -> dict[str, list[str]]:
    """Selbst erfasste Positionen, die einander Wort für Wort gleichen.

    Ein Dokument, derselbe Zielwert, derselbe Betrag, mehrfach als eigene
    Position — das entsteht nicht durch Tippen, sondern durch einen Fehler:
    die Oberfläche hängte ihre lokale Kopie bei jedem Laden erneut an,
    während der Server dieselbe Zeile längst lieferte. Jeder Seitenaufbau
    machte eine mehr (260923-dua, vom Nutzer gemeldet).

    Unterschiedliche Beträge bleiben unangetastet — zwei Kinder in derselben
    Kita sind zwei Positionen, und die sehen fast gleich aus.

    Returns:
        Gruppenschlüssel -> Schlüssel, ab dem zweiten (der erste bleibt).
    """
    from extractors.korrekturen import (
        ist_gestrichen, ist_zurueckgenommen, lade_alle, zerlege,
    )

    # Welche Schluessel ergeben tatsaechlich eine Zeile?
    #
    # Nicht jeder selbst erfasste Eintrag wird sichtbar: liegt auf seiner
    # Position schon ein extrahierter Wert, ueberspringt der Tabellenbau ihn.
    # Beim Aufraeumen muss die SICHTBARE Kopie bleiben — die erste Fassung
    # behielt stur die niedrigste Position und loeschte damit genau die, die
    # man sah (260923-dua).
    sichtbar: set[str] = set()
    try:
        from extractors.korrekturen import baue_schluessel
        from scripts.build_tax_output import (
            build_rows, ergaenze_eigene_zeilen,
        )
        rows: list[dict] = []
        ergebnisse = samples / "_results_full.json"
        if ergebnisse.exists():
            for e in json.loads(ergebnisse.read_text()):
                if (e.get("status") == "ok"
                        and str(e.get("pdf_name") or "").lower().endswith(".pdf")):
                    rows.extend(build_rows(e))
        vorher = len(rows)
        ergaenze_eigene_zeilen(rows, samples)
        sichtbar = {baue_schluessel(r.get("pdf_name", ""),
                                    r.get("beschreibung", ""), r.get("pos") or 1)
                    for r in rows[vorher:]}
    except Exception:
        sichtbar = set()

    gruppen: dict[str, list[str]] = {}
    for schluessel, e in sorted(lade_alle(samples).items()):
        if not isinstance(e, dict) or not e.get("neu"):
            continue
        if ist_zurueckgenommen(e) or ist_gestrichen(e):
            continue
        soll = str(e.get("soll") or "").strip()
        if not soll:
            continue
        beleg, ziel, _ = zerlege(schluessel)
        # Person und Unterscheidung gehören zur Identität: zwei Kinder mit
        # demselben Betrag sind zwei Positionen, keine Dublette.
        kennung = (f"{beleg}|{ziel}|{soll}|{e.get('person') or ''}"
                   f"|{e.get('unterscheidung') or ''}")
        gruppen.setdefault(kennung, []).append(schluessel)
    aus: dict[str, list[str]] = {}
    for kennung, ks in gruppen.items():
        if len(ks) < 2:
            continue
        # Behalten: die sichtbare Kopie. Gibt es keine oder mehrere, die
        # erste — dann ist ohnehin keine Wahl besser als die andere.
        bleibt = next((k for k in ks if k in sichtbar), ks[0])
        aus[kennung] = [k for k in ks if k != bleibt]
    return aus


def _hat_inhalt(eintrag) -> bool:
    return isinstance(eintrag, dict) and any(
        eintrag.get(f) not in (None, "", False) for f in INHALT)


def probe(samples: Path, weg: list[str]) -> dict[str, tuple[int, int]]:
    """Was käme heraus? Zeilen je Zielwert vorher und nachher.

    Gerechnet auf einer Kopie, ohne irgendetwas anzufassen — und mit
    **demselben Aufruf**, aus dem die Oberfläche ihre Zeilen baut.

    Die erste Fassung hat den Weg nachgebaut und dabei zwei Schritte
    ausgelassen (die Korrekturen und das Verwerfen der Nullzeilen). Sie sagte
    „4 → 3" voraus, heraus kamen 2. Eine Projektion, die etwas anderes misst
    als das Ziel, ist schlimmer als keine: man verlässt sich darauf
    (260923-dua).
    """
    import collections
    import shutil
    import tempfile

    from extractors.korrekturen import lade_alle
    from scripts.build_review_html import sammle_zeilen

    def zeilen_mit(korr: dict) -> collections.Counter:
        tmp = Path(tempfile.mkdtemp(prefix="steuer-probe-"))
        try:
            ziel = tmp / "json"
            ziel.mkdir()
            # Alles, was der Aufbau liest — nur die Korrekturen ersetzt.
            for name in ("_results_full.json",):
                quelle = samples / name
                if quelle.exists():
                    (ziel / name).write_bytes(quelle.read_bytes())
            for jp in samples.glob("*.json"):
                if jp.name.startswith("_") or jp.name == "korrekturen.json":
                    continue
                (ziel / jp.name).write_bytes(jp.read_bytes())
            (ziel / "korrekturen.json").write_text(
                json.dumps(korr, ensure_ascii=False))
            zeilen = sammle_zeilen(ziel, pdf_dir=None, mit_crops=False)
            return collections.Counter(
                str(r.get("zielwert") or "?") for r in zeilen)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    alles = lade_alle(samples)
    vorher = zeilen_mit(alles)
    nachher = zeilen_mit({k: v for k, v in alles.items() if k not in set(weg)})
    return {z: (vorher.get(z, 0), nachher.get(z, 0))
            for z in sorted(set(vorher) | set(nachher))}


def entferne(samples: Path, schluessel: list[str]) -> int:
    """Entfernt die Schlüssel aus beiden Ablagen — nach einer Sicherung.

    **Bestätigtes wird nie entfernt.** Dieses Werkzeug räumt Reste weg, deren
    Dokument es nicht mehr gibt. Zweimal hat es dabei Positionen gelöscht, die
    der Mensch selbst eingetippt und bestätigt hatte — beide Male, weil eine
    Aufräum-Regel die falsche der beiden Kopien für den Rest hielt
    (260923-dua). Ein verwaister, aber bestätigter Eintrag ist kein Müll,
    sondern ein Hinweis: das Dokument fehlt, die Zahl nicht. Er bleibt stehen
    und wird gemeldet.
    """
    from extractors.korrekturen import gilt_als_bestaetigt
    from scripts.apply_korrekturen import _beide_ablagen, _sichere

    weg = set(schluessel)
    # Was bestätigt ist, kommt von der Löschliste runter — vor jedem Zugriff
    # auf die Platte.
    geschuetzt = set()
    for ablage in _beide_ablagen(samples / "korrekturen.json"):
        if not ablage.exists():
            continue
        try:
            daten = json.loads(ablage.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(daten, dict):
            continue
        for k in weg:
            eintrag = daten.get(k)
            if isinstance(eintrag, dict) and gilt_als_bestaetigt(eintrag):
                geschuetzt.add(k)
    if geschuetzt:
        print(f"   {len(geschuetzt)} bestätigte(r) Eintrag/Einträge bleiben "
              f"stehen — bestätigte Werte werden hier nie entfernt.")
        for k in sorted(geschuetzt):
            print(f"      behalten: {k.rsplit('|', 2)[1] if k.count('|') >= 2 else k}")
    weg -= geschuetzt
    if not weg:
        return 0
    entfernt = 0
    for ablage in _beide_ablagen(samples / "korrekturen.json"):
        if not ablage.exists():
            continue
        try:
            daten = json.loads(ablage.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(daten, dict):
            continue
        rest = {k: v for k, v in daten.items() if k not in weg}
        if len(rest) == len(daten):
            continue
        _sichere(ablage)
        ablage.write_text(json.dumps(rest, indent=2, ensure_ascii=False,
                                     sort_keys=True) + "\n")
        entfernt = max(entfernt, len(daten) - len(rest))
    return entfernt


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", type=Path, default=Path("belege"))
    ap.add_argument("--samples", type=Path, required=True)
    ap.add_argument("--doppelte", action="store_true",
                    help="Auch die ueberzaehligen Kopien entfernen")
    ap.add_argument("--nur", default=None,
                    help="Nur Kopien dieses Zielwerts entfernen — zwei echte "
                         "Positionen mit gleichem Betrag sind von einer "
                         "Dublette nicht zu unterscheiden")
    ap.add_argument("--jetzt", action="store_true",
                    help="Die verwaisten Eintraege wirklich entfernen")
    ap.add_argument("--nur-zahlen", action="store_true")
    args = ap.parse_args()

    if not args.input.is_dir():
        print(f"Nicht gefunden: {args.input}", file=sys.stderr)
        return 1

    # Erst zuordnen — was sich auflösen lässt, ist nicht verwaist.
    from extractors.korrekturen import lade_alle
    from scripts.umbenennen import journal_anwenden

    umgehaengt = journal_anwenden(args.samples, args.input)
    verwaist = finde_verwaiste(args.samples, args.input)
    korr = lade_alle(args.samples)

    mit_arbeit = {b: ks for b, ks in verwaist.items()
                  if any(_hat_inhalt(korr.get(k)) for k in ks)}
    ohne_arbeit = {b: ks for b, ks in verwaist.items() if b not in mit_arbeit}
    alle_schluessel = [k for ks in verwaist.values() for k in ks]

    print("Einträge ohne Dokument — nur Zahlen, dieser Teil ist teilbar")
    print(TRENNER)
    if umgehaengt:
        print(f"   {umgehaengt:3}  Eintrag/Einträge einer früheren "
              f"Umbenennung nachgezogen")
    print(f"   {len(verwaist):3}  Dokumentname(n) ohne jede Datei")
    print(f"   {len(alle_schluessel):3}  Eintrag/Einträge hängen daran")
    print(f"   {len(mit_arbeit):3}  davon mit Inhalt (bestätigt, korrigiert "
          f"oder selbst erfasst)")

    doppelt = finde_doppelte(args.samples)
    if args.nur:
        from extractors.korrekturen import zerlege as _zer
        doppelt = {kennung: ks for kennung, ks in doppelt.items()
                   if _zer(ks[0])[1] == args.nur}
    doppel_schluessel = [k for ks in doppelt.values() for k in ks]
    if doppel_schluessel:
        print(f"   {len(doppelt):3}  selbst erfasste Position(en) mehrfach "
              f"vorhanden")
        print(f"   {len(doppel_schluessel):3}  ueberzaehlige Kopie(n) "
              f"(gleicher Beleg, Zielwert, Betrag, Person)")

    if not verwaist and not doppel_schluessel:
        print("\n→ Nichts verwaist, nichts doppelt.")
        return 0

    if not args.jetzt:
        print("\n→ Nichts entfernt. Verwaiste entfernen:")
        print("   make verwaiste JETZT=1")
        if doppel_schluessel:
            print("   Auch die ueberzaehligen Kopien:")
            print("   make verwaiste JETZT=1 DOPPELTE=1")
            print("   Besser gezielt, ein Zielwert nach dem anderen:")
            print("   make verwaiste JETZT=1 DOPPELTE=1 NUR=\"<Zielwert>\"")
            print("\n   ACHTUNG: zwei echte Positionen mit gleichem Betrag,")
            print("   gleicher Person und ohne Unterscheidung sind von einer")
            print("   Dublette nicht zu unterscheiden. Die Projektion unten")
            print("   zeigt, was verschwaende — erst lesen, dann entfernen.")
            print("\n   Was dabei herauskaeme (gerechnet, nichts angefasst):")
            for ziel, (a, b) in probe(args.samples, doppel_schluessel).items():
                if a != b:
                    print(f"      {ziel:<44} {a} → {b}")
            print("      (alle uebrigen Zielwerte unveraendert)")
        print("   Vorher geht eine Kopie in den Verlauf — "
              "zurück mit make staende")
    else:
        # Doppelte nur, wenn ausdruecklich verlangt. Ein Aufraeumbefehl, der
        # nebenbei selbst erfasste Positionen entfernt, ist zu gefaehrlich —
        # er hat es schon einmal getan (260923-dua).
        if args.doppelte:
            alle_schluessel = alle_schluessel + doppel_schluessel
        n = entferne(args.samples, alle_schluessel)
        print(f"\n→ {n} Eintrag/Einträge entfernt. "
              f"Zurückholen: make staende → make zurueckrollen AUF=…")

    if args.nur_zahlen:
        return 0

    if doppelt:
        # Wann ist jede Kopie zum ersten Mal aufgetaucht?
        #
        # Der Vervielfaeltigungsfehler legte seine Kopien beim Seitenaufbau
        # an — mehrere im selben Augenblick. Was der Mensch getippt hat,
        # erscheint einzeln und zu verschiedenen Zeiten. Der Verlauf weiss
        # das, und damit laesst sich trennen, was sonst gleich aussieht
        # (260923-dua).
        from extractors.korrekturen import lade as _lade
        verlauf = sorted(
            [q for o in (args.samples.parent, args.samples)
             for q in (o / "korrekturen-verlauf").glob("korrekturen-*.json")],
            key=lambda q: q.stem)
        zuerst: dict[str, str] = {}
        for q in verlauf:
            marke = q.stem.replace("korrekturen-", "").replace("-start", "")
            for k in _lade(q):
                zuerst.setdefault(k, marke)

        print("\n   Wann sind die Kopien entstanden? (teilbar)")
        from extractors.korrekturen import zerlege as _zer2
        for kennung, ks in sorted(doppelt.items()):
            ziel = _zer2(ks[0])[1]
            alle = [k for k in _lade(args.samples / "korrekturen.json")
                    if k in ks] + ks
            zeiten = sorted({zuerst.get(k, "?") for k in set(alle)})
            print(f"      {ziel:<32} Kopien zuerst gesehen: "
                  f"{', '.join(z[-6:] for z in zeiten)}")

        print("\n   Doppelte (nur Zielwert, teilbar):")
        from extractors.korrekturen import zerlege as _zer
        for kennung, ks in sorted(doppelt.items()):
            ziel = _zer(ks[0])[1]
            print(f"      {len(ks) + 1}×  {ziel}  — {len(ks)} zu viel")

    print("\n")
    print("Welche — NICHT teilen, enthält Dateinamen")
    print(TRENNER)
    for beleg, ks in sorted(verwaist.items()):
        marke = "!" if beleg in mit_arbeit else " "
        print(f" {marke} {beleg}")
        for k in sorted(ks):
            e = korr.get(k) or {}
            was = [f for f in INHALT if e.get(f) not in (None, "", False)]
            print(f"      · {k.partition('|')[2]}"
                  f"{'  [' + ', '.join(was) + ']' if was else '  (leer)'}")
    print("\n   ! = daran hängt Arbeit. Vor dem Entfernen ansehen.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
