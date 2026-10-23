"""Basis-Tabelle — EINE Zeile pro Quelldokument (Datei · Typ · Name · Wert).

Die einfachste sinnvolle Übersicht über einen Lauf: pro eingelesenem PDF genau
eine Zeile mit Dokumenttyp, dem identifizierenden Namen (Person/Rolle oder
Aussteller/Institut) und dem einen Leitwert des Belegtyps. Daraus folgt die
einzige Metrik dieser Sicht: wie viele Dokumente sind vollständig (Typ + Name +
Wert alle vorhanden und der Wert parsebar).

Im Unterschied zu ``build_tax_output.py`` (mehrzeilige Übertragungstabelle mit
allen Feldern) und ``build_steueraufstellung.py`` (pivotierte xlsx) ist dies
eine flache 1:1-Sicht Dokument→Zeile. Sie verschweigt keinen Beleg: auch
out-of-scope- und fehlgeschlagene Belege bekommen ihre Zeile mit Status und
Grund.

Design-Constraints:
  - Feld-Mapping pro Belegtyp NICHT neu erfinden: die Name-/Wert-Felder kommen
    aus ``steuer_zielmodell.SPECS`` (person_field/aussteller_field) bzw. dem
    bereits in ``build_steueraufstellung.py`` etablierten Spalten-Mapping.
  - "Name" bei personengebundenen Belegen über ``map_person_to_role`` /
    ``format_person_display`` aus ``build_tax_output`` (kanonischer Rollen-/
    Fantasy-Name im Sample-Pfad, kein PII-Leak), sonst Aussteller/Institut.
  - "Wert" über ``extractors.numbers.parse_swiss_amount`` geparst; nicht
    parsebar/fehlend → Wert leer + Lücken-Code.
  - ``--stats`` gibt NUR die Kopfzeilen-Statistik + Lücken-Zähler pro
    (belegtyp, ursache) aus, OHNE Dateinamen — privacy-tauglich teilbar.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from extractors.numbers import parse_swiss_amount
from extractors.steuer_zielmodell import get_spec
from scripts.build_tax_output import (
    ROLE_UNKNOWN,
    format_person_display,
    is_manual_review_marker,
    is_real_value,
    is_by_design,
    resolve_role_constrained,
)


# ── Belegtyp → (Name-Quelle, Wert-Feld) ──────────────────────────────────────
#
# Name-Quelle ist entweder "person" (über das person_field der SPEC, gemappt
# via map_person_to_role/format_person_display) oder "aussteller" (über das
# aussteller_field der SPEC, roher Marker/Institut). Wert-Feld ist der eine
# Leitwert des Belegtyps — abgeglichen mit den Spalten in
# build_steueraufstellung.py (Single Source of Truth für das Feld-Mapping).
#
# value_fields ist eine Liste: das erste vorhandene & echte Feld gewinnt
# (krankheitskosten: betrag ODER selbstgetragene_kosten, analog
# _build_krankheit_sheet).
NAME_PERSON = "person"
NAME_AUSSTELLER = "aussteller"

BASIS_MAP: dict[str, dict] = {
    "lohnausweis": {"name": NAME_PERSON, "value_fields": ["nettolohn_pos11"]},
    "bank_zinsausweis": {"name": NAME_AUSSTELLER, "value_fields": ["vermoegensstand_3112"]},
    "wertschriftenverzeichnis": {"name": NAME_AUSSTELLER, "value_fields": ["bestand_3112"]},
    # KK: vollstaendig, wenn mindestens EINE Praemie vorhanden — manche
    # Bescheinigungen sind reine VVG-Auszuege ohne KVG-Position.
    "kk_praemienbescheinigung": {"name": NAME_PERSON, "value_fields": ["praemie_kvg_total", "praemie_vvg_total"]},
    "krankheitskosten": {"name": NAME_PERSON, "value_fields": ["betrag", "selbstgetragene_kosten"]},
    "saeule_3a": {"name": NAME_PERSON, "value_fields": ["einzahlung_betrag"]},
    "spenden": {"name": NAME_AUSSTELLER, "value_fields": ["betrag"], "aussteller_field": "empfaenger"},
    "kinderbetreuung": {"name": NAME_PERSON, "value_fields": ["betrag"]},
}

# Out-of-Scope-Belegtypen, die als eigene Status-Zeile (kein Wert) erscheinen.
OOS_BELEGTYPEN = frozenset({
    "vermoegensverwaltungskosten",
    "corporate_action_einzeltransaktion",
})

# Lücken-Codes (maschinenlesbar, privacy-tauglich aggregierbar).
LUECKE_FELD_FEHLT = "feld_fehlt"
LUECKE_NICHT_PARSEBAR = "nicht_parsebar"
LUECKE_MR_MARKER = "manual_review_marker"
LUECKE_PERSON_UNBESTIMMT = "person_unbestimmt"
LUECKE_BELEG_FEHLGESCHLAGEN = "beleg_fehlgeschlagen"

STATUS_VOLLSTAENDIG = "vollständig"
STATUS_LUECKE = "lücke"
STATUS_OOS = "out-of-scope"
STATUS_FEHLGESCHLAGEN = "fehlgeschlagen"
# Bewusste, verifizierte Feld-Abwesenheit (field_exceptions): keine Lücke, aber
# auch nicht "vollständig mit Wert" — der Leitwert ist by-design nicht im Beleg.
STATUS_NA_BY_DESIGN = "n/a by design"

# Marker für eine gepflegte, verifizierte Feld-Abwesenheit (siehe
# process_samples_full.py / field_exceptions.json).
NOT_IN_BELEG_BY_DESIGN = "not_in_beleg_by_design"


def _field_map(entry: dict) -> dict[str, dict]:
    """feld-Name → field-dict."""
    return {f["feld"]: f for f in entry.get("fields", [])}


def _first_value_field(fmap: dict[str, dict], candidates: list[str]) -> dict | None:
    """Bestes Wert-Feld aus der Kandidatenliste.

    Bevorzugt das erste Feld mit ECHTEM Wert (Fallback-Semantik: ein
    KK-Beleg ist vollstaendig, wenn KVG ODER VVG vorhanden ist — ein
    existierendes, aber leeres KVG-Feld darf ein gefuelltes VVG-Feld nicht
    verdecken). Hat kein Kandidat einen echten Wert, liefert es das erste
    existierende Feld, damit der Caller feld_fehlt/Marker unterscheiden kann.
    """
    first_existing: dict | None = None
    for cand in candidates:
        if cand in fmap:
            if first_existing is None:
                first_existing = fmap[cand]
            val = fmap[cand].get("value")
            # by-design-Feld ist KEIN echter Wert-Treffer (Finding 4c): es darf
            # einen echten parsebaren Zweitwert (z.B. VVG) nicht verdecken. Nur
            # als first_existing behalten (Marker-Unterscheidung im Caller).
            if is_real_value(val) and not is_by_design(val):
                return fmap[cand]
    return first_existing


def _resolve_name(entry: dict, belegtyp: str, mapping: dict) -> tuple[str, bool]:
    """Bestimmt den Anzeige-Namen einer Zeile.

    Returns (name, person_unbestimmt). ``person_unbestimmt`` ist nur bei
    personengebundenen Belegen True, wenn die Rolle ``unbekannt/manuell`` ist —
    das ist ein Lücken-Grund (Vollständigkeit verlangt einen identifizierten
    Namen).
    """
    fmap = _field_map(entry)
    spec = get_spec(belegtyp)
    if mapping["name"] == NAME_PERSON:
        person_field = spec.person_field if spec else None
        person_raw = fmap.get(person_field or "", {}).get("value")
        role = resolve_role_constrained(person_raw, belegtyp)
        display = format_person_display(person_raw, belegtyp)
        return display, role == ROLE_UNKNOWN
    # Aussteller-Name: explizites Override-Feld oder das aussteller_field der SPEC.
    aussteller_field = mapping.get("aussteller_field") or (spec.aussteller_field if spec else None)
    aussteller_raw = fmap.get(aussteller_field or "", {}).get("value")
    if is_real_value(aussteller_raw):
        return str(aussteller_raw), False
    # Kein Aussteller extrahiert → Dateiname als Notbehelf, aber das ist keine
    # Personen-Lücke (Aussteller-Belege haben keine Rolle).
    return entry.get("pdf_name", "?"), False


def _resolve_value(value_field: dict | None) -> tuple[str, str | None]:
    """Parst den Leitwert. Returns (wert_str, luecke_code).

    wert_str ist der formatierte Betrag (oder "" bei Lücke); luecke_code ist
    None bei Erfolg, sonst der Ursachen-Code.
    """
    if value_field is None:
        return "", LUECKE_FELD_FEHLT
    raw = value_field.get("value")
    if raw is None or raw == "":
        return "", LUECKE_FELD_FEHLT
    # Bewusste, verifizierte Feld-Abwesenheit: leere Zelle, KEINE Lücke. Dieser
    # explizite Vergleich steht vor parse_swiss_amount, weil is_real_value den
    # String sonst als echt durchlässt und der Parse einen ValueError würfe.
    if raw == NOT_IN_BELEG_BY_DESIGN:
        return "", None
    if is_manual_review_marker(raw):
        return "", LUECKE_MR_MARKER
    if not is_real_value(raw):
        return "", LUECKE_FELD_FEHLT
    try:
        parsed = parse_swiss_amount(raw)
    except ValueError:
        return "", LUECKE_NICHT_PARSEBAR
    if parsed is None:
        return "", LUECKE_NICHT_PARSEBAR
    # Auf 2 Nachkommastellen, ohne Tausender-Apostroph (kompakte Zelle).
    return f"{float(parsed):.2f}", None


def build_row(entry: dict) -> dict:
    """Wandelt einen _results_full-Entry in genau EINE Basis-Zeile.

    Schlüssel: datei, dokumenttyp, name, wert, status, luecke.
    """
    pdf_name = entry.get("pdf_name", "?")
    status = entry.get("status", "?")
    belegtyp = entry.get("belegtyp", "unknown")

    # Fehlgeschlagene/leere Belege: eigene Zeile mit Ursache.
    if status not in ("ok", "out_of_scope"):
        ursache = entry.get("error") or f"status:{status}"
        return {
            "datei": pdf_name,
            "dokumenttyp": belegtyp if belegtyp and belegtyp != "unknown" else "—",
            "name": "—",
            "wert": "",
            "status": STATUS_FEHLGESCHLAGEN,
            "luecke": LUECKE_BELEG_FEHLGESCHLAGEN,
            "luecke_detail": str(ursache),
        }

    # Out-of-Scope: kein Wert, Grund als Status-Detail.
    if status == "out_of_scope" or belegtyp in OOS_BELEGTYPEN:
        grund = entry.get("out_of_scope_reason", "out of scope")
        fmap = _field_map(entry)
        spec = get_spec(belegtyp)
        aussteller_raw = (
            fmap.get(spec.aussteller_field or "", {}).get("value") if spec else None
        )
        name = str(aussteller_raw) if is_real_value(aussteller_raw) else "—"
        return {
            "datei": pdf_name,
            "dokumenttyp": belegtyp or "—",
            "name": name,
            "wert": "",
            "status": STATUS_OOS,
            "luecke": "",
            "luecke_detail": str(grund),
        }

    mapping = BASIS_MAP.get(belegtyp)
    if mapping is None:
        # Bekannter ok-Status, aber kein Basis-Mapping (z.B. neuer Belegtyp ohne
        # Eintrag) → als Lücke kennzeichnen, nicht stillschweigend verlieren.
        return {
            "datei": pdf_name,
            "dokumenttyp": belegtyp,
            "name": "—",
            "wert": "",
            "status": STATUS_LUECKE,
            "luecke": LUECKE_FELD_FEHLT,
            "luecke_detail": f"kein Basis-Mapping für `{belegtyp}`",
        }

    name, person_unbestimmt = _resolve_name(entry, belegtyp, mapping)
    fmap = _field_map(entry)
    value_field = _first_value_field(fmap, mapping["value_fields"])
    wert, luecke = _resolve_value(value_field)

    # Bewusste Feld-Abwesenheit (by-design): keine Lücke, eigener Status. Der
    # Leitwert ist verifiziert nicht im Beleg → nicht als Lücke bestrafen, aber
    # auch nicht als "vollständig mit Wert" ausweisen.
    by_design = (
        value_field is not None
        and value_field.get("value") == NOT_IN_BELEG_BY_DESIGN
    )

    # Person-Lücke greift, wenn der Wert sonst da wäre — sonst zählt die
    # Wert-Lücke (Wert-Code zeigen, Person-Hinweis ins Detail). Vollständig =
    # Wert parsebar UND (falls personengebunden) Person bestimmt.
    detail = ""
    if person_unbestimmt and not luecke:
        luecke = LUECKE_PERSON_UNBESTIMMT
    elif person_unbestimmt and luecke:
        detail = "person unbestimmt"

    if by_design and not luecke and not person_unbestimmt:
        return {
            "datei": pdf_name,
            "dokumenttyp": belegtyp,
            "name": name,
            "wert": "",
            "status": STATUS_NA_BY_DESIGN,
            "luecke": "",
            "luecke_detail": "",
        }

    vollstaendig = not luecke and not person_unbestimmt
    return {
        "datei": pdf_name,
        "dokumenttyp": belegtyp,
        "name": name,
        "wert": wert,
        "status": STATUS_VOLLSTAENDIG if vollstaendig else STATUS_LUECKE,
        "luecke": luecke or "",
        "luecke_detail": detail,
    }


def _plausible_year(entries: list[dict]) -> str:
    """Häufigstes plausibles ``jahr``-Feld (2018..2030) über die ok-Belege."""
    counter: dict[str, int] = defaultdict(int)
    for entry in entries:
        if entry.get("status") != "ok":
            continue
        for field in entry.get("fields", []):
            if field.get("feld") != "jahr":
                continue
            value = field.get("value")
            if not is_real_value(value):
                continue
            digits = "".join(ch for ch in str(value) if ch.isdigit())[:4]
            if len(digits) == 4 and 2018 <= int(digits) <= 2030:
                counter[digits] += 1
    if not counter:
        return ""
    return max(counter, key=lambda k: counter[k])


def build_rows(entries: list[dict]) -> list[dict]:
    """Eine Basis-Zeile pro Entry, in Eingabe-Reihenfolge."""
    return [build_row(e) for e in entries]


def completeness(rows: list[dict]) -> tuple[int, int]:
    """(vollständige Dokumente, Dokumente gesamt).

    Vollständig = Status ``vollständig`` (Typ + Name + Wert vorhanden &
    parsebar). Out-of-Scope zählt NICHT als vollständig, ist aber als Dokument
    im Gesamtzähler (jede Datei ist ein Dokument). So bleibt die Quote ehrlich:
    X von Y eingelesenen Dokumenten sind als Typ+Name+Wert vollständig.
    """
    total = len(rows)
    full = sum(1 for r in rows if r["status"] == STATUS_VOLLSTAENDIG)
    return full, total


def header_line(rows: list[dict], jahr: str) -> str:
    full, total = completeness(rows)
    jahr_str = jahr if jahr else "?"
    return (
        f"Basis-Tabelle Steuerjahr {jahr_str} — "
        f"{full}/{total} Dokumente vollständig (Typ+Name+Wert)"
    )


def gap_counts(rows: list[dict]) -> dict[tuple[str, str], int]:
    """Zähler pro (belegtyp, ursache) — privacy-tauglich (keine Dateinamen)."""
    counts: dict[tuple[str, str], int] = defaultdict(int)
    for row in rows:
        code = row.get("luecke") or ""
        if row["status"] == STATUS_FEHLGESCHLAGEN:
            code = LUECKE_BELEG_FEHLGESCHLAGEN
        if not code:
            continue
        counts[(row["dokumenttyp"], code)] += 1
    return dict(counts)


def _md_escape(s: str) -> str:
    return str(s).replace("|", "\\|")


def render_markdown(rows: list[dict], jahr: str) -> str:
    """Erzeugt die basis_tabelle.md (Kopfzeile + Tabelle)."""
    out: list[str] = []
    out.append(f"# {header_line(rows, jahr)}")
    out.append("")
    out.append("| Datei | Dokumenttyp | Name | Wert | Status | Lücke |")
    out.append("| --- | --- | --- | --- | --- | --- |")
    for row in rows:
        luecke_cell = row.get("luecke") or ""
        detail = row.get("luecke_detail") or ""
        if luecke_cell and detail:
            luecke_cell = f"{luecke_cell} ({detail})"
        elif detail:
            luecke_cell = detail
        out.append(
            "| {datei} | {typ} | {name} | {wert} | {status} | {luecke} |".format(
                datei=_md_escape(row["datei"]),
                typ=_md_escape(row["dokumenttyp"]),
                name=_md_escape(row["name"]),
                wert=_md_escape(row["wert"]) if row["wert"] else "—",
                status=_md_escape(row["status"]),
                luecke=_md_escape(luecke_cell) if luecke_cell else "—",
            )
        )
    out.append("")
    return "\n".join(out)


def render_stats(rows: list[dict], jahr: str) -> str:
    """Privacy-tauglicher Statistik-Block: Kopfzeile + Lücken-Zähler, OHNE
    Dateinamen (für produktive Läufe teilbar)."""
    out: list[str] = []
    out.append(header_line(rows, jahr))
    counts = gap_counts(rows)
    if not counts:
        out.append("Keine Lücken — alle Dokumente vollständig oder out-of-scope.")
        return "\n".join(out)
    out.append("")
    out.append("Lücken pro (Belegtyp, Ursache):")
    for (belegtyp, code) in sorted(counts):
        out.append(f"  {belegtyp} · {code}: {counts[(belegtyp, code)]}")
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Basis-Tabelle (1 Zeile pro Dokument) aus _results_full.json"
    )
    parser.add_argument(
        "--samples", "--input", dest="samples", type=Path,
        default=ROOT / "evals" / "samples_real_2022",
        help="Verzeichnis mit _results_full.json (auch output/latest/json)",
    )
    parser.add_argument(
        "--stats", action="store_true",
        help="Nur Kopfzeilen-Statistik + Lücken-Zähler (ohne Dateinamen) ausgeben",
    )
    args = parser.parse_args()

    samples_dir = args.samples if args.samples.is_absolute() else ROOT / args.samples
    results = samples_dir / "_results_full.json"
    if not results.exists():
        print(f"FEHLT: {results} — erst process_samples_full laufen lassen.",
              file=sys.stderr)
        return 1

    data = json.loads(results.read_text())
    rows = build_rows(data)
    jahr = _plausible_year(data)

    if args.stats:
        print(render_stats(rows, jahr))
        return 0

    output = samples_dir / "basis_tabelle.md"
    output.write_text(render_markdown(rows, jahr))
    full, total = completeness(rows)
    print(f"Geschrieben: {output}")
    print(f"  {full}/{total} Dokumente vollständig (Typ+Name+Wert)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
