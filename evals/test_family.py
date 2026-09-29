"""Unit-Tests für ``extractors.family`` — Plan 02-02 Wave 1 Task 1.

Behavior-Tests für ``load_family`` und das pydantic-validierte ``Family``-Schema.
Deckt D-A1..A6 aus ``02-CONTEXT.md`` ab:

* graceful None bei fehlender Datei (D-A2)
* Pydantic-Validation: 4 Roles, max 4 Members, AHV-Format (D-A3, D-A4, D-A5)
* Privacy-Warnung wenn AHV gesetzt ist (D-A4)
"""
from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from extractors.family import Family, FamilyMember, load_family


FIXTURE = Path(__file__).parent / "family.yaml.test"


def test_load_family_none_path_returns_none_when_no_file(tmp_path, monkeypatch):
    """``load_family(None)`` benutzt cwd / ``family.yaml`` und liefert None bei fehlender Datei."""
    monkeypatch.chdir(tmp_path)
    assert load_family(None) is None


def test_load_family_nonexistent_path_returns_none():
    """Expliziter Pfad auf nicht-existente Datei → None (graceful)."""
    assert load_family(Path("/nonexistent/family.yaml")) is None


def test_load_family_test_fixture_loads_successfully():
    """Test-Fixture mit 4 Members lädt erfolgreich; alle 4 Roles distinct."""
    fam = load_family(FIXTURE)
    assert fam is not None
    assert len(fam.members) == 4
    roles = {m.role for m in fam.members}
    assert roles == {"mann", "frau", "kind1", "kind2"}
    assert fam.warnings == []  # Test-Fixture ohne AHV → keine Warnungen


def test_load_family_invalid_yaml_raises_value_error(tmp_path):
    """Broken YAML → ValueError mit klarer Meldung."""
    bad = tmp_path / "broken.yaml"
    bad.write_text("members:\n  - role: mann\n    first_name: [unclosed\n")
    with pytest.raises(ValueError):
        load_family(bad)


def test_load_family_duplicate_role_raises(tmp_path):
    """Doppelte Role → ValidationError."""
    dup = tmp_path / "dup.yaml"
    dup.write_text(
        "members:\n"
        "  - role: mann\n    first_name: A\n    last_name: X\n"
        "  - role: mann\n    first_name: B\n    last_name: Y\n"
    )
    with pytest.raises((ValidationError, ValueError)):
        load_family(dup)


def test_load_family_too_many_members_raises(tmp_path):
    """5 Members → ValidationError (D-A5: max 4)."""
    too_many = tmp_path / "too_many.yaml"
    # 5 Members mit unterschiedlichen Roles geht gar nicht (closed enum), aber max-4-Check
    # greift schon bei 5 Einträgen — also auch wenn manche Roles wiederholt wären.
    too_many.write_text(
        "members:\n"
        "  - role: mann\n    first_name: A\n    last_name: X\n"
        "  - role: frau\n    first_name: B\n    last_name: X\n"
        "  - role: kind1\n    first_name: C\n    last_name: X\n"
        "  - role: kind2\n    first_name: D\n    last_name: X\n"
        "  - role: mann\n    first_name: E\n    last_name: X\n"
    )
    with pytest.raises((ValidationError, ValueError)):
        load_family(too_many)


def test_load_family_invalid_ahv_format_raises(tmp_path):
    """Ungültiges AHV-Format → ValidationError."""
    bad_ahv = tmp_path / "bad_ahv.yaml"
    bad_ahv.write_text(
        "members:\n"
        "  - role: mann\n    first_name: A\n    last_name: X\n    ahv: '123-invalid'\n"
    )
    with pytest.raises((ValidationError, ValueError)):
        load_family(bad_ahv)


def test_load_family_valid_ahv_triggers_warning(tmp_path):
    """AHV gesetzt → Family.warnings enthält Privacy-Eintrag (D-A4)."""
    with_ahv = tmp_path / "with_ahv.yaml"
    with_ahv.write_text(
        "members:\n"
        "  - role: mann\n    first_name: A\n    last_name: X\n    ahv: '756.1234.5678.97'\n"
    )
    fam = load_family(with_ahv)
    assert fam is not None
    assert len(fam.warnings) == 1
    assert "AHV" in fam.warnings[0]
    assert "mann" in fam.warnings[0]


def test_load_family_invalid_birth_date_raises(tmp_path):
    """Ungültiges birth_date → ValidationError."""
    bad_date = tmp_path / "bad_date.yaml"
    bad_date.write_text(
        "members:\n"
        "  - role: mann\n    first_name: A\n    last_name: X\n    birth_date: '15.05.1980'\n"
    )
    with pytest.raises((ValidationError, ValueError)):
        load_family(bad_date)


def test_family_member_minimum_fields():
    """FamilyMember akzeptiert minimale Pflichtfelder ohne birth_date/ahv."""
    m = FamilyMember(role="mann", first_name="Hans", last_name="Muster")
    assert m.birth_date is None
    assert m.ahv is None


def test_family_no_ahv_no_warning():
    """Family ohne AHV-Feld → warnings leer."""
    fam = Family(
        members=[
            FamilyMember(role="mann", first_name="Hans", last_name="Muster"),
            FamilyMember(role="frau", first_name="Maria", last_name="Muster"),
        ]
    )
    assert fam.warnings == []
