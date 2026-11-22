"""pytest-Konfiguration für Eval-Suite.

Stellt session-scope `model_check` bereit, der den SHA-Digest des aktiven
Ollama-Modells gegen `evals/baseline_model.json` prüft (D-B4 Drift-Protection).

Verhalten:
- Ollama nicht erreichbar / Modell nicht gepullt → `pytest.skip` (CI ohne Ollama
  blockiert nicht den ganzen Lauf).
- Erreichbar, aber Digest-Mismatch → `pytest.fail` (HARTER Fail, zwingt zu
  manueller Re-Baseline-Review).
- Übereinstimmung → liefert {model, digest} an Tests, die die Fixture nutzen.

Da der Baseline-Digest in der Kurzform (12 Hex-Zeichen aus `ollama list`)
gepinnt ist (`digest_kind: "short"`), wird der Vergleich als Substring-Match
durchgeführt: der Baseline-Wert muss im aktuellen Digest enthalten sein.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

from extractors import datenpfade

BASELINE_PATH = Path(__file__).parent / "baseline_model.json"

# Phase 5 (Datenschutz): Die Testsitzung ist von echten Daten getrennt.
# Mehr dazu in extractors/datenpfade.py. Die Pfade der Probe sind erfunden und
# nicht vorhanden; der Unterschied der Fehlerart (PermissionError statt
# FileNotFoundError) zeigt die Sperre, ohne dass je eine echte Datei geöffnet
# würde, falls sie fehlt.
_PROBE = datenpfade.ROOT / "belege-2099" / "probe.pdf"
_hook_registriert = False


def _erfundene_sync_listen(ordner: Path) -> None:
    """Zwei erfundene private Sync-Listen im heutigen Format.

    Die Einträge entstehen zur Laufzeit aus Fragmenten: Diese Datei steht auf
    der Whitelist des öffentlichen Spiegels, und ein wörtlicher Eintrag stünde
    sonst in einem Text, den der Verifier gegen genau diese Liste prüft.
    """
    marke = "Zzqx" + "-Erfunden-"
    (ordner / "sync_forbidden.private.txt").write_text(
        "# erfundene Testliste (Phase 5)\n"
        f"{marke}Kuerzel-90417\n"
        f"re:{marke}Muster-\\d{{6}}\n",
        encoding="utf-8")
    (ordner / "sync_substitutions.private.txt").write_text(
        "# erfundene Testliste (Phase 5)\n"
        f"{marke}Institut AG => INST-TEST\n",
        encoding="utf-8")


def _erfundene_familie(ordner: Path) -> Path:
    """Eine erfundene Familie im Format der echten (Rollen wie dort).

    Die Rollen ``mann``, ``frau``, ``kind1``, ``kind2`` ergeben die Personen
    ``elternteil_1``, ``elternteil_2``, ``kind_1``, ``kind_2``. Alle Namen sind
    erfunden und stehen in keinem Beleg.
    """
    datei = ordner / "familie-erfunden.yaml"
    datei.write_text(
        "members:\n"
        "  - {role: mann, first_name: Testo, last_name: Erfunden}\n"
        "  - {role: frau, first_name: Testa, last_name: Erfunden}\n"
        "  - {role: kind1, first_name: Testi, last_name: Erfunden}\n"
        "  - {role: kind2, first_name: Testu, last_name: Erfunden}\n",
        encoding="utf-8")
    return datei


def pytest_configure(config):
    """Trennt die Sitzung von echten Daten (Phase 5, DS-09).

    Setzt ``STEUER_TESTLAUF``, ``SYNC_PRIVATE_DIR`` (erfundene Listen) und
    ``STEUER_DATEN_WURZEL`` (nicht vorhanden), legt ``sitecustomize.py`` für
    Python-Kindprozesse auf ``PYTHONPATH`` und registriert den Audit-Hook.
    """
    global _hook_registriert
    os.environ["STEUER_TESTLAUF"] = "1"
    ordner = Path(tempfile.mkdtemp(prefix="testlauf-"))
    config.add_cleanup(lambda: shutil.rmtree(ordner, ignore_errors=True))

    _erfundene_sync_listen(ordner)
    os.environ["SYNC_PRIVATE_DIR"] = str(ordner)
    os.environ[datenpfade.ENV] = str(ordner / "kein-volume")
    os.environ[datenpfade.ENV_FAMILIE] = str(_erfundene_familie(ordner))

    site = ordner / "site"
    datenpfade.schreibe_testlauf_site(site)
    bisher = os.environ.get("PYTHONPATH")
    os.environ["PYTHONPATH"] = str(site) + (os.pathsep + bisher if bisher else "")
    os.environ["STEUER_TESTLAUF_SITE"] = str(site)

    if not _hook_registriert:
        sys.addaudithook(datenpfade.testlauf_audit)
        _hook_registriert = True


def pytest_sessionstart(session):
    """Bricht vor dem ersten Test ab, wenn die Trennung fehlt (Exit 3)."""
    fehlt: list[str] = []
    if os.environ.get("STEUER_TESTLAUF") != "1":
        fehlt.append("STEUER_TESTLAUF")
    sync = os.environ.get("SYNC_PRIVATE_DIR")
    if not sync or not (Path(sync) / "sync_forbidden.private.txt").is_file():
        fehlt.append("SYNC_PRIVATE_DIR")
    wurzel = os.environ.get(datenpfade.ENV)
    if not wurzel or Path(wurzel).exists():
        fehlt.append(datenpfade.ENV)
    familie = os.environ.get(datenpfade.ENV_FAMILIE)
    if not familie or not Path(familie).is_file() or datenpfade.testlauf_gesperrt(familie):
        fehlt.append(datenpfade.ENV_FAMILIE)
    site = os.environ.get("STEUER_TESTLAUF_SITE")
    if (not site or not (Path(site) / "sitecustomize.py").is_file()
            or site not in os.environ.get("PYTHONPATH", "").split(os.pathsep)):
        fehlt.append("STEUER_TESTLAUF_SITE")
    try:
        open(_PROBE, "rb").close()
        fehlt.append("Audit-Hook (Probe erfolgreich)")
    except PermissionError:
        pass
    except OSError:
        fehlt.append("Audit-Hook (Probe nicht abgewiesen)")
    if fehlt:
        pytest.exit("Testlauf nicht von echten Daten getrennt", returncode=3)


@pytest.fixture(scope="session")
def model_check() -> dict[str, str]:
    """Failed laut bei Modell-Drift, sonst gibt {model, digest} zurück."""
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    try:
        from extractors.llm_extract import get_model_digest

        actual = get_model_digest(baseline["model"])
    except Exception as exc:
        pytest.skip(f"Ollama nicht erreichbar — model_check übersprungen ({exc!r})")

    baseline_digest = str(baseline["digest"])
    actual_digest = str(actual["digest"])

    # Bei Kurzform-Baseline (digest_kind == "short") prüfen wir per Substring,
    # weil `ollama show` ggf. die Vollform zurückgibt und `ollama list` die Kurzform.
    if baseline.get("digest_kind") == "short":
        ok = baseline_digest in actual_digest or actual_digest in baseline_digest
    else:
        ok = actual_digest == baseline_digest

    if not ok:
        pytest.fail(
            f"Modell-Drift: baseline={baseline_digest[:12]}, "
            f"actual={actual_digest[:12]}. Re-Baseline nach manueller Review der Eval-Outputs."
        )
    return actual
