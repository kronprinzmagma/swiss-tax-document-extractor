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


def steuerjahr_des_laufs(samples: Path) -> str | None:
    """Das Steuerjahr des Laufs — das Jahr mit strikter Mehrheit, sonst None.

    Liest ``<samples>/_results_full.json`` (nur lesen): in Einträgen mit
    status ``ok`` die Felder ``jahr`` mit vierstelligem Wert 19xx/20xx.
    Kein fester Rückfall im Code (261025-kme, B1a/B1b): ohne klare Mehrheit
    wählen die Leser kein Jahr und melden es.
    """
    import json
    from collections import Counter

    try:
        daten = json.loads((Path(samples) / "_results_full.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    zaehler: Counter = Counter()
    for e in daten if isinstance(daten, list) else []:
        if not isinstance(e, dict) or e.get("status") != "ok":
            continue
        for f in e.get("fields") or []:
            if isinstance(f, dict) and f.get("feld") == "jahr":
                wert = str(f.get("value") or "").strip()
                if re.fullmatch(r"(?:19|20)\d{2}", wert):
                    zaehler[wert] += 1
    if not zaehler:
        return None
    (erstes, n), *rest = zaehler.most_common()
    if rest and rest[0][1] == n:
        return None
    return erstes


def _staat(kennung: str) -> tuple[str, str, str, bool]:
    """(Staat, Herkunft, Hinweis, Warnung) aus der Kennung des Kontos.

    Eine IBAN trägt ihr Land vorne; bisher stand jede ausländische IBAN als
    „CH" da, und eine Kontonummer wie „123-456" galt als „berechnet"
    (261025-kme, B6). Ausserhalb CH/LI fragt die Maske anders — ein Hinweis
    mit Warnung. Ohne IBAN: Schweiz angenommen.
    """
    k = re.sub(r"\s", "", str(kennung or "")).upper()
    if re.fullmatch(r"[A-Z]{2}\d{2}[0-9A-Z]{7,26}", k):
        land = k[:2]
        if land in ("CH", "LI"):
            return land, "berechnet", "", False
        return land, "berechnet", "Ausland — Maske prüfen", True
    return "CH", "annahme", "Schweiz angenommen", False


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
    # Wo im Beleg der angezeigte Wert steht: {seite, bbox, snippet}. Nur wenn
    # der angezeigte Wert der gelesene ist (261025-hbv, B5).
    anker: dict | None = None

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
                            hinweis="fehlt — Position aus der Durchsicht ohne "
                                    "Gegenstück im Dokument"))
        staat, s_herkunft, s_hinweis, s_warnung = _staat(g["iban"])
        bezeichnung = g["bezeichnung"] or (
            "Depot gemäss Steuerauszug" if g["depot"] else "")
        felder = [
            Feld("Bank, Gesellschaft", g["bank"], g["bank"], "ablage")
            if g["bank"] else Feld("Bank, Gesellschaft",
                                   hinweis="fehlt — im Beleg nachsehen"),
            kennung,
            Feld("Staat", staat, "", s_herkunft, s_hinweis, warnung=s_warnung),
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


def _gepruefte_zeilen(zeilen: list[dict], zielwerte) -> list[dict]:
    return [z for z in zeilen
            if z.get("zielwert") in zielwerte
            and (z.get("herkunft") in GEPRUEFT or z.get("vorabBetrag"))
            and betrag(z.get("betrag")) is not None]


def _gepruefter(zeilen: list[dict], zielwerte) -> Decimal | tuple | None:
    """Der geprüfte Wert: einer → Decimal, keiner → None, mehrere
    verschiedene → Tupel (Widerspruch, sichtbar statt verschwiegen —
    261025-hbv, B3; vorher galt „mehrere" wie „keiner")."""
    werte = {betrag(z.get("betrag")) for z in _gepruefte_zeilen(zeilen, zielwerte)}
    werte.discard(None)
    if not werte:
        return None
    return werte.pop() if len(werte) == 1 else tuple(sorted(werte))


def _normiert(text) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text or "").lower())


def objekt_marke(o) -> str:
    """Die Marke eines Objekts der Kette — so steht es in ``unterscheidung``
    einer je Objekt bestätigten Position (261025): Hypothek über ihre
    Nummer, Konto über die letzten vier Stellen der IBAN, sonst die Seite."""
    if o.art == "Hypothek":
        nr = o.werte.get("nummer")
        return f"Hypothek Nr. {nr[0].wert}" if nr else f"Hypothek S. {o.quellen[0][1]}"
    if o.kennung and o.kennung.herkunft != "annahme":
        return f"Konto …{str(o.kennung.wert).replace(' ', '')[-4:]}"
    return f"Konto S. {o.quellen[0][1]}"


def objekt_felder(o) -> list[tuple[str, str, Decimal | None, dict | None]]:
    """(Zielwert, Titel, Belegwert, Anker) — was je Objekt bestätigt wird."""
    from extractors import bankobjekte

    def wert(name):
        w, _, a = bankobjekte.einwert(o.werte.get(name))
        return (w if isinstance(w, Decimal) else None), a

    if o.art == "Hypothek":
        (s, a_s), (z, a_z) = wert("schuld"), wert("zins")
        return [(SCHULD, "Schuld am 31.12.", s, a_s),
                (SCHULDZINS, "Schuldzinsen", z, a_z)]
    (s, a_s), (z, a_z) = wert("saldo"), wert("zins")
    kolonne = bankobjekte.einwert(o.werte.get("kolonne"))[0]
    ertrag = ERTRAG_MIT_VST if kolonne == "A" else ERTRAG_OHNE_VST
    return [(SALDO[0], "Saldo 31.12.", s, a_s),
            (ertrag, "Ertrag " + ("mit" if kolonne == "A" else "ohne")
             + " Verrechnungssteuer", z, a_z)]


def _zuordnen(zeilen: list[dict], zielwerte, belegwert, kennungen,
              allein: bool, marke: str = "",
              fremde: set[str] | frozenset = frozenset()
              ) -> tuple[Decimal | tuple | None, list[dict]]:
    """Welche geprüften Zeilen gehören zu diesem Objekt (Konto, Hypothek)?

    Allein im Dokument: alle. Sonst eine Zeile, deren Betrag dem Belegwert
    gleicht oder deren Unterscheidung (mindestens 3 Zeichen) in einer
    Kennung des Objekts steht (IBAN, Hypothekar-Nr., Bezeichnung). Vorher
    galt in einem Dokument mit zwei Konten gar keine geprüfte Zeile — der
    Beleg überstimmte die Durchsicht (261025-hbv, B3).
    """
    kandidaten = _gepruefte_zeilen(zeilen, zielwerte)
    # Je Objekt bestätigt (261025): die Marke entscheidet, sonst nichts.
    # Eine Position mit der Marke eines ANDEREN Objekts gehört nie hierher —
    # auch nicht über einen gleichen Betrag (zwei gleich grosse Hypotheken).
    if marke:
        eigene = [z for z in kandidaten
                  if _normiert(z.get("unterscheidung")) == _normiert(marke)]
        if eigene:
            return _gepruefter(eigene, zielwerte), eigene
    kandidaten = [z for z in kandidaten
                  if _normiert(z.get("unterscheidung")) not in fremde]
    if allein:
        zugeordnet = kandidaten
    else:
        normiert = [k for k in (_normiert(x) for x in kennungen) if k]
        zugeordnet = []
        for z in kandidaten:
            u = _normiert(z.get("unterscheidung"))
            if ((isinstance(belegwert, Decimal) and betrag(z.get("betrag")) == belegwert)
                    or (len(u) >= 3 and any(u in k for k in normiert))):
                zugeordnet.append(z)
    return _gepruefter(zugeordnet, zielwerte), zugeordnet


def _unklar_melden(feld: Feld, z: dict, objekt: str) -> None:
    zusatz = f"Durchsicht nennt {anzeige(betrag(z.get('betrag')))}, Zuordnung zum {objekt} unklar"
    if zusatz not in feld.hinweis:
        feld.hinweis = "; ".join(x for x in (feld.hinweis, zusatz) if x)
    feld.warnung = True


def _aus_beleg(titel: str, wert, gepruefter: Decimal | tuple | None = None,
               ersatz: Decimal | None = None, anker: dict | None = None) -> Feld:
    """Betragsfeld: geprüft vor Beleg; ein abweichender Beleg warnt.

    ``anker`` gilt dem Belegwert — er steht nur am Feld, wenn der angezeigte
    Wert auch der Belegwert ist.
    """
    if isinstance(gepruefter, tuple):
        werte = " / ".join(anzeige(w) for w in gepruefter)
        hinweis = f"Durchsicht nennt mehrere Werte: {werte}"
        if isinstance(wert, Decimal):
            hinweis += f" — Beleg sagt {anzeige(wert)}"
        return Feld(titel, werte, "", "widerspruch", hinweis + " — bitte prüfen")
    if gepruefter is not None:
        f = Feld(titel, anzeige(gepruefter), eingabe(gepruefter), "ablage")
        if isinstance(wert, Decimal) and wert != gepruefter:
            f.hinweis = f"Beleg sagt {anzeige(wert)} — bitte prüfen"
            f.warnung = True
        elif isinstance(wert, Decimal):
            f.anker = anker
        return f
    if isinstance(wert, Decimal):
        return Feld(titel, anzeige(wert), eingabe(wert), "beleg", anker=anker)
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
    zugeordnet: set[int] = set()
    je_dok: list[tuple] = []
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
        saldo, _, a_saldo = bankobjekte.einwert(o.werte.get("saldo"))
        zins, _, a_zins = bankobjekte.einwert(o.werte.get("zins"))
        kolonne = bankobjekte.einwert(o.werte.get("kolonne"))[0]
        kennung = o.kennung
        bez = o.bezeichnung.wert if o.bezeichnung else ""
        # Geprüftes gilt auch in Dokumenten mit mehreren Konten — je Konto
        # über Betrag oder Unterscheidung zugeordnet (261025-hbv, B3).
        kennungen = [kennung.wert if kennung else "", bez]
        marke = objekt_marke(o)
        fremde = {_normiert(objekt_marke(x)) for x in objekte
                  if x is not o and set(doks) & {q[0] for q in x.quellen}}
        g_saldo, z_saldo = _zuordnen(eigene, SALDO, saldo, kennungen, allein,
                                     marke, fremde)
        g_mit, z_mit = _zuordnen(eigene, (ERTRAG_MIT_VST,), zins, kennungen, allein,
                                 marke, fremde)
        g_ohne, z_ohne = _zuordnen(eigene, (ERTRAG_OHNE_VST,), zins, kennungen, allein,
                                   marke, fremde)
        zugeordnet.update(id(z) for z in z_saldo + z_mit + z_ohne)
        lauf_ertrag = None
        if allein and not isinstance(zins, Decimal):
            werte = {betrag(z.get("betrag")) for z in eigene
                     if z.get("zielwert") in (ERTRAG_OHNE_VST, ERTRAG_MIT_VST)}
            werte.discard(None)
            lauf_ertrag = werte.pop() if len(werte) == 1 else None
        mit_vst = kolonne == "A" or g_mit is not None
        ertrag = _aus_beleg("Ertrag mit Verrechnungssteuer" if mit_vst
                            else "Ertrag ohne Verrechnungssteuer",
                            zins, g_mit if mit_vst else g_ohne, lauf_ertrag, a_zins)
        leer = Feld("Ertrag ohne Verrechnungssteuer" if mit_vst
                    else "Ertrag mit Verrechnungssteuer", herkunft="leer",
                    hinweis="nur eine der beiden Kolonnen")
        if (o.werte.get("zins") and o.werte["zins"][0].herkunft
                == bankobjekte.KEIN_ZINS_AUSGEWIESEN and ertrag.herkunft == "beleg"):
            ertrag.hinweis = "Bescheinigung weist keinen Zins aus"
        if ertrag.herkunft == "fehlt":
            ertrag.hinweis = ("kein Zins gefunden — im Beleg nachsehen; "
                              "ohne Zins 0.00 eintragen")
        if kennung and kennung.herkunft != "annahme":
            k_feld = Feld("Konto-/Depot-Nr.", kennung.wert, kennung.wert, "beleg",
                          anker=kennung.anker)
        elif kennung:
            k_feld = Feld("Konto-/Depot-Nr.", kennung.wert, "", "annahme",
                          "IBAN nicht gültig gelesen — im Beleg prüfen",
                          warnung=True, anker=kennung.anker)
        else:
            k_feld = Feld("Konto-/Depot-Nr.", hinweis="fehlt — im Beleg nachsehen")
        iban = kennung.wert if kennung else ""
        staat, s_herkunft, s_hinweis, s_warnung = _staat(iban)
        waehrung = o.waehrung.wert if o.waehrung else "CHF"
        felder = [
            Feld("Bank, Gesellschaft", bank, bank, "beleg") if bank
            else Feld("Bank, Gesellschaft", hinweis="fehlt — im Beleg nachsehen"),
            k_feld,
            Feld("Staat", staat, "", s_herkunft, s_hinweis, warnung=s_warnung),
            Feld("Bezeichnung", bez, bez, "beleg" if bez else "annahme",
                 "" if bez else "frei, z. B. Privatkonto",
                 anker=o.bezeichnung.anker if o.bezeichnung else None),
            Feld("Währung", waehrung, "",
                 "annahme" if o.waehrung and o.waehrung.herkunft == "annahme"
                 else "beleg", anker=o.waehrung.anker if o.waehrung else None),
            _aus_beleg("Total Guthaben (Steuerwert 31.12.)", saldo, g_saldo, anker=a_saldo),
        ]
        felder += [ertrag, leer] if not mit_vst else [leer, ertrag]
        n_leer = sum(leer_je_dok.get(d, 0) for d in doks)
        if n_leer:
            felder[1].hinweis = "; ".join(x for x in (
                felder[1].hinweis, f"dazu {n_leer} Nebenkonto/-konten ohne "
                "Guthaben und Zins im Dokument") if x)
        e = Eintrag("Bankkonto", " · ".join(t for t in (bank, bez or iban) if t),
                    person, doks, felder)
        aus.append(e)
        je_dok.append((doks, e, felder[5], ertrag))
    # Geprüfte Zeilen eines Dokuments mit mehreren Konten, die keinem Konto
    # zugeordnet werden konnten: an jedem Konto des Dokuments sagen, nicht
    # verschweigen (261025-hbv, B3).
    for d, n in konten_je_dok.items():
        if n < 2:
            continue
        for z in _gepruefte_zeilen(_zeilen_von(zeilen, d), KONTOWERTE):
            if id(z) in zugeordnet:
                continue
            for doks, _, total, ertrag_feld in je_dok:
                if d in doks:
                    _unklar_melden(total if z.get("zielwert") in SALDO else ertrag_feld,
                                   z, "Konto")
    return aus, gedeckt


def _nachgerechnet(o) -> bool:
    """Rechnet der Beleg dieser Hypothek in sich auf? Jede Zinszeile
    (Kapital × Satz × Tage / 360) und die Summe der Zeilen = Rechnungsbetrag."""
    kontrolle = o.werte.get("_kontrolle") or []
    summe = o.werte.get("_summe_stimmt") or []
    return bool(kontrolle) and bool(summe) and all(w.wert for w in kontrolle + summe)


def schulden_aus_kette(objekte, zeilen: list[dict],
                       tabellen: dict | None = None) -> tuple[list[Eintrag], set[str]]:
    """Ein Eintrag je Hypothek der Kette, mit Gläubiger-Adresse und Satz."""
    from extractors import bankobjekte

    aus, gedeckt = [], set()
    hyp_je_dok: dict[str, int] = {}
    for o in objekte:
        if o.art == "Hypothek":
            for d in dict.fromkeys(q[0] for q in o.quellen):
                hyp_je_dok[d] = hyp_je_dok.get(d, 0) + 1
    zugeordnet: set[int] = set()
    je_dok: list[tuple] = []
    for o in objekte:
        if o.art != "Hypothek":
            continue
        doks = list(dict.fromkeys(q[0] for q in o.quellen))
        gedeckt.update(doks)
        eigene = [z for d in doks for z in _zeilen_von(zeilen, d)]
        allein = all(hyp_je_dok.get(d) == 1 for d in doks)
        bank = _bankname(eigene, doks[0], (tabellen or {}).get(doks[0]))
        adresse = None
        if tabellen and doks[0] in tabellen:
            adresse = bankobjekte._bank(tabellen[doks[0]], bank)[1]
        glaeubiger = ", ".join(t for t in (bank, adresse.wert if adresse else "") if t)
        g_feld = Feld("Name, Vorname und Adresse des Gläubigers", glaeubiger,
                      glaeubiger, "beleg" if adresse else "ablage",
                      "" if adresse else "Adresse fehlt — aus dem Briefkopf ergänzen",
                      warnung=adresse is None, anker=adresse.anker if adresse else None)
        saetze = list(dict.fromkeys(w.wert for w in o.werte.get("zinssatz", [])))
        satz = Feld("Zinssatz (gehört laut Wegleitung dazu)",
                    " / ".join(saetze), " / ".join(saetze) if len(saetze) == 1 else "",
                    "beleg" if saetze else "fehlt",
                    "Satz wechselte in der Periode" if len(saetze) > 1 else "",
                    anker=o.werte["zinssatz"][0].anker if o.werte.get("zinssatz") else None)
        schuld, _, a_schuld = bankobjekte.einwert(o.werte.get("schuld"))
        zins, _, a_zins = bankobjekte.einwert(o.werte.get("zins"))
        # Geprüftes geht vor — auch bei Hypotheken; vorher las die Kette den
        # Beleg und überging die Durchsicht ganz (261025-hbv, B3).
        kennungen = [str(w.wert) for w in o.werte.get("nummer", [])] + [
            o.bezeichnung.wert if o.bezeichnung else ""]
        marke = objekt_marke(o)
        fremde = {_normiert(objekt_marke(x)) for x in objekte
                  if x is not o and set(doks) & {q[0] for q in x.quellen}}
        g_schuld, z_schuld = _zuordnen(eigene, (SCHULD,), schuld, kennungen, allein,
                                       marke, fremde)
        g_zins, z_zins = _zuordnen(eigene, (SCHULDZINS,), zins, kennungen, allein,
                                   marke, fremde)
        zugeordnet.update(id(z) for z in z_schuld + z_zins)
        felder = [g_feld, satz,
                  _aus_beleg("Schuld am 31.12.", schuld, g_schuld, anker=a_schuld),
                  _aus_beleg("Schuldzinsen", zins, g_zins, anker=a_zins)]
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
        je_dok.append((doks, o, felder[2], felder[3]))
    for d, n in hyp_je_dok.items():
        if n < 2:
            continue
        hier = [(o, s_feld, z_feld) for doks, o, s_feld, z_feld in je_dok if d in doks]
        for z in _gepruefte_zeilen(_zeilen_von(zeilen, d), (SCHULD, SCHULDZINS)):
            if id(z) in zugeordnet:
                continue
            ist_schuld = z.get("zielwert") == SCHULD
            # Gehört ein übriger Wert der Durchsicht genau zu einer Hypothek —
            # der einzigen ohne eigenen geprüften Wert —, wird er dort genannt,
            # nicht an alle gehängt (sonst sah es aus, als hätten alle
            # denselben Zins; der Anwender 261025). Rechnet der Beleg dieser Hypothek
            # nach (Kapital × Satz × Tage, Summe der Zeilen), gilt der Beleg;
            # sonst bleibt es eine Warnung.
            frei = [(o, s_feld if ist_schuld else z_feld) for o, s_feld, z_feld in hier
                    if (s_feld if ist_schuld else z_feld).herkunft != "ablage"]
            if len(frei) == 1:
                o, ziel = frei[0]
                gesagt = anzeige(betrag(z.get("betrag")))
                if not ist_schuld and _nachgerechnet(o) and ziel.herkunft == "beleg":
                    zusatz = (f"Durchsicht nennt {gesagt} — der Beleg rechnet nach "
                              "(Kapital × Satz × Tage), der Beleg gilt")
                    if zusatz not in ziel.hinweis:
                        ziel.hinweis = "; ".join(x for x in (ziel.hinweis, zusatz) if x)
                else:
                    _unklar_melden(ziel, z, "Hypothek")
                continue
            # Mehrere ohne eigenen Wert: an diese; keine: an alle.
            for _, ziel in frei or [(o, s_feld if ist_schuld else z_feld)
                                    for o, s_feld, z_feld in hier]:
                _unklar_melden(ziel, z, "Hypothek")
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
                           "iban": "IBAN des Kontos zum Depot"}.get(d.kennung_art, ""),
                          anker=d.anker.get("kennung"))
        else:
            k_feld = Feld("Konto-/Depot-Nr.",
                          hinweis="Pflichtfeld — Depot-Nr. aus dem Steuerauszug ergänzen")
        g_sw = _gepruefter(eigene, SALDO)
        g_a = _gepruefter(eigene, (ERTRAG_MIT_VST,))
        g_b = _gepruefter(eigene, (ERTRAG_OHNE_VST,))
        ertrag_b = _aus_beleg("Ertrag ohne Verrechnungssteuer", d.ertrag_b, g_b,
                              anker=d.anker.get("ertrag_b"))
        ertrag_a = _aus_beleg("Ertrag mit Verrechnungssteuer", d.ertrag_a, g_a,
                              anker=d.anker.get("ertrag_a"))
        if d.form == "portfolio" and g_a is None and g_b is None:
            # Ertrag (Zins) gehört zum Konto; die Wertschriften dieser
            # Übersicht weisen keinen aus (der Anwender: „es gibt das yuh/Swissquote-
            # Konto und die Wertschriften").
            ertrag_b = Feld("Ertrag ohne Verrechnungssteuer", "0.00", "0.00", "beleg",
                            "Übersicht weist für die Wertschriften keinen Ertrag aus; "
                            "der Zins steht beim Konto")
            ertrag_a = Feld("Ertrag mit Verrechnungssteuer", herkunft="leer",
                            hinweis="nur eine der beiden Kolonnen")
        steuerwert = _aus_beleg("Total Steuerwert", d.steuerwert, g_sw,
                                anker=d.anker.get("steuerwert"))
        if d.steuerwert_herkunft == "berechnet" and steuerwert.herkunft == "beleg":
            # Gesamtvermögen − Liquidität ist gerechnet, nicht gelesen
            # (261025-hbv, B6); der Anker zeigt aufs Gesamtvermögen.
            steuerwert.herkunft = "berechnet"
            steuerwert.hinweis = "; ".join(x for x in (
                steuerwert.hinweis, "berechnet: Gesamtvermögen − Liquidität") if x)
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
        # Übersicht/WV: alle Befunde ans Total. Portfolio und ohne Form: nur
        # die Warnungen — „Ertrag steht beim Konto" ist kein Alarm, eine
        # unklare Stichtagsspalte oder eine fehlende Form schon (261025-kme,
        # B1c). Hat die Durchsicht den Steuerwert geprüft, gilt er.
        if d.form not in ("portfolio", ""):
            melden = d.befunde
        else:
            melden = d.warnungen if felder[4].herkunft != "ablage" else []
        for b in melden:
            felder[4].hinweis = "; ".join(x for x in (felder[4].hinweis, b) if x)
            felder[4].warnung = True
        e = Eintrag("Steuerauszug/Depot",
                    " · ".join(t for t in (bank, "Depot gemäss Steuerauszug") if t),
                    person, [dok], felder)
        e.liquiditaet = d.liquiditaet if d.form == "portfolio" else None
        e.gesamt = d.gesamt
        e.gesamt_anker = d.anker.get("gesamt")
        e.text = " ".join(z.text for z in tab.zeilen).lower()
        aus.append(e)
    return aus


def alle_eintraege(samples: Path, zeilen: list[dict], inhaber_von: dict[str, str],
                   konto_aus_name=None, pdf_dir: Path | None = None
                   ) -> tuple[list[Eintrag], list[Eintrag]]:
    """(Konten und Depots, Schulden) — die eine Quelle für Leiste und Auszug.

    Konten und Hypotheken aus der Kette, Depots aus festen Stellen; nur was
    keiner der Leser abdeckt, kommt aus den Tabellenzeilen (261024-jga).
    ``pdf_dir``: der echte Ordner entscheidet, was „nicht mehr im Ordner"
    heisst (261025-jm6, A3); ohne ihn gilt der letzte Lauf.
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
    belegt = k_kette + d_kette
    return (belegt + _konten_abgleichen(belegt, konten(rest, iban_je_dokument(samples),
                                                         konto_aus_name),
                                                  _laufdokumente(samples, pdf_dir)),
            s_kette + _abgleichen(s_kette, schulden(rest)))


def _laufdokumente(samples: Path, pdf_dir: Path | None = None) -> set[str]:
    """Welche Dokumente liegen „im Ordner"? Namen in NFC (``dok_name``).

    Mit ``pdf_dir``: die PDFs des echten Ordners (Unterordner eingeschlossen,
    ohne ``irrelevant/``) — sonst hiess „fehlt im Ordner" nur „fehlt im
    letzten Lauf", und ein neu abgelegtes, noch nicht eingelesenes PDF galt
    als fehlend (261025-jm6, A3). Fehlt der Ordner, ist das ein Fehler.

    Rückfall: ohne Ordner gilt der letzte Lauf als Ordner (Korpus, alte
    Aufrufer).
    """
    import json
    if pdf_dir is not None:
        from extractors.belegordner import alle_pdfs
        if not Path(pdf_dir).is_dir():
            raise FileNotFoundError("PDF-Ordner (--pdf-dir) nicht gefunden")
        return {dok_name(p.name) for p in alle_pdfs(Path(pdf_dir))}
    try:
        return {dok_name(e.get("pdf_name")) for e in json.loads(
            (samples / "_results_full.json").read_text(encoding="utf-8"))}
    except (OSError, ValueError):
        return set()


def _konten_abgleichen(belegt: list[Eintrag], aus_zeilen: list[Eintrag],
                       laufdoks: set[str]) -> list[Eintrag]:
    """Konten der Durchsicht, deren Dokument nicht mehr im Ordner liegt.

    Hat ein Konto aus dem Ordner denselben Saldo, ist es dasselbe — sonst
    stünde es doppelt in der Erklärung (261024-jga, echter Lauf). Ohne
    Gegenstück bleibt es, mit Warnung.

    „Dasselbe" heisst seit 261025-hbv (B4): gleicher Betrag UND dieselbe Bank
    UND dieselbe Person. Gleiche Beträge sind kein Duplikat — zwei Kinder
    mit demselben Sparguthaben bei verschiedenen Banken waren vorher eines.
    Fällt ein Eintrag weg, sagt es der behaltene.
    """
    def saldo(e):
        f = _total(e)
        return betrag(f.wert) if f else None

    # Kandidaten (Betrag, Eintrag): Konten und Depots aus dem Ordner; auch
    # das Gesamtvermögen eines Portfolios zählt — ein alter Durchsicht-Wert
    # „inkl. Kontokorrent" ist dasselbe Depot (261024-jga, echter Lauf). Auch
    # Werte der Durchsicht zu Dokumenten im Ordner: ein alter Name mit
    # demselben Saldo bei derselben Bank ist dasselbe Konto.
    kandidaten = ([(saldo(e), e) for e in belegt]
                  + [(getattr(e, "gesamt", None), e) for e in belegt]
                  + [(saldo(e), e) for e in aus_zeilen
                     if any(dok_name(d) in laufdoks for d in e.dokumente)])
    kandidaten = [(b, e) for b, e in kandidaten if b is not None]
    bleiben = []
    # Kontokorrent eines Portfolios (1:1 gleicher Saldo): das Konto gehört zu
    # diesem Dokument — kein fehlendes Dokument (261024-jga, echter Lauf).
    kontokorrent = {getattr(e, "liquiditaet", None): e for e in belegt
                    if getattr(e, "liquiditaet", None) is not None}
    for e in aus_zeilen:
        verwaist = not any(dok_name(d) in laufdoks for d in e.dokumente)
        if not verwaist:
            bleiben.append(e)
            continue
        portfolio = kontokorrent.get(saldo(e))
        if portfolio is not None:
            e.dokumente = list(portfolio.dokumente)
            for f in e.felder:
                if f.titel.startswith("Konto-/Depot"):
                    nr = next((x.wert for x in portfolio.felder
                               if x.titel.startswith("Konto-/Depot") and x.wert), "")
                    f.wert, f.einsetzen, f.herkunft = nr, nr, "beleg"
                    f.hinweis, f.warnung = "Kontokorrent des Portfolios", False
                if f.titel.startswith("Total"):
                    f.hinweis = "Kontokorrent des Portfolios — 1:1 wie im Dokument"
                    f.warnung = False
            bleiben.append(e)
            continue
        treffer = next((k for b, k in kandidaten if k is not e and b == saldo(e)
                        and _selbe_bank(_bank_von(k), _bank_von(e))
                        and k.person == e.person), None)
        if treffer is not None:
            _deckt_auch(_total(treffer), e)
            continue
        for f in e.felder:
            if f.titel.startswith("Total"):
                f.hinweis = "Dokument nicht mehr im Ordner — nur aus der Durchsicht"
                f.warnung = True
        bleiben.append(e)
    return bleiben


def _total(e: Eintrag) -> Feld | None:
    return next((f for f in e.felder if f.titel.startswith("Total")), None)


def _bank_von(e: Eintrag) -> str:
    """Bank bzw. Gläubiger eines Eintrags (bei Gläubigern ohne Adresse)."""
    f = next((f for f in e.felder if f.titel.startswith(("Bank", "Name, Vorname"))), None)
    if f is None:
        return ""
    return f.wert.split(",")[0] if f.titel.startswith("Name, Vorname") else f.wert


def _selbe_bank(a: str, b: str) -> bool:
    """Dieselbe Bank — nur, wenn beide einen Namen tragen (261025-hbv, B4).

    Vorher galt ein fehlender Name als „passt": eine Schuld der Durchsicht
    ohne Gläubiger fiel neben jeder gleich hohen Hypothek weg.
    """
    ka, kb = _kern(a), _kern(b)
    return bool(ka) and bool(kb) and ka[:5] == kb[:5]


def _deckt_auch(feld: Feld | None, weg: Eintrag) -> None:
    """Spur am behaltenen Eintrag: was als dasselbe weggefallen ist."""
    if feld is None:
        return
    zusatz = f"deckt auch Durchsicht-Eintrag aus {', '.join(weg.dokumente)} ab"
    if zusatz not in feld.hinweis:
        feld.hinweis = "; ".join(x for x in (feld.hinweis, zusatz) if x)


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
        if sw.herkunft == "widerspruch":
            continue          # mehrere geprüfte Werte: nichts zu rechnen
        if liq not in saldi:
            # Abgezogen wird nur, wenn ein Konto GENAU diesen Kontostand hat
            # (der Anwender: „der Betrag sollte 1:1 übereinstimmen"). Sonst ist der
            # Kontokorrent kein eigenes Konto im Ordner Konten und bleibt im
            # Depot — sonst ginge er verloren.
            gesamt = (d.gesamt if getattr(d, "gesamt", None) is not None
                      else betrag(sw.einsetzen or sw.wert) + liq)
            geprueft = sw.herkunft == "ablage"
            if not geprueft:
                # Jetzt steht das gelesene Gesamtvermögen da — kein Rechenwert.
                sw.wert, sw.einsetzen = anzeige(gesamt), eingabe(gesamt)
                sw.herkunft, sw.anker = "beleg", getattr(d, "gesamt_anker", None)
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

    def schuld_feld(e):
        return next((x for x in e.felder if x.titel == "Schuld am 31.12."), None)

    belegt = [(e, *werte(e)) for e in kette]
    bleiben = []
    for r in aus_zeilen:
        schuld, zins = werte(r)
        # Dieselbe Hypothek nur bei gleichem Gläubiger UND gleicher Person —
        # gleiche Beträge sind kein Duplikat (261025-hbv, B4).
        gleich = [(e, s, z) for e, s, z in belegt
                  if _selbe_bank(_bank_von(e), _bank_von(r)) and e.person == r.person and (
                      (schuld is not None and s == schuld)
                      or (schuld is None and zins is not None and z == zins))]
        if any(z == zins for _, _, z in gleich) or (gleich and zins is None):
            e = next((e for e, _, z in gleich if z == zins), gleich[0][0])
            _deckt_auch(schuld_feld(e), r)
            continue
        if gleich:
            e = gleich[0][0]
            _deckt_auch(schuld_feld(e), r)
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


def objekte_des_dokuments(samples: Path, beleg: str) -> dict:
    """Hypotheken und Konten eines Dokuments für die Review (261025).

    Je Objekt: Marke, Seiten, Zinssatz und je Feld der Belegwert und — wenn
    schon je Objekt bestätigt — der bestätigte Wert. Dazu die Positionen,
    die durch eine solche Bestätigung zurückgetreten sind.
    """
    from extractors.ablage import lade_ersetzt, lade_geprueft

    dok = dok_name(beleg)
    objekte, _ = kette_laden(samples)
    hier = [o for o in objekte if dok in {dok_name(q[0]) for q in o.quellen}]
    geprueft = [z for z in lade_geprueft(samples) if dok_name(z.get("pdf_name")) == dok]
    aus = []
    for o in hier:
        marke = objekt_marke(o)
        felder = []
        for zielwert, titel, belegwert, anker in objekt_felder(o):
            bestaetigt = next((betrag(z.get("betrag")) for z in geprueft
                               if z.get("zielwert") == zielwert
                               and _normiert(z.get("unterscheidung")) == _normiert(marke)),
                              None)
            felder.append({
                "zielwert": zielwert, "titel": titel,
                "beleg": anzeige(belegwert) if belegwert is not None else "",
                "bestaetigt": anzeige(bestaetigt) if bestaetigt is not None else "",
                "seite": (anker or {}).get("seite"),
            })
        saetze = list(dict.fromkeys(str(w.wert) for w in o.werte.get("zinssatz", [])))
        aus.append({
            "marke": marke, "art": o.art,
            "seiten": sorted({q[1] for q in o.quellen if dok_name(q[0]) == dok}),
            "satz": " / ".join(saetze),
            "nachgerechnet": _nachgerechnet(o) if o.art == "Hypothek" else None,
            "felder": felder,
        })
    ersetzt = [{"zielwert": e["zielwert"], "pos": e["pos"],
                "betrag": anzeige(betrag(e["betrag"])) if betrag(e["betrag"]) is not None
                else str(e["betrag"])}
               for e in lade_ersetzt(samples) if dok_name(e["dokument"]) == dok]
    return {"objekte": aus, "ersetzt": ersetzt}


def bestaetige_objekt(samples: Path, beleg: str, marke: str,
                      werte: dict[str, str]) -> dict:
    """Prüft Marke und Felder gegen die Kette, dann in die Ablage."""
    from extractors import ablage

    dok = dok_name(beleg)
    objekte, _ = kette_laden(samples)
    hier = [o for o in objekte if dok in {dok_name(q[0]) for q in o.quellen}]
    ziel = next((o for o in hier if objekt_marke(o) == marke), None)
    if ziel is None:
        raise ValueError("Dieses Objekt gehört nicht zu diesem Dokument — Seite neu laden")
    erlaubt = {f[0] for f in objekt_felder(ziel)}
    sauber: dict[str, str] = {}
    for zielwert, roh in werte.items():
        if zielwert not in erlaubt:
            raise ValueError(f"Feld {zielwert} gehört nicht zu {marke}")
        b = betrag(roh)
        if b is None:
            raise ValueError(f"{zielwert}: kein Betrag")
        sauber[zielwert] = f"{abs(b):.2f}"
    marken: dict[str, list[str]] = {}
    for o in hier:
        for zielwert, *_ in objekt_felder(o):
            marken.setdefault(zielwert, []).append(objekt_marke(o))
    anker = {}
    for zielwert, _, _, a in objekt_felder(ziel):
        if a and a.get("seite"):
            kasten = a.get("bbox")
            anker[zielwert] = (f"S.{a['seite']} ({', '.join(f'{x:.1f}' for x in kasten)})"
                               if kasten else f"S.{a['seite']}")
    # Der Dateiname, unter dem das Dokument im Lauf steht (nicht NFC-normiert).
    name = next((q[0] for q in ziel.quellen if dok_name(q[0]) == dok), beleg)
    return ablage.bestaetige_objekt(samples, name, marke, sauber, marken, anker)
