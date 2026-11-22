"""Leistenvergleich: vorher/nachher ohne Beträge (261025). Werte erfunden."""
from __future__ import annotations

import sys
from pathlib import Path


def test_schnappschuss_meldet_geaenderten_wert_ohne_betrag(tmp_path, monkeypatch, capsys):
    from scripts import leistenvergleich as lv

    stand = [["Versicherungsprämien", "a.pdf", "", "Prämie Grundversicherung KVG", "1'000.00", "kind_1"]]
    monkeypatch.setattr(lv, "_bauen", lambda code, samples, ziel: [list(z) for z in stand])
    monkeypatch.setattr("scripts.pflichtfelder_check._kuerzel", lambda s: {"a.pdf": "D01 x · y"})
    datei = tmp_path / "stand.json"
    monkeypatch.setattr(sys, "argv", ["x", "--samples", str(tmp_path), "--merken", str(datei)])
    lv.main()
    stand[0][4] = "1'100.00"
    monkeypatch.setattr(sys, "argv", ["x", "--samples", str(tmp_path), "--gegen", str(datei)])
    lv.main()
    aus = capsys.readouterr().out
    assert "anders 1" in aus and "D01" in aus
    assert "1'000" not in aus and "1'100" not in aus
