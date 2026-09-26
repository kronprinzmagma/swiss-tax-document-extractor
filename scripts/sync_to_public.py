#!/usr/bin/env python3
"""Sync des privaten steuer-extraktor-Repos in ein anonymisiertes Public-Repo.

Whitelist-basiertes Kopieren (ausschliesslich git-getrackte Dateien) +
Inhaltssubstitution + Leak-Verifikation. Standardmaessig schreibt das Skript
in ein temporaeres Verzeichnis; mit --repo-url (oder PUBLIC_REPO_URL) wird
danach ein einzelner frischer Commit ins oeffentliche Repo force-gepusht.

Verwendung (immer via ``uv run`` — der Import von extractors.pii_patterns
zieht pydantic/pyyaml aus der Projektumgebung):

  uv run python scripts/sync_to_public.py --dry-run
  uv run python scripts/sync_to_public.py --target-dir /pfad/zum/stage
  PUBLIC_REPO_URL=git@github.com:you/swiss-tax-document-extractor.git \\
    uv run python scripts/sync_to_public.py

Exit-Codes: 2 Whitelist/Forbidden-Konflikt, 3 ``git ls-files`` fehlgeschlagen,
4 Datenleck gefunden, 5 Push fehlgeschlagen, 6 Testsuite im Stage rot.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from fnmatch import fnmatch
from pathlib import Path
from typing import Callable

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# Single Source of Truth fuer PII-Muster: dieselben Regexe wie Anonymizer
# und Privacy-Gate, nicht kopiert (D-08).
from extractors.pii_patterns import (  # noqa: E402
    AHV_GLUED_RE,
    AHV_REAL_RE,
    EMAIL_RE,
    IBAN_REAL_RE,
    PHONE_RE,
    PLACEHOLDER_STRINGS,
    is_ahv_placeholder,
    is_iban_placeholder,
    is_phone_placeholder,
)

# ---------------------------------------------------------------------------
# Identitaet des Public-Repos
# ---------------------------------------------------------------------------

PUBLIC_REPO_NAME = "swiss-tax-document-extractor"

# Dieses Skript steht selbst auf der Whitelist und wird unveraendert kopiert.
SELF_REL = "scripts/sync_to_public.py"

# Private Denylist (Echtwerte, Kuerzel): getrackt, aber nie synchronisiert.
PRIVATE_DENYLIST_REL = "scripts/sync_forbidden.private.txt"

# Private Substitutionen (Institutsnamen -> neutrale Marker): getrackt, aber
# nie synchronisiert — die Zuordnung selbst wuerde den Fingerabdruck verraten.
# Format: eine Zeile ``Quelle => Ersatz``; ganze Woerter, case-sensitiv.
PRIVATE_SUBSTITUTIONS_REL = "scripts/sync_substitutions.private.txt"

# Maschinengeneriertes Lockfile: kurze Zahlen kollidieren mit Hashes und
# Paketgroessen, deshalb kein Denylist-Scan (D-12). PII-Muster laufen trotzdem.
DENYLIST_SKIP_FILES: set[str] = {"uv.lock"}

# ---------------------------------------------------------------------------
# Fragmente der verbotenen Identitaets-Strings
#
# Warum Fragmente: Diese Datei wird byte-identisch ins Public-Repo kopiert
# (Substitution der Substitutionstabelle waere Unsinn) und vom Verifier
# trotzdem geprueft. Verbotene Strings duerfen deshalb nirgends wortwoertlich
# hier stehen — Python konkateniert die Literale erst zur Laufzeit.
# Echtwerte (Betraege, Konto-Fragmente, Kuerzel) gehoeren NICHT hierher,
# sondern in die private Denylist; sie wuerden sonst mitveroeffentlicht.
# ---------------------------------------------------------------------------

_VORNAME = "Ni" "ls"
_NACHNAME = "Sei" "ter"
_HOME_USER = _VORNAME.lower() + _NACHNAME.lower()
_GITHUB_USER = "kronprinz" "magma"

# ---------------------------------------------------------------------------
# Substitutionsliste — laengere / spezifischere Strings VOR kuerzeren (D-06)
# ---------------------------------------------------------------------------

SUBSTITUTIONS: list[tuple[str, str]] = [
    # extractors/vollstaendigkeit.py: Beispiel einer Extraktionsausgabe
    (f"{_VORNAME} {_NACHNAME} (elternteil_1)", "Hans Muster (elternteil_1)"),
    # LICENSE: Copyright-Halter
    (f"{_VORNAME} {_NACHNAME}", f"the {PUBLIC_REPO_NAME} authors"),
    # extractors/vollstaendigkeit.py: Docstring
    (f"{_VORNAME}' Grundsatz", "Grundsatz des Anwenders"),
    # evals/test_wertschriften_abteilungen.py: Modul- und Test-Docstring
    (f"{_VORNAME} hat", "Der Anwender hat"),
    # scripts/build_tax_output.py: Docstring des Optimierungszirkels
    (f"{_VORNAME} traegt", "der Anwender traegt"),
    # Fallback fuer alle uebrigen Vornamen-Fundstellen
    (_VORNAME, "der Anwender"),
    # Verweise auf den Planungsordner: nur der Dateiname bleibt stehen
    (".planning/", ""),
]

SUBSTITUTIONS_REGEX: list[tuple[str, str]] = []

# ---------------------------------------------------------------------------
# Verbotene Strings nach Substitution (Leak-Verifier prueft diese)
# ---------------------------------------------------------------------------

FORBIDDEN_AFTER_SUBSTITUTION: list[str] = [
    _VORNAME,
    _NACHNAME,
    _HOME_USER,     # Home-Verzeichnis-Name in absoluten Pfaden
    _GITHUB_USER,   # GitHub-Username gehoert nicht ins anonyme Repo
]


def _is_email_placeholder(value: str) -> bool:
    """Bekannte Beispiel-Adressen (example.com/example.ch) sind kein Leck."""
    if value in PLACEHOLDER_STRINGS:
        return True
    domain = value.rsplit("@", 1)[-1].lower()
    return domain in {"example.com", "example.ch"}


# Pattern-basierter Scan (Defense-in-Depth): faengt auch UNBEKANNTE Lecks
# wie eine echte IBAN oder AHV-Nummer, die versehentlich in Code, Tests
# oder Kommentare geraet. (Label, Regex, Placeholder-Test)
FORBIDDEN_PATTERNS: list[tuple[str, re.Pattern, Callable[[str], bool]]] = [
    ("IBAN", IBAN_REAL_RE, is_iban_placeholder),
    ("AHV", AHV_REAL_RE, is_ahv_placeholder),
    ("AHV", AHV_GLUED_RE, is_ahv_placeholder),
    ("Telefon", PHONE_RE, is_phone_placeholder),
    ("E-Mail", EMAIL_RE, _is_email_placeholder),
]

# Bekannte synthetische Werte aus Tests, Doku und Beispielen — diese duerfen
# im Public-Repo erscheinen (D-07). Jeder NEUE Treffer bricht den Sync ab,
# bis er hier bewusst freigegeben wird. Nur freigeben, was zweifelsfrei
# synthetisch ist; alles andere an der Quelle ersetzen.
ALLOWED_SYNTHETIC: set[str] = {
    "756.1234.5678.97",   # evals/test_cli_phase2.py, test_family.py  # gitleaks:allow
    "+41 11 1111111",     # evals/test_cross_token_pii_2023.py, anonymize_belege.py  # gitleaks:allow
    "+41 44 123 45 67",   # evals/test_pii_patterns.py, Kommentare in anonymize_belege.py  # gitleaks:allow
    "CH9300762011623852957",        # offizielle SIX-Beispiel-IBAN  # gitleaks:allow
    "CH93 0076 2011 6238 5295 7",   # offizielle SIX-Beispiel-IBAN  # gitleaks:allow
    "info@firma.ch",      # scripts/anonymize_belege.py, Beispiel-Domain  # gitleaks:allow
    "git@github.com",     # SSH-URL im Docstring dieses Skripts  # gitleaks:allow
}

# ---------------------------------------------------------------------------
# Whitelist — was nicht hier steht, existiert fuer das Public-Repo nicht.
# Auswahl laeuft ausschliesslich ueber ``git ls-files`` (D-05): gitignorte
# oder untracked Dateien im Working-Tree werden nie gestaged.
# ---------------------------------------------------------------------------

WHITELIST_FILES: list[str] = [
    "steuer_extraktor.py",
    "pyproject.toml",
    "uv.lock",
    "Makefile",
    ".github/workflows/test.yml",
    ".pre-commit-config.yaml",
    "STEUER-INVENTAR.md",
    "family.yaml.example",
    "privacy_secrets.local.example.yaml",
    "LICENSE",
]

# (Verzeichnis-Prefix, erlaubte Basename-Globs) — Unterverzeichnisse inklusive.
WHITELIST_DIRS: list[tuple[str, tuple[str, ...]]] = [
    ("extractors/", ("*.py",)),
    ("scripts/", ("*.py", "*.sh")),
    ("evals/", ("*.py", "*.json", "*.md", "*.yaml.test")),
    # Die Browser-Erweiterung: ausdrueckliche Globs statt "*", damit dort nie
    # eine andere Datei mitgeht. evals/test_extension.py prueft diese Dateien
    # und wird mitsynchronisiert — fehlten sie, brach die Public-CI (260925-ovq).
    ("extension/", ("*.json", "*.js", "*.css", "*.md")),
]

# Dateien, die beim Kopieren UMBENANNT werden
RENAMES: dict[str, str] = {
    "README.public.md": "README.md",
}

# ---------------------------------------------------------------------------
# Verbotene Pfade (Sanity-Gate gegen die Whitelist, D-03)
# ---------------------------------------------------------------------------

FORBIDDEN_FILES: set[str] = {
    "CLAUDE.md",
    "AGENTS.md",
    "DONE_CRITERIA.md",
    "README.md",                    # private README; oeffentlich ist README.public.md
    "SETUP_PUBLIC_REPO.md",
    ".github/workflows/sync-public.yml",
    PRIVATE_DENYLIST_REL,
    PRIVATE_SUBSTITUTIONS_REL,
    "family.yaml",                  # exakt — family.yaml.example bleibt erlaubt
    "privacy_secrets.local.yaml",
    "privacy_secrets.vorschlag.yaml",
    "aussteller.json",
    "probe_review.html",
}

FORBIDDEN_PREFIXES: tuple[str, ...] = (
    ".planning/",
    ".claude/",
    ".gsd/",
    "belege/",
    "output/",
    "evals/samples_real",
)

FORBIDDEN_GLOBS: tuple[str, ...] = ("*.numbers",)


def is_forbidden_path(rel: str) -> bool:
    """True, wenn der relative Pfad nie ins Public-Repo darf."""
    if rel in FORBIDDEN_FILES:
        return True
    if rel.startswith(FORBIDDEN_PREFIXES):
        return True
    name = Path(rel).name
    return any(fnmatch(name, g) for g in FORBIDDEN_GLOBS)


# .gitignore fuer das oeffentliche Repo (Regeln der privaten .gitignore plus
# Harness-Ordner; bewusst OHNE Planungsordner-Eintrag).
GITIGNORE_CONTENT = """\
# Belege bleiben strikt lokal — Privacy-Constraint
belege/
output/
aussteller.json
aussteller.json.bak
family.yaml
privacy_secrets.local.yaml
privacy_secrets.local.yaml.bak

# Eval-Fixtures: synthetische PDFs werden generiert, nicht commitet
evals/fixtures/*.pdf

# Anonymisierte Echtbeleg-Samples — lokal, NICHT eingecheckt
evals/samples_real/
evals/samples_real_*/

# Eval-Fonts: lokal abgelegt (OFL-Lizenz-Hygiene), nicht commitet
evals/fonts/*.ttf
evals/fonts/*.otf

# Python
__pycache__/
*.py[cod]
*$py.class
.venv/
venv/
*.egg-info/
build/
dist/
.pytest_cache/
.ruff_cache/
.mypy_cache/

# Editor / OS
.vscode/
.idea/
.DS_Store

# Agent-Harness-Zustand
.claude/
.gsd/

# Steueraufstellung-Beispiele (enthalten teils echte Daten) — nie committen
*.numbers
privacy_secrets.vorschlag.yaml
probe_review.html
"""

# ---------------------------------------------------------------------------
# Implementierung
# ---------------------------------------------------------------------------

BINARY_EXTENSIONS = {
    ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".ico",
    ".woff", ".woff2", ".ttf", ".otf", ".so", ".dylib", ".bin",
}


def is_binary_file(path: Path) -> bool:
    return path.suffix.lower() in BINARY_EXTENSIONS


def tracked_files(source_root: Path) -> list[str]:
    """Alle git-getrackten Pfade (relativ zu source_root).

    Es gibt bewusst KEINEN Working-Tree-Fallback: schlaegt git fehl, bricht
    der Sync ab (Exit 3 in main). Nur so ist strukturell garantiert, dass
    gitignorte Echtdaten neben whitelisted Verzeichnissen nie gestaged werden.
    """
    try:
        r = subprocess.run(
            ["git", "-C", str(source_root), "ls-files", "-z", "--cached"],
            capture_output=True, check=False,
        )
    except FileNotFoundError as e:
        raise RuntimeError("git nicht gefunden — kein Working-Tree-Fallback") from e
    if r.returncode != 0:
        raise RuntimeError(
            f"git ls-files fehlgeschlagen (Exit {r.returncode}): "
            f"{r.stderr.decode('utf-8', 'replace').strip()}"
        )
    return [p for p in r.stdout.decode("utf-8").split("\0") if p]


def _matches_whitelist_dir(rel: str) -> bool:
    name = Path(rel).name
    for prefix, patterns in WHITELIST_DIRS:
        if rel.startswith(prefix) and any(fnmatch(name, pat) for pat in patterns):
            return True
    return False


def select_whitelisted(tracked: list[str]) -> list[str]:
    """Sortierte Auswahl der whitelisted Pfade aus der getrackten Liste.

    RENAMES-Quellen werden separat behandelt (stage_files) und sind hier
    nicht enthalten.
    """
    tracked_set = set(tracked)
    out = {rel for rel in WHITELIST_FILES if rel in tracked_set}
    out.update(rel for rel in tracked if _matches_whitelist_dir(rel))
    return sorted(out)


def load_private_denylist(source_root: Path) -> list[tuple[str, re.Pattern]]:
    """Laedt die private Denylist; fehlt sie (Public-Klon), leere Liste."""
    path = source_root / PRIVATE_DENYLIST_REL
    if not path.exists():
        return []
    patterns: list[tuple[str, re.Pattern]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("re:"):
            patterns.append((line, re.compile(line[3:])))
        else:
            patterns.append((line, re.compile(re.escape(line))))
    return patterns


def load_private_substitutions(source_root: Path) -> list[tuple[re.Pattern, str]]:
    """Laedt private Wort-Substitutionen; fehlt die Datei (Public-Klon), leer.

    Laengere Quellen zuerst, damit ein Mehrwort-Name nicht von einer
    kuerzeren Regel halb ersetzt wird. Ganze Woerter: ``(?<!\\w)…(?!\\w)``.
    """
    path = source_root / PRIVATE_SUBSTITUTIONS_REL
    if not path.exists():
        return []
    pairs: list[tuple[str, str]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=>" not in line:
            continue
        src, dst = (part.strip() for part in line.split("=>", 1))
        if src:
            pairs.append((src, dst))
    pairs.sort(key=lambda sd: len(sd[0]), reverse=True)
    return [(re.compile(rf"(?<!\w){re.escape(src)}(?!\w)"), dst) for src, dst in pairs]


def apply_substitutions(
    text: str,
    private_subs: list[tuple[re.Pattern, str]] | tuple = (),
) -> str:
    result = text
    for src, dst in SUBSTITUTIONS:
        result = result.replace(src, dst)
    for pattern, dst in SUBSTITUTIONS_REGEX:
        result = re.sub(pattern, dst, result)
    for pattern, dst in private_subs:
        result = pattern.sub(dst, result)
    return result


def verify_no_leaks(
    texts: dict[str, str],
    private_patterns: list[tuple[str, re.Pattern]] | tuple = (),
) -> list[tuple[str, int, str]]:
    """Scannt die gestagten Textinhalte nach verbotenen Strings und PII-Mustern.

    Laeuft auf den In-Memory-Inhalten (nach Substitution), damit die Pruefung
    auch im Dry-Run greift. Pro Pattern-Treffer wird erst die Placeholder-
    Funktion, dann ALLOWED_SYNTHETIC befragt. Die private Denylist gilt fuer
    alle Dateien ausser DENYLIST_SKIP_FILES. Leere Liste = sauber.
    """
    leaks: list[tuple[str, int, str]] = []
    for rel, text in sorted(texts.items()):
        skip_private = Path(rel).name in DENYLIST_SKIP_FILES
        for lineno, line in enumerate(text.splitlines(), start=1):
            for forbidden in FORBIDDEN_AFTER_SUBSTITUTION:
                if forbidden in line:
                    leaks.append((rel, lineno, forbidden))
            for label, pattern, is_placeholder in FORBIDDEN_PATTERNS:
                for m in pattern.finditer(line):
                    value = m.group(0)
                    if is_placeholder(value) or value in ALLOWED_SYNTHETIC:
                        continue
                    leaks.append((rel, lineno, f"{label}: {value}"))
            if skip_private:
                continue
            for label, pattern in private_patterns:
                if pattern.search(line):
                    leaks.append((rel, lineno, f"privat: {label}"))
    return leaks


def copy_file(
    src: Path,
    dst: Path,
    rel: str,
    dry_run: bool = False,
    private_subs: list[tuple[re.Pattern, str]] | tuple = (),
) -> tuple[str, str | None]:
    """Datei stagen; gibt (kind, Text oder None bei Binaerdatei) zurueck."""
    if is_binary_file(src):
        if not dry_run:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        return "binary", None
    raw = src.read_text(encoding="utf-8")
    # Dieses Skript wird unveraendert kopiert: die Substitution der eigenen
    # Substitutionstabelle waere Unsinn. Der Verifier prueft es trotzdem —
    # deshalb stehen die verbotenen Strings hier nur als Fragmente.
    text = raw if rel == SELF_REL else apply_substitutions(raw, private_subs)
    if not dry_run:
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(text, encoding="utf-8")
    return "text", text


def clear_stage_dir(stage_dir: Path) -> None:
    if not stage_dir.exists():
        stage_dir.mkdir(parents=True, exist_ok=True)
        return
    for entry in stage_dir.iterdir():
        if entry.name == ".git":
            continue
        if entry.is_dir():
            shutil.rmtree(entry)
        else:
            entry.unlink()


def stage_files(
    source_root: Path,
    stage_dir: Path,
    dry_run: bool = False,
    verbose: bool = True,
) -> dict:
    """Kopiert die whitelisted, git-getrackten Dateien ins Stage.

    Im Dry-Run wird nichts geschrieben und kein Verzeichnis angelegt; die
    substituierten Texte stehen trotzdem in ``texts`` fuer den Verifier.
    """
    copied: list[str] = []
    renamed: list[str] = []
    missing: list[str] = []
    skipped_forbidden: list[str] = []
    texts: dict[str, str] = {}

    tracked = tracked_files(source_root)
    tracked_set = set(tracked)
    private_subs = load_private_substitutions(source_root)

    for rel in select_whitelisted(tracked):
        if is_forbidden_path(rel):
            skipped_forbidden.append(rel)
            if verbose:
                print(f"  FORBIDDEN (uebersprungen): {rel}")
            continue
        src = source_root / rel
        if not src.is_file():
            missing.append(rel)
            if verbose:
                print(f"  MISSING: {rel}")
            continue
        kind, text = copy_file(src, stage_dir / rel, rel, dry_run=dry_run,
                               private_subs=private_subs)
        if text is not None:
            texts[rel] = text
        copied.append(rel)
        if verbose:
            print(f"  copy   ({kind:6}): {rel}")

    for src_name, dst_name in RENAMES.items():
        src = source_root / src_name
        if src_name not in tracked_set or not src.is_file():
            missing.append(src_name)
            if verbose:
                print(f"  MISSING: {src_name}")
            continue
        _, text = copy_file(src, stage_dir / dst_name, src_name, dry_run=dry_run,
                            private_subs=private_subs)
        if text is not None:
            texts[dst_name] = text
        renamed.append(f"{src_name} -> {dst_name}")
        if verbose:
            print(f"  rename: {src_name} -> {dst_name}")

    if not dry_run:
        stage_dir.mkdir(parents=True, exist_ok=True)
        (stage_dir / ".gitignore").write_text(GITIGNORE_CONTENT, encoding="utf-8")
    texts[".gitignore"] = GITIGNORE_CONTENT
    if verbose:
        print("  write : .gitignore (Public-Repo-Version)")

    return {
        "copied": copied,
        "renamed": renamed,
        "missing": missing,
        "texts": texts,
        "skipped_forbidden": skipped_forbidden,
    }


def run_stage_tests(stage_dir: Path, timeout: int = 1800) -> tuple[bool, str]:
    """Faehrt die Testsuite im Stage — dem Baum, der wirklich gepusht wird.

    Warum hier und nicht im Quellbaum: der Sync **veraendert Quelltext**
    (Substitution privater Institutsnamen). Ein Test, der ein Fragment seiner
    eigenen Eingabe prueft, laeuft privat gruen und im Public-Klon rot — genau
    so geschehen am 2026-09-25 (260925-ovq): ``test_umlaute_bleiben`` setzte
    einen echten Institutsnamen ein und pruefte auf einen Namensteil. Dasselbe
    gilt fuer Tests, deren Pruefgegenstand nicht auf der Whitelist steht
    (``evals/test_extension.py`` ohne ``extension/``). Beides faellt nur auf,
    wenn der gestagte Baum selbst laeuft.

    Fail-closed: laesst sich das Gate nicht ausfuehren (kein ``uv``), gilt es
    als nicht bestanden. Ein ungepruefter Push ist kein bestandener Test.

    Setzt einen Git-Index im Stage voraus — ``ensure_stage_git_index``, an der
    Aufrufstelle. Der Index entsteht bewusst nicht hier: diese Funktion wird
    mit gemocktem ``subprocess.run`` geprueft, ein Git-Aufruf an dieser Stelle
    fiele dort in denselben Mock.
    """
    uv = shutil.which("uv")
    if uv is None:
        return False, ("uv nicht gefunden — die Testsuite im Stage konnte nicht "
                       "laufen. Fail-closed: kein Push. Mit --skip-tests bewusst "
                       "uebergehen.")
    try:
        r = subprocess.run(
            [uv, "run", "--directory", str(stage_dir), "--extra", "dev", "pytest", "-q"],
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False, f"Testsuite im Stage nach {timeout}s abgebrochen."
    ausgabe = (r.stdout + r.stderr).strip()
    return r.returncode == 0, ausgabe


def pytest_summenzeile(ausgabe: str) -> str:
    """Die Zeile, die zaehlt — nicht die letzte, die zufaellig kam.

    Hinter pytest steht in der gemeinsamen Ausgabe noch uv-stderr ("Installed
    46 packages"). ``splitlines()[-1]`` meldete deshalb einen Paket-Hinweis als
    Testergebnis. Eine Erfolgsmeldung, die nicht das Ergebnis nennt, ist keine.
    """
    for zeile in reversed(ausgabe.splitlines()):
        if "passed" in zeile or "failed" in zeile or "no tests ran" in zeile:
            return zeile.strip()
    return "gruen (keine Summenzeile gefunden)"


def ensure_stage_git_index(stage_dir: Path) -> str | None:
    """Legt einen Wegwerf-Git-Index im Stage an, bevor das Testgate laeuft.

    ``git_push`` initialisiert die echte Historie erst NACH dem Gate. Ohne
    diesen Vorab-Index waere der Stage zum Testzeitpunkt kein Git-Repository,
    und ``tracked_files`` — von ``test_sync_to_public.py`` selbst aufgerufen,
    mit ``REPO_ROOT`` = Stage — schluege strukturell fehl: jeder Sync-Lauf
    bliebe bei Exit 6 haengen, unabhaengig vom Inhalt. Nur ``init`` + ``add``,
    kein Commit noetig (``git ls-files --cached`` liest den Index). ``git_push``
    entfernt dieses ``.git`` restlos und legt es neu an.

    Gibt bei Erfolg ``None`` zurueck, sonst die Fehlermeldung.
    """
    git = shutil.which("git")
    if git is None:
        return "git nicht gefunden — kein Index im Stage moeglich."
    for git_args in (["init", "-q", "-b", "main"], ["add", "-A"]):
        r = subprocess.run([git, *git_args], cwd=stage_dir, capture_output=True, text=True)
        if r.returncode != 0:
            return f"git {' '.join(git_args)} im Stage fehlgeschlagen: {r.stderr.strip()}"
    return None


DEPLOY_KEY_PATH = Path.home() / ".ssh" / "steuer_extraktor_public_deploy"


def git_push(stage_dir: Path, repo_url: str, commit_msg: str) -> None:
    """Initialisiert frische Git-Historie und force-pusht ins oeffentliche Repo."""
    env = os.environ.copy()
    if DEPLOY_KEY_PATH.exists() and "GIT_SSH_COMMAND" not in env:
        env["GIT_SSH_COMMAND"] = (
            f"ssh -i {DEPLOY_KEY_PATH} -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"
        )

    def run(args: list[str]) -> None:
        r = subprocess.run(args, cwd=stage_dir, capture_output=True, text=True, env=env)
        if r.returncode != 0:
            raise RuntimeError(
                f"git {' '.join(args[1:])} fehlgeschlagen:\n"
                f"stdout: {r.stdout}\nstderr: {r.stderr}"
            )

    git_dir = stage_dir / ".git"
    if git_dir.exists():
        shutil.rmtree(git_dir)

    run(["git", "init", "-b", "main"])
    run(["git", "add", "-A"])
    run(["git", "-c", "user.email=steuer-extraktor-sync@example.com",
         "-c", "user.name=steuer-extraktor sync",
         "commit", "-m", commit_msg])
    run(["git", "remote", "add", "origin", repo_url])
    run(["git", "push", "--force", "origin", "main"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--target-dir", type=Path, default=None)
    parser.add_argument("--repo-url", type=str, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--commit-msg", type=str,
                        default="chore: sync public showcase snapshot")
    parser.add_argument("--skip-tests", action="store_true",
                        help="Testsuite im Stage nicht fahren (bewusster Notausgang)")
    args = parser.parse_args(argv)

    source_root: Path = args.source_root.resolve()
    repo_url = args.repo_url or os.environ.get("PUBLIC_REPO_URL")

    if args.target_dir:
        stage_dir = args.target_dir.resolve()
    elif args.dry_run:
        stage_dir = Path("(dry-run: kein Stage-Verzeichnis)")
    else:
        stage_dir = Path(tempfile.mkdtemp(prefix=f"{PUBLIC_REPO_NAME}-")).resolve()

    print(f"source: {source_root}")
    print(f"stage : {stage_dir}")
    print(f"modus : {'DRY-RUN' if args.dry_run else 'SCHREIBEN'}")
    print()

    # Getrackte Dateien — ohne git kein Sync (Exit 3).
    try:
        tracked = tracked_files(source_root)
    except RuntimeError as e:
        print(f"FATAL: {e}", file=sys.stderr)
        return 3

    # Sanity-Gate: kein verbotener Pfad darf whitelisted sein (Exit 2).
    konflikte = [rel for rel in select_whitelisted(tracked) if is_forbidden_path(rel)]
    konflikte += [src for src in RENAMES if is_forbidden_path(src)]
    if konflikte:
        for rel in konflikte:
            print(
                f"FATAL: verbotener Pfad '{rel}' ist whitelisted. "
                f"Bug in sync_to_public.py — bitte beheben.",
                file=sys.stderr,
            )
        return 2

    private_patterns = load_private_denylist(source_root)
    if not private_patterns:
        print(f"Hinweis: keine private Denylist ({PRIVATE_DENYLIST_REL}) — ok im Public-Klon.")

    if not args.dry_run:
        clear_stage_dir(stage_dir)

    print("Dateien werden gestaged:")
    summary = stage_files(source_root, stage_dir, dry_run=args.dry_run)
    if summary["missing"]:
        print(
            f"\nWARNUNG: {len(summary['missing'])} Dateien fehlen in der Quelle "
            f"(nicht getrackt oder nicht vorhanden):",
            file=sys.stderr,
        )
        for m in summary["missing"]:
            print(f"  - {m}", file=sys.stderr)

    # Leak-Verifikation laeuft auf den In-Memory-Inhalten — auch im Dry-Run.
    print("\nLeak-Verifikation:")
    leaks = verify_no_leaks(summary["texts"], private_patterns)
    if leaks:
        print(
            f"FATAL: {len(leaks)} persoenliche Datenleck(s) gefunden:",
            file=sys.stderr,
        )
        for path, lineno, match in leaks[:50]:
            print(f"  {path}:{lineno} — '{match}'", file=sys.stderr)
        if len(leaks) > 50:
            print(f"  ... und {len(leaks) - 50} weitere", file=sys.stderr)
        return 4
    print(
        f"  OK — keine Lecks gefunden (Strings + PII-Muster"
        f"{' + private Denylist' if private_patterns else ''} geprueft)."
    )

    # Testsuite im Stage — der Baum, der gepusht wird, muss selbst laufen.
    # Erst nach der Leak-Verifikation: ein Leck bricht ab, bevor irgendetwas
    # Laufzeit bekommt.
    if repo_url and not args.dry_run:
        if args.skip_tests:
            print("\nTestsuite im Stage: uebersprungen (--skip-tests).", file=sys.stderr)
        else:
            fehler = ensure_stage_git_index(stage_dir)
            if fehler:
                print(f"FATAL: {fehler} Fail-closed: kein Push.", file=sys.stderr)
                return 6
            print("\nTestsuite im Stage:")
            ok, ausgabe = run_stage_tests(stage_dir)
            if not ok:
                print("FATAL: Testsuite im Stage ist rot — es wird nicht gepusht.",
                      file=sys.stderr)
                for zeile in ausgabe.splitlines()[-40:]:
                    print(f"  {zeile}", file=sys.stderr)
                print(f"\nStage bleibt liegen: {stage_dir}", file=sys.stderr)
                return 6
            print(f"  OK — {pytest_summenzeile(ausgabe)}")

    if repo_url:
        if args.dry_run:
            print(f"\n(dry-run: wuerde nach {repo_url} pushen)")
        else:
            print(f"\nPushe nach {repo_url}:")
            try:
                git_push(stage_dir, repo_url, args.commit_msg)
                print("  OK — frischer Commit nach main gepusht.")
            except RuntimeError as e:
                print(f"FATAL: git push fehlgeschlagen:\n{e}", file=sys.stderr)
                return 5
    else:
        print(
            f"\nKein --repo-url / PUBLIC_REPO_URL angegeben. "
            f"Stage-Verzeichnis: {stage_dir}"
        )

    print(
        f"\nFertig. {len(summary['copied'])} Dateien kopiert, "
        f"{len(summary['renamed'])} umbenannt."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
