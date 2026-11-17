# README visual provenance

Asset: [demo-review.png](demo-review.png)

Captured from public source commit `2b3be77b4008825e16084cafc094e5bf87463821` on 2026-11-17. This is a staged demonstration with exclusively synthetic data, not evidence of a live AI run or measured model performance. The visible application/CLI content is based on existing code, not an invented interface.

## Method

The original baue_html function generated the review page from fixture rows. The existing “nach Beleg” control selected the document grouping. The image captures the complete document section, including editable values, confidence labels and separate amount/person confirmation controls. _crop_datauri rendered genuine PDF source-line crops from the synthetic PDF with its original red bounding-box logic. The title and framing are editorial; no review UI code was altered.

## Synthetic inputs and limits

Fictional BANK A and Alex Beispiel, test account DEMO-001, tax year 2025. Saldo per 31.12.2025: CHF 24,850.00 (field 30.1); Bruttoertrag ohne Verrechnungssteuer: CHF 124.25 (field 4). Word boxes were obtained with pdfplumber. Rows are staged as model-origin, probably correct, awaiting human confirmation. No Ollama extraction, tax filing, full-return validation or live database write is implied. The image demonstrates traceability and review, not extraction accuracy.

## Source references

- [scripts/build_review_html.py](../scripts/build_review_html.py)
- [extractors/zielwerte.py](../extractors/zielwerte.py)
- [scripts/review_server.py](../scripts/review_server.py)
- [extractors/ablage.py](../extractors/ablage.py)

## Maintenance

The PNG is a static documentation asset; no production code or dependency was changed. Its outer framing was authored in HTML/CSS and captured with Chromium, without generative image synthesis. The demo label is embedded in the pixels as well as the README caption. Keep both when reusing it.

This change targets only this public mirror. The private-to-public synchronisation may replace README.md and may omit or remove public-only assets. Durable preservation would require a separately authorised change to the private sync source/allowlist; none was made here. Recheck the README and asset after future syncs.
