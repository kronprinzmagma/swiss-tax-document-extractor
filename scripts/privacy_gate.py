#!/usr/bin/env python3
"""Privacy-Gate für anonymisierte Outputs.

Scannt alle Artefakte in einem Sample-Verzeichnis (json/md/txt/html/xlsx) auf:
- Familiennamen aus family.yaml (alle Varianten, case-sensitive für Vor-/Nachname,
  case-insensitive für Anredeformen)
- Geburtsdaten aus family.yaml.birth_date (alle Formate)
- Personen/Institutionen/IBAN-Nummern/Adressen/Geburtsdaten aus
  privacy_secrets.local.yaml
- Heuristik-Patterns: AHV (756.xxxx.xxxx.xx + glued), IBAN (CH/LI),
  CH-Telefonnummern, E-Mail, PLZ+Strasse, CH-Kontonummern (XX-XXXXXX-X)

Exit 1 bei jedem Treffer (failed). Exit 0 wenn sauber (passed).

Privacy-Constraints für diesen Gate:
- KEIN gematchter String wird in Logs/Report ausgegeben — nur Datei, Zeile,
  Kategorie und ein 8-stelliger sha256-Hash-Präfix (sodass User mit dem
  echten Wert hashen und vergleichen kann).
- family.yaml-Inhalte sind reine Lookup-Quelle, niemals geloggt.

Usage:
    python scripts/privacy_gate.py --samples evals/samples_real_2022
"""
from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from extractors.family import load_family
from extractors.pii_patterns import (
    ACCOUNT_NR_DASH_RE,
    ACCOUNT_NR_DOT_RE,
    AHV_GLUED_RE,
    AHV_REAL_RE,
    EMAIL_RE,
    IBAN_REAL_RE,
    KNOWN_CH_ORTE,
    PHONE_RE,
    PLACEHOLDER_STRINGS,
    PLZ_ORT_LABEL_DENYLIST,
    PLZ_ORT_RE,
    flatten_secret_institutions,
    generate_birthdate_variants,
    generate_name_variants,
    is_ahv_placeholder,
    is_iban_placeholder,
    is_phone_placeholder,
    is_plz_token_year_not_plz,
    load_privacy_secrets,
)


# Whitelist: Placeholder-Ortschaften, die NICHT als PLZ+Ort gewertet werden.
PLZ_ORT_WHITELIST = {"Musterstrasse", "Musterhausen", "Musterort"}

# PLZ-vs-Jahr-Disambiguierung (2xxx) liegt jetzt in
# extractors.pii_patterns.is_plz_token_year_not_plz — Single Source of Truth,
# damit Scrubber, privacy_gate und grade_run identisch entscheiden (Finding 6).


def hash8(s: str) -> str:
    """Liefert die ersten 8 Zeichen eines sha256-Hash hex digests."""
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:8]


def _is_placeholder_match(matched_str: str, category: str) -> bool:
    """Filtert Placeholder-Treffer, die der Anonymizer absichtlich setzt."""
    if matched_str in PLACEHOLDER_STRINGS:
        return True
    if category == "iban_real" and is_iban_placeholder(matched_str):
        return True
    if category == "ahv_real" and is_ahv_placeholder(matched_str):
        return True
    if category == "phone_real" and is_phone_placeholder(matched_str):
        return True
    if category == "plz_ort_real":
        # Wenn der Ort-Teil eine Placeholder-Variante ist
        for w in PLZ_ORT_WHITELIST:
            if w in matched_str:
                return True
        # Anonymizer setzt immer "8000 Musterhausen". In Output-MD-Tabellen
        # wird der Ort-Teil oft truncated ("8000 Mu…") und Musterhausen-
        # Whitelist greift nicht. Wenn PLZ exakt "8000" (Anonymizer-Placeholder)
        # → als Placeholder behandeln.
        _parts = matched_str.split()
        first4 = _parts[0] if _parts else ""
        # PLZ kann mit Country-Prefix kommen ("CH-2502") → reine Ziffern ziehen.
        _plz_digits = re.sub(r"\D", "", first4)[-4:]
        _ort_tok = _parts[1] if len(_parts) > 1 else None
        # A3 (260630-dsn): Ort-Token ist in Wahrheit ein Dokument-Label-Wort
        # ("1267 Abrechnungsperiode") → kein Ort, FP (skip). Trailing-Punctuation
        # strippen, damit "Versicherte:" ebenfalls greift.
        if _ort_tok is not None:
            _ort_clean = _ort_tok.rstrip(".,;:!?»")
            if _ort_clean in PLZ_ORT_LABEL_DENYLIST:
                return True
        if first4 in ("8000", "CH-8000"):
            return True
        # 2xxx ist mehrdeutig (Jahr vs. PLZ). Gemeinsame Heuristik mit
        # Scrubber + grade_run: nur wenn KEIN bekannter Ort folgt → Jahres-FP
        # (skip). Echte 2xxx-PLZ mit bekanntem Ort (2502 Biel) bleiben Treffer.
        if is_plz_token_year_not_plz(_plz_digits, _ort_tok):
            return True
        # "1900 Xxx" ist meist Folge des Birthdate-Placeholder "01.01.1900";
        # die existierende 4-stellige PLZ "1900" ist Saint-Maurice (sehr selten)
        # — wir gewichten Privacy-Schutz höher als Spurious-Match.
        if first4 == "1900":
            return True
    if category == "account_nr_real":
        # Anonymizer-Platzhalter sind "KONTO-XXXX" oder "000.000.000".
        if matched_str in {"000.000.000", "000-000-0"}:
            return True
        # Kontonummern aus PDF-Stempel-IDs ("1-1-2022"-artig) → niedrige Konfidenz
    return False


def build_family_pattern_list(family) -> list[tuple[str, str, re.Pattern]]:
    """Erzeugt für jeden family.yaml-Eintrag eine Liste (kategorie, fp_id, regex).

    fp_id (fingerprint id) ist der sha256-Hash-Prefix des Patterns selbst —
    damit kann der Report sagen "family_name_variant fp=a8b3c7e1" ohne den
    echten Namen zu leaken. User kann lokal hashen und Variante identifizieren.

    Patterns werden case-sensitive für Standalone-Tokens (Vor-/Nachname)
    erzeugt, aber als Word-Boundary regex.
    """
    patterns: list[tuple[str, str, re.Pattern]] = []
    if family is None:
        return patterns
    for m in family.members:
        for variant in generate_name_variants(m.first_name, m.last_name):
            if not variant:
                continue
            esc = re.escape(variant)
            # Word-Boundary: nicht Teil eines längeren Worts, aber auch
            # Glued-Varianten ohne Whitespace müssen matchen.
            if any(c.isspace() or c in ".,-" for c in variant):
                # Phrasen mit Trennzeichen: exakte Substring-Suche (keine \b nötig)
                pat = re.compile(esc)
            else:
                # Standalone-Token: \b davor und danach.
                pat = re.compile(rf"(?<![A-Za-zÀ-ÿ]){esc}(?![A-Za-zÀ-ÿ])")
            patterns.append(("family_name_variant", hash8(variant), pat))
        if m.birth_date:
            for bd in generate_birthdate_variants(m.birth_date):
                if bd == "01.01.1900":
                    continue
                pat = re.compile(re.escape(bd))
                patterns.append(("birthdate_real", hash8(bd), pat))
        if m.ahv:
            patterns.append(("ahv_real", hash8(m.ahv), re.compile(re.escape(m.ahv))))
    return patterns


def build_secret_pattern_list(secrets: dict) -> list[tuple[str, str, re.Pattern]]:
    """Patterns aus privacy_secrets.local.yaml."""
    patterns: list[tuple[str, str, re.Pattern]] = []
    for name in (secrets.get("persons") or []):
        if not isinstance(name, str) or not name.strip():
            continue
        # Eine Person aus secrets kann eine Form wie "Initialform N. Muster" sein;
        # wir generieren KEINE weiteren Varianten (das macht der Anonymizer
        # bereits), sondern matchen den Eintrag exakt.
        patterns.append(("secret_person", hash8(name), re.compile(re.escape(name))))
    for inst in flatten_secret_institutions(secrets):
        if inst.strip():
            patterns.append(("secret_institution", hash8(inst), re.compile(re.escape(inst))))
    for ac in (secrets.get("iban_account_numbers") or []):
        if isinstance(ac, str) and ac.strip():
            patterns.append(("secret_account", hash8(ac), re.compile(re.escape(ac))))
    for addr in (secrets.get("addresses") or []):
        if isinstance(addr, str) and addr.strip():
            patterns.append(("secret_address", hash8(addr), re.compile(re.escape(addr))))
    for bd in (secrets.get("birthdates") or []):
        if isinstance(bd, str) and bd.strip():
            if re.match(r"^\d{4}-\d{2}-\d{2}$", bd):
                for v in generate_birthdate_variants(bd):
                    if v == "01.01.1900":
                        continue
                    patterns.append(("secret_birthdate", hash8(v), re.compile(re.escape(v))))
            else:
                patterns.append(("secret_birthdate", hash8(bd), re.compile(re.escape(bd))))
    return patterns


HEURISTIC_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("iban_real", IBAN_REAL_RE),
    ("ahv_real", AHV_REAL_RE),
    ("ahv_real", AHV_GLUED_RE),
    ("phone_real", PHONE_RE),
    ("email_real", EMAIL_RE),
    ("plz_ort_real", PLZ_ORT_RE),
    ("account_nr_real", ACCOUNT_NR_DASH_RE),
    ("account_nr_real", ACCOUNT_NR_DOT_RE),
]


def _extract_xlsx_lines(path: Path) -> list[str]:
    """Extrahiert alle String-Zellen einer xlsx-Datei als Pseudo-Zeilen.

    Eine Zeile pro Arbeitsblatt-Zeile (Zellen mit Tab gejoint), damit der
    zeilenweise Scanner identisch arbeitet. Zellinhalte werden nie geloggt.
    """
    try:
        import openpyxl  # lazy: nur gebraucht, wenn xlsx im Scan-Set liegt
    except ImportError:
        print(f"WARN: openpyxl fehlt — {path.name} wird NICHT gescannt.",
              file=sys.stderr)
        return []
    try:
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    except Exception:
        print(f"WARN: {path.name} nicht als xlsx lesbar — uebersprungen.",
              file=sys.stderr)
        return []
    lines: list[str] = []
    for ws in wb.worksheets:
        for row in ws.iter_rows(values_only=True):
            cells = [str(c) for c in row if isinstance(c, str)]
            lines.append("\t".join(cells))
    wb.close()
    return lines


def scan_file(
    path: Path,
    family_patterns: list[tuple[str, str, re.Pattern]],
    secret_patterns: list[tuple[str, str, re.Pattern]],
) -> list[tuple[int, str, str]]:
    """Scannt eine Datei zeilenweise. Liefert eine Liste von (line, category, fp).

    Niemals der echte Match-String zurückgegeben. xlsx-Dateien werden
    textuell (Zell-Strings via openpyxl) extrahiert und gleich behandelt.
    """
    if path.suffix.lower() == ".xlsx":
        lines = _extract_xlsx_lines(path)
    else:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return []
        lines = text.splitlines()
    hits: list[tuple[int, str, str]] = []
    for lineno, line in enumerate(lines, start=1):
        # 1. Family-Patterns
        for category, fp_id, pat in family_patterns:
            for m in pat.finditer(line):
                if _is_placeholder_match(m.group(0), category):
                    continue
                hits.append((lineno, category, fp_id))
        # 2. Secret-Patterns
        for category, fp_id, pat in secret_patterns:
            for m in pat.finditer(line):
                if _is_placeholder_match(m.group(0), category):
                    continue
                hits.append((lineno, category, fp_id))
        # 3. Heuristik-Patterns
        for category, pat in HEURISTIC_PATTERNS:
            for m in pat.finditer(line):
                matched = m.group(0)
                if _is_placeholder_match(matched, category):
                    continue
                hits.append((lineno, category, hash8(matched)))
    return hits


def _run_staged(args) -> int:
    """Pre-Commit-Modus: scannt die git-gestagten Text-Dateien auf PII.

    Wird vom .pre-commit-config.yaml-Hook aufgerufen. Blockiert den Commit
    (Exit 1), wenn rohe PII in gestagten Dateien gefunden wird.
    """
    try:
        out = subprocess.run(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
            capture_output=True, text=True, cwd=ROOT, check=True,
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        print(f"WARN: git-Staging nicht lesbar ({type(e).__name__}) — Hook uebersprungen.",
              file=sys.stderr)
        return 0

    exts = [e.strip() for e in args.extensions.split(",") if e.strip()]
    staged = [
        ROOT / line.strip() for line in out.splitlines()
        if line.strip() and Path(line.strip()).suffix.lower() in exts
    ]
    staged = [p for p in staged if p.is_file() and not p.name.endswith(".truth.json")]
    if not staged:
        print("OK Privacy-Gate (staged): keine relevanten Text-Dateien gestaged.")
        return 0

    try:
        family = load_family(ROOT / "family.yaml")
    except Exception:
        family = None
    secrets = load_privacy_secrets(ROOT / "privacy_secrets.local.yaml")
    family_patterns = build_family_pattern_list(family)
    secret_patterns = build_secret_pattern_list(secrets)

    per_file_hits: dict[str, list[tuple[int, str, str]]] = {}
    for f in staged:
        hits = scan_file(f, family_patterns, secret_patterns)
        if hits:
            per_file_hits[str(f.relative_to(ROOT))] = hits

    if not per_file_hits:
        print(f"OK Privacy-Gate (staged): {len(staged)} Dateien sauber.")
        return 0

    total = sum(len(h) for h in per_file_hits.values())
    print(f"FAIL Privacy-Gate (staged): {total} PII-Treffer in {len(per_file_hits)} gestagten Dateien.")
    for fname in sorted(per_file_hits):
        cats: Counter = Counter()
        for _, c, _ in per_file_hits[fname]:
            cats[c] += 1
        print(f"  - {fname}: " + ", ".join(f"{c}×{n}" for c, n in sorted(cats.items())))
    print("Commit blockiert. PII entfernen oder anonymisieren.")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Privacy-Gate für anonymisierte Sample-Artefakte")
    parser.add_argument(
        "--samples",
        type=Path,
        default=ROOT / "evals" / "samples_real_2022",
        help="Verzeichnis mit anonymisierten Outputs",
    )
    parser.add_argument(
        "--extensions",
        type=str,
        default=".json,.md,.txt,.html,.xlsx",
        help="Komma-getrennte Liste der zu scannenden Datei-Endungen",
    )
    parser.add_argument(
        "--examples",
        type=int,
        default=3,
        help="Max. Anzahl Beispiel-Treffer pro Datei im Report (nur Hash)",
    )
    parser.add_argument(
        "--staged",
        action="store_true",
        help="Pre-Commit-Modus: scannt die git-gestagten Dateien statt --samples.",
    )
    args = parser.parse_args()

    if args.staged:
        return _run_staged(args)

    if not args.samples.is_dir():
        print(f"FEHLER: {args.samples} existiert nicht oder ist kein Verzeichnis", file=sys.stderr)
        return 2

    try:
        family = load_family(ROOT / "family.yaml")
    except Exception as e:
        print(f"WARN: family.yaml nicht ladbar: {type(e).__name__}", file=sys.stderr)
        family = None
    secrets = load_privacy_secrets(ROOT / "privacy_secrets.local.yaml")

    family_patterns = build_family_pattern_list(family)
    secret_patterns = build_secret_pattern_list(secrets)

    exts = [e.strip() for e in args.extensions.split(",") if e.strip()]
    # Truth-Files (*.truth.json) sind Test-Referenzwerte für eval_anchors,
    # nicht User-Output — daher vom Privacy-Gate ausgeschlossen.
    # anon_truth_mapping.json enthält intentional Original-PDF-Stems (mit
    # echten IBANs/Namen als Dateipfade) — internes Metadaten-File, kein Output.
    EXCLUDED_NAMES = frozenset({
        "anon_truth_mapping.json",
        # Burn-down-Doku referenziert beispielhaft Firmen-Namen (in Quote-Form
        # zur Erklärung der LLM-Halluzinationen) — kein User-Output, keine PII.
        "manual_review_burndown.md",
    })
    files = sorted(
        p for p in args.samples.rglob("*")
        if p.is_file() and p.suffix.lower() in exts
        and not p.name.endswith(".truth.json")
        and p.name not in EXCLUDED_NAMES
    )

    total_lines = 0
    per_file_hits: dict[str, list[tuple[int, str, str]]] = defaultdict(list)
    category_counter: Counter = Counter()
    for f in files:
        if f.suffix.lower() != ".xlsx":
            try:
                total_lines += sum(1 for _ in f.open(encoding="utf-8", errors="replace"))
            except OSError:
                continue
        hits = scan_file(f, family_patterns, secret_patterns)
        if hits:
            per_file_hits[str(f.relative_to(args.samples))] = hits
            for _, cat, _ in hits:
                category_counter[cat] += 1

    if not per_file_hits:
        print(f"OK Privacy-Gate sauber ({len(files)} Dateien, {total_lines} Zeilen gescannt)")
        return 0

    total_hits = sum(len(h) for h in per_file_hits.values())
    print(f"FAIL Privacy-Gate auf {args.samples} ({total_hits} Treffer in {len(per_file_hits)} Dateien)")
    print()
    print("Treffer pro Kategorie:")
    for cat, cnt in sorted(category_counter.items(), key=lambda x: -x[1]):
        print(f"  - {cat}: {cnt}")
    print()
    print("Treffer pro Datei (kategoriespezifisch):")
    for fname in sorted(per_file_hits.keys()):
        cats: Counter = Counter()
        for _, c, _ in per_file_hits[fname]:
            cats[c] += 1
        cat_str = ", ".join(f"{c}×{n}" for c, n in sorted(cats.items()))
        print(f"  - {fname}: {cat_str}")
    print()
    print(f"Beispiel-Hashes pro Datei (max {args.examples} pro Datei, fp = sha256-Prefix-8):")
    for fname in sorted(per_file_hits.keys()):
        seen: set[tuple[str, str]] = set()
        shown = 0
        for lineno, cat, fp in per_file_hits[fname]:
            key = (cat, fp)
            if key in seen:
                continue
            seen.add(key)
            print(f"  - {fname}:Z.{lineno} {cat} fp={fp}")
            shown += 1
            if shown >= args.examples:
                break
    return 1


if __name__ == "__main__":
    sys.exit(main())
