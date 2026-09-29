"""Layout-basierte VRS-Extraktion für Wertschriftenverzeichnisse.

Phase-F-Fix (F7): BROKER-S-Steueraufstellungen weisen VRS nicht als
labelte Zeile aus, sondern via Spalten 'A: Werte mit Verrechnungssteuerabzug'
und 'B: Werte ohne Verrechnungssteuerabzug'. VRS_total = 35% × Subtotal-Ertrag-A.

Returns (value_str, anchor_dict) oder None. Anchor enthält page/bbox des
Subtotal-Wertes für Nachweisbarkeit (Constraint #2 in CLAUDE.md).
"""
from __future__ import annotations

import re
from typing import Any

_AMOUNT_RE = re.compile(r"^\d+([',.]\d+)*$")


def _group_by_row(words: list[dict], target_top: float, tol: float = 3.0) -> list[dict]:
    """Wörter in derselben visuellen Zeile (Y-Toleranz ±tol)."""
    return [w for w in words if abs(w["top"] - target_top) <= tol]


def _parse_amount(s: str) -> float | None:
    """Parsed CH-Betrag (Tausendertrenner ', oder .)."""
    try:
        # Tausendertrenner ' und Punkte (wenn Komma als Dezimaltrenner) entfernen
        cleaned = s.replace("'", "")
        # Falls Komma als Dezimaltrenner verwendet, in Punkt umwandeln
        if "," in cleaned and "." not in cleaned:
            cleaned = cleaned.replace(",", ".")
        # Falls sowohl Komma als auch Punkt: Punkt = Tausendertrenner
        elif "," in cleaned and "." in cleaned:
            cleaned = cleaned.replace(".", "").replace(",", ".")
        return float(cleaned)
    except (ValueError, TypeError):
        return None


def extract_vrs_total_selma(
    words: list[dict],
) -> tuple[str, dict] | None:
    """VRS-Extraktion für BROKER-S-Layout: Subtotal-Ertrag-Spalte-A × 35%.

    Detektion: gibt es eine Zeile mit 'Werte', 'mit', 'Verrechnungssteuerabzug'
    (Spalten-Header)? Wenn ja, finde 'Subtotal'+'Ertrag'-Zeile auf derselben
    Seite und nimm den linken numerischen Wert als Spalte-A-Ertrag.

    Rückgabe: ('17.50', {'page': 1, 'bbox': (x0, top, x1, bottom)}) o.ä.
    """
    # 1. Detektion: Header 'Werte mit Verrechnungssteuerabzug' irgendwo?
    by_page: dict[int, list[dict]] = {}
    for w in words:
        by_page.setdefault(w.get("page", 1), []).append(w)

    for page_num, page_words in by_page.items():
        header_present = False
        # Finde 'Werte' mit anschliessend 'mit' und 'Verrechnungssteuerabzug'
        # in derselben Zeile.
        werte_words = [w for w in page_words if w["text"] == "Werte"]
        for werte in werte_words:
            row = _group_by_row(page_words, werte["top"])
            texts = {w["text"] for w in row}
            if "mit" in texts and "Verrechnungssteuerabzug" in texts:
                header_present = True
                break
        if not header_present:
            continue

        # 2. Subtotal-Ertrag-Zeile (oberhalb des Headers): 'Subtotal' + 'Ertrag'
        subtotal_words = [w for w in page_words if w["text"] == "Subtotal"]
        for sub in subtotal_words:
            row = _group_by_row(page_words, sub["top"])
            row_sorted = sorted(row, key=lambda w: w["x0"])
            row_texts = [w["text"] for w in row_sorted]
            if "Ertrag" not in row_texts:
                continue

            # 3. Numerische Werte rechts vom 'Ertrag'-Wort einsammeln
            ertrag_idx = row_texts.index("Ertrag")
            numeric_words = [
                w for w in row_sorted[ertrag_idx + 1 :]
                if _AMOUNT_RE.match(w["text"])
            ]
            if len(numeric_words) < 2:
                continue

            # Linker Wert = Spalte A (mit VRS), rechter = B (ohne VRS)
            col_a = numeric_words[0]
            col_a_val = _parse_amount(col_a["text"])
            if col_a_val is None:
                continue

            vrs = round(0.35 * col_a_val, 2)
            value_str = f"{vrs:.2f}"
            # WICHTIG: Der VRS-Wert ist BERECHNET (0.35 × Subtotal-A), nicht
            # im PDF verankert. Der bbox/source_value zeigt auf das Subtotal,
            # NICHT auf einen Original-VRS-Wert. Wir markieren das explizit als
            # `derived`, damit der Nachweis-Anhang ehrlich ausweist, dass hier
            # gerechnet wurde (Korrektheits-Constraint #2 in CLAUDE.md).
            anchor = {
                "page": page_num,
                "bbox": (col_a["x0"], col_a["top"], col_a["x1"], col_a["bottom"]),
                "source_value": col_a["text"],
                "derived": True,
                "derivation": "0.35 * subtotal_ertrag_spalte_a",
            }
            return value_str, anchor

    return None


def extract_vrs_total(words: list[dict]) -> tuple[str, dict] | None:
    """Wrapper für VRS-Layout-Extraktion. Aktuell nur BROKER-S-Variante."""
    return extract_vrs_total_selma(words)
