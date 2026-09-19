"""Jede Exportzeile hat so viele Felder wie der Kopf (260904-rmx).

Zweimal ist genau das schiefgegangen. Einmal schrieben die nicht zugeordneten
Dokumente 12 Werte in eine 22-spaltige Datei — die Notiz landete in
``person_neu`` und „Belegart: kk_…" wurde als Personenname zurückgelesen.
Und als eine Spalte (``zuruecknehmen``) dazukam, hätte dasselbe erneut
passieren können.

Der Test zählt die Felder jedes ``zeilen.push([...])`` im erzeugten
JavaScript und vergleicht sie mit der Kopfzeile. Er braucht keinen Browser:
gezählt wird auf der Quelle, und genau dort entsteht der Fehler.
"""
from __future__ import annotations

import re

from scripts.build_review_html import baue_html


def _js(doc: str) -> str:
    return re.search(r"<script>(.*?)</script>", doc, re.S).group(1)


def _felder_zaehlen(inhalt: str) -> int:
    """Zählt die Elemente einer JS-Array-Literalliste auf oberster Ebene."""
    tiefe = 0
    quote = None
    felder = 1
    i = 0
    while i < len(inhalt):
        c = inhalt[i]
        if quote:
            if c == "\\":
                i += 2
                continue
            if c == quote:
                quote = None
        elif c in "'\"`":
            quote = c
        elif c in "([{":
            tiefe += 1
        elif c in ")]}":
            tiefe -= 1
        elif c == "," and tiefe == 0:
            felder += 1
        i += 1
    return felder


def _array_nach(js: str, start: int) -> str | None:
    """Der Inhalt des Array-Literals, das bei ``start`` beginnt."""
    tiefe = 0
    quote = None
    for i in range(start, len(js)):
        c = js[i]
        if quote:
            if c == "\\":
                continue
            if c == quote:
                quote = None
            continue
        if c in "'\"`":
            quote = c
        elif c == "[":
            tiefe += 1
            if tiefe == 1:
                anfang = i + 1
        elif c == "]":
            tiefe -= 1
            if tiefe == 0:
                return js[anfang:i]
    return None


def kopfbreite(js: str) -> int:
    stelle = js.index("const kopf = [")
    return _felder_zaehlen(_array_nach(js, stelle))


def test_kopf_hat_die_erwarteten_spalten():
    js = _js(baue_html([], ["e1"]))
    assert kopfbreite(js) == 23


def test_jede_exportzeile_passt_zum_kopf():
    """Der eigentliche Schutz: keine Zeile darf schmaler oder breiter sein."""
    js = _js(baue_html([], ["e1"]))
    breite = kopfbreite(js)
    abweichend = []
    # Nur Aufrufe mit direktem Array-Literal — `zeilen.push(x.map(...))`
    # baut die Zeile anders und wuerde sonst das naechste Array im Text
    # erwischen (die erste Fassung mass so den Blob-Aufruf).
    for treffer in re.finditer(r"zeilen\.push\(\s*\[", js):
        inhalt = _array_nach(js, treffer.end() - 1)
        if inhalt is None:
            continue
        # Konstruktionsbedingt richtig: ueber den Kopf gebaut oder mit einem
        # Spread, der sich an kopf.length bemisst.
        if "kopf.map(" in inhalt or "...leer(kopf.length" in inhalt:
            continue
        n = _felder_zaehlen(inhalt)
        if n != breite:
            abweichend.append((n, inhalt[:80]))
    assert abweichend == [], abweichend


def test_der_zaehler_zaehlt_richtig():
    """Ohne Gegenprobe waere ein stets-nuller Zaehler auch gruen."""
    assert _felder_zaehlen("a, b, c") == 3
    assert _felder_zaehlen("a, [b, c], d") == 3
    assert _felder_zaehlen("'a,b', c") == 2
    assert _felder_zaehlen("f(x, y), z") == 2
    assert _felder_zaehlen("`a${b},${c}`, d") == 2


def test_zuruecknehmen_ist_eine_eigene_spalte():
    """Ohne sie war der Loeschknopf wirkungslos: die Zeile ging als
    'nichts geaendert' durch und kam beim naechsten Aufbau zurueck."""
    js = _js(baue_html([], ["e1"]))
    assert "'zuruecknehmen'" in js
    assert "k === 'zuruecknehmen' ? 'ja'" in js
