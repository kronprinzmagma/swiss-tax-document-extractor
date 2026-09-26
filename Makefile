# Makefile — Pipeline-Targets fuer steuer-extraktor.
#
# Alle Targets setzen PYTHONPATH=. (flache Repo-Struktur, siehe CLAUDE.md).
# PY zeigt auf das venv-Python falls vorhanden, sonst auf python3.

PY := $(shell [ -x .venv/bin/python ] && echo .venv/bin/python || echo python3)
SAMPLES := evals/samples_real_2022

export PYTHONPATH := .

.PHONY: help samples grade test test-all productive tabelle review-datei korrekturen-neuste dokumente dokumente-eines bereit felder bezug staende zurueckrollen korrekturen-zurueck korrekturen-zurueck-pruefen aufstellung check-aufstellung basis eval-quality eval-compare review korrekturen reextract secrets-vorschlag korrektur-diagnose umbenennen umbenennen-zurueck aussortieren aussortieren-zurueck verwaiste vergleich unterschied roentgen einfrieren staende-liste

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
	@echo "  umbenennen  - Originalbelege sprechend benennen (JETZT=1 fuehrt aus)"
	@echo "  umbenennen-zurueck - die letzten Umbenennungen rueckgaengig machen"
	@echo "  aussortieren - abgehakte Dokumente aus dem Eingangsordner nehmen (JETZT=1)"
	@echo "  aussortieren-zurueck - aussortierte Dokumente zurueckholen"
	@echo "  verwaiste   - Eintraege ohne Dokument finden (JETZT=1 entfernt)"
	@echo "  vergleich ALT=<commit> - hat ein Umbau die Zeilen veraendert?"
	@echo ""
	@echo "  --- Ablage: gepruefte Werte, gespeichert statt errechnet ---"
	@echo "  ablage-uebernehmen - einmalig: gepruefte Werte in die Ablage"
	@echo "  ablage-stand    - was steht in der Ablage? (teilbar)"
	@echo "  sicherung       - Ablage + Durchsicht datiert zur Seite legen"
	@echo "  einfrieren      - Stand als feste Tabelle festhalten"
	@echo "  staende-liste   - welche Staende gibt es"
	@echo "  staende-vergleich ALT=.. NEU=.. - zwei Staende vergleichen"
	@echo "  streichen ZIELWERT=.. - maschinelle Position zuruecknehmen"
	@echo "  roentgen        - jetziger Stand in Zahlen (teilbar)"
	@echo ""
	@echo "  --- Uebertragung ins Formular ---"
	@echo "  extension-daten - Werte fuer die Brave-Erweiterung erzeugen"
	@echo "  esteuerauszug   - haben die Dokumente einen eCH-0196-Barcode?"

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

# Was genau hat sich zu einem frueheren Stand geaendert — Feldnamen, keine
# Werte:  make unterschied AUF=20260923-143425
unterschied:
	@if [ -z '$(AUF)' ]; then echo 'Aufruf: make unterschied AUF=<Zeitpunkt aus `make staende`>'; exit 1; fi
	$(PY) scripts/zurueckrollen.py --samples $(LATEST)/json --felder '$(AUF)'

zurueckrollen:
	@if [ -z '$(AUF)' ]; then echo 'Aufruf: make zurueckrollen AUF=<Zeitpunkt aus `make staende`>'; exit 1; fi
	$(PY) scripts/zurueckrollen.py --samples $(LATEST)/json --auf '$(AUF)'

# Originalbelege sprechend benennen, sobald Aussteller und Dokumenttyp
# feststehen:  "BANK-U - Vermoegensausweis Zinsausweis - Heinz Hermann 2025.pdf"
#
# Ohne JETZT=1 wird nur gezeigt, was geschaehe. Die Schluessel der ganzen
# Durchsicht haengen am Dateinamen und wandern mit; jede Umbenennung steht im
# Journal und laesst sich zurueckdrehen.
JETZT ?=
umbenennen:
	$(PY) scripts/umbenennen.py --input $(PDFDIR) --samples $(LATEST)/json $(if $(JETZT),--jetzt,)

umbenennen-zurueck:
	$(PY) scripts/umbenennen.py --input $(PDFDIR) --samples $(LATEST)/json --zurueck

# Als irrelevant abgehakte Dokumente aus dem Eingangsordner nehmen — sie
# werden dann gar nicht mehr eingelesen. Sie bleiben in der Uebersicht
# sichtbar (Zustand "ausgeschlossen") und liegen einen Ordner tiefer, nicht
# im Muell.
aussortieren:
	$(PY) scripts/aussortieren.py --input $(PDFDIR) --samples $(LATEST)/json $(if $(JETZT),--jetzt,)

aussortieren-zurueck:
	$(PY) scripts/aussortieren.py --input $(PDFDIR) --samples $(LATEST)/json --zurueck

# Eintraege, deren Dokument es nicht gibt — meist Reste einer Umbenennung.
# Erst zuordnen (ueber das Umbenennungs-Journal), dann zeigen, und nur mit
# JETZT=1 entfernen. Vorher geht eine Kopie in den Verlauf.
DOPPELTE ?=
NUR ?=
verwaiste:
	$(PY) scripts/verwaiste.py --input $(PDFDIR) --samples $(LATEST)/json $(if $(JETZT),--jetzt,) $(if $(DOPPELTE),--doppelte,) $(if $(NUR),--nur "$(NUR)",)

# Hat ein Umbau die Zeilen veraendert — oder sieht es nur anders aus?
# Baut die Zeilen mit dem Code eines frueheren Commits und mit dem jetzigen,
# beide gegen dieselben Daten. Ausgabe nur Zahlen und Zielwerte.
#   make vergleich ALT=45a89f2
ALT ?=
vergleich:
	@if [ -z '$(ALT)' ]; then echo 'Aufruf: make vergleich ALT=<commit>'; exit 1; fi
	$(PY) scripts/codevergleich.py --samples $(LATEST)/json --alt '$(ALT)'

# Der jetzige Stand in Zahlen und Zielwerten — ohne Namen, ohne Betraege.
roentgen:
	$(PY) scripts/codevergleich.py --samples $(LATEST)/json --zeigen

# Den jetzigen Stand als feste Tabelle festhalten. Diese Dateien werden von
# keinem Lauf angefasst und von keiner Umbenennung verschoben.
streichen:
	$(PY) scripts/streichen.py --samples $(LATEST)/json \
	  --zielwert "$(ZIELWERT)" --grund "$(GRUND)" \
	  $(if $(PROBE),--probe,) $(if $(AUCH_EIGENE),--auch-eigene,)

ablage-uebernehmen:
	$(PY) scripts/ablage_uebernehmen.py --samples $(LATEST)/json \
	  $(if $(PROBE),--probe,)

einfrieren:
	$(PY) scripts/einfrieren.py --samples $(LATEST)/json

staende-liste:
	$(PY) scripts/einfrieren.py --samples $(LATEST)/json --liste

staende-vergleich:
	$(PY) scripts/einfrieren.py --samples $(LATEST)/json --vergleiche $(ALT) $(NEU)

ablage-stand:
	$(PY) scripts/ablage_stand.py --samples $(LATEST)/json

sicherung:
	$(PY) scripts/sicherung.py --samples $(LATEST)/json

esteuerauszug:
	$(PY) scripts/esteuerauszug_check.py --input $(PDFDIR) --anonym

extension-daten:
	$(PY) scripts/export_extension.py --samples $(LATEST)/json

etax-daten:
	$(PY) scripts/etax_daten.py --samples $(LATEST)/json

ablage-aussteller:
	$(PY) scripts/ablage_aussteller.py --samples $(LATEST)/json \
	  --alt "$(ALT)" --neu "$(NEU)" $(if $(PROBE),--probe,)

PYETAX := .venv-etax/bin/python

etax-bauen:
	$(PY) scripts/etax_daten.py --samples $(LATEST)/json
	$(PYETAX) scripts/etax_bauen.py \
	  --eingabe $(LATEST)/etax_eingabe.json \
	  --ziel $(LATEST)/esteuerauszug --jahr $(or $(JAHR),2025) \
	  --vorname "$(VORNAME)" --nachname "$(NACHNAME)" --nur "$(NUR)"

ablage-positionen:
	$(PY) scripts/ablage_position.py --samples $(LATEST)/json \
	  --zielwert "$(ZIELWERT)" $(if $(JETZT),--jetzt,)

nachweis:
	$(PY) scripts/ablage_nachweis.py --samples $(LATEST)/json $(if $(JETZT),--jetzt,)

ablage-nachziehen:
	$(PY) -c "import sys;sys.path.insert(0,'.');from pathlib import Path;from extractors.ablage import uebernimm_durchsicht;print('Nachgezogen:', uebernimm_durchsicht(Path('$(LATEST)/json')) or 'nichts')"

namenspruefung:
	$(PY) -c "import sys;sys.path.insert(0,'.');\
from pathlib import Path;\
from scripts.build_review_html import _pdf_name_of, sammle_zeilen;\
s=Path('$(LATEST)/json');\
j={_pdf_name_of(p) for p in sorted(s.glob('*.json')) if not p.name.startswith('_') and p.name!='korrekturen.json'};\
z=[r for r in sammle_zeilen(s, pdf_dir=None, mit_crops=False) if not r.get('gestrichen')];\
n={str(r.get('beleg') or '') for r in z};\
print(f'  {len(j):3}  eingelesene Dokumente (Word-JSON)');\
print(f'  {len(n):3}  Dokumentnamen in den Zeilen');\
print(f'  {len(n-j):3}  Namen ohne Word-JSON');\
print(f'  {sum(1 for r in z if str(r.get('beleg') or '') in (n-j)):3}  Zeilen betroffen')"

# Nur der erste Schritt: PDFs einlesen. Schreibt Word-JSONs und sonst nichts —
# kein Aufraeumen, keine Extraktion, keine Beruehrung der Ablage.
einlesen:
	$(PY) scripts/pdf_to_word_json.py --input $(PDFDIR) --output $(LATEST)/json

# Die selbst erzeugten eSteuerauszuege wieder entfernen.
etax-weg:
	$(PY) -c "import shutil,pathlib;\
d=pathlib.Path('$(LATEST)/esteuerauszug');\
n=len(list(d.glob('*.pdf'))) if d.is_dir() else 0;\
shutil.rmtree(d, ignore_errors=True);\
e=pathlib.Path('$(LATEST)/etax_eingabe.json'); e.unlink(missing_ok=True);\
print(f'{n} fehlerhafte PDF(s) entfernt, Eingabedatei weg.')"

konten-uebersicht:
	$(PY) scripts/konten_uebersicht.py --samples $(LATEST)/json

ibans:
	$(PY) scripts/etax_iban.py --samples $(LATEST)/json

doppelte:
	$(PY) scripts/doppelte_werte.py --samples $(LATEST)/json --input $(PDFDIR)

journal-anwenden:
	$(PY) -c "import sys;sys.path.insert(0,chr(46));from pathlib import Path;from scripts.umbenennen import journal_anwenden;print(journal_anwenden(Path('$(LATEST)/json'), Path('$(PDFDIR)')), chr(69)+chr(105)+chr(110)+chr(116)+chr(114)+chr(97)+chr(103)+chr(101)+chr(110)+chr(32)+chr(117)+chr(109)+chr(103)+chr(101)+chr(104)+chr(228)+chr(110)+chr(103)+chr(116))"

fingerabdruecke:
	$(PY) /tmp/claude-501/fuellen.py
