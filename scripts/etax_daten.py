#!/usr/bin/env python3
"""Die Ablage als Eingabe für den eSteuerauszug-Erzeuger.

Der Barcode-Weg: ZHprivateTax importiert ein PDF nach eCH-0196 und füllt das
Wertschriftenverzeichnis selbst aus — kein Feld, kein Klick. Die Banken
verlangen für dieses PDF Geld; erzeugt wird es hier aus den selbst geprüften
Werten.

Gebaut wird es von `opensteuerauszug` (MIT, https://github.com/vroonhof/
opensteuerauszug), einem gepflegten Python-Paket, das genau dafür gemacht ist.
Es liegt in einem **eigenen** venv (`.venv-etax`), weil es Git-Forks von
`ibflex` und `pdf417` mitbringt, die in der Umgebung dieses Projekts nichts zu
suchen haben. Dieses Skript schreibt die Brücke zwischen beiden: eine schlichte
JSON-Datei.

**Ein Auszug je Institut.** Ein eSteuerauszug stammt im Original von einer Bank
und trägt genau ein Institut. Bei mehreren Banken entstehen deshalb mehrere
PDFs — so, wie man sie auch einzeln von den Banken bekäme.

Aufruf::

    make etax-daten
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

# Welcher Zielwert ist was im Sinn von eCH-0196.
SALDO = ("Saldo 31.12.", "Steuerwert 31.12.")
ERTRAG_MIT_VST = "Bruttoertrag mit Verrechnungssteuer"
ERTRAG_OHNE_VST = "Bruttoertrag ohne Verrechnungssteuer"
SCHULD = "Hypothekarschuld 31.12."
SCHULDZINS = "Schuldzinsen Hypothek"


def _jahr(zeilen: list[dict], rueckfall: str = "") -> str:
    for z in zeilen:
        j = str(z.get("jahr") or "").strip()
        if len(j) == 4 and j.isdigit():
            return j
    return rueckfall


def _zeilen_der_tabelle(samples: Path) -> list[dict]:
    """Die Zeilen der Übertragungstabelle — sonst die Ablage allein.

    Die Ablage führt die geprüfte Grundmenge; was der Mensch danach ergänzt,
    steht in der Tabelle, aber nicht dort. Aus der Ablage allein gebaut
    fehlten sieben Konten im Auszug — und niemand hätte es gesehen, weil das
    PDF trotzdem richtig aussieht (260924-dua).

    Fehlt das Extraktionsergebnis, bleibt die Ablage die Wahrheit. Das ist
    kein Notbehelf: sie ist der Teil, den ein Mensch geprüft hat.
    """
    from extractors.korrekturen import vertragsname

    def _vertrag(z: dict) -> dict:
        # Eine Position der Durchsicht unter altem Namen („Vermögensstand
        # 31.12.") gehört zum Feld des Vertrags — sonst landet sie unter
        # „Übrige" statt beim Konto (261024-jga, echter Lauf).
        bevorzugt = ("Steuerwert 31.12." if z.get("belegtyp") == "wertschriftenverzeichnis"
                     else "Saldo 31.12.")
        import unicodedata
        name = unicodedata.normalize("NFC", str(z.get("zielwert") or ""))
        return {**z, "zielwert": vertragsname(name, bevorzugt)}

    try:
        from scripts.build_review_html import sammle_zeilen
        return [_vertrag({**z, "pdf_name": z.get("beleg")})
                for z in sammle_zeilen(samples, pdf_dir=None, mit_crops=False)
                if not z.get("gestrichen")]
    except (OSError, FileNotFoundError, ValueError):
        from extractors.ablage import lade_geprueft
        return lade_geprueft(samples)


def _kanonisch(roh) -> str:
    """Ein Betrag als reine Dezimalzahl (``"21083"``) — oder leer."""
    from extractors.eintraege import betrag
    wert = betrag(roh)
    return "" if wert is None else str(wert)


def baue(samples: Path) -> dict:
    """Konten und Schulden je Institut — die Eingabe für den Erzeuger."""

    # Dieselben Zeilen wie die Uebertragungstabelle, nicht nur die Ablage.
    #
    # Die Ablage fuehrt die gepruefte Grundmenge; was der Mensch danach
    # ergaenzt, steht in der Tabelle, aber nicht dort. Aus der Ablage allein
    # gebaut fehlten sieben Konten im Auszug — und niemand haette es gesehen,
    # weil das PDF trotzdem richtig aussieht (260924-dua).
    zeilen = _zeilen_der_tabelle(samples)

    # Dieselben Einträge wie die Leiste (261024-jga).
    #
    # Bisher gruppierte der Auszug die Tabellenzeilen selbst: ein Wert je
    # Dokument und Zielwert. Ein Dokument mit drei Hypotheken wurde zu einer,
    # beim Depot fiel Kolonne A weg. Jetzt liest er, was die Leiste zeigt —
    # Konten und Hypotheken aus der Kette, Depots aus festen Stellen.
    from extractors.eintraege import alle_eintraege
    from scripts.build_tax_output import konto_key
    from scripts.export_extension import _inhaber

    k_alle, s_alle = alle_eintraege(samples, zeilen, _inhaber(samples), konto_key)

    def feld(e, anfang: str) -> str:
        f = next((f for f in e.felder if f.titel.startswith(anfang)), None)
        # Der Erzeuger braucht die reine Dezimalzahl, nicht 10'000.00.
        return _kanonisch(f.einsetzen) if f and f.einsetzen else ""

    def bank(e) -> str:
        f = next((f for f in e.felder if f.titel.startswith(("Bank", "Name, Vorname"))), None)
        return (f.wert.split(",")[0].strip() if f and f.wert else "")

    jahr = _jahr([{"jahr": str(z.get("jahr") or "")} for z in zeilen])
    institute: dict[tuple, dict] = {}
    for e in k_alle:
        name = bank(e)
        if not name:
            continue
        haus = institute.setdefault((name, e.person), {"konten": [], "schulden": []})
        nummer = feld(e, "Konto-/Depot-Nr.")
        haus["konten"].append({
            "dokumente": list(e.dokumente),
            "nummer": nummer,
            "bezeichnung": e.titel,
            "jahr": jahr,
            "saldo": feld(e, "Total"),
            "ertrag_mit_vst": feld(e, "Ertrag mit"),
            "ertrag_ohne_vst": feld(e, "Ertrag ohne"),
        })
    for e in s_alle:
        name = bank(e)
        if not name:
            continue
        haus = institute.setdefault((name, e.person), {"konten": [], "schulden": []})
        haus["schulden"].append({
            "bezeichnung": e.titel, "jahr": jahr,
            "schuld": feld(e, "Schuld am"), "zins": feld(e, "Schuldzinsen"),
        })
    aus = [{"institut": n, "person": p, "konten": h["konten"],
            "schulden": h["schulden"], "paarung": {}}
           for (n, p), h in sorted(institute.items())]
    ohne_institut = sum(1 for e in k_alle + s_alle if not bank(e))

    # Eine Schuld ohne Zins — oder ein Zins ohne Schuld — ist unvollstaendig.
    #
    # Das passiert, wenn derselbe Glaeubiger unter zwei Schreibweisen erfasst
    # ist: die Hypothek landet beim einen Institut, der Zins beim anderen, und
    # gepaart werden kann nichts mehr. Im Auszug stuende dann eine Schuld ohne
    # Zinsabzug (260923-dua).
    # Ein Konto ohne Steuerwert ist unvollstaendig.
    #
    # Jede Zeile im Wertschriftenverzeichnis braucht einen Steuerwert per
    # 31.12. Ein Ertrag ohne Saldo entsteht, wenn eine Bank zwei Unterlagen
    # ausstellt — eine mit dem Saldo, eine mit dem Zins — und in einer davon
    # keine IBAN steht, ueber die sich beide zusammenfuehren liessen. Dann
    # steht der Ertrag bei einem Konto, das es so nicht gibt (260924-dua, vom
    # Nutzer gefunden).
    ohne_saldo = [k for h in aus for k in h["konten"]
                  if not k["saldo"]
                  and (k["ertrag_mit_vst"] or k["ertrag_ohne_vst"])]
    leer = [k for h in aus for k in h["konten"]
            if not k["saldo"] and not k["ertrag_mit_vst"]
            and not k["ertrag_ohne_vst"]]

    unpaar = [s for h in aus for s in h["schulden"]
              if not s["schuld"] or not s["zins"]]

    # Stimmt die Paarung? Der Zinssatz sagt es.
    #
    # Welcher Zins zu welcher Hypothek gehoert, steht nicht in den Daten —
    # gepaart wird der Reihe nach. Eine falsche Zuordnung faellt aber sofort
    # auf: sie ergibt einen unmoeglichen Zinssatz. Bei zwei gleich grossen
    # Hypotheken und einer kleineren mit hoeherem Zins waere ein vertauschtes
    # Paar an einem Satz von 8 % oder 0,1 % zu erkennen (260923-dua).
    #
    # Die Wegleitung verlangt den Zinssatz ohnehin im Schuldenverzeichnis
    # (S. 21) — er gehoert also sowieso ausgewiesen.
    from decimal import Decimal, InvalidOperation

    def _satz(schuld: str, zins: str):
        try:
            s_ = Decimal(str(schuld).replace("'", "").strip() or "0")
            z_ = Decimal(str(zins).replace("'", "").strip() or "0")
        except InvalidOperation:
            return None
        if s_ <= 0 or z_ <= 0:
            return None
        return float(z_ / s_ * 100)

    for h in aus:
        for sch in h["schulden"]:
            sch["zinssatz"] = _satz(sch["schuld"], sch["zins"])
    unplausibel = [s for h in aus for s in h["schulden"]
                   if s.get("zinssatz") is not None
                   and not (0.1 <= s["zinssatz"] <= 6.0)]
    # Und: zwei Institute, deren Name sich nur um einen Rechtsformzusatz
    # unterscheidet, sind vermutlich dasselbe Haus.
    def kern(name: str) -> str:
        t = name.lower()
        for zusatz in (" genossenschaft", " ag", " sa", " gmbh", " bank"):
            t = t.replace(zusatz, "")
        return t.replace(" ", "")

    kerne: dict[str, set] = {}
    for h in aus:
        kerne.setdefault(kern(h["institut"]), set()).add(h["institut"])
    doppelt = [sorted(v) for v in kerne.values() if len(v) > 1]

    # Der Kunde gehoert in den Auszug — ein Steuerauszug ohne Empfaenger ist
    # keiner. Der Name kommt aus der Familien-Konfiguration, nicht aus einer
    # Eingabe auf der Kommandozeile.
    kunde = {"vorname": "", "nachname": ""}
    try:
        from extractors.family import load_family
        familie = load_family()
        erwachsene = [m for m in (familie.members if familie else [])
                      if str(getattr(m, "role", "")).startswith("elternteil")]
        wer = (erwachsene or (familie.members if familie else []) or [None])[0]
        if wer is not None:
            kunde = {"vorname": str(getattr(wer, "first_name", "") or ""),
                     "nachname": str(getattr(wer, "last_name", "") or "")}
    except Exception:       # noqa: BLE001 - ohne Namen geht es auch
        pass

    return {"institute": aus, "ohne_institut": ohne_institut,
            "unvollstaendige_schulden": len(unpaar),
            "gleiche_institute": doppelt, "kunde": kunde,
            "unplausible_zinssaetze": len(unplausibel),
            "konten_ohne_saldo": ohne_saldo,
            "konten_leer": len(leer)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--samples", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    daten = baue(args.samples)
    ziel = args.out or args.samples.parent / "etax_eingabe.json"
    ziel.write_text(json.dumps(daten, indent=2, ensure_ascii=False),
                    encoding="utf-8")

    # Institutsnamen sind Bankbeziehungen — sie gehoeren nicht in eine
    # Ausgabe, die als teilbar beschrieben ist. Die erste Fassung druckte sie
    # unter genau dieser Ueberschrift (260923-dua).
    print("eSteuerauszug — Eingabe, nur Zahlen, dieser Teil ist teilbar")
    print(TRENNER)
    konten = sum(len(h["konten"]) for h in daten["institute"])
    schulden = sum(len(h["schulden"]) for h in daten["institute"])
    print(f"   {len(daten['institute']):3}  Institut(e) — also ebenso viele "
          f"Auszuege")
    print(f"   {konten:3}  Konto/Konten")
    print(f"   {schulden:3}  Schuld(en)")
    if daten["ohne_institut"]:
        print(f"   {daten['ohne_institut']:3}  Position(en) ohne Aussteller — "
              f"ohne Institut gibt es keinen Auszug")
    verteilung = sorted((len(h["konten"]) + len(h["schulden"]))
                        for h in daten["institute"])
    print(f"   Positionen je Institut (ohne Namen): {verteilung}")
    if daten["unvollstaendige_schulden"]:
        print(f"\n   ⚠ {daten['unvollstaendige_schulden']} Schuld(en) ohne "
              f"Zins oder Zins ohne Schuld.")
        print("     Meist sind Glaeubiger unter zwei Schreibweisen erfasst — "
              "dann\n     laesst sich nichts paaren.")
    if daten.get("konten_ohne_saldo"):
        print(f"\n   ⚠ {len(daten['konten_ohne_saldo'])} Konto/Konten mit "
              f"Ertrag, aber ohne Steuerwert per 31.12.")
        print("     Jede Zeile im Wertschriftenverzeichnis braucht einen "
              "Steuerwert.")
        print("     Meist stehen Saldo und Ertrag in zwei Unterlagen, die "
              "sich ohne")
        print("     IBAN nicht zusammenfuehren lassen.")
    if daten.get("konten_leer"):
        print(f"\n   ⚠ {daten['konten_leer']} Konto/Konten ohne jeden Wert.")
    if daten.get("unplausible_zinssaetze"):
        print(f"\n   ⚠ {daten['unplausible_zinssaetze']} Schuld(en) mit "
              f"unmoeglichem Zinssatz.")
        print("     Dann ist ein Zins der falschen Hypothek zugeordnet.")
    if daten["gleiche_institute"]:
        print(f"\n   ⚠ {len(daten['gleiche_institute'])} Institut(e) unter "
              f"zwei Schreibweisen erfasst.")
        print("     Das ergibt zwei Auszuege fuer dieselbe Bank. In der "
              "Durchsicht\n     den Aussteller vereinheitlichen.")
    print(f"\n→ {ziel}")
    print("\nWelche Institute — NICHT teilen, enthaelt Bankbeziehungen")
    print(TRENNER)
    for h in daten["institute"]:
        print(f"   {h['institut'][:32]:<32} {h.get('person') or '—':<14} "
              f"{len(h['konten'])} Konto/Konten, "
              f"{len(h['schulden'])} Schuld(en)")
    for h in daten["institute"]:
        paarung = h.get("paarung") or {}
        if paarung:
            print(f"   {h['institut'][:34]:<34} "
                  f"{paarung.get('Hypotheken', 0)} Hypothek(en), "
                  f"{paarung.get('Zinsen', 0)} Zins(en)")
    for k in daten.get("konten_ohne_saldo") or []:
        print(f"   ohne Steuerwert: {k['bezeichnung'][:52]}")
        for d in k.get("dokumente") or []:
            print(f"                    in: {d}")
    for h in daten["institute"]:
        for sch in h["schulden"]:
            if sch.get("zinssatz") is not None:
                print(f"   {sch['bezeichnung'][:40]:<40} "
                      f"Zinssatz {sch['zinssatz']:.2f} %")
    for gruppe in daten["gleiche_institute"]:
        print(f"   vermutlich dasselbe Haus: {' / '.join(gruppe)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
