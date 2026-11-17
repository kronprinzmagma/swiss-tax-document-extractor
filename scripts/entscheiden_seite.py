"""Seite „Zu entscheiden" der Review — nur, was die Prüfkette nicht lösen kann.

der Anwender (261024-jga): „ich will keinen Review von Dingen machen, die du selber
fixen könntest". Die Seite rechnet bei jedem Aufruf die ganze Kette neu
(alle Leser, Gegenproben, Abgleich mit der Durchsicht — dieselbe wie Leiste
und ``make pruefen``) und zeigt nur, was danach noch eine Warnung trägt.
Typisch: ein bestätigter Wert, dessen PDF nicht mehr im Ordner liegt.

Je Punkt zwei Knöpfe: „behalten" (gilt, Warnung erledigt) und — wo das
Dokument fehlt — „weglassen" (nicht in die Leiste; die Ablage bleibt
unberührt). Entschiedenes steht darunter und lässt sich zurücknehmen.
Läuft nur am Review-Server auf 127.0.0.1, mit dessen Token.

Entscheidungen, die keinen Wert mehr treffen (Dokument umbenannt, Betrag
geändert), stehen in einem eigenen Abschnitt (261025-jm6, A2d) — vorher
wirkten sie still nicht mehr.
"""
from __future__ import annotations

import html
import json
from pathlib import Path


def offene_punkte(samples: Path, daten: dict | None = None,
                  pdf_dir: Path | None = None) -> tuple[list[dict], list[dict], list[dict]]:
    """(offen, entschieden, ohne_wert) — je Punkt Formular, Titel, Betrag,
    Grund, Schlüssel.

    ``daten``: die schon gerechnete Leiste (Stand-Seite, make pruefen —
    einmal rechnen); sonst wird sie hier mit ``pdf_dir`` gerechnet.
    ``ohne_wert``: gesetzte Entscheidungen, die keinen Wert der Leiste
    treffen (261025-jm6, A2d).
    """
    from extractors import entscheide

    if daten is None:
        from scripts.export_extension import baue
        daten = baue(samples, pdf_dir=pdf_dir)
    offen, entschieden, ohne_wert = [], [], []
    for f in daten["formulare"]:
        for b in f["bloecke"]:
            if b.get("felder") and b.get("schluessel"):
                warn = [x for x in b["felder"] if x.get("warnung")]
                punkt = {
                    "formular": f["formular"], "titel": b.get("titel", ""),
                    "person": b.get("person", ""),
                    "betrag": next((x.get("wert") for x in b["felder"]
                                    if str(x.get("titel", "")).startswith(("Total", "Schuld am"))), ""),
                    "grund": "; ".join(f"{x['titel']}: {x.get('hinweis') or 'prüfen'}" for x in warn),
                    "dokumente": b.get("dokumente") or [],
                    "schluessel": b["schluessel"], "verwaist": b.get("verwaist", False),
                }
                if warn:
                    offen.append(punkt)
                elif b.get("entschieden"):
                    entschieden.append({**punkt, "aktion": b["entschieden"]})
            for w in b.get("werte") or []:
                punkt = {
                    "formular": f["formular"], "titel": w.get("bezeichnung", ""),
                    "person": b.get("person", ""), "betrag": w.get("betrag", ""),
                    "grund": w.get("hinweis", ""), "dokumente": [w.get("quelle", "")],
                    "schluessel": w.get("schluessel", ""), "verwaist": w.get("verwaist", False),
                }
                if w.get("warnung"):
                    offen.append(punkt)
                elif w.get("entschieden"):
                    entschieden.append({**punkt, "aktion": w["entschieden"]})
    # Weggelassene stehen nicht mehr in der Leiste — aus der Datei zeigen,
    # aber nur, wenn sie einen Wert getroffen haben. Die übrigen sind
    # „Entscheidungen ohne Wert".
    ohne = set(daten.get("entscheide_ohne_wert") or [])
    for k, e in sorted(entscheide.lade(samples).items()):
        dok, titel, wert, _ = entscheide.zerlege_schluessel(k)
        punkt = {"formular": "", "titel": titel, "person": "", "betrag": wert,
                 "grund": "", "dokumente": [dok], "schluessel": k, "verwaist": True,
                 "aktion": e.get("aktion")}
        if k in ohne:
            ohne_wert.append(punkt)
        elif e.get("aktion") == "weglassen":
            entschieden.append(punkt)
    return offen, entschieden, ohne_wert


def seite(samples: Path, token: str, pdf_dir: Path | None = None) -> str:
    offen, entschieden, ohne_wert = offene_punkte(samples, pdf_dir=pdf_dir)
    e = html.escape

    def zeile(p: dict, erledigt: bool) -> str:
        k = json.dumps(p["schluessel"])
        if erledigt:
            knoepfe = (f"<span class=tag>{e(p['aktion'])}</span>"
                       f"<button onclick='setze({e(k)}, null)'>zurücknehmen</button>")
        else:
            knoepfe = f"<button class=pri onclick='setze({e(k)}, \"behalten\")'>behalten</button>"
            if p["verwaist"]:
                knoepfe += (f"<button onclick='setze({e(k)}, \"weglassen\")'>"
                            "weglassen</button>")
        return (f"<tr><td>{e(p['formular'])}<br><b>{e(p['titel'])}</b><br>"
                f"<small>{e(p['person'])}</small></td>"
                f"<td class=r>{e(str(p['betrag']))}</td>"
                f"<td>{e(p['grund'])}<br><small>{e(', '.join(p['dokumente']))}</small></td>"
                f"<td class=k>{knoepfe}</td></tr>")

    kopf = "<tr><th>Wert</th><th>Betrag</th><th>Grund · Dokument</th><th></th></tr>"
    offen_html = ("<table>" + kopf + "".join(zeile(p, False) for p in offen) + "</table>"
                  if offen else "<p class=gut>Nichts zu entscheiden — die Prüfkette ist "
                                "ohne offene Punkte durchgelaufen.</p>")
    erledigt_html = ("<h2>Entschieden</h2><table>" + kopf
                     + "".join(zeile(p, True) for p in entschieden) + "</table>"
                     if entschieden else "")
    ohne_html = ("<h2>Entscheidungen ohne Wert</h2><p class=lead>Diese Entscheidungen "
                 "treffen keinen Wert der Leiste mehr — das Dokument wurde umbenannt "
                 "oder der Betrag hat sich geändert. Sie wirken nicht mehr; "
                 "zurücknehmen räumt sie auf (ein Grabstein bleibt in der Datei).</p>"
                 "<table>" + kopf + "".join(zeile(p, True) for p in ohne_wert) + "</table>"
                 if ohne_wert else "")
    return f"""<!doctype html><html lang=de><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Zu entscheiden</title>
<style>
:root{{--bg:#fff;--fg:#1d1d1f;--mut:#6e6e73;--lin:#d2d2d7;--pri:#0a5bd3;--ok:#1d7a3a}}
@media (prefers-color-scheme:dark){{:root{{--bg:#1c1c1e;--fg:#f2f2f7;--mut:#a1a1a6;--lin:#3a3a3c;--pri:#4c8dff;--ok:#5ec27a}}}}
body{{background:var(--bg);color:var(--fg);font:14px/1.45 -apple-system,system-ui,sans-serif;margin:0;padding:16px}}
h1{{font-size:20px;margin:0 0 4px}} h2{{font-size:16px;margin:24px 0 8px}}
p.lead{{color:var(--mut);margin:0 0 16px;max-width:70ch}}
table{{border-collapse:collapse;width:100%}} td,th{{border-bottom:1px solid var(--lin);padding:8px;vertical-align:top;text-align:left}}
td.r{{text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}} td.k{{white-space:nowrap}}
small{{color:var(--mut)}} .gut{{color:var(--ok);font-weight:600}}
button{{font:inherit;padding:4px 10px;margin:0 4px 4px 0;border:1px solid var(--lin);border-radius:6px;background:transparent;color:var(--fg);cursor:pointer}}
button.pri{{background:var(--pri);border-color:var(--pri);color:#fff}}
.tag{{color:var(--mut);margin-right:8px}}
@media (max-width:640px){{td.k,td.r{{white-space:normal}}}}
</style></head><body>
<h1>Zu entscheiden</h1>
<p class=lead>Nur, was die Prüfkette nicht selbst lösen kann — alle Leser,
Gegenproben und der Abgleich mit der Durchsicht sind schon gelaufen.
„weglassen" nimmt den Wert nur aus der Leiste; in der Ablage bleibt er.</p>
{offen_html}
{erledigt_html}
{ohne_html}
<script>
const TOKEN = {json.dumps(token)};
async function setze(schluessel, aktion) {{
  const r = await fetch('/api/entscheiden', {{method: 'POST',
    headers: {{'Content-Type': 'application/json', 'X-Steuer-Token': TOKEN}},
    body: JSON.stringify({{schluessel, aktion}})}});
  if (r.ok) location.reload();
  else alert('Nicht gespeichert: ' + (await r.text()));
}}
</script></body></html>"""
