"""Tests der Schutzarchitektur von scripts/sync_to_public.py.

Diese Datei steht selbst auf der Whitelist und wird ins Public-Repo
synchronisiert. Sie darf deshalb KEINEN verbotenen String, keinen Echtwert
und keinen Pfad des Planungsordners wortwoertlich enthalten — sonst wuerde
die Substitution die oeffentliche Kopie veraendern und der Test dort brechen.
Alle Beispiele werden aus den Modul-Konstanten (FORBIDDEN_PREFIXES,
FORBIDDEN_FILES, PRIVATE_DENYLIST_REL, SUBSTITUTIONS,
FORBIDDEN_AFTER_SUBSTITUTION) bzw. aus ``load_private_denylist`` abgeleitet.

Deterministisch: kein Netz, kein Push, kein Aufruf von ``git_push``.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from scripts.sync_to_public import (
    FORBIDDEN_AFTER_SUBSTITUTION,
    FORBIDDEN_FILES,
    FORBIDDEN_PREFIXES,
    PRIVATE_DENYLIST_REL,
    SUBSTITUTIONS,
    apply_substitutions,
    is_forbidden_path,
    load_private_denylist,
    select_whitelisted,
    stage_files,
    tracked_files,
    verify_no_leaks,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


# --- Test 1: Whitelist und Forbidden-Gate widersprechen sich nie -------------

def test_whitelist_und_forbidden_sind_disjunkt():
    auswahl = select_whitelisted(tracked_files(REPO_ROOT))
    assert auswahl, "Whitelist-Auswahl darf nicht leer sein"
    verboten = [rel for rel in auswahl if is_forbidden_path(rel)]
    assert verboten == []

    for prefix in FORBIDDEN_PREFIXES:
        assert is_forbidden_path(prefix + "x.md"), prefix
    for rel in FORBIDDEN_FILES:
        assert is_forbidden_path(rel), rel
    assert is_forbidden_path("foo.numbers")

    # Die private Denylist ist verboten UND von keinem Whitelist-Glob erfasst.
    assert is_forbidden_path(PRIVATE_DENYLIST_REL)
    assert PRIVATE_DENYLIST_REL not in auswahl

    erlaubt = [
        "family.yaml.example",
        "evals/family.yaml.test",
        "privacy_secrets.local.example.yaml",
        "scripts/sync_to_public.py",
        "LICENSE",
    ]
    for rel in erlaubt:
        assert not is_forbidden_path(rel), rel
        assert rel in auswahl, rel


# --- Test 2: Dry-Run meldet 0 Lecks und schreibt nichts ----------------------

def test_dry_run_ohne_lecks_und_ohne_schreiben(tmp_path):
    stage = tmp_path / "stage"
    summary = stage_files(REPO_ROOT, stage, dry_run=True, verbose=False)
    assert summary["copied"], "es muss etwas gestaged werden"
    assert verify_no_leaks(summary["texts"], load_private_denylist(REPO_ROOT)) == []
    assert not stage.exists(), "Dry-Run darf kein Stage-Verzeichnis anlegen"

    # Ohne Denylist-Datei (Public-Klon) laeuft der Verifier ohne sie weiter.
    assert load_private_denylist(tmp_path) == []


# --- Test 3: Substitution ist vollstaendig, Verifier ist scharf --------------

def test_substitution_vollstaendig_und_verifier_scharf():
    for src, _dst in SUBSTITUTIONS:
        text = apply_substitutions(f"vorher {src} nachher")
        assert verify_no_leaks({"x.py": text}) == [], src

    vollname = SUBSTITUTIONS[1][0]
    assert vollname not in apply_substitutions(f"Copyright (c) 2026 {vollname}")

    # Ohne Substitution meldet der Verifier jeden verbotenen String.
    for forbidden in FORBIDDEN_AFTER_SUBSTITUTION:
        leaks = verify_no_leaks({"x.py": f"pfad /Users/{forbidden}/x"})
        assert leaks and leaks[0][2] == forbidden, forbidden

    # Private Denylist: scharf in Quelldateien, ausgenommen im Lockfile.
    # Im Public-Klon fehlt die Datei — dann entfaellt nur dieser Teil.
    muster = load_private_denylist(REPO_ROOT)
    if muster:
        label = next(lbl for lbl, _ in muster if not lbl.startswith("re:"))
        text = f"Beispiel {label} im Text"
        treffer = verify_no_leaks({"x.py": text}, muster)
        assert treffer and treffer[0][2] == f"privat: {label}"
        assert verify_no_leaks({"uv.lock": text}, muster) == []


# --- Test 4: gitignorte und untracked Dateien werden nie gestaged -----------

@pytest.fixture
def tmp_repo(tmp_path):
    if shutil.which("git") is None:
        pytest.skip("git nicht verfuegbar")
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args: str) -> None:
        subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
            cwd=repo, check=True, capture_output=True,
        )

    git("init", "-q", "-b", "main")
    (repo / ".gitignore").write_text("output/\nevals/samples_real_*/\n", encoding="utf-8")
    getrackt = ["steuer_extraktor.py", "extractors/x.py", "evals/test_x.py"]
    for rel in getrackt:
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"# {rel}\n", encoding="utf-8")
    git("add", "--", *getrackt)

    # Erst NACH dem add: gitignorte und untracked Dateien im Working-Tree.
    nicht_getrackt = ["output/x.md", "evals/samples_real_2022/x.json", "evals/untracked.py"]
    for rel in nicht_getrackt:
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("geheim\n", encoding="utf-8")
    return repo, getrackt, nicht_getrackt


def test_nur_getrackte_dateien_landen_im_stage(tmp_repo, tmp_path):
    repo, getrackt, nicht_getrackt = tmp_repo
    stage = tmp_path / "stage"
    summary = stage_files(repo, stage, dry_run=False, verbose=False)

    assert sorted(summary["copied"]) == sorted(getrackt)
    for rel in getrackt:
        assert (stage / rel).is_file(), rel
    for rel in nicht_getrackt:
        assert rel not in summary["copied"], rel
        assert not (stage / rel).exists(), rel

    # Ohne git-Index gibt es keinen Working-Tree-Fallback, sondern einen Fehler.
    with pytest.raises(RuntimeError):
        tracked_files(tmp_path / "kein_repo")
