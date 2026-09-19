"""Steuer-Zielmodell — Single Source of Truth für die Familie Muster, ZH 2024.

Maschinenlesbares Pendant zu ``STEUER-ZIELMODELL.md``. Wird von
allen Konsumenten (``build_table.py``, ``eval_anchors.py``, künftig auch
``aussteller.py`` und Plausibilitäts-Regeln) gemeinsam genutzt — damit das
Markdown-Dokument und der Code nicht auseinanderlaufen (Codex' #5).

Pro Belegtyp:

* ``aussteller_field`` — welches Schema-Feld den Aussteller benennt (Pflichtfeld
  für Vorjahresvergleich in ``aussteller.json``).
* ``person_field`` — welches Schema-Feld den Personen-Bezug trägt (für
  ``family.yaml``-Match in der Tabelle).
* ``datum_field`` — Jahr/Periode-Feld.
* ``mandatory_fields`` — 🟢 Felder, die das Tool extrahieren MUSS.
* ``conditional_fields`` — 🟡 Felder, die nur unter Bedingung erscheinen
  (z.B. ``bvg_abzug_pos10a`` nur wenn ``bruttolohn_pos8 ≥ 22050``).
* ``plausibility_rules`` — Pflicht-Identitäten + Range-Checks.
* ``zhprivatetax_ziffer`` — Pointer in die Steuererklärung.

Konvention "by-design" (🟡): das Feld ist NICHT extrahierbar wenn die im
``when`` ausgedrückte Bedingung NICHT erfüllt ist — kein Eval-Fail.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Literal


PflichtStatus = Literal["mandatory", "conditional", "optional"]


@dataclass(frozen=True)
class FieldSpec:
    """Spezifikation eines Schema-Felds im Zielmodell."""
    name: str
    status: PflichtStatus
    # Beschreibung, wo das Feld in der ZHprivateTax-Maske landet.
    zhprivatetax_label: str = ""
    # Für conditional fields: textuelle Beschreibung der Bedingung
    # (zur Anzeige; maschinelle Auswertung erfolgt in plausibility_rules).
    when: str = ""


@dataclass(frozen=True)
class BelegtypSpec:
    """Vollständige Spezifikation eines Belegtyps."""
    belegtyp: str
    display_name: str
    zhprivatetax_ziffer: str            # z.B. "1.1/1.2", "4 + 30", "5.4"
    aussteller_field: str | None
    person_field: str | None
    datum_field: str | None             # typisch "jahr" oder "periode_bis"
    waehrung: str | None                # "CHF" oder None
    fields: tuple[FieldSpec, ...]
    # Plausibilitätsregeln als Callable: (extracted_values_dict) -> list[str]-Errors
    # Konvention: gibt leere Liste zurück bei OK, sonst eine Liste mit Fail-Begründungen
    plausibility_rules: tuple[Callable[[dict[str, str]], list[str]], ...] = ()
    # Rules, die arithmetische Konsistenz prüfen (Nettolohn=Pos8-Pos9-Pos10,
    # VRS=35%×Brutto). Werden bei anonymisierten Samples übersprungen
    # (Codex' #4 — Anonymisierungs-Werte sind nicht arithmetisch konsistent).
    arithmetic_rules: tuple[Callable[[dict[str, str]], list[str]], ...] = ()

    def mandatory_field_names(self) -> tuple[str, ...]:
        return tuple(f.name for f in self.fields if f.status == "mandatory")

    def conditional_field_names(self) -> tuple[str, ...]:
        return tuple(f.name for f in self.fields if f.status == "conditional")

    def all_field_names(self) -> tuple[str, ...]:
        return tuple(f.name for f in self.fields)


# ---------------------------------------------------------------------------
# Plausibilitäts-Helpers — feldspezifische Closure-Generatoren
# ---------------------------------------------------------------------------


def _parse_amount(s: str) -> float | None:
    """Schweizer Apostroph-Komma in Decimal-Float; None bei Misslingen."""
    if s is None or s == "" or s == "null":
        return None
    try:
        return float(s.replace("'", "").replace(",", "."))
    except (ValueError, TypeError):
        return None


def rule_nettolohn_identity(values: dict[str, str]) -> list[str]:
    """Pos11 = Pos8 − Pos9 − Pos10 ± CHF 5."""
    brutto = _parse_amount(values.get("bruttolohn_pos8", ""))
    abzug9 = _parse_amount(values.get("ahv_alv_nbu_abzug_pos9", ""))
    abzug10 = _parse_amount(values.get("bvg_abzug_pos10a", "")) or 0.0
    netto = _parse_amount(values.get("nettolohn_pos11", ""))
    if None in (brutto, abzug9, netto):
        return []  # Felder nicht alle vorhanden — andere Regeln greifen
    expected = brutto - abzug9 - abzug10
    if abs(netto - expected) > 5:
        return [
            f"Nettolohn-Identität verletzt: Pos11={netto:.2f}, "
            f"Pos8−Pos9−Pos10={expected:.2f} (Δ={netto-expected:+.2f}, "
            f"toleriert ±5)"
        ]
    return []


def rule_bvg_pflicht_oberhalb_schwelle(values: dict[str, str]) -> list[str]:
    """BVG-Pflichtfeld wenn Bruttolohn ≥ 22'050 CHF (2024-Schwelle)."""
    brutto = _parse_amount(values.get("bruttolohn_pos8", ""))
    bvg = _parse_amount(values.get("bvg_abzug_pos10a", ""))
    if brutto is None:
        return []
    if brutto >= 22050 and (bvg is None or bvg == 0):
        return [
            f"BVG-Pflicht: Bruttolohn {brutto:.2f} ≥ 22'050 CHF, aber Pos 10.1 "
            f"= {bvg or 'nicht extrahiert'} — Eintrittsschwelle nicht eingehalten"
        ]
    return []


def rule_verrechnungssteuer_plausibel(values: dict[str, str]) -> list[str]:
    """VRS ≈ 35% × Bruttoertrag, oder legitim 0 wenn Brutto ≤ 200 CHF.

    ``manual_review:vrs_not_reported`` wird als "acknowledged" gewertet — kein
    Plausibilitäts-Fail. Der User prüft manuell ob VRS tatsächlich nicht
    ausgewiesen ist (Broker-/Auslands-Wertschriften, Beleg ohne VRS-Ausweis).
    """
    brutto = _parse_amount(values.get("bruttoertrag", ""))
    vrs_raw = values.get("verrechnungssteuer", "") or ""
    vrs = _parse_amount(vrs_raw)
    if brutto is None:
        return []
    if brutto <= 200:
        return []  # Schwelle, VRS kann legitim 0 / nicht ausgewiesen sein
    # manual_review:* = bewusst für menschliche Prüfung markiert → kein Fail
    if vrs_raw.startswith("manual_review:") or vrs_raw == "not_in_beleg_by_design":
        return []
    if vrs is None:
        return [f"VRS-Pflicht: Bruttoertrag {brutto:.2f} > 200 CHF, aber VRS fehlt"]
    expected = brutto * 0.35
    if abs(vrs - expected) > 1:
        return [
            f"VRS-Plausibilität: erwartet ≈{expected:.2f} (35% × {brutto:.2f}), "
            f"extrahiert {vrs:.2f} (Δ={vrs-expected:+.2f}, toleriert ±1)"
        ]
    return []


def rule_zins_zu_saldo_plausibel(values: dict[str, str]) -> list[str]:
    """Ein Zinsertrag kann nie ein grosser Anteil des Saldos sein.

    Aufgedeckt an einem echten Beleg (260904-rmx): pdfplumber hatte den Saldo
    an einer Kerning-Lücke zerteilt, sodass die führende Ziffer fehlte —
    gemeldet wurden 71.88 statt 371.88. Der Wert war wortwörtlich im Dokument
    auffindbar, also verankert und grün. Aufgefallen ist er nur, weil ein Zins
    von 23.79 auf einem Saldo von 71.88 einem Zinssatz von 33 % entspräche.

    Schwelle 15 %: deutlich über jedem realistischen Sparzins, aber tief genug,
    um eine fehlende Ziffer zu fangen.

    Die Untergrenze liegt beim ZINS, nicht beim Saldo: ein Zins unter 5 CHF ist
    steuerlich unerheblich und sein Verhältnis rauschanfällig. Eine Untergrenze
    am Saldo hätte genau den Fall ausgeschlossen, den die Regel fangen soll —
    der fehlerhafte Saldo war ja gerade zu klein.
    """
    saldo = _parse_amount(values.get("vermoegensstand_3112", ""))
    zins = _parse_amount(values.get("bruttoertrag", ""))
    if saldo is None or zins is None or saldo <= 20 or zins < 5:
        return []
    anteil = zins / saldo
    if anteil > 0.15:
        return [
            f"Zins-zu-Saldo: Zins {zins:.2f} auf Saldo {saldo:.2f} entspricht "
            f"{anteil * 100:.0f}% — unrealistisch. Fehlt dem Saldo eine Ziffer?"
        ]
    return []


def rule_wsv_vrs_plausibel(values: dict[str, str]) -> list[str]:
    """WSV-Pendant: VRS_total ≈ 35% × Bruttoertrag_total bei Vollauszügen.

    Reine Bestandsausweise (Portfolio-Wertentwicklung) und Einzeltransaktions-
    Belege (Corporate-Action) haben kein bruttoertrag_total → Regel nicht
    anwendbar; gibt leere Liste zurück.

    ``manual_review:vrs_not_reported`` wird als "acknowledged" gewertet.
    """
    brutto = _parse_amount(values.get("bruttoertrag_total", ""))
    vrs_raw = values.get("verrechnungssteuer_total", "") or ""
    vrs = _parse_amount(vrs_raw)
    if brutto is None:
        return []
    if brutto <= 200:
        return []
    # manual_review:* = bewusst für menschliche Prüfung markiert → kein Fail
    if vrs_raw.startswith("manual_review:") or vrs_raw == "not_in_beleg_by_design":
        return []
    if vrs is None:
        return [
            f"WSV-VRS-Pflicht: Bruttoertrag_total {brutto:.2f} > 200 CHF, "
            f"aber VRS_total fehlt"
        ]
    expected = brutto * 0.35
    # Wertschriften haben oft Mix-Quellen (CH + Ausland) — VRS gilt nur für CH-Anteil.
    # Toleranz hier auf 10% statt strikte ±1, weil wir Ausland-Anteil nicht kennen.
    if vrs < expected * 0.1:  # weniger als 10% des erwarteten Maximum → verdächtig
        return [
            f"WSV-VRS-Plausibilität: erwartet bis ≈{expected:.2f} (35% × "
            f"{brutto:.2f}), extrahiert nur {vrs:.2f} — CH-Quellen-Anteil "
            f"prüfen oder Extraktion mangelhaft"
        ]
    return []


def rule_saeule_3a_cap(values: dict[str, str]) -> list[str]:
    """0 < Einzahlung ≤ 35'280 (2024, ohne PK).

    Für Eltern mit PK: Cap 7'056 — aber das Tool weiss nicht ob PK
    vorhanden ist, also nur den absoluten Maximalwert prüfen.
    """
    betrag = _parse_amount(values.get("einzahlung_betrag", ""))
    if betrag is None:
        return []
    if betrag <= 0:
        return [f"Säule 3a: Einzahlung ≤ 0 ({betrag}) — implausibel"]
    if betrag > 35280:
        return [
            f"Säule 3a: Einzahlung {betrag:.2f} > Cap 35'280 — über Maximum"
        ]
    return []


def rule_jahr_plausibel(values: dict[str, str]) -> list[str]:
    """Jahr muss 4-stellig und im Steuerzeitraum [2018, 2030] liegen."""
    jahr = values.get("jahr", "")
    if not jahr or jahr == "null":
        return []
    if not jahr.isdigit() or len(jahr) != 4:
        return [f"Jahr-Format: '{jahr}' ist kein 4-stelliges Jahr"]
    y = int(jahr)
    if y < 2018 or y > 2030:
        return [f"Jahr-Range: '{jahr}' ausserhalb [2018, 2030]"]
    return []


# Placeholder-Werte, die das LLM bei Unsicherheit emittiert. Pflichtfelder
# mit einem dieser Werte zählen NICHT als erfüllt (Codex' #2).
_PLACEHOLDER_VALUES: frozenset[str] = frozenset({
    "", "null", "—", "nicht angegeben", "nicht vorhanden", "unbekannt",
    "n/a", "na", "none", "keine angabe", "kein eintrag",
})


def is_placeholder(value: str | None) -> bool:
    """True wenn Wert ein LLM-Platzhalter ist (statt echter Extraktion)."""
    if value is None:
        return True
    return value.strip().lower() in _PLACEHOLDER_VALUES


# Datums-Format DD.MM.YYYY (Lohnausweis-Periode etc.)
_DATE_PATTERN = re.compile(r"^(\d{2})\.(\d{2})\.(\d{4})$")


def rule_datum_plausibel(values: dict[str, str], field_name: str) -> list[str]:
    """Datum im Format DD.MM.YYYY, Jahr in [2018, 2030].

    Im Unterschied zu ``rule_jahr_plausibel`` für ``periode_von``/
    ``periode_bis`` etc. — vorher fälschlich mit Jahr-Regel geprüft
    (Codex' #3).
    """
    value = values.get(field_name, "")
    if is_placeholder(value):
        return []
    m = _DATE_PATTERN.match(value.strip())
    if not m:
        return [f"Datum-Format: '{value}' ist kein DD.MM.YYYY ({field_name})"]
    day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if not (1 <= day <= 31) or not (1 <= month <= 12):
        return [f"Datum-Wert: '{value}' hat ungültigen Tag/Monat ({field_name})"]
    if year < 2018 or year > 2030:
        return [f"Datum-Range: Jahr {year} in '{value}' ausserhalb [2018, 2030] ({field_name})"]
    return []


def rule_lohnausweis_perioden(values: dict[str, str]) -> list[str]:
    """periode_von und periode_bis müssen valide Daten sein."""
    errors = []
    errors.extend(rule_datum_plausibel(values, "periode_von"))
    errors.extend(rule_datum_plausibel(values, "periode_bis"))
    return errors


# Label-Keywords, die belegen, dass ein extrahierter VRS-Wert wirklich aus der
# Verrechnungssteuer-Spalte stammt (Findings E2/E4). Steht keines davon im
# Anker-Snippet, wurde der Wert vermutlich aus einer Ertragsspalte gegriffen.
_VRS_LABEL_KEYWORDS: tuple[str, ...] = (
    "verrechnungssteuer",
    "vst",
    "rubrik a",
    "verrechnungssteueranspruch",
)


def _snippet_has_vrs_label(snippet: str) -> bool:
    """True wenn der Snippet ein VRS-Label-Keyword in der Nähe enthält."""
    if not snippet:
        return False
    low = snippet.lower()
    return any(kw in low for kw in _VRS_LABEL_KEYWORDS)


def rule_verrechnungssteuer_label_pflicht(values: dict[str, str]) -> list[str]:
    """VRS nur akzeptieren, wenn der Anker-Snippet Label-Nähe hat (E2/E4).

    Liest den VRS-Wert (``verrechnungssteuer`` ODER ``verrechnungssteuer_total``)
    und den zugehörigen Snippet (``verrechnungssteuer_snippet`` bzw.
    ``verrechnungssteuer_total_snippet``) aus dem values-Dict. Hat der Wert
    keinen VRS-Label in der Nähe, liegt der Verdacht nahe, dass er aus einer
    Ertragsspalte ("Ertrag Rubrik B 48.90") gegriffen wurde → Befund, der den
    Aufrufer veranlasst, das Feld auf manual_review/null zu setzen.

    ``manual_review:*``/``not_in_beleg_by_design``/leere Werte sind kein Befund.
    """
    errors: list[str] = []
    for vfield, sfield in (
        ("verrechnungssteuer", "verrechnungssteuer_snippet"),
        ("verrechnungssteuer_total", "verrechnungssteuer_total_snippet"),
    ):
        vrs_raw = (values.get(vfield, "") or "").strip()
        if not vrs_raw:
            continue
        if vrs_raw.startswith("manual_review:") or vrs_raw == "not_in_beleg_by_design":
            continue
        if _parse_amount(vrs_raw) is None:
            continue
        snippet = values.get(sfield, "") or ""
        if not _snippet_has_vrs_label(snippet):
            errors.append(
                f"VRS-Label fehlt: '{vrs_raw}' ({vfield}) ohne "
                f"Verrechnungssteuer-Label im Anker → Ertragsspalte? "
                f"manual_review/null"
            )
    return errors


def rule_lohnausweis_vertauschung(values: dict[str, str]) -> list[str]:
    """Lohnausweis-Feldvertauschungen erkennen (Finding E3, Checks a-c+e).

    (a) BVG == Nettolohn → beide verdächtig (Spaltenvertauschung).
    (b) BVG > 0.25 × Bruttolohn → BVG-Wert sitzt vermutlich in der Netto-/
        AHV-Spalte.
    (c) AHV/ALV/NBU > 0.15 × Bruttolohn → Abzug unplausibel hoch.
    (e) Quersumme Brutto − AHV − BVG ≈ Netto (±5%) als Plausi-Signal; grobe
        Abweichung deutet auf vertauschte Felder.

    Person-Name-im-Arbeitgeber-Check (d) lebt im Aufrufer
    (process_samples_full), weil er family.yaml-Namen zur Laufzeit braucht.
    """
    errors: list[str] = []
    brutto = _parse_amount(values.get("bruttolohn_pos8", ""))
    ahv = _parse_amount(values.get("ahv_alv_nbu_abzug_pos9", ""))
    bvg = _parse_amount(values.get("bvg_abzug_pos10a", ""))
    netto = _parse_amount(values.get("nettolohn_pos11", ""))

    # (a) BVG == Netto → Spaltenvertauschung.
    if bvg is not None and netto is not None and bvg > 0 and abs(bvg - netto) < 0.01:
        errors.append(
            "Lohnausweis-Vertauschung: BVG (Pos 10a) == Nettolohn (Pos 11) — "
            "Spalten vermutlich vertauscht (manual_review)"
        )

    # (b) BVG > 25% Brutto.
    if brutto is not None and bvg is not None and brutto > 0 and bvg > 0.25 * brutto:
        errors.append(
            f"Lohnausweis-Vertauschung: BVG {bvg:.2f} > 25% des Bruttolohns "
            f"{brutto:.2f} — Pos 10a vermutlich falsche Spalte (manual_review)"
        )

    # (c) AHV/ALV/NBU > 15% Brutto.
    if brutto is not None and ahv is not None and brutto > 0 and ahv > 0.15 * brutto:
        errors.append(
            f"Lohnausweis-Vertauschung: AHV/ALV/NBU {ahv:.2f} > 15% des "
            f"Bruttolohns {brutto:.2f} — Pos 9 unplausibel hoch (manual_review)"
        )

    # (e) Quersumme Brutto − AHV − BVG ≈ Netto (±5%).
    if None not in (brutto, ahv, netto) and brutto > 0:
        bvg_v = bvg or 0.0
        expected = brutto - ahv - bvg_v
        if abs(netto - expected) > max(5.0, 0.05 * brutto):
            errors.append(
                f"Lohnausweis-Quersumme: Brutto−AHV−BVG={expected:.2f} weicht "
                f">5% von Nettolohn {netto:.2f} ab — Felder prüfen (manual_review)"
            )
    return errors


# Bedarf re-Import, da im File jetzt regex genutzt wird (oben schon importiert)


# ---------------------------------------------------------------------------
# Belegtyp-Specs
# ---------------------------------------------------------------------------

LOHNAUSWEIS = BelegtypSpec(
    belegtyp="lohnausweis",
    display_name="Lohnausweis (ESTV Form. 11)",
    zhprivatetax_ziffer="1.1 / 1.2",
    aussteller_field="arbeitgeber",
    person_field="arbeitnehmer_name",
    datum_field="periode_bis",
    waehrung="CHF",
    fields=(
        FieldSpec("arbeitgeber", "mandatory", "Arbeitgeber + Adresse"),
        FieldSpec("periode_von", "mandatory", "Periode von (DD.MM.YYYY)"),
        FieldSpec("periode_bis", "mandatory", "Periode bis (DD.MM.YYYY)"),
        FieldSpec("bruttolohn_pos8", "mandatory", "Bruttolohn (Pos. 8)"),
        FieldSpec("ahv_alv_nbu_abzug_pos9", "mandatory", "AHV/IV/EO/ALV/NBUV (Pos. 9)"),
        FieldSpec("bvg_abzug_pos10a", "conditional",
                  "Ord. Beiträge berufl. Vorsorge (Pos. 10.1)",
                  when="Bruttolohn ≥ 22'050 CHF"),
        FieldSpec("nettolohn_pos11", "mandatory", "Nettolohn (Pos. 11)"),
        FieldSpec("quellensteuer_pos12", "conditional",
                  "Quellensteuer-Abzug (Pos. 12)",
                  when="bei QST-Pflicht"),
    ),
    plausibility_rules=(
        rule_bvg_pflicht_oberhalb_schwelle,
        rule_lohnausweis_perioden,
        rule_lohnausweis_vertauschung,
    ),
    arithmetic_rules=(rule_nettolohn_identity,),
)

BANK_ZINSAUSWEIS = BelegtypSpec(
    belegtyp="bank_zinsausweis",
    display_name="Bank Zinsausweis",
    zhprivatetax_ziffer="4 + 30 (WSV)",
    aussteller_field="institut",
    person_field="kontoinhaber_name",
    datum_field="jahr",
    waehrung="CHF",
    fields=(
        FieldSpec("institut", "mandatory", "Institut"),
        FieldSpec("kontoinhaber_name", "mandatory", "Eigentümer"),
        FieldSpec("jahr", "mandatory", "Steuerjahr"),
        # Bruttoertrag conditional: eine reine Kapital-/Saldobescheinigung
        # (Bankguthaben ohne Zins) hat legitim keinen Bruttozins. DoD 3:
        # "Wenn kein Bruttozins-/Ertragslabel sichtbar ist, keinen Ertrag
        # halluzinieren." → Feld darf fehlen, wenn kein Ertrags-Label im Beleg.
        FieldSpec("bruttoertrag", "conditional", "Bruttoertrag (Zinsen)",
                  when="nur wenn Bruttozins-/Ertragslabel im Beleg sichtbar"),
        FieldSpec("vermoegensstand_3112", "mandatory", "Vermögensstand 31.12."),
        FieldSpec("verrechnungssteuer", "conditional", "Verrechnungssteuer 35%",
                  when="Bruttoertrag > 200 CHF; sonst legitim 0/leer"),
        FieldSpec("konto_nr_redacted", "optional", "Konto-Nr (redacted)"),
        FieldSpec("kontotyp", "optional", "Kontotyp"),
    ),
    plausibility_rules=(rule_jahr_plausibel, rule_verrechnungssteuer_label_pflicht,
                        rule_zins_zu_saldo_plausibel),
    arithmetic_rules=(rule_verrechnungssteuer_plausibel,),
)

KK_PRAEMIEN = BelegtypSpec(
    belegtyp="kk_praemienbescheinigung",
    display_name="Krankenkasse Prämienbescheinigung",
    zhprivatetax_ziffer="5.4",
    aussteller_field="kasse",
    person_field="versicherte_person_name",
    datum_field="jahr",
    waehrung="CHF",
    fields=(
        FieldSpec("kasse", "mandatory", "Kasse"),
        FieldSpec("versicherte_person_name", "mandatory", "Person"),
        FieldSpec("jahr", "mandatory", "Steuerjahr"),
        FieldSpec("praemie_kvg_total", "conditional", "Prämie KVG (Grundversicherung)",
                  when="bei Hauptbescheinigung mit KVG-Inhalt; nicht bei reinen "
                       "VVG-Zusatzbescheinigungen (KK-S Privatversicherung etc.)"),
        FieldSpec("praemie_vvg_total", "conditional", "Prämie VVG (Zusatzversicherung)",
                  when="nur wenn Zusatzversicherung abgeschlossen"),
    ),
    plausibility_rules=(rule_jahr_plausibel,),
)

SAEULE_3A = BelegtypSpec(
    belegtyp="saeule_3a",
    display_name="Säule 3a Einzahlung",
    zhprivatetax_ziffer="13.1",
    aussteller_field="stiftung",
    person_field="kontoinhaber_name",
    datum_field="jahr",
    waehrung="CHF",
    fields=(
        FieldSpec("stiftung", "mandatory", "Stiftung / Bank"),
        FieldSpec("kontoinhaber_name", "mandatory", "Person"),
        FieldSpec("jahr", "mandatory", "Steuerjahr"),
        FieldSpec("einzahlung_betrag", "mandatory", "Einzahlung CHF"),
        FieldSpec("kontonummer_redacted", "optional", "Konto-Nr"),
    ),
    plausibility_rules=(rule_jahr_plausibel, rule_saeule_3a_cap),
)

WERTSCHRIFTEN = BelegtypSpec(
    belegtyp="wertschriftenverzeichnis",
    display_name="Wertschriften / Depot",
    zhprivatetax_ziffer="4 + 30 (WSV)",
    aussteller_field="institut",
    person_field="kontoinhaber_name",
    datum_field="jahr",
    waehrung="CHF",
    fields=(
        FieldSpec("institut", "mandatory", "Institut"),
        FieldSpec("kontoinhaber_name", "mandatory", "Eigentümer"),
        FieldSpec("jahr", "mandatory", "Steuerjahr"),
        # bestand_3112: bei Vollauszügen + Bestandsausweisen Pflicht, bei
        # Einzeltransaktions-Belegen (Corporate-Action) legitim leer.
        FieldSpec("bestand_3112", "conditional", "Vermögensstand 31.12.",
                  when="nur bei Vollauszug/Bestandsausweis, nicht bei "
                       "Einzeltransaktion (Corporate-Action)"),
        # bruttoertrag_total: bei Ertragsausweisen Pflicht, bei reinen
        # Bestandsausweisen (Portfolio-Wertentwicklung) legitim leer.
        FieldSpec("bruttoertrag_total", "conditional", "Bruttoertrag (Wertschriften)",
                  when="nur bei Ertragsausweis, nicht bei reinem Bestandsausweis"),
        FieldSpec("verrechnungssteuer_total", "conditional", "VRS 35%",
                  when="bei CH-Quellen-Ertrag > 200 CHF"),
        FieldSpec("depot_nr_redacted", "optional", "Depot-Nr"),
        FieldSpec("waehrung", "optional", "Währung"),
    ),
    plausibility_rules=(rule_jahr_plausibel, rule_verrechnungssteuer_label_pflicht),
    arithmetic_rules=(rule_wsv_vrs_plausibel,),
)

KINDERBETREUUNG = BelegtypSpec(
    belegtyp="kinderbetreuung",
    display_name="Kinderbetreuung",
    zhprivatetax_ziffer="11",
    aussteller_field="anbieter",
    person_field="kind_name",
    datum_field="jahr",
    waehrung="CHF",
    fields=(
        FieldSpec("anbieter", "mandatory", "Anbieter (Kita/Hort/...)"),
        FieldSpec("kind_name", "mandatory", "Kind"),
        FieldSpec("jahr", "mandatory", "Steuerjahr"),
        FieldSpec("betrag", "mandatory", "Betrag CHF"),
        FieldSpec("betreuungs_typ", "optional", "Betreuungstyp"),
    ),
    plausibility_rules=(rule_jahr_plausibel,),
)

KRANKHEITSKOSTEN = BelegtypSpec(
    belegtyp="krankheitskosten",
    display_name="Krankheitskosten (Selbstkosten)",
    zhprivatetax_ziffer="12.1",
    aussteller_field="leistungserbringer",
    person_field="person_name",
    datum_field="jahr",
    waehrung="CHF",
    fields=(
        FieldSpec("leistungserbringer", "mandatory", "Leistungserbringer"),
        FieldSpec("person_name", "mandatory", "Person"),
        FieldSpec("jahr", "mandatory", "Steuerjahr"),
        FieldSpec("betrag", "mandatory", "Selbstgetragene Kosten total"),
        FieldSpec("kategorie", "optional", "Kategorie"),
    ),
    plausibility_rules=(rule_jahr_plausibel,),
)

HYPOTHEK = BelegtypSpec(
    belegtyp="hypothek_zinsbestaetigung",
    display_name="Hypothek-Zinsbestätigung",
    zhprivatetax_ziffer="12 + 34",
    aussteller_field="institut",
    person_field="kontoinhaber_name",
    datum_field="jahr",
    waehrung="CHF",
    fields=(
        FieldSpec("institut", "mandatory", "Bank"),
        FieldSpec("kontoinhaber_name", "mandatory", "Person"),
        FieldSpec("liegenschaft", "mandatory", "Liegenschaft"),
        FieldSpec("jahr", "mandatory", "Steuerjahr"),
        FieldSpec("schuldzinsen", "mandatory", "Schuldzinsen CHF"),
        FieldSpec("schuldsaldo_3112", "mandatory", "Schuldsaldo 31.12. CHF"),
    ),
    plausibility_rules=(rule_jahr_plausibel,),
)

# Out-of-Scope-Belegtypen — bewusst NICHT in der Eingabe-Pipeline
# (Codex' Punkt 4). Hier nur zur Dokumentation.
#
# Die Hypothek stand hier, weil die Familie zur Miete wohnte. Sie tut es
# nicht mehr: drei Hypothekarbelege liegen im Ordner, und ohne Spec liefert
# der Belegtyp keinen einzigen Wert — Schuldzinsen sind ein Abzug, den man
# nicht vergessen will (260904-rmx).
LIEGENSCHAFTSUNTERHALT_OOS = "liegenschaftsunterhalt"        # Miete-Familie
BERUFSAUSLAGEN_OOS = "berufsauslagen"                        # Pauschalabzug
SPENDEN_OOS = "spenden"                                       # keine im Testset


# Master-Map
SPECS: dict[str, BelegtypSpec] = {
    spec.belegtyp: spec
    for spec in (
        LOHNAUSWEIS,
        BANK_ZINSAUSWEIS,
        KK_PRAEMIEN,
        SAEULE_3A,
        WERTSCHRIFTEN,
        KINDERBETREUUNG,
        KRANKHEITSKOSTEN,
        HYPOTHEK,
    )
}


# ---------------------------------------------------------------------------
# Konsumenten-API
# ---------------------------------------------------------------------------


def get_spec(belegtyp: str) -> BelegtypSpec | None:
    """Lookup; None für unbekannte oder out-of-scope Belegtypen."""
    return SPECS.get(belegtyp)


def is_in_scope(belegtyp: str) -> bool:
    """True wenn Belegtyp aktiv extrahiert wird (vs. Out-of-Scope)."""
    return belegtyp in SPECS


def relevant_fields(belegtyp: str) -> list[str]:
    """Felder, die in Tabelle/Eval als Pflicht-/Conditional-Felder gezählt werden."""
    spec = get_spec(belegtyp)
    if spec is None:
        return []
    return [
        f.name for f in spec.fields
        if f.status in ("mandatory", "conditional")
    ]


def mandatory_fields(belegtyp: str) -> list[str]:
    spec = get_spec(belegtyp)
    return list(spec.mandatory_field_names()) if spec else []


def run_plausibility(
    belegtyp: str,
    values: dict[str, str],
    *,
    skip_arithmetic: bool = False,
) -> list[str]:
    """Führt Plausibilitätsregeln aus. Gibt Liste mit Fail-Begründungen zurück
    (leer = alles OK).

    ``skip_arithmetic=True`` überspringt Regeln, die arithmetische Konsistenz
    zwischen Beträgen prüfen — relevant für anonymisierte Test-Samples, deren
    Werte zwar das echte Layout simulieren, aber keine konsistente Rechnung
    haben (Pos11 ≠ Pos8−Pos9−Pos10 im anonymisierten Datensatz; Codex' #4).
    """
    spec = get_spec(belegtyp)
    if spec is None:
        return []
    errors: list[str] = []
    for rule in spec.plausibility_rules:
        errors.extend(rule(values))
    if not skip_arithmetic:
        for rule in spec.arithmetic_rules:
            errors.extend(rule(values))
    return errors
