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

Aufruf::

    make etax-bauen
"""
from __future__ import annotations

import argparse
import json
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

TRENNER = "─" * 70


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


def baue_auszug(haus: dict, steuerjahr: int, kunde: dict):
    """Ein ``TaxStatement`` für ein Institut."""
    from opensteuerauszug.model.ech0196 import (
        BankAccount, BankAccountName, BankAccountNumber, BankAccountPayment,
        BankAccountTaxValue, Client, Institution, LiabilityAccount,
        LiabilityAccountPayment, LiabilityAccountTaxValue,
        ListOfBankAccounts, ListOfLiabilities, TaxStatement)

    von, bis = date(steuerjahr, 1, 1), date(steuerjahr, 12, 31)

    konten = []
    for k in haus["konten"]:
        stichtag = _stichtag(k["jahr"] or str(steuerjahr))
        saldo = _zahl(k["saldo"])
        zahlungen = []
        for feld, spalte in (("ertrag_mit_vst", "grossRevenueA"),
                             ("ertrag_ohne_vst", "grossRevenueB")):
            betrag = _zahl(k.get(feld, ""))
            if betrag is None:
                continue
            zahlungen.append(BankAccountPayment(
                paymentDate=stichtag, amountCurrency="CHF", amount=betrag,
                **{spalte: betrag}))
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
    return TaxStatement(
        minorVersion=2,
        creationDate=date.today(),
        taxPeriod=steuerjahr, periodFrom=von, periodTo=bis, country="CH",
        institution=Institution(name=haus["institut"][:40]),
        client=[Client(firstName=kunde.get("vorname") or None,
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
    """
    from opensteuerauszug.core.organisation import compute_org_nr

    orgnr = compute_org_nr(auszug)
    name = (_ascii(auszug.institution.name or "") or "OHNENAME")[:14]
    auszug.id = (f"CH{orgnr}{name.ljust(14, 'X')}"
                 f"{auszug.creationDate:%Y%m%d}01")


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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--eingabe", type=Path, required=True)
    ap.add_argument("--ziel", type=Path, required=True)
    ap.add_argument("--jahr", type=int, required=True)
    ap.add_argument("--vorname", default="")
    ap.add_argument("--nachname", default="")
    ap.add_argument("--nur", default="",
                    help="Nur Institute, deren Name diesen Text enthaelt")
    args = ap.parse_args()

    from opensteuerauszug.calculate.total import TotalCalculator
    from opensteuerauszug.render.render import render_tax_statement

    daten = json.loads(args.eingabe.read_text(encoding="utf-8"))
    args.ziel.mkdir(parents=True, exist_ok=True)
    # Erst leeren.
    #
    # Der Ordner sammelte Auszuege mehrerer Laeufe an — neun PDFs fuer sieben
    # Institute. Neben den richtigen lagen also die eines frueheren, fehler-
    # haften Laufs, und beim Oeffnen erwischt man die falsche Datei. Genau das
    # ist passiert (260924-dua).
    for alt_pdf in args.ziel.glob("*.pdf"):
        alt_pdf.unlink()
    kunde = daten.get("kunde") or {"vorname": args.vorname,
                                   "nachname": args.nachname}

    print("eSteuerauszug bauen — nur Zahlen, dieser Teil ist teilbar")
    print(TRENNER)
    gebaut, gescheitert, nachweise = 0, [], []
    haeuser = [h for h in daten["institute"]
               if not args.nur or args.nur.lower() in h["institut"].lower()]
    for haus in haeuser:
        try:
            auszug = baue_auszug(haus, args.jahr, kunde)
            setze_kennung(auszug, args.jahr)
            TotalCalculator().calculate(auszug)
            # Institut UND Kunde im Dateinamen — sonst ueberschreiben sich
            # die Auszuege zweier Kundenbeziehungen derselben Bank.
            name = "".join(c if c.isalnum() or c in "-_" else "-"
                           for c in f"{haus['institut']}-"
                                    f"{haus.get('person') or ''}")[:52]
            pfad = render_tax_statement(
                auszug, args.ziel / f"eSteuerauszug-{name}-{args.jahr}.pdf")
            gebaut += 1
            nachweise.append(_nachweis(pfad))
        except Exception as fehler:      # noqa: BLE001 - Bericht statt Absturz
            gescheitert.append((haus["institut"], f"{type(fehler).__name__}: "
                                                  f"{fehler}"))

    print(f"   {gebaut:3}  Auszug/Auszuege gebaut")
    print(f"   {len(gescheitert):3}  gescheitert")
    if nachweise:
        seiten = sum(n["seiten"] for n in nachweise)
        mit_barcode = sum(1 for n in nachweise if n["barcode"])
        print(f"   {seiten:3}  Seiten insgesamt")
        print(f"   {mit_barcode:3}  davon Auszuege mit Barcode — ohne ihn "
              f"waere das PDF nicht importierbar")
    print(f"\n→ {args.ziel}")
    if gescheitert:
        print("\nWelche und warum — NICHT teilen, enthaelt Bankbeziehungen")
        print(TRENNER)
        for institut, grund in gescheitert:
            print(f"   {institut:<40} {grund[:110]}")
    return 0 if gebaut and not gescheitert else 1


if __name__ == "__main__":
    raise SystemExit(main())
