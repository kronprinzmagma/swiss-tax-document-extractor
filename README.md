# swiss-tax-document-extractor

![Status](https://img.shields.io/badge/status-working--prototype-2ea44f?style=flat)
![Privacy](https://img.shields.io/badge/privacy-local--first-informational?style=flat)
![License](https://img.shields.io/badge/license-MIT-lightgrey?style=flat)

Turn a folder of Swiss tax documents (PDF) into one transfer table for the Canton of Zurich tax return — every amount anchored to the exact words on the original page, nothing leaving your machine.

> **About this repository:** Public, sanitised snapshot of a private working repo that the author uses for a real tax return in the Canton of Zurich. The code is identical; this repo ships without the private evaluation corpus of anonymised real documents and without the planning notes. Synthetic test documents for every supported type can be generated locally with `python -m evals.generate`.

<!-- HERO-VISUAL — bitte hier ein Bild einfuegen (staerkster visueller Hebel):
     Screenshot der Review-Oberflaeche oder der Uebertragungstabelle mit
     SYNTHETISCHEN Daten (Konfidenz-Faerbung, Bildausschnitt mit rot
     umrandetem Wert, Summe je Ziffer).
     1. Bild ablegen unter  docs/demo-review.png  (Pfad zu WHITELIST_FILES in
        scripts/sync_to_public.py hinzufuegen).
     2. Folgende Zeile einkommentieren:
![Review UI with synthetic data](docs/demo-review.png)
-->

---

## The Problem

Filing a Swiss tax return means collecting a stack of PDFs — salary certificates, bank interest statements, health-insurance premium statements, pillar 3a confirmations, donation receipts, childcare invoices — and typing the right number from each one into the right field of the cantonal software (ZHprivateTax) or a spreadsheet for the tax advisor.

Three things go wrong every year:

- **Manual transfer is slow and error-prone.** Twenty documents, one number each, and one of them is the gross invoice amount instead of your own share.
- **Trust.** A number extracted by a language model is worthless unless you can check it against the original page in seconds.
- **Forgetting.** The bank that sent a statement last year but not this year is invisible — until the tax office asks.

---

## The Solution

One run, one table, every value with an anchor back to the original document.

```
belege/*.pdf  ->  extraction  ->  steuer_uebertragung.md   (the transfer table)
                              ->  nachweis_anhang.md       (page + bounding box + snippet per value)
                              ->  steuer_extraktion_debug.md (everything, for tool development)
```

- **`steuer_uebertragung.md`** — the transfer table for ZHprivateTax or the tax advisor, in three sections: automatically transferable, check manually, out of scope. Columns: tax area, ZHprivateTax field number, person/role, issuer, description, amount CHF, year, source, status.
- **`nachweis_anhang.md`** — for every transferred position: page, bounding box, verbatim snippet, plausibility findings. Lets you verify each number word by word in the original PDF.
- **`steuer_extraktion_debug.md`** — all fields per document including confidence and anchors; useful for development and evaluation, not for filing.

A local **review UI** (`make review`, bound to `127.0.0.1` only, write access protected by a per-start token) is where the human work happens. Confirmed or corrected values are written to `korrekturen.json` and become expected values that every later run is checked against — the correction loop.

It opens on a **document overview**: one row per PDF in the folder, with the state spelled out as a word as well as a colour — *checked · open · nothing found · excluded · not read in*. The list starts from the **folder**, not from the extraction result; only that way does "it is lying there and was never read" exist as a state at all. The counters double as filters, as does the document type.

The right-hand pane holds exactly one document: its positions, the sum per field number as a cross-check against the total printed on the document, the page image with clickable words, and "next open one →".

**Several positions of the same kind per document.** A mortgage statement lists three mortgages; extraction finds one. `＋ Position` records the others. Each carries its running number in the key (`<document>|<target value>#2`), and a short distinguishing label ("fixed-rate 0.95 %") keeps them apart. Numbers once assigned never shift — if they did, a confirmed amount would point at a different mortgage after a deletion, and the check would still report *matches*.

**Speaking filenames.** Once issuer and document type are settled, the UI proposes a name and renames the original at the press of a button:

```
2025_tax_statement4.9.2026155454.pdf
   ->  BANK-U - Vermoegensausweis Zinsausweis - Erika Muster 2025.pdf
```

The document type comes from the target values actually **found**, not from the document class: a bank statement showing both a balance and interest income is both. The filename is also the key the whole review hangs on, so the word-level JSON, the extraction result and every confirmed value move with it in one step. Every rename is journalled and reversible.

---

## Design principles

**Rule before model.** Standardised or clearly labelled documents (the federal salary certificate *Form 11*, the pillar 3a certificate *Form 21*) are handled by deterministic layout extractors that work on word geometry — amount column, cross-check of gross minus deductions equals net. The language model is reserved for free-form documents. Every value carries its provenance (`regel` = rule, `modell` = model); only rule values with an anchor and no plausibility finding reach the confidence level *certain*.

**Every value has a text anchor.** Extraction works on word-level bounding boxes from `pdfplumber`. A value without an anchor is marked `unverified`; a computed value is marked `derived`, never presented as anchored.

**The field contract decides what appears in the table.** `extractors/zielwerte.py` is the single source for which value belongs to which ZHprivateTax field number. A test enforces that schema, transfer map and contract stay aligned — what the contract does not know does not appear.

**Nothing gets forgotten.** Issuers are tracked year over year (`aussteller.json`); an issuer present last year but missing in the current run raises a warning. A separate completeness check reports what should be there but is not.

**Silence never means delete.** The review is hours of work and the one part no run can reproduce. A copy of `korrekturen.json` is written to a history folder before every save, and the restore point taken at session start is marked and never rotated away. Withdrawing a value sets a tombstone and lifting a deletion writes an explicit `false`, because absence does not survive a merge — an older backup would simply resurrect what the human had removed. `make staende` lists the earlier states, `make zurueckrollen` restores one (backing up the current one first), and it refuses to run while a review is open, since the page in the browser would write the old state straight back.

---

## Privacy Architecture

- **PDFs never leave the machine.** The default extraction engine is a local Ollama model (`qwen2.5:7b-instruct-q4_K_M`) with constrained JSON output. The productive run refuses to start with a cloud engine.
- **Cloud is opt-in only, and only after redaction.** `--engine cloud` (Claude Haiku) is never the default and runs a regex redaction pass (names, AHV numbers, IBANs, addresses, phone numbers, amounts) before any call.
- **The evaluation corpus is anonymised before it is ever read by a test.** `scripts/anonymize_belege.py` replaces persons, institutions, IBANs, addresses and phone numbers with stable markers (`BANK-A`, `KK-B`, …) while preserving bounding boxes; a privacy gate scans the result and fails on any residue.
- **One pattern library.** `extractors/pii_patterns.py` is the single source of truth for PII detection — the anonymiser, the privacy gate and the public-sync verifier of this repository all import it.
- **OCR fallback stays local** (`ocrmypdf` + `tesseract`) for scanned PDFs.

---

## Quality gate

`scripts/grade_run.py` is the only definition of done for a sample run. It exits 0 only when all 20 checks pass, among them:

- no PII residue in the anonymised sample folder or in the transfer table
- 100 % of extracted values anchor-validated
- every focus target value appears in the transfer table
- automatic rows carry no `manual_review:*` markers; manual rows are counted, documented and machine-readable
- no wealth position that is merely a duplicated interest amount
- person roles restricted to the allowed set; person-sensitive areas never auto-transferred with an unknown person
- issuers appear only as stable anonymised markers
- confirmed values from the review UI are still hit — the one check that verifies the *number*, not just the structure

---

## Supported document types

Salary certificate (Form 11) · bank interest statement · securities register / custody statement · health-insurance premium statement · pillar 3a certificate (Form 21) · donation receipt · professional expenses / further education · childcare (daycare, after-school) · mortgage interest confirmation · property maintenance · medical expenses (health-insurance cost sharing)

---

## Setup

```bash
# with uv
uv sync --extra dev

# or with a plain venv
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# local extraction model (name must match DEFAULT_MODEL in extractors/llm_extract.py)
ollama pull qwen2.5:7b-instruct-q4_K_M

# synthetic test documents
python -m evals.generate
```

Optional: `pip install -e ".[ocr]"` for the scan fallback, `".[cloud]"` for the opt-in cloud engine.

---

## Usage

```bash
# productive local run: real PDFs in ./belege/ -> transfer table in ./output/latest/
uv run python scripts/run_productive_local.py --input belege --output output/latest

# review and correct values in the browser (localhost only)
make review

# rebuild the transfer table from confirmed corrections without re-extracting
make tabelle

# is the return ready to be filled in?  (completeness against the field contract)
make bereit

# give the original PDFs speaking filenames (JETZT=1 actually performs it)
make umbenennen

# earlier states of the review, and a way back to one of them
make staende
make zurueckrollen AUF=...

# sample path on an anonymised corpus + the 20-check grader
make samples
make grade
```

`make help` lists every target with a short description.

---

## Tests

```bash
uv run pytest            # deterministic suite, no Ollama required
uv run pytest -m llm_full   # full extraction evaluation against Ollama
```

The evaluation harness generates synthetic PDFs per document type with known values (`evals/expected/`) and measures field recall, precision, anchor validity and confidence calibration.

---

## Tech Stack

| Choice | Why |
|--------|-----|
| **Python 3.11+** | Rich PDF ecosystem |
| **pdfplumber** | Text *with* per-word bounding boxes — the anchor requirement for free |
| **ocrmypdf + tesseract** | Local, idempotent fallback for scanned PDFs |
| **Ollama + qwen2.5:7b-instruct-q4_K_M** | Default extractor; local, structured JSON output via format constraint |
| **pydantic v2** | One schema per document type; validates and serialises extraction results |
| **rich** | CLI tables with confidence colouring |
| **pytest** | Evaluation harness against synthetic fixtures |
| **openpyxl** | Spreadsheet export of the tax summary |
| **Claude Haiku (opt-in)** | Only with `--engine cloud` and only after regex redaction — never the default |

**Deliberately not used:** LangChain / LlamaIndex (overkill for schema-constrained extraction), cloud OCR (privacy), vector databases (documents are categorisable by issuer pattern, no semantic search needed).

Code comments and user-facing output are in German (Swiss spelling) because the target software and the documents are.

---

## License

Licensed under the [MIT License](LICENSE).

Contributions welcome — open an issue or pull request.
