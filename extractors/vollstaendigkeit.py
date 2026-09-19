"""Vollständigkeitsprüfung: was fehlt, obwohl es da sein müsste.

Der Anker-Mechanismus prüft, ob ein *gefundener* Wert stimmt. Er kann nicht
merken, dass etwas **gar nicht** gefunden wurde. Genau das ist bei einer
Steuererklärung der teurere Fehler: ein vergessener Abzug fällt niemandem auf.

Zwei Klassen von Regeln:

**Familienabdeckung.** Die Grundversicherung (KVG) ist in der Schweiz für jede
Person obligatorisch — auch für Kinder. Fehlt für ein Familienmitglied die
KVG-Prämie, fehlt entweder ein Dokument oder die Person wurde nicht erkannt.
Beides muss auffallen. Die Zusatzversicherung (VVG) ist freiwillig und wird
deshalb nicht eingefordert.

**Dokument-Konsistenz.** Ein Krankenkassen-Jahresauszug, der selbst getragene
Kosten ausweist, aber keine Prämie, ist in sich unstimmig — wer Kosten
abrechnet, ist versichert und zahlt Prämie. Steht sie nicht in der Tabelle,
wurde sie übersehen.

Alle Befunde tragen einen stabilen Code, damit sie maschinell weiterverarbeitet
und in Tests festgenagelt werden können.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field as _field

# Rollen, für die eine Grundversicherung existieren muss.
KVG_PFLICHT_ROLLEN: tuple[str, ...] = (
    "elternteil_1", "elternteil_2", "kind_1", "kind_2",
)

# Rollen, für die ein Lohnausweis und eine Säule-3a-Bescheinigung erwartet
# werden (User-Vorgabe: beide Erwachsenen sind erwerbstätig und zahlen ein).
# Bewusst eng gefasst — eine Warnung, die auf eine nicht erwerbstätige Person
# zeigt, wäre Rauschen.
ERWERBS_ROLLEN: tuple[str, ...] = ("elternteil_1", "elternteil_2")


@dataclass(frozen=True)
class Befund:
    """Ein Vollständigkeits- oder Konsistenzbefund.

    Attributes:
        code: stabiler Bezeichner, z.B. ``kvg_fehlt``.
        schwere: ``fehlt`` (etwas ist nicht da) oder ``unstimmig``
            (etwas widerspricht sich).
        text: Klartext für die Anzeige.
        betrifft: Rolle oder Dokumentname, worauf sich der Befund bezieht.
    """

    code: str
    schwere: str
    text: str
    betrifft: str = ""

    @property
    def betrifft_neutral(self) -> str:
        """``betrifft`` in einer Form, die auf die Konsole darf.

        Rollen (``elternteil_1``, ``kind_2``) sind generisch und dürfen
        überall hin. Dokumentnamen sind es nicht: Steuerbelege heissen nach
        den Menschen, um die es geht — „Steuerauszug 2025 <Vorname
        Nachname>.pdf" ist ein Personendatum, und es stand in einer Ausgabe,
        die als teilbar beschrieben war (260904-rmx).

        In der Oberfläche wird weiterhin der volle Name gezeigt: sie läuft
        lokal und der Mensch muss wissen, welches Dokument gemeint ist.
        """
        if not self.betrifft:
            return ""
        if self.betrifft in KVG_PFLICHT_ROLLEN or self.betrifft in ERWERBS_ROLLEN:
            return self.betrifft
        return "ein Dokument — welches, steht in der Oberfläche"


@dataclass
class Abdeckung:
    """Was pro Person gefunden wurde."""

    kvg: bool = False
    vvg: bool = False
    selbstkosten: bool = False
    lohn: bool = False
    saeule_3a: bool = False
    quellen: set[str] = _field(default_factory=set)


# Erkennungsmuster der Zielwert-Bezeichnungen. Als Daten und nicht als Code,
# damit die Oberfläche exakt dieselben Regeln anwenden kann — sonst laufen die
# Prüfung beim Erzeugen der Seite und die Prüfung im Browser auseinander.
MUSTER: dict[str, tuple[str, ...]] = {
    "kvg": ("KVG", "Grundversicherung"),
    "vvg": ("VVG", "Zusatzversicherung"),
    "selbstkosten": ("Selbst getragene", "Selbstgetragene"),
    "lohn": ("Nettolohn",),
    "saeule_3a": ("Säule 3a", "Saeule 3a"),
    "praemie": ("Prämie", "Praemie"),
}


def regelwerk(rollen: tuple[str, ...] = KVG_PFLICHT_ROLLEN) -> dict:
    """Regeln in einer Form, die auch die Oberfläche auswerten kann."""
    return {
        "rollen": list(rollen),
        "erwerbsRollen": [r for r in rollen if r in ERWERBS_ROLLEN],
        "muster": {k: list(v) for k, v in MUSTER.items()},
    }


# Die Personenspalte traegt einen Anzeigetext: „Vorname Nachname (rolle)".
# Er ist fuer Menschen gemacht — fuer den Abgleich taugt nur die Rolle.
_ROLLE_IN_KLAMMERN = re.compile(r"\(([a-z_]+(?:_[0-9]+)?)\)\s*$")

ALLE_ROLLEN: tuple[str, ...] = KVG_PFLICHT_ROLLEN + ("familie", "unbekannt/manuell")


def rolle_aus_person(wert) -> str:
    """Die Rolle aus einem Personenwert — oder "" wenn keine erkennbar ist.

    Die Extraktion liefert „Hans Muster (elternteil_1)". Verglichen wurde
    dieser ganze String mit „elternteil_1"; er passte nie, also meldete die
    Pruefung fuer jedes Familienmitglied ein fehlendes Dokument, obwohl alle
    Zeilen zugeordnet waren. Im Dropdown der Oberflaeche passte aus demselben
    Grund keine Option — das Feld blieb leer (260904-rmx).

    Kein Rateversuch auf dem Namen: laesst sich keine Rolle ablesen, ist die
    Zeile eben nicht zugeordnet. Das gehoert sichtbar zu sein, nicht
    ueberdeckt.
    """
    t = str(wert or "").strip()
    if not t:
        return ""
    if t in ALLE_ROLLEN:
        return t
    treffer = _ROLLE_IN_KLAMMERN.search(t)
    if treffer and treffer.group(1) in ALLE_ROLLEN:
        return treffer.group(1)
    return ""


def _rolle(zeile: dict) -> str:
    return rolle_aus_person(zeile.get("person"))


def sammle_abdeckung(zeilen: list[dict]) -> dict[str, Abdeckung]:
    """Wer hat was? Ausgewertet über die Zielwert-Bezeichnungen."""
    out: dict[str, Abdeckung] = {}
    for z in zeilen:
        beschreibung = str(z.get("beschreibung") or z.get("zielwert") or "")
        rolle = _rolle(z)
        if not rolle:
            continue
        a = out.setdefault(rolle, Abdeckung())
        a.quellen.add(str(z.get("pdf_name") or z.get("beleg") or ""))
        if "KVG" in beschreibung or "Grundversicherung" in beschreibung:
            a.kvg = True
        elif "VVG" in beschreibung or "Zusatzversicherung" in beschreibung:
            a.vvg = True
        elif "Selbst getragene" in beschreibung or "Selbstgetragene" in beschreibung:
            a.selbstkosten = True
        elif "Nettolohn" in beschreibung:
            a.lohn = True
        elif "Säule 3a" in beschreibung or "Saeule 3a" in beschreibung:
            a.saeule_3a = True
    return out


def pruefe_erwerbstaetige(
    zeilen: list[dict], rollen: tuple[str, ...] = ERWERBS_ROLLEN
) -> list[Befund]:
    """Beide Erwachsenen haben Lohnausweis und Säule 3a (User-Vorgabe)."""
    abdeckung = sammle_abdeckung(zeilen)
    befunde: list[Befund] = []
    for rolle in rollen:
        a = abdeckung.get(rolle) or Abdeckung()
        if not a.lohn:
            befunde.append(Befund(
                code="lohnausweis_fehlt",
                schwere="fehlt",
                text=(f"Kein Nettolohn für {rolle}. Beide Erwachsenen sind "
                      f"erwerbstätig — es fehlt ein Lohnausweis, oder die "
                      f"Person wurde darin nicht erkannt."),
                betrifft=rolle,
            ))
        if not a.saeule_3a:
            befunde.append(Befund(
                code="saeule_3a_fehlt",
                schwere="fehlt",
                text=(f"Keine Säule-3a-Einzahlung für {rolle}. Beide "
                      f"Erwachsenen zahlen ein — es fehlt eine Bescheinigung."),
                betrifft=rolle,
            ))
    return befunde


def pruefe_familienabdeckung(
    zeilen: list[dict], rollen: tuple[str, ...] = KVG_PFLICHT_ROLLEN
) -> list[Befund]:
    """Für jede Person eine Grundversicherung — sonst fehlt ein Dokument."""
    abdeckung = sammle_abdeckung(zeilen)
    befunde: list[Befund] = []
    for rolle in rollen:
        a = abdeckung.get(rolle)
        if a is None or not a.kvg:
            befunde.append(Befund(
                code="kvg_fehlt",
                schwere="fehlt",
                text=(f"Keine Prämie Grundversicherung (KVG) für {rolle}. "
                      f"Die KVG ist obligatorisch — es fehlt ein Dokument, "
                      f"oder die Person wurde darin nicht erkannt."),
                betrifft=rolle,
            ))
    return befunde


def pruefe_erwartetes_optionales(
    zeilen: list[dict], rollen: tuple[str, ...] = KVG_PFLICHT_ROLLEN
) -> list[Befund]:
    """Was üblich, aber nicht zwingend ist — als abhakbarer Hinweis.

    Grundsatz des Anwenders: lieber aktiv „gibt es nicht" festhalten, als etwas zu
    vergessen, das es gibt. Deshalb werden auch Zusatzversicherung und selbst
    getragene Kosten eingefordert — aber mit der Schwere ``hinweis``, damit
    man sie einmal wegklicken kann, statt sie jedes Jahr neu zu lesen.
    """
    abdeckung = sammle_abdeckung(zeilen)
    befunde: list[Befund] = []
    for rolle in rollen:
        a = abdeckung.get(rolle) or Abdeckung()
        if not a.vvg:
            befunde.append(Befund(
                code="vvg_fehlt",
                schwere="hinweis",
                text=(f"Keine Zusatzversicherung (VVG) für {rolle}. Falls "
                      f"keine besteht: abhaken."),
                betrifft=rolle,
            ))
        if not a.selbstkosten:
            befunde.append(Befund(
                code="selbstkosten_fehlen",
                schwere="hinweis",
                text=(f"Keine selbst getragenen Krankheitskosten für {rolle}. "
                      f"Falls im Jahr keine angefallen sind: abhaken."),
                betrifft=rolle,
            ))
    return befunde


def pruefe_dokument_konsistenz(zeilen: list[dict]) -> list[Befund]:
    """Selbst getragene Kosten ohne Prämie im selben Dokument sind unstimmig."""
    pro_dokument: dict[str, set[str]] = {}
    for z in zeilen:
        dok = str(z.get("pdf_name") or z.get("beleg") or "")
        if not dok:
            continue
        beschreibung = str(z.get("beschreibung") or z.get("zielwert") or "")
        art = pro_dokument.setdefault(dok, set())
        if "Prämie" in beschreibung or "Praemie" in beschreibung:
            art.add("praemie")
        if "Selbst getragene" in beschreibung or "Selbstgetragene" in beschreibung:
            art.add("selbstkosten")

    befunde: list[Befund] = []
    for dok, art in sorted(pro_dokument.items()):
        if "selbstkosten" in art and "praemie" not in art:
            befunde.append(Befund(
                code="selbstkosten_ohne_praemie",
                schwere="unstimmig",
                text=("Selbst getragene Kosten ausgewiesen, aber keine Prämie. "
                      "Wer Kosten abrechnet, ist versichert — die Prämie wurde "
                      "vermutlich übersehen."),
                betrifft=dok,
            ))
    return befunde


def pruefe_person_zuordnung(zeilen: list[dict]) -> list[Befund]:
    """Werte ohne Person lassen sich keiner Steuerpflicht zuordnen."""
    ohne = [z for z in zeilen
            if not _rolle(z) or _rolle(z) in ("unbekannt/manuell", "—")]
    if not ohne:
        return []
    return [Befund(
        code="person_fehlt",
        schwere="fehlt",
        text=(f"{len(ohne)} Position(en) ohne zugeordnete Person. In der "
              f"Übersicht per Dropdown nachtragen."),
        betrifft="",
    )]


def pruefe_alles(zeilen: list[dict],
                 rollen: tuple[str, ...] = KVG_PFLICHT_ROLLEN) -> list[Befund]:
    """Alle Regeln, sortiert: Fehlendes zuerst, dann Unstimmiges."""
    erwerbs = tuple(r for r in rollen if r in ERWERBS_ROLLEN)
    befunde = (pruefe_familienabdeckung(zeilen, rollen)
               + pruefe_erwerbstaetige(zeilen, erwerbs)
               + pruefe_dokument_konsistenz(zeilen)
               + pruefe_person_zuordnung(zeilen)
               + pruefe_erwartetes_optionales(zeilen, rollen))
    reihenfolge = {"fehlt": 0, "unstimmig": 1, "hinweis": 2}
    return sorted(befunde, key=lambda b: (reihenfolge.get(b.schwere, 9),
                                          b.code, b.betrifft))


def rollen_aus_family(family) -> tuple[str, ...]:
    """Leitet die zu prüfenden Rollen aus der family.yaml ab."""
    if family is None:
        return KVG_PFLICHT_ROLLEN
    abbildung = {"mann": "elternteil_1", "frau": "elternteil_2",
                 "kind1": "kind_1", "kind2": "kind_2"}
    rollen = [abbildung[m.role] for m in family.members if m.role in abbildung]
    return tuple(rollen) or KVG_PFLICHT_ROLLEN
