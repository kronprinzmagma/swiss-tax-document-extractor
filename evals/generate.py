"""Generator für synthetische Schweizer Lohnausweis-Fixtures (Phase 01, Wave 2).

Erzeugt drei Fixture-Familien, die die Eval-Suite in Wave 4 parametrisiert
konsumiert:

* **F1 — `lohnausweis_standard`** (Stufe L1):
  Reportlab-Default-Font (Helvetica), CH-Apostroph U+0027, kein Quellensteuer-
  Block. Profil ACME AG / 8000 Zürich. Siehe ``D-C1`` in 01-CONTEXT.md.
* **F2 — `lohnausweis_quellensteuer`** (Stufe L1):
  Helvetica, CH-Apostroph **U+2019** (typografische Variante — testet
  Pitfall 2 aus 01-RESEARCH.md), zusätzliche Pos 12 mit Quellensteuer-Abzug.
  Profil BORDER GmbH / 4051 Basel. Siehe ``D-C2``.
* **F3 — `lohnausweis_custom_font`** (Stufe L2):
  Inhalt = F1, aber mit eingebettetem Custom-Font (Inter-Regular.ttf
  unter ``evals/fonts/``), um den Glyph-Mapping-Pitfall (Pitfall 6) zu
  testen. Fehlt die Schriftdatei, fällt der Generator auf Helvetica zurück
  und markiert das im ``_meta.level``-Feld der expected.json.

Layout-Konventionen (siehe 01-RESEARCH.md §reportlab-Lohnausweis-Fixture):

* ``canvas.Canvas`` + ``drawString`` / ``drawRightString`` (NICHT Tables —
  Pitfall 3, Wort-Reihenfolge instabil).
* Header-Marker ``Lohnausweis`` und ``Form 11`` für den Klassifizierer.
* Beträge als **ein einziger** ``drawString``-Call (kein interner Space) —
  pdfplumber muss sie als ein Wort wiederfinden, sonst zerbricht der
  Anker-Match.
* Reproduzierbare Metadaten (fester ``Producer`` + ``CreationDate``), damit
  ``python -m evals.generate`` byte-identische PDFs über Re-Runs liefert.

Doc-Strings auf Schweizer Hochdeutsch, Code/API in Englisch.
Reines I/O-Modul — keine Cloud-Imports (PRV-01).
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

# ---------------------------------------------------------------------------
# Pfad-Konstanten und Determinismus-Anker
# ---------------------------------------------------------------------------

THIS_DIR = Path(__file__).resolve().parent
FIXTURES_DIR = THIS_DIR / "fixtures"
EXPECTED_DIR = THIS_DIR / "expected"
FONTS_DIR = THIS_DIR / "fonts"
INTER_FONT_PATH = FONTS_DIR / "Inter-Regular.ttf"

PRODUCER_TAG = "steuer-extraktor evals/generate.py"
# Reportlab's ``invariant=1``-Flag pinnt CreationDate/ModDate auf
# ``D:20000101000000`` und ID-Bytes auf einen festen Wert — damit liefern
# zwei Generator-Runs byte-identische PDFs (Snapshot-Drift-Erkennung
# Wave 3+). Siehe reportlab docs §canvas.Canvas(invariant=1).

# Apostroph-Konstanten — explizit, damit der F2-Pitfall-2-Test grep-bar bleibt.
APO = "'"  # U+0027, F1+F3
APO_TYPO = "’"  # U+2019, F2 (right single quotation mark)


# ---------------------------------------------------------------------------
# Layout-Hilfsstrukturen
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Position:
    """Eine Position auf dem Lohnausweis: Label links, Betrag rechtsbündig.

    ``amount`` ist bereits mit dem korrekten Apostroph-Zeichen formatiert
    (entweder :data:`APO` oder :data:`APO_TYPO`) — der Generator macht
    keine Apostroph-Konvertierung mehr.
    """

    label: str
    amount: str


def _draw_lohnausweis(
    c: canvas.Canvas,
    *,
    font_name: str,
    employer: str,
    period_von: str,
    period_bis: str,
    positions: list[Position],
) -> None:
    """Zeichnet das Standard-Lohnausweis-Layout auf ``c``.

    Layout-Aufbau (auf A4-Hochformat):

    1. Header-Block mit ``Lohnausweis`` + ``Form 11 — Eidg. Steuerverwaltung``.
    2. Arbeitgeber- und Periode-Block (Label links, Wert rechts daneben).
    3. Tabellarischer Positionsblock — Label per ``drawString`` linksbündig,
       Betrag per ``drawRightString`` rechtsbündig auf derselben Y-Linie.
    4. Footer mit Hinweis "Synthetische Test-Fixture".
    """

    width, height = A4
    left_x = 60
    right_x = width - 60  # Beträge rechtsbündig auf diesem X.

    # --- Kopfzeile -------------------------------------------------------
    c.setFont(font_name, 14)
    c.drawString(left_x, height - 60, "Lohnausweis")
    c.setFont(font_name, 9)
    c.drawString(left_x, height - 75, "Form 11 — Eidg. Steuerverwaltung")

    # --- Arbeitgeber + Periode ------------------------------------------
    c.setFont(font_name, 10)
    y = height - 110
    c.drawString(left_x, y, "Arbeitgeber:")
    c.drawString(left_x + 120, y, employer)
    y -= 22
    c.drawString(left_x, y, "Periode von:")
    c.drawString(left_x + 120, y, period_von)
    y -= 22
    c.drawString(left_x, y, "Periode bis:")
    c.drawString(left_x + 120, y, period_bis)

    # --- Positionsblock --------------------------------------------------
    y -= 36
    c.setFont(font_name, 10)
    for pos in positions:
        c.drawString(left_x, y, pos.label)
        c.drawRightString(right_x, y, pos.amount)
        y -= 22  # Gross genug, dass pdfplumber die Zeilen sauber gruppiert.

    # --- Footer ----------------------------------------------------------
    c.setFont(font_name, 8)
    c.drawString(
        left_x,
        40,
        "Synthetische Test-Fixture — kein produktiver Lohnausweis.",
    )


def _new_canvas(out_path: Path) -> canvas.Canvas:
    """Erzeugt einen Canvas mit deterministischen Metadaten.

    Nutzt reportlabs ``invariant=1``-Flag (pinnt CreationDate, ModDate und
    File-ID auf konstante Werte) und setzt zusätzlich ``Producer`` und
    ``Creator`` — damit zwei Generator-Runs byte-identische PDFs liefern.
    """
    c = canvas.Canvas(str(out_path), pagesize=A4, invariant=1)
    c.setProducer(PRODUCER_TAG)
    c.setCreator(PRODUCER_TAG)
    return c


# ---------------------------------------------------------------------------
# expected.json-Validierung (lockere Variante, siehe SUMMARY-Deviation)
# ---------------------------------------------------------------------------


def _validate_expected(
    expected: dict[str, Any],
    fixture: str,
    belegtyp: str = "lohnausweis",
) -> None:
    """Belegtyp-aware Schema-Gate vor dem Schreiben der expected.json.

    Die expected.json trägt **nur** die Extraktionswerte (``value`` +
    ``anchor_present``), nicht die volle :class:`TaggedField`-Hülle inkl.
    ``tag_refs``. Ein Roundtrip durch ``LohnausweisRaw.model_validate``
    würde an den fehlenden ``tag_refs`` scheitern.

    Phase 2 (Plan 02-03): die Pflichtfeld-Liste wird via
    :data:`extractors.schema.MANDATORY_FIELDS_FOR_BELEGTYP[belegtyp]` aus
    der Single-Source-of-Truth gezogen — statt einer hardcodierten
    Lohnausweis-Liste. Default ``belegtyp="lohnausweis"`` hält Phase-1-
    Generatoren rückwärtskompatibel.

    Strukturkorrektheit (``value: str``, ``anchor_present: bool``) wird
    weiterhin pro Feld geprüft.
    """
    # Lazy-Import: vermeidet zyklische Top-Level-Imports zwischen evals/ und
    # extractors/, falls extractors später Eval-Helper konsumiert.
    from extractors.schema import MANDATORY_FIELDS_FOR_BELEGTYP

    if belegtyp not in MANDATORY_FIELDS_FOR_BELEGTYP:
        raise ValueError(
            f"[{fixture}] unbekannter Belegtyp {belegtyp!r}; "
            f"erlaubt: {sorted(MANDATORY_FIELDS_FOR_BELEGTYP)}"
        )
    required_min = set(MANDATORY_FIELDS_FOR_BELEGTYP[belegtyp])
    missing = required_min - set(expected.keys())
    if missing:
        raise ValueError(
            f"[{fixture}] expected.json (belegtyp={belegtyp}) fehlt "
            f"Pflichtfelder: {sorted(missing)}"
        )
    for key, val in expected.items():
        if key.startswith("_") or val is None:
            continue
        if not isinstance(val, dict):
            raise TypeError(f"[{fixture}] Feld {key!r} ist kein Dict: {val!r}")
        if not isinstance(val.get("value"), str):
            raise TypeError(f"[{fixture}] Feld {key!r}.value ist kein str")
        if not isinstance(val.get("anchor_present"), bool):
            raise TypeError(f"[{fixture}] Feld {key!r}.anchor_present ist kein bool")


def _write_expected(
    expected: dict[str, Any],
    out_path: Path,
    fixture: str,
    belegtyp: str = "lohnausweis",
) -> None:
    """Schreibt expected.json mit ``ensure_ascii=False`` (literale Umlaute + U+2019)."""
    _validate_expected(expected, fixture, belegtyp=belegtyp)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(expected, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# F1 — lohnausweis_standard
# ---------------------------------------------------------------------------


def generate_f1_standard(
    out_pdf: Path,
    out_expected: Path,
    *,
    font: str = "Helvetica",
) -> dict[str, Any]:
    """Erzeugt F1 (Standard-Lohnausweis, ASCII-Apostroph U+0027).

    Beträge gemäss ``D-C1`` (01-CONTEXT.md) und der ``<fixture_specs>``-
    Sektion in 01-03-eval-fundament-PLAN.md:

    * Pos 1 Lohn = ``90'000.00``
    * Pos 2.1 Gehaltsnebenleistungen = ``2'400.00``
    * Pos 3 Unregelmässige Leistungen = ``3'000.00``
    * Pos 8 Bruttolohn total = ``95'400.00``
    * Pos 9 AHV/IV/EO/ALV/NBUV = ``5'056.20``
    * Pos 10.1 Berufliche Vorsorge (BVG) = ``10'843.80``
    * Pos 11 Nettolohn = ``79'500.00``

    ``font`` wird vom F3-Generator überschrieben (Inter, falls vorhanden);
    F1 selbst nutzt immer Helvetica.
    """
    employer = "ACME AG, 8000 Zürich"
    period_von = "01.01.2024"
    period_bis = "31.12.2024"

    pos1 = f"90{APO}000.00"
    pos21 = f"2{APO}400.00"
    pos3 = f"3{APO}000.00"
    bruttolohn = f"95{APO}400.00"
    ahv = f"5{APO}056.20"
    bvg = f"10{APO}843.80"
    netto = f"79{APO}500.00"

    positions = [
        Position("1. Lohn", pos1),
        Position("2.1 Gehaltsnebenleistungen", pos21),
        Position("3. Unregelmässige Leistungen", pos3),
        Position("8. Bruttolohn total", bruttolohn),
        Position("9. AHV/IV/EO/ALV/NBUV-Abzug", ahv),
        Position("10.1 Berufliche Vorsorge (BVG)", bvg),
        Position("11. Nettolohn", netto),
    ]

    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    c = _new_canvas(out_pdf)
    _draw_lohnausweis(
        c,
        font_name=font,
        employer=employer,
        period_von=period_von,
        period_bis=period_bis,
        positions=positions,
    )
    c.showPage()
    c.save()

    expected: dict[str, Any] = {
        "arbeitgeber": {"value": employer, "anchor_present": True},
        "periode_von": {"value": period_von, "anchor_present": True},
        "periode_bis": {"value": period_bis, "anchor_present": True},
        "bruttolohn_pos8": {"value": bruttolohn, "anchor_present": True},
        "ahv_alv_nbu_abzug_pos9": {"value": ahv, "anchor_present": True},
        "bvg_abzug_pos10a": {"value": bvg, "anchor_present": True},
        "nettolohn_pos11": {"value": netto, "anchor_present": True},
        "quellensteuer_pos12": None,
    }
    _write_expected(expected, out_expected, fixture="F1-standard", belegtyp="lohnausweis")
    return expected


# ---------------------------------------------------------------------------
# F2 — lohnausweis_quellensteuer
# ---------------------------------------------------------------------------


def generate_f2_quellensteuer(out_pdf: Path, out_expected: Path) -> dict[str, Any]:
    """Erzeugt F2 (Quellensteuer-Variante, typografisches Apostroph U+2019).

    Alle Beträge nutzen explizit :data:`APO_TYPO` (``’`` / U+2019), um den
    Apostroph-Pitfall (RESEARCH §Pitfall 2) im Anker-Resolver zu testen.
    Profil und Werte gemäss 01-CONTEXT.md ``D-C2`` und PLAN.md §<fixture_specs>:

    * Bruttolohn Pos 8 = ``115’200.00``
    * AHV/IV/EO/ALV/NBUV Pos 9 = ``6’105.60``
    * BVG Pos 10.1 = ``12’960.00``
    * Nettolohn Pos 11 = ``96’134.40`` (= 115'200 − 6'105.60 − 12'960)
    * Quellensteuer Pos 12 = ``16’134.40`` — **kritisch**: NICHT
      ``115’200.00`` (das war der MUST_FIX-Wert im Plan-Review).
    """
    employer = "BORDER GmbH, 4051 Basel"
    period_von = "01.01.2024"
    period_bis = "31.12.2024"

    pos1 = f"108{APO_TYPO}000.00"
    pos21 = f"2{APO_TYPO}100.00"
    pos3 = f"5{APO_TYPO}100.00"
    bruttolohn = f"115{APO_TYPO}200.00"
    ahv = f"6{APO_TYPO}105.60"
    bvg = f"12{APO_TYPO}960.00"
    netto = f"96{APO_TYPO}134.40"
    quellensteuer = f"16{APO_TYPO}134.40"

    positions = [
        Position("1. Lohn", pos1),
        Position("2.1 Gehaltsnebenleistungen", pos21),
        Position("3. Unregelmässige Leistungen", pos3),
        Position("8. Bruttolohn total", bruttolohn),
        Position("9. AHV/IV/EO/ALV/NBUV-Abzug", ahv),
        Position("10.1 Berufliche Vorsorge (BVG)", bvg),
        Position("11. Nettolohn", netto),
        Position("12. Quellensteuer-Abzug", quellensteuer),
    ]

    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    c = _new_canvas(out_pdf)
    _draw_lohnausweis(
        c,
        font_name="Helvetica",
        employer=employer,
        period_von=period_von,
        period_bis=period_bis,
        positions=positions,
    )
    c.showPage()
    c.save()

    expected: dict[str, Any] = {
        "arbeitgeber": {"value": employer, "anchor_present": True},
        "periode_von": {"value": period_von, "anchor_present": True},
        "periode_bis": {"value": period_bis, "anchor_present": True},
        "bruttolohn_pos8": {"value": bruttolohn, "anchor_present": True},
        "ahv_alv_nbu_abzug_pos9": {"value": ahv, "anchor_present": True},
        "bvg_abzug_pos10a": {"value": bvg, "anchor_present": True},
        "nettolohn_pos11": {"value": netto, "anchor_present": True},
        "quellensteuer_pos12": {"value": quellensteuer, "anchor_present": True},
    }
    _write_expected(
        expected, out_expected, fixture="F2-quellensteuer", belegtyp="lohnausweis"
    )
    return expected


# ---------------------------------------------------------------------------
# F3 — lohnausweis_custom_font
# ---------------------------------------------------------------------------


def _try_register_inter() -> tuple[str, str]:
    """Versucht Inter-Regular.ttf bei reportlab zu registrieren.

    Returns:
        Tuple ``(font_name, level)``:

        * ``("Inter", "L2-custom-font")`` — Schriftdatei vorhanden und
          erfolgreich registriert.
        * ``("Helvetica", "L1-fallback-no-inter")`` — Schriftdatei fehlt
          oder Registrierung schlug fehl. Der F3-Generator fällt damit
          inhaltlich auf F1-Niveau zurück, was im ``_meta``-Block der
          expected.json explizit dokumentiert wird.
    """
    if INTER_FONT_PATH.exists():
        try:
            pdfmetrics.registerFont(TTFont("Inter", str(INTER_FONT_PATH)))
            return "Inter", "L2-custom-font"
        except Exception as exc:  # pragma: no cover — defensiver Pfad
            print(
                f"[generate] WARN: Inter konnte nicht registriert werden ({exc!r}); "
                f"fallback auf Helvetica.",
                file=sys.stderr,
            )
    return "Helvetica", "L1-fallback-no-inter"


def generate_f3_custom_font(out_pdf: Path, out_expected: Path) -> dict[str, Any]:
    """Erzeugt F3 (Inhalt = F1, aber mit eingebettetem Custom-Font).

    Ruft :func:`generate_f1_standard` mit dem registrierten Font auf und
    ergänzt das resultierende expected-Dict um einen ``_meta``-Block:

    .. code-block:: python

        {"_meta": {"font": "Inter", "level": "L2-custom-font"}}

    Der ``_meta``-Block wird von der Eval-Suite (Wave 4) ignoriert — er
    dient nur Debugging und der Erkennung der tatsächlichen Eval-Stufe.
    """
    font_used, font_level = _try_register_inter()

    # Generate F1-Inhalt mit dem (ggf. registrierten) Font.
    expected = generate_f1_standard(out_pdf, out_expected, font=font_used)

    # _meta-Block ergänzen und expected.json mit dem zusätzlichen Feld neu schreiben.
    expected = dict(expected)
    expected["_meta"] = {"font": font_used, "level": font_level}
    _write_expected(
        expected, out_expected, fixture="F3-custom-font", belegtyp="lohnausweis"
    )
    return expected


# ===========================================================================
# Phase 2 — Bank-Zinsausweis-Generators (Plan 02-03)
# ===========================================================================
#
# Layout-Konventionen identisch zu Phase 1 (drawString + drawRightString,
# Beträge als ein einziger drawString-Call — Pitfall 3 aus 01-RESEARCH.md).
# Familien-Namen aus evals/family.yaml.test (Hans/Maria/Lina/Tim Muster) für
# Konsistenz mit der Person-Inferenz-Eval (Plan 02-05).


def _draw_bank_zinsausweis(
    c: canvas.Canvas,
    *,
    font_name: str,
    header_title: str,
    institut: str,
    kontoinhaber_name: str,
    jahr: str,
    bruttoertrag: str,
    verrechnungssteuer: str,
    vermoegensstand_3112: str,
    konto_nr_redacted: str | None = None,
) -> None:
    """Zeichnet ein Bank-Zinsausweis-Layout auf ``c``.

    Header-Marker (``Zinsausweis`` / ``Steuerausweis`` / ``Konto-auszug``)
    in den ersten Wörtern für den Klassifikator (Plan 02-04). Beträge
    rechtsbündig, Labels linksbündig — analog zum Lohnausweis-Layout.
    """
    width, height = A4
    left_x = 60
    right_x = width - 60

    # --- Kopfzeile -------------------------------------------------------
    c.setFont(font_name, 14)
    c.drawString(left_x, height - 60, header_title)
    c.setFont(font_name, 9)
    c.drawString(left_x, height - 75, "Form 340 — Eidg. Steuerverwaltung")

    # --- Institut + Kontoinhaber ----------------------------------------
    c.setFont(font_name, 10)
    y = height - 110
    c.drawString(left_x, y, "Institut:")
    c.drawString(left_x + 140, y, institut)
    y -= 22
    c.drawString(left_x, y, "Kontoinhaber:")
    # Voll-Name als ein einzelner drawString-Call — Person-Inferenz und
    # Anker-Resolver konsumieren ihn als zusammenhängenden Span.
    c.drawString(left_x + 140, y, kontoinhaber_name)
    y -= 22
    c.drawString(left_x, y, "Steuerjahr:")
    c.drawString(left_x + 140, y, jahr)
    if konto_nr_redacted is not None:
        y -= 22
        c.drawString(left_x, y, "Konto-Nr.:")
        c.drawString(left_x + 140, y, konto_nr_redacted)

    # --- Beträge ---------------------------------------------------------
    y -= 36
    c.setFont(font_name, 10)
    rows = [
        ("Bruttoertrag (Zinsen)", bruttoertrag),
        ("Verrechnungssteuer (35%)", verrechnungssteuer),
        ("Vermögensstand per 31.12.", vermoegensstand_3112),
    ]
    for label, amount in rows:
        c.drawString(left_x, y, label)
        c.drawRightString(right_x, y, amount)
        y -= 22

    # --- Footer ----------------------------------------------------------
    c.setFont(font_name, 8)
    c.drawString(
        left_x,
        40,
        "Synthetische Test-Fixture — kein produktiver Bank-Zinsausweis.",
    )


def generate_bank_standard(
    out_pdf: Path,
    out_expected: Path,
    *,
    font: str = "Helvetica",
) -> dict[str, Any]:
    """Erzeugt ``bank_zinsausweis_standard`` (BANK-Z-ähnlich, Hans Muster).

    Werte (Plan 02-03 §Behavior + family.yaml.test-Konsistenz):

    * Header ``Zinsausweis``
    * Institut ``BANK-Z``
    * Kontoinhaber ``Hans Muster`` → Person-Inferenz-Target ``mann``
    * Bruttoertrag ``1'250.00``
    * Verrechnungssteuer ``437.50`` (= 35% × 1250 — Plausibility-Anker)
    * Vermögensstand 31.12. ``85'420.00``
    * Steuerjahr ``2024``
    * Konto-Nr. ``XXXX-1234`` (last-4-Pattern, Optional)
    """
    institut = "BANK-Z"
    kontoinhaber = "Hans Muster"
    jahr = "2024"
    bruttoertrag = f"1{APO}250.00"
    verrechnungssteuer = "437.50"
    vermoegensstand = f"85{APO}420.00"
    konto_nr = "XXXX-1234"

    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    c = _new_canvas(out_pdf)
    _draw_bank_zinsausweis(
        c,
        font_name=font,
        header_title="Zinsausweis",
        institut=institut,
        kontoinhaber_name=kontoinhaber,
        jahr=jahr,
        bruttoertrag=bruttoertrag,
        verrechnungssteuer=verrechnungssteuer,
        vermoegensstand_3112=vermoegensstand,
        konto_nr_redacted=konto_nr,
    )
    c.showPage()
    c.save()

    expected: dict[str, Any] = {
        "institut": {"value": institut, "anchor_present": True},
        "kontoinhaber_name": {"value": kontoinhaber, "anchor_present": True},
        "bruttoertrag": {"value": bruttoertrag, "anchor_present": True},
        "vermoegensstand_3112": {"value": vermoegensstand, "anchor_present": True},
        "verrechnungssteuer": {"value": verrechnungssteuer, "anchor_present": True},
        "jahr": {"value": jahr, "anchor_present": True},
        "konto_nr_redacted": {"value": konto_nr, "anchor_present": True},
        "_meta": {
            "belegtyp": "bank_zinsausweis",
            "level": "L1-clean",
            "font": font,
            "expected_person": "mann",
        },
    }
    _write_expected(
        expected, out_expected, fixture="bank_standard", belegtyp="bank_zinsausweis"
    )
    return expected


def generate_bank_zkb(out_pdf: Path, out_expected: Path) -> dict[str, Any]:
    """Erzeugt ``bank_zinsausweis_zkb`` (Joint-Account, Hans + Maria Muster).

    Testet:

    * Alternativer Header ``Steuerausweis`` (Klassifikator-Robustheit, D-D1).
    * Joint-Account-Pattern ``Hans und Maria Muster`` → Person-Inferenz
      matcht beide Eltern → Rolle ``gemeinsam`` (D-B2 Schritt 5).
    * VRS = 35% × Bruttoertrag (112.00 = 0.35 × 320.00 — Plausibility ok).
    """
    institut = "BANK-Z"
    kontoinhaber = "Hans und Maria Muster"
    jahr = "2024"
    bruttoertrag = "320.00"
    verrechnungssteuer = "112.00"
    vermoegensstand = f"215{APO}600.00"

    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    c = _new_canvas(out_pdf)
    _draw_bank_zinsausweis(
        c,
        font_name="Helvetica",
        header_title="Steuerausweis",
        institut=institut,
        kontoinhaber_name=kontoinhaber,
        jahr=jahr,
        bruttoertrag=bruttoertrag,
        verrechnungssteuer=verrechnungssteuer,
        vermoegensstand_3112=vermoegensstand,
    )
    c.showPage()
    c.save()

    expected: dict[str, Any] = {
        "institut": {"value": institut, "anchor_present": True},
        "kontoinhaber_name": {"value": kontoinhaber, "anchor_present": True},
        "bruttoertrag": {"value": bruttoertrag, "anchor_present": True},
        "vermoegensstand_3112": {"value": vermoegensstand, "anchor_present": True},
        "verrechnungssteuer": {"value": verrechnungssteuer, "anchor_present": True},
        "jahr": {"value": jahr, "anchor_present": True},
        "_meta": {
            "belegtyp": "bank_zinsausweis",
            "level": "L1-clean",
            "font": "Helvetica",
            "expected_person": "gemeinsam",
        },
    }
    _write_expected(
        expected, out_expected, fixture="bank_zkb", belegtyp="bank_zinsausweis"
    )
    return expected


def generate_bank_custom_font(out_pdf: Path, out_expected: Path) -> dict[str, Any]:
    """Erzeugt ``bank_zinsausweis_custom_font`` — Inhalt = standard, Font = Inter.

    Custom-Font-L2 für Phase 2 (analog F3 für Lohnausweis): testet den
    Glyph-Mapping-Pitfall (Pitfall 6 in 01-RESEARCH.md). Fällt auf
    Helvetica zurück, falls Inter-Regular.ttf fehlt — Level wird im
    ``_meta``-Block dokumentiert.
    """
    font_used, font_level = _try_register_inter()
    expected = generate_bank_standard(out_pdf, out_expected, font=font_used)
    expected = dict(expected)
    expected["_meta"] = {
        "belegtyp": "bank_zinsausweis",
        "level": font_level,
        "font": font_used,
        "expected_person": "mann",
    }
    _write_expected(
        expected,
        out_expected,
        fixture="bank_custom_font",
        belegtyp="bank_zinsausweis",
    )
    return expected


# ===========================================================================
# Phase 2 — Krankenkassen-Prämienbescheinigung-Generators
# ===========================================================================


def _draw_kk_praemienbescheinigung(
    c: canvas.Canvas,
    *,
    font_name: str,
    header_title: str,
    kasse: str,
    versicherte_person_name: str,
    jahr: str,
    praemie_kvg_total: str,
    praemie_vvg_total: str | None = None,
    mitversicherte_kinder: list[str] | None = None,
) -> None:
    """Zeichnet ein KK-Prämienbescheinigung-Layout auf ``c``.

    Header (``Prämienbescheinigung`` / ``Krankenkasse``) in den ersten
    Wörtern für den Klassifikator (D-D1).
    """
    width, height = A4
    left_x = 60
    right_x = width - 60

    c.setFont(font_name, 14)
    c.drawString(left_x, height - 60, header_title)
    c.setFont(font_name, 9)
    c.drawString(left_x, height - 75, "Bescheinigung Ziff. 16 — Krankenkassenprämien")

    c.setFont(font_name, 10)
    y = height - 110
    c.drawString(left_x, y, "Krankenkasse:")
    c.drawString(left_x + 160, y, kasse)
    y -= 22
    c.drawString(left_x, y, "Versicherte Person:")
    c.drawString(left_x + 160, y, versicherte_person_name)
    y -= 22
    c.drawString(left_x, y, "Steuerjahr:")
    c.drawString(left_x + 160, y, jahr)

    if mitversicherte_kinder:
        for idx, kind in enumerate(mitversicherte_kinder, start=1):
            y -= 22
            c.drawString(left_x, y, f"Mitversichertes Kind {idx}:")
            c.drawString(left_x + 160, y, kind)

    y -= 36
    c.setFont(font_name, 10)
    c.drawString(left_x, y, "Prämie KVG (Grundversicherung) Total")
    c.drawRightString(right_x, y, praemie_kvg_total)
    if praemie_vvg_total is not None:
        y -= 22
        c.drawString(left_x, y, "Prämie VVG (Zusatzversicherung) Total")
        c.drawRightString(right_x, y, praemie_vvg_total)

    c.setFont(font_name, 8)
    c.drawString(
        left_x,
        40,
        "Synthetische Test-Fixture — keine produktive KK-Bescheinigung.",
    )


def generate_kk_standard(out_pdf: Path, out_expected: Path) -> dict[str, Any]:
    """Erzeugt ``kk_praemienbescheinigung_standard`` (KK-H, Maria Muster).

    Werte:

    * Header ``Prämienbescheinigung``
    * Kasse ``KK-H``
    * Versicherte Person ``Maria Muster`` → Person-Inferenz ``frau``
    * Prämie KVG Total ``4'200.00`` (≥1000 — Plausibility-OK)
    * Steuerjahr ``2024``
    """
    kasse = "KK-H"
    person = "Maria Muster"
    jahr = "2024"
    kvg = f"4{APO}200.00"

    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    c = _new_canvas(out_pdf)
    _draw_kk_praemienbescheinigung(
        c,
        font_name="Helvetica",
        header_title="Prämienbescheinigung",
        kasse=kasse,
        versicherte_person_name=person,
        jahr=jahr,
        praemie_kvg_total=kvg,
    )
    c.showPage()
    c.save()

    expected: dict[str, Any] = {
        "kasse": {"value": kasse, "anchor_present": True},
        "versicherte_person_name": {"value": person, "anchor_present": True},
        "jahr": {"value": jahr, "anchor_present": True},
        "praemie_kvg_total": {"value": kvg, "anchor_present": True},
        "_meta": {
            "belegtyp": "kk_praemienbescheinigung",
            "level": "L1-clean",
            "font": "Helvetica",
            "expected_person": "frau",
        },
    }
    _write_expected(
        expected,
        out_expected,
        fixture="kk_standard",
        belegtyp="kk_praemienbescheinigung",
    )
    return expected


def generate_kk_helsana(out_pdf: Path, out_expected: Path) -> dict[str, Any]:
    """Erzeugt ``kk_praemienbescheinigung_helsana`` (Familien-Variante: Lina + Tim).

    Werte:

    * Header ``Bescheinigung über die Bezahlung der Krankenkassenprämien``
      (Lang-Variante — Klassifikator-Robustheit, D-D1).
    * Kasse ``KK-H``
    * Versicherte Person ``Lina Muster`` → Person-Inferenz ``kind1``
    * Mitversichertes Kind ``Tim Muster`` (kind2 — wird in Phase 3
      Mehrfach-Person-Mapping konsumiert; in v1 nur als sichtbarer Name).
    * KVG ``1'080.00``, VVG ``240.00`` (Optional gefüllt)
    * Steuerjahr ``2024``
    """
    kasse = "KK-H"
    person = "Lina Muster"
    jahr = "2024"
    kvg = f"1{APO}080.00"
    vvg = "240.00"
    kinder = ["Tim Muster"]

    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    c = _new_canvas(out_pdf)
    _draw_kk_praemienbescheinigung(
        c,
        font_name="Helvetica",
        header_title="Bescheinigung über die Bezahlung der Krankenkassenprämien",
        kasse=kasse,
        versicherte_person_name=person,
        jahr=jahr,
        praemie_kvg_total=kvg,
        praemie_vvg_total=vvg,
        mitversicherte_kinder=kinder,
    )
    c.showPage()
    c.save()

    expected: dict[str, Any] = {
        "kasse": {"value": kasse, "anchor_present": True},
        "versicherte_person_name": {"value": person, "anchor_present": True},
        "jahr": {"value": jahr, "anchor_present": True},
        "praemie_kvg_total": {"value": kvg, "anchor_present": True},
        "praemie_vvg_total": {"value": vvg, "anchor_present": True},
        "_meta": {
            "belegtyp": "kk_praemienbescheinigung",
            "level": "L1-clean",
            "font": "Helvetica",
            "expected_person": "kind1",
        },
    }
    _write_expected(
        expected,
        out_expected,
        fixture="kk_helsana",
        belegtyp="kk_praemienbescheinigung",
    )
    return expected


# ===========================================================================
# Phase 2 — Säule-3a-Generators
# ===========================================================================


def _draw_saeule_3a(
    c: canvas.Canvas,
    *,
    font_name: str,
    header_title: str,
    stiftung: str,
    kontoinhaber_name: str,
    jahr: str,
    einzahlung_betrag: str,
    valutadatum: str | None = None,
    kontonummer_redacted: str | None = None,
) -> None:
    """Zeichnet ein Säule-3a-Bescheinigung-Layout auf ``c``.

    Header (``Säule 3a`` / ``3a-Konto`` / ``gebundene Vorsorge``) in den
    ersten Wörtern für den Klassifikator (D-D1).
    """
    width, height = A4
    left_x = 60
    right_x = width - 60

    c.setFont(font_name, 14)
    c.drawString(left_x, height - 60, header_title)
    c.setFont(font_name, 9)
    c.drawString(left_x, height - 75, "Bescheinigung Ziff. 14 — Säule 3a (gebundene Vorsorge)")

    c.setFont(font_name, 10)
    y = height - 110
    c.drawString(left_x, y, "Stiftung:")
    c.drawString(left_x + 140, y, stiftung)
    y -= 22
    c.drawString(left_x, y, "Kontoinhaber:")
    c.drawString(left_x + 140, y, kontoinhaber_name)
    y -= 22
    c.drawString(left_x, y, "Steuerjahr:")
    c.drawString(left_x + 140, y, jahr)
    if kontonummer_redacted is not None:
        y -= 22
        c.drawString(left_x, y, "Konto-Nr.:")
        c.drawString(left_x + 140, y, kontonummer_redacted)
    if valutadatum is not None:
        y -= 22
        c.drawString(left_x, y, "Valutadatum:")
        c.drawString(left_x + 140, y, valutadatum)

    y -= 36
    c.setFont(font_name, 10)
    c.drawString(left_x, y, "Einzahlung im Steuerjahr (CHF)")
    c.drawRightString(right_x, y, einzahlung_betrag)

    c.setFont(font_name, 8)
    c.drawString(
        left_x,
        40,
        "Synthetische Test-Fixture — keine produktive 3a-Bescheinigung.",
    )


def generate_saeule_3a_standard(out_pdf: Path, out_expected: Path) -> dict[str, Any]:
    """Erzeugt ``saeule_3a_standard`` (STIFTUNG-V, Hans Muster, EXAKT 2024-Limit).

    Werte (RESEARCH-Korrektur — 2024-Limit ist 7'056, nicht 7'258):

    * Header ``Bescheinigung Säule 3a``
    * Stiftung ``STIFTUNG-V``
    * Kontoinhaber ``Hans Muster`` → Person-Inferenz ``mann``
    * Einzahlung ``7'056.00`` (= 2024-Limit Erwerbstätige mit BVG; ESTV-verifiziert)
    * Steuerjahr ``2024``
    """
    stiftung = "STIFTUNG-V"
    kontoinhaber = "Hans Muster"
    jahr = "2024"
    einzahlung = f"7{APO}056.00"

    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    c = _new_canvas(out_pdf)
    _draw_saeule_3a(
        c,
        font_name="Helvetica",
        header_title="Bescheinigung Säule 3a",
        stiftung=stiftung,
        kontoinhaber_name=kontoinhaber,
        jahr=jahr,
        einzahlung_betrag=einzahlung,
    )
    c.showPage()
    c.save()

    expected: dict[str, Any] = {
        "stiftung": {"value": stiftung, "anchor_present": True},
        "kontoinhaber_name": {"value": kontoinhaber, "anchor_present": True},
        "jahr": {"value": jahr, "anchor_present": True},
        "einzahlung_betrag": {"value": einzahlung, "anchor_present": True},
        "_meta": {
            "belegtyp": "saeule_3a",
            "level": "L1-clean",
            "font": "Helvetica",
            "expected_person": "mann",
        },
    }
    _write_expected(
        expected, out_expected, fixture="saeule_3a_standard", belegtyp="saeule_3a"
    )
    return expected


def generate_saeule_3a_viac(out_pdf: Path, out_expected: Path) -> dict[str, Any]:
    """Erzeugt ``saeule_3a_viac`` (Maria Muster, unter 2024-Limit, mit Valutadatum).

    Werte:

    * Header ``3a-Konto Bescheinigung`` (Header-Variante für Klassifikator-
      Robustheit).
    * Stiftung ``STIFTUNG-V``
    * Kontoinhaber ``Maria Muster`` → Person-Inferenz ``frau``
    * Einzahlung ``5'500.00`` (unter Limit, Plausibility-OK)
    * Valutadatum ``28.12.2024`` (Optional gefüllt)
    * Steuerjahr ``2024``
    """
    stiftung = "STIFTUNG-V"
    kontoinhaber = "Maria Muster"
    jahr = "2024"
    einzahlung = f"5{APO}500.00"
    valuta = "28.12.2024"

    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    c = _new_canvas(out_pdf)
    _draw_saeule_3a(
        c,
        font_name="Helvetica",
        header_title="3a-Konto Bescheinigung",
        stiftung=stiftung,
        kontoinhaber_name=kontoinhaber,
        jahr=jahr,
        einzahlung_betrag=einzahlung,
        valutadatum=valuta,
    )
    c.showPage()
    c.save()

    expected: dict[str, Any] = {
        "stiftung": {"value": stiftung, "anchor_present": True},
        "kontoinhaber_name": {"value": kontoinhaber, "anchor_present": True},
        "jahr": {"value": jahr, "anchor_present": True},
        "einzahlung_betrag": {"value": einzahlung, "anchor_present": True},
        "valutadatum": {"value": valuta, "anchor_present": True},
        "_meta": {
            "belegtyp": "saeule_3a",
            "level": "L1-clean",
            "font": "Helvetica",
            "expected_person": "frau",
        },
    }
    _write_expected(
        expected, out_expected, fixture="saeule_3a_viac", belegtyp="saeule_3a"
    )
    return expected


# ===========================================================================
# Phase 3 Wave 3 (Plan 03-04) — Wertschriften / Spenden / Berufsauslagen
# ===========================================================================
#
# Layout-Konventionen identisch zu Phase 1+2: drawString + drawRightString,
# Beträge als ein einzelner drawString-Call (Pitfall 3 RESEARCH). Familien-
# Namen aus evals/family.yaml.test (Hans/Maria Muster) für Person-Inferenz-
# Coverage. APO = U+0027 in Batch 1 — der U+2019-Edge-Case ist bereits in
# Phase-2-Fixture F2 abgedeckt.


def _draw_wertschriften(
    c: canvas.Canvas,
    *,
    font_name: str,
    header_title: str,
    institut: str,
    kontoinhaber_name: str,
    jahr: str,
    bestand_3112: str,
    bruttoertrag_total: str,
    verrechnungssteuer_total: str,
    depot_nr_redacted: str | None = None,
) -> None:
    """Zeichnet ein Wertschriftenverzeichnis-/Depot-Auszug-Layout auf ``c``.

    Header-Marker (``Wertschriftenverzeichnis`` / ``Depot-Auszug`` /
    ``Portfolio-Auszug``) in den ersten Wörtern für den Klassifikator
    (Plan 03-04). Beträge rechtsbündig, Labels linksbündig — analog
    Bank-Zinsausweis-Layout.
    """
    width, height = A4
    left_x = 60
    right_x = width - 60

    c.setFont(font_name, 14)
    c.drawString(left_x, height - 60, header_title)
    c.setFont(font_name, 9)
    c.drawString(left_x, height - 75, "Steuerreporting — Wertschriften Total (Stichtag 31.12.)")

    c.setFont(font_name, 10)
    y = height - 110
    c.drawString(left_x, y, "Institut:")
    c.drawString(left_x + 140, y, institut)
    y -= 22
    c.drawString(left_x, y, "Kontoinhaber:")
    c.drawString(left_x + 140, y, kontoinhaber_name)
    y -= 22
    c.drawString(left_x, y, "Steuerjahr:")
    c.drawString(left_x + 140, y, jahr)
    if depot_nr_redacted is not None:
        y -= 22
        c.drawString(left_x, y, "Depot-Nr.:")
        c.drawString(left_x + 140, y, depot_nr_redacted)

    y -= 36
    c.setFont(font_name, 10)
    rows = [
        ("Bestand per 31.12.", bestand_3112),
        ("Bruttoertrag total", bruttoertrag_total),
        ("Verrechnungssteuer (35%)", verrechnungssteuer_total),
    ]
    for label, amount in rows:
        c.drawString(left_x, y, label)
        c.drawRightString(right_x, y, amount)
        y -= 22

    c.setFont(font_name, 8)
    c.drawString(
        left_x,
        40,
        "Synthetische Test-Fixture — kein produktives Wertschriftenverzeichnis.",
    )


def generate_wertschriften_standard(
    out_pdf: Path,
    out_expected: Path,
) -> dict[str, Any]:
    """Erzeugt ``wertschriftenverzeichnis_standard`` (BANK-U, Hans Muster).

    Werte (Plan 03-04 §Behavior, family.yaml.test-Konsistenz):

    * Header ``Wertschriftenverzeichnis``
    * Institut ``BANK-U AG``
    * Kontoinhaber ``Hans Muster`` → Person-Inferenz ``mann``
    * Bestand 31.12. ``850'000.00``
    * Bruttoertrag total ``12'400.00``
    * Verrechnungssteuer ``4'340.00`` (= 35% × 12'400 — Plausibility-OK)
    * Steuerjahr ``2024``
    * Depot-Nr. ``XXXX-9876`` (last-4-Pattern)
    """
    institut = "BANK-U AG"
    kontoinhaber = "Hans Muster"
    jahr = "2024"
    bestand = f"850{APO}000.00"
    bruttoertrag = f"12{APO}400.00"
    verrechnungssteuer = f"4{APO}340.00"
    depot_nr = "XXXX-9876"

    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    c = _new_canvas(out_pdf)
    _draw_wertschriften(
        c,
        font_name="Helvetica",
        header_title="Wertschriftenverzeichnis",
        institut=institut,
        kontoinhaber_name=kontoinhaber,
        jahr=jahr,
        bestand_3112=bestand,
        bruttoertrag_total=bruttoertrag,
        verrechnungssteuer_total=verrechnungssteuer,
        depot_nr_redacted=depot_nr,
    )
    c.showPage()
    c.save()

    expected: dict[str, Any] = {
        "institut": {"value": institut, "anchor_present": True},
        "kontoinhaber_name": {"value": kontoinhaber, "anchor_present": True},
        "bestand_3112": {"value": bestand, "anchor_present": True},
        "bruttoertrag_total": {"value": bruttoertrag, "anchor_present": True},
        "verrechnungssteuer_total": {"value": verrechnungssteuer, "anchor_present": True},
        "jahr": {"value": jahr, "anchor_present": True},
        "depot_nr_redacted": {"value": depot_nr, "anchor_present": True},
        "_meta": {
            "belegtyp": "wertschriftenverzeichnis",
            "level": "L1-clean",
            "font": "Helvetica",
            "expected_person": "mann",
        },
    }
    _write_expected(
        expected,
        out_expected,
        fixture="wertschriftenverzeichnis_standard",
        belegtyp="wertschriftenverzeichnis",
    )
    return expected


def generate_wertschriften_joint(
    out_pdf: Path,
    out_expected: Path,
) -> dict[str, Any]:
    """Erzeugt ``wertschriftenverzeichnis_joint`` (BANK-Z, Joint-Depot Hans+Maria).

    Testet:

    * Alternativer Header ``Depot-Auszug`` (Klassifikator-Robustheit, D-D1).
    * Joint-Depot-Pattern ``Hans und Maria Muster`` → Person-Inferenz
      matcht beide Eltern → Rolle ``gemeinsam`` (analog Bank-bank-z-Fixture).
    * VRS = 35% × Bruttoertrag (1'120.00 = 0.35 × 3'200.00 — Plausibility-OK).
    """
    institut = "BANK-Z"
    kontoinhaber = "Hans und Maria Muster"
    jahr = "2024"
    bestand = f"215{APO}600.00"
    bruttoertrag = f"3{APO}200.00"
    verrechnungssteuer = f"1{APO}120.00"

    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    c = _new_canvas(out_pdf)
    _draw_wertschriften(
        c,
        font_name="Helvetica",
        header_title="Depot-Auszug",
        institut=institut,
        kontoinhaber_name=kontoinhaber,
        jahr=jahr,
        bestand_3112=bestand,
        bruttoertrag_total=bruttoertrag,
        verrechnungssteuer_total=verrechnungssteuer,
    )
    c.showPage()
    c.save()

    expected: dict[str, Any] = {
        "institut": {"value": institut, "anchor_present": True},
        "kontoinhaber_name": {"value": kontoinhaber, "anchor_present": True},
        "bestand_3112": {"value": bestand, "anchor_present": True},
        "bruttoertrag_total": {"value": bruttoertrag, "anchor_present": True},
        "verrechnungssteuer_total": {"value": verrechnungssteuer, "anchor_present": True},
        "jahr": {"value": jahr, "anchor_present": True},
        "_meta": {
            "belegtyp": "wertschriftenverzeichnis",
            "level": "L1-clean",
            "font": "Helvetica",
            "expected_person": "gemeinsam",
        },
    }
    _write_expected(
        expected,
        out_expected,
        fixture="wertschriftenverzeichnis_joint",
        belegtyp="wertschriftenverzeichnis",
    )
    return expected


# ---------------------------------------------------------------------------
# Spenden-Generatoren
# ---------------------------------------------------------------------------


def _draw_spenden(
    c: canvas.Canvas,
    *,
    font_name: str,
    header_title: str,
    empfaenger: str,
    spender_name: str,
    jahr: str,
    betrag: str,
    steuerbefreiungs_status: str | None = None,
) -> None:
    """Zeichnet eine Spendenbescheinigung / Zuwendungsbestätigung auf ``c``."""
    width, height = A4
    left_x = 60
    right_x = width - 60

    c.setFont(font_name, 14)
    c.drawString(left_x, height - 60, header_title)
    c.setFont(font_name, 9)
    c.drawString(left_x, height - 75, "Bescheinigung über eine Zuwendung (Spende)")

    c.setFont(font_name, 10)
    y = height - 110
    c.drawString(left_x, y, "Empfänger:")
    c.drawString(left_x + 140, y, empfaenger)
    y -= 22
    c.drawString(left_x, y, "Spender:")
    c.drawString(left_x + 140, y, spender_name)
    y -= 22
    c.drawString(left_x, y, "Steuerjahr:")
    c.drawString(left_x + 140, y, jahr)

    y -= 36
    c.setFont(font_name, 10)
    c.drawString(left_x, y, "Spendenbetrag")
    c.drawRightString(right_x, y, betrag)

    if steuerbefreiungs_status is not None:
        y -= 36
        c.setFont(font_name, 9)
        c.drawString(left_x, y, steuerbefreiungs_status)

    c.setFont(font_name, 8)
    c.drawString(
        left_x,
        40,
        "Synthetische Test-Fixture — keine produktive Spendenquittung.",
    )


def generate_spenden_standard(
    out_pdf: Path,
    out_expected: Path,
    *,
    font: str = "Helvetica",
) -> dict[str, Any]:
    """Erzeugt ``spenden_standard`` (WWF Schweiz, Maria Muster).

    Werte:

    * Header ``Spendenbescheinigung``
    * Empfänger ``WWF Schweiz``
    * Spender ``Maria Muster`` → Person-Inferenz ``frau``
    * Betrag ``500.00`` (CHF, keine Apostroph-Trennung nötig)
    * Steuerjahr ``2024``
    * Status "Die Organisation ist als gemeinnützig anerkannt"

    ``font`` wird vom Custom-Font-Generator überschrieben.
    """
    empfaenger = "WWF Schweiz"
    spender = "Maria Muster"
    jahr = "2024"
    betrag = "500.00"
    status = "Die Organisation ist als gemeinnützig anerkannt"

    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    c = _new_canvas(out_pdf)
    _draw_spenden(
        c,
        font_name=font,
        header_title="Spendenbescheinigung",
        empfaenger=empfaenger,
        spender_name=spender,
        jahr=jahr,
        betrag=betrag,
        steuerbefreiungs_status=status,
    )
    c.showPage()
    c.save()

    expected: dict[str, Any] = {
        "empfaenger": {"value": empfaenger, "anchor_present": True},
        "spender_name": {"value": spender, "anchor_present": True},
        "betrag": {"value": betrag, "anchor_present": True},
        "jahr": {"value": jahr, "anchor_present": True},
        "steuerbefreiungs_status": {"value": status, "anchor_present": True},
        "_meta": {
            "belegtyp": "spenden",
            "level": "L1-clean",
            "font": font,
            "expected_person": "frau",
        },
    }
    _write_expected(
        expected,
        out_expected,
        fixture="spenden_standard",
        belegtyp="spenden",
    )
    return expected


def generate_spenden_custom_font(
    out_pdf: Path,
    out_expected: Path,
) -> dict[str, Any]:
    """Erzeugt ``spenden_custom_font`` (Inhalt = standard, Font = Inter, L2).

    Custom-Font-L2-Variante für Spenden — testet den Glyph-Mapping-Pitfall
    (Pitfall 6 in 01-RESEARCH.md) analog zu ``bank_zinsausweis_custom_font``.
    Fällt auf Helvetica zurück, falls ``Inter-Regular.ttf`` fehlt.
    """
    font_used, font_level = _try_register_inter()
    expected = generate_spenden_standard(out_pdf, out_expected, font=font_used)
    expected = dict(expected)
    expected["_meta"] = {
        "belegtyp": "spenden",
        "level": font_level,
        "font": font_used,
        "expected_person": "frau",
    }
    _write_expected(
        expected,
        out_expected,
        fixture="spenden_custom_font",
        belegtyp="spenden",
    )
    return expected


# ---------------------------------------------------------------------------
# Berufsauslagen-Generatoren
# ---------------------------------------------------------------------------


def _draw_berufsauslagen(
    c: canvas.Canvas,
    *,
    font_name: str,
    header_title: str,
    anbieter: str,
    person_name: str,
    jahr: str,
    betrag: str,
    kursart: str | None = None,
) -> None:
    """Zeichnet eine Berufsauslagen-/Kursbestätigung auf ``c``."""
    width, height = A4
    left_x = 60
    right_x = width - 60

    c.setFont(font_name, 14)
    c.drawString(left_x, height - 60, header_title)
    c.setFont(font_name, 9)
    c.drawString(left_x, height - 75, "Bescheinigung Weiterbildung / Berufsauslagen")

    c.setFont(font_name, 10)
    y = height - 110
    c.drawString(left_x, y, "Anbieter:")
    c.drawString(left_x + 140, y, anbieter)
    y -= 22
    c.drawString(left_x, y, "Teilnehmer:")
    c.drawString(left_x + 140, y, person_name)
    y -= 22
    c.drawString(left_x, y, "Steuerjahr:")
    c.drawString(left_x + 140, y, jahr)
    if kursart is not None:
        y -= 22
        c.drawString(left_x, y, "Kursart:")
        c.drawString(left_x + 140, y, kursart)

    y -= 36
    c.setFont(font_name, 10)
    c.drawString(left_x, y, "Kurskosten gesamt")
    c.drawRightString(right_x, y, betrag)

    c.setFont(font_name, 8)
    c.drawString(
        left_x,
        40,
        "Synthetische Test-Fixture — keine produktive Kursbestätigung.",
    )


def generate_berufsauslagen_standard(
    out_pdf: Path,
    out_expected: Path,
) -> dict[str, Any]:
    """Erzeugt ``berufsauslagen_standard`` (EB Zürich, Hans Muster, MAS).

    Werte:

    * Header ``Kursbestätigung``
    * Anbieter ``EB Zürich``
    * Teilnehmer ``Hans Muster`` → Person-Inferenz ``mann``
    * Betrag ``1'850.00``
    * Kursart ``Weiterbildung``
    * Steuerjahr ``2024``
    """
    anbieter = "EB Zürich"
    person = "Hans Muster"
    jahr = "2024"
    betrag = f"1{APO}850.00"
    kursart = "Weiterbildung"

    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    c = _new_canvas(out_pdf)
    _draw_berufsauslagen(
        c,
        font_name="Helvetica",
        header_title="Kursbestätigung",
        anbieter=anbieter,
        person_name=person,
        jahr=jahr,
        betrag=betrag,
        kursart=kursart,
    )
    c.showPage()
    c.save()

    expected: dict[str, Any] = {
        "anbieter": {"value": anbieter, "anchor_present": True},
        "person_name": {"value": person, "anchor_present": True},
        "betrag": {"value": betrag, "anchor_present": True},
        "jahr": {"value": jahr, "anchor_present": True},
        "kursart": {"value": kursart, "anchor_present": True},
        "_meta": {
            "belegtyp": "berufsauslagen",
            "level": "L1-clean",
            "font": "Helvetica",
            "expected_person": "mann",
        },
    }
    _write_expected(
        expected,
        out_expected,
        fixture="berufsauslagen_standard",
        belegtyp="berufsauslagen",
    )
    return expected


def generate_berufsauslagen_hsg(
    out_pdf: Path,
    out_expected: Path,
) -> dict[str, Any]:
    """Erzeugt ``berufsauslagen_hsg`` (Universität St. Gallen, Maria Muster).

    Testet:

    * Header-Variante ``Weiterbildung — Bescheinigung`` (Klassifikator-
      Robustheit, D-D1).
    * Anbieter mit Spezialzeichen (``Universität St. Gallen``).
    * Optionales Feld ``kursart`` weggelassen — prüft Optional-Handling.
    * Person ``Maria Muster`` → Person-Inferenz ``frau``.
    """
    anbieter = "Universität St. Gallen"
    person = "Maria Muster"
    jahr = "2024"
    betrag = f"4{APO}200.00"

    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    c = _new_canvas(out_pdf)
    _draw_berufsauslagen(
        c,
        font_name="Helvetica",
        header_title="Weiterbildung — Bescheinigung",
        anbieter=anbieter,
        person_name=person,
        jahr=jahr,
        betrag=betrag,
        kursart=None,
    )
    c.showPage()
    c.save()

    expected: dict[str, Any] = {
        "anbieter": {"value": anbieter, "anchor_present": True},
        "person_name": {"value": person, "anchor_present": True},
        "betrag": {"value": betrag, "anchor_present": True},
        "jahr": {"value": jahr, "anchor_present": True},
        "_meta": {
            "belegtyp": "berufsauslagen",
            "level": "L1-clean",
            "font": "Helvetica",
            "expected_person": "frau",
        },
    }
    _write_expected(
        expected,
        out_expected,
        fixture="berufsauslagen_hsg",
        belegtyp="berufsauslagen",
    )
    return expected


# ===========================================================================
# Phase 3 Wave 4 (Plan 03-05) — Kinderbetreuung / Hypothek /
# Liegenschaftsunterhalt / Krankheitskosten
# ===========================================================================
#
# Layout-Konventionen identisch zu Phase 1+2+Wave 3: drawString +
# drawRightString, Beträge als ein einzelner drawString-Call. Family-Namen
# aus evals/family.yaml.test (Hans/Maria/Lina/Tim Muster) für Person-Inferenz.
# APO = U+0027 in Wave 4 — U+2019-Edge-Case bleibt in F2 abgedeckt.


# ---------------------------------------------------------------------------
# Kinderbetreuung-Generatoren (D-E3)
# ---------------------------------------------------------------------------


def _draw_kinderbetreuung(
    c: canvas.Canvas,
    *,
    font_name: str,
    header_title: str,
    anbieter: str,
    kind_name: str,
    jahr: str,
    betrag: str,
    betreuungs_typ: str | None = None,
) -> None:
    """Zeichnet eine Kinderbetreuungs-Bestätigung (Kita/Hort/Tagesfamilie) auf ``c``."""
    width, height = A4
    left_x = 60
    right_x = width - 60

    c.setFont(font_name, 14)
    c.drawString(left_x, height - 60, header_title)
    c.setFont(font_name, 9)
    c.drawString(left_x, height - 75, "Bestätigung über Kinderbetreuungskosten")

    c.setFont(font_name, 10)
    y = height - 110
    c.drawString(left_x, y, "Anbieter:")
    c.drawString(left_x + 160, y, anbieter)
    y -= 22
    c.drawString(left_x, y, "Betreutes Kind:")
    c.drawString(left_x + 160, y, kind_name)
    y -= 22
    c.drawString(left_x, y, "Steuerjahr:")
    c.drawString(left_x + 160, y, jahr)
    if betreuungs_typ is not None:
        y -= 22
        c.drawString(left_x, y, "Betreuungs-Typ:")
        c.drawString(left_x + 160, y, betreuungs_typ)

    y -= 36
    c.setFont(font_name, 10)
    c.drawString(left_x, y, "Betreuungskosten Total")
    c.drawRightString(right_x, y, betrag)

    c.setFont(font_name, 8)
    c.drawString(
        left_x,
        40,
        "Synthetische Test-Fixture — keine produktive Kinderbetreuungs-Bestätigung.",
    )


def generate_kinderbetreuung_standard(
    out_pdf: Path,
    out_expected: Path,
) -> dict[str, Any]:
    """Erzeugt ``kinderbetreuung_standard`` (Kita Sonnenschein, Lina als kind1).

    Werte (Plan 03-05 §Behavior):

    * Header ``Kinderbetreuung — Bestätigung``
    * Anbieter ``Kita Sonnenschein``
    * Kind ``Lina Muster`` → Person-Inferenz ``kind1``
    * Betrag ``18'500.00`` (unter ZH-2024-Cap 25'000)
    * Betreuungs-Typ ``Kita``
    * Steuerjahr ``2024``
    """
    anbieter = "Kita Sonnenschein"
    kind = "Lina Muster"
    jahr = "2024"
    betrag = f"18{APO}500.00"
    typ = "Kita"

    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    c = _new_canvas(out_pdf)
    _draw_kinderbetreuung(
        c,
        font_name="Helvetica",
        header_title="Kinderbetreuung — Bestätigung",
        anbieter=anbieter,
        kind_name=kind,
        jahr=jahr,
        betrag=betrag,
        betreuungs_typ=typ,
    )
    c.showPage()
    c.save()

    expected: dict[str, Any] = {
        "anbieter": {"value": anbieter, "anchor_present": True},
        "kind_name": {"value": kind, "anchor_present": True},
        "betrag": {"value": betrag, "anchor_present": True},
        "jahr": {"value": jahr, "anchor_present": True},
        "betreuungs_typ": {"value": typ, "anchor_present": True},
        "_meta": {
            "belegtyp": "kinderbetreuung",
            "level": "L1-clean",
            "font": "Helvetica",
            "expected_person": "kind1",
        },
    }
    _write_expected(
        expected,
        out_expected,
        fixture="kinderbetreuung_standard",
        belegtyp="kinderbetreuung",
    )
    return expected


def generate_kinderbetreuung_edge_cap(
    out_pdf: Path,
    out_expected: Path,
) -> dict[str, Any]:
    """Erzeugt ``kinderbetreuung_edge_cap`` (Tagi Hort, Tim als kind2, knapp unter Cap).

    Testet:

    * Alternativer Header ``Hort-Betreuungskosten``.
    * Kind ``Tim Muster`` → Person-Inferenz ``kind2``.
    * Betrag ``24'800.00`` (knapp unter ZH-2024-Cap 25'000 → Plausibility-OK).
    """
    anbieter = "Tagi Hort Zürich"
    kind = "Tim Muster"
    jahr = "2024"
    betrag = f"24{APO}800.00"
    typ = "Hort"

    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    c = _new_canvas(out_pdf)
    _draw_kinderbetreuung(
        c,
        font_name="Helvetica",
        header_title="Hort-Betreuungskosten",
        anbieter=anbieter,
        kind_name=kind,
        jahr=jahr,
        betrag=betrag,
        betreuungs_typ=typ,
    )
    c.showPage()
    c.save()

    expected: dict[str, Any] = {
        "anbieter": {"value": anbieter, "anchor_present": True},
        "kind_name": {"value": kind, "anchor_present": True},
        "betrag": {"value": betrag, "anchor_present": True},
        "jahr": {"value": jahr, "anchor_present": True},
        "betreuungs_typ": {"value": typ, "anchor_present": True},
        "_meta": {
            "belegtyp": "kinderbetreuung",
            "level": "L1-clean",
            "font": "Helvetica",
            "expected_person": "kind2",
        },
    }
    _write_expected(
        expected,
        out_expected,
        fixture="kinderbetreuung_edge_cap",
        belegtyp="kinderbetreuung",
    )
    return expected


# ---------------------------------------------------------------------------
# Hypothek-Generatoren (D-E4)
# ---------------------------------------------------------------------------


def _draw_hypothek(
    c: canvas.Canvas,
    *,
    font_name: str,
    header_title: str,
    institut: str,
    kontoinhaber_name: str,
    liegenschaft: str,
    jahr: str,
    schuldzinsen: str,
    schuldsaldo_3112: str,
) -> None:
    """Zeichnet eine Hypothek-Zinsbestätigung / Schuldzinsausweis auf ``c``."""
    width, height = A4
    left_x = 60
    right_x = width - 60

    c.setFont(font_name, 14)
    c.drawString(left_x, height - 60, header_title)
    c.setFont(font_name, 9)
    c.drawString(left_x, height - 75, "Bescheinigung Hypothekarzinsen und Schuldsaldo")

    c.setFont(font_name, 10)
    y = height - 110
    c.drawString(left_x, y, "Institut:")
    c.drawString(left_x + 160, y, institut)
    y -= 22
    c.drawString(left_x, y, "Kontoinhaber:")
    c.drawString(left_x + 160, y, kontoinhaber_name)
    y -= 22
    c.drawString(left_x, y, "Liegenschaft:")
    c.drawString(left_x + 160, y, liegenschaft)
    y -= 22
    c.drawString(left_x, y, "Steuerjahr:")
    c.drawString(left_x + 160, y, jahr)

    y -= 36
    c.setFont(font_name, 10)
    rows = [
        ("Schuldzinsen gesamt", schuldzinsen),
        ("Schuldsaldo per 31.12.", schuldsaldo_3112),
    ]
    for label, amount in rows:
        c.drawString(left_x, y, label)
        c.drawRightString(right_x, y, amount)
        y -= 22

    c.setFont(font_name, 8)
    c.drawString(
        left_x,
        40,
        "Synthetische Test-Fixture — keine produktive Hypothek-Zinsbestätigung.",
    )


def generate_hypothek_standard(out_pdf: Path, out_expected: Path) -> dict[str, Any]:
    """Erzeugt ``hypothek_zinsbestaetigung_standard`` (BANK-Z, Joint Hans+Maria).

    Werte (Plan 03-05 §Behavior):

    * Header ``Hypothek-Zinsbestätigung``
    * Institut ``BANK-Z``
    * Kontoinhaber ``Hans und Maria Muster`` → Person-Inferenz ``gemeinsam``
    * Liegenschaft ``EFH Bahnhofstr. 10, 8001 Zürich``
    * Schuldzinsen ``8'400.00``
    * Schuldsaldo ``420'000.00`` (Ratio 2% — Plausibility-OK)
    * Steuerjahr ``2024``
    """
    institut = "BANK-Z"
    kontoinhaber = "Hans und Maria Muster"
    liegenschaft = "EFH Bahnhofstr. 10, 8001 Zürich"
    jahr = "2024"
    schuldzinsen = f"8{APO}400.00"
    schuldsaldo = f"420{APO}000.00"

    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    c = _new_canvas(out_pdf)
    _draw_hypothek(
        c,
        font_name="Helvetica",
        header_title="Hypothek-Zinsbestätigung",
        institut=institut,
        kontoinhaber_name=kontoinhaber,
        liegenschaft=liegenschaft,
        jahr=jahr,
        schuldzinsen=schuldzinsen,
        schuldsaldo_3112=schuldsaldo,
    )
    c.showPage()
    c.save()

    expected: dict[str, Any] = {
        "institut": {"value": institut, "anchor_present": True},
        "kontoinhaber_name": {"value": kontoinhaber, "anchor_present": True},
        "liegenschaft": {"value": liegenschaft, "anchor_present": True},
        "schuldzinsen": {"value": schuldzinsen, "anchor_present": True},
        "schuldsaldo_3112": {"value": schuldsaldo, "anchor_present": True},
        "jahr": {"value": jahr, "anchor_present": True},
        "_meta": {
            "belegtyp": "hypothek_zinsbestaetigung",
            "level": "L1-clean",
            "font": "Helvetica",
            "expected_person": "gemeinsam",
        },
    }
    _write_expected(
        expected,
        out_expected,
        fixture="hypothek_zinsbestaetigung_standard",
        belegtyp="hypothek_zinsbestaetigung",
    )
    return expected


def generate_hypothek_raiffeisen(out_pdf: Path, out_expected: Path) -> dict[str, Any]:
    """Erzeugt ``hypothek_zinsbestaetigung_raiffeisen`` (BANK-R, Hans alleine).

    Testet:

    * Alternativer Header ``Schuldzinsausweis`` (Klassifikator-Robustheit).
    * Institut ``BANK-R``.
    * Kontoinhaber ``Hans Muster`` → Person-Inferenz ``mann``.
    * Schuldzinsen ``5'600.00`` / Schuldsaldo ``350'000.00`` (Ratio 1.6% — OK).
    """
    institut = "BANK-R"
    kontoinhaber = "Hans Muster"
    liegenschaft = "ETW Seefeldstr. 22, 8008 Zürich"
    jahr = "2024"
    schuldzinsen = f"5{APO}600.00"
    schuldsaldo = f"350{APO}000.00"

    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    c = _new_canvas(out_pdf)
    _draw_hypothek(
        c,
        font_name="Helvetica",
        header_title="Schuldzinsausweis",
        institut=institut,
        kontoinhaber_name=kontoinhaber,
        liegenschaft=liegenschaft,
        jahr=jahr,
        schuldzinsen=schuldzinsen,
        schuldsaldo_3112=schuldsaldo,
    )
    c.showPage()
    c.save()

    expected: dict[str, Any] = {
        "institut": {"value": institut, "anchor_present": True},
        "kontoinhaber_name": {"value": kontoinhaber, "anchor_present": True},
        "liegenschaft": {"value": liegenschaft, "anchor_present": True},
        "schuldzinsen": {"value": schuldzinsen, "anchor_present": True},
        "schuldsaldo_3112": {"value": schuldsaldo, "anchor_present": True},
        "jahr": {"value": jahr, "anchor_present": True},
        "_meta": {
            "belegtyp": "hypothek_zinsbestaetigung",
            "level": "L1-clean",
            "font": "Helvetica",
            "expected_person": "mann",
        },
    }
    _write_expected(
        expected,
        out_expected,
        fixture="hypothek_zinsbestaetigung_raiffeisen",
        belegtyp="hypothek_zinsbestaetigung",
    )
    return expected


# ---------------------------------------------------------------------------
# Liegenschaftsunterhalt-Generatoren (D-E5)
# ---------------------------------------------------------------------------


def _draw_liegenschaftsunterhalt(
    c: canvas.Canvas,
    *,
    font_name: str,
    header_title: str,
    handwerker_anbieter: str,
    eigentuemer_name: str,
    liegenschaft: str,
    jahr: str,
    betrag: str,
    beschreibung: str,
) -> None:
    """Zeichnet eine Handwerker-Rechnung / Liegenschaftsunterhalt-Bestätigung auf ``c``.

    Der Beschreibungstext (``Reparatur Fassaden-Anstrich Süd`` /
    ``Anbau Wintergarten gemäss Offerte``) wird in Plan 03-06 von
    :func:`extractors.confidence.classify_werterhaltend` konsumiert.
    """
    width, height = A4
    left_x = 60
    right_x = width - 60

    c.setFont(font_name, 14)
    c.drawString(left_x, height - 60, header_title)
    c.setFont(font_name, 9)
    c.drawString(left_x, height - 75, "Rechnung Liegenschaftsunterhalt")

    c.setFont(font_name, 10)
    y = height - 110
    c.drawString(left_x, y, "Handwerker:")
    c.drawString(left_x + 160, y, handwerker_anbieter)
    y -= 22
    c.drawString(left_x, y, "Eigentümer:")
    c.drawString(left_x + 160, y, eigentuemer_name)
    y -= 22
    c.drawString(left_x, y, "Liegenschaft:")
    c.drawString(left_x + 160, y, liegenschaft)
    y -= 22
    c.drawString(left_x, y, "Steuerjahr:")
    c.drawString(left_x + 160, y, jahr)

    # Beschreibungstext für werterhaltend-Heuristik (Plan 03-06).
    y -= 28
    c.setFont(font_name, 9)
    c.drawString(left_x, y, "Beschreibung:")
    c.drawString(left_x + 160, y, beschreibung)

    y -= 36
    c.setFont(font_name, 10)
    c.drawString(left_x, y, "Rechnungsbetrag")
    c.drawRightString(right_x, y, betrag)

    c.setFont(font_name, 8)
    c.drawString(
        left_x,
        40,
        "Synthetische Test-Fixture — keine produktive Handwerker-Rechnung.",
    )


def generate_liegenschaftsunterhalt_reparatur(
    out_pdf: Path,
    out_expected: Path,
) -> dict[str, Any]:
    """Erzeugt ``liegenschaftsunterhalt_reparatur`` (Maler Müller, Reparatur Fassade).

    Werte (Plan 03-05 §Behavior — werterhaltend=True erwartet):

    * Header ``Handwerker-Rechnung — Liegenschaftsunterhalt``.
    * Handwerker ``Maler Müller AG, Zürich``.
    * Eigentümer ``Hans Muster`` → Person-Inferenz ``mann``.
    * Beschreibung "Reparatur Fassaden-Anstrich Süd" → werterhaltend=True
      via classify_werterhaltend (Plan 03-06).
    * Betrag ``3'200.00``.
    """
    handwerker = "Maler Müller AG, Zürich"
    eigentuemer = "Hans Muster"
    liegenschaft = "EFH Bahnhofstr. 10, 8001 Zürich"
    jahr = "2024"
    betrag = f"3{APO}200.00"
    beschreibung = "Reparatur Fassaden-Anstrich Süd"

    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    c = _new_canvas(out_pdf)
    _draw_liegenschaftsunterhalt(
        c,
        font_name="Helvetica",
        header_title="Handwerker-Rechnung — Liegenschaftsunterhalt",
        handwerker_anbieter=handwerker,
        eigentuemer_name=eigentuemer,
        liegenschaft=liegenschaft,
        jahr=jahr,
        betrag=betrag,
        beschreibung=beschreibung,
    )
    c.showPage()
    c.save()

    expected: dict[str, Any] = {
        "handwerker_anbieter": {"value": handwerker, "anchor_present": True},
        "eigentuemer_name": {"value": eigentuemer, "anchor_present": True},
        "liegenschaft": {"value": liegenschaft, "anchor_present": True},
        "betrag": {"value": betrag, "anchor_present": True},
        "jahr": {"value": jahr, "anchor_present": True},
        "werterhaltend": None,
        "_meta": {
            "belegtyp": "liegenschaftsunterhalt",
            "level": "L1-clean",
            "font": "Helvetica",
            "expected_person": "mann",
            "expected_werterhaltend": True,
            "beschreibung": beschreibung,
        },
    }
    _write_expected(
        expected,
        out_expected,
        fixture="liegenschaftsunterhalt_reparatur",
        belegtyp="liegenschaftsunterhalt",
    )
    return expected


def generate_liegenschaftsunterhalt_anbau(
    out_pdf: Path,
    out_expected: Path,
) -> dict[str, Any]:
    """Erzeugt ``liegenschaftsunterhalt_anbau`` (BauPlan, Anbau Wintergarten).

    Werte (Plan 03-05 §Behavior — werterhaltend=False erwartet):

    * Header ``Unterhaltskosten — Rechnung``.
    * Handwerker ``BauPlan Schweiz GmbH``.
    * Eigentümer ``Maria Muster`` → Person-Inferenz ``frau``.
    * Beschreibung "Anbau Wintergarten gemäss Offerte" → werterhaltend=False.
    * Betrag ``42'500.00``.
    """
    handwerker = "BauPlan Schweiz GmbH"
    eigentuemer = "Maria Muster"
    liegenschaft = "EFH Bahnhofstr. 10, 8001 Zürich"
    jahr = "2024"
    betrag = f"42{APO}500.00"
    beschreibung = "Anbau Wintergarten gemäss Offerte"

    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    c = _new_canvas(out_pdf)
    _draw_liegenschaftsunterhalt(
        c,
        font_name="Helvetica",
        header_title="Unterhaltskosten — Rechnung",
        handwerker_anbieter=handwerker,
        eigentuemer_name=eigentuemer,
        liegenschaft=liegenschaft,
        jahr=jahr,
        betrag=betrag,
        beschreibung=beschreibung,
    )
    c.showPage()
    c.save()

    expected: dict[str, Any] = {
        "handwerker_anbieter": {"value": handwerker, "anchor_present": True},
        "eigentuemer_name": {"value": eigentuemer, "anchor_present": True},
        "liegenschaft": {"value": liegenschaft, "anchor_present": True},
        "betrag": {"value": betrag, "anchor_present": True},
        "jahr": {"value": jahr, "anchor_present": True},
        "werterhaltend": None,
        "_meta": {
            "belegtyp": "liegenschaftsunterhalt",
            "level": "L1-clean",
            "font": "Helvetica",
            "expected_person": "frau",
            "expected_werterhaltend": False,
            "beschreibung": beschreibung,
        },
    }
    _write_expected(
        expected,
        out_expected,
        fixture="liegenschaftsunterhalt_anbau",
        belegtyp="liegenschaftsunterhalt",
    )
    return expected


# ---------------------------------------------------------------------------
# Krankheitskosten-Generatoren (D-E6)
# ---------------------------------------------------------------------------


def _draw_krankheitskosten(
    c: canvas.Canvas,
    *,
    font_name: str,
    header_title: str,
    leistungserbringer: str,
    person_name: str,
    jahr: str,
    betrag: str,
    kategorie: str | None = None,
) -> None:
    """Zeichnet eine Krankheitskosten-Rechnung (Arzt/Apotheke/Spital) auf ``c``."""
    width, height = A4
    left_x = 60
    right_x = width - 60

    c.setFont(font_name, 14)
    c.drawString(left_x, height - 60, header_title)
    c.setFont(font_name, 9)
    c.drawString(left_x, height - 75, "Rechnung Krankheitskosten (Patientenanteil)")

    c.setFont(font_name, 10)
    y = height - 110
    c.drawString(left_x, y, "Leistungserbringer:")
    c.drawString(left_x + 170, y, leistungserbringer)
    y -= 22
    c.drawString(left_x, y, "Behandelte Person:")
    c.drawString(left_x + 170, y, person_name)
    y -= 22
    c.drawString(left_x, y, "Steuerjahr:")
    c.drawString(left_x + 170, y, jahr)
    if kategorie is not None:
        y -= 22
        c.drawString(left_x, y, "Kategorie:")
        c.drawString(left_x + 170, y, kategorie)

    y -= 36
    c.setFont(font_name, 10)
    c.drawString(left_x, y, "Rechnungsbetrag")
    c.drawRightString(right_x, y, betrag)

    c.setFont(font_name, 8)
    c.drawString(
        left_x,
        40,
        "Synthetische Test-Fixture — keine produktive Krankheitskosten-Rechnung.",
    )


def generate_krankheitskosten_arzt(
    out_pdf: Path,
    out_expected: Path,
) -> dict[str, Any]:
    """Erzeugt ``krankheitskosten_arzt`` (Dr. Meier, Maria als frau).

    Werte:

    * Header ``Arzt-Rechnung``.
    * Leistungserbringer ``Dr. med. Hans Meier, Zürich``.
    * Person ``Maria Muster`` → Person-Inferenz ``frau``.
    * Betrag ``285.00``.
    * Kategorie ``Arzt``.
    """
    leistung = "Dr. med. Hans Meier, Zürich"
    person = "Maria Muster"
    jahr = "2024"
    betrag = "285.00"
    kategorie = "Arzt"

    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    c = _new_canvas(out_pdf)
    _draw_krankheitskosten(
        c,
        font_name="Helvetica",
        header_title="Arzt-Rechnung",
        leistungserbringer=leistung,
        person_name=person,
        jahr=jahr,
        betrag=betrag,
        kategorie=kategorie,
    )
    c.showPage()
    c.save()

    expected: dict[str, Any] = {
        "leistungserbringer": {"value": leistung, "anchor_present": True},
        "person_name": {"value": person, "anchor_present": True},
        "betrag": {"value": betrag, "anchor_present": True},
        "jahr": {"value": jahr, "anchor_present": True},
        "kategorie": {"value": kategorie, "anchor_present": True},
        "_meta": {
            "belegtyp": "krankheitskosten",
            "level": "L1-clean",
            "font": "Helvetica",
            "expected_person": "frau",
        },
    }
    _write_expected(
        expected,
        out_expected,
        fixture="krankheitskosten_arzt",
        belegtyp="krankheitskosten",
    )
    return expected


def generate_krankheitskosten_apotheke(
    out_pdf: Path,
    out_expected: Path,
) -> dict[str, Any]:
    """Erzeugt ``krankheitskosten_apotheke`` (Apotheke zur Krone, Tim als kind2).

    Werte:

    * Header ``Apotheken-Rechnung``.
    * Leistungserbringer ``Apotheke zur Krone``.
    * Person ``Tim Muster`` → Person-Inferenz ``kind2``.
    * Betrag ``87.50``.
    * Kategorie ``Medikament``.
    """
    leistung = "Apotheke zur Krone"
    person = "Tim Muster"
    jahr = "2024"
    betrag = "87.50"
    kategorie = "Medikament"

    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    c = _new_canvas(out_pdf)
    _draw_krankheitskosten(
        c,
        font_name="Helvetica",
        header_title="Apotheken-Rechnung",
        leistungserbringer=leistung,
        person_name=person,
        jahr=jahr,
        betrag=betrag,
        kategorie=kategorie,
    )
    c.showPage()
    c.save()

    expected: dict[str, Any] = {
        "leistungserbringer": {"value": leistung, "anchor_present": True},
        "person_name": {"value": person, "anchor_present": True},
        "betrag": {"value": betrag, "anchor_present": True},
        "jahr": {"value": jahr, "anchor_present": True},
        "kategorie": {"value": kategorie, "anchor_present": True},
        "_meta": {
            "belegtyp": "krankheitskosten",
            "level": "L1-clean",
            "font": "Helvetica",
            "expected_person": "kind2",
        },
    }
    _write_expected(
        expected,
        out_expected,
        fixture="krankheitskosten_apotheke",
        belegtyp="krankheitskosten",
    )
    return expected


# ---------------------------------------------------------------------------
# CLI-Entry-Point
# ---------------------------------------------------------------------------


# ===========================================================================
# Phase 3 Wave 5 (Plan 03-06) — LLM-Fallback-Klassifikator-Fixture (D-C6)
# ===========================================================================
#
# Synthetisches PDF, das KEINEN der 11 Header-Regex matcht (kein
# "Lohnausweis", kein "Form 11"), aber inhaltlich klar ein Lohnausweis ist.
# Wird in evals/test_classifier_fallback.py konsumiert, um zu verifizieren,
# dass classify() bei max_score == 0 den LLM-Fallback aufruft und das
# Dokument trotzdem als ``lohnausweis`` erkennt.


def _draw_lohnausweis_headerless(
    c: canvas.Canvas,
    *,
    font_name: str,
    employer: str,
    period_von: str,
    period_bis: str,
    positions: list[Position],
) -> None:
    """Zeichnet einen Lohnausweis OHNE die Header-Strings ``Lohnausweis`` / ``Form 11``.

    Statt der Header-Strings nutzen wir eine generische Überschrift
    (``Mitarbeiter-Vergütungsabrechnung 2024``), die KEINEN der 11
    ``_PATTERNS``-Regex matcht. Pos-Labels (Pos 8 / Pos 9 / Pos 10.1 / Pos
    11) bleiben — das ist genau das semantische Signal, das ein LLM
    auswerten muss.
    """
    width, height = A4
    left_x = 60
    right_x = width - 60

    # --- Kopfzeile OHNE Header-Marker -----------------------------------
    c.setFont(font_name, 14)
    c.drawString(left_x, height - 60, "Mitarbeiter-Vergütungsabrechnung 2024")
    c.setFont(font_name, 9)
    c.drawString(left_x, height - 75, "Interne Personalabteilung")

    # --- Arbeitgeber + Periode ------------------------------------------
    c.setFont(font_name, 10)
    y = height - 110
    c.drawString(left_x, y, "Arbeitgeber:")
    c.drawString(left_x + 120, y, employer)
    y -= 22
    c.drawString(left_x, y, "Periode von:")
    c.drawString(left_x + 120, y, period_von)
    y -= 22
    c.drawString(left_x, y, "Periode bis:")
    c.drawString(left_x + 120, y, period_bis)

    # --- Positionsblock --------------------------------------------------
    y -= 36
    c.setFont(font_name, 10)
    for pos in positions:
        c.drawString(left_x, y, pos.label)
        c.drawRightString(right_x, y, pos.amount)
        y -= 22

    # --- Footer ----------------------------------------------------------
    c.setFont(font_name, 8)
    c.drawString(
        left_x,
        40,
        "Synthetische Test-Fixture — Header bewusst entfernt (Plan 03-06 LLM-Fallback).",
    )


def generate_lohnausweis_no_header(out_pdf: Path, out_expected: Path) -> dict[str, Any]:
    """Erzeugt ``lohnausweis_no_header.pdf`` für den LLM-Fallback-Test (D-C6).

    Inhalt analog F1 (gleiche Beträge, gleicher Arbeitgeber Hans Muster /
    ACME AG), ABER der Header-Marker ``Lohnausweis`` und ``Form 11`` fehlt.
    Stattdessen steht im Kopf ``Mitarbeiter-Vergütungsabrechnung 2024`` —
    keiner der 11 ``_PATTERNS``-Regex matcht (max_score == 0). Pos-Labels
    bleiben, damit das LLM den Beleg klassifizieren kann.

    Im expected-Dict wird ``_meta.test_classifier_fallback = True`` gesetzt
    und ``_meta.expected_belegtyp = "lohnausweis"`` für den
    classifier-Fallback-Test.
    """
    employer = "ACME AG, 8000 Zürich"
    period_von = "01.01.2024"
    period_bis = "31.12.2024"

    pos1 = f"90{APO}000.00"
    pos21 = f"2{APO}400.00"
    pos3 = f"3{APO}000.00"
    bruttolohn = f"95{APO}400.00"
    ahv = f"5{APO}056.20"
    bvg = f"10{APO}843.80"
    netto = f"79{APO}500.00"

    positions = [
        Position("Pos 1 Lohn", pos1),
        Position("Pos 2.1 Gehaltsnebenleistungen", pos21),
        Position("Pos 3 Unregelmässige Leistungen", pos3),
        Position("Pos 8 Bruttolohn total", bruttolohn),
        Position("Pos 9 AHV/IV/EO/ALV/NBUV-Abzug", ahv),
        Position("Pos 10.1 Berufliche Vorsorge (BVG)", bvg),
        Position("Pos 11 Nettolohn", netto),
    ]

    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    c = _new_canvas(out_pdf)
    _draw_lohnausweis_headerless(
        c,
        font_name="Helvetica",
        employer=employer,
        period_von=period_von,
        period_bis=period_bis,
        positions=positions,
    )
    c.showPage()
    c.save()

    expected: dict[str, Any] = {
        "arbeitgeber": {"value": employer, "anchor_present": True},
        "periode_von": {"value": period_von, "anchor_present": True},
        "periode_bis": {"value": period_bis, "anchor_present": True},
        "bruttolohn_pos8": {"value": bruttolohn, "anchor_present": True},
        "ahv_alv_nbu_abzug_pos9": {"value": ahv, "anchor_present": True},
        "bvg_abzug_pos10a": {"value": bvg, "anchor_present": True},
        "nettolohn_pos11": {"value": netto, "anchor_present": True},
        "quellensteuer_pos12": None,
        "_meta": {
            "test_classifier_fallback": True,
            "expected_belegtyp": "lohnausweis",
            "note": (
                "Plan 03-06 LLM-Fallback-Fixture — Header 'Lohnausweis'/'Form 11' "
                "bewusst entfernt; max_score der 11 Header-Regex == 0."
            ),
        },
    }
    _write_expected(
        expected, out_expected, fixture="lohnausweis_no_header", belegtyp="lohnausweis"
    )
    return expected


def generate_all() -> dict[str, Path]:
    """Generiert alle Phase-1- und Phase-2-Fixtures und liefert die Pfade zurück."""
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    EXPECTED_DIR.mkdir(parents=True, exist_ok=True)

    targets = [
        # Phase 1 — Lohnausweis (unverändert)
        (
            "lohnausweis_standard",
            FIXTURES_DIR / "lohnausweis_standard.pdf",
            EXPECTED_DIR / "lohnausweis_standard.json",
            generate_f1_standard,
        ),
        (
            "lohnausweis_quellensteuer",
            FIXTURES_DIR / "lohnausweis_quellensteuer.pdf",
            EXPECTED_DIR / "lohnausweis_quellensteuer.json",
            generate_f2_quellensteuer,
        ),
        (
            "lohnausweis_custom_font",
            FIXTURES_DIR / "lohnausweis_custom_font.pdf",
            EXPECTED_DIR / "lohnausweis_custom_font.json",
            generate_f3_custom_font,
        ),
        # Phase 2 — Bank-Zinsausweis (Plan 02-03)
        (
            "bank_zinsausweis_standard",
            FIXTURES_DIR / "bank_zinsausweis_standard.pdf",
            EXPECTED_DIR / "bank_zinsausweis_standard.json",
            generate_bank_standard,
        ),
        (
            "bank_zinsausweis_zkb",
            FIXTURES_DIR / "bank_zinsausweis_zkb.pdf",
            EXPECTED_DIR / "bank_zinsausweis_zkb.json",
            generate_bank_zkb,
        ),
        (
            "bank_zinsausweis_custom_font",
            FIXTURES_DIR / "bank_zinsausweis_custom_font.pdf",
            EXPECTED_DIR / "bank_zinsausweis_custom_font.json",
            generate_bank_custom_font,
        ),
        # Phase 2 — KK-Prämienbescheinigung
        (
            "kk_praemienbescheinigung_standard",
            FIXTURES_DIR / "kk_praemienbescheinigung_standard.pdf",
            EXPECTED_DIR / "kk_praemienbescheinigung_standard.json",
            generate_kk_standard,
        ),
        (
            "kk_praemienbescheinigung_helsana",
            FIXTURES_DIR / "kk_praemienbescheinigung_helsana.pdf",
            EXPECTED_DIR / "kk_praemienbescheinigung_helsana.json",
            generate_kk_helsana,
        ),
        # Phase 2 — Säule 3a
        (
            "saeule_3a_standard",
            FIXTURES_DIR / "saeule_3a_standard.pdf",
            EXPECTED_DIR / "saeule_3a_standard.json",
            generate_saeule_3a_standard,
        ),
        (
            "saeule_3a_viac",
            FIXTURES_DIR / "saeule_3a_viac.pdf",
            EXPECTED_DIR / "saeule_3a_viac.json",
            generate_saeule_3a_viac,
        ),
        # Phase 3 Wave 3 (Plan 03-04) — Wertschriften
        (
            "wertschriftenverzeichnis_standard",
            FIXTURES_DIR / "wertschriftenverzeichnis_standard.pdf",
            EXPECTED_DIR / "wertschriftenverzeichnis_standard.json",
            generate_wertschriften_standard,
        ),
        (
            "wertschriftenverzeichnis_joint",
            FIXTURES_DIR / "wertschriftenverzeichnis_joint.pdf",
            EXPECTED_DIR / "wertschriftenverzeichnis_joint.json",
            generate_wertschriften_joint,
        ),
        # Phase 3 Wave 3 — Spenden (inkl. L2 Custom-Font)
        (
            "spenden_standard",
            FIXTURES_DIR / "spenden_standard.pdf",
            EXPECTED_DIR / "spenden_standard.json",
            generate_spenden_standard,
        ),
        (
            "spenden_custom_font",
            FIXTURES_DIR / "spenden_custom_font.pdf",
            EXPECTED_DIR / "spenden_custom_font.json",
            generate_spenden_custom_font,
        ),
        # Phase 3 Wave 3 — Berufsauslagen
        (
            "berufsauslagen_standard",
            FIXTURES_DIR / "berufsauslagen_standard.pdf",
            EXPECTED_DIR / "berufsauslagen_standard.json",
            generate_berufsauslagen_standard,
        ),
        (
            "berufsauslagen_hsg",
            FIXTURES_DIR / "berufsauslagen_hsg.pdf",
            EXPECTED_DIR / "berufsauslagen_hsg.json",
            generate_berufsauslagen_hsg,
        ),
        # Phase 3 Wave 4 (Plan 03-05) — Kinderbetreuung
        (
            "kinderbetreuung_standard",
            FIXTURES_DIR / "kinderbetreuung_standard.pdf",
            EXPECTED_DIR / "kinderbetreuung_standard.json",
            generate_kinderbetreuung_standard,
        ),
        (
            "kinderbetreuung_edge_cap",
            FIXTURES_DIR / "kinderbetreuung_edge_cap.pdf",
            EXPECTED_DIR / "kinderbetreuung_edge_cap.json",
            generate_kinderbetreuung_edge_cap,
        ),
        # Phase 3 Wave 4 — Hypothek
        (
            "hypothek_zinsbestaetigung_standard",
            FIXTURES_DIR / "hypothek_zinsbestaetigung_standard.pdf",
            EXPECTED_DIR / "hypothek_zinsbestaetigung_standard.json",
            generate_hypothek_standard,
        ),
        (
            "hypothek_zinsbestaetigung_raiffeisen",
            FIXTURES_DIR / "hypothek_zinsbestaetigung_raiffeisen.pdf",
            EXPECTED_DIR / "hypothek_zinsbestaetigung_raiffeisen.json",
            generate_hypothek_raiffeisen,
        ),
        # Phase 3 Wave 4 — Liegenschaftsunterhalt
        (
            "liegenschaftsunterhalt_reparatur",
            FIXTURES_DIR / "liegenschaftsunterhalt_reparatur.pdf",
            EXPECTED_DIR / "liegenschaftsunterhalt_reparatur.json",
            generate_liegenschaftsunterhalt_reparatur,
        ),
        (
            "liegenschaftsunterhalt_anbau",
            FIXTURES_DIR / "liegenschaftsunterhalt_anbau.pdf",
            EXPECTED_DIR / "liegenschaftsunterhalt_anbau.json",
            generate_liegenschaftsunterhalt_anbau,
        ),
        # Phase 3 Wave 4 — Krankheitskosten
        (
            "krankheitskosten_arzt",
            FIXTURES_DIR / "krankheitskosten_arzt.pdf",
            EXPECTED_DIR / "krankheitskosten_arzt.json",
            generate_krankheitskosten_arzt,
        ),
        (
            "krankheitskosten_apotheke",
            FIXTURES_DIR / "krankheitskosten_apotheke.pdf",
            EXPECTED_DIR / "krankheitskosten_apotheke.json",
            generate_krankheitskosten_apotheke,
        ),
        # Phase 3 Wave 5 (Plan 03-06) — LLM-Fallback-Klassifikator
        (
            "lohnausweis_no_header",
            FIXTURES_DIR / "lohnausweis_no_header.pdf",
            EXPECTED_DIR / "lohnausweis_no_header.json",
            generate_lohnausweis_no_header,
        ),
    ]

    out_paths: dict[str, Path] = {}
    for name, pdf_path, expected_path, fn in targets:
        fn(pdf_path, expected_path)
        out_paths[name] = pdf_path
        print(f"[generate] {name}: PDF={pdf_path.name}  expected={expected_path.name}")
    return out_paths


def main() -> None:
    paths = generate_all()
    print(f"Generated {len(paths)} fixtures in {FIXTURES_DIR}")


if __name__ == "__main__":
    main()
