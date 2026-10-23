# Steuerwerte einsetzen — Erweiterung für Brave

Zeigt die selbst geprüften Werte neben ZHprivateTax und setzt sie auf Klick
ins Formular. Kein Abtippen, keine Zwischenablage, kein Suchen in einer
Tabelle.

## Was sie tut — und was nicht

Die Erweiterung **kennt die Felder des Formulars nicht**, und sie muss es auch
nicht. Du klickst in ein Eingabefeld, dann in der Leiste auf „einsetzen" — der
Wert landet in dem Feld, das zuletzt den Fokus hatte. Das hat zwei Vorteile:
sie funktioniert weiter, wenn das Steueramt die Oberfläche umbaut, und sie kann
nichts an der falschen Stelle eintragen, weil du die Stelle bestimmst.

Sie **sendet nichts**. Keine `fetch`- oder `XHR`-Aufrufe, keine Berechtigung
ausser `storage`. Die Werte kommen aus einer Datei, die du einmal auswählst,
und liegen danach im lokalen Speicher deines Browsers.

Ein Detail, das Geld kostet, wenn es fehlt: für die Ziffern **4, 12, 30.1 und
34** rechnet ZHprivateTax die Summe selbst aus den Einzelpositionen des
Wertschriften- bzw. Schuldenverzeichnisses. Wer sie zusätzlich einträgt,
verdoppelt sie. Solche Werte zeigt die Leiste gelb und **ohne** Knopf — sie
sind Kontrollzahlen, keine Eingabewerte.

## Einrichten

1. Werte erzeugen:

   ```
   make extension-daten
   ```

   Das schreibt `output/latest/steuerwerte.json`. Die Datei enthält echte
   Beträge und bleibt lokal.

2. In Brave `brave://extensions` öffnen, **Entwicklermodus** einschalten,
   „Entpackte Erweiterung laden" und diesen Ordner (`extension/`) wählen.

3. ZHprivateTax öffnen. Rechts erscheint die Leiste. Beim ersten Mal
   „Datei wählen …" anklicken und `steuerwerte.json` auswählen.

## Bedienen

- **Einsetzen:** ins Formularfeld klicken, dann in der Leiste auf „einsetzen".
- **Suchen:** das Suchfeld filtert über Bezeichnung, Ziffer, Feldnummer,
  Aussteller und Person.
- **Fortschritt:** oben rechts steht, wie viele der einzutragenden Werte schon
  gesetzt sind. Gesetzte Zeilen werden blass.
- **Verschieben:** die Leiste lässt sich am Kopf packen und wegziehen, damit
  sie kein Feld verdeckt. Mit `▾` klappt sie zusammen.

## Aufbau

| Datei | Zweck |
|---|---|
| `manifest.json` | Manifest V3, nur `storage`, nur ZH-Domains |
| `panel.js` | die Leiste, das Merken des Feldes, das Einsetzen |
| `panel.css` | Darstellung, hell und dunkel |

Erzeugt wird die Datendatei von `scripts/export_extension.py`.

## Warum der Wert mit Ereignissen gesetzt wird

Moderne Oberflächen lesen nicht das Attribut, sondern horchen auf Ereignisse.
Ein blosses `el.value = …` trägt den Wert sichtbar ein, aber das Formular kennt
ihn nicht — beim Speichern wäre er wieder weg. Deshalb setzt `einsetzen()` über
den nativen Setter und löst danach `input` **und** `change` aus. Nachgeprüft
gegen eine Testseite, die beide Ereignisse protokolliert.
