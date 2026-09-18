"""Single-Source-of-Truth Grader für die Pipeline-Outputs.

Wendet 19 harte Check-Kriterien auf einen Sample-Ordner an. Exit 0 nur wenn
**alle** Checks PASS. Bei FAIL wird ein vollständiger Report ausgegeben und
exit 1 geliefert.

Zweck: einziges Done-Kriterium für „Pipeline ist fertig". Keine punktuellen
„besser geworden"-Aussagen — nur PASS oder FAIL.

Aufruf::

    python scripts/grade_run.py --samples evals/samples_real_2022

"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from extractors.pii_patterns import (
    IBAN_REAL_RE,
    AHV_REAL_RE,
    AHV_GLUED_RE,
    PHONE_RE,
    EMAIL_RE,
    PLZ_ORT_RE,
    KNOWN_CH_ORTE,
    ACCOUNT_NR_DASH_RE,
    ACCOUNT_NR_DOT_RE,
    PLACEHOLDER_STRINGS,
    is_iban_placeholder,
    is_ahv_placeholder,
    is_phone_placeholder,
    is_plz_token_year_not_plz,
    load_privacy_secrets,
)
from extractors.family import load_family


# --------------------------------------------------------------------------- #
# Konstanten                                                                  #
# --------------------------------------------------------------------------- #

ALLOWED_ROLES: set[str] = {
    "elternteil_1", "elternteil_2", "kind_1", "kind_2",
    "familie", "unbekannt/manuell",
}

# Aussteller müssen zu diesen Patterns matchen (anonymisierte stabile IDs)
# Beispiele: "BANK-A AG", "BROKER-A Finance AG", "KK-A Versicherungen AG",
#            "STIFTUNG_3A-A Vorsorgestiftung 3a", "ARBEITGEBER-A".
# Klartext-Firmennamen wie "Raiffeisenbank", "Post CH AG", "Sanitas" sind
# damit verboten.
AUSSTELLER_PATTERN = re.compile(
    r"^(?:BANK|BROKER|KK|STIFTUNG_3A|ARBEITGEBER|FIRMA|ANBIETER|INST|INSTITUT|"
    r"LEISTUNGSERBRINGER)-[A-Z][A-Z0-9_]*(?:\s+[A-Za-z0-9äöüÄÖÜß\.\-]+)*$"
)
# Manual-review-Marker werden im UI als "⚠ <grund>" gerendert (build_tax_output.
# fmt_display_value); diese zählen als explizite "needs sichtprüfung"-Label und
# sind kein PII-Leak.
_AUSSTELLER_MR_PATTERN = re.compile(r"^⚠\s+\S")
AUSSTELLER_UNKNOWN_LABELS: set[str] = {"unbekannt/manuell", "—", "manual_review"}


@dataclass
class CheckResult:
    name: str
    passed: bool
    summary: str
    details: list[str]


# --------------------------------------------------------------------------- #
# Checks                                                                      #
# --------------------------------------------------------------------------- #


def check_privacy_gate(samples: Path) -> CheckResult:
    """1. privacy_gate.py muss PASS liefern."""
    cmd = [sys.executable, str(ROOT / "scripts" / "privacy_gate.py"),
           "--samples", str(samples)]
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT)
    passed = r.returncode == 0 and "OK Privacy-Gate sauber" in r.stdout
    details = []
    if not passed:
        details = (r.stdout + "\n" + r.stderr).strip().splitlines()[-20:]
    return CheckResult(
        "privacy_gate", passed,
        "PASS" if passed else f"FAIL (exit={r.returncode})",
        details,
    )


def check_privacy_secrets(samples: Path) -> CheckResult:
    """2. privacy_secrets.local.yaml muss existieren ODER Limitierung deklariert."""
    secrets_path = ROOT / "privacy_secrets.local.yaml"
    if secrets_path.exists():
        secrets = load_privacy_secrets(secrets_path)
        n_institutions = sum(
            len(v) if isinstance(v, list) else 0
            for v in (secrets.get("institutions") or {}).values()
        ) if isinstance(secrets.get("institutions"), dict) else len(
            secrets.get("institutions") or [])
        n_addresses = len(secrets.get("addresses") or [])
        if n_institutions == 0 and n_addresses == 0:
            return CheckResult(
                "privacy_secrets", False,
                f"FAIL: {secrets_path.name} existiert, ist aber leer "
                f"(institutions=0, addresses=0). Echte Firmen/Banken/KKs "
                f"werden so nicht erkannt.",
                [f"Datei: {secrets_path}"],
            )
        return CheckResult(
            "privacy_secrets", True,
            f"PASS: institutions={n_institutions}, addresses={n_addresses}",
            [],
        )
    return CheckResult(
        "privacy_secrets", False,
        f"FAIL: {secrets_path.name} fehlt. Ohne diese Datei kann der "
        f"Anonymizer reale Firmen-/Banknamen nicht erkennen, und der Gate "
        f"kann Restbestände nicht prüfen.",
        [f"Erwartete Datei: {secrets_path}",
         "Template: privacy_secrets.local.example.yaml"],
    )


_JSON_MAPPING_EXCLUDES = frozenset({"anon_truth_mapping", "field_exceptions"})


def check_json_truth_mapping(samples: Path) -> CheckResult:
    """3+4. Jede .json hat passendes .truth.json; keine verwaisten Truth-Files."""
    json_files = {
        p.stem for p in samples.glob("*.json")
        if not p.name.endswith(".truth.json")
        and not p.name.startswith("_")
        and p.stem not in _JSON_MAPPING_EXCLUDES
    }
    truth_files = {
        p.name.removesuffix(".truth.json")
        for p in samples.glob("*.truth.json")
    }
    json_only = json_files - truth_files
    truth_only = truth_files - json_files
    mapping_file = samples / "anon_truth_mapping.json"
    has_mapping = mapping_file.exists()

    if has_mapping:
        # Mapping-Datei erlaubt JSON-Stem -> Truth-Stem-Verknüpfung
        try:
            mapping = json.loads(mapping_file.read_text())
            for json_stem in list(json_only):
                truth_stem = mapping.get(json_stem)
                if truth_stem and truth_stem in truth_files:
                    json_only.discard(json_stem)
                    truth_only.discard(truth_stem)
        except json.JSONDecodeError:
            pass

    if not json_only and not truth_only:
        return CheckResult(
            "json_truth_mapping", True,
            f"PASS: {len(json_files)} JSON ↔ {len(truth_files)} Truth.",
            [],
        )
    details = []
    if json_only:
        details.append(f"{len(json_only)} JSON ohne Truth-File:")
        for s in sorted(json_only)[:10]:
            details.append(f"  - {s}")
    if truth_only:
        details.append(f"{len(truth_only)} Truth-File ohne JSON:")
        for s in sorted(truth_only)[:10]:
            details.append(f"  - {s}")
    return CheckResult(
        "json_truth_mapping", False,
        f"FAIL: {len(json_only)} JSON-only, {len(truth_only)} Truth-only.",
        details,
    )


def check_eval_anchors(samples: Path) -> CheckResult:
    """5. eval_anchors.py muss 100% PASS liefern."""
    cmd = [sys.executable, str(ROOT / "scripts" / "eval_anchors.py"),
           "--samples", str(samples)]
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT)
    # Parse "X/Y Checks bestanden"
    out = r.stdout
    m = re.search(r"(\d+)/(\d+)\s+Checks", out)
    if m:
        passed_n = int(m.group(1))
        total = int(m.group(2))
        ok = passed_n == total and r.returncode == 0
        summary = f"{'PASS' if ok else 'FAIL'}: {passed_n}/{total}"
    else:
        ok = False
        summary = f"FAIL: konnte Output nicht parsen (exit={r.returncode})"
    details = []
    if not ok:
        # Zeige Failures-Block
        fail_lines: list[str] = []
        capture = False
        for ln in out.splitlines():
            if ln.startswith("Failures:") or ln.startswith("Braucht Sichtprüfung"):
                capture = True
            if capture:
                fail_lines.append(ln)
        details = fail_lines[:30]
    return CheckResult("eval_anchors", ok, summary, details)


def check_eval_coverage(samples: Path) -> CheckResult:
    """5b. Truth-Stringwert-Match UND Label-Nähe müssen > 0/0 sein ODER
    eine dokumentierte Ausnahme muss vorliegen.

    Diese Checks laufen nur, wenn Truth-Files `fields`-Blöcke mit
    `near_label`-Einträgen haben. Sind beide 0/0, ist die Anker-Qualität
    faktisch ungeprüft — das muss im Report sichtbar sein.

    Ausnahme-Datei: ``eval_coverage_exception.md`` im Samples-Ordner
    (Owner-signierte Erklärung, warum 0/0 akzeptabel ist).
    """
    cmd = [sys.executable, str(ROOT / "scripts" / "eval_anchors.py"),
           "--samples", str(samples)]
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT)
    out = r.stdout
    # Parse "Truth-Stringwert-Match (value)    : X/Y"
    sm = re.search(r"Truth-Stringwert-Match.*?:\s*(\d+)/(\d+)", out)
    lm = re.search(r"Label-Nähe.*?:\s*(\d+)/(\d+)", out)
    sv_n = int(sm.group(1)) if sm else -1
    sv_d = int(sm.group(2)) if sm else -1
    ln_n = int(lm.group(1)) if lm else -1
    ln_d = int(lm.group(2)) if lm else -1

    # Fail-closed (Finding 7): wenn die Regexes den eval_anchors-Output NICHT
    # parsen können (-1-Werte), darf der Check NICHT stillschweigend PASS
    # zurückgeben. Ein unparsebarer Report bedeutet: Anker-Qualität faktisch
    # ungeprüft → rot.
    if sv_d < 0 or ln_d < 0:
        return CheckResult(
            "eval_coverage", False,
            "FAIL: eval_anchors-Output nicht parsebar (Stringwert-Match/Label-Nähe "
            "nicht gefunden). Coverage ungeprüft → fail-closed.",
            [
                f"eval_anchors exit={r.returncode}",
                "Erwartete Zeilen 'Truth-Stringwert-Match: X/Y' und 'Label-Nähe: X/Y' fehlen.",
                (r.stderr or "")[:200],
            ],
        )

    both_zero = (sv_d == 0 and ln_d == 0)
    exception_file = samples / "eval_coverage_exception.md"

    if not both_zero:
        return CheckResult(
            "eval_coverage", True,
            f"PASS: Stringwert-Match {sv_n}/{sv_d}, Label-Nähe {ln_n}/{ln_d}",
            [],
        )
    if exception_file.exists():
        return CheckResult(
            "eval_coverage", True,
            f"PASS (dokumentierte Ausnahme): Truth-Stringwert-Match 0/0, "
            f"Label-Nähe 0/0 — keine annotierten near_label in Truth-Files. "
            f"Eval prüft nur Belegtyp + Pflichtfelder + Plausibilität. "
            f"Siehe {exception_file.name}.",
            [
                "⚠ UNKONTROLLIERT: Ob extrahierte Werte mit Truth übereinstimmen, wird NICHT geprüft.",
                "⚠ UNKONTROLLIERT: Ob Anker räumlich neben dem korrekten Label liegen, wird NICHT geprüft.",
                f"  Ausnahme dokumentiert in: {exception_file.name}",
                "  Aktivierung: fields.X.near_label in Truth-Files eintragen.",
            ],
        )
    return CheckResult(
        "eval_coverage", False,
        "FAIL: Truth-Stringwert-Match 0/0 UND Label-Nähe 0/0 ohne dokumentierte Ausnahme.",
        [
            "Truth-Files haben keine 'fields'-Blöcke mit 'near_label'.",
            "Entweder Truth-Files annotieren ODER eval_coverage_exception.md erstellen.",
        ],
    )


def check_person_required_roles(samples: Path) -> CheckResult:
    """8b. Auto-Zeilen in personensensitiven Steuerbereichen dürfen nicht
    'unbekannt/manuell' als Person/Rolle haben.

    Betrifft: Lohnausweis/Einkommen, KK-Prämien, Säule 3a,
    Kinderbetreuung, Krankheitskosten.
    """
    f = samples / "steuer_uebertragung.md"
    if not f.exists():
        return CheckResult("person_required_roles", False, f"FAIL: {f.name} fehlt.", [])
    rows = _parse_table_rows(f.read_text())
    # Personensensitive Steuerbereiche (Keyword-Match auf Steuerbereich-Spalte)
    SENSITIVE_KEYWORDS = ("Einkommen", "Abzüge", "Kinderbetreuung",
                          "Krankheit", "Säule", "3a")
    bad: list[str] = []
    for section, cells in rows:
        if not section.startswith("🟢"):
            continue  # nur Auto-Zeilen prüfen
        if len(cells) < 9:
            continue
        sb = cells[0].strip()
        role = cells[2].strip()
        beschr = cells[4].strip()
        if role != "unbekannt/manuell":
            continue
        if any(kw in sb or kw in beschr for kw in SENSITIVE_KEYWORDS):
            bad.append(
                f"Auto-Zeile mit unbekannt/manuell: [{sb}] {beschr}"
            )
    if bad:
        return CheckResult(
            "person_required_roles", False,
            f"FAIL: {len(bad)} Auto-Zeile(n) mit unbekannt/manuell in "
            f"personensensitivem Bereich.",
            bad[:10],
        )
    return CheckResult(
        "person_required_roles", True,
        "PASS: keine Auto-Zeilen mit unbekannt/manuell in personensensitiven Bereichen.",
        [],
    )


def check_focus_matrix(samples: Path) -> CheckResult:
    """6. _focus_matrix.md existiert und enthält kein F5/F6/F7."""
    f = samples / "_focus_matrix.md"
    if not f.exists():
        return CheckResult(
            "focus_matrix", False,
            f"FAIL: {f.name} fehlt im Sample-Ordner.",
            [f"Generator: scripts/focus_matrix.py --samples {samples}"],
        )
    text = f.read_text()
    # Wir suchen "F5", "F6", "F7" in der Hauptklasse-Spalte. Sentinels in
    # Tabellen-Rows haben Form "| F5 |".
    forbidden = []
    for token in ("F5", "F6", "F7"):
        if re.search(rf"\|\s*{token}\s*\|", text):
            forbidden.append(token)
    if forbidden:
        return CheckResult(
            "focus_matrix", False,
            f"FAIL: enthält {', '.join(forbidden)} (Fehlerklasse(n) noch offen).",
            [],
        )
    return CheckResult("focus_matrix", True, "PASS: keine F5/F6/F7.", [])


def check_artifact_mtimes(samples: Path) -> CheckResult:
    """7. steuer_uebertragung.md + nachweis_anhang.md neuer als _results_full.json."""
    results = samples / "_results_full.json"
    uebertragung = samples / "steuer_uebertragung.md"
    anhang = samples / "nachweis_anhang.md"
    missing = [p.name for p in (results, uebertragung, anhang) if not p.exists()]
    if missing:
        return CheckResult(
            "artifact_mtimes", False,
            f"FAIL: fehlende Artefakte: {', '.join(missing)}",
            [],
        )
    r_m = results.stat().st_mtime
    u_m = uebertragung.stat().st_mtime
    a_m = anhang.stat().st_mtime
    if u_m >= r_m and a_m >= r_m:
        return CheckResult("artifact_mtimes", True, "PASS", [])
    bad = []
    if u_m < r_m:
        bad.append(f"steuer_uebertragung.md älter als _results_full.json")
    if a_m < r_m:
        bad.append(f"nachweis_anhang.md älter als _results_full.json")
    return CheckResult("artifact_mtimes", False, "FAIL: " + "; ".join(bad), [])


def check_uebertragung_pii(samples: Path) -> CheckResult:
    """8. steuer_uebertragung.md enthält keine rohen PII.

    Zusätzlich zum allgemeinen Privacy-Gate prüfen wir hier *spezifisch* auf
    Klartext-Familiennamen aus family.yaml, IBAN/AHV/Phone-Heuristik und PLZ+Ort.
    """
    f = samples / "steuer_uebertragung.md"
    if not f.exists():
        return CheckResult("uebertragung_pii", False, f"FAIL: {f.name} fehlt.", [])
    text = f.read_text()
    family = load_family(ROOT / "family.yaml")
    findings: list[str] = []

    # Fantasy-Vollnamen (vom Anonymizer gesetzt). Whitelist greift NUR, wenn die
    # GANZE Variante exakt einer dieser Tokens/Vollnamen entspricht — NICHT bei
    # Substring-Treffern (Finding 8). Ein realer Name "Hans Müller" darf nicht
    # durchrutschen, nur weil "Hans" im Fantasy-Pool ist.
    _FANTASY_TOKENS = {"Muster", "Hans", "Maria", "Lina", "Tim", "Anna", "Peter"}
    _FANTASY_FULLNAMES = {
        f"{vn} {nn}" for vn in {"Hans", "Maria", "Lina", "Tim", "Anna", "Peter"}
        for nn in {"Muster"}
    }

    def _is_fantasy_whitelisted(variant: str) -> bool:
        v = variant.strip()
        # Exakter Einzel-Token (z.B. "Hans") oder exakter Fantasy-Vollname
        # (z.B. "Hans Muster"). Substring-Treffer werden NICHT whitelisted.
        return v in _FANTASY_TOKENS or v in _FANTASY_FULLNAMES

    # Family-Namen (real, alle Varianten — eine direkte case-sensitive Substring-Suche
    # reicht für Klartext-Test)
    if family:
        from extractors.pii_patterns import generate_name_variants
        for m in family.members:
            for v in generate_name_variants(m.first_name, m.last_name):
                if not v:
                    continue
                if v in text and not _is_fantasy_whitelisted(v):
                    findings.append(f"Family-Name-Variante in MD: '{v}'")

    # Heuristik-Patterns
    for label, pat, placeholder_check in [
        ("iban_real", IBAN_REAL_RE, is_iban_placeholder),
        ("ahv_real", AHV_REAL_RE, is_ahv_placeholder),
        ("ahv_glued", AHV_GLUED_RE, is_ahv_placeholder),
        ("phone_real", PHONE_RE, is_phone_placeholder),
        ("email_real", EMAIL_RE, lambda s: s in PLACEHOLDER_STRINGS),
        ("acct_dash", ACCOUNT_NR_DASH_RE, lambda s: s in {"000-000-0"}),
        ("acct_dot", ACCOUNT_NR_DOT_RE, lambda s: s in {"000.000.000"}),
    ]:
        for m in pat.finditer(text):
            if placeholder_check(m.group(0)):
                continue
            findings.append(f"{label}: '{m.group(0)[:40]}'")

    # PLZ + Ort
    for m in PLZ_ORT_RE.finditer(text):
        full = m.group(0)
        ort = m.group(1)
        if ort not in KNOWN_CH_ORTE:
            continue
        if "Musterhausen" in full or "Musterort" in full:
            continue
        first4 = full.split()[0]
        _plz_digits = re.sub(r"\D", "", first4)[-4:]
        # 2xxx-Disambiguierung via Single-Source-Heuristik: da der Ort hier
        # bereits als bekannter CH-Ort verifiziert ist, wird 2502 Biel korrekt
        # als PLZ gemeldet (vorher pauschal als Jahr verworfen — Finding 6).
        if is_plz_token_year_not_plz(_plz_digits, ort):
            continue
        findings.append(f"plz_ort_real: '{full}'")

    if findings:
        return CheckResult(
            "uebertragung_pii", False,
            f"FAIL: {len(findings)} PII-Treffer in steuer_uebertragung.md.",
            findings[:20],
        )
    return CheckResult("uebertragung_pii", True, "PASS", [])


_TABLE_ROW_RE = re.compile(r"^\|(.+)\|\s*$")
_HEADER_RE = re.compile(r"\|\s*Steuerbereich\s*\|", re.IGNORECASE)


def _parse_table_rows(text: str) -> list[tuple[str, list[str]]]:
    """Parsed Markdown-Tabellen-Rows aus steuer_uebertragung.md.

    Returns: list of (section_header, [cells]) — section_header ist der letzte
    H2 vor der Row.
    """
    rows: list[tuple[str, list[str]]] = []
    current_section = ""
    in_data_table = False
    for ln in text.splitlines():
        if ln.startswith("## "):
            current_section = ln[3:].strip()
            in_data_table = False
            continue
        if _HEADER_RE.match(ln):
            in_data_table = True
            continue
        if "---" in ln and "|" in ln:
            continue  # separator
        if in_data_table:
            m = _TABLE_ROW_RE.match(ln)
            if not m:
                in_data_table = False
                continue
            cells = [c.strip() for c in m.group(1).split("|")]
            rows.append((current_section, cells))
    return rows


def check_person_roles(samples: Path) -> CheckResult:
    """9. Person-Spalte: (anonymisierter) Name, optional mit Rolle in Klammern.

    Produktions-Output zeigt den Namen, nicht nur die Rolle (User-Vorgabe).
    Diese Prüfung stellt sicher, dass die Person-Zelle KEINE Adress-/Zahlen-
    Fragmente leakt (PLZ, IBAN, Strasse) und — falls eine Rolle in Klammern
    angehängt ist — diese aus ALLOWED_ROLES stammt.
    Echte-Namen-Schutz läuft separat über uebertragung_pii (family.yaml).
    """
    f = samples / "steuer_uebertragung.md"
    if not f.exists():
        return CheckResult("person_roles", False, f"FAIL: {f.name} fehlt.", [])
    rows = _parse_table_rows(f.read_text())
    _ROLE_SUFFIX = re.compile(r"\s*\(([^)]+)\)\s*$")
    _ADDRESS_LEAK = re.compile(
        r"\d{3,}|[A-Za-zÀ-ÿ]+strasse|[A-Za-zÀ-ÿ]+platz|Postfach|CH\d{2}",
        re.IGNORECASE,
    )
    bad: list[str] = []
    for section, cells in rows:
        if section.startswith("⚪"):
            continue
        if len(cells) < 9:
            continue
        person = cells[2].strip()
        if person in ("—", ""):
            continue
        # Optionalen Rollen-Suffix "(role)" abtrennen und prüfen
        m = _ROLE_SUFFIX.search(person)
        name_part = person
        if m:
            role = m.group(1).strip()
            if role not in ALLOWED_ROLES:
                bad.append(f"[{section[:25]}] ungültige Rolle: '{role}'")
                continue
            name_part = person[:m.start()].strip()
        # Reine Rollen-Labels (ohne Name) sind weiterhin erlaubt
        if name_part in ALLOWED_ROLES:
            continue
        # Name-Teil darf keine Adress-/Zahlen-Fragmente enthalten
        if _ADDRESS_LEAK.search(name_part):
            bad.append(f"[{section[:25]}] Adress-/Zahlen-Leak in Person: '{name_part[:40]}'")
    if bad:
        return CheckResult(
            "person_roles", False,
            f"FAIL: {len(bad)} Zeilen mit problematischem Person-Wert.",
            bad[:15],
        )
    return CheckResult("person_roles", True, "PASS", [])


def check_aussteller_anonymized(samples: Path) -> CheckResult:
    """10. Aussteller-Spalte: nur stabile anonymisierte IDs."""
    f = samples / "steuer_uebertragung.md"
    if not f.exists():
        return CheckResult("aussteller_anonymized", False, f"FAIL: {f.name} fehlt.", [])
    rows = _parse_table_rows(f.read_text())
    bad: list[str] = []
    for section, cells in rows:
        if section.startswith("⚪"):
            continue
        if len(cells) < 9:
            continue
        aussteller = cells[3].strip()
        if aussteller in AUSSTELLER_UNKNOWN_LABELS:
            continue
        if AUSSTELLER_PATTERN.match(aussteller):
            continue
        if _AUSSTELLER_MR_PATTERN.match(aussteller):
            continue
        bad.append(f"[{section[:30]}] Aussteller='{aussteller[:60]}'")
    if bad:
        return CheckResult(
            "aussteller_anonymized", False,
            f"FAIL: {len(bad)} Zeilen mit nicht-anonymisiertem Aussteller.",
            bad[:15],
        )
    return CheckResult("aussteller_anonymized", True, "PASS", [])


def check_auto_rows_clean(samples: Path) -> CheckResult:
    """11. Auto-Zeilen müssen vollständig sein."""
    f = samples / "steuer_uebertragung.md"
    if not f.exists():
        return CheckResult("auto_rows_clean", False, f"FAIL: {f.name} fehlt.", [])
    rows = _parse_table_rows(f.read_text())
    bad: list[str] = []
    for section, cells in rows:
        if not section.startswith("🟢"):
            continue
        if len(cells) < 9:
            continue
        # Spalte 8 (Status) bleibt bewusst aussen vor: dieser Check prueft die
        # Vollstaendigkeit der Zeile, nicht ihre Konfidenz.
        person, kontext, beschreibung, betrag, jahr, quelle = (
            cells[2], cells[3], cells[4], cells[5], cells[6], cells[7]
        )
        problems = []
        if "manual_review" in " ".join(cells):
            problems.append("manual_review-Marker")
        if betrag in ("—", "", "null"):
            problems.append("Betrag fehlt")
        if jahr in ("—", ""):
            problems.append("Jahr fehlt")
        if "S." not in quelle and "(text-regex)" not in quelle and "(text-match)" not in quelle:
            problems.append("Anker fehlt (keine Seite in Quelle)")
        if "⚠" in person or "⚠" in kontext:
            problems.append("Warn-Marker im Kontext")
        if problems:
            bad.append(f"[{beschreibung[:30]}] {', '.join(problems)} | {quelle[:30]}")
    if bad:
        return CheckResult(
            "auto_rows_clean", False,
            f"FAIL: {len(bad)} Auto-Zeilen mit Defekt.",
            bad[:15],
        )
    return CheckResult("auto_rows_clean", True, "PASS", [])


def check_manual_review_documented(samples: Path) -> CheckResult:
    """12. Manuell-prüfen-Zeilen: gezählt und begründet."""
    f = samples / "steuer_uebertragung.md"
    if not f.exists():
        return CheckResult("manual_review_documented", False,
                           f"FAIL: {f.name} fehlt.", [])
    text = f.read_text()
    if "Manuell prüfen" not in text:
        return CheckResult(
            "manual_review_documented", False,
            "FAIL: Sektion 'Manuell prüfen' fehlt im Output.",
            [],
        )
    rows = _parse_table_rows(text)
    manual_rows = [r for sec, r in rows if "Manuell" in sec or "manuell" in sec]
    # Heuristik: jede Manual-Row sollte eine Begründung erkennbar machen — entweder
    # in Beschreibung, Person, oder via Verweis auf nachweis_anhang.md. Wir
    # verlangen mindestens: Sektion existiert, Anzahl wird genannt.
    if "Manuell prüfen (" not in text:
        return CheckResult(
            "manual_review_documented", False,
            "FAIL: Manuell-Sektion ohne explizite Anzahl.",
            [],
        )
    return CheckResult(
        "manual_review_documented", True,
        f"PASS: {len(manual_rows)} Manuell-Zeilen.",
        [],
    )


def check_no_dupe_amount_misuse(samples: Path) -> CheckResult:
    """13. Keine AUTO-Zeile mit identischem Betrag in inkompatiblen Feldern.

    Prüft den gerenderten Output: auto-Rows desselben Belegs dürfen nicht den
    GLEICHEN Betrag für ``Bestand`` und ``Ertrag`` zeigen (Extraktor-Verdacht).
    """
    f = samples / "steuer_uebertragung.md"
    if not f.exists():
        return CheckResult("no_dupe_amount_misuse", False,
                           f"FAIL: {f.name} fehlt.", [])
    rows = _parse_table_rows(f.read_text())
    # group auto-rows by quelle-pdf-name; check for amount-collision across
    # incompatible field-beschreibung-pairs.
    by_pdf: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for section, cells in rows:
        if not section.startswith("🟢"):
            continue
        if len(cells) < 9:
            continue
        beschreibung = cells[4].strip()
        betrag = cells[5].strip()
        quelle = cells[7].strip()
        # PDF-name aus Quelle extrahieren (alles vor "S.")
        m = re.match(r"`([^`]+)`", quelle)
        if not m:
            continue
        pdf = m.group(1)
        by_pdf[pdf].append((beschreibung, betrag))
    bad: list[str] = []
    INCOMPATIBLE_PAIRS = [
        ({"Vermögensstand", "Bestand"}, {"Ertrag", "Zinsertrag", "Bruttoertrag"}),
    ]
    for pdf, items in by_pdf.items():
        # build by-amount index
        amt_index: dict[str, list[str]] = defaultdict(list)
        for beschr, betrag in items:
            if betrag in ("—", "", "0", "0.00", "0.0"):
                continue
            n = betrag.replace("'", "").replace(" ", "").replace(",", ".")
            if n in ("0", "0.0", "0.00"):
                continue
            amt_index[n].append(beschr)
        for amt, beschreibungen in amt_index.items():
            if len(beschreibungen) < 2:
                continue
            for grpA, grpB in INCOMPATIBLE_PAIRS:
                a_hit = any(any(k in b for k in grpA) for b in beschreibungen)
                b_hit = any(any(k in b for k in grpB) for b in beschreibungen)
                if a_hit and b_hit:
                    bad.append(f"{pdf}: Betrag {amt} sowohl als {grpA & set(beschreibungen)} als auch als {grpB & set(beschreibungen)} — Verdacht")
    if bad:
        return CheckResult(
            "no_dupe_amount_misuse", False,
            f"FAIL: {len(bad)} Auto-Row-Kollisionen.",
            bad[:10],
        )
    return CheckResult("no_dupe_amount_misuse", True, "PASS", [])


def check_is_transfer_table(samples: Path) -> CheckResult:
    """14. Output ist Übertragungs-, keine Debug-Tabelle."""
    f = samples / "steuer_uebertragung.md"
    if not f.exists():
        return CheckResult("is_transfer_table", False, f"FAIL: {f.name} fehlt.", [])
    text = f.read_text()
    # Negativ-Check: Debug-Felder dürfen NICHT in der Tabelle stehen
    forbidden_columns = ("BBox", "Snippet", "Konfidenz", "Anker (x0,top)")
    found = [c for c in forbidden_columns if c in text]
    if found:
        return CheckResult(
            "is_transfer_table", False,
            f"FAIL: Debug-Spalten gefunden: {', '.join(found)}",
            [],
        )
    # Positiv-Check: muss die korrekten Spalten haben
    expected = ("Steuerbereich", "Ziffer", "Beschreibung", "Betrag CHF",
                "Quelle", "Status")
    missing = [c for c in expected if c not in text]
    if missing:
        return CheckResult(
            "is_transfer_table", False,
            f"FAIL: Übertragungs-Spalten fehlen: {', '.join(missing)}",
            [],
        )
    return CheckResult("is_transfer_table", True, "PASS", [])


def _print_per_belegtyp_report(samples: Path) -> None:
    """Druckt Auto/Manual/OOS-Verteilung pro Belegtyp (kein PASS/FAIL)."""
    results_path = samples / "_results_full.json"
    table_path = samples / "steuer_uebertragung.md"
    if not results_path.exists() or not table_path.exists():
        return
    try:
        data = json.loads(results_path.read_text())
        file2bt = {r["pdf_name"]: r.get("belegtyp", "?") for r in data}
    except Exception:
        return
    text = table_path.read_text()
    from collections import defaultdict as _dd
    section_counts: dict[str, dict[str, int]] = _dd(lambda: _dd(int))
    current = None
    for ln in text.splitlines():
        if ln.startswith("## 🟢"): current = "auto"; continue
        if ln.startswith("## 🟡"): current = "manual"; continue
        if ln.startswith("## ⚪"): current = "oos"; continue
        if not current:
            continue
        if not ln.startswith("|") or "---" in ln:
            continue
        m = re.search(r"`([^`]+\.pdf)`", ln)
        if not m:
            continue
        fn = m.group(1)
        bt = "?"
        for k, v in file2bt.items():
            if k.rsplit(".", 1)[0] in fn or fn.rsplit(".", 1)[0] in k:
                bt = v
                break
        section_counts[current][bt] += 1
    if not any(section_counts.values()):
        return
    print()
    print("Auto/Manual/OOS pro Belegtyp:")
    print(f"  {'Belegtyp':<35s} {'auto':>5s} {'manual':>7s} {'oos':>5s}")
    print(f"  {'-'*35} {'-'*5} {'-'*7} {'-'*5}")
    all_bts = sorted(
        set(section_counts["auto"]) | set(section_counts["manual"]) | set(section_counts["oos"])
    )
    for bt in all_bts:
        a = section_counts["auto"].get(bt, 0)
        m = section_counts["manual"].get(bt, 0)
        o = section_counts["oos"].get(bt, 0)
        print(f"  {bt:<35s} {a:>5d} {m:>7d} {o:>5d}")
    ta = sum(section_counts["auto"].values())
    tm = sum(section_counts["manual"].values())
    to = sum(section_counts["oos"].values())
    print(f"  {'TOTAL':<35s} {ta:>5d} {tm:>7d} {to:>5d}")


def check_addresses_anonymized_intent(samples: Path) -> CheckResult:
    """14. Wenn family-Adressen anonymisiert werden sollen, sollten sie auch
    in privacy_secrets.local.yaml stehen oder per family.yaml verfügbar sein.

    WARN-fall: leere `addresses`-Liste, aber Anonymizer ersetzt
    `Musterstrasse` als Adressen-Fallback. → wir reporten als Hinweis,
    nicht als FAIL (Adress-Erkennung läuft auch ohne Secrets-Liste).
    """
    p = ROOT / "privacy_secrets.local.yaml"
    if not p.exists():
        return CheckResult(
            "addresses_anonymized_intent", True,
            "PASS (Secrets-File fehlt — Hinweis bereits in privacy_secrets-Check)",
            [],
        )
    try:
        import yaml
        d = yaml.safe_load(p.read_text()) or {}
        addrs = d.get("addresses") or []
        if addrs:
            return CheckResult(
                "addresses_anonymized_intent", True,
                f"PASS: addresses={len(addrs)}",
                [],
            )
        # Fail-closed (Finding 7): leere addresses-Liste ist NICHT automatisch
        # ok. family.yaml hat KEINE Adress-Felder, deckt Adressen also nicht ab.
        # Owner muss die Abwesenheit explizit bestätigen, sonst rot.
        confirmed = bool(d.get("addresses_intentionally_empty"))
        if confirmed:
            return CheckResult(
                "addresses_anonymized_intent", True,
                "PASS (addresses_intentionally_empty: true — Owner bestätigt, dass "
                "keine zu anonymisierenden Adressen vorliegen; generische "
                "STREET/PLZ-Heuristik bleibt aktiv).",
                [],
            )
        return CheckResult(
            "addresses_anonymized_intent", False,
            "FAIL: addresses=0 in privacy_secrets und keine explizite Bestätigung. "
            "Entweder addresses-Liste pflegen ODER `addresses_intentionally_empty: true` "
            "setzen (family.yaml deckt Adressen nicht ab).",
            [
                "privacy_secrets.local.yaml: addresses=[] und kein addresses_intentionally_empty.",
            ],
        )
    except Exception as e:
        return CheckResult(
            "addresses_anonymized_intent", False,
            f"FAIL: {type(e).__name__}: {e}",
            [],
        )


def check_manual_review_machine_readable(samples: Path) -> CheckResult:
    """15. Jede Manual-Review-Zeile in steuer_uebertragung.md MUSS eine
    konkrete maschinenlesbare Ursache haben (manual_review:* marker,
    Plausibility-Hinweis, oder dokumentierte Kategorie).

    Akzeptierte Marker (in Aussteller-Spalte): `⚠ <reason>`-Pattern oder
    stable Aussteller-Marker (BANK-A etc.) bei plausibilitäts-bedingten MR.
    """
    f = samples / "steuer_uebertragung.md"
    if not f.exists():
        return CheckResult(
            "manual_review_machine_readable", False,
            f"FAIL: {f.name} fehlt.", [],
        )
    rows = _parse_table_rows(f.read_text())
    nachweis = samples / "nachweis_anhang.md"
    nachweis_text = nachweis.read_text() if nachweis.exists() else ""
    untraceable: list[str] = []
    for section, cells in rows:
        if not section.startswith("🟡"):
            continue
        if len(cells) < 9:
            continue
        aussteller = cells[3].strip()
        beschreibung = cells[4].strip()
        datei_cell = cells[7].strip()
        # Akzeptabel: ⚠-Marker in Aussteller ODER Plausi-Hinweis im Nachweis
        if aussteller.startswith("⚠ "):
            continue
        # Plausi-Hinweis: Nachweis erwähnt manual_review oder plausibility
        fname_m = re.search(r"`([^`]+\.pdf)`", datei_cell)
        if fname_m:
            fn = fname_m.group(1)
            # Suche im Nachweis-Block dieser Datei nach MR-/Plausi-Begründung
            idx = nachweis_text.find(fn)
            if idx >= 0:
                block = nachweis_text[idx:idx+2000]
                if (
                    "manual_review" in block
                    or "Manual-Review-Marker" in block  # Nachweis-Sektion mit Begründung
                    or "Plausibility" in block
                    or "Plausibilitäts" in block
                    or "dupe" in block.lower()
                    or "bestand" in block.lower() and "ertrag" in block.lower()
                ):
                    continue
        untraceable.append(f"[{beschreibung[:40]}] {datei_cell[:40]}")
    if untraceable:
        return CheckResult(
            "manual_review_machine_readable", False,
            f"FAIL: {len(untraceable)} MR-Zeilen ohne maschinenlesbare Ursache",
            untraceable[:10],
        )
    return CheckResult(
        "manual_review_machine_readable", True,
        "PASS: alle MR-Zeilen haben maschinenlesbare Ursache", [],
    )


def check_focus_target_output_matches(samples: Path) -> CheckResult:
    """18. Steuer-Fokus-Tabelle muss in der generierten Übertragungstabelle
    fachlich abgedeckt sein.

    Liest ``steuer_fokus_tabelle.md`` aus dem Samples-Ordner und prüft
    für jeden Zielwert-Block (Lohnausweis, KK, Bank), ob der erwartete
    CHF-Betrag in ``steuer_uebertragung.md`` vorkommt.

    Nicht byte-genau — der Betrag muss als Substring in der Tabelle stehen.
    """
    fokus = samples / "steuer_fokus_tabelle.md"
    uebertrag = samples / "steuer_uebertragung.md"
    if not fokus.exists():
        # Fail-closed (Finding 7): CLAUDE.md definiert Check 18 als Produkt-Check,
        # der "rot blockiert". Fehlt die Zielwert-Datei, ist das Produktziel
        # NICHT verifizierbar → FAIL statt stillem PASS.
        return CheckResult(
            "focus_target_output_matches", False,
            "FAIL: steuer_fokus_tabelle.md fehlt — Fokus-Zielwerte nicht prüfbar "
            "(Produkt-Check, fail-closed).",
            [
                f"Erwartet: {fokus}.",
                "Zielwerte aus STEUER-ZIELWERTE-FOKUS.md in "
                "steuer_fokus_tabelle.md ueberfuehren, um den Check zu erfuellen.",
            ],
        )
    if not uebertrag.exists():
        return CheckResult(
            "focus_target_output_matches", False,
            "FAIL: steuer_uebertragung.md fehlt.", [],
        )
    fokus_text = fokus.read_text()
    ueber_text = uebertrag.read_text()

    def _norm(s: str) -> str:
        return s.replace("'", "").replace(" ", "").replace(",", ".").strip()

    def _stem(s: str) -> str:
        s = s.replace("…", "").strip()
        s = re.sub(r"\.pdf$", "", s, flags=re.IGNORECASE).strip()
        return re.sub(r"\s+", " ", s)

    def _doc_match(a: str, b: str) -> bool:
        a, b = _stem(a), _stem(b)
        if not a or not b:
            return False
        if a == b:
            return True
        # Truncation auf einer Seite ("…") → Prefix-Match (min. 12 Zeichen)
        short, long = (a, b) if len(a) <= len(b) else (b, a)
        return len(short) >= 12 and long.startswith(short)

    # 1. Übertragungstabelle: (quelle_stem, betrag_norm) je Zeile
    ueber_rows: list[tuple[str, str]] = []
    for ln in ueber_text.splitlines():
        if not ln.startswith("|") or "---" in ln or "Steuerbereich" in ln:
            continue
        cells = [c.strip() for c in ln.split("|")[1:-1]]
        if len(cells) < 9:
            continue
        qm = re.search(r"`([^`]+)`", cells[7])
        if not qm:
            continue
        ueber_rows.append((_stem(qm.group(1)), _norm(cells[5])))

    # 2. Fokus-Tabelle: pro Sektion Header lesen → Dokument-Spalte + CHF-Spalten.
    #    Strikte Prüfung: (Dokument, Betrag) muss als (Quelle, Betrag) in der
    #    Übertragungstabelle vorkommen — nicht nur der Betrag irgendwo.
    missing: list[str] = []
    current_section = ""
    doc_col = 0
    amount_cols: list[int] = []
    headers: list[str] = []
    for ln in fokus_text.splitlines():
        if ln.startswith("## "):
            current_section = ln[3:].strip()
            amount_cols = []
            continue
        if not ln.startswith("|"):
            continue
        if "---" in ln:
            continue
        cells = [c.strip() for c in ln.split("|")[1:-1]]
        # Header-Zeile?
        if "Dokument" in ln:
            headers = cells
            doc_col = next((i for i, h in enumerate(cells) if "Dokument" in h), 0)
            amount_cols = [i for i, h in enumerate(cells) if "CHF" in h or "Wert" in h]
            continue
        if not amount_cols:
            continue
        if len(cells) <= max(amount_cols + [doc_col]):
            continue
        docm = re.search(r"`([^`]+)`", cells[doc_col])
        if not docm:
            continue
        doc = docm.group(1)
        for ci in amount_cols:
            betrag = cells[ci].strip()
            if betrag in ("—", "-", "") or not any(c.isdigit() for c in betrag):
                continue
            bn = _norm(betrag)
            found = any(
                bn == ub and _doc_match(doc, us) for us, ub in ueber_rows
            )
            if not found:
                label = headers[ci] if ci < len(headers) else "?"
                missing.append(
                    f"[{current_section[:15]}] {_stem(doc)[:32]} · {label} = {betrag}"
                )

    if missing:
        return CheckResult(
            "focus_target_output_matches", False,
            f"FAIL: {len(missing)} Fokus-Zielwerte fehlen (Dokument+Betrag) "
            f"in steuer_uebertragung.md.",
            missing[:15],
        )
    return CheckResult(
        "focus_target_output_matches", True,
        "PASS: alle Fokus-Zielwerte (Dokument+Betrag) in steuer_uebertragung.md abgedeckt.",
        [],
    )


def check_steueraufstellung_valid(samples: Path) -> CheckResult:
    """19. steueraufstellung.xlsx existiert, ist aktuell und sauber (fail-closed).

    - xlsx muss existieren.
    - mtime >= _results_full.json (xlsx darf nicht älter als die Quelle sein).
    - check_aufstellung.check_workbook (importiert, KEIN subprocess) liefert 0
      Befunde.

    Jeder Fehlerpfad (fehlende Datei, veraltet, Import-/Lauffehler, Befunde) →
    passed=False.
    """
    xlsx = samples / "steueraufstellung.xlsx"
    results = samples / "_results_full.json"
    if not xlsx.exists():
        return CheckResult(
            "steueraufstellung_valid", False,
            "FAIL: steueraufstellung.xlsx fehlt", [],
        )
    if results.exists() and xlsx.stat().st_mtime < results.stat().st_mtime:
        return CheckResult(
            "steueraufstellung_valid", False,
            "FAIL: xlsx älter als _results_full.json", [],
        )
    try:
        from scripts.check_aufstellung import check_workbook
        import openpyxl

        wb = openpyxl.load_workbook(xlsx, data_only=True)
        findings = check_workbook(wb)
    except Exception as e:
        return CheckResult(
            "steueraufstellung_valid", False,
            f"FAIL: check_aufstellung-Fehler: {type(e).__name__}", [],
        )
    if findings:
        return CheckResult(
            "steueraufstellung_valid", False,
            "FAIL: " + ", ".join(f"{k}x{v}" for k, v in sorted(findings.items())),
            [],
        )
    return CheckResult("steueraufstellung_valid", True, "PASS", [])


def check_bestaetigte_werte(samples: Path) -> CheckResult:
    """20. Bestätigte Sollwerte werden weiterhin getroffen (Regressionsschutz).

    Bis hierher misst der Grader ausschliesslich strukturell: Anker vorhanden,
    Pflichtfelder gefüllt, kein PII, Artefakte frisch. Ob die **Zahl stimmt**,
    prüfte nichts — weshalb monatelang ein Bruttorechnungsbetrag als
    Selbstanteil in der Tabelle stehen konnte, mit einwandfreiem Anker.

    Diese Prüfung schliesst die Lücke: was in der Review-Oberfläche bestätigt
    oder korrigiert wurde, liegt als Sollwert in ``korrekturen.json`` und muss
    von jedem späteren Lauf getroffen werden.

    Ohne hinterlegte Sollwerte ist der Check bestanden — er kann nur prüfen,
    was der Mensch bestätigt hat, und das ehrlich zu sagen ist besser, als
    Sicherheit vorzutäuschen.
    """
    from extractors.korrekturen import bericht, lade_alle, vergleiche

    # Im produktiven Lauf liegen die Ergebnisse in output/latest/json/, die
    # heruntergeladene korrektur.csv und damit korrekturen.json aber eine
    # Ebene darueber. Beide Orte pruefen.
    korrekturen = lade_alle(samples)
    if not korrekturen:
        return CheckResult(
            "bestaetigte_werte", True,
            "PASS: keine bestätigten Sollwerte hinterlegt — nichts zu prüfen",
            ["Hinweis: erst mit bestätigten Werten schützt dieser Check vor "
             "Rückfällen. In der Review-Oberfläche bestätigen, dann "
             "apply_korrekturen.py laufen lassen."],
        )

    results = samples / "_results_full.json"
    if not results.exists():
        return CheckResult("bestaetigte_werte", False,
                           "FAIL: _results_full.json fehlt", [])
    try:
        from scripts.build_tax_output import build_rows
        zeilen: list[dict] = []
        for eintrag in json.loads(results.read_text()):
            if eintrag.get("status") == "ok":
                zeilen.extend(build_rows(eintrag))
    except Exception as exc:
        return CheckResult("bestaetigte_werte", False,
                           f"FAIL: Lauf nicht auswertbar ({exc!r})", [])

    abgleiche = vergleiche(korrekturen, zeilen)
    text, sauber = bericht(abgleiche)
    kopf, *rest = text.split("\n")
    return CheckResult(
        "bestaetigte_werte", sauber,
        ("PASS: " if sauber else "FAIL: ") + kopf,
        rest,
    )


CHECKS = [
    check_privacy_gate,
    check_privacy_secrets,
    check_json_truth_mapping,
    check_eval_anchors,
    check_eval_coverage,           # 5b: ehrliche Eval-Abdeckung (Truth/Label)
    check_focus_matrix,
    check_artifact_mtimes,
    check_uebertragung_pii,
    check_person_roles,
    check_person_required_roles,   # 8b: kein unbekannt/manuell in personensensitiven Auto-Zeilen
    check_aussteller_anonymized,
    check_auto_rows_clean,
    check_manual_review_documented,
    check_no_dupe_amount_misuse,
    check_is_transfer_table,
    check_addresses_anonymized_intent,
    check_manual_review_machine_readable,
    check_focus_target_output_matches,   # 18: Fokus-Zielwerte abgedeckt
    check_steueraufstellung_valid,       # 19: xlsx aktuell + sauber (fail-closed)
    check_bestaetigte_werte,             # 20: bestätigte Sollwerte weiterhin getroffen
]


# --------------------------------------------------------------------------- #
# Main                                                                        #
# --------------------------------------------------------------------------- #


def main() -> int:
    parser = argparse.ArgumentParser(description="Single-Source-of-Truth Grader")
    parser.add_argument(
        "--samples", type=Path, default=ROOT / "evals" / "samples_real_2022",
        help="Sample-Ordner mit _results_full.json und Output-MD-Files",
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="Auch PASS-Details ausgeben",
    )
    args = parser.parse_args()

    samples = args.samples if args.samples.is_absolute() else ROOT / args.samples
    if not samples.is_dir():
        print(f"FEHLER: {samples} ist kein Verzeichnis", file=sys.stderr)
        return 2

    print(f"Grade-Run gegen: {samples}")
    print("=" * 78)
    _print_per_belegtyp_report(samples)
    print("=" * 78)
    results: list[CheckResult] = []
    for fn in CHECKS:
        try:
            r = fn(samples)
        except Exception as e:
            r = CheckResult(fn.__name__, False, f"EXCEPTION: {type(e).__name__}: {e}", [])
        results.append(r)
        status = "✓" if r.passed else "✗"
        print(f"  {status} {r.name:30s} {r.summary}")
        if not r.passed and r.details:
            for d in r.details[:8]:
                print(f"      {d}")
            if len(r.details) > 8:
                print(f"      ... ({len(r.details) - 8} weitere)")
        elif args.verbose and r.passed and r.details:
            for d in r.details:
                print(f"      {d}")
    print("=" * 78)
    passed = sum(1 for r in results if r.passed)
    total = len(results)
    if passed == total:
        print(f"PASS — {passed}/{total} Checks OK.")
        return 0
    print(f"FAIL — {passed}/{total} Checks OK, {total - passed} müssen behoben werden.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
