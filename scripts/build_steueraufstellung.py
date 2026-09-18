"""Erzeugt die Steueraufstellung-xlsx aus ``_results_full.json``.

Pivotiert die Extraktions-Ergebnisse in die Zielstruktur des Nutzers
(``Steueraufstellung.numbers``): ein Arbeitsblatt pro vorhandener Belegtyp-
Kategorie, automatischer A/B-Split der Bank-Konten (mit / ohne Verrechnungs-
steuer), Personen-Pivot über die KK-/Krankheits-/3a-/Lohn-Belege und
Summenzeilen — direkt in Numbers/Excel summierbar (float-Zellen).

Im Unterschied zu ``build_tax_output.py`` (Markdown-Übertragungstabelle)
liefert dieser Generator eine einzelne, kalkulierbare xlsx. Die Markdown-
Tabelle bleibt das harte Done-Kriterium (Grader); die xlsx ist ein
ergänzender Komfort-Output.

Design-Constraints:
  - Beträge IMMER als float-Zelle (oder ``None`` bei manuell-prüfen), nie als
    String — sonst nicht summierbar.
  - Manual-Review-Werte und nicht-ok-Belege verschwinden NICHT: sie bekommen
    eine eigene Zeile mit Status ``manuell prüfen`` und leerer Betragszelle.
  - Personen-Pivot nutzt :func:`map_person_to_role` aus ``build_tax_output``
    (importiert, NICHT dupliziert).
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

import openpyxl
from openpyxl.styles import Font

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from extractors.numbers import parse_swiss_amount
from scripts.build_tax_output import (
    resolve_role_constrained,
    resolve_kinderbetreuung_kind,
    format_person_display,
    clean_display_name,
    is_manual_review_marker,
    is_real_value,
    is_by_design,
    ROLE_UNKNOWN,
    PERSON_REQUIRED_BELEGTYPEN,
    _FANTASY_FIRSTNAME_TO_ROLE,
    _REAL_FIRSTNAME_TO_ROLE,
)


def _build_role_to_canonical() -> dict[str, str]:
    """Invertiert die Vornamen→Rolle-Maps zu Rolle→kanonischer Anzeigename.

    Damit erscheint im Personen-Pivot pro Rolle EIN stabiler Name (nicht der
    rohe Extraktions-String "Muster, Maria" vs. "Maria Muster"). Sample-Pfad
    (Fantasy-Vornamen Hans/Maria/Lina/Tim) hat Vorrang; produktiv fällt es auf
    den ersten echten family.yaml-Vornamen pro Rolle zurück.
    """
    out: dict[str, str] = {}
    # Produktiv zuerst eintragen, damit Fantasy (Sample-Pfad) überschreibt.
    for tok, roles in _REAL_FIRSTNAME_TO_ROLE.items():
        for role in roles:
            out.setdefault(role, tok.capitalize())
    for name, role in _FANTASY_FIRSTNAME_TO_ROLE.items():
        out[role] = name
    return out


_ROLE_TO_CANONICAL = _build_role_to_canonical()

# Status-Labels für die xlsx (deutsch, Schweizer Hochdeutsch).
STATUS_AUTO = "auto"
STATUS_MANUELL = "manuell prüfen"


def _to_float(value: str | None) -> float | None:
    """Parst einen CH-formatierten Betrag (Apostroph-Tausender, Komma-Dezimal)
    zu ``float``.

    Liefert ``None`` bei leer, Placeholder, ``manual_review:*``-Marker oder
    nicht parsebarem Wert — damit landet in der Betragszelle entweder eine
    summierbare Zahl oder gar nichts (kein Markertext).
    """
    # Bewusste, verifizierte Feld-Abwesenheit (field_exceptions): kein Betrag.
    # is_real_value lässt den String heute durch → expliziter Vergleich nötig,
    # sonst würfe parse_swiss_amount einen ValueError oder es landete ein
    # Markertext in der Betragszelle.
    if value == "not_in_beleg_by_design":
        return None
    if not is_real_value(value):
        return None
    assert isinstance(value, str)
    # parse_swiss_amount ist Single Source of Truth für CH-Beträge. Ein
    # Dot-Tausender mit führender Ziffer >= 1 ("7.793" → 7793, "1.234.567" →
    # 1234567) wird korrekt geparst (siehe _DOT_THOUSANDS_RE). Nur tatsächlich
    # unparsebare Strings (z. B. "0.125" mit führender 0 oder Buchstaben)
    # werfen ValueError → wir liefern None, sodass kein falscher Betrag
    # summiert wird und die Zeile auf "manuell prüfen" fällt.
    try:
        decimal_value = parse_swiss_amount(value)
    except ValueError:
        return None
    return float(decimal_value) if decimal_value is not None else None


def _field_map(entry: dict) -> dict[str, dict]:
    """feld-Name → field-dict (analog ``fmap`` in build_tax_output)."""
    return {f["feld"]: f for f in entry.get("fields", [])}


def _field_value(field: dict | None) -> str | None:
    if not field:
        return None
    return field.get("value")


def _row_status(field: dict | None) -> str:
    """``auto`` nur wenn echter Wert UND gültiger Anker, sonst ``manuell prüfen``."""
    if field is None:
        return STATUS_MANUELL
    value = field.get("value")
    if is_real_value(value) and field.get("anchor_valid"):
        return STATUS_AUTO
    return STATUS_MANUELL


def _amount_cell(field: dict | None) -> float | None:
    """Betragszelle für ein einzelnes Feld: float bei auto-Status, sonst None."""
    if _row_status(field) != STATUS_AUTO:
        return None
    return _to_float(_field_value(field))


def _plausible_year(entries: list[dict]) -> str:
    """Häufigstes plausibles ``jahr``-Feld (2018..2030) über die ok-Belege."""
    counter: dict[str, int] = defaultdict(int)
    for entry in entries:
        for field in entry.get("fields", []):
            if field.get("feld") != "jahr":
                continue
            value = field.get("value")
            if not is_real_value(value):
                continue
            digits = "".join(ch for ch in str(value) if ch.isdigit())[:4]
            if len(digits) == 4 and digits.isdigit() and 2018 <= int(digits) <= 2030:
                counter[digits] += 1
    if not counter:
        return ""
    return max(counter, key=lambda k: counter[k])


# ── Hilfsfunktionen zum Schreiben einheitlicher Blätter ──────────────────────

def _init_sheet(wb: openpyxl.Workbook, title: str, headers: list[str], jahr: str):
    """Legt ein Blatt mit Steuerjahr-Zeile (1), fetten Spaltenköpfen (2) an.

    Daten beginnen in Zeile 3. Gibt das Worksheet zurück.
    """
    ws = wb.create_sheet(title=title)
    ws.cell(row=1, column=1, value=f"Steuerjahr {jahr}" if jahr else "Steuerjahr")
    for col_idx, head in enumerate(headers, start=1):
        cell = ws.cell(row=2, column=col_idx, value=head)
        cell.font = Font(bold=True)
    return ws


def _write_data_row(ws, row_idx: int, values: list) -> None:
    for col_idx, val in enumerate(values, start=1):
        ws.cell(row=row_idx, column=col_idx, value=val)


def _write_total_row(ws, row_idx: int, label_col: int, sum_cols: dict[int, float]) -> None:
    """Schreibt eine Total-Zeile: fettes Label + float-Summen in den sum_cols."""
    label_cell = ws.cell(row=row_idx, column=label_col, value="Total")
    label_cell.font = Font(bold=True)
    for col_idx, total in sum_cols.items():
        cell = ws.cell(row=row_idx, column=col_idx, value=total)
        cell.font = Font(bold=True)


# ── Personen-Pivot-Sammler ───────────────────────────────────────────────────

class _PersonPivot:
    """Sammelt pro Rolle die summierten Beträge, Anzeige-Name, Quellen.

    Gleiche Rolle (≠ unbekannt/manuell) → summieren, Quellen kommasepariert
    (dedupliziert, Reihenfolge stabil). Rolle ``unbekannt/manuell`` → jeder
    Beleg eine eigene Zeile (nicht zusammenfassen).
    """

    def __init__(self, amount_keys: list[str], belegtyp: str | None = None) -> None:
        self._amount_keys = amount_keys
        # Belegtyp für die ROLE_UNKNOWN-Demotion (personensensitive Blätter).
        self._belegtyp = belegtyp
        # role_key → bucket
        self._buckets: dict[str, dict] = {}
        # Reihenfolge der unbekannt-Belege bleibt über separaten Zähler stabil
        self._unknown_seq = 0

    def add(self, person_raw: str | None, display: str,
            amounts: dict[str, float | None], quelle: str, any_manual: bool) -> None:
        role = resolve_role_constrained(person_raw, self._belegtyp)
        if role != ROLE_UNKNOWN:
            # Kanonischen Anzeigenamen pro Rolle verwenden, NICHT den rohen
            # Extraktions-String — damit "Maria Muster" und "Muster, Maria"
            # in DERSELBEN Rolle-Zeile landen. Fällt nichts zu (z.B. Rolle
            # familie ohne kanonischen Namen), behalte das Display.
            canonical = _ROLE_TO_CANONICAL.get(role)
            if canonical:
                display = canonical
        if role == ROLE_UNKNOWN:
            # Rolle unbestimmt (z.B. kein family.yaml): trotzdem mehrere Belege
            # DERSELBEN Person zusammenfassen — Gruppierung über den
            # bereinigten Namen (normalisiert). Nur wenn auch kein Name
            # bestimmbar ist, bekommt jeder Beleg eine eigene Zeile.
            name = clean_display_name(person_raw)
            if name:
                key = f"__name_{name.strip().lower()}"
            else:
                key = f"__unknown_{self._unknown_seq}"
                self._unknown_seq += 1
        else:
            key = role
        # ROLE_UNKNOWN auf personensensitivem Blatt → nie 'auto' (Grader §10):
        # eine unbestimmte Person bei Lohn/KK/3a/Kinderbetreuung/Krankheit wird
        # zwingend zu 'manuell prüfen' demotiert.
        force_manual = (
            role == ROLE_UNKNOWN
            and self._belegtyp in PERSON_REQUIRED_BELEGTYPEN
        )
        bucket = self._buckets.get(key)
        if bucket is None:
            bucket = {
                "display": display,
                "amounts": {k: 0.0 for k in self._amount_keys},
                "quellen": [],
                "any_manual": False,
            }
            self._buckets[key] = bucket
        if quelle and quelle not in bucket["quellen"]:
            bucket["quellen"].append(quelle)
        if any_manual or force_manual:
            bucket["any_manual"] = True
        for k in self._amount_keys:
            val = amounts.get(k)
            # Nicht-verankerte Teilbeträge (None) werden NICHT aufsummiert.
            if val is not None:
                bucket["amounts"][k] += val

    def rows(self) -> list[dict]:
        out: list[dict] = []
        for bucket in self._buckets.values():
            status = STATUS_MANUELL if bucket["any_manual"] else STATUS_AUTO
            out.append({
                "display": bucket["display"],
                "amounts": bucket["amounts"],
                "quelle": ", ".join(bucket["quellen"]),
                "status": status,
            })
        return out


# ── Blatt-Generatoren ────────────────────────────────────────────────────────

def _build_konten_sheets(wb: openpyxl.Workbook, banks: list[dict], jahr: str) -> None:
    """Bank-Zinsausweise: A/B-Split via verrechnungssteuer > 0."""
    if not banks:
        return
    headers = ["Konto", "Abschluss", "Zinsertrag", "Quelle", "Status"]
    rows_a: list[dict] = []
    rows_b: list[dict] = []
    for entry in banks:
        fmap = _field_map(entry)
        institut = _field_value(fmap.get("institut"))
        kontotyp_field = fmap.get("kontotyp")
        kontotyp = _field_value(kontotyp_field)
        # by-design-kontotyp nie roh rendern (Finding 4a): dann nur Institut.
        if is_real_value(kontotyp) and not is_by_design(kontotyp):
            konto = f"{institut}, {kontotyp}"
        else:
            konto = institut or entry.get("pdf_name", "?")

        vst_field = fmap.get("verrechnungssteuer")
        vst_value = _field_value(vst_field)
        vst_float = _to_float(vst_value)
        brutto_field = fmap.get("bruttoertrag")
        brutto_float = _amount_cell(brutto_field)
        abschluss_float = _amount_cell(fmap.get("vermoegensstand_3112"))

        row = {
            "konto": konto,
            "abschluss": abschluss_float,
            "zinsertrag": brutto_float,
            "quelle": entry.get("pdf_name", "?"),
            "status": STATUS_AUTO,
        }
        if vst_float is not None and vst_float > 0:
            rows_a.append(row)
        else:
            # Unklarer VSt-Fall (Marker oder nicht parsebar) mit Bruttoertrag > 0
            # → in B, aber als manuell-prüfen markiert.
            unklar = (
                (is_manual_review_marker(vst_value) or (vst_value not in (None, "") and vst_float is None))
                and (brutto_float is not None and brutto_float > 0)
            )
            if unklar:
                row["status"] = STATUS_MANUELL
            rows_b.append(row)

    for title, rows in (("Konten A", rows_a), ("Konten B", rows_b)):
        if not rows:
            continue
        ws = _init_sheet(wb, title, headers, jahr)
        r = 3
        sum_abschluss = 0.0
        sum_zins = 0.0
        for row in rows:
            _write_data_row(ws, r, [
                row["konto"], row["abschluss"], row["zinsertrag"],
                row["quelle"], row["status"],
            ])
            if row["abschluss"] is not None:
                sum_abschluss += row["abschluss"]
            if row["zinsertrag"] is not None:
                sum_zins += row["zinsertrag"]
            r += 1
        _write_total_row(ws, r, label_col=1, sum_cols={2: sum_abschluss, 3: sum_zins})


def _build_kk_sheet(wb: openpyxl.Workbook, kk: list[dict], jahr: str) -> None:
    """Versicherungsprämien (KK): Personen-Pivot (KVG + VVG)."""
    if not kk:
        return
    pivot = _PersonPivot(["kvg", "vvg"], belegtyp="kk_praemienbescheinigung")
    for entry in kk:
        fmap = _field_map(entry)
        person_raw = _field_value(fmap.get("versicherte_person_name"))
        display = format_person_display(person_raw, "kk_praemienbescheinigung")
        kvg_field = fmap.get("praemie_kvg_total")
        vvg_field = fmap.get("praemie_vvg_total")
        kvg = _amount_cell(kvg_field)
        vvg = _amount_cell(vvg_field)
        # any_manual: ein vorhandenes, aber nicht-auto Prämienfeld zieht die Zeile
        # auf manuell. Ein by-design-Feld zählt NICHT als "vorhanden aber nicht
        # auto" (Finding 4b) — es ist neutral; der echte Zweitwert entscheidet.
        # Fehlen BEIDE Prämienfelder komplett (kvg_field und vvg_field is None),
        # ist die Zeile substanzlos → manuell, statt eine auto-0.00-Zeile zu
        # erzeugen (Finding 2).
        kvg_manual = (
            _field_value(kvg_field) not in (None, "")
            and not is_by_design(_field_value(kvg_field))
            and _row_status(kvg_field) != STATUS_AUTO
        )
        vvg_manual = (
            _field_value(vvg_field) not in (None, "")
            and not is_by_design(_field_value(vvg_field))
            and _row_status(vvg_field) != STATUS_AUTO
        )
        any_manual = kvg_manual or vvg_manual or (kvg_field is None and vvg_field is None)
        pivot.add(person_raw, display, {"kvg": kvg, "vvg": vvg},
                  entry.get("pdf_name", "?"), any_manual)

    headers = ["Person", "Grundversicherung KVG", "Zusatzversicherung VVG",
               "Prämienverbilligung", "Quelle", "Status"]
    ws = _init_sheet(wb, "Versicherungsprämien", headers, jahr)
    r = 3
    sum_kvg = 0.0
    sum_vvg = 0.0
    for row in pivot.rows():
        kvg = row["amounts"]["kvg"] if row["status"] == STATUS_AUTO else None
        vvg = row["amounts"]["vvg"] if row["status"] == STATUS_AUTO else None
        _write_data_row(ws, r, [
            row["display"], kvg, vvg, None, row["quelle"], row["status"],
        ])
        if kvg is not None:
            sum_kvg += kvg
        if vvg is not None:
            sum_vvg += vvg
        r += 1
    _write_total_row(ws, r, label_col=1, sum_cols={2: sum_kvg, 3: sum_vvg})


def _build_krankheit_sheet(wb: openpyxl.Workbook, kosten: list[dict], jahr: str) -> None:
    """Krankheits- und Unfallkosten: Personen-Pivot (Kostenbeteiligung)."""
    if not kosten:
        return
    pivot = _PersonPivot(["betrag"], belegtyp="krankheitskosten")
    for entry in kosten:
        fmap = _field_map(entry)
        person_raw = _field_value(fmap.get("person_name"))
        display = format_person_display(person_raw, "krankheitskosten")
        # Betrag: `betrag` ODER dynamisch `selbstgetragene_kosten`.
        betrag_field = fmap.get("betrag") or fmap.get("selbstgetragene_kosten")
        betrag = _amount_cell(betrag_field)
        any_manual = (
            _field_value(betrag_field) not in (None, "")
            and _row_status(betrag_field) != STATUS_AUTO
        ) or betrag_field is None
        pivot.add(person_raw, display, {"betrag": betrag},
                  entry.get("pdf_name", "?"), any_manual)

    headers = ["Person", "Kostenbeteiligung", "Quelle", "Status"]
    ws = _init_sheet(wb, "Krankheits- und Unfallkosten", headers, jahr)
    r = 3
    sum_betrag = 0.0
    for row in pivot.rows():
        betrag = row["amounts"]["betrag"] if row["status"] == STATUS_AUTO else None
        _write_data_row(ws, r, [row["display"], betrag, row["quelle"], row["status"]])
        if betrag is not None:
            sum_betrag += betrag
        r += 1
    _write_total_row(ws, r, label_col=1, sum_cols={2: sum_betrag})


def _build_saeule3a_sheet(wb: openpyxl.Workbook, belege: list[dict], jahr: str) -> None:
    if not belege:
        return
    pivot = _PersonPivot(["einzahlung"], belegtyp="saeule_3a")
    for entry in belege:
        fmap = _field_map(entry)
        person_raw = _field_value(fmap.get("kontoinhaber_name"))
        display = format_person_display(person_raw, "saeule_3a")
        field = fmap.get("einzahlung_betrag")
        betrag = _amount_cell(field)
        any_manual = (_field_value(field) not in (None, "") and _row_status(field) != STATUS_AUTO) or field is None
        pivot.add(person_raw, display, {"einzahlung": betrag},
                  entry.get("pdf_name", "?"), any_manual)

    headers = ["Person", "Einzahlung", "Quelle", "Status"]
    ws = _init_sheet(wb, "Säule 3a", headers, jahr)
    r = 3
    total = 0.0
    for row in pivot.rows():
        betrag = row["amounts"]["einzahlung"] if row["status"] == STATUS_AUTO else None
        _write_data_row(ws, r, [row["display"], betrag, row["quelle"], row["status"]])
        if betrag is not None:
            total += betrag
        r += 1
    _write_total_row(ws, r, label_col=1, sum_cols={2: total})


def _build_lohn_sheet(wb: openpyxl.Workbook, belege: list[dict], jahr: str) -> None:
    if not belege:
        return
    # Spalten gegen steuer_zielmodell.SPECS["lohnausweis"] abgeglichen. Mandatory
    # sind: arbeitgeber, periode_von, periode_bis, bruttolohn_pos8,
    # ahv_alv_nbu_abzug_pos9, nettolohn_pos11. Diese sechs sind hier als Spalte
    # vertreten. Das mandatory-Feld "jahr" wird NICHT als Spalte geführt — es
    # steht bereits als Steuerjahr-Kopfzeile (Zeile 1) für das ganze Blatt.
    headers = [
        "Person", "Arbeitgeber", "Periode von", "Periode bis",
        "Bruttolohn (Pos. 8)", "AHV/ALV/NBU (Pos. 9)", "Nettolohn (Pos. 11)",
        "Quelle", "Status",
    ]
    ws = _init_sheet(wb, "Lohnausweise", headers, jahr)
    r = 3
    sum_brutto = 0.0
    sum_ahv = 0.0
    sum_netto = 0.0
    for entry in belege:
        fmap = _field_map(entry)
        person_raw = _field_value(fmap.get("arbeitnehmer_name"))
        display = format_person_display(person_raw, "lohnausweis")
        arbeitgeber = _field_value(fmap.get("arbeitgeber"))
        periode_von = _field_value(fmap.get("periode_von"))
        periode_bis = _field_value(fmap.get("periode_bis"))
        brutto_field = fmap.get("bruttolohn_pos8")
        ahv_field = fmap.get("ahv_alv_nbu_abzug_pos9")
        netto_field = fmap.get("nettolohn_pos11")
        brutto = _amount_cell(brutto_field)
        ahv = _amount_cell(ahv_field)
        netto = _amount_cell(netto_field)
        status = STATUS_AUTO
        # Betragsfelder nicht auto ODER unbestimmte Person (personensensitiv)
        # → ganze Zeile auf "manuell prüfen", Betragszellen None.
        person_role = resolve_role_constrained(person_raw, "lohnausweis")
        if (
            _row_status(brutto_field) != STATUS_AUTO
            or _row_status(netto_field) != STATUS_AUTO
            or person_role == ROLE_UNKNOWN
        ):
            status = STATUS_MANUELL
            brutto = None
            ahv = None
            netto = None
        _write_data_row(ws, r, [display, arbeitgeber, periode_von, periode_bis,
                                brutto, ahv, netto,
                                entry.get("pdf_name", "?"), status])
        if brutto is not None:
            sum_brutto += brutto
        if ahv is not None:
            sum_ahv += ahv
        if netto is not None:
            sum_netto += netto
        r += 1
    _write_total_row(ws, r, label_col=1,
                     sum_cols={5: sum_brutto, 6: sum_ahv, 7: sum_netto})


def _build_wertschriften_sheet(wb: openpyxl.Workbook, belege: list[dict], jahr: str) -> None:
    if not belege:
        return
    headers = ["Institut", "Bestand 31.12.", "Bruttoertrag", "Quelle", "Status"]
    ws = _init_sheet(wb, "Wertschriften", headers, jahr)
    r = 3
    sum_bestand = 0.0
    sum_ertrag = 0.0
    for entry in belege:
        fmap = _field_map(entry)
        institut = _field_value(fmap.get("institut")) or entry.get("pdf_name", "?")
        bestand_field = fmap.get("bestand_3112")
        ertrag_field = fmap.get("bruttoertrag_total")
        bestand = _amount_cell(bestand_field)
        ertrag = _amount_cell(ertrag_field)
        status = STATUS_AUTO
        if _row_status(bestand_field) != STATUS_AUTO or _row_status(ertrag_field) != STATUS_AUTO:
            status = STATUS_MANUELL
        # Plausibilität: Bestand < Bruttoertrag desselben Belegs ist
        # implausibel (Ertrag kann den Vermögensstand nicht übersteigen) →
        # Extraktions-Verdacht, Zeile auf "manuell prüfen", Betragszellen None.
        if bestand is not None and ertrag is not None and bestand < ertrag:
            status = STATUS_MANUELL
            bestand = None
            ertrag = None
        _write_data_row(ws, r, [institut, bestand, ertrag,
                                entry.get("pdf_name", "?"), status])
        if bestand is not None:
            sum_bestand += bestand
        if ertrag is not None:
            sum_ertrag += ertrag
        r += 1
    _write_total_row(ws, r, label_col=1, sum_cols={2: sum_bestand, 3: sum_ertrag})


def _build_spenden_sheet(wb: openpyxl.Workbook, belege: list[dict], jahr: str) -> None:
    if not belege:
        return
    headers = ["Empfänger", "Betrag", "Quelle", "Status"]
    ws = _init_sheet(wb, "Spenden", headers, jahr)
    r = 3
    total = 0.0
    for entry in belege:
        fmap = _field_map(entry)
        empfaenger = _field_value(fmap.get("empfaenger")) or entry.get("pdf_name", "?")
        field = fmap.get("betrag")
        betrag = _amount_cell(field)
        status = _row_status(field)
        _write_data_row(ws, r, [empfaenger, betrag,
                                entry.get("pdf_name", "?"), status])
        if betrag is not None:
            total += betrag
        r += 1
    _write_total_row(ws, r, label_col=1, sum_cols={2: total})


def _build_kinderbetreuung_sheet(wb: openpyxl.Workbook, belege: list[dict], jahr: str) -> None:
    if not belege:
        return
    headers = ["Kind", "Anbieter", "Betrag", "Quelle", "Status"]
    ws = _init_sheet(wb, "Kinderbetreuung", headers, jahr)
    r = 3
    total = 0.0
    for entry in belege:
        fmap = _field_map(entry)
        kind_raw = _field_value(fmap.get("kind_name"))
        kind = format_person_display(kind_raw, "kinderbetreuung")
        anbieter = _field_value(fmap.get("anbieter"))
        field = fmap.get("betrag")
        betrag = _amount_cell(field)
        status = _row_status(field)
        # Kinderbetreuung ist personensensitiv: unbestimmtes Kind (ROLE_UNKNOWN)
        # → nie 'auto', Betragszelle None (Grader §10). Das Kind wird ueber den
        # Fallback (Primaerfeld + Zweitfelder) bestimmt, damit es auch dann
        # gefunden wird, wenn kind_name leer ist, ein Zweitfeld es aber nennt.
        kind_role = resolve_kinderbetreuung_kind(entry)
        if kind_role == ROLE_UNKNOWN:
            status = STATUS_MANUELL
            betrag = None
        _write_data_row(ws, r, [kind, anbieter, betrag,
                                entry.get("pdf_name", "?"), status])
        if betrag is not None:
            total += betrag
        r += 1
    _write_total_row(ws, r, label_col=1, sum_cols={3: total})


def _build_oos_sheet(wb: openpyxl.Workbook, oos: list[dict], rest: list[dict]) -> None:
    """Out of Scope + alle nicht-ok-Belege, die in kein inhaltliches Blatt passen.

    Damit verschwindet kein eingelesener Beleg stillschweigend.
    """
    if not oos and not rest:
        return
    ws = wb.create_sheet(title="Out of Scope")
    for col_idx, head in enumerate(["Datei", "Grund"], start=1):
        cell = ws.cell(row=1, column=col_idx, value=head)
        cell.font = Font(bold=True)
    r = 2
    for entry in oos:
        ws.cell(row=r, column=1, value=entry.get("pdf_name", "?"))
        ws.cell(row=r, column=2, value=entry.get("out_of_scope_reason", "out of scope"))
        r += 1
    for entry in rest:
        ws.cell(row=r, column=1, value=entry.get("pdf_name", "?"))
        status = entry.get("status", "?")
        grund = entry.get("error") or f"Status `{status}` — manuell prüfen"
        ws.cell(row=r, column=2, value=str(grund))
        r += 1


# ── Belegtypen, die ein eigenes inhaltliches Blatt bekommen ──────────────────
_CONTENT_BELEGTYPEN = frozenset({
    "bank_zinsausweis", "kk_praemienbescheinigung", "krankheitskosten",
    "saeule_3a", "lohnausweis", "wertschriftenverzeichnis", "spenden",
    "kinderbetreuung",
})


def build_workbook(entries: list[dict]) -> openpyxl.Workbook:
    """Pivotiert die _results_full.json-Entries in eine Steueraufstellung-xlsx.

    Ein Blatt pro vorhandener Belegtyp-Kategorie (leere Kategorien werden
    übersprungen). Gibt das in-memory Workbook zurück (testbar ohne Datei-I/O).
    """
    wb = openpyxl.Workbook()
    # Default-Blatt entfernen — wir legen nur tatsächlich befüllte Blätter an.
    default_sheet = wb.active
    wb.remove(default_sheet)

    ok_entries = [e for e in entries if e.get("status") == "ok"]
    jahr = _plausible_year(ok_entries)

    by_type: dict[str, list[dict]] = defaultdict(list)
    for entry in ok_entries:
        by_type[entry.get("belegtyp", "unknown")].append(entry)

    _build_konten_sheets(wb, by_type.get("bank_zinsausweis", []), jahr)
    _build_kk_sheet(wb, by_type.get("kk_praemienbescheinigung", []), jahr)
    _build_krankheit_sheet(wb, by_type.get("krankheitskosten", []), jahr)
    _build_saeule3a_sheet(wb, by_type.get("saeule_3a", []), jahr)
    _build_lohn_sheet(wb, by_type.get("lohnausweis", []), jahr)
    _build_wertschriften_sheet(wb, by_type.get("wertschriftenverzeichnis", []), jahr)
    _build_spenden_sheet(wb, by_type.get("spenden", []), jahr)
    _build_kinderbetreuung_sheet(wb, by_type.get("kinderbetreuung", []), jahr)

    # Out-of-Scope-Sammelblatt: explizite OOS-Belege + nicht-ok-Belege, die in
    # kein inhaltliches Blatt passen (extract_failed/empty/unknown/pipeline_error).
    oos = [e for e in entries if e.get("status") == "out_of_scope"]
    rest = [
        e for e in entries
        if e.get("status") not in ("ok", "out_of_scope")
        and e.get("belegtyp", "unknown") not in _CONTENT_BELEGTYPEN
    ]
    # Nicht-ok-Belege mit content-Belegtyp (z.B. extract_failed lohnausweis)
    # haben in ihrem inhaltlichen Blatt keine ok-Zeile → ebenfalls auffangen,
    # damit nichts verschwindet.
    rest += [
        e for e in entries
        if e.get("status") not in ("ok", "out_of_scope")
        and e.get("belegtyp", "unknown") in _CONTENT_BELEGTYPEN
    ]
    _build_oos_sheet(wb, oos, rest)

    # Falls gar nichts erzeugt wurde (leerer Lauf): ein leeres Hinweis-Blatt,
    # damit die xlsx valide bleibt (Workbook braucht ≥1 Blatt).
    if not wb.worksheets:
        ws = wb.create_sheet(title="Steueraufstellung")
        ws.cell(row=1, column=1, value="Keine Belege im Lauf.")

    return wb


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Steueraufstellung-xlsx aus _results_full.json"
    )
    parser.add_argument(
        "--samples", "--input", dest="samples", type=Path,
        default=ROOT / "evals" / "samples_real_2022",
        help="Verzeichnis mit _results_full.json",
    )
    parser.add_argument(
        "--output", type=Path, default=None,
        help="Ziel-xlsx (Default: <samples>/steueraufstellung.xlsx)",
    )
    args = parser.parse_args()

    samples_dir = args.samples if args.samples.is_absolute() else ROOT / args.samples
    results = samples_dir / "_results_full.json"
    if not results.exists():
        print(f"FEHLT: {results} — erst process_samples_full laufen lassen.",
              file=sys.stderr)
        return 1

    output = args.output if args.output else samples_dir / "steueraufstellung.xlsx"
    if not output.is_absolute():
        output = ROOT / output

    data = json.loads(results.read_text())
    wb = build_workbook(data)
    wb.save(output)

    print(f"Geschrieben: {output}")
    print(f"  Blätter: {', '.join(wb.sheetnames)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
