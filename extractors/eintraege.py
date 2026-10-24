"""Einträge, wie ZHprivateTax sie abfragt — ein Konto, eine Schuld.

Massstab ist `FELDLISTE-ZHPRIVATETAX-2025.md`: die Felder der
Online-Masken, in ihrer Reihenfolge. Vorher gab die Leiste lose Zeilen aus
— sechs Beträge unter „Schuldenverzeichnis", ohne zu sagen, welcher Zins zu
welcher Hypothek gehört, und Beträge so roh wie im Beleg (`21.083` für
21'083). Damit liess sich kein Formular ausfüllen (261024-jga, vom Nutzer
gemeldet).

Regeln, die hier gelten:

* **Ein Konto = eine Bank + eine Kennung.** Zusammengelegt wird nur über die
  IBAN aus dem Beleg. Eine Ziffernfolge im Dateinamen kann Konto-, Kunden-
  oder Datumsnummer sein (`123456` steht in Kontoauszug, Depot und
  Zinsabrechnung derselben Kundin) — sie wird angezeigt, nie verwendet.
* **Jeder Betrag wird gelesen, nicht durchgereicht** (`parse_swiss_amount`).
  Angezeigt wird `12'164.86`, eingesetzt `12164.86`.
* **Jede Lücke ist ein Feld** mit Herkunft ``fehlt`` — nie ein stilles Leer.

Herkunft je Feld: ``ablage`` (in der Durchsicht geprüft), ``beleg`` (aus dem
Beleg gelesen, nicht durchgesehen), ``berechnet``, ``annahme``, ``fehlt``.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from extractors.numbers import parse_swiss_amount

SALDO = ("Saldo 31.12.", "Steuerwert 31.12.")
ERTRAG_MIT_VST = "Bruttoertrag mit Verrechnungssteuer"
ERTRAG_OHNE_VST = "Bruttoertrag ohne Verrechnungssteuer"
SCHULD = "Hypothekarschuld 31.12."
SCHULDZINS = "Schuldzinsen Hypothek"
KONTOWERTE = (*SALDO, ERTRAG_MIT_VST, ERTRAG_OHNE_VST)


def _ist_datum(ziffern: str) -> bool:
    """``20240623`` ist ein Datum, keine Kontonummer."""
    import datetime as _dt
    if len(ziffern) != 8 or not ziffern.isdigit():
        return False
    try:
        _dt.date(int(ziffern[:4]), int(ziffern[4:6]), int(ziffern[6:]))
        return 1990 <= int(ziffern[:4]) <= 2100
    except ValueError:
        return False


def betrag(text) -> Decimal | None:
    """Der Betrag als Zahl — oder ``None``, wenn keiner da ist."""
    roh = str(text or "").strip()
    if not roh:
        return None
    try:
        return parse_swiss_amount(roh)
    except (ValueError, TypeError):
        return None


def anzeige(wert: Decimal) -> str:
    """``12164.86`` → ``12'164.86``. Immer zwei Stellen."""
    q = wert.quantize(Decimal("0.01"), ROUND_HALF_UP)
    return f"{q:,.2f}".replace(",", "'")


def eingabe(wert: Decimal) -> str:
    """Was ins Formularfeld kommt: ``10'000.00`` — wie die Anzeige.

    der Anwender (261024-jga): „alle Zahlen im Format 10'000.00 — das akzeptiert
    ZHprivateTax". Eine Schreibweise für Anzeige und Einsetzen.
    """
    return anzeige(wert)


@dataclass
class Feld:
    titel: str
    wert: str = ""          # was die Leiste zeigt
    einsetzen: str = ""     # was ins Feld kommt; leer = kein Knopf
    herkunft: str = "fehlt"
    hinweis: str = ""
    # Muss der Mensch hier etwas tun? Fehlt etwas, widersprechen sich die
    # Quellen, ist eine Zuordnung nur angenommen — dann ja.
    warnung: bool = False

    def __post_init__(self):
        self.warnung = self.warnung or self.herkunft in ("fehlt",
                                                         "widerspruch")


def _betragsfeld(titel: str, werte: list[tuple[Decimal, str]],
                 herkunft: str = "ablage") -> Feld:
    """Ein Betragsfeld aus einer oder mehreren Fundstellen.

    Sagen zwei Belege desselben Kontos Verschiedenes, wird nichts gewählt:
    beide stehen da, und es gibt keinen Knopf.
    """
    verschieden = sorted({w for w, _ in werte})
    if not verschieden:
        return Feld(titel, hinweis="fehlt — im Beleg nachsehen")
    if len(verschieden) > 1:
        return Feld(titel, " / ".join(anzeige(w) for w in verschieden),
                    herkunft="widerspruch",
                    hinweis="Belege widersprechen sich: "
                            + "; ".join(f"{anzeige(w)} ({d})"
                                        for w, d in werte))
    return Feld(titel, anzeige(verschieden[0]), eingabe(verschieden[0]),
                herkunft)


@dataclass
class Eintrag:
    maske: str              # „Bankkonto", „Wertschrift und Guthaben", „Schuld"
    titel: str
    person: str
    dokumente: list[str]
    felder: list[Feld] = field(default_factory=list)

    def als_dict(self) -> dict:
        return asdict(self)


def _umbenannt(feld: Feld, titel: str) -> Feld:
    feld.titel = titel
    return feld


def konten(zeilen: list[dict], iban_von: dict[str, str] | None = None,
           konto_aus_name=None) -> list[Eintrag]:
    """Einträge fürs Wertschriftenverzeichnis, je Bank und Kennung.

    ``iban_von``: Dokument → IBAN, nur wo genau eine im Dokument steht.
    ``konto_aus_name``: Dokumentname → Ziffernfolge oder None (nur Hinweis).
    """
    iban_von = iban_von or {}
    gruppen: dict[tuple, dict] = {}
    for z in zeilen:
        zielwert = str(z.get("zielwert") or "")
        if zielwert not in KONTOWERTE:
            continue
        dok = str(z.get("pdf_name") or z.get("beleg") or "")
        bank = str(z.get("aussteller") or "").strip()
        person = str(z.get("person") or "").strip()
        pos = int(z.get("pos") or 1)
        iban = iban_von.get(dok, "")
        # Ohne IBAN: je Dokument und Position — getrennt statt verschmolzen.
        schluessel = (bank, person, iban) if iban else (bank, person, dok, pos)
        g = gruppen.setdefault(schluessel, {
            "bank": bank, "person": person, "iban": iban, "dokumente": [],
            "depot": False, "werte": {}, "bezeichnung": "", "dok_pos": []})
        if dok not in g["dokumente"]:
            g["dokumente"].append(dok)
        g["dok_pos"].append((dok, pos))
        g["depot"] |= str(z.get("belegtyp") or "") == "wertschriftenverzeichnis"
        g["bezeichnung"] = g["bezeichnung"] or str(z.get("unterscheidung") or "")
        wert = betrag(z.get("betrag"))
        if wert is not None:
            schluessel_wert = "saldo" if zielwert in SALDO else zielwert
            g["werte"].setdefault(schluessel_wert, []).append((wert, dok))

    aus = []
    for g in sorted(gruppen.values(),
                    key=lambda g: (g["person"], g["bank"], g["iban"],
                                   g["dokumente"])):
        if g["iban"]:
            kennung = Feld("Konto-/Depot-Nr.", g["iban"], g["iban"], "beleg")
        else:
            aus_name = next((k for d in g["dokumente"]
                             if konto_aus_name and (k := konto_aus_name(d))
                             and not _ist_datum(k)), None)
            kennung = (Feld("Konto-/Depot-Nr.", aus_name, aus_name, "annahme",
                            "aus dem Dateinamen — kann auch eine "
                            "Kundennummer sein, bitte prüfen", warnung=True)
                       if aus_name else
                       Feld("Konto-/Depot-Nr.",
                            hinweis="fehlt — im Beleg nachsehen"))
        staat = g["iban"][:2] if g["iban"][:2] in ("CH", "LI") else "CH"
        bezeichnung = g["bezeichnung"] or (
            "Depot gemäss Steuerauszug" if g["depot"] else "")
        felder = [
            Feld("Bank, Gesellschaft", g["bank"], g["bank"], "ablage")
            if g["bank"] else Feld("Bank, Gesellschaft",
                                   hinweis="fehlt — im Beleg nachsehen"),
            kennung,
            Feld("Staat", staat, "", "berechnet" if g["iban"] else "annahme",
                 "" if g["iban"] else "Schweiz angenommen"),
            Feld("Bezeichnung", bezeichnung, bezeichnung,
                 "ablage" if g["bezeichnung"] else "annahme",
                 "" if bezeichnung else "frei, z. B. Privatkonto"),
            Feld("Währung", "CHF", "", "annahme", "nicht aus dem Beleg gelesen"),
            _betragsfeld("Total Guthaben (Steuerwert 31.12.)",
                         g["werte"].get("saldo", [])),
            _betragsfeld("Ertrag ohne Verrechnungssteuer",
                         g["werte"].get(ERTRAG_OHNE_VST, [])),
            _betragsfeld("Ertrag mit Verrechnungssteuer",
                         g["werte"].get(ERTRAG_MIT_VST, [])),
        ]
        # Ein fehlender Ertrag ist meist schlicht keiner: kein Alarm.
        for f in felder[-2:]:
            if f.herkunft == "fehlt":
                f.hinweis = "kein Ertrag ausgewiesen"
                f.herkunft = "leer"
                f.warnung = False
        if g["depot"]:
            # Maske „Steuerauszug/Depot erfassen" (in der Demo geprüft):
            # Bank und Depot-Nr. und Total Steuerwert sind Pflicht, Staat
            # und Bezeichnung gibt es dort nicht, dafür die Zugehörigkeit.
            felder = [felder[0], felder[1],
                      Feld("Zugehörigkeit (Person 1 / Person 2)",
                           g["person"], "", "ablage"),
                      felder[4],
                      _umbenannt(felder[5], "Total Steuerwert"),
                      felder[6], felder[7]]
            if not felder[1].wert:
                felder[1].hinweis = ("Pflichtfeld — Depot-Nr. aus dem "
                                     "Steuerauszug ergänzen")
        aus.append(Eintrag(
            maske="Steuerauszug/Depot" if g["depot"] else "Bankkonto",
            titel=" · ".join(t for t in (g["bank"], kennung.wert
                                          or bezeichnung) if t),
            person=g["person"], dokumente=g["dokumente"], felder=felder))
    return aus


def schulden(zeilen: list[dict]) -> list[Eintrag]:
    """Einträge fürs Schuldenverzeichnis: je Schuld eine Zeile.

    Schuld und Zins werden je Gläubiger und Person gepaart — über eine
    gleiche Unterscheidung, sonst der Reihe nach (Position). Wer der Reihe
    nach gepaart wurde, sagt es: die Ablage kennt die Verbindung nicht.
    """
    gruppen: dict[tuple, dict] = {}
    for z in zeilen:
        zielwert = str(z.get("zielwert") or "")
        if zielwert not in (SCHULD, SCHULDZINS):
            continue
        schluessel = (str(z.get("aussteller") or "").strip(),
                      str(z.get("person") or "").strip())
        g = gruppen.setdefault(schluessel, {SCHULD: [], SCHULDZINS: []})
        g[zielwert].append(z)

    aus = []
    for (glaeubiger, person), g in sorted(gruppen.items()):
        def ordnung(z):
            return (int(z.get("pos") or 1), str(z.get("unterscheidung") or ""))
        hyp = sorted(g[SCHULD], key=ordnung)
        zin = sorted(g[SCHULDZINS], key=ordnung)
        paare: list[tuple[dict | None, dict | None, str]] = []
        # Erst die eindeutigen: gleiche Unterscheidung auf beiden Seiten.
        for h in list(hyp):
            u = str(h.get("unterscheidung") or "").strip()
            treffer = [x for x in zin
                       if u and str(x.get("unterscheidung") or "").strip() == u]
            if len(treffer) == 1:
                paare.append((h, treffer[0], "gleiche Unterscheidung"))
                hyp.remove(h)
                zin.remove(treffer[0])
        for i in range(max(len(hyp), len(zin))):
            paare.append((hyp[i] if i < len(hyp) else None,
                          zin[i] if i < len(zin) else None,
                          "der Reihe nach"))

        for n, (h, zi, wie) in enumerate(paare, start=1):
            s = betrag((h or {}).get("betrag"))
            zins = betrag((zi or {}).get("betrag"))
            zusatz = next((str(x.get("unterscheidung")) for x in (h, zi)
                           if x and x.get("unterscheidung")), "")
            satz = ""
            if s and zins:
                satz = f"{(zins / s * 100).quantize(Decimal('0.01'))} %"
            felder = [
                Feld("Name, Vorname und Adresse des Gläubigers", glaeubiger,
                     glaeubiger, "ablage",
                     "Adresse fehlt — aus Vertrag/Briefkopf ergänzen",
                     warnung=True),
                Feld("Zinssatz (gehört laut Wegleitung dazu)", satz, "",
                     "berechnet" if satz else "fehlt",
                     "Zins ÷ Schuld — Näherung, den Vertragssatz prüfen"
                     if satz else "fehlt — im Vertrag nachsehen"),
                _betragsfeld("Schuld am 31.12.",
                             [(s, (h or {}).get("pdf_name", ""))] if s else []),
                _betragsfeld("Schuldzinsen",
                             [(zins, (zi or {}).get("pdf_name", ""))]
                             if zins else []),
            ]
            if len(paare) > 1 and wie == "der Reihe nach":
                for f in felder[2:]:
                    f.hinweis = "der Reihe nach zugeordnet — bitte prüfen"
                    f.warnung = True
            dokumente = sorted({str(x.get("pdf_name") or "")
                                for x in (h, zi) if x})
            aus.append(Eintrag(
                maske="Schuld",
                titel=" · ".join(t for t in (
                    glaeubiger, zusatz or (f"Schuld {n}" if len(paare) > 1
                                            else "")) if t),
                person=person, dokumente=dokumente, felder=felder))
    return aus


def iban_je_dokument(samples: Path) -> dict[str, str]:
    """Dokument → IBAN, nur wo genau eine im Dokument steht."""
    try:
        from scripts.etax_iban import sammle
        return {d: i[0] for d, i in sammle(samples).items() if len(i) == 1}
    except Exception:      # noqa: BLE001 - ohne IBANs bleibt es beim Dokument
        return {}


# ---------------------------------------------------------------------------
# Konten und Hypotheken aus der Kette (261024-jga).
#
# Bis hier baute die Leiste Konten und Schulden aus den Tabellenzeilen des
# Extraktionslaufs — ein Wert je Dokument und Zielwert. Ein Dokument mit
# drei Hypotheken erschien als eine, mit dem Zins der einen und der Schuld
# der anderen. Die Kette liest Seite für Seite: eine Seite, ein Objekt;
# dieselbe IBAN oder Hypothekar-Nr. ist dasselbe Objekt. Sie ist die
# Quelle; was der Mensch in der Durchsicht bestätigt hat, geht vor.

GEPRUEFT = ("ablage", "mensch")


def dok_name(name) -> str:
    """Dokumentname in einer Schreibweise (NFC).

    macOS legt „ö" in Dateinamen zerlegt ab (o + ¨), die Ablage hält es
    zusammengesetzt. Gleich aussehende Namen waren damit verschieden, und
    ein Eintrag der Durchsicht fand sein Konto nicht (261024-jga).
    """
    return unicodedata.normalize("NFC", str(name or ""))


def kette_laden(samples: Path, typen=("bank_zinsausweis",
                                      "hypothek_zinsbestaetigung")):
    """(Objekte, Tabelle je Dokument) für alle Dokumente der ``typen``."""
    import json

    from extractors import bankobjekte, rohtabelle

    typ_von = {}
    try:
        for e in json.loads((samples / "_results_full.json").read_text(encoding="utf-8")):
            typ_von[dok_name(e.get("pdf_name"))] = e.get("belegtyp")
    except (OSError, ValueError):
        return [], {}
    tabellen = {}
    for pfad in sorted(samples.glob("*.json")):
        if pfad.name.startswith("_") or pfad.name.endswith(".truth.json"):
            continue
        try:
            d = json.loads(pfad.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(d, dict) or "pages" not in d:
            continue
        name = dok_name(d.get("pdf_name"))
        if typ_von.get(name) in typen:
            tabellen[name] = rohtabelle.baue(d)
    return bankobjekte.objekte(list(tabellen.items())), tabellen


def _zeilen_von(zeilen: list[dict], dok: str) -> list[dict]:
    return [z for z in zeilen if dok_name(z.get("pdf_name") or z.get("beleg")) == dok]


def _gepruefter(zeilen: list[dict], zielwerte) -> Decimal | None:
    werte = {betrag(z.get("betrag")) for z in zeilen
             if z.get("zielwert") in zielwerte
             and (z.get("herkunft") in GEPRUEFT or z.get("vorabBetrag"))}
    werte.discard(None)
    return werte.pop() if len(werte) == 1 else None


def _aus_beleg(titel: str, wert, gepruefter: Decimal | None = None,
               ersatz: Decimal | None = None) -> Feld:
    """Betragsfeld: geprüft vor Beleg; ein abweichender Beleg warnt."""
    if gepruefter is not None:
        f = Feld(titel, anzeige(gepruefter), eingabe(gepruefter), "ablage")
        if isinstance(wert, Decimal) and wert != gepruefter:
            f.hinweis = f"Beleg sagt {anzeige(wert)} — bitte prüfen"
            f.warnung = True
        return f
    if isinstance(wert, Decimal):
        return Feld(titel, anzeige(wert), eingabe(wert), "beleg")
    if ersatz is not None:
        return Feld(titel, anzeige(ersatz), eingabe(ersatz), "beleg",
                    "vom Modell gelesen, Seite nicht eindeutig — bitte prüfen",
                    warnung=True)
    if wert is not None:
        return Feld(titel, str(wert), "", "widerspruch",
                    "der Beleg nennt mehrere Werte")
    return Feld(titel, hinweis="fehlt — im Beleg nachsehen")


def _bankname(zeilen: list[dict], dok: str, tabelle=None) -> str:
    """Bank des Dokuments — nur ein Name, der im Dokument auch steht.

    Das Modell setzte einmal die Bank eines anderen Dokuments ein. Steht der
    Aussteller nicht im Text, gilt der Anfang des Dateinamens (die Dokumente
    heissen „Bank - Art - Person Jahr").
    """
    # „Bank - Art - Person" oder „Bank-Name-Zinsabrechnung-…": alles vor der
    # Dokumentart ist die Bank.
    stamm = dok.rsplit(".", 1)[0]
    if " - " in stamm:
        aus_name = stamm.split(" - ")[0].strip()
    else:
        teil = re.split(r"[\s_-]+(?:Zinsabrechnung|Zinsausweis|Verm[öo]gensausweis|"
                        r"Schuldenausweis|Kontoauszug|Steuerauszug|Saldo|Hypothek)",
                        stamm, maxsplit=1, flags=re.I)
        aus_name = teil[0].replace("-", " ").strip() if len(teil) > 1 else ""
    aussteller = next((str(z.get("aussteller")).strip() for z in zeilen
                       if z.get("aussteller")), "")
    # „," ist kein Name.
    if not re.search(r"[A-Za-zÄÖÜäöü]", aussteller):
        aussteller = ""
    if tabelle is None:
        return aussteller or aus_name
    text = " ".join(z.text for z in tabelle.zeilen).lower()
    if aussteller and aussteller.lower() in text:
        return aussteller
    if aus_name and aus_name.lower() in text:
        return aus_name
    return aussteller or aus_name


def konten_aus_kette(objekte, zeilen: list[dict],
                     tabellen: dict | None = None) -> tuple[list[Eintrag], set[str]]:
    """Ein Eintrag je Konto der Kette; dazu die Dokumente, die er abdeckt."""
    from extractors import bankobjekte

    aus, gedeckt = [], set()
    # Nebenkonto ohne Guthaben und ohne Zins (Fremdwährungskonto mit 0.00):
    # für die Erklärung leer — kein eigener Eintrag, aber ein Hinweis.
    leer_je_dok: dict[str, int] = {}

    def ist_leer(o) -> bool:
        saldo = bankobjekte.einwert(o.werte.get("saldo"))[0]
        zins = bankobjekte.einwert(o.werte.get("zins"))[0]
        return saldo is None and (zins is None or zins == 0)

    for o in objekte:
        if o.art == "Konto" and ist_leer(o):
            for d in dict.fromkeys(q[0] for q in o.quellen):
                leer_je_dok[d] = leer_je_dok.get(d, 0) + 1
                gedeckt.add(d)
    objekte = [o for o in objekte if not (o.art == "Konto" and ist_leer(o))]
    konten_je_dok: dict[str, int] = {}
    for o in objekte:
        if o.art == "Konto":
            for d in dict.fromkeys(q[0] for q in o.quellen):
                konten_je_dok[d] = konten_je_dok.get(d, 0) + 1
    for o in objekte:
        if o.art != "Konto":
            continue
        doks = list(dict.fromkeys(q[0] for q in o.quellen))
        gedeckt.update(doks)
        eigene = [z for d in doks for z in _zeilen_von(zeilen, d)]
        # Ein Wert aus Lauf oder Ablage gehört nur dann sicher zu diesem
        # Konto, wenn das Dokument kein zweites hat.
        allein = all(konten_je_dok.get(d) == 1 for d in doks)
        bank = _bankname(eigene, doks[0], (tabellen or {}).get(doks[0]))
        person = next((str(z.get("person")) for z in eigene if z.get("person")), "")
        saldo = bankobjekte.einwert(o.werte.get("saldo"))[0]
        zins = bankobjekte.einwert(o.werte.get("zins"))[0]
        kolonne = bankobjekte.einwert(o.werte.get("kolonne"))[0]
        g_saldo = _gepruefter(eigene, SALDO) if allein else None
        g_mit = _gepruefter(eigene, (ERTRAG_MIT_VST,)) if allein else None
        g_ohne = _gepruefter(eigene, (ERTRAG_OHNE_VST,)) if allein else None
        lauf_ertrag = None
        if allein and not isinstance(zins, Decimal):
            werte = {betrag(z.get("betrag")) for z in eigene
                     if z.get("zielwert") in (ERTRAG_OHNE_VST, ERTRAG_MIT_VST)}
            werte.discard(None)
            lauf_ertrag = werte.pop() if len(werte) == 1 else None
        mit_vst = kolonne == "A" or g_mit is not None
        ertrag = _aus_beleg("Ertrag mit Verrechnungssteuer" if mit_vst
                            else "Ertrag ohne Verrechnungssteuer",
                            zins, g_mit if mit_vst else g_ohne, lauf_ertrag)
        leer = Feld("Ertrag ohne Verrechnungssteuer" if mit_vst
                    else "Ertrag mit Verrechnungssteuer", herkunft="leer",
                    hinweis="nur eine der beiden Kolonnen")
        if (o.werte.get("zins") and o.werte["zins"][0].herkunft
                == bankobjekte.KEIN_ZINS_AUSGEWIESEN and ertrag.herkunft == "beleg"):
            ertrag.hinweis = "Bescheinigung weist keinen Zins aus"
        if ertrag.herkunft == "fehlt":
            ertrag.hinweis = ("kein Zins gefunden — im Beleg nachsehen; "
                              "ohne Zins 0.00 eintragen")
        kennung = o.kennung
        if kennung and kennung.herkunft != "annahme":
            k_feld = Feld("Konto-/Depot-Nr.", kennung.wert, kennung.wert, "beleg")
        elif kennung:
            k_feld = Feld("Konto-/Depot-Nr.", kennung.wert, "", "annahme",
                          "IBAN nicht gültig gelesen — im Beleg prüfen",
                          warnung=True)
        else:
            k_feld = Feld("Konto-/Depot-Nr.", hinweis="fehlt — im Beleg nachsehen")
        iban = kennung.wert if kennung else ""
        bez = o.bezeichnung.wert if o.bezeichnung else ""
        waehrung = o.waehrung.wert if o.waehrung else "CHF"
        felder = [
            Feld("Bank, Gesellschaft", bank, bank, "beleg") if bank
            else Feld("Bank, Gesellschaft", hinweis="fehlt — im Beleg nachsehen"),
            k_feld,
            Feld("Staat", iban[:2] if iban[:2] in ("CH", "LI") else "CH", "",
                 "berechnet" if iban else "annahme"),
            Feld("Bezeichnung", bez, bez, "beleg" if bez else "annahme",
                 "" if bez else "frei, z. B. Privatkonto"),
            Feld("Währung", waehrung, "",
                 "annahme" if o.waehrung and o.waehrung.herkunft == "annahme"
                 else "beleg"),
            _aus_beleg("Total Guthaben (Steuerwert 31.12.)", saldo, g_saldo),
        ]
        felder += [ertrag, leer] if not mit_vst else [leer, ertrag]
        n_leer = sum(leer_je_dok.get(d, 0) for d in doks)
        if n_leer:
            felder[1].hinweis = "; ".join(x for x in (
                felder[1].hinweis, f"dazu {n_leer} Nebenkonto/-konten ohne "
                "Guthaben und Zins im Dokument") if x)
        aus.append(Eintrag("Bankkonto",
                           " · ".join(t for t in (bank, bez or iban) if t),
                           person, doks, felder))
    return aus, gedeckt


def schulden_aus_kette(objekte, zeilen: list[dict],
                       tabellen: dict | None = None) -> tuple[list[Eintrag], set[str]]:
    """Ein Eintrag je Hypothek der Kette, mit Gläubiger-Adresse und Satz."""
    from extractors import bankobjekte

    aus, gedeckt = [], set()
    for o in objekte:
        if o.art != "Hypothek":
            continue
        doks = list(dict.fromkeys(q[0] for q in o.quellen))
        gedeckt.update(doks)
        eigene = [z for d in doks for z in _zeilen_von(zeilen, d)]
        bank = _bankname(eigene, doks[0], (tabellen or {}).get(doks[0]))
        adresse = None
        if tabellen and doks[0] in tabellen:
            adresse = bankobjekte._bank(tabellen[doks[0]], bank)[1]
        glaeubiger = ", ".join(t for t in (bank, adresse.wert if adresse else "") if t)
        g_feld = Feld("Name, Vorname und Adresse des Gläubigers", glaeubiger,
                      glaeubiger, "beleg" if adresse else "ablage",
                      "" if adresse else "Adresse fehlt — aus dem Briefkopf ergänzen",
                      warnung=adresse is None)
        saetze = list(dict.fromkeys(w.wert for w in o.werte.get("zinssatz", [])))
        satz = Feld("Zinssatz (gehört laut Wegleitung dazu)",
                    " / ".join(saetze), " / ".join(saetze) if len(saetze) == 1 else "",
                    "beleg" if saetze else "fehlt",
                    "Satz wechselte in der Periode" if len(saetze) > 1 else "")
        schuld = bankobjekte.einwert(o.werte.get("schuld"))[0]
        zins = bankobjekte.einwert(o.werte.get("zins"))[0]
        felder = [g_feld, satz, _aus_beleg("Schuld am 31.12.", schuld),
                  _aus_beleg("Schuldzinsen", zins)]
        # Die Periode wird genannt, nicht bewertet: eine Hypothek kann erst
        # im Lauf des Jahres beginnen (der Anwender, 261024-jga: „gelten erst ab Juli").
        periode = bankobjekte.einwert(o.werte.get("periode"))[0]
        if periode:
            felder[3].hinweis = f"Zinsperiode {periode}"
        nr = o.werte.get("nummer")
        titel = " · ".join(t for t in (bank, (o.bezeichnung.wert if o.bezeichnung else ""),
                                       str(nr[0].wert) if nr else "") if t)
        person = next((str(z.get("person")) for z in eigene if z.get("person")), "")
        aus.append(Eintrag("Schuld", titel, person, doks, felder))
    return aus, gedeckt


def tabellen_fuer(samples: Path, typen: tuple[str, ...]) -> dict:
    """Dokument → Rohtabelle, für alle Dokumente der ``typen``."""
    return kette_laden(samples, typen)[1]


def depots_aus_dokumenten(tabellen: dict, zeilen: list[dict],
                          inhaber_von: dict[str, str]) -> list[Eintrag]:
    """Ein Eintrag „Steuerauszug/Depot" je Dokument, aus festen Stellen.

    Kolonne A (mit VSt) und B (ohne) getrennt — das Formular rechnet aus A
    den Rückerstattungsanspruch. Geprüftes aus der Durchsicht geht vor.
    """
    from extractors import depot
    from scripts.build_tax_output import _collect_candidate_roles

    aus = []
    for dok, tab in tabellen.items():
        d = depot.lese(tab)
        eigene = _zeilen_von(zeilen, dok)
        bank = _bankname(eigene, dok, tab)
        eltern = _collect_candidate_roles(inhaber_von.get(dok, "")) & {
            "elternteil_1", "elternteil_2"}
        if len(eltern) == 2:
            person, p_hinweis = "familie", "gemeinsames Depot beider Eltern"
        elif len(eltern) == 1:
            person, p_hinweis = next(iter(eltern)), ""
        else:
            person = next((str(z.get("person")) for z in eigene
                           if z.get("person")), "")
            p_hinweis = "" if person else "Inhaber nicht erkannt"
        if d.kennung:
            # Weist die Bank keine Depot-Nr. aus, ist die Kundennummer die
            # Kennung im Dokument — ein Hinweis, kein Alarm.
            k_feld = Feld("Konto-/Depot-Nr.", d.kennung, d.kennung, "beleg",
                          {"kunde": "Kundennummer — das Dokument nennt keine Depot-Nr.",
                           "iban": "IBAN des Kontos zum Depot"}.get(d.kennung_art, ""))
        else:
            k_feld = Feld("Konto-/Depot-Nr.",
                          hinweis="Pflichtfeld — Depot-Nr. aus dem Steuerauszug ergänzen")
        g_sw = _gepruefter(eigene, SALDO)
        g_a = _gepruefter(eigene, (ERTRAG_MIT_VST,))
        g_b = _gepruefter(eigene, (ERTRAG_OHNE_VST,))
        ertrag_b = _aus_beleg("Ertrag ohne Verrechnungssteuer", d.ertrag_b, g_b)
        ertrag_a = _aus_beleg("Ertrag mit Verrechnungssteuer", d.ertrag_a, g_a)
        if d.form == "portfolio" and g_a is None and g_b is None:
            # Ertrag (Zins) gehört zum Konto; die Wertschriften dieser
            # Übersicht weisen keinen aus (der Anwender: „es gibt das yuh/Swissquote-
            # Konto und die Wertschriften").
            ertrag_b = Feld("Ertrag ohne Verrechnungssteuer", "0.00", "0.00", "beleg",
                            "Übersicht weist für die Wertschriften keinen Ertrag aus; "
                            "der Zins steht beim Konto")
            ertrag_a = Feld("Ertrag mit Verrechnungssteuer", herkunft="leer",
                            hinweis="nur eine der beiden Kolonnen")
        steuerwert = _aus_beleg("Total Steuerwert", d.steuerwert, g_sw)
        if d.form == "portfolio" and d.liquiditaet:
            steuerwert.hinweis = "; ".join(x for x in (
                steuerwert.hinweis,
                f"Gesamtvermögen {anzeige(d.gesamt)} ohne Liquidität "
                f"{anzeige(d.liquiditaet)} — die steht als Konto") if x)
        felder = [
            Feld("Bank, Gesellschaft", bank, bank, "beleg") if bank
            else Feld("Bank, Gesellschaft", hinweis="fehlt — im Dokument nachsehen"),
            k_feld,
            Feld("Zugehörigkeit (Person 1 / Person 2)", person, "",
                 "beleg" if person else "fehlt", p_hinweis),
            Feld("Währung", "CHF", "", "annahme"),
            steuerwert,
            ertrag_b, ertrag_a,
        ]
        for b in d.befunde:
            if d.form != "portfolio":
                felder[4].hinweis = "; ".join(x for x in (felder[4].hinweis, b) if x)
                felder[4].warnung = True
        e = Eintrag("Steuerauszug/Depot",
                    " · ".join(t for t in (bank, "Depot gemäss Steuerauszug") if t),
                    person, [dok], felder)
        e.liquiditaet = d.liquiditaet if d.form == "portfolio" else None
        e.gesamt = d.gesamt
        e.text = " ".join(z.text for z in tab.zeilen).lower()
        aus.append(e)
    return aus


def alle_eintraege(samples: Path, zeilen: list[dict], inhaber_von: dict[str, str],
                   konto_aus_name=None) -> tuple[list[Eintrag], list[Eintrag]]:
    """(Konten und Depots, Schulden) — die eine Quelle für Leiste und Auszug.

    Konten und Hypotheken aus der Kette, Depots aus festen Stellen; nur was
    keiner der Leser abdeckt, kommt aus den Tabellenzeilen (261024-jga).
    """
    objekte, tabellen = kette_laden(samples)
    k_kette, k_gedeckt = konten_aus_kette(objekte, zeilen, tabellen)
    s_kette, s_gedeckt = schulden_aus_kette(objekte, zeilen, tabellen)
    d_tabellen = tabellen_fuer(samples, ("wertschriftenverzeichnis",))
    d_kette = depots_aus_dokumenten(d_tabellen, zeilen, inhaber_von)
    gedeckt = k_gedeckt | s_gedeckt | set(d_tabellen)
    _liquiditaet_gegenprobe(d_kette, k_kette)
    rest = [z for z in zeilen
            if dok_name(z.get("pdf_name") or z.get("beleg")) not in gedeckt]
    return (k_kette + d_kette + konten(rest, iban_je_dokument(samples), konto_aus_name),
            s_kette + _abgleichen(s_kette, schulden(rest)))


def _liquiditaet_gegenprobe(depots: list[Eintrag], konten_: list[Eintrag]) -> None:
    """Liquidität im Portfolio = Kontostand des Kontos derselben Person?

    Das Konto wird über den Banknamen gefunden, der in der Übersicht steht
    (yuh: „Bankdienstleistungen … von Swissquote Bank AG").
    """
    for d in depots:
        liq = getattr(d, "liquiditaet", None)
        if not liq:
            continue
        personen = ({"elternteil_1", "elternteil_2"} if d.person == "familie"
                    else {d.person})
        passend = [k for k in konten_ if k.person in personen and k.felder
                   and _kern(k.felder[0].wert)[:6]
                   and _kern(k.felder[0].wert)[:6] in re.sub(r"[^a-z]", "", d.text)]
        saldi = {betrag(f.wert) for k in passend for f in k.felder
                 if f.titel.startswith("Total Guthaben")}
        sw = next(f for f in d.felder if f.titel == "Total Steuerwert")
        if liq not in saldi:
            # Abgezogen wird nur, wenn ein Konto GENAU diesen Kontostand hat
            # (der Anwender: „der Betrag sollte 1:1 übereinstimmen"). Sonst ist der
            # Kontokorrent kein eigenes Konto im Ordner Konten und bleibt im
            # Depot — sonst ginge er verloren.
            gesamt = (d.gesamt if getattr(d, "gesamt", None) is not None
                      else betrag(sw.einsetzen or sw.wert) + liq)
            geprueft = sw.herkunft == "ablage"
            if not geprueft:
                sw.wert, sw.einsetzen = anzeige(gesamt), eingabe(gesamt)
            sw.hinweis = "Gesamtvermögen inkl. Kontokorrent — kein Konto mit diesem Kontostand"
            sw.warnung = geprueft and betrag(sw.wert) != gesamt
            if sw.warnung:
                sw.hinweis += f"; Beleg sagt {anzeige(gesamt)} — bitte prüfen"


def _kern(name: str) -> str:
    return re.sub(r"[^a-z]", "", (name or "").lower())[:8]


def _abgleichen(kette: list[Eintrag], aus_zeilen: list[Eintrag]) -> list[Eintrag]:
    """Schulden aus der Durchsicht gegen die Kette halten — nichts doppelt.

    Im echten Lauf standen Hypotheken aus der Durchsicht unter anderen
    Dokumentnamen (Schuldenausweis, Zinsausweis) neben denselben drei aus der
    Zinsabrechnung (261024-jga). Gleiche Schuld und gleicher Zins: dieselbe
    Hypothek. Gleiche Schuld, anderer Zins: dieselbe, der Zins der Durchsicht
    wird angezeigt (anderer Zeitraum?). Sonst bleibt der Eintrag, mit Warnung.
    """
    def werte(e):
        f = {x.titel: x for x in e.felder}
        return (betrag(f.get("Schuld am 31.12.", Feld("")).wert),
                betrag(f.get("Schuldzinsen", Feld("")).wert))

    def glaeubiger(e):
        return _kern(e.felder[0].wert.split(",")[0]) if e.felder else ""

    belegt = [(e, *werte(e)) for e in kette]
    bleiben = []
    for r in aus_zeilen:
        schuld, zins = werte(r)
        def selbe_bank(a, b):
            return not a or not b or a[:5] == b[:5]

        gleich = [(e, s, z) for e, s, z in belegt
                  if selbe_bank(glaeubiger(e), glaeubiger(r)) and (
                      (schuld is not None and s == schuld)
                      or (schuld is None and zins is not None and z == zins))]
        if any(z == zins for _, _, z in gleich) or (gleich and zins is None):
            continue
        if gleich:
            e = gleich[0][0]
            zf = next(x for x in e.felder if x.titel == "Schuldzinsen")
            # Das Dokument gilt; der abweichende Wert der Durchsicht wird
            # genannt, nicht verschwiegen — ohne Alarm.
            zusatz = f"Durchsicht nannte {anzeige(zins)} ({', '.join(r.dokumente)})"
            zf.hinweis = "; ".join(x for x in (zf.hinweis, zusatz) if x)
            continue
        for x in r.felder[2:]:
            x.hinweis = "; ".join(y for y in (x.hinweis, "im Dokument nicht gefunden — "
                                              "nur aus der Durchsicht") if y)
            x.warnung = True
        bleiben.append(r)
    return bleiben
