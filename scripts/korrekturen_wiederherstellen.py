#!/usr/bin/env python3
"""Holt die Durchsicht aus allen noch vorhandenen Quellen zurück.

Anlass: eine Regel in ``apply_korrekturen`` machte aus „diese Zeile bringt
nichts mit" ein „lösche den Eintrag". Die Live-Oberfläche schickt bei jedem
Speichern jede Zeile — also hat das blosse Öffnen der Seite die bestätigten
Werte gelöscht. Ohne Rückfrage, ohne Sicherung.

Dieses Skript nimmt jede Quelle, die es noch gibt, und führt sie zusammen.
Es löscht nichts und überschreibt nur dort, wo eine jüngere Quelle etwas
Konkretes sagt:

1. ``korrekturen.json`` neben den JSONs (der Stand der Live-Sitzung)
2. ``korrekturen.json`` eine Ebene höher (aus ``make korrekturen``)
3. jede ``korrektur*.csv`` im Output-Ordner, älteste zuerst
4. alles im Ordner ``korrekturen-verlauf`` (Sicherungen)

Reihenfolge: älteste Quelle zuerst, jüngere ergänzen. Ein Eintrag verliert
dabei nie ein Feld — nur leere Hülsen werden von echten Werten überholt.

Was dieses Skript NICHT erreichen kann: was ausschliesslich im Browser
stand und nie exportiert wurde. Das steht im ``localStorage`` der
Review-Seite und kommt zurück, sobald die Seite das nächste Mal speichert —
also die Seite öffnen und nicht vorher den Browser-Speicher leeren.

Aufruf::

    PYTHONPATH=. .venv/bin/python scripts/korrekturen_wiederherstellen.py \\
        --samples output/latest/json
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

# Felder, die einen Eintrag „wertvoll" machen. Ein Eintrag, der nur noch
# zielwert/ziffer traegt, ist eine leere Huelse — er darf einen vollen
# Eintrag nie ueberschreiben.
INHALT = ("soll", "person", "aussteller", "zielwert_neu", "ziffer_neu",
          "bestaetigt_betrag", "bestaetigt_person", "wert_betrag",
          "wert_person", "neu", "entfernt", "label", "kontext")


def _hat_inhalt(eintrag) -> bool:
    return isinstance(eintrag, dict) and any(eintrag.get(f) for f in INHALT)


def _zusammen(ziel: dict[str, dict], quelle: dict[str, dict]) -> tuple[int, int]:
    """Führt ``quelle`` in ``ziel`` — feldweise, ohne je etwas zu verlieren.

    Eine Ausnahme von „nie etwas verlieren": ein **Grabstein** gewinnt. Nimmt
    der Mensch einen Eintrag zurück, sagt er ausdrücklich „das wollte ich
    nicht mehr" — eine ältere Sicherung darf das nicht überstimmen. Vorher
    machte ``make korrekturen-zurueck`` jede Rücknahme und jede aufgehobene
    Streichung zunichte (260923-dua).
    """
    from extractors.korrekturen import ist_zurueckgenommen

    neu = ergaenzt = 0
    for schluessel, eintrag in quelle.items():
        if not isinstance(eintrag, dict):
            continue
        if ist_zurueckgenommen(ziel.get(schluessel)):
            # Schon zurueckgenommen — nur ein erneutes Setzen durch den
            # Menschen kann das aufheben, nicht eine aeltere Quelle.
            continue
        if ist_zurueckgenommen(eintrag):
            ziel[schluessel] = dict(eintrag)
            ergaenzt += 1
            continue
        if schluessel not in ziel:
            ziel[schluessel] = dict(eintrag)
            if _hat_inhalt(eintrag):
                neu += 1
            continue
        vorher = dict(ziel[schluessel])
        for feld, wert in eintrag.items():
            # Ein leerer Wert ueberschreibt nie einen vorhandenen — mit einer
            # Ausnahme: ``entfernt: False`` ist eine AUSSAGE („die Streichung
            # ist aufgehoben"), kein fehlender Wert. Ohne diese Ausnahme holte
            # die Wiederherstellung jede aufgehobene Streichung zurueck
            # (260923-dua).
            if feld in ("entfernt", "grund"):
                vorher[feld] = wert
                continue
            if wert in (None, "", False) and feld in vorher:
                continue
            vorher[feld] = wert
        if vorher != ziel[schluessel]:
            ergaenzt += 1
        ziel[schluessel] = vorher
    return neu, ergaenzt


def _aus_csv(pfad: Path) -> dict[str, dict]:
    """Eine exportierte korrektur.csv als Eintraege — ohne etwas zu schreiben."""
    import tempfile

    from scripts.apply_korrekturen import lies_korrekturen, uebernehme
    zeilen = lies_korrekturen(pfad)
    if not zeilen:
        return {}
    with tempfile.TemporaryDirectory() as tmp:
        ziel = Path(tmp) / "k.json"
        uebernehme(zeilen, ziel)
        return json.loads(ziel.read_text()) if ziel.exists() else {}


def _aus_tabelle(pfad: Path) -> dict[str, dict]:
    """Eine frühere ``steuer_uebertragung.csv`` als Einträge.

    Diese Datei wird bei jedem produktiven Lauf geschrieben — **mit** den
    damals geltenden Korrekturen. Sie ist damit ein Abbild des Zustands, den
    der Mensch zuletzt gesehen hat, und die einzige Spur eines Standes, der
    nur in der Live-Sitzung existierte.

    Übernommen werden Person und Betrag als Werte, **nicht** als
    Bestätigungen: was hier steht, kann aus dem Modell stammen. Der Mensch
    sieht wieder, was er sah, und bestätigt selbst nach.
    """
    import csv as _csv

    if not pfad.exists():
        return {}
    aus: dict[str, dict] = {}
    try:
        with pfad.open(encoding="utf-8", newline="") as fh:
            for r in _csv.DictReader(fh):
                beleg = (r.get("Quelle") or "").strip()
                ziel = (r.get("Beschreibung") or "").strip()
                if not beleg or not ziel:
                    continue
                eintrag: dict = {"zielwert": ziel,
                                 "ziffer": (r.get("Ziffer") or "").strip()}
                # Diese Werte stammen aus der fertigen Tabelle — also
                # groesstenteils vom Modell, nicht von einem Menschen. Sie
                # als ``soll`` abzulegen machte sie zu bestaetigten
                # Sollwerten: der Grader haette ab dann einen Modellwert als
                # geprueft behandelt und jede Korrektur daran als Rueckschritt
                # gemeldet (260923-dua). Deshalb ein eigener, nicht
                # bestaetigender Schluessel.
                betrag = (r.get("Betrag CHF") or "").strip()
                if betrag and betrag not in ("—", "-"):
                    eintrag["vorschlag_betrag"] = betrag
                person = (r.get("Person") or "").strip()
                if person and person not in ("—", "-"):
                    eintrag["vorschlag_person"] = person
                aus[f"{beleg}|{ziel}"] = eintrag
    except (OSError, ValueError):
        return {}
    return aus


def _aus_markdown(pfad: Path) -> dict[str, dict]:
    """Die Übertragungstabelle als Einträge.

    ``steuer_uebertragung.md`` wird bei jedem Tabellenbau geschrieben — mit
    den damals geltenden Korrekturen. Sie überlebt oft, wenn die CSV schon
    überschrieben ist, und ist damit eine eigenständige Spur des Zustands.

    Gelesen werden nur die Datenzeilen der Tabelle; Person und Betrag als
    Werte, nicht als Bestätigungen.
    """
    if not pfad.exists():
        return {}
    try:
        text = pfad.read_text(encoding="utf-8")
    except OSError:
        return {}

    aus: dict[str, dict] = {}
    spalten: list[str] = []
    for zeile in text.splitlines():
        z = zeile.strip()
        if not z.startswith("|"):
            spalten = []
            continue
        felder = [f.strip() for f in z.strip("|").split("|")]
        if not spalten:
            spalten = felder
            continue
        if all(set(f) <= set("-: ") for f in felder):
            continue          # Trennzeile unter dem Kopf
        if len(felder) != len(spalten):
            continue
        r = dict(zip(spalten, felder))
        beleg = (r.get("Quelle") or "").strip()
        ziel = (r.get("Beschreibung") or "").strip()
        if not beleg or not ziel or beleg.lower() == "quelle":
            continue
        # Die Quelle-Spalte ist fuer Menschen gemacht: `name.pdf` S.3, auf 50
        # Zeichen gekuerzt. Als Schluessel taugt sie nicht — daraus entstuenden
        # Phantom-Eintraege, die zu keiner Zeile passen (260904-rmx).
        beleg = beleg.strip("`").strip()
        for trenner in (" S.", " (text-regex)", " (text-match)"):
            if trenner in beleg:
                beleg = beleg.split(trenner)[0]
        beleg = beleg.strip("` ").rstrip("…")
        if not beleg:
            continue
        eintrag: dict = {"zielwert": ziel, "ziffer": (r.get("Ziffer") or "").strip()}
        betrag = (r.get("Betrag CHF") or "").strip().replace("\\|", "|")
        if betrag and betrag not in ("—", "-", ""):
            eintrag["soll"] = betrag
        person = (r.get("Person") or r.get("Person/Rolle") or "").strip()
        if person and person not in ("—", "-"):
            eintrag["person"] = person
        aus[f"{beleg}|{ziel}"] = eintrag
    return aus


def _ergaenze(gesamt: dict[str, dict],
              quelle: dict[str, dict]) -> tuple[int, int] | None:
    """Führt eine Quelle mit gekürzten Dokumentnamen ein — nur ergänzend.

    Der Name aus der Markdown-Tabelle kann abgeschnitten sein. Er wird
    deshalb als Präfix gegen die bereits bekannten Schlüssel geprüft; passt
    genau einer, werden fehlende Felder ergänzt. Passt keiner oder mehrere,
    bleibt die Zeile liegen — ein falsch zugeordneter Betrag wäre schlimmer
    als ein fehlender.
    """
    if not quelle:
        return None
    ergaenzt = 0
    for schluessel, eintrag in quelle.items():
        beleg, _, ziel = schluessel.partition("|")
        treffer = [k for k in gesamt
                   if k.partition("|")[2] == ziel
                   and k.partition("|")[0].startswith(beleg)]
        if len(treffer) != 1:
            continue
        vorhanden = gesamt[treffer[0]]
        vorher = dict(vorhanden)
        for feld, wert in eintrag.items():
            if wert in (None, "", False):
                continue
            vorhanden.setdefault(feld, wert)
        if vorhanden != vorher:
            ergaenzt += 1
    return (len(quelle), ergaenzt)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--samples", type=Path, required=True,
                    help="Ordner mit den Word-JSONs (dort liegt der Live-Stand)")
    ap.add_argument("--schreiben", action="store_true",
                    help="Ergebnis wirklich schreiben (sonst nur berichten)")
    args = ap.parse_args()

    from extractors.korrekturen import lade
    from scripts.apply_korrekturen import _sichere

    latest = args.samples.parent
    quellen: list[tuple[str, dict]] = []

    # Aelteste zuerst: Sicherungen, dann CSVs nach Alter, dann die beiden
    # aktuellen Dateien. So gewinnt der juengste konkrete Wert.
    # Nach echter Zeit ordnen, nicht nach Ablage. Vorher kamen erst alle
    # Sicherungen des einen Ordners, dann alle des anderen — ein aelterer
    # Stand ueberschrieb damit regelmaessig einen juengeren (260923-dua).
    verlauf = list((latest / "korrekturen-verlauf").glob("*.json"))
    verlauf += list((args.samples / "korrekturen-verlauf").glob("*.json"))
    for p in sorted(verlauf, key=lambda x: (x.stem.replace("-start", ""),
                                            x.stat().st_mtime)):
        quellen.append((f"Sicherung {p.name}", lade(p)))

    # Aus Time Machine zurueckgeholte Staende. Der Finder haengt beim
    # "Beide behalten" eine Nummer an: korrekturen 2.json, korrekturen 3.json.
    # Sie koennen in beiden Ordnern liegen, je nachdem wo zurueckgeholt wurde.
    for ordner in (latest, args.samples):
        for jp in sorted(ordner.glob("korrekturen*.json")):
            if jp.name == "korrekturen.json":
                continue        # wird weiter unten ohnehin gelesen
            quellen.append((f"zurueckgeholt: {jp.name} ({ordner.name})",
                            lade(jp)))

    csvs = [q for ordner in (latest, args.samples)
            for q in ordner.glob("[Kk]orrektur*.csv")]
    for q in sorted(csvs, key=lambda x: x.stat().st_mtime):
        quellen.append((f"CSV {q.name} ({q.parent.name})", _aus_csv(q)))

    for name in ("steuer_uebertragung.csv", "_results.csv"):
        for ordner in (latest, args.samples):
            eintraege = _aus_tabelle(ordner / name)
            if eintraege:
                quellen.append((f"{name} ({ordner.name})", eintraege))

    quellen.append(("korrekturen.json (Output-Ordner)",
                    lade(latest / "korrekturen.json")))
    quellen.append(("korrekturen.json (Live-Sitzung)",
                    lade(args.samples / "korrekturen.json")))

    print("Wiederherstellung — welche Quellen gibt es noch?")
    print(TRENNER)
    gesamt: dict[str, dict] = {}
    for name, quelle in quellen:
        voll = sum(1 for e in quelle.values() if _hat_inhalt(e))
        neu, ergaenzt = _zusammen(gesamt, quelle)
        print(f"   {len(quelle):4} Eintraege ({voll:3} mit Inhalt)  "
              f"→ {neu:3} neu, {ergaenzt:3} ergaenzt   {name}")
    if not quellen:
        print("   keine")

    # Die Markdown-Tabellen zum Schluss — sie duerfen nur ergaenzen. Ihr
    # Dokumentname ist gekuerzt, ein neuer Schluessel daraus waere ein
    # Phantom, das zu keiner Zeile passt.
    for ordner in (latest, args.samples):
        pfad = ordner / "steuer_uebertragung.md"
        n = _ergaenze(gesamt, _aus_markdown(pfad))
        if n is not None:
            print(f"   {n[0]:4} Zeilen gelesen                        "
                  f"→ {n[1]:3} ergaenzt   steuer_uebertragung.md ({ordner.name})")

    voll = sum(1 for e in gesamt.values() if _hat_inhalt(e))
    print(f"\n   Zusammengefuehrt: {len(gesamt)} Eintraege, {voll} mit Inhalt.")

    if not args.schreiben:
        print("\n→ Nichts geschrieben. Mit --schreiben wirklich ausfuehren:")
        print(f"   make korrekturen-zurueck")
        return 0

    text = json.dumps(gesamt, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    for ziel in (latest / "korrekturen.json", args.samples / "korrekturen.json"):
        _sichere(ziel)
        ziel.write_text(text)
        print(f"   geschrieben: {ziel}")
    print("\n→ Wiederhergestellt. Was nur im Browser stand, kommt zurueck, "
          "sobald die Review-Seite das naechste Mal speichert — also die "
          "Seite oeffnen und den Browser-Speicher NICHT leeren.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
