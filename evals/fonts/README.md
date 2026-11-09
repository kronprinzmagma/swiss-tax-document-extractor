# evals/fonts/

Schriftarten für die Eval-Fixtures. Dieses Verzeichnis ist gitignored
(Lizenz-Hygiene und Repo-Schlankheit), die Schriftdateien müssen lokal
abgelegt werden.

## Inter-Regular.ttf (für F3-Fixture)

Die F3-Fixture (`lohnausweis_custom_font`, Stufe L2 — siehe
`phases/01-lohnausweis-end-to-end/01-CONTEXT.md` §D-C3) erzeugt
ein Lohnausweis-PDF mit explizit eingebettetem Custom-Font, um den
Glyph-Mapping-Pitfall (PDF-Wort vs. erwartetes Codepoint) zu testen.

### Download

Inter ist OFL-lizenziert (kommerzielle Nutzung erlaubt, Lizenz muss
mitgeliefert werden — wir embedden nur in unsere lokalen Test-PDFs).

- Quelle: <https://github.com/rsms/inter/releases>
- Datei: `Inter-Regular.ttf` (oder `.otf`)
- Zielpfad: `evals/fonts/Inter-Regular.ttf`

Falls die Datei fehlt, fällt F3 automatisch auf die Default-Helvetica-
Variante (L1 statt L2) zurück. Das wird in `baseline_model.json` und
im Run-Log dokumentiert.

### Privacy

Schriftdateien sind nicht-PII, aber wir tracken sie trotzdem nicht im
Repo, damit die Lizenz-Pflicht (OFL-Hinweis im Distribution-Build)
explizit beim User liegt.
