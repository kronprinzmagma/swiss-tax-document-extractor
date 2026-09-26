"""Die Seite muss ihr eigenes Skript zu Ende ausführen können (260923-dua).

Die Oberfläche ist eingebettetes JavaScript in einer Python-Datei. Alle
bisherigen Tests prüfen sie als **Text**: kommt diese Zeichenkette vor? Das
findet keinen einzigen Laufzeitfehler.

Konkret passiert: eine neue Konstante wurde oberhalb der Konstante eingefügt,
von der sie abhängt. In JavaScript ist das die *temporale Todeszone* — beim
Laden fliegt ein ``ReferenceError``, das Skript bricht ab, und **die ganze
Seite bleibt leer**. Alle 1161 Tests waren grün. Aufgefallen ist es erst beim
Hinschauen im Browser.

Dieser Test prüft die Regel, die das verhindert: keine Deklaration auf
oberster Ebene darf eine benutzen, die weiter unten steht.

Das ersetzt keinen Blick in den Browser — aber es fängt die Fehlerklasse ab,
die am teuersten ist, weil sie alles auf einmal umlegt.
"""
from __future__ import annotations

import re

from scripts.build_review_html import baue_html


def _js(doc: str) -> str:
    return doc.split("<script>", 1)[1].rsplit("</script>", 1)[0]


def _ohne_strings_und_kommentare(js: str) -> str:
    """Zeichenketten und Kommentare raus — sonst zählt jedes Wort darin mit."""
    aus: list[str] = []
    i, n = 0, len(js)
    while i < n:
        c = js[i]
        if c == "/" and i + 1 < n and js[i + 1] == "/":
            while i < n and js[i] != "\n":
                i += 1
            continue
        if c == "/" and i + 1 < n and js[i + 1] == "*":
            i = js.find("*/", i)
            i = n if i < 0 else i + 2
            continue
        if c in "'\"`":
            quote = c
            i += 1
            while i < n:
                if js[i] == "\\":
                    i += 2
                    continue
                if js[i] == quote:
                    i += 1
                    break
                # Template-Literale duerfen ${...} enthalten — der Inhalt ist
                # echter Code und bleibt deshalb stehen.
                if quote == "`" and js.startswith("${", i):
                    tiefe, i = 1, i + 2
                    start = i
                    while i < n and tiefe:
                        if js[i] == "{":
                            tiefe += 1
                        elif js[i] == "}":
                            tiefe -= 1
                        i += 1
                    aus.append(js[start:i - 1])
                    continue
                i += 1
            aus.append(" ")
            continue
        aus.append(c)
        i += 1
    return "".join(aus)


# Deklarationen auf oberster Ebene: Zeilenanfang, keine Einrückung.
_DEKL = re.compile(r"^(?:const|let|var)\s+([A-Za-z_$][\w$]*)", re.MULTILINE)


def finde_todeszonen(js: str) -> list[str]:
    """Jede Deklaration, die eine weiter unten stehende benutzt."""
    js = _ohne_strings_und_kommentare(js)

    stellen: dict[str, int] = {}
    for treffer in _DEKL.finditer(js):
        stellen.setdefault(treffer.group(1), treffer.start())

    fehler: list[str] = []
    for name, stelle in stellen.items():
        # Ab der Deklaration selbst, nicht erst ab dem Zeilenende: die
        # Verwendung steht in aller Regel auf derselben Zeile. (Erste Fassung
        # dieses Tests uebersah genau deshalb den Fehler, den er finden soll.)
        weiter = re.search(r"^(?:const|let|var|function)\s", js[stelle + 1:],
                           re.MULTILINE)
        grenze = stelle + 1 + weiter.start() if weiter else len(js)
        rumpf = js[stelle:grenze]
        for anderer, wo in stellen.items():
            if wo <= stelle or anderer == name:
                continue
            if re.search(rf"\b{re.escape(anderer)}\b", rumpf):
                fehler.append(
                    f"{name} (Zeile {js[:stelle].count(chr(10)) + 1}) benutzt "
                    f"{anderer}, das erst in Zeile "
                    f"{js[:wo].count(chr(10)) + 1} deklariert wird")
    return fehler


def test_keine_konstante_benutzt_eine_spaetere():
    """Temporale Todeszone: das Skript bricht ab, die Seite bleibt leer."""
    fehler = finde_todeszonen(_js(baue_html([], ["e1"])))
    assert not fehler, (
        "Diese Verwendungen laufen in die temporale Todeszone — die Seite "
        "bliebe leer:\n  " + "\n  ".join(fehler))


def test_pruefung_erkennt_den_fehler_wirklich():
    """Ein Test, der nie anschlaegt, ist kein Test.

    Genau dieser Fall ist passiert: WKEY wurde oberhalb von KORPUS eingefuegt.
    """
    fehler = finde_todeszonen(
        "const WKEY = 'x' + KORPUS;\n"
        "const KORPUS = 'a';\n")
    assert len(fehler) == 1
    assert "WKEY" in fehler[0] and "KORPUS" in fehler[0]


def test_pruefung_schlaegt_nicht_grundlos_an():
    """Die richtige Reihenfolge ist kein Fehler — und ein Name in einer
    Zeichenkette oder einem Kommentar zaehlt nicht."""
    assert finde_todeszonen(
        "const KORPUS = 'a';\n"
        "const WKEY = 'x' + KORPUS;\n") == []
    assert finde_todeszonen(
        "const A = 'spaeter B benutzen';\n"
        "// B kommt weiter unten\n"
        "const B = 2;\n") == []
