# Makefile — Pipeline-Targets fuer steuer-extraktor.
#
# Alle Targets setzen PYTHONPATH=. (flache Repo-Struktur, siehe CLAUDE.md).
# PY zeigt auf das venv-Python falls vorhanden, sonst auf python3.

PY := $(shell [ -x .venv/bin/python ] && echo .venv/bin/python || echo python3)
SAMPLES := evals/samples_real_2022

export PYTHONPATH := .

.PHONY: help samples grade test test-all productive tabelle review-datei korrekturen-neuste dokumente dokumente-eines bereit felder bezug staende zurueckrollen korrekturen-zurueck korrekturen-zurueck-pruefen aufstellung check-aufstellung basis eval-quality eval-compare review korrekturen reextract secrets-vorschlag korrektur-diagnose

help:
	@echo "Targets:"
	@echo "  samples           - Pipeline gegen $(SAMPLES): anonymize -> process -> build -> matrix"
	@echo "  aufstellung       - Steueraufstellung-xlsx gegen $(SAMPLES) (build_steueraufstellung.py)"
	@echo "  basis             - Basis-Tabelle (1 Zeile pro Dokument) gegen $(SAMPLES) (build_basis_tabelle.py)"
	@echo "  check-aufstellung - Privacy-tauglicher xlsx-Validator gegen $(SAMPLES) (Exit 0/1)"
	@echo "  grade             - 20-Check-Grader gegen $(SAMPLES) (Done-Kriterium)"
	@echo "  eval-quality      - strukturelle Qualitaetsmetriken gegen $(SAMPLES) -> eval_quality.md (truth-frei, Report-Tool)"
	@echo "  eval-compare      - Modellvergleich: make eval-compare OTHER=<andere>/_results_full.json -> eval_compare.md"
	@echo "  test        - deterministische Eval-Suite (ohne Ollama)"
	@echo "  test-all    - volle Eval-Suite inkl. LLM-Tests (Ollama noetig)"
	@echo "  productive  - End-to-End gegen echte Belege (run_productive_local.py)"
	@echo "  review      - Review mit lokalem Server (speichert selbst)"
	@echo "  review-datei- Review als blosse HTML-Datei (CSV-Rueckweg)"
	@echo "  korrekturen - ausgefuellte korrektur.csv zurueckspielen"
	@echo "  korrekturen-neuste - die juengste korrektur*.csv einspielen"
	@echo "  tabelle     - Uebertragungstabelle neu bauen, ohne Extraktion"
	@echo "  dokumente   - welches PDF ist wo haengengeblieben?"
	@echo "  dokumente-eines DOK=... - warum fehlt genau dieses Dokument?"
	@echo "  bereit      - kann die Steuererklaerung ausgefuellt werden?"
	@echo "  felder      - jedes Dokument, jedes Feld, sein Zustand"
	@echo "  bezug       - finden die Korrekturen noch ihre Zeilen?"
	@echo "  staende     - frühere Staende der Durchsicht auflisten"
	@echo "  zurueckrollen AUF=... - auf einen frueheren Stand zurueck"
	@echo "  korrekturen-zurueck - Durchsicht aus allen Quellen wiederherstellen"

# Voller Sample-Lauf: Anonymisierung -> Extraktion -> Outputs -> Fokus-Matrix.
# WICHTIG: --output $(SAMPLES) — ohne das schreibt der Anonymizer in seinen
# Default evals/samples_real und die Folgeschritte laufen auf alten Daten.
samples:
	$(PY) scripts/anonymize_belege.py --output $(SAMPLES)
	$(PY) scripts/process_samples_full.py --samples $(SAMPLES)
	$(PY) scripts/build_tax_output.py --samples $(SAMPLES)
	$(PY) scripts/build_table.py --samples $(SAMPLES)
	$(PY) scripts/build_steueraufstellung.py --samples $(SAMPLES)
	$(PY) scripts/focus_matrix.py --samples $(SAMPLES)

# Steueraufstellung-xlsx eigenstaendig (setzt _results_full.json voraus).
aufstellung:
	$(PY) scripts/build_steueraufstellung.py --samples $(SAMPLES)

# Basis-Tabelle (1 Zeile pro Dokument: Typ+Name+Wert; setzt _results_full.json voraus).
basis:
	$(PY) scripts/build_basis_tabelle.py --samples $(SAMPLES)

# Bank-Matrix: Status der 5 Bank-Pflichtfelder pro Bankbeleg (nur Codes).
bank-matrix:
	$(PY) scripts/bank_matrix.py --samples $(SAMPLES)

# Privacy-tauglicher xlsx-Validator (Exit 0/1). --file ueberschreibbar:
#   make check-aufstellung FILE=/pfad/zur/steueraufstellung.xlsx
FILE ?= $(SAMPLES)/steueraufstellung.xlsx
check-aufstellung:
	$(PY) scripts/check_aufstellung.py --file $(FILE)

# Done-Kriterium: 20-Check-Grader (Exit 0 nur bei allen PASS).
grade:
	$(PY) scripts/grade_run.py --samples $(SAMPLES)

# Eval-Qualitaet: strukturelle Metriken (truth-frei, Report-Tool, Default Exit 0).
# Optional fail-closed: make eval-quality MIN_COV=0.9
MIN_COV ?=
eval-quality:
	$(PY) scripts/eval_quality.py --samples $(SAMPLES) $(if $(MIN_COV),--min-pflicht-coverage $(MIN_COV),)

# Modellvergleich: OTHER zeigt auf das _results_full.json des Vergleichslaufs.
#   make eval-compare SAMPLES=evals/samples_real_2023 OTHER=evals/samples_real_2022/_results_full.json
OTHER ?=
eval-compare:
	$(PY) scripts/eval_quality.py --samples $(SAMPLES) --compare $(OTHER)

# Deterministische Teilsuite (schnell, kein Ollama).
test:
	$(PY) -m pytest evals/ -q -m "not llm_full" --ignore=evals/test_extraction.py

# Volle Suite inkl. LLM-Eval (Ollama + Modell noetig, ~10-15 min).
test-all:
	$(PY) -m pytest evals/ -m ""

# Produktiver End-to-End-Lauf gegen echte Belege.
productive:
	$(PY) scripts/run_productive_local.py

# Lokale Review-Oberflaeche zum Bestaetigen/Korrigieren (oeffnet im Browser).
# Schreibt nach output/ — die Seite enthaelt echte Werte und ist gitignored.
# PDFDIR liefert Ausschnitte und Links zum Original — ohne ihn nur Text.
PDFDIR ?= belege
PORT ?= 8765
review:
	$(PY) scripts/review_server.py --samples output/latest/json --pdf-dir $(PDFDIR) --port $(PORT)

# Die Seite als blosse Datei — ohne Server, ohne Speichern. Der Rueckweg
# laeuft dann ueber CSV-Export und `make korrekturen`.
review-datei:
	$(PY) scripts/build_review_html.py --samples output/latest/json --out output/latest/review.html --pdf-dir $(PDFDIR)
	@echo "Oeffnen:  open output/latest/review.html"

# Einzelnes Dokument neu extrahieren, optional mit vorgegebenem Belegtyp:
#   make reextract PDF="Auszug.pdf" TYP=kk_praemienbescheinigung
PDF ?=
TYP ?=
reextract:
	$(PY) scripts/reextract.py --samples output/latest/json --pdf "$(PDF)" $(if $(TYP),--belegtyp $(TYP),)

# Vorschlaege fuer die Secrets-Datei aus den eigenen Dokumenten erzeugen.
# Schreibt eine Vorschlagsdatei, nie direkt in die echte.
secrets-vorschlag:
	$(PY) scripts/secrets_vorschlag.py --samples output/latest/json

# Warum kommen die eigenen Korrekturen nicht an? Nur Zahlen, teilbar.
korrektur-diagnose:
	$(PY) scripts/korrekturen_diagnose.py --samples output/latest/json

# Ausgefuellte korrektur.csv zurueckspielen.
KORR ?= output/latest/korrektur.csv
korrekturen:
	$(PY) scripts/apply_korrekturen.py --file $(KORR)

# Die zuletzt exportierte Korrektur-CSV einspielen, ohne ihren Namen zu kennen.
#
# Der Browser nummeriert Downloads durch ("korrektur 2.csv", "korrektur 3.csv")
# und der Name mit Leerzeichen laesst sich schlecht tippen. Hier sucht die
# Shell die juengste selbst — der Ordner gehoert dem Menschen, nicht dem Agenten.
korrekturen-neuste:
	@f=$$(ls -t $(LATEST)/[Kk]orrektur*.csv 2>/dev/null | head -1); \
	if [ -z "$$f" ]; then \
		echo "Keine korrektur*.csv in $(LATEST)/ gefunden."; exit 1; \
	fi; \
	echo "Spiele ein: $$f"; \
	$(PY) scripts/apply_korrekturen.py --file "$$f"

# Uebertragungstabelle neu bauen, OHNE erneute Extraktion.
#
# Der Weg nach `make korrekturen`: liest das vorhandene _results_full.json,
# wendet die bestaetigten Korrekturen an und schreibt steuer_uebertragung.md,
# nachweis_anhang.md und die xlsx neu. Sekunden statt einer halben Stunde —
# `make productive` waere hier falsch, das laesst das Sprachmodell erneut
# ueber alle Dokumente laufen, obwohl sich nur die Korrekturen geaendert
# haben.
LATEST ?= output/latest
tabelle:
	$(PY) scripts/build_tax_output.py --samples $(LATEST)/json
	-$(PY) scripts/build_steueraufstellung.py --samples $(LATEST)/json
	@for f in steuer_uebertragung.md nachweis_anhang.md steueraufstellung.xlsx; do \
		if [ -f "$(LATEST)/json/$$f" ]; then cp "$(LATEST)/json/$$f" "$(LATEST)/$$f"; fi; \
	done
	@echo "Neu gebaut: $(LATEST)/steuer_uebertragung.md"

# Welches Dokument ist wo haengengeblieben? Der Weg PDF -> JSON -> Ergebnis ->
# Zeile, pro Dokument. Zuerst nur Zahlen (teilbar), danach die Namen (lokal).
dokumente:
	$(PY) scripts/dokumente_check.py --input $(PDFDIR) --samples $(LATEST)/json

# Ein einzelnes Dokument und sein ganzer Weg:
#   make dokumente-eines DOK="Kontoauszug - 2025"
DOK ?=
dokumente-eines:
	@if [ -z '$(DOK)' ]; then echo 'Aufruf: make dokumente-eines DOK="Teil des Dateinamens"'; exit 1; fi
	$(PY) scripts/dokumente_check.py --input $(PDFDIR) --samples $(LATEST)/json --dokument '$(DOK)'

# Die eine Frage, die zaehlt: kann ich die Steuererklaerung damit ausfuellen?
# Prueft den ganzen Weg und sagt, was noch offen ist. Exit 0 nur, wenn nichts.
bereit:
	$(PY) scripts/bereitschaft.py --input $(PDFDIR) --samples $(LATEST)/json

# Die Durchsicht aus allen noch vorhandenen Quellen zurueckholen: exportierte
# CSVs, beide korrekturen.json, Sicherungen. Loescht nichts, fuehrt zusammen.
korrekturen-zurueck-pruefen:
	$(PY) scripts/korrekturen_wiederherstellen.py --samples $(LATEST)/json

korrekturen-zurueck:
	$(PY) scripts/korrekturen_wiederherstellen.py --samples $(LATEST)/json --schreiben

# Die feine Auflösung: pro Dokument jedes Sollfeld aus dem Feldvertrag mit
# Wert, Anker, Person, Herkunft und Zustand — auch die, die fehlen.
felder:
	$(PY) scripts/felder_matrix.py --input $(PDFDIR) --samples $(LATEST)/json --md $(LATEST)/felder.md

# Vor und nach jeder Umbenennung: finden die gespeicherten Korrekturen noch
# ihre Zeilen? Die Zahl darf nie sinken.
bezug:
	$(PY) scripts/bezug_pruefen.py --samples $(LATEST)/json

# Frühere Staende der Durchsicht auflisten und einen davon zurueckholen.
# Der jetzige Stand wird dabei zuerst gesichert — auch das Zurueckrollen
# ist umkehrbar.
AUF ?=
staende:
	$(PY) scripts/zurueckrollen.py --samples $(LATEST)/json

zurueckrollen:
	@if [ -z '$(AUF)' ]; then echo 'Aufruf: make zurueckrollen AUF=<Zeitpunkt aus `make staende`>'; exit 1; fi
	$(PY) scripts/zurueckrollen.py --samples $(LATEST)/json --auf '$(AUF)'
