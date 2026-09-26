"""Bestätigte Werte als Regressionsschutz.

Bisher wurde strukturell gemessen: ist ein Anker da, sind die Pflichtfelder
gefüllt, steckt kein PII in der Ausgabe. Nie wurde geprüft, ob die **Zahl
stimmt**. Deshalb konnte monatelang ein Bruttorechnungsbetrag in der Tabelle
stehen, obwohl die Spezifikation ihn namentlich ausschliesst.

Diese Lücke schliesst der Korrekturzirkel: was der Mensch in der
Review-Oberfläche bestätigt oder korrigiert, landet in ``korrekturen.json``
und wird ab dann bei jedem Lauf nachgeprüft. Weicht ein späterer Lauf ab,
schlägt er fehl — auch wenn Anker und Struktur einwandfrei aussehen.

Privacy: die Berichte nennen Dokument, Zielwert und Status, aber **niemals
einen Betrag**. Ein Abweichungsbericht sagt „weicht ab", nicht „6300 statt
9300". Damit bleibt er teilbar.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Abgleich:
    """Ergebnis eines Soll-Ist-Vergleichs für einen bestätigten Wert."""

    beleg: str
    zielwert: str
    zustand: str   # "stimmt" | "weicht_ab" | "fehlt" | "andernorts"
    # Warum "fehlt"? Drei sehr verschiedene Ursachen sahen bisher gleich aus,
    # und nur eine davon ist verlorene Arbeit (260923-dua):
    #   "unbekannte_bezeichnung" — das Dokument kennt den Zielwert gar nicht
    #   "ohne_betrag"            — die Zeile ist da, hat aber keinen Betrag
    #   "kein_dokument"          — zu diesem Dokument gibt es keine Zeilen
    grund: str = ""


def lade(pfad: Path) -> dict[str, dict]:
    """Liest ``korrekturen.json``. Fehlt sie, gibt es nichts zu prüfen."""
    if not pfad.exists():
        return {}
    try:
        daten = json.loads(pfad.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return daten if isinstance(daten, dict) else {}


# Umbenannte Zielwerte: neue Bezeichnung -> frueher gebrauchte.
#
# Wird eine Bezeichnung im Feldvertrag praeziser gefasst, zeigen alle
# bestehenden Schluessel ins Leere. Das ist an EINER Stelle abgefangen worden
# und an drei weiteren nicht — mit der Folge, dass die Oberflaeche die
# Bestaetigungen nicht mehr fand und sie beim naechsten Speichern verschwanden
# (260904-rmx). Die Zuordnung gehoert deshalb hierher, wo sie jeder Aufrufer
# sieht.
ALTE_BEZEICHNUNGEN: dict[str, tuple[str, ...]] = {
    "Bruttoertrag ohne Verrechnungssteuer": (
        "Bruttozins / Bruttoertrag", "Bruttoertrag Wertschriften",
        "Bruttoertrag", "Bruttozins",
    ),
    "Bruttoertrag mit Verrechnungssteuer": ("Verrechnungssteuer 35%",),
    # Der Feldvertrag und die Uebertragungstabelle benannten dieselben Felder
    # verschieden ("Saldo 31.12." gegen "Vermögensstand 31.12."). Jede
    # Pruefung, die beide vergleicht, war dadurch blind. Seit die Tabelle die
    # Bezeichnungen des Vertrags uebernimmt, brauchen die alten einen Alias
    # (260904-rmx).
    "Saldo 31.12.": ("Vermögensstand 31.12.",),
    "Steuerwert 31.12.": ("Vermögensstand 31.12.",),
    "Prämie Grundversicherung KVG": ("Prämie KVG (Grundversicherung)",),
    "Prämie Zusatzversicherung VVG": ("Prämie VVG (Zusatzversicherung)",),
    "Nettolohn": ("Nettolohn (Pos. 11)",),
}


# Mehrere Positionen derselben Art in EINEM Dokument.
#
# Ein Hypothekarbeleg nennt drei Hypotheken und drei Schuldzinsen. Der
# Schluessel "<Beleg>|<Zielwert>" kann davon genau eine fassen: die zweite
# ueberschrieb die erste, und eine von Hand erfasste zweite Position fiel beim
# Tabellenbau ganz weg, weil der Schluessel schon belegt war — ohne Meldung
# (260923-dua).
#
# Deshalb traegt jede weitere Position ihre laufende Nummer am Zielwert-Teil:
# "<Beleg>|<Zielwert>#2". Die erste bleibt ohne Zusatz, damit alle bereits
# bestaetigten Eintraege ihren Schluessel behalten.
#
# Die Nummer sitzt bewusst NICHT als drittes Segment hinter einem zweiten
# "|": jeder Leser im Projekt zerlegt mit partition("|") und bekaeme sonst
# einen abgeschnittenen Zielwert. Gebaut und zerlegt wird ausschliesslich
# hier.
def baue_schluessel(beleg: str, zielwert: str, pos: int = 1) -> str:
    """Der Schluessel einer Position — ``pos`` zaehlt ab 1."""
    try:
        n = int(pos)
    except (TypeError, ValueError):
        n = 1
    return f"{beleg}|{zielwert}" if n <= 1 else f"{beleg}|{zielwert}#{n}"


# Frueher trugen zusaetzliche Konten desselben Belegs eine eigene Bezeichnung
# statt einer Positionsnummer — der Schluessel konnte nur eine Position je
# Zielwert fassen. Bestehende Bestaetigungen liegen noch unter diesen Namen und
# muessen ihre Zeile weiter finden (260923-dua).
ALTE_POSITIONSNAMEN: dict[str, tuple[str, int]] = {
    "Vermögensstand 31.12. (Konto 2)": ("Saldo 31.12.", 2),
    "Vermögensstand 31.12. (Konto 3)": ("Saldo 31.12.", 3),
}


def zerlege(schluessel_text: str) -> tuple[str, str, int]:
    """``"a.pdf|Zins#2"`` -> ``("a.pdf", "Zins", 2)``.

    Ein ``#`` im Zielwert selbst soll unangetastet bleiben. Die Regel dafuer
    ist bewusst eng gefasst — ``"Konto #1234"`` ist eine Kontonummer und keine
    Position 1234:

    * kein Leerzeichen vor dem ``#``,
    * eine bis drei Ziffern, ohne fuehrende Null,
    * Wert mindestens 2 (die erste Position traegt nie einen Zusatz).

    Es bleibt eine Restunschaerfe: ein Zielwert, der woertlich auf ``#12``
    endet, wuerde als Position gelesen. Deshalb weist die Oberflaeche eine
    solche Eingabe ab (:func:`ist_gueltiger_zielwert`).
    """
    beleg, _, rest = str(schluessel_text or "").partition("|")
    if rest in ALTE_POSITIONSNAMEN:
        name, pos = ALTE_POSITIONSNAMEN[rest]
        return beleg, name, pos
    kopf, marke, nr = rest.rpartition("#")
    if (marke and kopf and not kopf[-1].isspace()
            and nr.isdigit() and not nr.startswith("0")
            and len(nr) <= 3 and int(nr) >= 2):
        return beleg, kopf, int(nr)
    return beleg, rest, 1


def ist_gueltiger_zielwert(zielwert: str) -> bool:
    """Falsch, wenn die Bezeichnung mit der Positionsnummer verwechselbar ist.

    Die Oberflaeche laesst freie Bezeichnungen zu. Eine, die auf ``#2`` endet,
    waere von der laufenden Nummer nicht zu unterscheiden — dann zeigten zwei
    verschiedene Werte auf denselben Schluessel.
    """
    text = str(zielwert or "")
    return zerlege(f"x|{text}") == ("x", text, 1)


def schluessel_kandidaten(beleg: str, zielwert: str,
                          pos: int = 1) -> list[str]:
    """Alle Schluessel, unter denen dieser Wert gespeichert sein kann."""
    namen = [zielwert, *ALTE_BEZEICHNUNGEN.get(zielwert, ())]
    aus = [baue_schluessel(beleg, n, pos) for n in namen]
    # Und die frueheren Bezeichnungen zusaetzlicher Konten, die ihre Position
    # im Namen trugen statt als Nummer.
    aus += [f"{beleg}|{alt}" for alt, (n, p) in ALTE_POSITIONSNAMEN.items()
            if n == zielwert and p == pos]
    return aus


def ist_zurueckgenommen(eintrag) -> bool:
    """Ein Grabstein — der Mensch wollte diesen Eintrag ausdrücklich nicht mehr.

    Früher wurde der Schlüssel dafür entfernt. Ein entfernter Schlüssel ist
    aber Abwesenheit, und Abwesenheit überlebt keine Zusammenführung: die
    zweite Ablage oder eine Sicherung im Verlauf brachte ihn zurück, und
    ``make korrekturen-zurueck`` machte jede Rücknahme zunichte (260923-dua).
    """
    return isinstance(eintrag, dict) and bool(eintrag.get("zurueckgenommen"))


def ist_gestrichen(eintrag) -> bool:
    """„Dieses Dokument weist den Wert gar nicht aus."

    Kein Betragsfehler und keine Aussage über die Steuererklärung — der Wert
    kann in einem anderen Dokument stehen und dort richtig sein.
    """
    return isinstance(eintrag, dict) and bool(eintrag.get("entfernt"))


def gilt_als_bestaetigt(eintrag) -> bool:
    """Hat ein Mensch diesen Betrag im Original gesehen?

    Zwei Wege führen dahin, und beide zählen:

    * Er hat den extrahierten Wert geprüft und abgehakt (``bestaetigt_betrag``).
    * Er hat ihn **selbst eingetippt** (``neu`` mit einem ``soll``). Wer einen
      Betrag von Hand erfasst, hat ihn im Beleg gelesen — ihn danach noch
      einmal abhaken zu lassen ist Bürokratie, und genau das liess drei
      Dokumente dauerhaft als „ungeprüft" dastehen (260923-dua).

    Eine gestrichene oder zurückgenommene Position zählt nicht.
    """
    if not isinstance(eintrag, dict):
        return False
    if ist_zurueckgenommen(eintrag) or ist_gestrichen(eintrag):
        return False
    if eintrag.get("bestaetigt_betrag"):
        return True
    return bool(eintrag.get("neu") and str(eintrag.get("soll") or "").strip())


def finde(korrekturen: dict[str, dict], beleg: str, zielwert: str,
          pos: int = 1) -> dict | None:
    """Den Eintrag zu einer Zeile — auch unter einer frueheren Bezeichnung.

    Zurückgenommene Einträge gelten als nicht vorhanden.
    """
    for k in schluessel_kandidaten(beleg, zielwert, pos):
        eintrag = korrekturen.get(k)
        if isinstance(eintrag, dict) and not ist_zurueckgenommen(eintrag):
            return eintrag
    return None


def ausgeschlossene_dokumente(korrekturen: dict[str, dict]) -> dict[str, str]:
    """Dokumente, die der Mensch ausdruecklich als erledigt abgehakt hat.

    Er setzt die Belegart auf „irrelevant" oder auf eine andere — in beiden
    Faellen liegt der Eintrag unter ``"<Beleg>|<Belegart>"``. Die Auswertung
    dieser Schluessel stand bisher nur in ``scripts/bereitschaft.py``; die
    Oberflaeche kannte sie nicht und zeigte abgehakte Dokumente weiter als
    offen. Eine Frage, zwei Antworten — deshalb hierher (260923-dua).

    Returns:
        Beleg -> gesetzte Belegart (``"irrelevant"`` oder ein Belegtyp).
    """
    from extractors.zielwerte import ZIELWERTE

    erlaubt = set(ZIELWERTE) | {"irrelevant"}
    aus: dict[str, str] = {}
    for k, eintrag in (korrekturen or {}).items():
        beleg, wert, _ = zerlege(k)
        if (wert in erlaubt and isinstance(eintrag, dict)
                and not ist_zurueckgenommen(eintrag)):
            aus[beleg] = wert
    return aus


def lade_alle(samples_dir: Path) -> dict[str, dict]:
    """Alle Korrekturen zu einem Lauf — aus beiden möglichen Ablagen.

    Es gibt zwei Wege, wie Korrekturen entstehen, und sie landen an
    verschiedenen Orten:

    * ``apply_korrekturen`` schreibt neben die CSV, also nach
      ``output/latest/korrekturen.json``.
    * Der Live-Server schreibt in den Sample-Ordner, also nach
      ``output/latest/json/korrekturen.json``.

    Die Aufrufer nahmen bisher ``lade(a) or lade(b)`` — das ist kein „beide",
    sondern „die erste, die es gibt". Sobald der Server einmal gespeichert
    hatte, war die ganze über die CSV eingespielte Arbeit unsichtbar. Ohne
    Fehlermeldung: die Tabelle sah einfach wieder aus wie vor der Durchsicht.

    Deshalb zusammenführen. Bei gleichem Schlüssel gewinnt der Sample-Ordner —
    er ist der Ort, an dem die laufende Sitzung schreibt, also der jüngere
    Stand.

    **Feldweise, nicht als Ganzes.** ``dict.update`` ersetzte den ganzen
    Eintrag: hatte der CSV-Weg eine notierte Beschriftung hinterlegt und die
    laufende Sitzung berührte dieselbe Zeile, war die Beschriftung weg. Auch
    hier gilt die Regel des Hauses — ergänzen, nie stillschweigend entfernen
    (260923-dua).
    """
    zusammen: dict[str, dict] = {}
    for pfad in (samples_dir.parent / "korrekturen.json",
                 samples_dir / "korrekturen.json"):
        for schluessel, eintrag in lade(pfad).items():
            if not isinstance(eintrag, dict):
                continue
            vorher = zusammen.get(schluessel)
            if not isinstance(vorher, dict):
                zusammen[schluessel] = dict(eintrag)
                continue
            neu = dict(vorher)
            for feld, wert in eintrag.items():
                # Ein leerer Wert verdrängt keinen vorhandenen — das wäre
                # wieder „Schweigen heisst löschen". Ausdrückliche Negationen
                # (``entfernt: False``) sind keine leeren Werte: sie tragen
                # eine Aussage und müssen durchkommen.
                if wert in (None, "") and feld in neu:
                    continue
                neu[feld] = wert
            zusammen[schluessel] = neu
    return zusammen


def _normalisiere(wert) -> str:
    """Vergleichsform eines Betrags — Apostroph, Leerzeichen, Komma egal."""
    s = str(wert or "").strip()
    s = s.replace("'", "").replace("’", "").replace(" ", "").replace(",", ".")
    # "1000" und "1000.00" sind derselbe Betrag.
    if s.endswith(".00"):
        s = s[:-3]
    return s


def vergleiche(korrekturen: dict[str, dict],
               zeilen: list[dict]) -> list[Abgleich]:
    """Prüft jeden bestätigten Sollwert gegen den aktuellen Lauf.

    Args:
        korrekturen: Inhalt von ``korrekturen.json``, Schlüssel
            ``"<beleg>|<zielwert>"``.
        zeilen: Zeilen des aktuellen Laufs mit ``pdf_name``/``beleg``,
            ``beschreibung``/``zielwert`` und ``betrag``.

    Returns:
        Ein :class:`Abgleich` je Eintrag mit hinterlegtem ``soll``. Einträge
        ohne ``soll`` (nur eine notierte Beschriftung) werden übersprungen —
        sie sind ein Hinweis für eine Regel, kein Sollwert.
    """
    ist: dict[str, str] = {}
    for z in zeilen:
        beleg = str(z.get("pdf_name") or z.get("beleg") or "")
        ziel = str(z.get("beschreibung") or z.get("zielwert") or "")
        if beleg and ziel:
            ist[baue_schluessel(beleg, ziel, z.get("pos") or 1)] = \
                _normalisiere(z.get("betrag"))

    # Ein Eintrag kann unter einer frueheren Bezeichnung gespeichert sein.
    # Ohne diesen Umweg meldet der Abgleich „fehlt" fuer jede umbenannte
    # Zeile — sechs Stueck nach der Wertschriften-Umbenennung, und es sah
    # aus wie verlorene Arbeit statt wie ein Namensunterschied (260904-rmx).
    neu_zu_alt: dict[str, list[str]] = {}
    for neu_name, alte in ALTE_BEZEICHNUNGEN.items():
        for a in alte:
            neu_zu_alt.setdefault(a, []).append(neu_name)

    # Welche Positionen gibt es je (Beleg, Bezeichnung)? Fuer den Fall, dass
    # sich die Nummer verschoben hat.
    positionen: dict[tuple[str, str], list[int]] = {}
    for z in zeilen:
        b = str(z.get("pdf_name") or z.get("beleg") or "")
        n = str(z.get("beschreibung") or z.get("zielwert") or "")
        if b and n:
            positionen.setdefault((b, n), []).append(int(z.get("pos") or 1))

    def _ist_wert(beleg: str, ziel: str, pos: int) -> str | None:
        kandidaten = [ziel, *neu_zu_alt.get(ziel, [])]
        for kandidat in kandidaten:
            wert = ist.get(baue_schluessel(beleg, kandidat, pos))
            if wert:
                return wert
        # Nicht an dieser Position — aber vielleicht an einer anderen.
        #
        # Ein bestaetigter Wert, der im selben Dokument unter demselben
        # Zielwert an anderer Stelle steht, ist nicht verloren; die Nummer hat
        # sich verschoben. Ihn als "fehlt" zu melden, treibt den Menschen auf
        # die Suche nach etwas, das da ist (260923-dua).
        for kandidat in kandidaten:
            for andere in sorted(positionen.get((beleg, kandidat), [])):
                if andere == pos:
                    continue
                wert = ist.get(baue_schluessel(beleg, kandidat, andere))
                if wert:
                    return wert
        return None

    # Werte eines abgehakten Dokuments sind keine Sollwerte mehr — sonst
    # meldet die Pruefung genau das als "fehlt", was der Mensch gerade
    # ausdruecklich aus der Steuererklaerung genommen hat (260923-dua).
    belege_mit_zeilen = {b for (b, _) in positionen}

    # Welche Betraege stehen je Dokument in der Tabelle? Damit laesst sich
    # unterscheiden, ob eine Bestaetigung ohne passende Zeile verlorene Arbeit
    # ist oder blosser Rueckstand: steht die Zahl unter anderem Namen im
    # selben Dokument, ist sie nicht weg — nur der Schluessel ist veraltet.
    betraege_je_beleg: dict[str, set[str]] = {}
    for z in zeilen:
        b = str(z.get("pdf_name") or z.get("beleg") or "")
        w = _normalisiere(z.get("betrag"))
        if b and w:
            betraege_je_beleg.setdefault(b, set()).add(w)

    irrelevant = {b for b, art in ausgeschlossene_dokumente(korrekturen).items()
                  if art == "irrelevant"}

    out: list[Abgleich] = []
    for k, eintrag in sorted(korrekturen.items()):
        if zerlege(k)[0] in irrelevant:
            continue
        # Ein Grabstein und eine bewusste Streichung sind keine Sollwerte.
        #
        # Vorher meldete der Abgleich beide als „fehlt": der Mensch strich
        # eine faelschlich erkannte Position, und die Pruefung warf ihm genau
        # das als Rueckschritt vor. Ein Pruefpunkt, der die richtige Handlung
        # bestraft, wird nie wieder gruen (260923-dua).
        if ist_zurueckgenommen(eintrag) or ist_gestrichen(eintrag):
            continue
        soll = eintrag.get("soll") if isinstance(eintrag, dict) else None
        if soll is None:
            continue
        beleg, ziel, pos = zerlege(k)
        gefunden = _ist_wert(beleg, ziel, pos)
        # Wenn nichts gefunden wurde: woran lag es? Ohne diese Unterscheidung
        # schickt der Bericht den Menschen auf die Suche nach einem Wert, der
        # entweder nie zu diesem Dokument gehoerte oder schlicht keinen Betrag
        # hat — beides keine verlorene Arbeit.
        grund = ""
        if gefunden is None:
            kandidaten = [ziel, *neu_zu_alt.get(ziel, [])]
            if beleg not in belege_mit_zeilen:
                grund = "kein_dokument"
            elif any((beleg, k) in positionen for k in kandidaten):
                grund = "ohne_betrag"
            else:
                # Was bietet das Dokument stattdessen an? Die Bezeichnungen
                # stammen aus dem Feldvertrag und sind teilbar — sie zeigen
                # sofort, ob die Bestaetigung am falschen Beleg haengt oder
                # ob der Zielwert schlicht verschwunden ist.
                da = sorted({n for (b_, n) in positionen if b_ == beleg})
                grund = "unbekannte_bezeichnung"
                if da:
                    grund += ": stattdessen " + ", ".join(da[:6])
        # Im Bericht die Position mitnennen — sonst stehen bei drei Hypotheken
        # dreimal dieselben zwei Worte da, und man weiss nicht, welche gemeint
        # ist.
        name = ziel if pos <= 1 else f"{ziel} (Position {pos})"
        if gefunden is None:
            # Die Zahl steht im selben Dokument, nur unter einem anderen
            # Namen: kein Rueckschritt, sondern ein veralteter Schluessel.
            # Als "fehlt" gemeldet schickte das den Menschen auf die Suche
            # nach einem Wert, den er laengst in der Tabelle hat (260923-dua).
            if _normalisiere(soll) in betraege_je_beleg.get(beleg, set()):
                out.append(Abgleich(beleg, name, "andernorts", grund))
            else:
                out.append(Abgleich(beleg, name, "fehlt", grund))
        elif gefunden == _normalisiere(soll):
            out.append(Abgleich(beleg, name, "stimmt"))
        else:
            out.append(Abgleich(beleg, name, "weicht_ab"))
    return out


def bericht(abgleiche: list[Abgleich]) -> tuple[str, bool]:
    """Teilbarer Bericht ohne Betraege. Returns ``(text, ist_sauber)``."""
    if not abgleiche:
        return ("Keine bestätigten Sollwerte hinterlegt — nichts zu prüfen. "
                "In der Review-Oberfläche Werte bestätigen oder korrigieren, "
                "dann greift der Schutz ab dem nächsten Lauf.", True)

    zaehler = {"stimmt": 0, "weicht_ab": 0, "fehlt": 0}
    for a in abgleiche:
        zaehler[a.zustand] += 1

    zeilen = [f"{len(abgleiche)} bestätigte Sollwerte geprüft: "
              f"{zaehler['stimmt']} stimmen, {zaehler['weicht_ab']} weichen ab, "
              f"{zaehler['fehlt']} fehlen im Lauf."]
    for a in abgleiche:
        if a.zustand == "stimmt":
            continue
        wort = "weicht ab" if a.zustand == "weicht_ab" else "fehlt"
        zeilen.append(f"  [{wort}] {a.beleg} — {a.zielwert}")
    if zaehler["weicht_ab"] or zaehler["fehlt"]:
        zeilen.append("(Betraege werden bewusst nicht ausgegeben — "
                      "der Bericht bleibt teilbar.)")
    return "\n".join(zeilen), not (zaehler["weicht_ab"] or zaehler["fehlt"])


class SchutzVerletzt(Exception):
    """Ein Schreibvorgang haette bestaetigte Arbeit verloren.

    Wird geworfen, bevor irgendetwas auf die Platte geht. Die alte Datei
    bleibt unberuehrt.
    """

    def __init__(self, verloren: list[str]) -> None:
        self.verloren = verloren
        super().__init__(
            f"{len(verloren)} bestaetigte(r) Eintrag/Eintraege waeren "
            f"verschwunden")


def pruefe_schutz(vorher: dict, nachher: dict) -> list[str]:
    """Welche bestaetigte Arbeit ginge bei diesem Schreibvorgang verloren?

    Der Grundsatz, den dieses Projekt dreimal teuer gelernt hat: **ein
    bestaetigter Wert verschwindet nie von selbst.** Er darf sich aendern, er
    darf zurueckgenommen werden — aber er darf nicht einfach weg sein. Wo das
    doch passierte, war es immer ein Fehler im Programm, nie ein Wunsch des
    Menschen: eine Umbenennung, die den Bezug abriss; eine Aufraeum-Regel, die
    die falsche Kopie behielt; eine Zusammenfuehrung, in der die Abwesenheit
    gewann (260923-dua).

    Zurueck kommt die Liste der Schluessel, die verschwinden wuerden. Leer
    heisst: der Schreibvorgang ist unbedenklich.

    Ausdrueckliche Ruecknahme ist erlaubt — sie hinterlaesst einen Grabstein
    (``zurueckgenommen``) oder eine Streichung (``entfernt``). Beides ist ein
    Eintrag, kein Loch.
    """
    verloren = []
    for schluessel, alt in vorher.items():
        if not isinstance(alt, dict):
            continue
        if not gilt_als_bestaetigt(alt):
            continue
        if ist_zurueckgenommen(alt) or ist_gestrichen(alt):
            continue
        if schluessel in nachher:
            continue
        verloren.append(schluessel)
    return sorted(verloren)
