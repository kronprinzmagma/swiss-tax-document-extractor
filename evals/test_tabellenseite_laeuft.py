"""Die Übertragungsseite muss ihr Skript wirklich ausführen können.

Die Oberfläche ist eingebettetes JavaScript in einer Python-Datei, und alle
Tests prüfen sie als **Text**. Zweimal an einem Tag ist deshalb dasselbe
passiert: eine Zeile lief, bevor das stand, was sie braucht — beim ersten Mal
eine Konstante über ihrer Abhängigkeit, beim zweiten ein Aufruf über der
Deklaration. In beiden Fällen bricht das Skript beim Laden ab, die Seite
bleibt leer, und alle Tests sind grün.

`evals/test_review_js_reihenfolge.py` prüft die Reihenfolge der Deklarationen
statisch. Das reicht nicht: ein **Aufruf** oberhalb einer Deklaration fällt
dort nicht auf.

Hier läuft das Skript deshalb wirklich — in Node, mit einem knappen Ersatz für
Browser und DOM. Kein Ersatz für einen Blick auf die Seite, aber er fängt die
Klasse von Fehlern ab, die alles auf einmal umlegt.

Ohne Node wird der Test übersprungen; er ist ein Zusatz, keine Voraussetzung.
"""
from __future__ import annotations

import shutil
import subprocess

import pytest

from scripts.build_review_html import tabelle_seite

NODE = shutil.which("node")

# Gerade so viel Browser, dass das Skript durchlaeuft. Was es anfasst, muss
# hier stehen — was hier fehlt, faellt als Fehler auf, und das ist richtig so.
STUB = """
const speicher = {};
const localStorage = {
  getItem: (k) => (k in speicher ? speicher[k] : null),
  setItem: (k, v) => { speicher[k] = String(v); },
  removeItem: (k) => { delete speicher[k]; },
};
function knoten(kennung){
  const k = kennung || 'a.pdf|Saldo 31.12.|1';
  return {
    hidden: false, textContent: '', innerHTML: '', checked: false,
    dataset: { k: k }, style: {},
    classList: { toggle(){}, add(){}, remove(){}, contains(){ return false; } },
    querySelector: () => knoten(k),
    querySelectorAll: () => [knoten(k)],
    closest: () => knoten(k),
    getAttribute: () => null,
    scrollIntoView(){},
    getBoundingClientRect: () => ({ top: 100, left: 0, width: 400,
                                    height: 200, bottom: 300, right: 400 }),
    addEventListener(){}, removeEventListener(){}, remove(){},
    focus(){}, appendChild(){}, insertBefore(){},
    scrollTop: 0, scrollHeight: 1000, clientHeight: 400,
    scrollWidth: 400, clientWidth: 400, value: '', selectedIndex: 0,
    options: [], innerText: '',
  };
}
// WICHTIG: die Attrappe muss Zeilen liefern.
//
// Die erste Fassung gab ueberall eine leere Liste zurueck. Damit lief kein
// einziger forEach- oder filter-Rueckruf, und genau der Fehler, den der Test
// finden soll — ein Zugriff auf eine noch nicht deklarierte Konstante — kam
// nie zur Ausfuehrung. Der Test war gruen, auch mit dem Fehler im Code
// (260923-dua). Beim Nachpruefen aufgefallen, nicht beim Schreiben.
const document = {
  getElementById: () => knoten(),
  querySelector: () => knoten(),
  querySelectorAll: () => [knoten('a.pdf|Saldo 31.12.|1'),
                           knoten('a.pdf|Bruttoertrag|1')],
  createElement: () => knoten(),
};
const window = { open(){}, addEventListener(){}, innerHeight: 900,
                 innerWidth: 1400, scrollY: 0, scrollTo(){},
                 getComputedStyle: () => ({}) };
const navigator = { clipboard: { writeText: () => Promise.resolve() } };
function fetch(){ return Promise.resolve({ json: () => ({}),
                                           text: () => '' }); }
function setInterval(){ return 0; }
function setTimeout(){ return 0; }
"""


def _skript(doc: str) -> str:
    return doc.split("<script>", 1)[1].rsplit("</script>", 1)[0]


@pytest.mark.skipif(NODE is None, reason="node nicht vorhanden")
def test_die_seite_laeuft_ohne_fehler(tmp_path):
    zeilen = [
        {"beleg": "a.pdf", "aussteller": "Bank", "person": "elternteil_1",
         "zielwert": "Saldo 31.12.", "betrag": "1000.00", "ziffer": "30.1"},
        {"beleg": "a.pdf", "aussteller": "Bank", "person": "elternteil_1",
         "zielwert": "Bruttoertrag ohne Verrechnungssteuer",
         "betrag": "12.40", "ziffer": "4"},
    ]
    datei = tmp_path / "seite.js"
    datei.write_text(STUB + "\n" + _skript(tabelle_seite(zeilen, 1, "test")),
                     encoding="utf-8")
    fertig = subprocess.run([NODE, str(datei)], capture_output=True, text=True,
                            timeout=30)
    assert fertig.returncode == 0, (
        "Das Skript der Uebertragungsseite bricht beim Laden ab — die Seite "
        "bliebe leer:\n" + fertig.stderr[-2000:])


@pytest.mark.skipif(NODE is None, reason="node nicht vorhanden")
def test_die_arbeitsseite_laeuft_ohne_fehler(tmp_path):
    """Dieselbe Pruefung fuer die Review-Oberflaeche.

    Dort ist derselbe Fehler dreimal passiert: eine Zeile lief, bevor stand,
    was sie braucht. Der statische Reihenfolge-Test sieht nur Deklarationen —
    ein Aufruf oder eine sofort ausgefuehrte Funktion faellt ihm nicht auf.
    """
    from scripts.build_review_html import baue_html, rollen_optionen

    zeilen = [{"nr": 1, "beleg": "a.pdf", "typlabel": "Bankbelege", "pos": 1,
               "zielwert": "Saldo 31.12.", "rohZielwert": "Saldo 31.12.",
               "ziffer": "30.1", "betrag": "1000.00", "konfidenz": "sicher",
               "grund": "Regel", "person": "familie", "aussteller": "Bank",
               "neu": False, "vorabBetrag": False, "vorabPerson": False,
               "gestrichen": False, "unterscheidung": "", "jahr": "2025"}]
    dokumente = [{"beleg": "a.pdf", "zustand": "offen", "zustandwort": "offen",
                  "belegtyp": "bank_zinsausweis", "arten": ["Bankbelege"],
                  "positionen": 1, "bestaetigt": 0, "gestrichen": 0,
                  "offen": 1, "im_ordner": True, "neuername": "",
                  "namengrund": "", "grund": ""}]
    doc = baue_html(zeilen, rollen_optionen(), dokumente=dokumente,
                    live_token="t", korpus="test")
    datei = tmp_path / "arbeitsseite.js"
    datei.write_text(STUB + "\n" + _skript(doc), encoding="utf-8")
    fertig = subprocess.run([NODE, str(datei)], capture_output=True, text=True,
                            timeout=60)
    assert fertig.returncode == 0, (
        "Das Skript der Arbeitsseite bricht beim Laden ab — die Seite bliebe "
        "leer:\n" + fertig.stderr[-2500:])


@pytest.mark.skipif(NODE is None, reason="node nicht vorhanden")
def test_der_test_wuerde_den_fehler_finden(tmp_path):
    """Ein Test, der nie anschlaegt, ist kein Test.

    Genau dieser Fall ist passiert: `hakenAnwenden()` stand ueber `erledigt`.
    """
    datei = tmp_path / "kaputt.js"
    datei.write_text(STUB + "\nfrueh();\nconst spaet = 1;\n"
                     "function frueh(){ return spaet; }\n", encoding="utf-8")
    fertig = subprocess.run([NODE, str(datei)], capture_output=True, text=True,
                            timeout=30)
    assert fertig.returncode != 0
    assert "before initialization" in fertig.stderr
