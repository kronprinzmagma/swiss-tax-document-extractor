"""Seite „Stand" der Review — alles nach dem Datenmodell, auf einen Blick.

der Anwender (261024-jga): „Es gibt Informationen, die pro Person benötigt werden,
pro Dokument und Typ gibt es Informationen, die extrahiert werden müssen …
Ich möchte einen Stand sehen, wo alles definiert und plausibel ist."

Die Seite rechnet bei jedem Aufruf die ganze Kette (dieselbe wie Leiste,
eSteuerauszug und ``make pruefen``) und zeigt:

1. **Oben:** fehlende Dokumente, verletzte Regeln, offene Entscheidungen.
2. **Je Person:** jeder Wert, den das Formular verlangt — Betrag, Feld,
   Dokument, Status (✓ belegt · ⚠ mit Grund).
3. **Je Dokument:** Typ, Ordner, Pflichtwerte erfüllt?

Korrigiert wird in der Review; diese Seite liest nur. Läuft nur am
Review-Server auf 127.0.0.1.

Seit 261025-jm6 (A4): die Leiste wird einmal gerechnet (mit Rollen, wie
``make pruefen``) und an Prüfung und „Zu entscheiden" weitergereicht; ein
fehlendes Dokument zählt nach derselben Regel wie im Terminal
(``export_extension.dokument_fehlt``), nicht über den Hinweistext.
"""
from __future__ import annotations

import html
from pathlib import Path

def _zeilen_je_person(leiste: dict) -> dict[str, list[dict]]:
    """Je Person (Rolle) die Zeilen — mit verwaist/entschieden für die Regel."""
    aus: dict[str, list[dict]] = {}
    for f in leiste["formulare"]:
        for b in f["bloecke"]:
            person = b.get("person") or "ohne Person"
            for w in b.get("werte") or []:
                aus.setdefault(person, []).append({
                    "formular": f["formular"], "titel": w.get("bezeichnung", ""),
                    "feld": w.get("feld", ""), "betrag": w.get("betrag", ""),
                    "quelle": w.get("quelle", ""),
                    "dokumente": [w.get("quelle", "")] if w.get("quelle") else [],
                    "verwaist": bool(w.get("verwaist")),
                    "entschieden": w.get("entschieden") or "",
                    "warnung": bool(w.get("warnung")),
                    "grund": w.get("hinweis") or w.get("info") or "",
                    "zusatz": ""})
            for a in b.get("angaben") or []:
                for v in a.get("werte") or []:
                    aus.setdefault(person, []).append({
                        "formular": f["formular"], "titel": v.get("titel", ""),
                        "feld": "", "betrag": v.get("wert", ""),
                        "quelle": a.get("quelle", ""), "dokumente": [],
                        "verwaist": False, "entschieden": "",
                        "warnung": not v.get("wert"),
                        "grund": "" if v.get("wert") else "fehlt", "zusatz": ""})
            if b.get("felder"):
                warn = [x for x in b["felder"] if x.get("warnung")]
                total = next((x for x in b["felder"]
                              if str(x.get("titel", "")).startswith(("Total", "Schuld am"))), {})
                weitere = [f"{x['titel'].split(' (')[0]}: {x['wert']}" for x in b["felder"]
                           if x.get("wert") and x is not total
                           and not str(x.get("titel", "")).startswith(("Staat", "Währung"))]
                aus.setdefault(person, []).append({
                    "formular": f["formular"], "titel": b.get("titel", ""),
                    "feld": "", "betrag": total.get("wert", ""),
                    "quelle": ", ".join(b.get("dokumente") or []),
                    "dokumente": list(b.get("dokumente") or []),
                    "verwaist": bool(b.get("verwaist")),
                    "entschieden": b.get("entschieden") or "",
                    "warnung": bool(warn),
                    "grund": "; ".join(f"{x['titel']}: {x.get('hinweis') or 'prüfen'}"
                                       for x in warn),
                    "zusatz": " · ".join(weitere)})
    return aus


def _anzeige(namen: dict[str, str], rolle: str) -> str:
    """Rolle → Name für die Überschrift (lokal, nie geteilt)."""
    r = str(rolle or "")
    if r.lower() == "familie":
        return "Familie"
    return namen.get(r, r)


def _person_schluessel(rolle: str) -> tuple:
    """Reihenfolge: Rollen wie im Datenmodell, dann Familie, dann der Rest."""
    from extractors.datenmodell import rollen

    reihe = rollen()
    if rolle in reihe:
        return (reihe.index(rolle), rolle)
    if str(rolle).lower() == "familie":
        return (len(reihe), rolle)
    return (len(reihe) + 1, str(rolle))


def seite(samples: Path, pdf_dir: Path | None = None) -> str:
    from scripts import pflichtfelder_check as pc
    from scripts.build_review_html import personennamen
    from scripts.entscheiden_seite import offene_punkte
    from scripts.export_extension import dokument_fehlt

    if not (samples / "_results_full.json").exists():
        return ("<!doctype html><meta charset=utf-8><title>Stand</title>"
                "<p>Kein Extraktionsergebnis — zuerst <code>make productive</code>.</p>")

    # Einmal rechnen (261025-jm6, A4b): Rollen statt Namen — pruefe_leiste
    # braucht Rollen. Die Namen kommen erst in der Anzeige dazu.
    leiste = pc._leiste(samples, pdf_dir=pdf_dir)
    pruefung = pc.pruefe_leiste(samples, daten=leiste)
    kk = getattr(pc.pruefe_leiste, "kk", {})
    namen = personennamen()
    e = html.escape
    befunde = [b for b in pruefung if b["fehlt"]]
    zu_entscheiden, _, _ = offene_punkte(samples, daten=leiste)
    personen = _zeilen_je_person(leiste)
    # Eine Regel mit make pruefen: verwaist und nicht entschieden (A4a).
    fehlende = sorted({q for zeilen in personen.values() for z in zeilen
                       if dokument_fehlt(z) for q in z["dokumente"]})

    def ampel(n: int, gut: str, schlecht: str) -> str:
        return (f"<li class=ok>✓ {e(gut)}</li>" if n == 0
                else f"<li class=warn>⚠ {n} {e(schlecht)}</li>")

    kopf = ("<ul class=ampel>"
            + ampel(len(fehlende), "alle Dokumente im Ordner", "Dokument(e) fehlen im Ordner")
            + ampel(len(befunde), "alle Pflichtwerte je Dokument und Person da",
                    "Regel(n) verletzt")
            + ampel(len(zu_entscheiden), "nichts zu entscheiden",
                    "Punkt(e) zu entscheiden")
            + "</ul>")
    if befunde:
        kopf += "<h3>Verletzte Regeln</h3><ul>" + "".join(
            f"<li><b>{e(str(b['dokument']))}</b> ({e(b['typ'])}): {e('; '.join(b['fehlt']))}</li>"
            for b in befunde) + "</ul>"
    if zu_entscheiden:
        kopf += ("<p><a href='/entscheiden' target=_blank rel=noopener>"
                 f"{len(zu_entscheiden)} Punkt(e) zu entscheiden ↗</a></p>")

    teile = []
    for person in sorted(personen, key=_person_schluessel):
        zeilen = personen[person]
        ok = sum(1 for z in zeilen if not z["warnung"])
        reihen = "".join(
            f"<tr class='{'w' if z['warnung'] else ''}'>"
            f"<td>{e(z['formular'])}</td>"
            f"<td><b>{e(z['titel'])}</b>"
            + (f"<br><small>{e(z['zusatz'])}</small>" if z["zusatz"] else "")
            + f"</td><td class=r>{e(str(z['betrag']))}</td>"
            f"<td>{e(z['feld'])}</td>"
            f"<td>{'⚠ ' + e(z['grund']) if z['warnung'] else '✓' + (' <small>' + e(z['grund']) + '</small>' if z['grund'] else '')}</td>"
            f"<td><small>{e(z['quelle'])}</small></td></tr>"
            for z in zeilen)
        teile.append(f"<h2>{e(_anzeige(namen, person))} <small>{ok}/{len(zeilen)} ✓</small></h2>"
                     "<table><tr><th>Formular</th><th>Wert</th><th>Betrag</th>"
                     "<th>Feld</th><th>Status</th><th>Dokument</th></tr>"
                     f"{reihen}</table>")

    kk_html = ""
    if kk:
        kk_html = ("<h2>Krankenkasse je Person</h2><table><tr><th>Person</th>"
                   "<th>Grundversicherung</th><th>Zusatz</th><th>Verbilligung</th>"
                   "<th>Dokumente</th></tr>" + "".join(
                       f"<tr><td>{e(_anzeige(namen, r))}</td><td class=r>{z['kvg']}</td><td class=r>{z['vvg']}</td>"
                       f"<td class=r>{z['verbilligung']}</td><td>{len(z['dokumente'])}"
                       + (f" <span class=warn>+ nur Durchsicht</span>" if z.get("nur_durchsicht") else "")
                       + "</td></tr>" for r, z in kk.items()) + "</table>")

    # Je Dokument: Typ, Person, Befund — und darunter jeder Wert, der aus
    # diesem Dokument in die Leiste geht (der Anwender: „pro Dokument und Person").
    from extractors.eintraege import dok_name
    werte_je_dok: dict[str, list[dict]] = {}
    for person, zeilen in personen.items():
        for z in zeilen:
            for q in str(z["quelle"]).split(", "):
                if q:
                    werte_je_dok.setdefault(dok_name(q), []).append(
                        {**z, "person": _anzeige(namen, person)})
    teile_dok = []
    for b in sorted(pruefung, key=lambda x: str(x["dokument"])):
        werte = werte_je_dok.get(dok_name(b["dokument"]), [])
        reihen = "".join(
            f"<tr class='{'w' if z['warnung'] else ''}'><td>{e(z['person'])}</td>"
            f"<td>{e(z['formular'])}</td><td><b>{e(z['titel'])}</b>"
            + (f"<br><small>{e(z['zusatz'])}</small>" if z["zusatz"] else "")
            + f"</td><td class=r>{e(str(z['betrag']))}</td><td>{e(z['feld'])}</td>"
            f"<td>{'⚠ ' + e(z['grund']) if z['warnung'] else '✓'}</td></tr>"
            for z in werte)
        teile_dok.append(
            f"<h3>{'⚠' if b['fehlt'] else '✓'} {e(str(b['dokument']))} "
            f"<small>{e(b['typ'])}</small></h3>"
            + (f"<p class=warn>{e('; '.join(b['fehlt']))}</p>" if b["fehlt"] else "")
            + ("<table><tr><th>Person</th><th>Formular</th><th>Wert</th><th>Betrag</th>"
               "<th>Feld</th><th>Status</th></tr>" + reihen + "</table>" if reihen
               else "<p><small>keine Werte für die Leiste</small></p>"))
    doks = "".join(teile_dok)

    return f"""<!doctype html><html lang=de><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Stand der Steuerwerte</title>
<style>
:root{{--bg:#fff;--fg:#1d1d1f;--mut:#6e6e73;--lin:#d2d2d7;--ok:#1d7a3a;--warn:#8a6100;--wbg:#fff7e0}}
@media (prefers-color-scheme:dark){{:root{{--bg:#1c1c1e;--fg:#f2f2f7;--mut:#a1a1a6;--lin:#3a3a3c;--ok:#5ec27a;--warn:#e0b355;--wbg:#2e2614}}}}
body{{background:var(--bg);color:var(--fg);font:14px/1.45 -apple-system,system-ui,sans-serif;margin:0;padding:16px;max-width:1200px}}
h1{{font-size:22px;margin:0 0 8px}} h2{{font-size:17px;margin:28px 0 8px}} h3{{font-size:15px}}
h2 small{{color:var(--mut);font-weight:400}} small{{color:var(--mut)}}
ul.ampel{{list-style:none;padding:0;margin:8px 0 16px}} ul.ampel li{{margin:4px 0;font-weight:600}}
.ok{{color:var(--ok)}} .warn{{color:var(--warn)}}
table{{border-collapse:collapse;width:100%}} td,th{{border-bottom:1px solid var(--lin);padding:6px 8px;vertical-align:top;text-align:left}}
tr.w td{{background:var(--wbg)}} td.r{{text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}}
a{{color:inherit}}
@media (max-width:700px){{table{{display:block;overflow-x:auto}}}}
</style></head><body>
<h1>Stand der Steuerwerte 2025</h1>
<p><small>Bei jedem Aufruf neu gerechnet — dieselbe Prüfkette wie Leiste,
eSteuerauszug und <code>make pruefen</code>. Korrigieren in der Review.</small></p>
{kopf}
{"".join(teile)}
{kk_html}
<h2>Je Dokument</h2>
{doks}
</body></html>"""
