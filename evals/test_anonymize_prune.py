"""Tests für den Stale-Output-Cleanup im Anonymizer (R3, 260612-m8t).

Hintergrund: Bei einem Re-Anonymisierungs-Lauf können im Output-Ordner
JSON/TXT-Paare von früheren Läufen liegen bleiben, die nicht mehr zum
aktuellen Input gehören. Folgeskripte (process_samples_full, grade_run)
zählen sie stumm mit und verfälschen die Lauf-Bilanz.

`prune_stale_outputs` macht stale Paare sichtbar:
- Default (prune=False): WARNUNG, Datei bleibt.
- prune=True: stale *.json/*.txt werden gelöscht.
- Spezialdateien (_results*, anon_truth_mapping, field_exceptions,
  *.truth.json, *.md, *.xlsx, *.csv) werden NIE gelöscht.

Privacy: die WARNUNG nennt nur Dateinamen, keinen Inhalt. KEINE echten
Namen — synthetische Stems.
"""
from pathlib import Path

import pytest

from scripts.anonymize_belege import prune_stale_outputs


def _touch(path: Path, content: str = "{}") -> None:
    path.write_text(content, encoding="utf-8")


@pytest.fixture
def output_dir(tmp_path: Path) -> Path:
    d = tmp_path / "samples"
    d.mkdir()
    return d


def test_default_warnt_loescht_nicht(output_dir, capsys):
    """Default: stale Paar bleibt erhalten, WARNUNG wird ausgegeben."""
    _touch(output_dir / "frisch.json")
    _touch(output_dir / "frisch.txt", "text")
    _touch(output_dir / "alt.json")
    _touch(output_dir / "alt.txt", "text")

    deleted = prune_stale_outputs(output_dir, {"frisch"}, prune=False)

    assert deleted == []
    # Stale Datei bleibt erhalten.
    assert (output_dir / "alt.json").exists()
    assert (output_dir / "alt.txt").exists()
    out = capsys.readouterr()
    combined = out.out + out.err
    assert "alt" in combined
    assert "--prune" in combined


def test_prune_loescht_stale(output_dir):
    """Mit prune=True werden stale *.json/*.txt gelöscht."""
    _touch(output_dir / "frisch.json")
    _touch(output_dir / "alt.json")
    _touch(output_dir / "alt.txt", "text")

    deleted = prune_stale_outputs(output_dir, {"frisch"}, prune=True)

    assert not (output_dir / "alt.json").exists()
    assert not (output_dir / "alt.txt").exists()
    assert (output_dir / "frisch.json").exists()
    # Rückgabe enthält die gelöschten Dateinamen.
    names = {Path(p).name for p in deleted}
    assert "alt.json" in names
    assert "alt.txt" in names


def test_spezialdateien_nie_geloescht(output_dir):
    """Spezialdateien bleiben auch mit prune=True erhalten."""
    spezial = [
        "_results.csv",
        "_results_full.json",
        "anon_truth_mapping.json",
        "field_exceptions.json",
        "beleg.truth.json",
        "steuer_uebertragung.md",
        "steueraufstellung-2023.xlsx",
        "daten.csv",
    ]
    for name in spezial:
        _touch(output_dir / name)

    # Lauf-Set ist leer → alles wäre "stale", aber Spezialdateien sind geschützt.
    deleted = prune_stale_outputs(output_dir, set(), prune=True)

    for name in spezial:
        assert (output_dir / name).exists(), f"{name} wurde fälschlich gelöscht"
    assert deleted == []


def test_frische_paare_unangetastet(output_dir):
    """Im Lauf erzeugte Stems bleiben immer erhalten."""
    _touch(output_dir / "a.json")
    _touch(output_dir / "a.txt", "t")
    _touch(output_dir / "b.json")

    deleted = prune_stale_outputs(output_dir, {"a", "b"}, prune=True)

    assert deleted == []
    assert (output_dir / "a.json").exists()
    assert (output_dir / "b.json").exists()
