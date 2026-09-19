"""LLM-Fallback-Klassifikator (D-C1..C6, CLS-02 step 3 — Plan 03-06 Wave 5).

Wird von :func:`extractors.classifier.classify` aufgerufen, wenn alle 11
Header-Regex-Scores == 0 sind (keine regelbasierte Erkennung möglich).
Macht EINEN zusätzlichen Qwen-Call mit
:class:`extractors.schema.ClassifierFallbackResult` als ``format=``-Schema —
XGrammar erzwingt das geschlossene ``Belegtyp``-Literal (kein freier Text).

Cascade-Position (D-C1):

* CLS-02 step 1 (aussteller.json-Lookup) — Phase-3-Backlog, NICHT aktiv.
* CLS-02 step 2 (Header-Regex) — Phase 2 + 3 productive in
  :mod:`extractors.classifier`.
* CLS-02 step 3 (LLM-Fallback) — DIESES MODUL.

Schwellwert (D-C4):

* ``confidence ≥ CLASSIFIER_FALLBACK_THRESHOLD`` (= 0.6) UND
  ``belegtyp != "unknown"`` ⇒ Tuple ``(belegtyp, None)``.
* sonst ⇒ ``("unknown", ReasonCode.UNSUPPORTED_TYPE)``.

Pitfall 7 (RESEARCH §5): ``confidence`` aus diesem Modul fliesst NICHT in
``compose_confidence`` der Feld-Konfidenzen ein — es ist ein reines
Threshold-Signal für die Klassifikator-Entscheidung. LLM-Self-Confidence
bleibt nach D-B5 für Feld-Werte ignoriert.

Text-Ausschnitt (D-C6, RESEARCH §5): nur erste
:data:`_HEAD_CHARS` (= 1'000) Zeichen des joined-text werden an das LLM
geschickt — Klassifikation braucht keinen Volltext, spart Latenz und
Token-Budget.

Graceful Degradation: jede Ollama-Exception (Netzwerk, Schema-Verletzung)
führt zu ``("unknown", UNSUPPORTED_TYPE)`` — nie Crash. Der Caller in
``classifier.classify`` ist Pipeline-kritisch und darf nicht propagieren.
"""
from __future__ import annotations

from extractors.llm_extract import DEFAULT_MODEL
from extractors.schema import Belegtyp, ClassifierFallbackResult, ReasonCode
from extractors.tokenize import TaggedWord

#: Schwellwert für die Klassifikator-Entscheidung (D-C4). Werte unterhalb
#: dieser Schranke werden als „nicht-vertrauenswürdig" verworfen und auf
#: ``UNSUPPORTED_TYPE`` gemappt — defensives Verhalten gegen Halluzinationen.
CLASSIFIER_FALLBACK_THRESHOLD = 0.6

#: Anzahl Zeichen des joined-text, die an das LLM geschickt werden. 1'000
#: Zeichen decken typische erste Seiten ab; mehr lohnt sich für reine
#: Klassifikation nicht (D-C6, RESEARCH §5).
_HEAD_CHARS = 1000

_PROMPT = """Klassifiziere dieses Dokument als einen der folgenden 11 Schweizer Steuer-Belegtypen:

- lohnausweis: Form 11 Lohnausweis vom Arbeitgeber (Bruttolohn, AHV, BVG, Nettolohn).
- bank_zinsausweis: Bank-Steuerausweis mit Zinserträgen (Form 340).
- wertschriftenverzeichnis: Depot-/Wertschriften-Auszug mit Bestand 31.12.
- kk_praemienbescheinigung: Krankenkassen-Prämienbestätigung (KVG-Grundversicherung).
- saeule_3a: Säule-3a-Vorsorge-Bescheinigung (gebundene Vorsorge).
- spenden: Spendenbescheinigung einer gemeinnützigen Organisation.
- berufsauslagen: Weiterbildungs-/Kurskosten-Bestätigung.
- kinderbetreuung: Kita-/Hort-/Tagesfamilie-Betreuungs-Rechnung.
- hypothek_zinsbestaetigung: Hypotheken-Schuldzinsen / Schuldsaldo per 31.12.
- liegenschaftsunterhalt: Handwerker-Rechnung Liegenschaftsunterhalt.
- krankheitskosten: Arzt-/Apotheken-/Spital-/Therapie-Rechnung.

Bei Unsicherheit → "unknown" mit niedriger confidence (< 0.6).
Nur klassifizieren, NICHT extrahieren.

TEXT (erste 1000 Zeichen):
{text}
"""


def classify_via_llm(
    words: list[TaggedWord] | list[dict],
    model: str = DEFAULT_MODEL,
) -> tuple[Belegtyp, ReasonCode | None]:
    """Ruft Qwen mit :class:`ClassifierFallbackResult`-Schema und mappt das Resultat.

    Args:
        words: Wort-Liste aus :func:`extractors.pdf_reader.read_pdf` (Dicts
            mit ``"text"``-Key) oder :class:`extractors.tokenize.TaggedWord`-
            Liste. Die Funktion liest defensiv via ``.text`` bzw. ``["text"]``.
        model: Ollama-Modell-Tag (Default :data:`extractors.llm_extract.DEFAULT_MODEL`).

    Returns:
        * ``(belegtyp, None)`` bei ``confidence ≥ 0.6`` und ``belegtyp != "unknown"``.
        * ``("unknown", ReasonCode.UNSUPPORTED_TYPE)`` sonst (zu niedrige
          Konfidenz, explizit ``"unknown"`` vom LLM, oder Ollama-Exception).

    Niemals raisen — Caller in ``classifier.classify`` ist Pipeline-kritisch.
    """
    try:
        # Lazy-Import: das Modul soll auch importierbar sein, wenn ``ollama``
        # (noch) nicht installiert ist — der Crash kommt erst beim Aufruf.
        import ollama
    except Exception:
        return ("unknown", ReasonCode.UNSUPPORTED_TYPE)

    def _word_text(w: object) -> str:
        if isinstance(w, dict):
            return str(w.get("text", ""))
        return str(getattr(w, "text", ""))

    text = " ".join(_word_text(w) for w in words)[:_HEAD_CHARS]
    prompt = _PROMPT.format(text=text)

    try:
        response = ollama.chat(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            format=ClassifierFallbackResult.model_json_schema(),
            options={"temperature": 0, "num_ctx": 4096},
        )
        msg = response["message"] if isinstance(response, dict) else response.message
        content = msg["content"] if isinstance(msg, dict) else msg.content
        result = ClassifierFallbackResult.model_validate_json(content)
    except Exception:
        return ("unknown", ReasonCode.UNSUPPORTED_TYPE)

    # Pitfall 7: explizit ``"unknown"`` und Threshold-Check.
    if result.belegtyp == "unknown" or result.confidence < CLASSIFIER_FALLBACK_THRESHOLD:
        return ("unknown", ReasonCode.UNSUPPORTED_TYPE)
    return (result.belegtyp, None)
