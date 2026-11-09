#!/usr/bin/env python3
"""Aus der Ablage einen eSteuerauszug nach eCH-0196 bauen — PDF mit Barcode.

**Läuft in einem eigenen venv** (`.venv-etax`), nicht im venv dieses Projekts:
`opensteuerauszug` bringt Git-Forks von `ibflex` und `pdf417` mit. Die Brücke
ist die JSON-Datei aus `scripts/etax_daten.py`.

Was hier passiert: die geprüften Konten und Schulden werden auf das
eCH-0196-Modell von `opensteuerauszug` (MIT, github.com/vroonhof/
opensteuerauszug) abgebildet, die Summen von dessen `TotalCalculator`
gerechnet, und daraus entsteht je Institut ein PDF mit PDF417-Barcode.
ZHprivateTax importiert ein solches PDF und füllt das
Wertschriftenverzeichnis selbst aus.

**Ein Auszug je Institut.** So kommen sie auch von den Banken.

**Was das PDF nicht ist:** kein Bankbeleg. Es trägt die selbst geprüften
Werte, nicht die Bestätigung eines Instituts. Es ersetzt die Belege nicht,
sondern spart das Abtippen. Die Werte müssen vorher stimmen — hier stehen
ausschliesslich geprüfte.

**Fail-closed (261025-kme, B5a):** ein Institut mit einem Konto ohne
Steuerwert, einer Schuld ohne Zins oder ohne Betrag oder einem unmöglichen
Zinssatz wird nicht gebaut — der Auszug unterschlüge sonst Vermögen oder
einen Abzug. Es wird mit Kürzel (I01, I02 …) und Grund genannt, Exit 1. Mit
``--trotzdem`` entsteht es doch, mit einer Textdatei daneben; Exit bleibt 1.
Alte PDFs kommen in ``<ziel>/verlauf/<Zeit>/`` statt in den Papierkorb
(Bestandsschutz 2025).

Aufruf::

    make etax-bauen
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

TRENNER = "─" * 70
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Codes der Eingabe (scripts/etax_daten.py UNVOLLSTAENDIG) als Text. Hier
# wiederholt, weil dieses Skript in .venv-etax läuft und etax_daten das
# Projekt-venv braucht.
GRUENDE = {
    "konto_ohne_saldo": "Konto ohne Steuerwert",
    "schuld_ohne_zins": "Schuld ohne Zins",
    "zins_ohne_schuld": "Zins ohne Schuld",
    "zinssatz_unplausibel": "Zinssatz unplausibel",
}


class KundeFehlt(ValueError):
    """Ohne Kundennamen ist ein Steuerauszug keiner."""


def _kunde(vorname: str, nachname: str, daten: dict) -> dict:
    """VORNAME/NACHNAME vor den Daten; sonst der Kunde aus der Eingabe.

    Bisher gewann ``daten["kunde"]`` immer — auch mit leeren Strings, und die
    Argumente wirkten nie (261025-kme, B5b).
    """
    vorname, nachname = (vorname or "").strip(), (nachname or "").strip()
    if vorname and nachname:
        return {"vorname": vorname, "nachname": nachname}
    if vorname or nachname:
        raise KundeFehlt("Vor- und Nachname zusammen angeben (VORNAME=… NACHNAME=…)")
    k = daten.get("kunde") or {}
    if str(k.get("vorname") or "").strip() and str(k.get("nachname") or "").strip():
        return {"vorname": str(k["vorname"]).strip(), "nachname": str(k["nachname"]).strip()}
    raise KundeFehlt("Kein Kundenname — VORNAME=… NACHNAME=… angeben oder in der "
                     "Familien-Konfiguration ein Elternteil mit Vor- und Nachnamen führen")


def _satz_unplausibel(sch: dict) -> bool:
    satz = sch.get("zinssatz")
    return isinstance(satz, (int, float)) and not (0.1 <= satz <= 6.0)


def _unvollstaendig(haus: dict) -> list[str]:
    """Was das Institut unvollständig macht — Codes aus GRUENDE.

    Die Codes der Eingabe, vereinigt mit einer eigenen Prüfung: so wird auch
    eine Eingabe von vor 261025-kme (ohne ``unvollstaendig``) geprüft.
    """
    codes = list(haus.get("unvollstaendig") or [])
    if any(not str(k.get("saldo") or "").strip() for k in haus.get("konten") or []):
        codes.append("konto_ohne_saldo")
    for sch in haus.get("schulden") or []:
        if not str(sch.get("zins") or "").strip():
            codes.append("schuld_ohne_zins")
        if not str(sch.get("schuld") or "").strip():
            codes.append("zins_ohne_schuld")
        if _satz_unplausibel(sch):
            codes.append("zinssatz_unplausibel")
    return list(dict.fromkeys(codes))


def _widerspruch(daten: dict) -> list[str]:
    """Melden die Kopfzähler mehr, als den Instituten zuzuordnen ist?"""
    institute = daten.get("institute") or []
    konten = [k for h in institute for k in h.get("konten") or []]
    schulden = [s for h in institute for s in h.get("schulden") or []]
    ohne_saldo = sum(1 for k in konten if not str(k.get("saldo") or "").strip())
    unpaar = sum(1 for s in schulden
                 if not str(s.get("schuld") or "").strip() or not str(s.get("zins") or "").strip())
    unplausibel = sum(1 for s in schulden if _satz_unplausibel(s))
    aus = []
    if len(daten.get("konten_ohne_saldo") or []) > ohne_saldo:
        aus.append("Konten ohne Steuerwert")
    if int(daten.get("unvollstaendige_schulden") or 0) > unpaar:
        aus.append("unvollständige Schulden")
    if int(daten.get("unplausible_zinssaetze") or 0) > unplausibel:
        aus.append("unplausible Zinssätze")
    return aus


def _dateiname(haus: dict, jahr: int) -> str:
    name = "".join(c if c.isalnum() or c in "-_" else "-"
                   for c in f"{haus['institut']}-{haus.get('person') or ''}")[:52]
    return f"eSteuerauszug-{name}-{jahr}.pdf"
# Das amtliche Schema, Importe auf die lokalen Dateien umgebogen — die
# Pruefung braucht kein Netz.
SCHEMA = ROOT / "specs" / "eCH-0196" / "eCH-0196-2-2.xsd"

# Verrechnungssteuer auf Ertraege der Kolonne A — der Rueckerstattungsanspruch.
VST_SATZ = Decimal("0.35")


def _zahl(text: str) -> Decimal | None:
    t = str(text or "").strip().replace("'", "").replace(" ", "")
    if not t:
        return None
    try:
        return Decimal(t)
    except InvalidOperation:
        return None


def _stichtag(jahr: str) -> date:
    """Der 31.12. des Steuerjahres — der Stichtag jedes Steuerwerts."""
    try:
        return date(int(jahr), 12, 31)
    except (TypeError, ValueError):
        return date(date.today().year - 1, 12, 31)


def _rolle_kurz(person: str) -> str:
    """``elternteil_1`` → ``E1``: kurz, stabil, ohne Namen."""
    t = _ascii(person or "")
    if not t:
        return ""
    ziffern = "".join(c for c in t if c.isdigit())
    return (t[0].upper() + ziffern)[:4]


def baue_auszug(haus: dict, steuerjahr: int, kunde: dict,
                kanton: str = "ZH"):
    """Ein ``TaxStatement`` für ein Institut.

    Muss gegen ``eCH-0196-2-2.xsd`` bestehen — sonst meldet ZHprivateTax
    beim Import nur „Der Barcode enthält keine gültigen Daten" (261024-jga).
    Die Pflichtattribute, die hier fehlten: ``canton``, ``clientNumber``,
    ``bankAccountCountry`` und je Zahlung ``grossRevenueA``,
    ``grossRevenueB`` und ``withHoldingTaxClaim``. Dazu stand
    ``minorVersion`` auf 2 statt 22. Die Bibliothek setzt das alles in
    ihrem Bereinigungsschritt, den dieser Weg nicht durchlaeuft.
    """
    from opensteuerauszug.model.ech0196 import (
        BankAccount, BankAccountName, BankAccountNumber, BankAccountPayment,
        BankAccountTaxValue, Client, ClientNumber, Institution,
        LiabilityAccount,
        LiabilityAccountPayment, LiabilityAccountTaxValue,
        ListOfBankAccounts, ListOfLiabilities, TaxStatement)

    von, bis = date(steuerjahr, 1, 1), date(steuerjahr, 12, 31)

    konten = []
    for k in haus["konten"]:
        stichtag = _stichtag(k["jahr"] or str(steuerjahr))
        saldo = _zahl(k["saldo"])
        zahlungen = []
        # Das Schema verlangt je Zahlung beide Kolonnen und den Anspruch —
        # die nicht betroffene Kolonne steht als 0 da, nicht als Luecke.
        for feld, kolonne_a in (("ertrag_mit_vst", True),
                                ("ertrag_ohne_vst", False)):
            betrag = _zahl(k.get(feld, ""))
            if betrag is None:
                continue
            zahlungen.append(BankAccountPayment(
                paymentDate=stichtag, amountCurrency="CHF", amount=betrag,
                grossRevenueA=betrag if kolonne_a else Decimal(0),
                grossRevenueB=Decimal(0) if kolonne_a else betrag,
                withHoldingTaxClaim=(
                    (betrag * VST_SATZ).quantize(Decimal("0.01"), ROUND_HALF_UP)
                    if kolonne_a else Decimal(0))))
        nummer = str(k["nummer"] or "")
        # Die eigenen Typen des Modells, nicht blosse Strings — und `payment`
        # ist eine Liste ohne Default: leer heisst `[]`, nicht `None`.
        konten.append(BankAccount(
            bankAccountName=BankAccountName(k["bezeichnung"][:40]),
            # Eine IBAN gehoert ins `iban`-Feld, nicht in `bankAccountNumber`
            # — das Format kennt beide, und die Steuersoftware liest das
            # richtige.
            iban=(k["nummer"] if str(k["nummer"] or "").startswith(("CH", "LI"))
                  else None),
            bankAccountNumber=(
                BankAccountNumber(k["nummer"][:32])
                if k["nummer"] and not str(k["nummer"]).startswith(("CH", "LI"))
                else None),
            bankAccountCountry=(nummer[:2] if nummer.startswith(("CH", "LI"))
                                else "CH"),
            bankAccountCurrency="CHF",
            taxValue=(BankAccountTaxValue(
                referenceDate=stichtag, balanceCurrency="CHF",
                balance=saldo, value=saldo) if saldo is not None else None),
            payment=zahlungen))

    schulden = []
    for sch in haus["schulden"]:
        stichtag = _stichtag(sch["jahr"] or str(steuerjahr))
        betrag = _zahl(sch["schuld"]) or Decimal(0)
        zins = _zahl(sch["zins"])
        # Der Schuldsaldo als eigenes Element — mit Stichtag.
        #
        # Hier stand nur `totalTaxValue`, also die Summe. Eine Schuld ohne
        # `taxValue` hat im Auszug keinen Saldo per 31.12., und der Zins haengt
        # an nichts (260924-dua). Bei den Konten war es von Anfang an richtig;
        # bei den Schulden habe ich es vergessen.
        schulden.append(LiabilityAccount(
            bankAccountName=BankAccountName(sch["bezeichnung"][:40]),
            bankAccountCountry="CH",
            bankAccountCurrency="CHF",
            taxValue=LiabilityAccountTaxValue(
                referenceDate=stichtag, balanceCurrency="CHF",
                balance=betrag, value=betrag),
            totalTaxValue=betrag,
            totalGrossRevenueB=(zins or Decimal(0)),
            payment=([LiabilityAccountPayment(
                paymentDate=stichtag, amountCurrency="CHF", amount=zins,
                grossRevenueB=zins)] if zins is not None else [])))

    # Die `id` ist zweierlei zugleich, und beides muss stimmen.
    #
    # Sie ist der Dateiname im Barcode (`file_name = tax_statement.id`) — ohne
    # sie schlaegt dort eine Pruefung an, die nach einer veralteten Bibliothek
    # aussieht, aber schlicht `None` nicht vertraegt. Und aus ihrem Anfang
    # liest die Bibliothek die fuenfstellige Organisationsnummer; ein
    # sprechender Text ergab „org_nr 'euer-'".
    #
    # Deshalb: fuenf Ziffern voran, dann ASCII. Die Ziffern rechnet
    # `compute_org_nr` selbst aus dem Institutsnamen — ein Hash im
    # 19000er-Bereich, der fuer selbst erzeugte Auszuege vorgesehen ist.
    # Die Kundennummer ist Pflicht. Eine echte gibt es nicht; die Rolle haelt
    # zwei Kundenbeziehungen derselben Bank auseinander, ohne einen Namen.
    kundennummer = _rolle_kurz(haus.get("person") or "") or "NOIDENTI"
    return TaxStatement(
        minorVersion=22,
        canton=kanton,
        creationDate=date.today(),
        taxPeriod=steuerjahr, periodFrom=von, periodTo=bis, country="CH",
        institution=Institution(name=haus["institut"][:40]),
        client=[Client(clientNumber=ClientNumber(kundennummer),
                       firstName=kunde.get("vorname") or None,
                       lastName=kunde.get("nachname") or None)],
        listOfBankAccounts=(ListOfBankAccounts(bankAccount=konten)
                            if konten else None),
        listOfLiabilities=(ListOfLiabilities(liabilityAccount=schulden)
                           if schulden else None))


def _ascii(text: str) -> str:
    """Nur Buchstaben und Ziffern — der Barcode vertraegt keine Umlaute."""
    import unicodedata

    ersetzt = (text.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue")
                   .replace("Ä", "Ae").replace("Ö", "Oe").replace("Ü", "Ue")
                   .replace("ß", "ss"))
    zerlegt = unicodedata.normalize("NFKD", ersetzt)
    return "".join(c for c in zerlegt if c.isalnum() and c.isascii())


def setze_kennung(auszug, steuerjahr: int) -> None:
    """Die Kennung nach dem Format, das die Bibliothek erwartet.

    Aus `render.py`:

        CC(2 Zeichen) NNNNN(5 Ziffern org_nr) CCCCCCCCCCCCCC(14 Zeichen)
        YYYYMMDD(8) SS(2)

    Die Organisationsnummer wird als ``id[2:7]`` gelesen — steht sie
    woanders, nimmt die Bibliothek fuenf beliebige Zeichen dafuer. Genau das
    ergab die Meldung „org_nr '172Zu' must be a 5-digit string".

    Das Datum ist der **Stichtag** (``periodTo``), nicht der Tag der
    Erstellung — so verlangt es der Pruefer der Steuersoftware. Und die
    Kundenrolle gehoert in die Stammnummer: zwei Auszuege derselben Bank
    trugen sonst dieselbe Kennung, und der zweite Import haelt sich fuer
    den ersten (261024-jga).
    """
    from opensteuerauszug.core.organisation import compute_org_nr

    orgnr = compute_org_nr(auszug)
    rolle = auszug.client[0].clientNumber if auszug.client else ""
    rolle = "" if rolle == "NOIDENTI" else _ascii(rolle)
    name = (_ascii(auszug.institution.name or "") or "OHNENAME")
    stamm = (name[:14 - len(rolle)] + rolle).ljust(14, "X")
    auszug.id = f"CH{orgnr}{stamm}{auszug.periodTo:%Y%m%d}01"


def schemafehler(auszug) -> list[str]:
    """Was verletzt das XML im Barcode am Schema eCH-0196?

    Leer heisst: importierbar. Vorher sah ein Auszug mit Barcode richtig
    aus, und erst ZHprivateTax sagte beim Import „keine gültigen Daten" —
    ohne zu sagen, welche (261024-jga). Jetzt prueft der Bau selbst und
    schreibt ein ungueltiges PDF gar nicht erst.
    """
    from lxml import etree

    schema = etree.XMLSchema(etree.parse(str(SCHEMA)))
    if schema.validate(etree.fromstring(auszug.to_xml_bytes())):
        return []
    # Nur die Meldung, ohne Werte: sie nennt Element und Attribut.
    return [f.message.split("}")[-1][:160] for f in schema.error_log]


def _nachweis(pfad: Path) -> dict:
    """Ist wirklich ein Barcode drin?

    Geprueft wird nicht die Dateigroesse, sondern ob eine Seite ein Bild
    traegt: der PDF417-Code wird als Bild eingebettet. Ein PDF ohne ihn sieht
    richtig aus und ist doch nutzlos — die Steuersoftware kann nichts damit
    anfangen.
    """
    from pypdf import PdfReader

    leser = PdfReader(str(pfad))
    bilder = 0
    for seite in leser.pages:
        try:
            bilder += len(seite.images)
        except Exception:      # noqa: BLE001 - eine kaputte Seite zaehlt 0
            pass
    return {"seiten": len(leser.pages), "barcode": bilder > 0,
            "bytes": pfad.stat().st_size}


def _echter_name(text: str) -> bool:
    """Ein Name hat Buchstaben — „…" oder „-" ist ein Platzhalter."""
    return bool(re.search(r"[^\W\d_]", text or ""))


def _kunde_fuer(haus: dict, vorname: str, nachname: str, daten: dict) -> dict:
    """Der Kunde DIESES Auszugs: die Person, der das Institut gehört.

    Die Auszüge gehören zwei Personen. Bisher bekam jeder denselben Namen —
    den von VORNAME/NACHNAME oder, ohne sie, den des ersten Elternteils;
    einmal stand in allen elf „…" (der Anwender 261027). Jetzt zuerst der Name der
    Rolle aus der Familien-Konfiguration (``daten["kunden"]``); VORNAME/
    NACHNAME nur, wo die Rolle keinen hat. Platzhalter werden abgewiesen.
    """
    rolle = str(haus.get("person") or "")
    k = (daten.get("kunden") or {}).get(rolle) or {}
    v, n = str(k.get("vorname") or "").strip(), str(k.get("nachname") or "").strip()
    if _echter_name(v) and _echter_name(n):
        return {"vorname": v, "nachname": n}
    v, n = (vorname or "").strip(), (nachname or "").strip()
    if v or n:
        if not (_echter_name(v) and _echter_name(n)):
            raise KundeFehlt("VORNAME/NACHNAME sind Platzhalter oder unvollständig — "
                             "echte Namen angeben oder weglassen")
        return {"vorname": v, "nachname": n}
    personen = {str(h.get("person") or "") for h in daten.get("institute") or []}
    if len(personen) > 1:
        raise KundeFehlt(f"Für die Rolle {rolle or '(ohne)'} steht kein Name in der "
                         "Familien-Konfiguration — die Auszüge gehören mehreren "
                         "Personen, ein gemeinsamer Name wäre falsch")
    return _kunde("", "", daten)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--eingabe", type=Path, required=True)
    ap.add_argument("--ziel", type=Path, required=True)
    ap.add_argument("--jahr", type=int, required=True)
    ap.add_argument("--vorname", default="")
    ap.add_argument("--nachname", default="")
    ap.add_argument("--nur", default="",
                    help="Nur Institute, deren Name diesen Text enthaelt")
    ap.add_argument("--trotzdem", action="store_true",
                    help="auch unvollständige Institute bauen (mit Textdatei daneben); "
                         "Exit bleibt 1")
    args = ap.parse_args()

    daten = json.loads(args.eingabe.read_text(encoding="utf-8"))

    print("eSteuerauszug bauen — nur Zahlen, dieser Teil ist teilbar")
    print(TRENNER)
    # Zuerst der Kunde — bevor das Ziel berührt wird (261025-kme, B5b).
    # Je Auszug der Name seiner Person (261027) — alle vorab, damit ein
    # fehlender Name nichts halb baut.
    try:
        kunden = {id(h): _kunde_fuer(h, args.vorname, args.nachname, daten)
                  for h in daten.get("institute") or []
                  if not args.nur or args.nur.lower() in h["institut"].lower()}
    except KundeFehlt as fehler:
        print(f"   ABBRUCH: {fehler}")
        return 1
    print(f"   {len({tuple(k.values()) for k in kunden.values()}):3}  Person(en) als "
          "Kunde — je Auszug die Person des Instituts")
    widerspruch = _widerspruch(daten)
    if widerspruch:
        print("   ABBRUCH: Eingabe widersprüchlich — die Zähler melden "
              + ", ".join(widerspruch) + ", die keinem Institut zuzuordnen sind. "
              "make etax-daten neu laufen lassen.")
        return 1

    # Kürzel in der Reihenfolge der Eingabe: stabil, ohne Namen.
    alle = list(daten.get("institute") or [])
    kuerzel = {id(h): f"I{i:02d}" for i, h in enumerate(alle, start=1)}
    haeuser = [h for h in alle
               if not args.nur or args.nur.lower() in h["institut"].lower()]

    # Alte Auszüge in den Verlauf, nicht löschen (Bestandsschutz 2025).
    #
    # Der Ordner sammelte Auszuege mehrerer Laeufe an — neun PDFs fuer sieben
    # Institute; beim Oeffnen erwischt man die falsche Datei (260924-dua).
    # Bei einem gezielten Lauf (--nur) nur die der betroffenen Institute
    # (260929-dua).
    from scripts.verlauf import in_verlauf_verschieben
    args.ziel.mkdir(parents=True, exist_ok=True)
    if args.nur:
        stamm = {_dateiname(h, args.jahr) for h in haeuser}
        alte = [p for p in args.ziel.glob("*.pdf") if p.name in stamm]
    else:
        alte = sorted(args.ziel.glob("*.pdf"))
    alte += [p for p in args.ziel.glob("*.unvollstaendig.txt")
             if p.name[:-len(".unvollstaendig.txt")] in {a.name for a in alte}]
    if alte:
        verlauf = args.ziel / "verlauf" / datetime.now().strftime("%Y%m%d-%H%M%S")
        for alt in alte:
            in_verlauf_verschieben(alt, verlauf)
        print(f"   {len(alte):3}  alte Datei(en) in den Verlauf verschoben")

    zurueck = [(h, _unvollstaendig(h)) for h in haeuser]
    zurueck = [(h, c) for h, c in zurueck if c]
    zu_bauen = [h for h in haeuser
                if args.trotzdem or not any(h is z for z, _ in zurueck)]
    if zurueck:
        teile = ", ".join(f"{kuerzel[id(h)]} " + ", ".join(GRUENDE.get(c, c) for c in codes)
                          for h, codes in zurueck)
        print(f"   {len(zurueck):3}  Institut(e) zurückgehalten (unvollständig): {teile}"
              + (" — mit --trotzdem dennoch gebaut" if args.trotzdem else ""))

    gebaut, gescheitert, nachweise = 0, [], []
    if zu_bauen:
        from opensteuerauszug.calculate.total import TotalCalculator
        from opensteuerauszug.render.render import render_tax_statement

        codes_von = {id(h): c for h, c in zurueck}
        for haus in zu_bauen:
            try:
                auszug = baue_auszug(haus, args.jahr, kunden[id(haus)])
                setze_kennung(auszug, args.jahr)
                TotalCalculator().calculate(auszug)
                fehler_xsd = schemafehler(auszug)
                if fehler_xsd:
                    raise ValueError("Schema eCH-0196: " + "; ".join(fehler_xsd))
                # Institut UND Kunde im Dateinamen — sonst ueberschreiben sich
                # die Auszuege zweier Kundenbeziehungen derselben Bank.
                pfad = render_tax_statement(auszug, args.ziel / _dateiname(haus, args.jahr))
                gebaut += 1
                nachweise.append(_nachweis(pfad))
                if id(haus) in codes_von:
                    # Daneben, ohne Beträge: was an diesem Auszug fehlt.
                    Path(f"{pfad}.unvollstaendig.txt").write_text(
                        f"{kuerzel[id(haus)]}: unvollständig — "
                        + ", ".join(GRUENDE.get(c, c) for c in codes_von[id(haus)])
                        + "\nMit --trotzdem gebaut; vor dem Import prüfen.\n",
                        encoding="utf-8")
            except Exception as fehler:      # noqa: BLE001 - Bericht statt Absturz
                gescheitert.append((haus, f"{type(fehler).__name__}: {fehler}"))

    print(f"   {gebaut:3}  Auszug/Auszuege gebaut")
    print(f"   {len(gescheitert):3}  gescheitert"
          + (" (" + ", ".join(kuerzel[id(h)] for h, _ in gescheitert) + ")"
             if gescheitert else ""))
    if nachweise:
        seiten = sum(n["seiten"] for n in nachweise)
        mit_barcode = sum(1 for n in nachweise if n["barcode"])
        print(f"   {seiten:3}  Seiten insgesamt")
        print(f"   {mit_barcode:3}  davon Auszuege mit Barcode — ohne ihn "
              f"waere das PDF nicht importierbar")
    # Wie viele Auszuege jetzt im Ordner liegen — nicht nur, wie viele
    # dieser Lauf gebaut hat. Bei einem gezielten Nachbau ist genau das die
    # Frage: sind die uebrigen noch da? (260929-dua)
    print(f"   {len(list(args.ziel.glob('*.pdf'))):3}  PDF(s) liegen jetzt "
          f"im Ordner")
    if not zu_bauen:
        print("   Nichts zu bauen.")
    print(f"\n→ {args.ziel}")
    if gescheitert or zurueck:
        print("\nWelche und warum — NICHT teilen, enthaelt Bankbeziehungen")
        print(TRENNER)
        for haus, codes in zurueck:
            print(f"   {kuerzel[id(haus)]}  {haus['institut']:<40} "
                  + ", ".join(GRUENDE.get(c, c) for c in codes))
        for haus, grund in gescheitert:
            print(f"   {kuerzel[id(haus)]}  {haus['institut']:<40} {grund[:110]}")
    return 0 if gebaut and not gescheitert and not zurueck else 1


if __name__ == "__main__":
    raise SystemExit(main())
