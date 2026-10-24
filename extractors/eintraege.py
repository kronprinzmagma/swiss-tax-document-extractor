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
    """Was ins Formularfeld kommt: reine Zahl, ZHprivateTax formatiert selbst."""
    return f"{wert.quantize(Decimal('0.01'), ROUND_HALF_UP):.2f}"


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
