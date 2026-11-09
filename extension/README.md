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

Sie **sendet nichts**. Keine `fetch`- oder `XHR`-Aufrufe, Berechtigungen nur
`storage` und `sidePanel`. Die Werte kommen aus einer Datei, die du einmal auswählst,
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

3. ZHprivateTax öffnen und auf das Symbol der Erweiterung klicken: die
   Leiste öffnet sich im **Seitenteil rechts**. Die Seite wird dabei
   schmaler wie in einem kleineren Fenster — nichts liegt darüber, nichts
   muss seitwärts gescrollt werden. Beim ersten Mal „Datei wählen …"
   anklicken und `steuerwerte.json` auswählen.

**Neue Werte** (nach einer Korrektur in der Review): `make extension-daten`,
dann in der Leiste `⟳` und die Datei neu wählen. **Neue Fassung der
Erweiterung:** in `brave://extensions` bei „Steuerwerte einsetzen" auf ↻
(Neu laden), danach die ZHprivateTax-Seite neu laden.

## Bedienen

- **Einsetzen:** ins Formularfeld klicken, dann in der Leiste auf „einsetzen".
- **Kategorien:** jedes Formular (Lohnausweis, Versicherungsprämien,
  Schuldenverzeichnis …) ist ein aufklappbarer Abschnitt mit eigenem Zähler
  „gesetzt/einzutragen" und ✓, wenn alles gesetzt ist. Öffnest du einen,
  schliessen sich die anderen — so stehen etwa nur die Krankenkassenangaben
  offen. Welcher offen ist, merkt sich der Browser.
- **Suchen:** das Suchfeld filtert über Bezeichnung, Ziffer, Feldnummer,
  Aussteller und Person.
- **Fortschritt:** oben rechts steht, wie viele der einzutragenden Werte schon
  gesetzt sind. Gesetzte Zeilen werden blass.
- **Platz:** die Leiste ist das Seitenteil des Browsers (rechts). Breite
  ändern am Rand des Seitenteils; schliessen und öffnen über das Symbol.
  Fehlt ein Feld oder ist keine ZHprivateTax-Seite offen, sagt es die Leiste.

## Aufbau

| Datei | Zweck |
|---|---|
| `manifest.json` | Manifest V3, nur `storage` und `sidePanel`, Seiten-Skript nur auf ZH-Domains |
| `panel.html`, `panel.js` | die Leiste im Seitenteil; schickt den Wert an die Seite |
| `seite.js` | in der ZHprivateTax-Seite: merkt das Feld, setzt den Wert ein |
| `hintergrund.js` | Klick aufs Symbol öffnet das Seitenteil |
| `panel.css` | Darstellung, hell und dunkel |

Erzeugt wird die Datendatei von `scripts/export_extension.py`.

## Warum der Wert mit Ereignissen gesetzt wird

Moderne Oberflächen lesen nicht das Attribut, sondern horchen auf Ereignisse.
Ein blosses `el.value = …` trägt den Wert sichtbar ein, aber das Formular kennt
ihn nicht — beim Speichern wäre er wieder weg. Deshalb setzt `einsetzen()` über
den nativen Setter und löst danach `input` **und** `change` aus. Nachgeprüft
gegen eine Testseite, die beide Ereignisse protokolliert.
