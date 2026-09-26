# Steuer-Inventar — Kanton Zürich (Beispiel-Familie, 2 Eltern + 2 Kinder, ohne Wohneigentum)

Mapping der ZH-Steuererklärungs-Ziffern auf benötigte Werte und Dokumenttypen.
**Single Source of Truth** für die Regex-Extraktion: pro Dokumenttyp ist klar,
welche Werte gesucht werden und welche Labels im PDF nach ihnen suchen.

> **Beispiel-Haushalt ohne Wohneigentum** — Hypothek-Zinsbestätigung und Liegenschaftsunterhalt
> bleiben im Code wegen Test-Fixtures und Erweiterbarkeit erhalten, sind aber
> für einen Haushalt ohne Wohneigentum irrelevant. Im Live-Output werden sie nur erfasst,
> falls Belege dieses Typs auftauchen.

> ZH-Wegleitung 2024. Numerierung kann je nach Jahr leicht abweichen (z.B.
> 2023 vs. 2024 Ziffer-Verschiebungen) — die Werte selbst bleiben aber stabil.

---

## A. EINKÜNFTE

### Ziff. 1 — Einkünfte aus unselbständiger Erwerbstätigkeit
**Dokument**: Lohnausweis (Form 11, ESTV)

| Wert | Label im PDF (typisch) | Verwendung |
|------|------------------------|------------|
| Arbeitgeber | "Arbeitgeber:" / Kopfzeile | Aussteller-Identifikation |
| Person | "Name:" / "Mitarbeiter:" / Kopfzeile | Person-Zuordnung |
| Periode | "Periode von/bis", "vom...bis" | Jahres-Kontrolle |
| **Bruttolohn (Pos 8)** | "8. Bruttolohn", "Bruttolohn total" | Ziff. 1 Brutto |
| **Nettolohn (Pos 11)** | "11. Nettolohn", "Nettolohn" | Ziff. 1 Netto |
| AHV/IV/EO/ALV/NBUV (Pos 9) | "9. AHV/IV/EO/ALV/NBUV" | Pflichtbeiträge |
| BVG-Abzug (Pos 10.1) | "10.1 Berufliche Vorsorge (BVG)" | Säule-2-Beiträge |
| Quellensteuer (Pos 12) | "12. Quellensteuer" | Nur bei Quellenbesteuerten |

### Ziff. 4 — Wertschriften- und Guthabenertrag
**Dokumente**: Bank-Zinsausweis (Form 340), Wertschriftenverzeichnis / Depot-Auszug

| Wert | Label im PDF (typisch) | Verwendung |
|------|------------------------|------------|
| Institut | "Institut:", "Bank:", Header-Logo | Aussteller |
| Kontoinhaber | "Kontoinhaber:", "Inhaber:" | Person-Zuordnung |
| **Bruttoertrag (Zinsen)** | "Bruttoertrag", "Zinsen", "Bruttozins" | Ziff. 4 Ertrag |
| **Vermögensstand 31.12.** | "Vermögensstand per 31.12.", "Saldo per 31.12." | Ziff. 30 Vermögen |
| **Verrechnungssteuer** | "Verrechnungssteuer", "VST (35%)" | DA-1 / Rückforderung |
| Konto-Nr. (last 4) | "Konto-Nr.:", "IBAN: ...XXXX" | Identifikation |

**Wichtig**: VS-berechtigt (CH-Banken) vs. nicht (BROKER-X, foreign brokers) →
separate Behandlung beim Übertrag.

### Ziff. 31 — Wertschriften-Vermögen
**Dokument**: Wertschriftenverzeichnis / Depot-Auszug

| Wert | Label | Verwendung |
|------|-------|------------|
| **Depot-Bestand 31.12.** | "Bestand per 31.12.", "Depot-Total", "Steuerwert" | Ziff. 31 |
| **Bruttoertrag total** | "Bruttoertrag total", "Total Erträge" | Ziff. 4 |
| **Verrechnungssteuer total** | "Verrechnungssteuer total" | DA-1 |

---

## B. ABZÜGE VOM EINKOMMEN

### Ziff. 14 — Beiträge an Säule 3a
**Dokument**: Säule-3a-Bescheinigung

| Wert | Label | Limit (CHF) |
|------|-------|-------------|
| Stiftung | "Stiftung:", "Anbieter:" | — |
| Kontoinhaber | "Kontoinhaber:" | Person-Zuordnung |
| **Einzahlung Steuerjahr** | "Einzahlung", "Beitrag", "Einlagen total" | 2024: max. 7'056 (BVG-Erwerbstätige) |

### Ziff. 16 — Versicherungsbeiträge (Krankenkassenprämien)
**Dokument**: KK-Prämienbescheinigung

| Wert | Label | Anmerkung |
|------|-------|-----------|
| Kasse | "Krankenkasse:", "Versicherer:", Header | Aussteller |
| Versicherte Person | "Versicherte Person:", "Versicherter:" | Person-Zuordnung |
| **Prämie KVG (Grundversicherung)** | "Prämie KVG", "Jahresprämie KVG", "Grundversicherung Total" | Ziff. 16 abzugsfähig |
| Prämie VVG (Zusatz) | "Prämie VVG", "Zusatzversicherung Total" | Optional |
| Kinder mitversichert | Liste in der Bescheinigung | Zuordnung zu Kind |

### Ziff. 19 — Schuldzinsen (Hypothek)
**Dokument**: Hypothek-Zinsbestätigung

| Wert | Label | Verwendung |
|------|-------|------------|
| Bank | "Institut:", "Bank:" | Aussteller |
| Eigentümer | "Kontoinhaber:", "Schuldner:" | Person (oft Joint) |
| Liegenschaft | "Liegenschaft:", "Objekt:" | Adresse |
| **Schuldzinsen Jahr** | "Schuldzinsen", "Hypothekarzinsen total" | Ziff. 19 |
| **Schuldsaldo 31.12.** | "Schuldsaldo per 31.12.", "Saldo per 31.12." | Ziff. 41 |

### Ziff. 18 — Krankheits- und Unfallkosten
**Dokument**: Arzt-/Apotheken-/Spital-Rechnungen

| Wert | Label | Verwendung |
|------|-------|------------|
| Leistungserbringer | Header (Arztpraxis, Apotheke) | Aussteller |
| Behandelte Person | "Patient:", "Rechnungsadresse:" | Person-Zuordnung |
| **Patientenanteil / Rechnungsbetrag** | "Patientenanteil", "Total CHF", "Rechnungsbetrag" | Ziff. 18 |

> Selbstbehalt-Schwelle: 5% des Nettoeinkommens (ZH 2024). Wird nachgelagert berechnet.

### Ziff. 20.1 — Berufsauslagen (Weiterbildung)
**Dokument**: Kursbestätigung / Weiterbildungs-Rechnung

| Wert | Label | Verwendung |
|------|-------|------------|
| Anbieter | "Anbieter:", Header (EB Zürich, Universität, ...) | Aussteller |
| Person | "Teilnehmer:", "Studierende(r):" | Zuordnung |
| **Kurskosten** | "Kurskosten gesamt", "Kursgebühr", "Rechnungsbetrag" | Ziff. 20.1 |

### Ziff. 21 — Kinderbetreuungskosten
**Dokument**: Kita-/Hort-/Tagesfamilien-Bestätigung

| Wert | Label | Limit (CHF) |
|------|-------|-------------|
| Anbieter | "Anbieter:", "Kita:", "Hort:" | Aussteller |
| **Betreutes Kind** | "Betreutes Kind:", "Kind:" | Zuordnung kind1/kind2 |
| **Betreuungskosten Jahr** | "Betreuungskosten Total", "Jahresbeitrag", "Total" | ZH 2024: max. 25'000 / Kind |

### Ziff. 22 — Liegenschaftsunterhalt (werterhaltend)
**Dokument**: Handwerker-Rechnungen

| Wert | Label | Verwendung |
|------|-------|------------|
| Handwerker | Header (Maler, Sanitär, ...) | Aussteller |
| Eigentümer | "Rechnungsadresse:", "Kunde:" | Person |
| Liegenschaft | "Objekt:", "Adresse Bauobjekt:" | Adresse |
| **Rechnungsbetrag** | "Total brutto", "Rechnungsbetrag", "Total CHF" | Ziff. 22 |
| werterhaltend? | Beschreibungstext ("Reparatur", "Anstrich" → ja; "Anbau", "Neubau" → nein) | Heuristik nachgelagert |

### Ziff. 29 — Spenden
**Dokument**: Spendenquittung

| Wert | Label | Verwendung |
|------|-------|------------|
| Empfänger | "Empfänger:", Header, Logo | Aussteller |
| Spender | "Spender:", "Zuwender:" | Person-Zuordnung |
| **Spendenbetrag** | "Spendenbetrag", "Zuwendung", "Betrag" | Ziff. 29 (gemeinnützig) |

---

## C. VERMÖGEN

| Ziffer | Wert | Dokument | Feld |
|--------|------|----------|------|
| Ziff. 30 | Bank-/Postguthaben 31.12. | Bank-Zinsausweis | Vermögensstand per 31.12. |
| Ziff. 31 | Wertschriften 31.12. | Wertschriftenverzeichnis | Bestand per 31.12. |
| Ziff. 41 | Hypothekarschuld 31.12. | Hypothek-Zinsbestätigung | Schuldsaldo per 31.12. |

---

## D. Extraktions-Strategie

**Schritt 1 — Klassifikation**: Bestehende `extractors.classifier.classify()`
identifiziert den Belegtyp (Lohnausweis, Bank, KK, 3a, ...).

**Schritt 2 — Regex-Extraktion**: Pro Belegtyp wird das oben dokumentierte
Label-Muster im Klartext gesucht; der nächste Token/die nächsten Tokens nach
dem Label sind der gesuchte Wert. Pattern-Definition in
`extractors/regex_extract.py`.

**Schritt 3 — LLM-Fallback (lokal)**: Findet Regex die Pflicht-Werte nicht
(z.B. unbekannter Sender, Custom-Layout), wird der vereinfachte Ollama-Prompt
aufgerufen — Klartext, ohne Tags.

**Schritt 4 — Anker-Validierung**: Für jeden extrahierten Wert wird die
Bounding-Box im Original-PDF via `extractors.anchor_resolver._verbatim_fallback`
ermittelt. Bleibt der Anker leer → Wert geht in `unverified.csv`.

**Output**: Pro Dokument 1 kompakte Zeile in der Tabelle — nur die für die
Steuererklärung benötigten Werte (siehe oben), nicht alle Schema-Felder.
