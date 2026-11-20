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

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from scripts.sync_to_public import (
    FORBIDDEN_AFTER_SUBSTITUTION,
    WHITELIST_DIRS,
    FORBIDDEN_FILES,
    FORBIDDEN_PREFIXES,
    PRIVATE_DENYLIST_REL,
    PRIVATE_SUBSTITUTIONS_REL,
    SUBSTITUTIONS,
    apply_substitutions,
    apply_substitutions_to_file,
    is_forbidden_path,
    load_private_denylist,
    ensure_stage_git_index,
    load_private_substitutions,
    pytest_summenzeile,
    run_stage_tests,
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


# --- Test 3b: LICENSE-Copyright-Zeile ist die einzige Ausnahme --------------
# Entscheid des Inhabers 2026-11-20: Der Public-Klon nennt in der LICENSE den echten
# Lizenzinhaber. Der Name wird aus SUBSTITUTIONS abgeleitet (siehe Kopf).

def test_license_copyright_zeile_ist_einzige_ausnahme():
    vollname = SUBSTITUTIONS[1][0]
    zeile = f"Copyright (c) 2026 {vollname}"
    lizenz = f"MIT License\n\n{zeile}\n\nPermission: {vollname}\n"

    out = apply_substitutions_to_file("LICENSE", lizenz)
    assert f"{zeile}\n" in out
    assert out.count(vollname) == 1  # andere LICENSE-Zeilen bleiben anonymisiert
    assert verify_no_leaks({"LICENSE": f"{zeile}\n"}) == []

    # Gegenfaelle: andere Dateien, verschachtelte LICENSE, Zusatztext
    for rel in ("README.md", "docs/LICENSE"):
        assert vollname not in apply_substitutions_to_file(rel, lizenz), rel
        assert verify_no_leaks({rel: zeile}), rel
    assert verify_no_leaks({"LICENSE": f"{zeile}, Zuerich"})
    assert verify_no_leaks({"LICENSE": f"Kontakt: {vollname}"})


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


# --- Test 5: private Substitutionen — ganze Woerter, nie synchronisiert ------

def test_private_substitutionen_ganze_woerter(tmp_path):
    """Die private Tabelle ersetzt nur ganze Woerter und die laengste Quelle zuerst.

    Fehlt die Datei (Public-Klon), passiert nichts — der Klon muss ohne sie
    laufen. Die Datei selbst darf nie auf der Whitelist landen: sie ist die
    Zuordnung Marker -> Institut und wuerde den Fingerabdruck verraten.
    """
    assert PRIVATE_SUBSTITUTIONS_REL in FORBIDDEN_FILES
    assert load_private_substitutions(tmp_path) == []

    (tmp_path / "scripts").mkdir()
    (tmp_path / PRIVATE_SUBSTITUTIONS_REL).write_text(
        "# Kommentar\nAlpha => X-1\nAlpha Beta AG => X-2\n", encoding="utf-8"
    )
    subs = load_private_substitutions(tmp_path)
    text = apply_substitutions("Alpha, Alpha Beta AG, Alphabet, alpha_x", subs)
    assert text == "X-1, X-2, Alphabet, alpha_x"


def test_private_substitutionen_lassen_keinen_quellnamen_stehen():
    """Was die Tabelle ersetzt, darf im Stage nirgends mehr auftauchen."""
    subs = load_private_substitutions(REPO_ROOT)
    if not subs:
        pytest.skip("keine private Substitutionstabelle (Public-Klon)")
    summary = stage_files(REPO_ROOT, tmp_stage := REPO_ROOT / ".pytest_cache" / "sync-stage",
                          dry_run=True, verbose=False)
    assert not tmp_stage.exists()
    reste = [
        (rel, pattern.pattern)
        for rel, text in summary["texts"].items()
        if rel != "scripts/sync_to_public.py"
        for pattern, _ in subs
        if pattern.search(text)
    ]
    assert reste == []


# --- Test 9: Wer geprueft wird, muss mitkommen -------------------------------
#
# Am 2026-09-25 (260925-ovq) lag ``evals/test_extension.py`` im Public-Klon,
# ``extension/`` nicht: sechs Tests suchten Dateien, die es dort nie gab. Ein
# synchronisierter Test, der ein Wurzelverzeichnis anfasst, braucht dieses
# Verzeichnis auch auf der Whitelist — sonst prueft er Luft.

def test_gepruefte_verzeichnisse_kommen_mit():
    auswahl = set(select_whitelisted(tracked_files(REPO_ROOT)))
    prefixe = {prefix.rstrip("/") for prefix, _ in WHITELIST_DIRS}
    # Wurzelverzeichnisse, die es im privaten Repo gibt und die ein Test
    # ueber ``parent.parent / "<name>"`` erreicht.
    muster = re.compile(r'parent\.parent\s*/\s*"([^"/]+)"')
    fehlend: list[tuple[str, str]] = []
    for rel in sorted(auswahl):
        if not rel.startswith("evals/") or not Path(rel).name.startswith("test_"):
            continue
        quelle = (REPO_ROOT / rel).read_text(encoding="utf-8")
        for name in muster.findall(quelle):
            ziel = REPO_ROOT / name
            if not ziel.is_dir() or name in prefixe:
                continue
            if is_forbidden_path(f"{name}/"):
                continue
            fehlend.append((rel, name))
    assert fehlend == [], (
        "Diese Tests werden synchronisiert, ihr Pruefgegenstand nicht: "
        f"{fehlend}"
    )


def test_erweiterung_ist_vollstaendig_whitelisted():
    """Die Dateien der Erweiterung — keine mehr, keine weniger. Seit dem
    Seitenteil (261028) dazu panel.html, seite.js und hintergrund.js."""
    auswahl = set(select_whitelisted(tracked_files(REPO_ROOT)))
    dabei = {rel for rel in auswahl if rel.startswith("extension/")}
    assert dabei == {
        "extension/manifest.json",
        "extension/panel.html",
        "extension/panel.js",
        "extension/panel.css",
        "extension/seite.js",
        "extension/hintergrund.js",
        "extension/README.md",
    }, dabei


# --- Test 10: Das Gate vor dem Push ist fail-closed --------------------------

def test_stage_tests_sind_fail_closed(monkeypatch, tmp_path):
    """Kein ``uv`` heisst nicht bestanden — ein ungepruefter Push ist keiner."""
    monkeypatch.setattr("scripts.sync_to_public.shutil.which", lambda _: None)
    ok, grund = run_stage_tests(tmp_path)
    assert ok is False
    assert "uv" in grund


def test_stage_tests_melden_rote_suite(monkeypatch, tmp_path):
    """Die Suite faellt um — dann ist der Push tabu."""
    class Fertig:
        returncode = 1
        stdout = "1 failed, 2 passed"
        stderr = ""

    monkeypatch.setattr("scripts.sync_to_public.shutil.which", lambda _: "/usr/bin/uv")
    monkeypatch.setattr("scripts.sync_to_public.subprocess.run",
                        lambda *a, **k: Fertig())
    ok, ausgabe = run_stage_tests(tmp_path)
    assert ok is False
    assert "1 failed" in ausgabe


def test_stage_index_ohne_git_kein_push(monkeypatch, tmp_path):
    """Ohne Index laeuft ``tracked_files`` im Stage nicht — also kein Push."""
    monkeypatch.setattr("scripts.sync_to_public.shutil.which", lambda _: None)
    assert "git" in (ensure_stage_git_index(tmp_path) or "")


def test_stage_index_macht_den_stage_lesbar(tmp_path):
    """Echter Lauf: nach dem Vorab-Index sieht ``tracked_files`` den Stage."""
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    assert ensure_stage_git_index(tmp_path) is None
    assert tracked_files(tmp_path) == ["a.py"]


def test_summenzeile_nennt_das_ergebnis():
    """Hinter pytest steht noch uv-stderr — gemeldet wird trotzdem pytest."""
    ausgabe = ("1300 passed, 5 skipped, 55 deselected in 32.79s\n"
               "Using CPython 3.12.13\n"
               "Installed 46 packages in 28ms")
    assert pytest_summenzeile(ausgabe) == "1300 passed, 5 skipped, 55 deselected in 32.79s"
    assert "keine Summenzeile" in pytest_summenzeile("nur Rauschen")

# Regression: ein weiterer Sync muss die bewusste README-Grafik erhalten.
def _hero_fixture(tmp_path):
    from scripts import sync_to_public as sync
    root = tmp_path / "quelle"
    (root / "docs").mkdir(parents=True)
    readme = root / "README.public.md"
    readme.write_text("![Demo](" + sync.HERO_IMAGE + ")\n[Herkunft](" + sync.HERO_PROVENANCE + ")\n")
    # Kleine synthetische PNG-Datei; kein echtes Dokument.
    import base64
    (root / sync.HERO_IMAGE).write_bytes(base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jGZkAAAAASUVORK5CYII="))
    (root / sync.HERO_PROVENANCE).write_text("Synthetische Demo; keine Echtdaten.\n")
    return sync, root, readme


def test_hero_guard_accepts_complete_selected_assets(tmp_path):
    sync, root, _ = _hero_fixture(tmp_path)
    assert sync.verify_public_hero(root, [sync.HERO_IMAGE, sync.HERO_PROVENANCE]) == []


def test_hero_guard_rejects_commented_image(tmp_path):
    sync, root, readme = _hero_fixture(tmp_path)
    readme.write_text("<!-- " + readme.read_text() + " -->")
    assert sync.verify_public_hero(root)


def test_hero_guard_rejects_missing_image(tmp_path):
    sync, root, _ = _hero_fixture(tmp_path)
    (root / sync.HERO_IMAGE).unlink()
    assert sync.verify_public_hero(root)


def test_hero_guard_rejects_unselected_image(tmp_path):
    sync, root, _ = _hero_fixture(tmp_path)
    assert sync.verify_public_hero(root, [sync.HERO_PROVENANCE])


def test_hero_guard_rejects_non_png(tmp_path):
    sync, root, _ = _hero_fixture(tmp_path)
    (root / sync.HERO_IMAGE).write_bytes(b"kein Bild")
    assert sync.verify_public_hero(root)


def test_hero_guard_rejects_missing_provenance(tmp_path):
    sync, root, _ = _hero_fixture(tmp_path)
    (root / sync.HERO_PROVENANCE).unlink()
    assert sync.verify_public_hero(root)


def test_hero_guard_stops_before_clearing_or_pushing(tmp_path, monkeypatch):
    sync, root, _ = _hero_fixture(tmp_path)
    (root / sync.HERO_IMAGE).unlink()
    if hasattr(sync, "tracked_files"):
        monkeypatch.setattr(sync, "tracked_files", lambda _: [sync.HERO_IMAGE, sync.HERO_PROVENANCE, "README.public.md"])
    if hasattr(sync, "verify_classification"):
        monkeypatch.setattr(sync, "verify_classification", lambda _: [])
    def forbidden(*args, **kwargs):
        raise AssertionError("Bei fehlendem Hero darf kein Ziel geleert oder gepusht werden")
    monkeypatch.setattr(sync, "clear_stage_dir", forbidden)
    monkeypatch.setattr(sync, "git_push", forbidden)
    assert sync.main(["--source-root", str(root), "--target-dir", str(tmp_path / "ziel")]) == 9
