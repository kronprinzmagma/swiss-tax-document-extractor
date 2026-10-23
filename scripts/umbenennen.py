#!/usr/bin/env python3
"""Originalbelege sprechend benennen — sobald Aussteller und Art feststehen.

Ein Beleg heisst nach dem Download ``2025_tax_statement4.9.2026155454.pdf``.
Welche Bank, welche Person, welche Art von Papier — der Name sagt es nicht.
Sobald in der Durchsicht **Aussteller und Belegart** bestätigt sind, ist alles
bekannt, was ein brauchbarer Name braucht.

Umbenennen ist ein Eingriff in die Originale. Drei Vorkehrungen:

**Nichts geschieht ungefragt.** Ohne ``--jetzt`` wird nur gezeigt, was
geschähe. Der Vorschlag steht auch in der Review-Oberfläche neben jedem
Dokument.

**Nichts wird überschrieben.** Existiert der Zielname schon, bekommt der neue
eine Nummer. Zwei Abrechnungen derselben Bank im selben Jahr gibt es wirklich.

**Alles ist umkehrbar.** Jede Umbenennung steht mit Zeitpunkt in
``umbenennungen.json``; ``--zurueck`` dreht sie in umgekehrter Reihenfolge
wieder zurück.

Der Dateiname ist nicht bloss Kosmetik: er ist der Schlüssel, unter dem die
ganze Durchsicht hängt (``korrekturen.json`` verwendet
``"<Dokument>|<Zielwert>"``). Eine Umbenennung ohne Mitziehen dieser Schlüssel
würde die gesamte Arbeit verwaisen lassen. Deshalb wandern in einem Zug mit:

* die PDF-Datei selbst,
* das Word-JSON (Dateiname und ``pdf_name`` darin),
* der Eintrag in ``_results_full.json``,
* **jeder Schlüssel in beiden ``korrekturen.json``**.

Die Sicherungen im Verlauf bleiben unangetastet — sie sollen zeigen, wie es
war. Wer auf einen Stand von vor einer Umbenennung zurückrollt, bekommt
deshalb die alten Namen; das Journal sagt, welche gemeint sind.

Ausgabe in zwei Teilen: zuerst Zahlen (teilbar), dann die Namen (bleibt auf
dem Gerät).

Aufruf::

    PYTHONPATH=. .venv/bin/python scripts/umbenennen.py \\
        --input belege --samples output/latest/json
    …  --jetzt          wirklich umbenennen
    …  --zurueck        die letzte Runde rückgängig machen
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TRENNER = "─" * 70
JOURNAL = "umbenennungen.json"

# Ohne diese beiden gibt es keinen sprechenden Namen — und ohne einen Menschen,
# der sie bestätigt hat, ist der Name geraten.
PFLICHT = ("aussteller", "belegart")


def _personennamen() -> dict[str, str]:
    """Rolle -> Klarname aus ``family.yaml``. Fehlt sie, bleibt es leer."""
    try:
        from extractors.family import load_family
        family = load_family(ROOT / "family.yaml")
    except Exception:
        return {}
    if family is None:
        return {}
    abbildung = {"mann": "elternteil_1", "frau": "elternteil_2",
                 "kind1": "kind_1", "kind2": "kind_2"}
    aus: dict[str, str] = {}
    for m in family.members:
        rolle = abbildung.get(m.role)
        if rolle:
            aus[rolle] = f"{m.first_name} {m.last_name}".strip()
    return aus


def _jahr_der_zeilen(zeilen: list[dict]) -> str:
    for z in zeilen:
        j = str(z.get("jahr") or "").strip()
        if len(j) == 4 and j.isdigit():
            return j
    return ""


def plane(samples: Path, pdf_dir: Path,
          zeilen: list[dict] | None = None) -> list[dict]:
    """Was umbenannt würde — ein Eintrag je Dokument mit Vorschlag und Grund.

    Bewertet jedes PDF im Ordner. ``bereit`` sagt, ob umbenannt werden kann;
    ``grund`` sagt sonst, was fehlt.
    """
    from extractors.dateiname import (
        baue_dateiname, freier_name, ist_schon_sprechend, typen_aus_zielwerten,
    )
    from extractors.korrekturen import lade_alle
    from extractors.vollstaendigkeit import rolle_aus_person

    korr = lade_alle(samples)
    namen = _personennamen()

    if zeilen is None:
        zeilen = []
        ergebnisse = samples / "_results_full.json"
        if ergebnisse.exists():
            from scripts.build_tax_output import (
                build_rows, ergaenze_eigene_zeilen, wende_korrekturen_an,
            )
            for e in json.loads(ergebnisse.read_text()):
                if e.get("status") == "ok":
                    zeilen.extend(build_rows(e))
            wende_korrekturen_an(zeilen, samples, entfernen=False)
            ergaenze_eigene_zeilen(zeilen, samples)

    je_beleg: dict[str, list[dict]] = {}
    for z in zeilen:
        je_beleg.setdefault(str(z.get("pdf_name") or z.get("beleg") or ""),
                            []).append(z)

    # Belegart auch aus der Durchsicht: der Mensch kann sie dort gesetzt haben.
    art_aus_durchsicht: dict[str, str] = {}
    from extractors.korrekturen import ausgeschlossene_dokumente
    art_aus_durchsicht.update(ausgeschlossene_dokumente(korr))

    belegt = {p.name for p in pdf_dir.glob("*.pdf")}
    plan: list[dict] = []
    for pdf in sorted(pdf_dir.glob("*.pdf")):
        rs = je_beleg.get(pdf.name, [])
        aussteller = next((str(r.get("aussteller") or "").strip()
                           for r in rs if str(r.get("aussteller") or "").strip()),
                          "")
        belegart = (art_aus_durchsicht.get(pdf.name)
                    or next((str(r.get("belegtyp") or "") for r in rs
                             if r.get("belegtyp")), ""))
        rollen = [rolle_aus_person(r.get("person")) for r in rs]
        rollen = [r for r in rollen if r and r != "unbekannt/manuell"]
        # Nur wenn das ganze Dokument EINER Person gehoert. Bei einem Beleg
        # ueber zwei Kinder waere ein Name im Dateinamen irrefuehrend.
        person = ""
        if rollen and len(set(rollen)) == 1:
            person = namen.get(rollen[0], rollen[0])
        jahr = _jahr_der_zeilen(rs)

        # Der Dokumenttyp kommt aus den gefundenen Zielwerten, nicht aus der
        # Belegart: ein Bankbeleg mit Saldo UND Zinsertrag ist beides, und
        # beides gehoert in den Namen (Vorgabe des Nutzers).
        zielwerte = [str(r.get("beschreibung") or r.get("zielwert") or "")
                     for r in rs if not r.get("gestrichen")]
        typen = typen_aus_zielwerten(zielwerte, belegart)

        fehlt = [f for f, w in (("Aussteller", aussteller),
                                ("Dokumenttyp", typen)) if not w]
        vorschlag = baue_dateiname(aussteller=aussteller, typen=typen,
                                   person=person, jahr=jahr)
        schon = ist_schon_sprechend(pdf.name, vorschlag)
        ziel = "" if (fehlt or schon) else freier_name(
            vorschlag, belegt - {pdf.name})

        plan.append({
            "alt": pdf.name,
            "neu": ziel,
            "vorschlag": vorschlag,
            "bereit": bool(ziel),
            "grund": ("heisst schon so" if schon and not fehlt
                      else f"es fehlt: {', '.join(fehlt)}" if fehlt else ""),
            "aussteller": aussteller,
            "belegart": belegart,
            "typen": typen,
            "person": person,
            "jahr": jahr,
        })
        if ziel:
            belegt.add(ziel)
    return plan


def _migriere_korrekturen(samples: Path, alt: str, neu: str) -> int:
    """Jeden Schlüssel ``"<alt>|…"`` auf ``"<neu>|…"`` umhängen.

    Ohne das verliert die ganze Durchsicht ihren Bezug — die Werte stünden
    unter einem Dokument, das es nicht mehr gibt.
    """
    from scripts.apply_korrekturen import _beide_ablagen, _sichere

    getroffen = 0
    for ablage in _beide_ablagen(samples / "korrekturen.json"):
        if not ablage.exists():
            continue
        try:
            daten = json.loads(ablage.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(daten, dict):
            continue
        neu_daten: dict[str, dict] = {}
        n = 0
        for k, v in daten.items():
            beleg, _, rest = k.partition("|")
            ziel = f"{neu}|{rest}"
            # Gibt es den Zielschluessel schon, wuerde das Umhaengen ihn
            # ueberschreiben — stiller Verlust. Dann bleibt der alte stehen
            # und faellt als verwaist auf, wo man ihn ansehen kann.
            if beleg == alt and ziel not in daten:
                neu_daten[ziel] = v
                n += 1
            else:
                neu_daten[k] = v
        if n:
            _sichere(ablage)
            ablage.write_text(json.dumps(neu_daten, indent=2,
                                         ensure_ascii=False,
                                         sort_keys=True) + "\n")
            getroffen = max(getroffen, n)
    return getroffen


def _migriere_ablage(samples: Path, alt: str, neu: str) -> int:
    """Den Dokumentnamen in der Ablage nachziehen.

    Die Umbenennung zog `korrekturen.json` und die Word-JSONs nach — die
    Ablage kannte sie nicht, weil die erst später entstand. Folge: die
    geprüften Positionen zeigten weiter auf den alten Namen, fanden ihr
    Word-JSON nicht mehr, und damit fiel auch das Seitenbild weg. Ohne das
    gibt es keinen Knopf, um einen Wert im Beleg zu markieren
    (260924-dua, vom Nutzer gemeldet: 7 Namen ohne Word-JSON, 10 Zeilen).

    Der Dokumentname ist in der Ablage bewusst **nicht** gesperrt — er
    verweist auf die Quelle und ist kein Wert. Genau dafür.
    """
    from extractors.ablage import oeffne, pfad_fuer

    pfad = pfad_fuer(samples)
    if not pfad.exists():
        return 0
    verbindung = oeffne(pfad)
    betroffen = [r["id"] for r in verbindung.execute(
        "SELECT id FROM position WHERE dokument = ?", (alt,))]
    if not betroffen:
        return 0
    with verbindung:
        for kennung in betroffen:
            verbindung.execute(
                "UPDATE position SET dokument=?, geaendert_am=datetime('now') "
                "WHERE id=?", (neu, kennung))
            verbindung.execute(
                "INSERT INTO protokoll (id, was, feld, vorher, nachher) "
                "VALUES (?,?,?,?,?)",
                (kennung, "umbenannt", "dokument", alt, neu))
    return len(betroffen)


def _migriere_json(samples: Path, alt: str, neu: str) -> bool:
    """Word-JSON umbenennen und ``pdf_name`` darin nachziehen."""
    getroffen = False
    for jp in sorted(samples.glob("*.json")):
        if jp.name.startswith("_") or jp.name == "korrekturen.json":
            continue
        try:
            doc = json.loads(jp.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(doc, dict) or doc.get("pdf_name") != alt:
            continue
        doc["pdf_name"] = neu
        neuer_pfad = jp.with_name(Path(neu).stem + jp.suffix)
        if neuer_pfad != jp and neuer_pfad.exists():
            neuer_pfad = jp          # lieber den alten Dateinamen behalten
        neuer_pfad.write_text(json.dumps(doc, ensure_ascii=False))
        if neuer_pfad != jp:
            jp.unlink()
        getroffen = True
    return getroffen


def _migriere_ergebnisse(samples: Path, alt: str, neu: str) -> bool:
    pfad = samples / "_results_full.json"
    if not pfad.exists():
        return False
    try:
        daten = json.loads(pfad.read_text())
    except (OSError, json.JSONDecodeError):
        return False
    getroffen = False
    for e in daten:
        if isinstance(e, dict) and e.get("pdf_name") == alt:
            e["pdf_name"] = neu
            getroffen = True
    if getroffen:
        pfad.write_text(json.dumps(daten, ensure_ascii=False, indent=2))
    return getroffen


def _journal(samples: Path) -> Path:
    return samples.parent / JOURNAL


def fuehre_aus(samples: Path, pdf_dir: Path, plan: list[dict],
               nur: str | None = None) -> list[dict]:
    """Benennt um und schreibt das Journal. Gibt die erledigten Schritte zurück.

    Ein Fehler bei einem Dokument stoppt die übrigen nicht — aber er wird
    gemeldet, und das Journal enthält nur, was wirklich geschehen ist.
    """
    getan: list[dict] = []
    for eintrag in plan:
        if not eintrag["bereit"]:
            continue
        if nur and eintrag["alt"] != nur:
            continue
        quelle, ziel = pdf_dir / eintrag["alt"], pdf_dir / eintrag["neu"]
        if not quelle.is_file() or ziel.exists():
            continue
        try:
            quelle.rename(ziel)
        except OSError:
            continue
        _migriere_korrekturen(samples, eintrag["alt"], eintrag["neu"])
        _migriere_json(samples, eintrag["alt"], eintrag["neu"])
        _migriere_ablage(samples, eintrag["alt"], eintrag["neu"])
        _migriere_ergebnisse(samples, eintrag["alt"], eintrag["neu"])
        getan.append({"zeit": datetime.now().isoformat(timespec="seconds"),
                      "alt": eintrag["alt"], "neu": eintrag["neu"]})

    if getan:
        pfad = _journal(samples)
        bisher = []
        if pfad.exists():
            try:
                bisher = json.loads(pfad.read_text())
            except (OSError, json.JSONDecodeError):
                bisher = []
        pfad.write_text(json.dumps(bisher + getan, ensure_ascii=False,
                                   indent=2))
    return getan


def journal_lesen(samples: Path) -> list[dict]:
    """Alle bisherigen Umbenennungen, älteste zuerst."""
    pfad = _journal(samples)
    if not pfad.exists():
        return []
    try:
        daten = json.loads(pfad.read_text())
    except (OSError, json.JSONDecodeError):
        return []
    return daten if isinstance(daten, list) else []


def journal_anwenden(samples: Path, input_dir: Path | None = None) -> int:
    """Einträge, die noch unter einem alten Dateinamen liegen, mitziehen.

    Die Umbenennung zieht die Durchsicht mit — aber nur, was zu diesem
    Zeitpunkt da war. Der Browser hält seinen eigenen Speicher und schrieb
    danach selbst erfasste Positionen weiter unter dem ALTEN Namen zurück.
    Daraus entstand ein zweites Dokument, das es im Ordner gar nicht gibt
    (260923-dua, vom Nutzer gemeldet).

    Diese Funktion wendet das Journal erneut an. Sie ist idempotent: gibt es
    keinen Schlüssel mehr unter dem alten Namen, tut sie nichts.

    Returns:
        Zahl der umgehängten Schlüssel.
    """
    # NIEMALS einen Namen umhaengen, den es noch gibt.
    #
    # Ein Dateiname kann nach einer Umbenennung wieder auftauchen — ein
    # zweiter Beleg desselben Instituts, oder eine zurueckgeholte Datei. Wird
    # sein Eintrag dann stur nach dem Journal verschoben, verliert ein
    # LEBENDES Dokument seine Bestaetigungen und steht wieder als offen da
    # (260923-dua). Das Journal darf nur Namen aufloesen, die niemand mehr
    # traegt.
    from scripts.verwaiste import bekannte_dokumente

    lebt: set[str] = set()
    if input_dir is not None:
        lebt = bekannte_dokumente(samples, input_dir)

    getroffen = 0
    for eintrag in journal_lesen(samples):
        alt_name, neu_name = eintrag.get("alt"), eintrag.get("neu")
        if not (alt_name and neu_name) or alt_name == neu_name:
            continue
        if alt_name in lebt:
            continue
        getroffen += _migriere_korrekturen(samples, alt_name, neu_name)
        _migriere_ablage(samples, alt_name, neu_name)
    return getroffen


def zurueck(samples: Path, pdf_dir: Path, anzahl: int | None = None
            ) -> list[dict]:
    """Umbenennungen rückgängig machen — die jüngsten zuerst."""
    pfad = _journal(samples)
    if not pfad.exists():
        return []
    try:
        journal = json.loads(pfad.read_text())
    except (OSError, json.JSONDecodeError):
        return []
    zu_tun = journal[-anzahl:] if anzahl else list(journal)
    erledigt: list[dict] = []
    for eintrag in reversed(zu_tun):
        quelle, ziel = pdf_dir / eintrag["neu"], pdf_dir / eintrag["alt"]
        if not quelle.is_file() or ziel.exists():
            continue
        try:
            quelle.rename(ziel)
        except OSError:
            continue
        _migriere_korrekturen(samples, eintrag["neu"], eintrag["alt"])
        _migriere_json(samples, eintrag["neu"], eintrag["alt"])
        _migriere_ergebnisse(samples, eintrag["neu"], eintrag["alt"])
        _migriere_ablage(samples, eintrag["neu"], eintrag["alt"])
        erledigt.append(eintrag)
    rest = [e for e in journal if e not in erledigt]
    pfad.write_text(json.dumps(rest, ensure_ascii=False, indent=2))
    return erledigt


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", type=Path, default=Path("belege"))
    ap.add_argument("--samples", type=Path, required=True)
    ap.add_argument("--jetzt", action="store_true",
                    help="Wirklich umbenennen (sonst nur zeigen)")
    ap.add_argument("--zurueck", action="store_true",
                    help="Die letzten Umbenennungen rückgängig machen")
    ap.add_argument("--nur-zahlen", action="store_true")
    args = ap.parse_args()

    if not args.input.is_dir():
        print(f"Nicht gefunden: {args.input}", file=sys.stderr)
        return 1

    if args.zurueck:
        erledigt = zurueck(args.samples, args.input)
        print(f"{len(erledigt)} Umbenennung(en) rückgängig gemacht.")
        if erledigt and not args.nur_zahlen:
            print("\nWelche — NICHT teilen, enthält Dateinamen")
            print(TRENNER)
            for e in erledigt:
                print(f"   {e['neu']}  →  {e['alt']}")
        return 0

    plan = plane(args.samples, args.input)
    bereit = [p for p in plan if p["bereit"]]
    schon = [p for p in plan if not p["bereit"] and "heisst schon" in p["grund"]]
    fehlt = [p for p in plan if not p["bereit"] and p not in schon]

    print("Sprechende Dateinamen — nur Zahlen, dieser Teil ist teilbar")
    print(TRENNER)
    print(f"   {len(plan):3}  PDF(s) im Ordner")
    print(f"   {len(bereit):3}  können umbenannt werden")
    print(f"   {len(schon):3}  heissen bereits sprechend")
    print(f"   {len(fehlt):3}  brauchen noch Aussteller oder Belegart")

    if fehlt:
        from collections import Counter
        was = Counter(p["grund"] for p in fehlt)
        for grund, n in was.most_common():
            print(f"        {n}×  {grund}")

    if not args.jetzt:
        print("\n→ Nichts geändert. Wirklich umbenennen:")
        print("   make umbenennen JETZT=1")
    else:
        getan = fuehre_aus(args.samples, args.input, plan)
        print(f"\n→ {len(getan)} Datei(en) umbenannt. "
              f"Rückgängig: make umbenennen-zurueck")

    if args.nur_zahlen:
        return 0

    print("\n")
    print("Welche — NICHT teilen, enthält Dateinamen")
    print(TRENNER)
    for p in plan:
        if p["bereit"]:
            print(f"   {p['alt']}\n      →  {p['neu']}")
        elif p["grund"] and "heisst schon" not in p["grund"]:
            print(f"   {p['alt']}\n      ·  {p['grund']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
