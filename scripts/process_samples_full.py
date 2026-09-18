"""Volle Pipeline auf JSON-Samples (mit BBoxes).

Im Unterschied zu ``process_samples.py``, das nur den linearisierten ``.txt``-Text
verwendet, lädt dieses Skript die JSON-Samples mit Wort-BBoxes und schickt sie
durch die identische Pipeline wie ``steuer_extraktor.py``:

    JSON-Wörter → tokenize → classify → extract → resolve_field

Output: ``evals/samples_real/_results_full.json`` mit pro Sample und Feld:

    {pdf_name, feld, value, bbox, page, snippet, anchor_valid}

Diese Datenbasis erlaubt die anker-basierte Eval — also nicht nur "stimmt der
Stringwert", sondern "wurde der Wert aus der richtigen räumlichen Region
gewählt" (Codex-Review).
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

from extractors.anchor_resolver import join_space_thousands, resolve_field
from extractors.label_priors import get_priors
from extractors.out_of_scope import classify_out_of_scope
from extractors.date_inference import infer_jahr
from extractors.classifier import classify
from extractors.llm_extract import extract
from extractors.regex_extract import regex_overrides
from extractors.schema import (
    BankZinsausweisRaw,
    BerufsauslagenRaw,
    HypothekZinsbestaetigungRaw,
    KinderbetreuungRaw,
    KkPraemienbescheinigungRaw,
    KrankheitskostenRaw,
    LiegenschaftsunterhaltRaw,
    LohnausweisRaw,
    Saeule3aRaw,
    SpendenquittungRaw,
    TaggedField,
    WertschriftenverzeichnisRaw,
)
from extractors.tokenize import tokenize

SCHEMA_DISPATCH = {
    "lohnausweis": LohnausweisRaw,
    "bank_zinsausweis": BankZinsausweisRaw,
    "kk_praemienbescheinigung": KkPraemienbescheinigungRaw,
    "saeule_3a": Saeule3aRaw,
    "wertschriftenverzeichnis": WertschriftenverzeichnisRaw,
    "spenden": SpendenquittungRaw,
    "berufsauslagen": BerufsauslagenRaw,
    "kinderbetreuung": KinderbetreuungRaw,
    "hypothek_zinsbestaetigung": HypothekZinsbestaetigungRaw,
    "liegenschaftsunterhalt": LiegenschaftsunterhaltRaw,
    "krankheitskosten": KrankheitskostenRaw,
}


# Betragsfelder, für die der E1-Space-Tausender-Join (join_space_thousands)
# in Frage kommt. Reine Personen-/Jahr-/Text-Felder sind ausgeschlossen.
_AMOUNT_FIELDS_FOR_THOUSANDS: frozenset[str] = frozenset({
    "bruttolohn_pos8", "ahv_alv_nbu_abzug_pos9", "bvg_abzug_pos10a",
    "nettolohn_pos11", "quellensteuer_pos12",
    "vermoegensstand_3112", "bruttoertrag", "bruttoertrag_total",
    "verrechnungssteuer", "verrechnungssteuer_total",
    "einzahlung_betrag", "betrag", "praemie_kvg_total",
    "schuldzinsen", "schuldsaldo_3112",
})


def _arbeitgeber_enthaelt_familienname(arbeitgeber: str) -> bool:
    """True, wenn der Arbeitgeber-Wert einen family.yaml-Personnamen enthält (E3 d).

    Prüft Vor-/Nachname jedes Familienmitglieds — als Paar in beliebiger
    Reihenfolge ("Vorname Nachname" ODER "Nachname Vorname") und mit optionaler
    Anrede ("Herr/Frau"). Konservativ: ein einzelner gemeinsamer Vorname reicht
    NICHT (zu viele False Positives) — beide Tokens müssen vorkommen. family.yaml
    fehlt → keine Aussage (False). KEINE Namen hartcodiert.
    """
    if not arbeitgeber:
        return False
    try:
        from extractors.family import load_family
        fam = load_family()
    except Exception:
        return False
    if fam is None:
        return False
    low = arbeitgeber.lower()
    for member in fam.members:
        first = (member.first_name or "").strip().lower()
        last = (member.last_name or "").strip().lower()
        if first and last and first in low and last in low:
            return True
    return False


# Strassen-Suffixe für die Adress-Erkennung (R7 b). Bewusst lokal gehalten —
# pii_patterns kennt keine Strasse+Hausnummer-Regex (Adressen laufen dort über
# die privacy_secrets-Liste). Suffix + Hausnummer grenzt Adressen zuverlässig
# von echten Firmennamen ("ACME AG", "BANK-A") ab.
_STRASSE_SUFFIX = r"(?:strasse|str\.?|platz|weg|gasse|allee|ring|rain|halde|g(?:ä|ae)ssli)"
_ARBEITGEBER_ADRESSE_RE = re.compile(
    r"[A-Za-zÀ-ÿ]+" + _STRASSE_SUFFIX + r"\s*\d+",
    re.IGNORECASE,
)


def _arbeitgeber_ist_adresse(arbeitgeber: str) -> bool:
    """True, wenn der Arbeitgeber-Wert eine reine Adresse ist (R7 b).

    Erkennt "<Strassenname mit Suffix> <Hausnummer>" (z. B. "Musterstrasse 34",
    "Bahnhofweg 1", "Dorfplatz 5") — auch mit nachfolgendem ", PLZ Ort". Echte
    Firmennamen ohne Strassen-Suffix + Hausnummer ("ACME AG", "BANK-A") gelten
    nicht als Adresse.
    """
    if not arbeitgeber:
        return False
    return bool(_ARBEITGEBER_ADRESSE_RE.search(arbeitgeber))


def _json_to_words(json_doc: dict) -> list[dict]:
    """Konvertiert JSON-Sample in pdfplumber-kompatible Wortliste (mit page)."""
    words: list[dict] = []
    for page in json_doc["pages"]:
        page_num = page["page_num"]
        for w in page["words"]:
            words.append({
                "text": w["text"],
                "x0": w["x0"],
                "top": w["top"],
                "x1": w["x1"],
                "bottom": w["bottom"],
                "page": page_num,
            })
    return words


def _norm_amount(s: str) -> str:
    """Normalisiert Betrag für Vergleich: Apostroph/Space weg, Komma→Punkt."""
    return s.replace("'", "").replace(" ", "").replace(",", ".")


def _locate_value_in_words(value: str, tag_map, context: int = 3):
    """Findet einen (ggf. über mehrere Tokens verteilten) Betrags-/Wert-String
    in der Wortliste und liefert (bbox, page, snippet).

    Tausender-/Cents-getrennte Beträge ("2 593.18" als Tokens ['2','593.18'])
    werden über ein 1–3-Wort-Sliding-Window zusammengeführt. Liefert
    (None, None, "") wenn nicht auffindbar — dann darf der Wert NICHT als
    anchor_valid=True gelten (Nachweis-Pflicht, User-Vorgabe Task 3).
    """
    if not value:
        return None, None, ""
    target = _norm_amount(str(value))
    if not target:
        return None, None, ""
    words = list(tag_map.values())
    n = len(words)
    for i in range(n):
        acc = ""
        for j in range(i, min(i + 3, n)):
            if words[j].page != words[i].page:
                break
            acc += _norm_amount(words[j].text)
            if acc == target:
                span = words[i:j + 1]
                bbox = [
                    min(w.bbox[0] for w in span),
                    min(w.bbox[1] for w in span),
                    max(w.bbox[2] for w in span),
                    max(w.bbox[3] for w in span),
                ]
                lo = max(0, i - context)
                hi = min(n, j + 1 + context)
                snippet = " ".join(w.text for w in words[lo:hi])
                return bbox, words[i].page, snippet[:200]
            if len(acc) > len(target):
                break
    return None, None, ""


# --------------------------------------------------------------------------- #
# R4: Mehrkonten-Total-Heuristik (260612-m8t)                                  #
# --------------------------------------------------------------------------- #
_ENDSALDO_RE = re.compile(r"\bEndsaldo\b", re.IGNORECASE)
_TOTAL_LABEL_RE = re.compile(
    r"\b(?:Total|Zusammenfassung|Guthaben\s+Steuerwert)\b", re.IGNORECASE
)
_BETRAG_TOKEN_RE = re.compile(r"^\d[\d'’.,]*\d$")


def detect_mehrkonten_total(words: list[dict]) -> dict | None:
    """Erkennt Mehrkonten-Bank-Belege mit Zusammenfassungs-Total (R4).

    Bei ≥2 ``Endsaldo``-Zeilen:
    - Mit Zusammenfassungs-Total ("Total 12'164.86") →
      ``vermoegensstand_3112`` = Total-Wert, ``kontotyp`` = "mehrere Konten (N)",
      ``snippet`` = Total-Zeile (Anker). N = Anzahl Endsaldo-Treffer.
    - Ohne Total → ``vermoegensstand_3112`` = "manual_review:mehrkonten_ohne_total".

    Liefert ``None``, wenn < 2 Endsaldo-Zeilen (kein Eingriff bei Einzelkonto).
    Konservativ: nur reine Wort-Token-Analyse, keine LLM-Abhängigkeit.
    """
    endsaldo_count = sum(1 for w in words if _ENDSALDO_RE.search(w.get("text", "")))
    if endsaldo_count < 2:
        return None

    # Zusammenfassungs-Total suchen: ein Total-Label-Token, gefolgt (im Lese-
    # Fluss, bis zu 4 Tokens) von einem Betrags-Token.
    n = len(words)
    for i, w in enumerate(words):
        if not _TOTAL_LABEL_RE.search(w.get("text", "")):
            continue
        for j in range(i + 1, min(i + 5, n)):
            cand = words[j].get("text", "")
            if _BETRAG_TOKEN_RE.match(cand):
                lo = max(0, i - 1)
                hi = min(n, j + 2)
                snippet = " ".join(words[k].get("text", "") for k in range(lo, hi))
                return {
                    "vermoegensstand_3112": cand,
                    "kontotyp": f"mehrere Konten ({endsaldo_count})",
                    "snippet": snippet[:200],
                }
    # ≥2 Endsaldo, aber keine Zusammenfassung → manual_review.
    return {
        "vermoegensstand_3112": "manual_review:mehrkonten_ohne_total",
        "kontotyp": f"mehrere Konten ({endsaldo_count})",
        "snippet": "",
    }


# --------------------------------------------------------------------------- #
# R5: VSt-Härtung — Nicht-Null ohne Label → manual_review (260612-m8t)         #
# --------------------------------------------------------------------------- #
def harden_vrs_fields(fields_out: list[dict]) -> None:
    """Demotiert Nicht-Null-VSt-Werte ohne Label im Anker-Snippet (R5, in-place).

    Für ``verrechnungssteuer``/``verrechnungssteuer_total``: ist der Wert ein
    echter Betrag != 0.00 und enthält der Anker-Snippet KEIN VSt-Label
    (``_snippet_has_vrs_label``), dann ist der Wert vermutlich aus einer
    Ertragsspalte gegriffen → ``value="manual_review:vrs_label_fehlt"``,
    ``anchor_valid=False``.

    AUSNAHME: Wert == 0.00 oder unparsbar → unverändert (viele Belege weisen
    0.00 ohne Label aus). ``manual_review:*``/``not_in_beleg_by_design``/leere
    Werte werden nicht angetastet.
    """
    from extractors.numbers import parse_swiss_amount
    from extractors.steuer_zielmodell import _snippet_has_vrs_label

    for rec in fields_out:
        if rec.get("feld") not in ("verrechnungssteuer", "verrechnungssteuer_total"):
            continue
        value = rec.get("value")
        if not value or not isinstance(value, str):
            continue
        if value.startswith("manual_review:") or value == "not_in_beleg_by_design":
            continue
        try:
            parsed = parse_swiss_amount(value)
        except ValueError:
            continue  # unparsbar → unverändert lassen
        if parsed is None or parsed == 0:
            continue  # 0.00-Ausnahme
        if not _snippet_has_vrs_label(rec.get("snippet", "") or ""):
            rec["value"] = "manual_review:vrs_label_fehlt"
            rec["anchor_valid"] = False


_PRAEMIEN_LABEL_RE = re.compile(
    r"pr[äa]mie|grundversicherung|zusatzversicherung|\bKVG\b|\bVVG\b|"
    r"versicherung|police",
    re.IGNORECASE,
)


def harden_kk_praemien(fields_out: list[dict], words: list[dict]) -> None:
    """Prämien-Label-Pflicht (in-place, 260904-rmx).

    Beobachtet an einem echten Auszug: das Sprachmodell gab als
    ``praemie_kvg_total`` den Wert ``8000.00`` aus — die **Postleitzahl** der
    Absenderadresse. Das Dokument wies gar keine Grundversicherung aus, nur
    eine Zusatzversicherung. Der Wert stand wortwörtlich im Beleg, war also
    verankert und landete grün in der Übertragungstabelle. Dieselbe Fehlerklasse
    wie die AHV-Nummer als AHV-Abzug.

    Die Prüfung: in der Zeile des Ankers muss ein Prämien-Label stehen. Ein
    Wort wie „Grundversicherung" irgendwo im Fliesstext genügt nicht — genau
    dieser Fehler hat einen früheren Anlauf unbrauchbar gemacht, weil die
    Rechtsbelehrung am Seitenende das Wort enthält.

    Werte, die eine beschriftete Regel geliefert hat, bleiben unangetastet:
    dort ist das Label bereits Teil des Musters.
    """
    for rec in fields_out:
        if rec.get("feld") not in ("praemie_kvg_total", "praemie_vvg_total",
                                   "praemie_total"):
            continue
        if rec.get("herkunft") == "regel":
            continue
        wert = rec.get("value")
        if not wert or not isinstance(wert, str):
            continue
        if wert.startswith("manual_review:") or wert in ("null", "",
                                                         "not_in_beleg_by_design"):
            continue
        kontext = _line_context_for_bbox(words, rec.get("bbox"), rec.get("page"))
        if kontext and _PRAEMIEN_LABEL_RE.search(kontext):
            continue
        rec["value"] = f"manual_review:praemie_label_fehlt_{rec['feld']}"
        rec["anchor_valid"] = False


def bruttoertrag_snippet_is_dividende(snippet: str) -> bool:
    """True, wenn der Anker-Snippet eines Bank-Bruttoertrags eine Dividende ist (R8).

    Dividenden-Gutschriften im Kontoauszug sind kein Kontozins — sie gehören
    einmalig ins Wertschriftenverzeichnis. Taucht "Dividende" im Snippet auf,
    besteht Doppelzählungsgefahr (User-verifiziert: dieselbe Dividende steht
    zusätzlich auf einem separaten GS-/Depot-Auszug).
    """
    if not snippet:
        return False
    return "dividende" in snippet.lower()


def _line_context_for_bbox(
    words: list[dict],
    bbox: list[float] | None,
    page: int | None,
    top_tol: float = 3.0,
) -> str:
    """Liefert den Zeilen-Text um eine Anker-bbox (A4, 260630-dsn).

    Sammelt alle Token auf derselben ``page`` mit ähnlichem ``top`` (Toleranz-
    Fenster ±``top_tol`` pt um die Anker-``top``; bbox-Format ``[x0,top,x1,
    bottom]``, top = Index 1), sortiert sie nach ``x0`` (Lesereihenfolge) und
    joint sie zu einer Zeile. Erlaubt es, "Dividende" auch dann zu finden, wenn
    es im engen Anker-Snippet fehlt, aber in der Tabellen-Zeile steht.
    """
    if not words or not bbox or len(bbox) < 2:
        return ""
    anchor_top = bbox[1]
    line_words = [
        w for w in words
        if (page is None or w.get("page") == page)
        and isinstance(w.get("top"), (int, float))
        and abs(w["top"] - anchor_top) <= top_tol
    ]
    line_words.sort(key=lambda w: w.get("x0", 0))
    return " ".join(w.get("text", "") for w in line_words)


def _page_text_for_bbox(words: list[dict], page: int | None) -> str:
    """Liefert den gesamten Text einer Seite (A4-Erweiterung, 260630-dsn).

    Joint alle Token der ``page`` in Lesereihenfolge (top, dann x0). Wird
    genutzt, wenn der bruttoertrag-Anker auf einer Summenzeile ("Total
    Gutschrift 116.70") liegt, die Dividende aber auf einer separaten
    Detailzeile derselben Seite steht.
    """
    if not words:
        return ""
    page_words = [
        w for w in words
        if (page is None or w.get("page") == page)
        and isinstance(w.get("top"), (int, float))
    ]
    page_words.sort(key=lambda w: (round(w.get("top", 0)), w.get("x0", 0)))
    return " ".join(w.get("text", "") for w in page_words)


def harden_dividende_bruttoertrag(
    fields_out: list[dict],
    words: list[dict] | None = None,
) -> None:
    """Demotiert Bank-Bruttoertrag aus einer Dividenden-Zeile (R8, in-place).

    Für ``bruttoertrag``: ist der Wert ein echter Betrag != 0.00 und enthält der
    Anker-Snippet ODER der Zeilen-Kontext "Dividende", dann ist es eine
    Wertschriften-Dividende im Kontoauszug →
    ``value="manual_review:dividende_im_kontoauszug"``, ``anchor_valid=False``
    (gehört ins Wertschriftenverzeichnis, Doppelzählungsgefahr).

    A4 (260630-dsn): Ist ``words`` gegeben, wird zusätzlich zum engen Snippet
    der Zeilen-Kontext der Anker-bbox geprüft — bei engem Snippet ("116.70")
    steht "Dividende" oft nur im umgebenden Zeilen-Text. Ohne ``words``
    bleibt der reine Snippet-Fallback aktiv (Default).

    AUSNAHME: Wert == 0.00 oder unparsbar → unverändert (0.00 ist kein echter
    Ertrag, kein Doppelzählungsrisiko). ``manual_review:*``/
    ``not_in_beleg_by_design``/leere Werte werden nicht angetastet.
    """
    from extractors.numbers import parse_swiss_amount

    for rec in fields_out:
        if rec.get("feld") != "bruttoertrag":
            continue
        value = rec.get("value")
        if not value or not isinstance(value, str):
            continue
        if value.startswith("manual_review:") or value == "not_in_beleg_by_design":
            continue
        try:
            parsed = parse_swiss_amount(value)
        except ValueError:
            continue  # unparsbar → unverändert lassen
        if parsed is None or parsed == 0:
            continue  # 0.00-Ausnahme
        ist_dividende = bruttoertrag_snippet_is_dividende(rec.get("snippet", "") or "")
        if not ist_dividende and words:
            kontext = _line_context_for_bbox(words, rec.get("bbox"), rec.get("page"))
            ist_dividende = "dividende" in kontext.lower()
            # A4-Erweiterung: Der bruttoertrag kann auf einer Summenzeile
            # ("Total Gutschrift 116.70") ankern, während die Dividende auf
            # einer separaten Detailzeile derselben Seite steht. Ist die
            # Anker-Zeile eine Gutschrift/Summe (kein echtes Zins-Label) UND
            # enthält die Seite "Dividende", ist der Ertrag die Dividende →
            # Doppelzählungsgefahr. Konservativ: echte Zins-Belege (Anker auf
            # "Bruttozins"/"Habenzins") lösen NICHT aus.
            if not ist_dividende and "gutschrift" in kontext.lower():
                seite = _page_text_for_bbox(words, rec.get("page"))
                ist_dividende = "dividende" in seite.lower()
        if ist_dividende:
            rec["value"] = "manual_review:dividende_im_kontoauszug"
            rec["anchor_valid"] = False


def process_json_sample(json_path: Path, model: str | None = None,
                        belegtyp_override: str | None = None) -> dict[str, Any]:
    """Führt die volle Pipeline auf einem JSON-Sample aus."""
    json_doc = json.loads(json_path.read_text())
    words = _json_to_words(json_doc)

    if not words:
        return {"pdf_name": json_doc["pdf_name"], "status": "empty", "fields": []}

    # Out-of-Scope ZUERST prüfen — Filename-Regeln (Corporate-Action, Jahresgebühr)
    # sind zuverlässiger als der Text-Klassifikator, der z.B. Corporate-Action
    # fälschlicherweise als bank_zinsausweis einordnet.
    plain_text = " ".join(w["text"] for w in words)
    oos = classify_out_of_scope(plain_text, json_doc["pdf_name"])
    if oos is not None:
        belegtyp_hinweis, reason = oos
        return {
            "pdf_name": json_doc["pdf_name"],
            "status": "out_of_scope",
            "belegtyp": belegtyp_hinweis,
            "out_of_scope_reason": reason,
            "fields": [],
        }

    belegtyp, _reason = classify(words)
    # Manuelle Vorgabe schlaegt die Klassifikation (260904-rmx): erkennt der
    # Header-Regex einen Belegtyp nicht, kann der Mensch ihn setzen und die
    # Extraktion gezielt wiederholen, statt den Beleg von Hand zu erfassen.
    if belegtyp_override:
        belegtyp = belegtyp_override
    # E5: Freizügigkeitskonto (2. Säule) ist immer out-of-scope — das Guthaben
    # ist erst bei Bezug steuerbar und darf NICHT ins Wertschriftenverzeichnis.
    if belegtyp == "freizuegigkeitskonto":
        return {
            "pdf_name": json_doc["pdf_name"],
            "status": "out_of_scope",
            "belegtyp": "freizuegigkeitskonto",
            "out_of_scope_reason": (
                "Freizügigkeitsguthaben 2. Säule — erst bei Bezug steuerbar"
            ),
            "fields": [],
        }
    if belegtyp == "unknown" or belegtyp not in SCHEMA_DISPATCH:
        return {
            "pdf_name": json_doc["pdf_name"],
            "status": "unknown_belegtyp",
            "belegtyp": belegtyp,
            "fields": [],
        }

    tagged_text, tag_map = tokenize(words)
    schema_class = SCHEMA_DISPATCH[belegtyp]

    try:
        raw = extract(tagged_text, schema_class=schema_class,
                      **({"model": model} if model else {}))
    except Exception as e:
        return {
            "pdf_name": json_doc["pdf_name"],
            "status": "extract_failed",
            "belegtyp": belegtyp,
            "error": str(e)[:200],
            "fields": [],
        }

    # Regex-Overrides (für klar beschriftete Felder).
    plain_text = " ".join(w["text"] for w in words)
    _all_regex_overrides = regex_overrides(plain_text, belegtyp)
    _selbstkosten_unvollstaendig: str | None = None
    # Feldnamen, deren Wert von einer beschrifteten Regel oder der
    # Formulargeometrie stammt (nicht vom Sprachmodell).
    _feld_herkunft_regel: set[str] = set()
    for field_name, value in _all_regex_overrides.items():
        if hasattr(raw, field_name):
            setattr(raw, field_name, TaggedField(value=value, tag_refs=[]))
            _feld_herkunft_regel.add(field_name)
        elif field_name == "_selbstkosten_unvollstaendig":
            # Die Selbstkosten wurden aus Komponenten summiert, aber es gab
            # weder ein ausgewiesenes Total zum Gegenrechnen noch mehr als
            # eine erkannte Komponente. Der Wert ist vermutlich zu klein —
            # der Beleg gehoert in die manuelle Pruefung.
            _selbstkosten_unvollstaendig = value
        elif field_name == "vermoegensstand_3112_all":
            # Multi-Konto: alle Guthabensaldo-Werte speichern
            setattr(raw, "_vermoegensstand_all_extra", TaggedField(value=value, tag_refs=[]))
        else:
            # Ein Override fuer ein Feld, das das Schema nicht kennt, verfiel
            # bisher lautlos. Genau so blieb monatelang unbemerkt, dass
            # `selbstgetragene_kosten` zwar per Regex erkannt und von
            # TRANSFER_MAP erwartet wurde, im Schema aber fehlte — der Betrag
            # tauchte in keiner Tabelle auf. Jetzt laut.
            print(
                f"    WARN: Regex-Override '{field_name}' passt zu keinem Feld "
                f"in {schema_class.__name__} — Wert verworfen. Schema ergaenzen?",
                file=sys.stderr,
            )

    # Praemien-Konsistenz (260904-rmx): `praemie_total` ist ein FALLBACK fuer
    # Dokumente, die KVG und VVG nicht trennen. Sind beide (oder eines davon)
    # sauber ausgewiesen, wuerde das Total dieselbe Praemie ein zweites Mal in
    # die Tabelle bringen. Die Regex-Ebene kannte diese Regel bereits, das
    # Sprachmodell nicht — deshalb hier hart erzwingen.
    if belegtyp == "kk_praemienbescheinigung" and hasattr(raw, "praemie_total"):
        from extractors.steuer_zielmodell import is_placeholder as _is_ph_pt

        def _hat_wert(fname: str) -> bool:
            tf = getattr(raw, fname, None)
            val = tf.value if tf else ""
            return bool(val) and not _is_ph_pt(val)

        if _hat_wert("praemie_kvg_total") or _hat_wert("praemie_vvg_total"):
            raw.praemie_total = None


    # Layout-Override Lohnausweis (260904-rmx): Form 11 ist ein standardisiertes
    # Formular — die Betragsspalte ist geometrisch eindeutig. Die Regel schlaegt
    # das Sprachmodell dort deutlich (3/3 statt 61% Pflichtfeld-Abdeckung), weil
    # sie nicht auf die von der OCR zerstoerten Labels angewiesen ist.
    # Die Werte werden wortwoertlich uebernommen, damit die nachgelagerte
    # Anker-Aufloesung die Box im Beleg wiederfindet.
    _layout_plausibel: bool | None = None
    if belegtyp == "lohnausweis":
        from extractors.layout_lohnausweis import extract_lohnausweis_layout
        _seite = json_doc["pages"][0]
        _seiten_words = [w for w in words if w.get("page") == _seite["page_num"]]
        _layout = extract_lohnausweis_layout(
            _seiten_words, _seite.get("width"), _seite.get("height")
        )
        if _layout is not None:
            _layout_plausibel = bool(_layout["plausibel"])
            for _fname in ("bruttolohn_pos8", "nettolohn_pos11"):
                if hasattr(raw, _fname):
                    setattr(raw, _fname,
                            TaggedField(value=_layout[_fname]["value"], tag_refs=[]))
                    _feld_herkunft_regel.add(_fname)

    # Layout-Override Saeule 3a (260904-rmx): Form 21 ist wie Form 11 ein
    # standardisiertes Formular. Der Zielwert steht in der Zeile
    # "Total ... Saeule 3a" in der Betragsspalte; die Vertragszeilen darueber
    # liefern die Gegenprobe. Die Beschriftung entscheidet, nicht die Groesse
    # des Betrags — in derselben Spalte stehen auch Abschluss- und
    # Faelligkeitsjahr.
    if belegtyp == "saeule_3a":
        from extractors.layout_saeule3a import extract_saeule3a_layout
        _seite3a = json_doc["pages"][0]
        _w3a = [w for w in words if w.get("page") == _seite3a["page_num"]]
        _l3a = extract_saeule3a_layout(_w3a, _seite3a.get("width"),
                                       _seite3a.get("height"))
        if _l3a is not None and hasattr(raw, "einzahlung_betrag"):
            raw.einzahlung_betrag = TaggedField(
                value=_l3a["einzahlung_betrag"]["value"], tag_refs=[])
            _feld_herkunft_regel.add("einzahlung_betrag")
            _layout_plausibel = bool(_l3a["plausibel"])

    # kind_name-Inferenz via family.yaml (F5-Fix, Codex-Plan).
    # Nur für kinderbetreuung: wenn LLM "nicht angegeben" oder leer liefert,
    # family.yaml-Match versuchen. Wenn nur 1 Kind eindeutig → Name setzen.
    # Wenn ambiguous oder kein Match → manual_review-Marker.
    if belegtyp == "kinderbetreuung" and hasattr(raw, "kind_name"):
        from extractors.steuer_zielmodell import is_placeholder as _is_ph
        from extractors.family import load_family
        from extractors.person_inference import infer_person, _name_normalize
        _kn_tf = getattr(raw, "kind_name", None)
        _kn_val = _kn_tf.value if _kn_tf else ""
        if _is_ph(_kn_val) or not _kn_val:
            _fam = load_family()
            if _fam:
                # Versuche Kind-Mitglieder im Volltext zu finden
                _kinder = [m for m in _fam.members if m.role in ("kind1", "kind2")]
                _matches = []
                _norm_text = _name_normalize(plain_text)
                for _k in _kinder:
                    _needle = _name_normalize(f"{_k.first_name} {_k.last_name}")
                    if _needle and _needle in _norm_text:
                        _matches.append(_k)
                if len(_matches) == 1:
                    # Eindeutiger Match → Name aus family.yaml setzen
                    _k = _matches[0]
                    setattr(raw, "kind_name", TaggedField(
                        value=f"{_k.first_name} {_k.last_name}",
                        tag_refs=_kn_tf.tag_refs if _kn_tf else [],
                    ))
                else:
                    # Kein oder mehrere Matches → manual_review
                    setattr(raw, "kind_name", TaggedField(
                        value="manual_review:kind_ambiguous",
                        tag_refs=[],
                    ))

    # F7-Layout-Extraktion (Selma-WSV): VRS nicht als Label, sondern als
    # Spalten-Subtotal 'A: Werte mit Verrechnungssteuerabzug'. VRS = 35% × col_A.
    # Vor manual_review:vrs_not_reported versuchen, damit echte Werte aus dem
    # Beleg extrahiert werden statt nur als Sichtprüfung markiert.
    _layout_vrs_anchor: dict | None = None
    if belegtyp == "wertschriftenverzeichnis" and hasattr(raw, "verrechnungssteuer_total"):
        from extractors.steuer_zielmodell import is_placeholder as _is_ph_layout
        _vrs_tf_pre = getattr(raw, "verrechnungssteuer_total", None)
        _vrs_val_pre = _vrs_tf_pre.value if _vrs_tf_pre else ""
        if _is_ph_layout(_vrs_val_pre) or not _vrs_val_pre:
            from extractors.layout_vrs import extract_vrs_total
            _layout_result = extract_vrs_total(words)
            if _layout_result is not None:
                _val, _layout_vrs_anchor = _layout_result
                setattr(raw, "verrechnungssteuer_total", TaggedField(
                    value=_val,
                    tag_refs=[],
                ))

    # VRS manual_review: wenn Bruttoertrag > 200 aber VRS fehlt → nur dann als
    # manual_review:vrs_not_reported markieren, wenn das Dokument KEINEN VRS-Label
    # enthält. Falls ein VRS-Label vorhanden ist, ist es ein Extraktionsfehler
    # (Wert bleibt als fehlend/Placeholder stehen, nicht als by-design absent).
    import re as _re_vrs
    VRS_LABELS_RE = _re_vrs.compile(
        r"Verrechnungssteuer|VRS|Impôt anticipé|Withholding tax",
        _re_vrs.IGNORECASE,
    )
    from extractors.steuer_zielmodell import is_placeholder as _is_ph_vrs
    def _parse_amount_simple(s):
        try:
            return float(str(s).replace("'", "").replace(",", "."))
        except (ValueError, TypeError):
            return None

    for _vrs_field, _brutto_field in [
        ("verrechnungssteuer", "bruttoertrag"),
        ("verrechnungssteuer_total", "bruttoertrag_total"),
    ]:
        if hasattr(raw, _vrs_field) and hasattr(raw, _brutto_field):
            _vrs_tf = getattr(raw, _vrs_field, None)
            _brutto_tf = getattr(raw, _brutto_field, None)
            _vrs_val = _vrs_tf.value if _vrs_tf else ""
            _brutto_val = _brutto_tf.value if _brutto_tf else ""
            if _is_ph_vrs(_vrs_val) or not _vrs_val:
                brutto = _parse_amount_simple(_brutto_val)
                if brutto is not None and brutto > 200:
                    if not VRS_LABELS_RE.search(plain_text):
                        # Kein VRS-Label im Dokument → by-design absent (Broker, Ausland)
                        setattr(raw, _vrs_field, TaggedField(
                            value="manual_review:vrs_not_reported",
                            tag_refs=[],
                        ))
                    # else: VRS-Label vorhanden aber nicht extrahiert → Extraktionsfehler,
                    # Feld bleibt als Placeholder/fehlend (wird von Eval als missing gezählt)

    # Kontoauszug-Heuristik (bank_zinsausweis): wenn bruttoertrag fehlt, aber
    # Transaktionszeilen Dividende/Ertrag/Zinsgutschrift im Dokument stehen →
    # manual_review-Marker, weil potenziell redundant mit TAX_P/Zinsabrechnung.
    if belegtyp == "bank_zinsausweis" and hasattr(raw, "bruttoertrag"):
        from extractors.steuer_zielmodell import is_placeholder as _is_ph_be
        _be_tf = getattr(raw, "bruttoertrag", None)
        _be_val = _be_tf.value if _be_tf else ""
        if _is_ph_be(_be_val) or not _be_val:
            _txn_re = _re_vrs.compile(r"Dividende|Zinsgutschrift|Ertrag", _re_vrs.IGNORECASE)
            if _txn_re.search(plain_text):
                setattr(raw, "bruttoertrag", TaggedField(
                    value="manual_review:bruttoertrag_transaction_line",
                    tag_refs=[],
                ))

    # Name-Cleanup: trailing-Geburtsdatum + Komma-Listen aus Personen-Feldern
    # entfernen. LLM gibt z.B. "Muster, Hans, geboren am 01.01.1900" zurück;
    # der Anker-Resolver kann das nicht räumlich verankern.
    from extractors.name_cleanup import clean_person_name
    from extractors.steuer_zielmodell import get_spec as _zm_get
    _spec = _zm_get(belegtyp)
    if _spec and _spec.person_field and hasattr(raw, _spec.person_field):
        _tf = getattr(raw, _spec.person_field)
        if _tf and _tf.value:
            cleaned = clean_person_name(_tf.value)
            if cleaned != _tf.value:
                setattr(raw, _spec.person_field,
                        TaggedField(value=cleaned, tag_refs=_tf.tag_refs))

    # periode_von/bis: wenn LLM einen Wert liefert, der weder im Anker
    # auflösbar ist NOCH ein Tag-Match hat, ist es typischerweise eine
    # Halluzination — entfernen statt anzeigen.
    for periode_field in ("periode_von", "periode_bis"):
        if hasattr(raw, periode_field):
            tf = getattr(raw, periode_field, None)
            if tf and tf.value:
                # Check ob Wert irgendwo im Doc-Text vorkommt
                if tf.value not in plain_text and tf.value.replace(".", "") not in plain_text.replace(".", ""):
                    setattr(raw, periode_field, TaggedField(value="", tag_refs=[]))

    # Halluzinations-Filter für Institutions-/Personen-Felder.
    # LLM erfindet manchmal echte Firmennamen ("Swica", "CSS", "Universität Zürich"),
    # obwohl der anonymisierte Text nur Marker enthält ("KK-A", "INST-B").
    # → wenn extrahierter Wert NICHT im plain_text vorkommt, ist es Halluzination
    # und wird auf manual_review markiert (Privacy-Leak + Anker fehlt sowieso).
    _HALLUCINATION_CHECK_FIELDS = {
        "kasse": ("KK", "INST"),
        "aussteller": ("BANK", "BROKER", "INST", "STIFTUNG_3A"),
        "arbeitgeber": ("ARBEITGEBER", "INST"),
        "stiftung": ("STIFTUNG_3A", "INST"),
        "institut": ("BANK", "BROKER", "INST"),
        "anbieter": ("ANBIETER", "INST"),
        "empfaenger": ("INST",),
        "leistungserbringer": ("LEISTUNGSERBRINGER", "INST"),
        "versicherte_person_name": (),
        "person_name": (),
        "kontoinhaber": (),
    }
    _MARKER_RE = re.compile(
        r"\b(?:KK|BANK|BROKER|STIFTUNG_3A|INST|FIRMA|ANBIETER|INSTITUT|LEISTUNGSERBRINGER|ARBEITGEBER)-[A-Z][A-Z0-9_]*\b"
    )
    for hname, allowed_prefixes in _HALLUCINATION_CHECK_FIELDS.items():
        if not hasattr(raw, hname):
            continue
        tf = getattr(raw, hname, None)
        if not tf or not tf.value:
            continue
        v = tf.value.strip()
        if not v or v.startswith("manual_review:"):
            continue
        # Akzeptiere Platzhalter-Marker (KK-A, BANK-B, INST-X, BROKER-Y, …)
        if _MARKER_RE.fullmatch(v):
            continue
        # Prüfe Substring-Vorkommen (case-insensitive, ohne Komma/Punkt)
        v_norm = v.replace(",", "").replace(".", "").lower()
        text_norm = plain_text.replace(",", "").replace(".", "").lower()
        if v_norm and v_norm not in text_norm:
            # Halluziniert — Fallback: suche im plain_text einen passenden
            # anonymisierten Marker für dieses Feld (z.B. INST-M für arbeitgeber).
            # Wenn EIN Marker eindeutig vorhanden ist, übernehmen statt MR.
            substituted = False
            if allowed_prefixes:
                # Liste mit Position (Reihenfolge im Text)
                matches = [
                    (m.start(), m.group(0)) for m in _MARKER_RE.finditer(plain_text)
                    if m.group(0).split("-")[0] in allowed_prefixes
                ]
                unique_markers = {m[1] for m in matches}
                marker = None
                if len(unique_markers) == 1:
                    marker = unique_markers.pop()
                elif matches:
                    # Mehrere Marker → nehme den FRÜHESTEN (typisch:
                    # Arbeitgeber-/Kasse-Header steht am Doc-Anfang).
                    matches.sort()
                    marker = matches[0][1]
                if marker:
                    setattr(raw, hname, TaggedField(
                        value=marker,
                        tag_refs=tf.tag_refs,
                    ))
                    substituted = True
            if not substituted:
                # Person-Felder: enthalten typischerweise Fantasy-Tokens (Hans,
                # Muster, Lina, Tim). Wenn ein Fantasy-Vorname im Wert UND im
                # Text steht, akzeptiere den Wert als (fuzzy) anonymisiert.
                if not allowed_prefixes:  # nur Person-Felder
                    fantasy_first = ("Hans", "Maria", "Lina", "Tim", "Anna", "Peter")
                    if any(fn in v and fn in plain_text for fn in fantasy_first):
                        # Wert ist anonymisierter Name — Standalone-Tokens, nur
                        # Anker fehlt. Akzeptiere ohne MR-Markierung.
                        continue
                setattr(raw, hname, TaggedField(
                    value=f"manual_review:hallucination_{hname}",
                    tag_refs=[],
                ))

    # E3 (d): Lohnausweis-Arbeitgeber enthält einen family.yaml-Personnamen?
    # Der Arbeitgeber ist immer eine Firma — steht stattdessen ein
    # Familien-Mitglied drin (in beliebiger Reihenfolge, auch mit Anrede
    # "Herr/Frau"), ist die Person-/Firma-Spalte vermutlich vertauscht →
    # manual_review. family.yaml-Namen kommen zur Laufzeit, keine Hartcodierung.
    if belegtyp == "lohnausweis":
        _ag_tf = getattr(raw, "arbeitgeber", None)
        _ag_v = _ag_tf.value if _ag_tf else ""
        if _ag_v and not str(_ag_v).startswith("manual_review:"):
            if _arbeitgeber_enthaelt_familienname(str(_ag_v)):
                setattr(raw, "arbeitgeber", TaggedField(
                    value="manual_review:arbeitgeber_ist_person",
                    tag_refs=[],
                ))
            elif _arbeitgeber_ist_adresse(str(_ag_v)):
                # R7 b: reine Adresse statt Firmenname → Person-/Firma-Spalte
                # vertauscht oder Adresszeile gegriffen → manual_review.
                setattr(raw, "arbeitgeber", TaggedField(
                    value="manual_review:arbeitgeber_ist_adresse",
                    tag_refs=[],
                ))

    # Nettolohn ist nun primärer Steuer-Zielwert (Ziffer 11).
    # Die netto_identity_delta-Demotion wird NICHT mehr durchgeführt —
    # der LLM-Wert wird akzeptiert wenn er im Dokumenttext vorkommt.
    # Plausibility-Fehler (Pos11 ≠ Pos8−Pos9−Pos10a) können auftreten wenn
    # Pos10b (weitere BVG-Abzüge) nicht extrahiert wurde; das ist ein
    # Hinweis, kein Sperrgrund für den Nettolohn-Wert.

    # Bank — DoD 3: "Wenn kein Bruttozins-/Ertragslabel sichtbar ist, keinen
    # Ertrag halluzinieren." Wenn KEIN Ertrags-Label im Text steht, ist jeder
    # bruttoertrag (egal woher) eine Halluzination → löschen.
    # Ein Wert "0.00" bei vorhandenem Label (z.B. "Bruttozins 0.00") bleibt.
    # R4: Mehrkonten-Bank-Beleg mit Zusammenfassung. Greift nur bei ≥2
    # Endsaldo-Zeilen (sonst None → Einzelkonto-Logik unverändert). Mit Total →
    # Total-Vermögensstand + kontotyp; ohne Total → manual_review.
    if belegtyp == "bank_zinsausweis":
        _mk = detect_mehrkonten_total(words)
        if _mk is not None:
            setattr(raw, "vermoegensstand_3112", TaggedField(
                value=_mk["vermoegensstand_3112"], tag_refs=[]
            ))
            if hasattr(raw, "kontotyp"):
                setattr(raw, "kontotyp", TaggedField(
                    value=_mk["kontotyp"], tag_refs=[]
                ))

    if belegtyp == "bank_zinsausweis":
        _be_tf = getattr(raw, "bruttoertrag", None)
        _be_v = _be_tf.value if _be_tf else ""
        if _be_v and not str(_be_v).startswith("manual_review:"):
            # Ertrags-Label-Familie: Bruttozins, Bruttoertrag, Zins(zahlung),
            # Gutschrift, Abschlussbetreffnis, Habenzins, Dividende, Total CHF.
            has_ertrag_label = bool(re.search(
                r"\bBrutto(?:zins|ertrag)\b|\bZinszahlung\b|\bAbschlussbetreffnis\b"
                r"|\bGutschrift\b|\bHabenzins\b|\bNettozins\b|\bDividende\b",
                plain_text, re.IGNORECASE
            ))
            if not has_ertrag_label:
                # Kein Ertrags-Label → Wert ist Halluzination (meist = Vermögensstand)
                setattr(raw, "bruttoertrag", TaggedField(value="", tag_refs=[]))

    # KK Praemien: selbstgetragene_kosten aus regex_overrides einfügen.
    # Das Feld ist nicht im Schema (KkPraemienbescheinigungRaw) — wir
    # injizieren es als dynamisches Zusatzfeld für den Transfer-Output.
    if belegtyp in ("kk_praemienbescheinigung", "krankheitskosten"):
        from extractors.regex_extract import regex_overrides as _ro
        _overrides = _ro(plain_text, belegtyp)
        if "selbstgetragene_kosten" in _overrides:
            _sk = _overrides["selbstgetragene_kosten"]
            # Als synthetisches TaggedField anhängen (nicht im Schema,
            # wird als extra feld in fields_out eingetragen)
            setattr(raw, "_selbstgetragene_kosten_extra", TaggedField(
                value=_sk, tag_refs=[]
            ))
        if "praemie_kvg_total" in _overrides:
            _pk = _overrides["praemie_kvg_total"]
            if hasattr(raw, "praemie_kvg_total"):
                # Feld im Schema vorhanden → nur überschreiben wenn leer
                existing = getattr(raw, "praemie_kvg_total", None)
                if not (existing and existing.value):
                    setattr(raw, "praemie_kvg_total", TaggedField(value=_pk, tag_refs=[]))
            else:
                # Krankheitskosten-Schema hat praemie_kvg_total nicht →
                # als dynamisches Extra-Feld eintragen
                setattr(raw, "_praemie_kvg_extra", TaggedField(value=_pk, tag_refs=[]))
        # Prämie Total (nur wenn keine KVG/VVG-Trennung im Dokument) — eigenes
        # Feld, damit Beschreibung "Prämie Total" und NICHT "Prämie KVG" lautet.
        if "praemie_total" in _overrides:
            setattr(raw, "_praemie_total_extra", TaggedField(
                value=_overrides["praemie_total"], tag_refs=[]
            ))

    # Jahr-Inferenz aus Dateiname/Header — Codex' Punkt 5.
    # Override nur wenn LLM-Wert fehlt ODER implausibel ist. Wenn auch die
    # Inferenz nichts findet, das implausible LLM-Jahr ENTFERNEN — sonst zeigt
    # die Tabelle weiterhin 'jahr=12413' (Codex' #3-Bug). Lieber als "missing"
    # markieren als falschen Wert anzeigen.
    _jahr_inference_source: str | None = None
    if hasattr(raw, "jahr"):
        current_jahr = getattr(raw, "jahr", None)
        current_value = (
            current_jahr.value if current_jahr and current_jahr.value else None
        )
        plausible = (
            current_value is not None
            and current_value.isdigit()
            and 2018 <= int(current_value) <= 2030
        )
        if not plausible:
            jahr_inferred, infer_source = infer_jahr(json_doc["pdf_name"], plain_text)
            if jahr_inferred:
                setattr(raw, "jahr", TaggedField(value=jahr_inferred, tag_refs=[]))
                _jahr_inference_source = infer_source
            else:
                # Keine zuverlässige Quelle — implausibles LLM-Jahr löschen
                setattr(raw, "jahr", TaggedField(value="", tag_refs=[]))

    # field_exceptions — gepflegte, verifizierte Feld-Abwesenheiten.
    # Eine optionale Datei `field_exceptions.json` im Sample-Verzeichnis listet
    # pro PDF-Stem die Felder, die im Beleg by-design NICHT vorkommen (z.B. eine
    # Zins-/Kapitalbescheinigung ohne Zinsposition). Das Wissen fliesst als
    # gepflegte Ausnahme ein, nicht als Code-Hack pro Datei. Format:
    #   {pdf_stem: {feldname: "grund"}}
    # Diese Schleife läuft NACH allen Heuristiken — die Exception gewinnt also
    # und überschreibt einen evtl. gesetzten manual_review:*-Marker mit dem
    # bewussten Marker "not_in_beleg_by_design".
    _field_exception_sources: dict[str, str] = {}
    _exceptions_applied = 0
    _exceptions_skipped = 0
    _exceptions_path = json_path.parent / "field_exceptions.json"
    if _exceptions_path.exists():
        try:
            _exceptions_doc = json.loads(_exceptions_path.read_text())
        except (ValueError, OSError):
            _exceptions_doc = {}
        _stem_exceptions = _exceptions_doc.get(json_path.stem, {})
        for _feld, _grund in _stem_exceptions.items():
            if hasattr(raw, _feld):
                # Leitplanke (Finding 5): wenn die Extraktion bereits einen
                # echten, verankerten Wert lieferte, ist die Exception
                # vermutlich veraltet (das Feld kommt doch im Beleg vor). Dann
                # NICHT stumm mit "not_in_beleg_by_design" überschreiben —
                # warnen und überspringen, damit kein echter Wert verloren geht.
                _existing = getattr(raw, _feld, None)
                _has_real_anchor = (
                    _existing is not None
                    and not isinstance(_existing, list)
                    and getattr(_existing, "value", None) not in (None, "")
                    and bool(getattr(_existing, "tag_refs", None))
                )
                if _has_real_anchor:
                    print(
                        f"WARNUNG: stale exception? {json_path.stem}:{_feld} hat "
                        f"echten verankerten Wert — Exception übersprungen",
                        file=sys.stderr,
                    )
                    _exceptions_skipped += 1
                    continue
                setattr(raw, _feld, TaggedField(
                    value="not_in_beleg_by_design", tag_refs=[]
                ))
                _field_exception_sources[_feld] = f"user_verified:{_grund}"
                _exceptions_applied += 1

    fields_out: list[dict] = []
    for fname in schema_class.model_fields:
        if fname == "schema_version":
            continue
        tf = getattr(raw, fname, None)
        if tf is None or isinstance(tf, list):
            continue
        # R6 (260612-m8t): Müllwert-Guard für Betragsfelder. Ein LLM-Müllwert
        # wie "{CHF 8.718}" darf NICHT als Betrag mit anchor_valid=true
        # durchgehen. parse_swiss_amount wirft bei Klammern/Buchstaben einen
        # ValueError → Feld auf manual_review:nicht_parsebar, anchor_valid=False.
        if (
            fname in _AMOUNT_FIELDS_FOR_THOUSANDS
            and tf.value
            and isinstance(tf.value, str)
            and not tf.value.startswith("manual_review:")
            and tf.value not in {"null", "", "not_in_beleg_by_design"}
        ):
            from extractors.numbers import parse_swiss_amount as _psa
            if re.search(r"[{}\[\]()]|[A-Za-z]", tf.value):
                try:
                    _psa(tf.value)
                except ValueError:
                    tf = TaggedField(
                        value="manual_review:nicht_parsebar", tag_refs=[]
                    )
                    setattr(raw, fname, tf)
        valid, snippet, bboxes = resolve_field(
            tf.value, tf.tag_refs, tag_map,
            label_priors=get_priors(belegtyp, fname),
        )
        # E1 Space-Tausender-Korrektur: bei Betragsfeldern prüfen, ob der
        # extrahierte Rumpf ("652.55") in Wahrheit nur den Tausenderblock eines
        # space-getrennten Betrags ("24 652.55") verloren hat. Greift nur bei
        # numerischen Feldern und nur, wenn der Join einen anderen (vollen) Wert
        # ergibt — konservativ, kein Eingriff bei bereits korrekten Werten.
        if (
            fname in _AMOUNT_FIELDS_FOR_THOUSANDS
            and tf.value
            and not str(tf.value).startswith("manual_review:")
            and str(tf.value) not in {"null", "", "not_in_beleg_by_design"}
        ):
            _joined = join_space_thousands(str(tf.value), tag_map)
            if _joined is not None:
                _joined_value, _joined_bboxes = _joined
                if _joined_value != str(tf.value):
                    tf.value = _joined_value
                    valid = True
                    bboxes = _joined_bboxes
                    snippet = _joined_value
        # Page über BBox-Lookup
        page: int | None = None
        if tf.tag_refs and tf.tag_refs[0] in tag_map:
            page = tag_map[tf.tag_refs[0]].page
        elif bboxes:
            for tw in tag_map.values():
                if tw.bbox == bboxes[0]:
                    page = tw.page
                    break
        # inference_source: für "jahr" aus Dateiname/Header-Inferenz tracken;
        # für verrechnungssteuer_total mit Layout-Rule (Selma) markieren;
        # für alle anderen Felder: "text:bbox_anchor" wenn Anker vorhanden, sonst None.
        if fname in _field_exception_sources:
            # field_exception greift vor der bestehenden infsrc-Kaskade:
            # user_verified:<grund> ist die Quelle des by-design-Werts.
            infsrc: str | None = _field_exception_sources[fname]
        elif fname == "jahr" and _jahr_inference_source and not tf.tag_refs:
            infsrc = _jahr_inference_source
        elif (
            fname == "verrechnungssteuer_total"
            and _layout_vrs_anchor is not None
            and not tf.tag_refs
        ):
            infsrc = "layout_rule:selma_ertrag_mit_vrs_35pct"
        elif valid and bboxes:
            infsrc = "text:bbox_anchor"
        else:
            infsrc = None
        # Layout-Anker-Override für verrechnungssteuer_total
        _bbox_out = list(bboxes[0]) if bboxes else None
        _page_out = page
        _anchor_valid_out = valid
        if (
            fname == "verrechnungssteuer_total"
            and _layout_vrs_anchor is not None
            and not _anchor_valid_out
        ):
            _bb = _layout_vrs_anchor["bbox"]
            _bbox_out = [_bb[0], _bb[1], _bb[2], _bb[3]]
            _page_out = _layout_vrs_anchor["page"]
            _anchor_valid_out = True
        # Person-Felder ohne BBox-Anker: Akzeptiere wenn der Wert aus
        # Fantasy-Tokens besteht (die anonymisierten Namen liegen nicht als
        # BBox-Anker vor — das ist ein Anonymizer-Artefakt, kein Fehler).
        # Nur wenn der Wert komplett nicht im Text vorkommt → MR.
        _value_out = tf.value
        if (
            fname in {"versicherte_person_name", "person_name", "kontoinhaber",
                      "arbeitnehmer_name", "spender_name", "kind_name"}
            and _value_out
            and not _value_out.startswith("manual_review:")
            and _value_out != "not_in_beleg_by_design"
            and not _anchor_valid_out
        ):
            # Fantasy-Vorname vorhanden → als anonymisiert akzeptieren
            _fantasy_names = {"Hans", "Maria", "Lina", "Tim", "Anna", "Peter",
                              "Muster", "Herr", "Frau"}
            _v_tokens = set(_value_out.replace(",", " ").replace(".", " ").split())
            if _v_tokens & _fantasy_names:
                pass  # akzeptiert — anonymisierter Name ohne BBox ist normal
            else:
                _value_out = f"manual_review:no_anchor_{fname}"

        # Beträge ohne BBox-Anker, aber mit eindeutigem String-Match im Text
        # → text-occurrence-Anker akzeptieren. Verhindert MR-Cascade für
        # Quellensteuer/Bruttolohn etc. wenn OCR-Layout den Resolver scheitert.
        if (
            fname in {"bruttolohn_pos8", "quellensteuer_pos12",
                      "ahv_alv_nbu_abzug_pos9", "nettolohn_pos11",
                      "bvg_abzug_pos10a", "prämie_kvg", "prämie_vvg",
                      "einzahlung_3a", "vermoegensstand_3112",
                      "bruttoertrag", "bruttoertrag_total",
                      "verrechnungssteuer", "verrechnungssteuer_total"}
            and _value_out
            and not str(_value_out).startswith("manual_review:")
            and _value_out != "not_in_beleg_by_design"
            and str(_value_out) not in {"null", ""}
            and not _anchor_valid_out
        ):
            # Task 3: kein anchor_valid=true ohne Nachweis. Wert in den Wörtern
            # lokalisieren → bbox/page/snippet. Nur dann als verankert gelten.
            _loc_bbox, _loc_page, _loc_snip = _locate_value_in_words(_value_out, tag_map)
            if _loc_bbox is not None:
                _anchor_valid_out = True
                _bbox_out = _loc_bbox
                _page_out = _loc_page
                snippet = _loc_snip
                if infsrc is None:
                    infsrc = "text:word_match"
            else:
                v_str = str(_value_out).replace("'", "").replace(",", ".")
                text_norm = plain_text.replace("'", "").replace(",", ".")
                if v_str in text_norm:
                    # Im Text vorhanden, aber nicht als zusammenhängende Tokens
                    # lokalisierbar → Plaintext-Snippet als Nachweis liefern.
                    _idx = text_norm.find(v_str)
                    _raw_idx = max(0, _idx - 40)
                    snippet = plain_text[_raw_idx:_raw_idx + 120].strip()
                    _anchor_valid_out = True
                    if infsrc is None:
                        infsrc = "text:plaintext_occurrence"
                else:
                    # Wert NICHT im Doc → wahrscheinlich LLM-Halluzination.
                    _value_out = f"manual_review:value_not_in_document_{fname}"
        _field_record = {
            "feld": fname,
            "value": _value_out,
            "bbox": _bbox_out,
            "page": _page_out,
            "snippet": snippet[:200],
            "anchor_valid": _anchor_valid_out,
            "inference_source": infsrc,
            # Herkunft (260904-rmx): das staerkste Konfidenz-Signal ueberhaupt.
            # Ein Wert, den eine beschriftete Regel oder die Formulargeometrie
            # geliefert hat, ist verlaesslicher als einer, den das Sprachmodell
            # unter mehreren Zahlen ausgewaehlt hat. Bisher waren beide im
            # Ergebnis ununterscheidbar.
            "herkunft": ("regel" if fname in _feld_herkunft_regel else "modell"),
        }
        # Berechnete (derived) Werte ehrlich markieren: der Layout-VRS-Wert ist
        # 0.35 × Subtotal, nicht ein im PDF verankerter Originalbetrag. Der
        # bbox zeigt auf das Subtotal — `derived` macht das im Nachweis sichtbar.
        if (
            fname == "verrechnungssteuer_total"
            and _layout_vrs_anchor is not None
            and _layout_vrs_anchor.get("derived")
        ):
            _field_record["derived"] = True
            _field_record["derivation"] = _layout_vrs_anchor.get(
                "derivation", "0.35 * subtotal_ertrag_spalte_a"
            )
            _field_record["derivation_source_value"] = _layout_vrs_anchor.get(
                "source_value"
            )
        fields_out.append(_field_record)

    # Injiziere dynamische Zusatzfelder die nicht im Schema stehen.
    # Task 3: jedes Extra-Feld bekommt einen Nachweis (bbox/page/snippet) via
    # _locate_value_in_words. anchor_valid=True NUR wenn lokalisierbar.
    def _label_snippet(label: str, occurrence: int = 0, following: int = 5):
        """Findet das `occurrence`-te Vorkommen von `label` und liefert
        (bbox, page, snippet) über Label + folgende Tokens. Für Werte, deren
        Ziffern im OCR-Text getrennt/umgestellt stehen (HBL: '.56 1'234')."""
        ws = list(tag_map.values())
        hits = 0
        for i, w in enumerate(ws):
            if label.lower() in w.text.lower():
                if hits == occurrence:
                    span = ws[i:i + following + 1]
                    bbox = [min(x.bbox[0] for x in span), min(x.bbox[1] for x in span),
                            max(x.bbox[2] for x in span), max(x.bbox[3] for x in span)]
                    return bbox, w.page, " ".join(x.text for x in span)[:200]
                hits += 1
        return None, None, ""

    def _make_extra_field(feld: str, value: str, source: str,
                          label: str | None = None, occurrence: int = 0) -> dict:
        bbox, page, snip = _locate_value_in_words(value, tag_map)
        if bbox is not None:
            return {"feld": feld, "value": value, "bbox": bbox, "page": page,
                    "snippet": snip, "anchor_valid": True, "inference_source": source}
        # Fallback 1: Plaintext-Snippet (Wert zusammenhängend im Text)
        vn = _norm_amount(value)
        tn = _norm_amount(plain_text)
        if vn and vn in tn:
            _idx = tn.find(vn)
            _raw = max(0, _idx - 40)
            snip = plain_text[_raw:_raw + 120].strip()
            return {"feld": feld, "value": value, "bbox": None, "page": None,
                    "snippet": snip[:200], "anchor_valid": True,
                    "inference_source": source}
        # Fallback 2: Label-gebundener Nachweis (OCR-getrennte Ziffern, z.B. HBL)
        if label:
            lb, lp, ls = _label_snippet(label, occurrence)
            if lb is not None:
                return {"feld": feld, "value": value, "bbox": lb, "page": lp,
                        "snippet": ls, "anchor_valid": True, "inference_source": source}
        # Kein Nachweis → nicht als verankert ausgeben
        return {"feld": feld, "value": value, "bbox": None, "page": None,
                "snippet": "", "anchor_valid": False, "inference_source": None}

    # - vermoegensstand_3112_all: zusätzliche Kontozeilen für Multi-Konto-Dokumente
    _extra_vmst_all = getattr(raw, "_vermoegensstand_all_extra", None)
    if _extra_vmst_all and _extra_vmst_all.value:
        _all_vals = _extra_vmst_all.value.split(";")
        for _i, _vmst_v in enumerate(_all_vals[1:], start=2):  # erste ist schon im Schema
            # Label-gebundener Nachweis: i-tes "Guthabensaldo"-Vorkommen
            fields_out.append(_make_extra_field(
                f"vermoegensstand_3112_konto{_i}", _vmst_v.strip(),
                "regex:hbl_guthabensaldo_multi",
                label="Guthabensaldo", occurrence=_i - 1))

    # - praemie_kvg_total: für krankheitskosten-Belege mit kombiniertem Inhalt
    _extra_kvg = getattr(raw, "_praemie_kvg_extra", None)
    if _extra_kvg and _extra_kvg.value:
        fields_out.insert(0, _make_extra_field(
            "praemie_kvg_total", _extra_kvg.value, "regex:kk_kvg"))

    # - praemie_total: nur wenn keine KVG/VVG-Trennung im Dokument sichtbar
    _extra_total = getattr(raw, "_praemie_total_extra", None)
    if _extra_total and _extra_total.value:
        fields_out.insert(0, _make_extra_field(
            "praemie_total", _extra_total.value, "regex:kk_praemie_total"))

    # - selbstgetragene_kosten: aus regex_overrides für KK-Belege
    _extra_tf = getattr(raw, "_selbstgetragene_kosten_extra", None)
    if _extra_tf and _extra_tf.value:
        fields_out.append(_make_extra_field(
            "selbstgetragene_kosten", _extra_tf.value, "regex:kk_selbstkosten"))

    # R5 (260612-m8t): VSt-Härtung als Nach-Pass — Nicht-Null-VSt ohne Label
    # im Anker-Snippet wird zu manual_review:vrs_label_fehlt demotiert (0.00
    # bleibt). Der Wert verschwindet damit aus der Übertragungstabelle.
    harden_vrs_fields(fields_out)
    # A4 (260630-dsn): Zeilen-Kontext aus ``words`` mitgeben, damit "Dividende"
    # auch bei engem Anker-Snippet aus der Tabellen-Zeile erkannt wird.
    harden_dividende_bruttoertrag(fields_out, words=words)
    # Praemien-Label-Pflicht: verhindert, dass eine Postleitzahl oder eine
    # andere Zahl ohne Praemien-Label als Praemie in die Tabelle wandert.
    if belegtyp in ("kk_praemienbescheinigung", "krankheitskosten"):
        harden_kk_praemien(fields_out, words)

    # Herkunft auf allen Datensaetzen sicherstellen — einige entstehen in
    # Hilfsfunktionen (Mehrkonto, Layout-VRS) und kennen das Feld nicht.
    for _rec in fields_out:
        _rec.setdefault(
            "herkunft",
            "regel" if _rec.get("feld") in _feld_herkunft_regel else "modell",
        )

    result_out = {
        "pdf_name": json_doc["pdf_name"],
        "status": "ok",
        "belegtyp": belegtyp,
        "fields": fields_out,
    }
    # Truncation-Marker durchreichen: wenn llm_extract den Belegtext gekürzt
    # hat, ist die Extraktion potenziell unvollständig → build_tax_output
    # kennzeichnet den Beleg als manuell-prüfen (Korrektheits-Constraint).
    if getattr(raw, "_text_truncated", False):
        result_out["text_truncated"] = True
    # Gegenprobe des Layout-Extraktors durchreichen (260904-rmx): Brutto minus
    # Abzuege muss Netto ergeben. Geht die Rechnung auf, ist der Wert
    # arithmetisch bewiesen; geht sie nicht auf, hat die OCR die Abzuege
    # zerlegt und der Beleg gehoert in die manuelle Pruefung.
    if _layout_plausibel is not None:
        result_out["layout_plausibel"] = _layout_plausibel
    # Unvollstaendige Selbstkosten-Summe durchreichen (260904-rmx): lieber
    # eine sichtbare Warnung als eine zu kleine Zahl in der Steuererklaerung.
    if _selbstkosten_unvollstaendig is not None:
        result_out["selbstkosten_unvollstaendig"] = _selbstkosten_unvollstaendig
        for _rec in fields_out:
            if _rec.get("feld") == "selbstgetragene_kosten":
                _rec["anchor_valid"] = False
                _rec["plaus_hinweis"] = (
                    "Summe aus nur einer erkannten Komponente "
                    f"({_selbstkosten_unvollstaendig}) und kein ausgewiesenes "
                    "Total zum Gegenrechnen — vermutlich unvollstaendig"
                )
        # E6: truncierte Belege MIT extrahierten Betragsfeldern bekommen einen
        # maschinenlesbaren Plausi-Hinweis — Beträge könnten unvollständig sein.
        _hat_betrag = any(
            f.get("feld") in _AMOUNT_FIELDS_FOR_THOUSANDS
            and f.get("value")
            and not str(f.get("value")).startswith("manual_review:")
            and str(f.get("value")) not in {"", "null", "not_in_beleg_by_design"}
            for f in fields_out
        )
        if _hat_betrag:
            result_out["plausi_hinweise"] = result_out.get("plausi_hinweise", []) + [
                "⚠ text_truncated — Beträge ggf. unvollständig"
            ]
    # field_exception-Zähler durchreichen, damit main() pro Lauf aggregiert
    # ausweisen kann, wie viele Exceptions angewandt bzw. (stale) übersprungen
    # wurden (Finding 5).
    if _exceptions_applied or _exceptions_skipped:
        result_out["_field_exceptions"] = {
            "applied": _exceptions_applied,
            "skipped": _exceptions_skipped,
        }
    return result_out


def _ist_word_json(pfad: Path) -> bool:
    """Nur Dokumente extrahieren — keine Korrektur- oder Verlaufsdateien.

    Seit der Abgleich korrekturen.json verschont (er hat sie vorher bei jedem
    Lauf geloescht), liegt sie im selben Ordner wie die Word-JSONs. Eine
    Namensliste allein reicht nicht: der naechste Dateiname ist wieder neu.
    Ein Word-JSON erkennt man an seinem "pages"-Feld (260904-rmx).
    """
    try:
        with pfad.open(encoding="utf-8") as fh:
            kopf = fh.read(4096)
    except OSError:
        return False
    return '"pages"' in kopf


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--samples", type=Path, default=Path("evals/samples_real"),
                        help="Verzeichnis mit *.json-Wort-Dumps (Default: evals/samples_real)")
    parser.add_argument("--model", type=str, default=None,
                        help="Ollama-Modell-Override (Default: DEFAULT_MODEL aus "
                             "llm_extract). Für Modellvergleich, z.B. "
                             "qwen2.5:14b-instruct-q4_K_M.")
    parser.add_argument("--out", type=Path, default=None,
                        help="Ausgabe-Pfad für die Ergebnis-JSON (Default: "
                             "<samples>/_results_full.json). Für Modellvergleich "
                             "ein separater Pfad, um die Baseline nicht zu "
                             "überschreiben.")
    args = parser.parse_args()
    samples_dir = args.samples
    json_files = sorted(
        p for p in samples_dir.glob("*.json")
        if not p.name.endswith(".truth.json")
        and "_results" not in p.name
        and p.name != "anon_truth_mapping.json"
        and p.name != "field_exceptions.json"
        and _ist_word_json(p)
    )
    print(f"Verarbeite {len(json_files)} JSON-Samples...")

    results: list[dict] = []
    # Kein Dateiname auf der Konsole: Steuerbelege heissen nach Menschen,
    # Konten und Arbeitgebern, und die Laufausgabe wird kopiert und
    # weitergegeben. Die laufende Nummer genuegt, um den Fortschritt zu
    # sehen; welches Dokument haengt, sagt `make dokumente` — lokal.
    for nr, jp in enumerate(json_files, start=1):
        print(f"  [{nr:>2}/{len(json_files)}]", end=" ", flush=True)
        try:
            res = process_json_sample(jp, model=args.model)
            print(f"→ {res['status']} ({res.get('belegtyp','?')})")
        except Exception as e:
            res = {"pdf_name": jp.stem, "status": "pipeline_error", "error": str(e)[:200], "fields": []}
            print(f"→ ERROR: {e}")
        results.append(res)

    # field_exception-Zähler aggregieren, dann den internen Schlüssel aus den
    # Ergebnissen entfernen (nicht ins _results_full.json schreiben — Downstream
    # erwartet nur fachliche Felder).
    fe_applied = 0
    fe_skipped = 0
    for r in results:
        fe = r.pop("_field_exceptions", None)
        if fe:
            fe_applied += fe.get("applied", 0)
            fe_skipped += fe.get("skipped", 0)

    out_path = args.out if args.out else samples_dir / "_results_full.json"
    out_path.write_text(json.dumps(results, indent=2, ensure_ascii=False))
    print(f"\nGeschrieben: {out_path}")

    n_ok = sum(1 for r in results if r["status"] == "ok")
    print(f"OK: {n_ok}/{len(results)}")
    print(
        f"field_exceptions: {fe_applied} angewandt, {fe_skipped} übersprungen "
        f"(stale)"
    )


if __name__ == "__main__":
    main()
