"""Der letzte Blick, bevor die Zahlen ins Formular wandern.

Die Übertragungstabelle ist das Ende der Kette. Was hier falsch steht, steht
danach in der Steuererklärung. Deshalb ein Kontrollblock zuoberst, der drei
Fragen stellt — dieselben drei, die ein Mensch stellen würde, der die Liste
zum letzten Mal überfliegt:

**1. Ist alles da, was da sein muss?**
Ein Lohnausweis je erwerbstätiger Person, eine Krankenkassenprämie je
Familienmitglied, eine Säule-3a-Bescheinigung. Das prüft
:mod:`extractors.vollstaendigkeit` bereits — hier wird es nur mitgeführt,
damit alles an einer Stelle steht.

**2. Ist ein Eintrag in sich vollständig?**
Gibt es eine Hypothekarschuld, muss es dazu Schuldzinsen geben — und umgekehrt.
Drei Hypotheken auf einem Beleg heissen drei Zinsbeträge. Fehlt einer, ist
nicht der Beleg unvollständig, sondern die Durchsicht.

**3. Sind die Zahlen plausibel?**
Ist es überhaupt eine Zahl? Ist die Grössenordnung glaubwürdig? Eine
Krankenkassenprämie von 45 Franken im Jahr ist so sicher falsch wie eine von
450'000.

Zur Ehrlichkeit der dritten Frage: die Bandbreiten sind **Erfahrungswerte**,
keine Vorschriften. Sie sagen „das sieht ungewöhnlich aus", nicht „das ist
falsch", und sie sind hier an einer Stelle gesammelt, damit man sie anpassen
kann. Hart sind nur zwei Dinge: ein Wert, der keine Zahl ist, und die
gesetzlichen Höchstbeträge der Säule 3a.
"""
from __future__ import annotations

from dataclasses import dataclass

from extractors.numbers import parse_swiss_amount

# Gesetzliche Höchstabzüge Säule 3a, Steuerjahr 2025/26.
# Quelle: BSV, „Welche Beiträge kann ich in die Säule 3a einzahlen?"
#
#   mit Pensionskasse   7'258
#   ohne Pensionskasse  20 % des Einkommens, höchstens 36'288
#
# MASSGEBLICH IST 7'258. Der höhere Betrag gilt nur OHNE 2. Säule, in der
# Praxis also für Selbständige; in diesem Haushalt sind beide Erwachsenen
# angestellt und haben eine Pensionskasse.
#
# Die erste Fassung hatte es umgekehrt: sie nahm den Selbständigen-Betrag als
# Fehlergrenze und meldete alles darunter nur als Hinweis. Eine Einzahlung von
# 20'000 wäre damit durchgerutscht (260923-dua, vom Nutzer korrigiert).
SAEULE_3A_MAX = 7_258
SAEULE_3A_OHNE_PK = 36_288

# Rueckwaertskompatibler Name — anderswo importiert.
SAEULE_3A_MIT_PK = SAEULE_3A_MAX

# Zielwert -> (untere, obere) Grenze der üblichen Grössenordnung in CHF.
#
# Erfahrungswerte für einen Schweizer Haushalt, bewusst weit gefasst: ein
# Hinweis, der bei jedem zweiten Wert anschlägt, wird nach dem dritten Mal
# überlesen. Ein Wert ausserhalb heisst „bitte ansehen", nicht „falsch".
BANDBREITEN: dict[str, tuple[float, float]] = {
    "Nettolohn": (5_000, 1_000_000),
    "Prämie Grundversicherung KVG": (600, 12_000),
    "Prämie Zusatzversicherung VVG": (50, 12_000),
    "Prämie Total (nicht aufgeteilt)": (600, 20_000),
    "Selbst getragene Krankheits- und Unfallkosten": (10, 100_000),
    "Einzahlung Säule 3a": (100, SAEULE_3A_MAX),
    "Kosten Kinderbetreuung": (100, 80_000),
    "Schuldzinsen Hypothek": (50, 200_000),
    "Hypothekarschuld 31.12.": (1_000, 10_000_000),
    "Saldo 31.12.": (1, 20_000_000),
    "Steuerwert 31.12.": (1, 20_000_000),
    "Bruttoertrag ohne Verrechnungssteuer": (1, 2_000_000),
    "Bruttoertrag mit Verrechnungssteuer": (1, 2_000_000),
}

# Was zusammengehört: kommt das eine vor, wird das andere erwartet — und zwar
# gleich oft. Je Dokument geprüft.
PAARE: tuple[tuple[str, str], ...] = (
    ("Hypothekarschuld 31.12.", "Schuldzinsen Hypothek"),
)

# Glaubwürdiger Hypothekarzins, als Anteil der Schuld. Unter 0.1 % ist es
# kaum ein Jahreszins, über 8 % kaum eine Hypothek.
ZINSSATZ_BAND = (0.001, 0.08)


@dataclass(frozen=True)
class Befund:
    """Ein Punkt für den Schlusscheck."""

    gruppe: str     # "pflicht" | "vollstaendig" | "plausibel"
    schwere: str    # "fehlt" | "unstimmig" | "hinweis"
    text: str

    @property
    def ist_fehler(self) -> bool:
        return self.schwere in ("fehlt", "unstimmig")


GRUPPEN = {
    "pflicht": "Ist alles da, was da sein muss?",
    "vollstaendig": "Ist jeder Eintrag in sich vollständig?",
    "plausibel": "Sind die Zahlen plausibel?",
}


def betrag_oder_nichts(text) -> float | None:
    """Der Betrag als Zahl — oder ``None``, wenn es keine ist.

    ``parse_swiss_amount`` wirft bei Muellwerten eine Ausnahme. Das ist dort
    richtig; hier ist „keine Zahl" gerade das Ergebnis, nach dem gefragt wird.
    """
    roh = str(text or "").strip()
    if not roh:
        return None
    try:
        return parse_swiss_amount(roh)
    except (ValueError, TypeError):
        return None


def _chf(wert: float) -> str:
    """1234567.5 -> "1'234'567.50" — Schweizer Schreibweise.

    Vorher stand hier ``f"{wert:,.0f}".replace(",", "'")``. Das ersetzte auch
    die Kommas im umgebenden Satz: „die Person hat einen Lohnausweis' also
    eine Pensionskasse" (260923-dua).
    """
    ganz = f"{wert:,.2f}".replace(",", "\u2019").replace("\u2019", "'")
    return ganz.rstrip("0").rstrip(".") if ganz.endswith(".00") else ganz


def _betrag(zeile: dict) -> float | None:
    return betrag_oder_nichts(zeile.get("betrag"))


def _name(zeile: dict) -> str:
    """Wie die Zeile im Bericht heisst — ohne Dokumentnamen.

    Steuerbelege heissen nach den Menschen, um die es geht. Der Bericht steht
    in der Oberfläche und darf den Namen zeigen; er wird aber auch kopiert und
    weitergegeben, deshalb bleibt er hier draussen.
    """
    teile = [str(zeile.get("zielwert") or "?")]
    unt = str(zeile.get("unterscheidung") or "").strip()
    if unt:
        teile.append(f"({unt})")
    rolle = str(zeile.get("person") or "").strip()
    if rolle:
        teile.append(f"— {rolle}")
    return " ".join(teile)


def pruefe_pflicht(zeilen: list[dict], rollen: tuple[str, ...]) -> list[Befund]:
    """Fehlt ein Beleg, den es für diese Familie geben muss?"""
    from extractors.vollstaendigkeit import pruefe_alles

    aus: list[Befund] = []
    for b in pruefe_alles([z for z in zeilen if not z.get("gestrichen")],
                          rollen):
        aus.append(Befund("pflicht",
                          "fehlt" if b.schwere == "fehlt" else "hinweis",
                          b.text))
    return aus


def pruefe_vollstaendig(zeilen: list[dict]) -> list[Befund]:
    """Gehört zu jedem Wert der Gegenwert — Hypothek und Schuldzinsen."""
    je_beleg: dict[str, list[dict]] = {}
    for z in zeilen:
        if z.get("gestrichen"):
            continue
        je_beleg.setdefault(str(z.get("beleg") or z.get("pdf_name") or ""),
                            []).append(z)

    aus: list[Befund] = []
    for beleg, rs in sorted(je_beleg.items()):
        namen = [str(r.get("zielwert") or r.get("beschreibung") or "")
                 for r in rs]
        for eins, andere in PAARE:
            a, b = namen.count(eins), namen.count(andere)
            if a and not b:
                aus.append(Befund(
                    "vollstaendig", "fehlt",
                    f"{eins} vorhanden ({a}×), aber kein einziger Wert "
                    f"„{andere}\" im selben Dokument."))
            elif b and not a:
                aus.append(Befund(
                    "vollstaendig", "fehlt",
                    f"{andere} vorhanden ({b}×), aber kein einziger Wert "
                    f"„{eins}\" im selben Dokument."))
            elif a and b and a != b:
                aus.append(Befund(
                    "vollstaendig", "unstimmig",
                    f"{a}× „{eins}\", aber {b}× „{andere}\" im selben Dokument "
                    f"— zu jeder Schuld gehört ein Zins."))
    return aus


def _zinssatz_pruefen(zeilen: list[dict]) -> list[Befund]:
    """Passt der Schuldzins zur Schuld?

    Nur wenn es je Dokument genau eine Schuld und einen Zins gibt — sonst ist
    nicht klar, welcher Zins zu welcher Hypothek gehört, und eine Warnung auf
    Verdacht wäre Rauschen.
    """
    je_beleg: dict[str, list[dict]] = {}
    for z in zeilen:
        if not z.get("gestrichen"):
            je_beleg.setdefault(str(z.get("beleg") or ""), []).append(z)

    aus: list[Befund] = []
    for beleg, rs in sorted(je_beleg.items()):
        schulden = [r for r in rs
                    if str(r.get("zielwert") or "") == "Hypothekarschuld 31.12."]
        zinsen = [r for r in rs
                  if str(r.get("zielwert") or "") == "Schuldzinsen Hypothek"]
        if len(schulden) != 1 or len(zinsen) != 1:
            continue
        schuld, zins = _betrag(schulden[0]), _betrag(zinsen[0])
        if not schuld or zins is None or schuld <= 0:
            continue
        satz = zins / schuld
        if not (ZINSSATZ_BAND[0] <= satz <= ZINSSATZ_BAND[1]):
            aus.append(Befund(
                "plausibel", "hinweis",
                f"Schuldzins ergibt {satz * 100:.2f}% der Hypothekarschuld — "
                f"ungewöhnlich. Stimmen beide Beträge?"))
    return aus


def pruefe_plausibel(zeilen: list[dict]) -> list[Befund]:
    """Ist es eine Zahl, und ist die Grössenordnung glaubwürdig?"""
    aus: list[Befund] = []
    for z in zeilen:
        if z.get("gestrichen"):
            continue
        roh = str(z.get("betrag") or "").strip()
        name = _name(z)
        if not roh:
            aus.append(Befund("plausibel", "fehlt",
                              f"{name}: kein Betrag."))
            continue
        wert = _betrag(z)
        if wert is None:
            aus.append(Befund("plausibel", "unstimmig",
                              f"{name}: „{roh}\" ist keine Zahl."))
            continue
        if wert < 0:
            aus.append(Befund("plausibel", "unstimmig",
                              f"{name}: negativer Betrag."))
            continue

        ziel = str(z.get("zielwert") or "")
        # Gesetzliche Grenze — das ist keine Erfahrung, sondern eine Vorschrift.
        if ziel == "Einzahlung Säule 3a":
            if wert > SAEULE_3A_MAX:
                aus.append(Befund(
                    "plausibel", "unstimmig",
                    f"{name}: {_chf(wert)} übersteigt den Höchstabzug von "
                    f"{_chf(SAEULE_3A_MAX)}. Mehr ist nur ohne Pensionskasse "
                    f"zulässig (bis {_chf(SAEULE_3A_OHNE_PK)})."))
                continue

        band = BANDBREITEN.get(ziel)
        if band and not (band[0] <= wert <= band[1]):
            wie = "niedrig" if wert < band[0] else "hoch"
            aus.append(Befund(
                "plausibel", "hinweis",
                f"{name}: {_chf(wert)} ist ungewöhnlich {wie} für diesen "
                f"Wert (üblich {_chf(band[0])}–{_chf(band[1])})."))
    return aus + _zinssatz_pruefen(zeilen)


def pruefe_schluss(zeilen: list[dict],
                   rollen: tuple[str, ...] | None = None) -> list[Befund]:
    """Alle drei Fragen auf einmal — in der Reihenfolge, in der man sie stellt."""
    from extractors.vollstaendigkeit import KVG_PFLICHT_ROLLEN

    return (pruefe_pflicht(zeilen, rollen or KVG_PFLICHT_ROLLEN)
            + pruefe_vollstaendig(zeilen)
            + pruefe_plausibel(zeilen))
