"""Sprechende Dateinamen für Steuerbelege.

Ein Beleg heisst nach dem Download ``2025_tax_statement4.9.2026155454.pdf``.
Das Jahr steht vorn, der Rest ist der Zeitstempel des Downloads — welche Bank,
welche Person, welche Art von Papier, sagt der Name nicht. Wer ein Jahr später
etwas sucht, muss jede Datei öffnen.

Sobald in der Durchsicht **Aussteller und Belegart** feststehen — und das ist
der Moment, in dem ein Mensch hingesehen hat —, lässt sich ein Name bilden,
der die Frage beantwortet::

    BANK-U - Vermögensausweis Zinsausweis - Erika Muster 2025.pdf

Das Schema stammt vom Nutzer:

    ``<Aussteller> - <Dokumenttypen> - <Person> <Jahr>``

**Mehrere Inhalte werden alle genannt.** Ein Bankbeleg, der sowohl den Saldo
per 31.12. als auch den Zinsertrag ausweist, ist beides — Vermögensausweis
*und* Zinsausweis. Wer später den Zinsausweis sucht, soll ihn am Namen
erkennen, auch wenn das Dokument vor allem ein Vermögensausweis ist.

Der Dokumenttyp kommt deshalb **nicht** aus der Belegart, sondern aus den
Zielwerten, die im Dokument tatsächlich gefunden wurden.

Umlaute bleiben stehen: „Vermögensausweis" ist der richtige Name, und
macOS wie Linux kommen damit zurecht. Entfernt wird nur, was einen Pfad
zerlegen oder ein Werkzeug verwirren könnte.

Dieses Modul rechnet **nur** — es fasst keine Datei an. Das Umbenennen
mitsamt Journal und Rückweg steht in ``scripts/umbenennen.py``.
"""
from __future__ import annotations

import re

# Zielwert aus dem Feldvertrag -> wie der Inhalt im Dateinamen heisst.
#
# Mehrere Zielwerte koennen auf dasselbe Wort zeigen: „Bruttoertrag mit" und
# „ohne Verrechnungssteuer" sind beide der Zinsausweis, und er soll nicht
# doppelt im Namen stehen.
TYP_AUS_ZIELWERT: dict[str, str] = {
    "Saldo 31.12.": "Vermögensausweis",
    "Steuerwert 31.12.": "Vermögensausweis",
    "Bruttoertrag ohne Verrechnungssteuer": "Zinsausweis",
    "Bruttoertrag mit Verrechnungssteuer": "Zinsausweis",
    "Nettolohn": "Lohnausweis",
    "Prämie Grundversicherung KVG": "Prämienbescheinigung",
    "Prämie Zusatzversicherung VVG": "Prämienbescheinigung",
    "Prämie Total (nicht aufgeteilt)": "Prämienbescheinigung",
    "Selbst getragene Krankheits- und Unfallkosten": "Kostenübersicht",
    "Einzahlung Säule 3a": "Säule 3a",
    "Schuldzinsen Hypothek": "Zinsausweis",
    "Hypothekarschuld 31.12.": "Schuldenausweis",
    "Kosten Kinderbetreuung": "Betreuungskosten",
}

# Fallback, wenn kein einziger Zielwert bekannt ist — dann sagt wenigstens die
# Belegart, worum es geht.
TYP_AUS_BELEGART: dict[str, str] = {
    "lohnausweis": "Lohnausweis",
    "bank_zinsausweis": "Bankbeleg",
    "wertschriftenverzeichnis": "Wertschriftenverzeichnis",
    "kk_praemienbescheinigung": "Prämienbescheinigung",
    "krankheitskosten": "Kostenübersicht",
    "saeule_3a": "Säule 3a",
    "spenden": "Spendenbescheinigung",
    "kinderbetreuung": "Betreuungskosten",
    "berufsauslagen": "Berufsauslagen",
    "weiterbildung": "Weiterbildung",
    "hypothek_zinsbestaetigung": "Hypothek",
    "liegenschaftsunterhalt": "Liegenschaft",
    "irrelevant": "Nicht steuerrelevant",
}

# Reihenfolge der Typen im Namen — nicht alphabetisch, sondern so, wie man
# einen Beleg beschreiben wuerde: erst der Bestand, dann der Ertrag.
TYP_REIHENFOLGE = (
    "Lohnausweis", "Vermögensausweis", "Wertschriftenverzeichnis",
    "Zinsausweis", "Schuldenausweis", "Hypothek", "Säule 3a",
    "Prämienbescheinigung", "Kostenübersicht", "Betreuungskosten",
    "Spendenbescheinigung", "Berufsauslagen", "Weiterbildung",
    "Liegenschaft", "Bankbeleg", "Nicht steuerrelevant",
)

MAX_AUSSTELLER = 30
MAX_NAME = 150

# Was einen Pfad zerlegt oder Werkzeuge verwirrt. Umlaute gehoeren NICHT dazu.
_VERBOTEN = re.compile(r'[/\\:*?"<>|\x00-\x1f]')

# Rechtsformen und Zusaetze, die keinen Beleg vom anderen unterscheiden.
_BALLAST = re.compile(
    r"\b(AG|SA|GmbH|Sàrl|Sarl|Ltd|Inc|Genossenschaft|Vorsorgestiftung|"
    r"Stiftung|Gruppe|Group|Holding|Zweigniederlassung|Niederlassung|"
    r"Schweiz|Switzerland)\b\.?", re.IGNORECASE)


def entschaerfe(text: str) -> str:
    """Ein Textstück, das in einem Dateinamen nichts kaputt macht.

    Umlaute bleiben. Weg müssen Schrägstriche und Doppelpunkte — mit ihnen
    liesse sich ein Pfad unterschieben — sowie Steuerzeichen. Mehrfache
    Leerzeichen werden zu einem; der Bindestrich mit Leerzeichen drumherum
    ist das Trennzeichen des Schemas und darf innerhalb eines Teils nicht
    vorkommen.
    """
    s = _VERBOTEN.sub(" ", str(text or ""))
    s = s.replace(" - ", " ")
    return re.sub(r"\s+", " ", s).strip(" .-")


def aussteller_kurz(aussteller: str) -> str:
    """Der unterscheidende Teil eines Ausstellernamens.

    „Bank X AG, Zweigniederlassung Zürich" und „Bank X AG" sind dasselbe
    Institut; die Rechtsform und der Zusatz unterscheiden keinen Beleg.
    """
    roh = str(aussteller or "").split(",")[0]
    ohne = entschaerfe(_BALLAST.sub(" ", roh))
    ohne = ohne or entschaerfe(roh)
    if len(ohne) <= MAX_AUSSTELLER:
        return ohne
    return ohne[:MAX_AUSSTELLER].rsplit(" ", 1)[0] or ohne[:MAX_AUSSTELLER]


def typen_aus_zielwerten(zielwerte, belegart: str | None = None) -> list[str]:
    """Welche Dokumenttypen stecken in diesem Beleg?

    Ein Bankbeleg mit Saldo **und** Zinsertrag ist Vermögensausweis und
    Zinsausweis — beide gehören in den Namen. Doppelte fallen weg, die
    Reihenfolge ist die von :data:`TYP_REIHENFOLGE`.
    """
    gefunden: set[str] = set()
    for z in zielwerte or ():
        wort = TYP_AUS_ZIELWERT.get(str(z or "").strip())
        if wort:
            gefunden.add(wort)
    if not gefunden and belegart:
        wort = TYP_AUS_BELEGART.get(str(belegart))
        if wort:
            gefunden.add(wort)
    rang = {w: i for i, w in enumerate(TYP_REIHENFOLGE)}
    return sorted(gefunden, key=lambda w: (rang.get(w, 99), w))


def baue_dateiname(*, aussteller: str | None, typen, person: str | None,
                   jahr: str | int | None, endung: str = ".pdf") -> str:
    """Der sprechende Name nach dem Schema des Nutzers.

    ``<Aussteller> - <Typen> - <Person> <Jahr>``

    Fehlt ein Teil, fällt er samt Trennzeichen weg — kein „ - - ". Bleibt
    nichts übrig, ``""``; dann wird nicht umbenannt.

    Args:
        typen: Liste von Dokumenttypen, z.B. aus :func:`typen_aus_zielwerten`.
        person: Klarname. Was der Aufrufer übergibt, steht im Namen — dieses
            Modul schlägt keine Rolle in einen Namen um.
    """
    a = aussteller_kurz(aussteller or "")
    t = " ".join(entschaerfe(x) for x in (typen or []) if entschaerfe(x))
    p = entschaerfe(person or "")
    j = entschaerfe(str(jahr or ""))
    hinten = " ".join(x for x in (p, j) if x)

    teile = [x for x in (a, t, hinten) if x]
    if not teile:
        return ""
    kern = " - ".join(teile)
    if len(kern) > MAX_NAME:
        kern = kern[:MAX_NAME].rsplit(" ", 1)[0].rstrip(" -")
    return kern + endung


def ist_schon_sprechend(name: str, vorschlag: str) -> bool:
    """Heisst die Datei bereits so — bis auf eine angehängte Nummer?

    Ein zweiter Lauf soll aus ``… 2025.pdf`` nicht ``… 2025-2.pdf`` machen.
    """
    if not vorschlag:
        return True
    stamm = vorschlag.rsplit(".", 1)[0]
    ist = str(name or "").rsplit(".", 1)[0]
    return ist == stamm or re.fullmatch(re.escape(stamm) + r"-\d+",
                                        ist) is not None


def freier_name(vorschlag: str, belegt: set[str]) -> str:
    """Hängt ``-2``, ``-3`` an, bis der Name frei ist.

    Zwei Belege derselben Bank, derselben Person und desselben Jahres gibt es
    wirklich — eine Quartals- und eine Jahresabrechnung. Überschrieben wird
    niemals etwas.
    """
    if not vorschlag:
        return ""
    if vorschlag not in belegt:
        return vorschlag
    stamm, _, endung = vorschlag.rpartition(".")
    n = 2
    while f"{stamm}-{n}.{endung}" in belegt:
        n += 1
    return f"{stamm}-{n}.{endung}"
