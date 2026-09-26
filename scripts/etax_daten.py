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
    try:
        from scripts.build_review_html import sammle_zeilen
        return [{**z, "pdf_name": z.get("beleg")}
                for z in sammle_zeilen(samples, pdf_dir=None, mit_crops=False)
                if not z.get("gestrichen")]
    except (OSError, FileNotFoundError, ValueError):
        from extractors.ablage import lade_geprueft
        return lade_geprueft(samples)


def baue(samples: Path) -> dict:
    """Konten und Schulden je Institut — die Eingabe für den Erzeuger."""

    # Dieselben Zeilen wie die Uebertragungstabelle, nicht nur die Ablage.
    #
    # Die Ablage fuehrt die gepruefte Grundmenge; was der Mensch danach
    # ergaenzt, steht in der Tabelle, aber nicht dort. Aus der Ablage allein
    # gebaut fehlten sieben Konten im Auszug — und niemand haette es gesehen,
    # weil das PDF trotzdem richtig aussieht (260924-dua).
    zeilen = _zeilen_der_tabelle(samples)

    # Die IBAN ist die einzige verlaessliche Kontoidentitaet.
    #
    # Vorher wurden Konten ueber eine Ziffernfolge im Dateinamen
    # zusammengefasst — die kann auch ein Datum sein, und dann verschmelzen
    # zwei fremde Konten. Steht im Dokument genau eine IBAN, ist die Sache
    # eindeutig; stehen mehrere oder keine, bleibt es beim Dokument. Nichts
    # wird geraten (260924-dua, vom Nutzer angestossen).
    try:
        from scripts.etax_iban import sammle as _ibans
        je_dokument = _ibans(samples)
    except Exception:      # noqa: BLE001 - ohne IBANs geht es auch
        je_dokument = {}
    iban_von = {d: i[0] for d, i in je_dokument.items() if len(i) == 1}

    # Je Institut, darin je Konto. Der Aussteller ist das Institut; fehlt er,
    # laesst sich kein Auszug bauen — ein eSteuerauszug ohne Absender gibt es
    # nicht.
    institute: dict[str, dict] = {}
    ohne_institut = 0
    for z in zeilen:
        aussteller = str(z.get("aussteller") or "").strip()
        zielwert = str(z.get("zielwert") or "").strip()
        if zielwert not in (*SALDO, ERTRAG_MIT_VST, ERTRAG_OHNE_VST,
                            SCHULD, SCHULDZINS):
            continue
        if not aussteller:
            ohne_institut += 1
            continue
        dok = str(z.get("pdf_name") or "")
        # Ein Auszug je Bank UND Kunde.
        #
        # Die Spezifikation (eCH-0196, SSK) ist eindeutig: der Auszug listet
        # alles „fuer deine Kundenbeziehung" — alle Konten, Depots und
        # Schulden dieser einen Beziehung, in einem Dokument. Eine Beziehung
        # gehoert einem Kunden. Zwei Ehegatten bei derselben Bank sind zwei
        # Beziehungen, also zwei Auszuege.
        #
        # Gruppiert wurde nur nach Institut. Dadurch standen die Konten beider
        # in einem Auszug durcheinander (260924-dua, vom Nutzer gemeldet).
        person = str(z.get("person") or "").strip()
        haus = institute.setdefault(
            (aussteller, person),
            {"institut": aussteller, "person": person,
             "konten": {}, "schulden": {}})
        topf = "schulden" if zielwert in (SCHULD, SCHULDZINS) else "konten"
        # Konten je Dokument bzw. Kontonummer — Schulden je INSTITUT.
        #
        # Schuld und Zins derselben Hypothek stehen oft in zwei Dokumenten
        # (Saldobestaetigung und Zinsbestaetigung). Je Dokument gruppiert
        # landen sie in getrennten Toepfen und paaren nie; im Auszug stuende
        # dann jede Schuld ohne Zinsabzug (260923-dua).
        # Konten je DOKUMENT, nicht je geratener Kontonummer.
        #
        # `konto_key` nimmt jede Ziffernfolge ab sechs Stellen aus dem
        # Dateinamen — auch ein Datum. Fuer die Tabelle ist ein falsches
        # Zusammenfassen unschoen; im Auszug verschmelzen dann zwei Konten zu
        # einem, und die Ertraege stimmen nicht mehr. Hier zaehlt Trennen mehr
        # als Zusammenfassen: eine Zeile zu viel sieht man, eine verschmolzene
        # nicht (260924-dua).
        schluessel = ("alle" if topf == "schulden"
                      else (iban_von.get(dok) or dok))
        # Die Nummer ist die IBAN — oder gar keine. Eine geratene waere
        # schlechter als eine fehlende: das Formular verlangt „bei Konto inkl.
        # Nummer", und eine falsche Nummer ist eine falsche Angabe.
        eintrag = haus[topf].setdefault(
            schluessel, {"nummer": iban_von.get(dok, ""), "zeilen": []})
        eintrag["zeilen"].append({
            "zielwert": zielwert,
            "pos": int(z.get("pos") or 1),
            "betrag": str(z.get("betrag") or "").strip(),
            "jahr": str(z.get("jahr") or "").strip(),
            "person": str(z.get("person") or "").strip(),
            "unterscheidung": str(z.get("unterscheidung") or "").strip(),
            "dokument": dok,
        })

    aus = []
    for name, _person in sorted(institute):
        haus = institute[(name, _person)]
        konten, schulden = [], []
        for schluessel in sorted(haus["konten"]):
            k = haus["konten"][schluessel]
            werte = {z["zielwert"]: z["betrag"] for z in k["zeilen"]}
            konten.append({
                "nummer": k["nummer"],
                "bezeichnung": " · ".join(
                    t for t in (name, k["nummer"]) if t),
                "jahr": _jahr(k["zeilen"]),
                "saldo": next((werte[s] for s in SALDO if s in werte), ""),
                "ertrag_mit_vst": werte.get(ERTRAG_MIT_VST, ""),
                "ertrag_ohne_vst": werte.get(ERTRAG_OHNE_VST, ""),
            })
        for schluessel in sorted(haus["schulden"]):
            k = haus["schulden"][schluessel]
            # Schuld und Zins paaren — ohne dass dabei etwas verlorengeht.
            #
            # Zwei Irrwege liegen hinter dieser Stelle. Nach der
            # Unterscheidung gruppiert fielen drei Hypotheken auf zwei
            # zusammen, sobald zwei keinen Zusatz trugen. Nach der
            # Positionsnummer gruppiert ueberschrieb die zweite Hypothek mit
            # derselben Nummer die erste — auch dann fehlte eine, lautlos
            # (260923-dua).
            #
            # Jetzt: beide Arten als geordnete Listen, dann der Reihe nach
            # gepaart. Nichts wird ueberschrieben, und sind es unterschiedlich
            # viele, bleibt der Ueberhang als eigener Eintrag stehen und
            # faellt in der Pruefung auf.
            def _sortiert(zielwert: str, k=k) -> list[dict]:
                # `k` ausdruecklich binden: eine Closure ueber die
                # Schleifenvariable ist hier zwar harmlos, weil sofort
                # aufgerufen — aber nur heute.
                return sorted((z for z in k["zeilen"]
                               if z["zielwert"] == zielwert),
                              key=lambda z: (int(z["pos"]), z["betrag"]))

            hypotheken = _sortiert(SCHULD)
            zinsen = _sortiert(SCHULDZINS)
            k["paarung"] = {"Hypotheken": len(hypotheken),
                            "Zinsen": len(zinsen)}
            for i in range(max(len(hypotheken), len(zinsen))):
                h = hypotheken[i] if i < len(hypotheken) else None
                zi = zinsen[i] if i < len(zinsen) else None
                zusatz = next((x["unterscheidung"] for x in (h, zi)
                               if x and x["unterscheidung"]), "")
                if not zusatz and max(len(hypotheken), len(zinsen)) > 1:
                    zusatz = f"Hypothek {i + 1}"
                schulden.append({
                    "bezeichnung": " · ".join(t for t in (name, zusatz) if t),
                    "jahr": _jahr(k["zeilen"]),
                    "schuld": (h or {}).get("betrag", ""),
                    "zins": (zi or {}).get("betrag", ""),
                })
        if konten or schulden:
            paarung = {}
            for kk in haus["schulden"].values():
                paarung.update(kk.get("paarung") or {})
            aus.append({"institut": name, "person": _person,
                        "konten": konten, "schulden": schulden,
                        "paarung": paarung})

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
