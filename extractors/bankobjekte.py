"""Schritt 3 der Kette: Zusammengehöriges aus der Rohtabelle zu Objekten.

Ein Bankdokument enthält eine Bank (Name, Adresse) und ein oder mehrere
Konten. Ein Konto beginnt mit seinem Kopf — Kontoart, Nummer, IBAN,
Währung — und reicht bis zum nächsten Kopf. Was dazwischen steht (Saldo,
Zins, Verrechnungssteuer), gehört zu diesem Konto. Damit hat ein Auszug mit
drei Konten drei Objekte, nicht eines (vorher: ein Konto je Dokument, der
Rest ging verloren — u. a. ein Habenzins von 6.05 auf der zweiten Seite).

Die Beschriftungen stammen aus der Layout-Analyse der echten Dokumente
(`ZIELBILD_EXTRAKTION.md`, Abschnitt 1). Jeder Wert trägt den Anker
seiner Zeile in der Rohtabelle.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal

from extractors.rohtabelle import Rohtabelle, Zeile, ibans_in, waehrungen_in

# Kontokopf: ein Wort auf „konto" (Privatkonto, E-Sparkonto, „BANK-N Konto").
_KONTOART_RE = re.compile(r"\b([\w-]*konto(?:\s+plus)?)\b", re.IGNORECASE)
_KEIN_KOPF_RE = re.compile(
    r"kontostand|kontoführung|kontoavisierung|kontoinhaber|kontoauszug"
    r"|wertschriftenverzeichnis|konto- und kreditprodukten", re.IGNORECASE)
_KONTONR_RE = re.compile(
    r"(?:Kontonummer|Konto-Nr\.?|Konto)\s*:?\s*([\dA-Z][\dA-Z.\-/]{3,})")
# „Kontonummer:795800 - Währung: CHF" — Kopf ohne Kontoart (E-Trading).
_NUMMERNKOPF_RE = re.compile(r"Kontonummer\s*:\s*\S+.*W\S*hrung", re.IGNORECASE)

# Saldo per 31.12. — in dieser Reihenfolge: der Saldo NACH dem Zinsabschluss
# ist der Steuerwert, „Kontostand" allein ist oft der Stand davor.
SALDO_LABELS = (
    r"Kontostand nach Zinsabschluss", r"Guthabensaldo am 31\.12", r"Endsaldo",
    r"Saldo zu Ihren Gunsten", r"Neuer Saldo", r"Saldo per 31\.12",
    r"Steuerwert", r"Kontostand",
)
ERTRAG_LABELS = (r"Bruttozins", r"Habenzins", r"Zinszahlung",
                 r"Abschlussbetreffnis", r"Bruttoertrag")
VST_FREI_RE = re.compile(r"verrechnungssteuerfrei|ohne\s+(VST|Verrechnungssteuer)",
                         re.IGNORECASE)
VST_RE = re.compile(r"Verrechnungssteuer", re.IGNORECASE)
_ADRESSE_STOPP = re.compile(r"telefon|tel\.|uid|www\.|@|betreut|sie werden"
                            r"|zuständig|seite", re.IGNORECASE)

# Bruttozins über CHF 200 im Jahr unterliegt der Verrechnungssteuer
# (Wegleitung ZH 2025, S. 25: Kolonne A).
VST_GRENZE = Decimal("200")


@dataclass
class Wert:
    wert: object
    anker: dict | None = None
    herkunft: str = "dokument"      # dokument | berechnet | annahme
    stichtag: str | None = None     # ISO-Datum, wenn der Wert eines hat


@dataclass
class Konto:
    kopf: list[Zeile] = field(default_factory=list)
    zeilen: list[Zeile] = field(default_factory=list)
    werte: dict[str, Wert] = field(default_factory=dict)


@dataclass
class Bankdokument:
    dokument: str
    bank: Wert | None = None
    adresse: Wert | None = None
    konten: list[Konto] = field(default_factory=list)


_TABELLENKOPF_RE = re.compile(r"\bDatum\b.*\b(Saldo|Gutschrift|Belastung)",
                              re.IGNORECASE)


def _ist_kopf(z: Zeile, in_tabelle: bool = False) -> bool:
    """Beginnt hier ein Konto?

    In einer Buchungstabelle ist „Übertrag Sparkonto Plus CH00…" Buchungs-
    text, kein Kontokopf — dort zählt nur eine beschriftete oder voran-
    gestellte IBAN (261024-jga: der Saldo des Privatkontos landete sonst
    beim Sparkonto).
    """
    t = z.text
    ibans = ibans_in(t)
    if re.search(r"\bIBAN\b", t) or _NUMMERNKOPF_RE.search(t):
        return True
    if ibans and ibans_in(z.zellen[0].text) and t.strip().startswith(ibans[0][:4]):
        return True
    if in_tabelle:
        return False
    if ibans:
        return True
    if _KEIN_KOPF_RE.search(t):
        return False
    m = _KONTOART_RE.search(t)
    if not m:
        return False
    # Ein Kopf ist kurz oder nennt Währung bzw. Nummer — keine Fliesstext-
    # zeile. Gezählt wird die erste Zelle: „E-Jugendsparkonto | Seite: 1 / 1"
    # ist ein Titel, auch wenn die Seitenangabe danebensteht.
    erste = z.zellen[0].text
    return ((bool(_KONTOART_RE.search(erste)) and len(erste.split()) <= 4)
            or len(t.split()) <= 6 or bool(waehrungen_in(t))
            or bool(re.search(r"\d{3,}", t)))


def _aus_spalte(zeilen: list[Zeile], label: str):
    """Tabellenform: Label im Spaltenkopf, Wert in der Zeile „Total".

    E-Trading-Zinsabrechnung: ``Datum | Habenzins | Sollzins | … | Total``
    und darunter ``Total | 0.00 | 0.00 | …``. Zugeordnet wird über die
    waagrechte Lage der Spalte, nicht über die Reihenfolge der Zahlen.
    """
    rx = re.compile(label, re.IGNORECASE)
    for i, kopf in enumerate(zeilen):
        zelle = next((c for c in kopf.zellen if rx.fullmatch(c.text.strip())
                      or rx.match(c.text.strip())), None)
        if zelle is None or len(kopf.zellen) < 3:
            continue
        mitte = (zelle.x0 + zelle.x1) / 2
        for z in zeilen[i + 1:i + 8]:
            if not re.match(r"Total\b", z.text):
                continue
            for c in z.zellen:
                if c.x0 - 25 <= mitte <= c.x1 + 25:
                    from extractors.rohtabelle import betrag_in
                    wert = betrag_in(c.text)
                    if wert is not None:
                        return wert, z
    return None


def _betrag_nach_label(z: Zeile, label: str, letzter: bool = False):
    """Der Betrag in der Zelle nach dem Label (oder der letzte der Zeile)."""
    betraege = z.betraege()
    if not betraege:
        return None
    if letzter:
        return betraege[-1][1]
    rx = re.compile(label, re.IGNORECASE)
    label_i = next((i for i, c in enumerate(z.zellen) if rx.search(c.text)), -1)
    nach = [w for i, w in betraege if i > label_i]
    return nach[0] if nach else None


def _briefkopf_adresse(erste: list[Zeile]) -> Wert | None:
    """Adresse im Briefkopf, auch wenn sie in einer eigenen Spalte steht.

    Gesucht im obersten Achtel der Seite: eine Zelle mit Strasse und Nummer
    (oder Postfach) und darunter, in derselben Spalte, PLZ und Ort.
    """
    hoehe = max((z.bbox[3] for z in erste), default=842)
    oben = [z for z in erste if z.y < hoehe * 0.12]
    for i, z in enumerate(oben):
        for c in z.zellen:
            if not re.search(r"[A-Za-zäöüÄÖÜ]{3,}\s*\d+\b|Postfach", c.text):
                continue
            for z2 in oben[i + 1:i + 4]:
                for c2 in z2.zellen:
                    if abs(c2.x0 - c.x0) < 25 and re.search(r"\b\d{4}\s+[A-ZÄÖÜ]", c2.text):
                        return Wert(f"{c.text}, {c2.text}",
                                    {"seite": z.seite, "snippet": f"{c.text} / {c2.text}"})
    return None


def _bank(tabelle: Rohtabelle, name: str) -> tuple[Wert | None, Wert | None]:
    """Name und Adresse der Bank aus dem Briefkopf der ersten Seite."""
    erste = [z for z in tabelle.zeilen if z.seite == tabelle.zeilen[0].seite]
    kern = (name or "").split()[0].lower() if name else ""
    start = next((i for i, z in enumerate(erste[:15])
                  if kern and kern in z.text.lower()), None)
    if start is None:
        return (Wert(name, None, "ablage") if name else None), _briefkopf_adresse(erste)
    zeilen = []
    for z in erste[start + 1:start + 6]:
        if _ADRESSE_STOPP.search(z.text) or abs(z.x0 - erste[start].x0) > 4:
            break
        zeilen.append(z)
    adresse = _briefkopf_adresse(erste)
    if zeilen and any(re.search(r"\b\d{4}\s+\w", z.text) for z in zeilen):
        adresse = Wert(", ".join(z.text.replace(" | ", " ") for z in zeilen),
                       {"seite": zeilen[0].seite,
                        "snippet": " / ".join(z.text for z in zeilen)})
    return Wert(name or erste[start].text, erste[start].anker(), "ablage"), adresse


def zerlege(tabelle: Rohtabelle, bankname: str = "") -> Bankdokument:
    """Rohtabelle eines Bankdokuments → Bank + Konten mit ihren Werten."""
    dok = Bankdokument(tabelle.dokument)
    dok.bank, dok.adresse = _bank(tabelle, bankname)

    aktuell: Konto | None = None
    hat_werte = False
    in_tabelle, seite = False, None
    for z in tabelle.zeilen:
        if z.seite != seite:
            in_tabelle, seite = False, z.seite
        if _TABELLENKOPF_RE.search(z.text):
            in_tabelle = True
        if _ist_kopf(z, in_tabelle):
            # Kopfzeilen desselben Kontos (Kontoart, IBAN, Nummer) stehen
            # untereinander, ohne Betrag dazwischen. Kam schon ein Betrag,
            # beginnt hier das nächste Konto.
            gleich = (aktuell is not None and not hat_werte
                      and z.seite == aktuell.kopf[-1].seite
                      and z.y - aktuell.kopf[-1].y < 80)
            if gleich:
                aktuell.kopf.append(z)
            else:
                aktuell = Konto(kopf=[z])
                dok.konten.append(aktuell)
                hat_werte = False
            continue
        if aktuell is not None:
            aktuell.zeilen.append(z)
            if z.betraege():
                hat_werte = True

    for k in dok.konten:
        _werte(k)
    # Ein Kopf ohne jede Angabe ist ein Fehlgriff (z. B. eine Fussnote).
    dok.konten = [k for k in dok.konten
                  if "saldo" in k.werte or "ertrag" in k.werte]
    return dok


def _werte(k: Konto) -> None:
    kopftext = " ".join(z.text for z in k.kopf)
    ibans = ibans_in(kopftext)
    if ibans:
        k.werte["iban"] = Wert(ibans[0], k.kopf[0].anker())
    m = _KONTONR_RE.search(kopftext.replace(" | ", " "))
    if m and not ibans_in(m.group(1)):
        k.werte["kontonummer"] = Wert(m.group(1), k.kopf[0].anker())
    elif not ibans:
        # Ohne Konto-Nr. bleibt die Kunden-Nr. — als Annahme, nicht als
        # Kontonummer ausgegeben.
        kunde = next((z for z in k.kopf + k.zeilen[:10]
                      if re.match(r"Kunde\b", z.text) and len(z.zellen) > 1), None)
        if kunde:
            k.werte["kontonummer"] = Wert(kunde.zellen[1].text, kunde.anker(),
                                          "annahme")
    # „Kontoart / Währung | Sparkonto Plus / CHF" oder „Kontoart | TRADING"
    # steht oft im Block unter dem Kopf, nicht im Kopf selbst.
    angabe = next((z for z in k.zeilen[:25]
                   if re.match(r"Kontoart\b", z.text, re.I)
                   and len(z.zellen) > 1), None)
    # Sonst die erste Währungsangabe im Kontoteil („KONTOAUSZUG in CHF").
    nennung = next((z for z in k.zeilen[:30]
                    if re.search(r"\bin\s+(CHF|EUR|USD|GBP)\b", z.text)), None)
    w = (waehrungen_in(kopftext) or (waehrungen_in(angabe.text) if angabe else [])
         or (waehrungen_in(nennung.text) if nennung else []))
    quelle = (k.kopf[0] if waehrungen_in(kopftext)
              else angabe if angabe and waehrungen_in(angabe.text) else nennung)
    k.werte["waehrung"] = (Wert(w[0], quelle.anker()) if w
                           else Wert("CHF", None, "annahme"))
    art = _KONTOART_RE.search(kopftext)
    if art and art.group(1).lower() == "konto":
        # „BANK-N Konto": der Name steht davor.
        art = re.search(r"(\w[\w-]*\s+Konto)\b", kopftext)
    if art:
        k.werte["kontoart"] = Wert(art.group(1), k.kopf[0].anker())
    elif angabe:
        art_text = angabe.zellen[1].text.split("/")[0].strip()
        k.werte["kontoart"] = Wert(art_text, angabe.anker())

    for label in SALDO_LABELS:
        treffer = [z for z in k.zeilen if re.search(label, z.text, re.I)
                   and z.betraege()]
        if treffer:
            z = treffer[-1]              # der letzte Stand gilt
            k.werte["saldo"] = Wert(_betrag_nach_label(z, label, letzter=True),
                                    z.anker())
            break
    for label in ERTRAG_LABELS:
        tabelle = _aus_spalte(k.zeilen, label)
        if tabelle is not None:
            k.werte["ertrag"] = Wert(tabelle[0], tabelle[1].anker())
            break
        for z in k.zeilen:
            if re.search(label, z.text, re.I):
                b = _betrag_nach_label(z, label)
                if b is not None:
                    k.werte["ertrag"] = Wert(b, z.anker())
                    break
        if "ertrag" in k.werte:
            break

    text = " ".join(z.text for z in k.zeilen)
    vst = next((z for z in k.zeilen if VST_RE.search(z.text)
                and not VST_FREI_RE.search(z.text) and z.betraege()), None)
    ertrag = k.werte.get("ertrag")
    vst_spalte = _aus_spalte(k.zeilen, r"Verrechnungs")
    if vst_spalte is not None and vst_spalte[0] > 0:
        k.werte["kolonne"] = Wert("A", vst_spalte[1].anker())
    elif vst is not None and vst.betraege()[-1][1] > 0:
        k.werte["kolonne"] = Wert("A", vst.anker())
    elif VST_FREI_RE.search(text):
        z = next(z for z in k.zeilen if VST_FREI_RE.search(z.text))
        k.werte["kolonne"] = Wert("B", z.anker())
    elif ertrag is not None:
        k.werte["kolonne"] = Wert(
            "A" if ertrag.wert > VST_GRENZE else "B", None, "berechnet")


# Schritt 4: was die Maske „Bankkonto" in ZHprivateTax verlangt (Demo
# 2026-10-24, Katalog ZH25-0063…0075). Pflicht = ohne den Wert lässt sich
# der Eintrag nicht sinnvoll speichern bzw. nicht zuordnen.
MASKE_BANKKONTO = (
    ("Bank, Gesellschaft", "bank", True),
    ("Konto-/Depot-Nr.", "kennung", True),
    ("Staat", "staat", False),
    ("Bezeichnung", "kontoart", False),
    ("Währung", "waehrung", True),
    ("Total Guthaben (Steuerwert 31.12.)", "saldo", True),
    ("Ertrag ohne Verrechnungssteuer", "ertrag_b", False),
    ("Ertrag mit Verrechnungssteuer", "ertrag_a", False),
)


def uebertragung(dok: Bankdokument) -> list[dict]:
    """Schritt 5: je Konto die Felder der Maske, mit Herkunft und Anker."""
    aus = []
    for k in dok.konten:
        w = dict(k.werte)
        if dok.bank:
            w["bank"] = dok.bank
        kennung = w.get("iban") or w.get("kontonummer")
        if kennung:
            w["kennung"] = kennung
            iban = w.get("iban")
            w["staat"] = (Wert(iban.wert[:2], iban.anker, "berechnet")
                          if iban else Wert("CH", None, "annahme"))
        ertrag, kolonne = w.get("ertrag"), w.get("kolonne")
        if ertrag is not None:
            ziel = "ertrag_a" if kolonne and kolonne.wert == "A" else "ertrag_b"
            w[ziel] = Wert(ertrag.wert, ertrag.anker,
                           "dokument" if kolonne and kolonne.herkunft == "dokument"
                           else "berechnet")
        felder = []
        for titel, schluessel, pflicht in MASKE_BANKKONTO:
            v = w.get(schluessel)
            # Ein Ertrag, den das Dokument nicht ausweist, ist keiner.
            leer = "leer" if schluessel.startswith("ertrag") else "fehlt"
            felder.append({
                "titel": titel, "pflicht": pflicht,
                "wert": None if v is None else v.wert,
                "herkunft": leer if v is None else v.herkunft,
                "anker": None if v is None else v.anker,
            })
        luecken = sum(1 for f in felder
                      if f["pflicht"] and (f["wert"] is None
                                           or f["herkunft"] == "annahme"))
        aus.append({"dokument": dok.dokument, "felder": felder,
                    "pflichtluecken": luecken,
                    "adresse": dok.adresse.wert if dok.adresse else None})
    return aus


# --- Plausibilität: kann der Wert so stimmen? ------------------------------
#
# Geprüft wird die Form, nicht die Wahrheit: eine IBAN mit falscher
# Prüfsumme ist sicher falsch, eine mit richtiger kann trotzdem die falsche
# sein. Befunde sind deshalb Hinweise, keine Korrekturen (261024-jga,
# vom Nutzer verlangt).

_IBAN_LAENGE = {"CH": 21, "LI": 21}
_WAEHRUNGEN = {"CHF", "EUR", "USD", "GBP", "JPY", "CAD", "AUD", "SEK", "NOK", "DKK"}
SALDO_OBERGRENZE = Decimal("10000000")
ZINSSATZ_OBERGRENZE = Decimal("0.05")     # 5 % — darüber ist es kein Zins


def iban_gueltig(iban: str) -> str | None:
    """None, wenn gültig; sonst der Grund."""
    t = iban.replace(" ", "").upper()
    if len(t) <= 5:
        return "unvollständig oder maskiert"
    land = t[:2]
    if land in _IBAN_LAENGE and len(t) != _IBAN_LAENGE[land]:
        return f"{land}-IBAN hat {_IBAN_LAENGE[land]} Stellen, hier {len(t)}"
    umgestellt = t[4:] + t[:4]
    try:
        zahl = int("".join(str(int(c, 36)) for c in umgestellt))
    except ValueError:
        return "enthält ungültige Zeichen"
    return None if zahl % 97 == 1 else "Prüfsumme stimmt nicht"


def pruefe(eintrag: dict, adresse: str | None = None) -> list[dict]:
    """Befunde zu einem Eintrag aus `uebertragung` — je Feld, mit Grund."""
    f = {x["titel"].split(" (")[0]: x for x in eintrag["felder"]}
    befunde = []

    def befund(feld, stufe, grund):
        befunde.append({"feld": feld, "stufe": stufe, "grund": grund})

    kennung = f["Konto-/Depot-Nr."]
    if kennung["wert"]:
        k = str(kennung["wert"])
        if re.match(r"^(CH|LI)\d", k):
            grund = iban_gueltig(k)
            if grund:
                befund("Konto-/Depot-Nr.", "fehler", f"IBAN {grund}")
        elif re.fullmatch(r"\d{8}", k) and 1990 <= int(k[:4]) <= 2100:
            befund("Konto-/Depot-Nr.", "fehler", "sieht aus wie ein Datum")
        elif not re.search(r"\d{4,}", k):
            befund("Konto-/Depot-Nr.", "warnung", "keine Ziffernfolge — maskiert?")
        if kennung["herkunft"] == "annahme":
            befund("Konto-/Depot-Nr.", "warnung",
                   "Kunden-Nr., keine Kontonummer — im Dokument prüfen")
    w = f["Währung"]["wert"]
    if w and w not in _WAEHRUNGEN:
        befund("Währung", "fehler", f"„{w}" + "“ ist kein Währungscode")

    saldo = f["Total Guthaben"]["wert"]
    if saldo is not None:
        if saldo < 0:
            befund("Total Guthaben", "warnung",
                   "negativ — eine Schuld gehört ins Schuldenverzeichnis")
        if abs(saldo) > SALDO_OBERGRENZE:
            befund("Total Guthaben", "warnung", "über 10 Mio. — Lesefehler?")
        if saldo != saldo.quantize(Decimal("0.01")):
            befund("Total Guthaben", "fehler", "mehr als zwei Nachkommastellen")
    for titel, kolonne in (("Ertrag ohne Verrechnungssteuer", "B"),
                           ("Ertrag mit Verrechnungssteuer", "A")):
        e = f[titel]["wert"]
        if e is None:
            continue
        if e < 0:
            befund(titel, "fehler", "negativer Ertrag")
        if saldo and saldo > 0 and e / saldo > ZINSSATZ_OBERGRENZE:
            befund(titel, "warnung",
                   f"{(e / saldo * 100).quantize(Decimal('0.1'))} % des Saldos "
                   "— als Zins unrealistisch; Saldo oder Ertrag falsch gelesen?")
        if kolonne == "B" and e > VST_GRENZE:
            befund(titel, "warnung", "über CHF 200 — unterliegt in der Regel "
                   "der Verrechnungssteuer (Kolonne A)")
    if adresse is not None:
        if not re.search(r"\b\d{4}\s+[A-ZÄÖÜ]", adresse):
            befund("Adresse der Bank", "warnung", "keine PLZ mit Ort erkannt")
        if not re.search(r"[A-Za-zäöü]{3,}\s*\d+[a-z]?\b", adresse):
            befund("Adresse der Bank", "hinweis", "keine Strasse mit Nummer — Postfach?")
    return befunde


# --- Seite für Seite (der Anwender' Regel, 2026-10-24) ------------------------------
#
# Eine Seite gehört zu genau einem Objekt. Auf jeder Seite stehen IBAN bzw.
# Nummer, was es ist (Kontoart oder Festhypothek), die Bank und „Saldo"
# bzw. „Zins". Seiten mit derselben IBAN/Nummer sind dasselbe Objekt — auch
# über Dokumente hinweg. Ein Objekt hat einen Saldo (bzw. eine Schuld) und
# einen Zins; sagen zwei Seiten Verschiedenes, ist das ein Widerspruch.
#
# Vorher wurde nach Kontoköpfen gesucht, die nur aus drei Layouts bekannt
# waren — unbekannte Köpfe verteilten Werte auf falsche oder doppelte Konten.

# „Hypothekarbank" / „Hypothekarkasse" ist ein Bankname, keine Hypothek —
# sonst wird jedes Konto dieser Bank zur Schuld (261024-jga, echter Lauf).
_HYPOTHEK_RE = re.compile(r"(fest|variable|libor|saron|geldmarkt)?-?\s*hypothek"
                          r"(?!ar(?:bank|kasse))", re.IGNORECASE)
_NUMMER_RE = re.compile(
    r"(?:Konto(?:nummer|-Nr\.?)?|Hypothek(?:ar)?(?:-Nr\.?|nummer)?|Vertrag(?:s-?Nr\.?|snummer)?"
    r"|Nr\.)\s*:?\s*([0-9][0-9A-Z.\-/]{3,})")
_SALDO_RE = re.compile(r"saldo|kontostand|guthaben|steuerwert", re.IGNORECASE)
_ZINS_RE = re.compile(r"zins", re.IGNORECASE)
_VST_KOLONNE_RE = re.compile(r"\b(ohne|mit)\s+(VSt|Verrechnungssteuer)\b", re.IGNORECASE)
_BESCHEINIGUNG_RE = re.compile(r"Zins-?\s*und\s+Kapital|Zinsausweis|Zinsbescheinigung",
                               re.IGNORECASE)
KEIN_ZINS_AUSGEWIESEN = "keiner_ausgewiesen"


def obj_hat_saldo(obj) -> bool:
    return bool(obj.werte.get("saldo"))
_KEIN_ZINS_RE = re.compile(r"zinssatz|zinsperiode|zinsabschluss\s+\d|zinsen?\s+ab\b"
                           r"|verrechnungssteuerfrei", re.IGNORECASE)
_SCHULD_RE = re.compile(r"betrag|kapital|schuld|saldo|hypothek|darlehen",
                        re.IGNORECASE)
_PROZENT_WERT_RE = re.compile(r"(\d{1,2}[.,]\d{1,4})\s?%")


@dataclass
class Seitenobjekt:
    art: str                        # „Konto" oder „Hypothek"
    schluessel: tuple
    bezeichnung: Wert | None = None
    kennung: Wert | None = None
    waehrung: Wert | None = None
    werte: dict[str, list[Wert]] = field(default_factory=dict)
    quellen: list[tuple[str, int]] = field(default_factory=list)


def _zeilen_mit(zeilen, rx, ohne=None):
    return [z for z in zeilen if rx.search(z.text)
            and not (ohne and ohne.search(z.text)) and z.betraege()]


_HYP_NR_RE = re.compile(r"(?:Fest)?[Hh]ypothek(?:\s+Nr\.?)?\s*\|?\s*([0-9][0-9 .]{6,}[0-9])")
_DATUM_RE = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})")


def _datum(text: str) -> str | None:
    m = _DATUM_RE.search(text)
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else None


def hypothekar_nr(text: str) -> str | None:
    """„Festhypothek | 22 0.043.799.09 / CHF" → „22 0.043.799.09"."""
    m = _HYP_NR_RE.search(text)
    return m.group(1).strip() if m else None


def _hypothek_lesen(obj, zeilen, setze) -> None:
    """Schuld, Zins, Zinssatz und Periode einer Hypothek — mit Nachrechnung.

    An den Originalen der Bezirks-Sparkasse festgemacht (261024-jga):
    Schuld = „Saldo am …" (negativ geführt), Zins = „Rechnungsbetrag",
    Zinssatz = Spalte der Zinsrechnung (1.4500 = 1.45 %), Periode =
    „Zinsabrechnung vom … bis …". Jede Zeile der Zinsrechnung lässt sich
    nachrechnen: Kapital × Satz × Tage / 360.
    """
    vorher = None
    for z in zeilen:
        t = z.text
        if re.search(r"Saldo am|Schuld per|Kapital per", t, re.I):
            # Der Betrag steht in derselben Zeile — oder (Schuldenausweis)
            # in der Zeile darüber: „CHF 100'000.00 Festhypothek Nr. …".
            quelle = z if z.betraege() else vorher
            if quelle is not None and quelle.betraege():
                setze("schuld", quelle, abs(quelle.betraege()[0 if quelle is vorher else -1][1]),
                      _datum(t))
        elif re.search(r"Rechnungsbetrag|Zinsbetrag|Total\s+Zins", t, re.I) and z.betraege():
            setze("zins", z, abs(z.betraege()[-1][1]))
        elif re.search(r"Marchzins", t, re.I) and z.betraege():
            setze("marchzins", z, abs(z.betraege()[0][1]))
        m = re.search(r"Zinsabrechnung vom\s+(\S+)\s+bis\s+(\S+)", t)
        if m:
            setze("periode", z, f"{m.group(1)} – {m.group(2)}")
        # Zinsrechnungszeile: Tage, Satz (x.xxxx), Kapital, Betrag
        satz = re.search(r"\|\s*(\d{1,2}\.\d{4})\s*\|", t)
        if satz:
            setze("zinssatz", z, f"{Decimal(satz.group(1)).normalize():f} %")
            zahlen = [b for _, b in z.betraege()]
            tage = next((b for b in zahlen if b == b.to_integral() and 0 < b <= 366), None)
            kapital = next((abs(b) for b in zahlen if abs(b) >= 1000), None)
            if tage and kapital and zahlen:
                soll = kapital * Decimal(satz.group(1)) / 100 * tage / 360
                ist = abs(zahlen[-1])
                setze("_kontrolle", z, abs(soll - ist) <= Decimal("0.5"))
                setze("_zeilenzins", z, ist)
        m = re.search(r"Zinssatz\s+([0-9][0-9.,oO]*)\s*(%|o/o|°/o)", t)
        if m and not satz:
            wert = m.group(1).replace("o", "0").replace("O", "0").replace(",", ".")
            setze("zinssatz", z, f"{wert} %")
        vorher = z
    # Summe der Zinszeilen = Rechnungsbetrag?
    zeilen_z = [w.wert for w in obj.werte.get("_zeilenzins", [])]
    if zeilen_z and obj.werte.get("zins"):
        obj.werte["_summe_stimmt"] = [Wert(
            abs(sum(zeilen_z) - obj.werte["zins"][0].wert) <= Decimal("0.05"))]


# Titelbereich einer Seite: so viele Zeilen von oben.
KOPFZEILEN = 6
# Struktur einer Hypothekenseite neben der Nummer (hypothekar_nr).
_HYP_STRUKTUR_RE = re.compile(r"Kapital\s+per|Schuld\s+per|Hypothek(?:ar)?-?\s*Nr\.?",
                              re.IGNORECASE)


def _ist_hypothek(zeilen: list[Zeile], iban: str | None) -> bool:
    """Eine Hypothekenseite — nicht schon jede Seite mit dem Wort.

    Bisher machte jedes „Hypothek" auf der Seite eine Schuld, auch die
    Buchung „Hypothekarzins" auf einem Privatkonto (261025-kme, B3). Jetzt:
    das Wort steht da UND die Seite hat Hypothek-Struktur (Nummer,
    „Kapital per", „Schuld per", „Hypothek Nr.") — oder das Wort steht im
    Titelbereich und die Seite ist nicht als Konto belegt (gültige IBAN UND
    eine Kontoart-Zeile). „Saldo am" und eine Zinssatz-Spalte zählen nicht
    als Struktur: beide stehen auch auf Kontoseiten.
    """
    if not any(_HYPOTHEK_RE.search(z.text) for z in zeilen):
        return False
    struktur = any(hypothekar_nr(z.text) or _HYP_STRUKTUR_RE.search(z.text) for z in zeilen)
    if struktur:
        return True
    im_kopf = any(_HYPOTHEK_RE.search(z.text) for z in zeilen[:KOPFZEILEN])
    kontoart = any(_KONTOART_RE.search(z.text) and not _KEIN_KOPF_RE.search(z.text)
                   for z in zeilen)
    konto_belegt = bool(iban and iban_gueltig(iban) is None and kontoart)
    return im_kopf and not konto_belegt


def _seite_lesen(dokument: str, seite: int, zeilen: list[Zeile]) -> Seitenobjekt | None:
    text = " ".join(z.text for z in zeilen)
    iban_zeile = next((z for z in zeilen if ibans_in(z.text)), None)
    nummer_zeile = next((z for z in zeilen if _NUMMER_RE.search(z.text)), None)
    iban = ibans_in(iban_zeile.text)[0] if iban_zeile else None
    hypothek = _HYPOTHEK_RE.search(text) if _ist_hypothek(zeilen, iban) else None
    art = "Hypothek" if hypothek else "Konto"

    kennung = None
    if iban and iban_gueltig(iban) is None:
        kennung = Wert(iban, iban_zeile.anker())
    elif nummer_zeile:
        nr = _NUMMER_RE.search(nummer_zeile.text).group(1).strip()
        # Kein Datum, und keine maskierte Nummer aus lauter Nullen.
        if (not re.fullmatch(r"\d{2}\.\d{2}\.\d{2,4}", nr)
                and set(re.sub(r"\D", "", nr)) - {"0"}):
            kennung = Wert(nr, nummer_zeile.anker())

    w = waehrungen_in(text)
    waehrung_zeile = next((z for z in zeilen if waehrungen_in(z.text)), None)
    waehrung = (Wert(w[0], waehrung_zeile.anker()) if w
                else Wert("CHF", None, "annahme"))

    if hypothek:
        hz = next(z for z in zeilen if _HYPOTHEK_RE.search(z.text))
        bezeichnung = Wert(hypothek.group(0).strip().title()
                           if hypothek.group(1) else "Hypothek", hz.anker())
        nr = next((hypothekar_nr(z.text) for z in zeilen if hypothekar_nr(z.text)), None)
        if nr:
            obj_nr = Wert(nr, next(z for z in zeilen if hypothekar_nr(z.text)).anker())
    else:
        kz = next((z for z in zeilen if _KONTOART_RE.search(z.text)
                   and not _KEIN_KOPF_RE.search(z.text)), None)
        bezeichnung = (Wert(_KONTOART_RE.search(kz.text).group(1), kz.anker())
                       if kz else None)
        if bezeichnung and bezeichnung.wert.lower() == "konto":
            m = re.search(r"(\w[\w-]*\s+Konto)\b", kz.text)
            bezeichnung = Wert(m.group(1), kz.anker()) if m else None

    obj = Seitenobjekt(art, (), bezeichnung, kennung, waehrung)
    if hypothek and nr:
        obj.werte["nummer"] = [obj_nr]
    obj.quellen.append((dokument, seite))

    def setze(name, zeile, wert, stichtag=None):
        w = Wert(wert, zeile.anker())
        w.stichtag = stichtag
        obj.werte.setdefault(name, []).append(w)

    if art == "Hypothek":
        _hypothek_lesen(obj, zeilen, setze)
    else:
        saldozeilen = _zeilen_mit(zeilen, _SALDO_RE)
        if saldozeilen:
            bevorzugt = [z for z in saldozeilen
                         if re.search(r"31\.12|nach|per|am\s", z.text, re.I)]
            z = (bevorzugt or saldozeilen)[-1]
            setze("saldo", z, z.betraege()[-1][1])
        zinszeilen = _zeilen_mit(zeilen, _ZINS_RE, _KEIN_ZINS_RE)
        spalte = None if zinszeilen else _aus_spalte(
            zeilen, r"Habenzins|Bruttozins|Zinsertrag|Zinsgutschrift")
        if spalte is not None:
            # Tabellenform: „Habenzins" im Kopf, Betrag in der Zeile „Total".
            setze("zins", spalte[1], spalte[0])
        elif not zinszeilen:
            # „Habenzins" kann fehlen (Scan, graue Zeile): dann trägt die
            # Zeile „ohne/mit Verrechnungssteuer 50.00" den Zins (261024-jga).
            zinszeilen = _zeilen_mit(zeilen, _VST_KOLONNE_RE, _SALDO_RE)
        if spalte is None and zinszeilen:
            brutto = [z for z in zinszeilen if re.search(r"brutto|haben", z.text, re.I)]
            z = (brutto or zinszeilen)[0]
            label_i = next((i for i, c in enumerate(z.zellen)
                            if _ZINS_RE.search(c.text) or _VST_KOLONNE_RE.search(c.text)), -1)
            nach = [b for i, b in z.betraege() if i > label_i]
            setze("zins", z, nach[0] if nach else z.betraege()[-1][1])
        elif (spalte is None and _BESCHEINIGUNG_RE.search(text)
              and obj_hat_saldo(obj)):
            # Eine Zins- und Kapitalbescheinigung nennt den Zins, wenn es
            # einen gibt. Keine Zinszeile heisst: kein Zins (261024-jga).
            tz = next(z for z in zeilen if _BESCHEINIGUNG_RE.search(z.text))
            obj.werte["zins"] = [Wert(Decimal("0.00"), tz.anker(),
                                      KEIN_ZINS_AUSGEWIESEN)]
        if VST_FREI_RE.search(text):
            fz = next(z for z in zeilen if VST_FREI_RE.search(z.text))
            setze("kolonne", fz, "B")
        else:
            vz = _zeilen_mit(zeilen, VST_RE, VST_FREI_RE)
            if vz and vz[-1].betraege()[-1][1] > 0:
                setze("kolonne", vz[-1], "A")
    if not obj.werte:
        return None
    # Eine IBAN, die nicht gültig ist (maskiert, falsch gelesen), wird
    # angezeigt, aber nicht zum Zusammenlegen benutzt.
    if iban and kennung is None:
        obj.kennung = Wert(iban, iban_zeile.anker(), "annahme")
        obj.werte["_iban_ungueltig"] = [Wert(iban_gueltig(iban))]
    return obj


def objekte(dokumente: list[tuple[str, Rohtabelle]]) -> list[Seitenobjekt]:
    """Alle Seiten aller Dokumente → zusammengelegte Objekte.

    Schlüssel: Art + IBAN/Nummer + Währung. Ohne Kennung bleibt die Seite
    ein eigenes Objekt (lieber getrennt als falsch verschmolzen).
    """
    zusammen: dict[tuple, Seitenobjekt] = {}
    for dokument, tabelle in dokumente:
        seiten: dict[int, list[Zeile]] = {}
        for z in tabelle.zeilen:
            seiten.setdefault(z.seite, []).append(z)
        bloecke = []
        for nr, zeilen in seiten.items():
            # Sonderfall: mehrere Hypotheken auf einer Seite (Schuldenausweis)
            # — jeder Block „Hypothek Nr. …" ist ein eigenes Objekt.
            starts = [i for i, z in enumerate(zeilen)
                      if re.search(r"\bHypothek\s+Nr\.?", z.text)]
            if len(starts) >= 2:
                for a, b in zip(starts, starts[1:] + [len(zeilen)]):
                    bloecke.append((nr, zeilen[a:b]))
            else:
                bloecke.append((nr, zeilen))
        for nr, zeilen in bloecke:
            o = _seite_lesen(dokument, nr, zeilen)
            if o is None:
                continue
            kennung = (o.kennung.wert.replace(" ", "").upper()
                       if o.kennung and o.kennung.herkunft != "annahme" else None)
            # Hypotheken über ihre Nummer: so finden Schuldenausweis (ohne
            # IBAN) und Zinsabrechnung (mit IBAN) zueinander.
            if o.art == "Hypothek" and o.werte.get("nummer"):
                kennung = "NR" + re.sub(r"\D", "", str(o.werte["nummer"][0].wert))
            o.schluessel = ((o.art, kennung, o.waehrung.wert) if kennung
                            else (o.art, dokument, nr))
            if o.schluessel in zusammen:
                ziel = zusammen[o.schluessel]
                ziel.quellen += o.quellen
                for name, werte in o.werte.items():
                    ziel.werte.setdefault(name, []).extend(werte)
                ziel.bezeichnung = ziel.bezeichnung or o.bezeichnung
            else:
                zusammen[o.schluessel] = o
    return list(zusammen.values())


def einwert(werte: list[Wert] | None) -> tuple[object, str, dict | None]:
    """(Wert, Herkunft, Anker) — mehrere verschiedene Werte = Widerspruch."""
    if not werte:
        return None, "fehlt", None
    verschieden = []
    for w in werte:
        if w.wert not in verschieden:
            verschieden.append(w.wert)
    if len(verschieden) > 1:
        # Verschiedene Stichtage sind kein Widerspruch: der jüngste gilt
        # (Schuld per 24.05. im Schuldenausweis, per 24.12. in der Abrechnung).
        daten = {w.stichtag for w in werte}
        if None not in daten and len(daten) == len(werte):
            neuester = max(werte, key=lambda w: w.stichtag)
            return neuester.wert, neuester.herkunft, neuester.anker
        return (" / ".join(str(v) for v in verschieden), "widerspruch",
                werte[0].anker)
    return werte[0].wert, werte[0].herkunft, werte[0].anker
