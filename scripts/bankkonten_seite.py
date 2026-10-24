"""Die Bankkonten-Ansicht der Review-Oberfläche: die Kette, sichtbar.

Je Bankdokument: welche Konten darin stehen und was die ZHprivateTax-Maske
„Bankkonto" je Konto bekommt — Feld für Feld, mit Herkunft und der Stelle
im Dokument. Daneben der Abgleich mit der Durchsicht: stimmt der gelesene
Saldo mit dem geprüften überein, führt das Dokument mehr Konten, als die
Ablage kennt?

Nur lesen. Die Kette: `extractors/rohtabelle.py` → `extractors/bankobjekte.py`.
"""
from __future__ import annotations

import html
import json
from decimal import Decimal
from pathlib import Path

from extractors import bankobjekte, rohtabelle
from extractors.eintraege import anzeige, betrag

_PERSON = {"elternteil_1": "Person 1", "elternteil_2": "Person 2",
           "familie": "Familie"}

_CSS = """
:root{--bg:#fbfbfa;--fg:#1a1a18;--mut:#6b6b66;--li:#e4e4e0;--card:#fff;
--akz:#2b5fd9;--ok:#1f7a3a;--warn:#8a6100;--bad:#b42318}
@media(prefers-color-scheme:dark){:root{--bg:#17171a;--fg:#ececea;--mut:#9a9a95;
--li:#2e2e33;--card:#1f1f23;--akz:#7aa2f7;--ok:#6cc58a;--warn:#e0b355;--bad:#f08a80}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
header{position:sticky;top:0;background:var(--bg);z-index:3;
border-bottom:1px solid var(--li);padding:12px 20px}
h1{font-size:16px;margin:0 0 3px}
.sub{color:var(--mut);font-size:12.5px}
.kz{display:flex;gap:18px;flex-wrap:wrap;margin-top:8px;font-size:13px}
.kz b{font-variant-numeric:tabular-nums}
main{padding:14px 20px 60px;max-width:1100px}
section{margin-bottom:22px}
.dk{display:flex;gap:10px;align-items:baseline;flex-wrap:wrap;margin:0 0 6px}
.dk .t{font-weight:600}
.dk .m{color:var(--mut);font-size:12.5px}
.dk a{margin-left:auto;font-size:12.5px;color:var(--akz)}
.abgl{font-size:12.5px;margin:0 0 8px}
.abgl.ok{color:var(--ok)} .abgl.warn{color:var(--warn)} .abgl.bad{color:var(--bad)}
.konto{background:var(--card);border:1px solid var(--li);border-radius:9px;
margin-bottom:10px;overflow:hidden}
.konto h3{font-size:13px;margin:0;padding:8px 12px;border-bottom:1px solid var(--li)}
.konto h3 span{color:var(--mut);font-weight:400}
table{width:100%;border-collapse:collapse}
td{padding:6px 12px;border-bottom:1px solid var(--li);vertical-align:top}
tr:last-child td{border-bottom:0}
td.f{width:34%} td.w{width:20%;font-weight:600;text-align:right;
font-variant-numeric:tabular-nums;white-space:nowrap}
td.h{width:14%;font-size:12px;color:var(--mut)}
td.a{font-size:11.5px;color:var(--mut);overflow-wrap:anywhere}
.p{color:var(--bad);font-weight:600}
tr.fehlt td.w,tr.fehlt td.h{color:var(--bad)}
tr.annahme td.h{color:var(--warn)}
.leer{color:var(--mut);font-style:italic}
.adr{font-size:12.5px;margin:0 0 8px;color:var(--mut)}
.b{display:inline-block;font-size:11.5px;border-radius:5px;padding:0 6px;
margin:0 6px 2px 0;font-weight:600}
.b.fehler{background:#fde8e6;color:var(--bad)}
.b.warnung{background:#fdf3dc;color:var(--warn)}
.b.hinweis{background:var(--li);color:var(--mut)}
@media(prefers-color-scheme:dark){.b.fehler{background:#3a1d1b}.b.warnung{background:#3a311b}}
"""

_HERKUNFT = {"dokument": "im Dokument", "ablage": "Durchsicht",
             "berechnet": "berechnet", "annahme": "Annahme — prüfen",
             "fehlt": "fehlt", "leer": "nicht ausgewiesen"}


def _e(x) -> str:
    return html.escape(str(x), quote=True)


def _wert(w) -> str:
    if w is None:
        return ""
    if isinstance(w, Decimal):
        return anzeige(w)
    return str(w)


def _dokumente(samples: Path) -> list[dict]:
    """Bankdokumente des Laufs, die auch in der Tabelle stehen."""
    from scripts.etax_daten import _zeilen_der_tabelle
    zeilen = _zeilen_der_tabelle(samples)
    je_dok: dict[str, dict] = {}
    for z in zeilen:
        dok = str(z.get("pdf_name") or "")
        e = je_dok.setdefault(dok, {"bank": "", "person": "", "salden": []})
        e["bank"] = e["bank"] or str(z.get("aussteller") or "")
        e["person"] = e["person"] or str(z.get("person") or "")
        if z.get("zielwert") in ("Saldo 31.12.", "Steuerwert 31.12."):
            wert = betrag(z.get("betrag"))
            if wert is not None:
                e["salden"].append(wert)
    aus = []
    for pfad in sorted(samples.glob("*.json")):
        if pfad.name.startswith("_") or pfad.name.endswith(".truth.json"):
            continue
        try:
            d = json.loads(pfad.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(d, dict) or d.get("belegtyp") != "bank_zinsausweis":
            continue
        name = str(d.get("pdf_name") or pfad.stem + ".pdf")
        if name not in je_dok:
            continue
        info = je_dok[name]
        objekte = bankobjekte.zerlege(rohtabelle.baue(d), info["bank"])
        konten = bankobjekte.uebertragung(objekte)
        adresse = objekte.adresse.wert if objekte.adresse else None
        for k in konten:
            k["befunde"] = bankobjekte.pruefe(k)
        aus.append({"name": name, **info, "konten": konten,
                    "adresse": adresse,
                    "adressbefunde": bankobjekte.pruefe(
                        {"felder": konten[0]["felder"]}, adresse or "")
                    if konten else []})
    return aus


def _abgleich(salden: list[Decimal], konten: list[dict]) -> tuple[str, str]:
    gelesen = [k["felder"][5]["wert"] for k in konten
               if k["felder"][5]["wert"] is not None]
    if not salden:
        return "warn", "In der Durchsicht steht für dieses Dokument kein Saldo."
    if sorted(salden) == sorted(gelesen):
        return "ok", "Saldo stimmt mit der Durchsicht überein."
    if len(salden) == 1 and len(gelesen) > 1 and sum(gelesen) == salden[0]:
        return "warn", (f"Die Durchsicht hat eine Summe ({anzeige(salden[0])}), "
                        f"das Dokument führt {len(gelesen)} Konten — die Maske "
                        "verlangt je Konto einen Eintrag.")
    fehlend = [w for w in gelesen if w not in salden]
    if set(salden) <= set(gelesen) and fehlend:
        return "warn", ("Die Durchsicht kennt nicht alle Konten — zusätzlich "
                        "im Dokument: " + ", ".join(anzeige(w) for w in fehlend))
    return "bad", ("Weicht ab — Durchsicht: "
                   + ", ".join(anzeige(w) for w in salden)
                   + "; gelesen: " + (", ".join(anzeige(w) for w in gelesen)
                                      or "nichts"))


def bankkonten_seite(samples: Path, mit_pdf: bool = True) -> str:
    dokumente = _dokumente(samples)
    n_konten = sum(len(d["konten"]) for d in dokumente)
    n_luecken = sum(k["pflichtluecken"] for d in dokumente for k in d["konten"])
    abgleiche = [_abgleich(d["salden"], d["konten"]) for d in dokumente]
    n_ok = sum(1 for a in abgleiche if a[0] == "ok")
    n_befunde = sum(len(k["befunde"]) for d in dokumente for k in d["konten"])

    teile = []
    for d, (klasse, satz) in zip(dokumente, abgleiche):
        kopf = " · ".join(t for t in (d["bank"],
                                     _PERSON.get(d["person"], d["person"])) if t)
        link = (f'<a href="/pdf/{_e(d["name"])}" target="_blank" '
                f'rel="noopener">Dokument öffnen ↗</a>') if mit_pdf else ""
        konten = []
        for i, k in enumerate(d["konten"], start=1):
            zeilen = []
            je_feld: dict[str, list] = {}
            for b in k["befunde"]:
                je_feld.setdefault(b["feld"], []).append(b)
            for f in k["felder"]:
                anker = f["anker"] or {}
                kurz = f["titel"].split(" (")[0]
                pruef = "".join(
                    f'<span class="b {b["stufe"]}">{_e(b["grund"])}</span>'
                    for b in je_feld.get(kurz, []))
                stelle = (f'S. {anker.get("seite")}: {_e(anker.get("snippet", ""))}'
                          if anker.get("snippet") else "")
                wert = _wert(f["wert"])
                zeilen.append(
                    f'<tr class="{_e(f["herkunft"])}">'
                    f'<td class="f">{"<span class=p>*</span> " if f["pflicht"] else ""}'
                    f'{_e(f["titel"])}</td>'
                    f'<td class="w">{_e(wert) if wert else "<span class=leer>—</span>"}</td>'
                    f'<td class="h">{_e(_HERKUNFT.get(f["herkunft"], f["herkunft"]))}</td>'
                    f'<td class="a">{pruef}{stelle}</td></tr>')
            luecken = k["pflichtluecken"]
            konten.append(
                f'<div class="konto"><h3>Konto {i} '
                f'<span>· {luecken} Pflichtlücke{"n" if luecken != 1 else ""}</span>'
                f'</h3><table>{"".join(zeilen)}</table></div>')
        adr = [b for b in d["adressbefunde"] if b["feld"] == "Adresse der Bank"]
        adresse = (f'<p class="adr">Adresse der Bank: '
                   f'{_e(d["adresse"]) if d["adresse"] else "<span class=leer>nicht gefunden</span>"}'
                   + "".join(f' <span class="b {b["stufe"]}">{_e(b["grund"])}</span>'
                             for b in (adr if d["adresse"] else []))
                   + '</p>')
        if not konten:
            konten.append('<div class="konto"><h3>kein Konto erkannt '
                          '<span>— im Dokument nachsehen</span></h3></div>')
        teile.append(
            f'<section><div class="dk"><span class="t">{_e(kopf)}</span>'
            f'<span class="m">{_e(d["name"])}</span>{link}</div>'
            f'<p class="abgl {klasse}">{_e(satz)}</p>{adresse}'
            f'{"".join(konten)}</section>')

    return f"""<!doctype html>
<html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Bankkonten</title><style>{_CSS}</style></head><body>
<header>
  <h1>Bankkonten — aus den Dokumenten gelesen</h1>
  <div class="sub">Je Konto die Felder der ZHprivateTax-Maske „Bankkonto",
    mit Herkunft und Stelle im Dokument. <span class="p">*</span> = Pflichtfeld.
    Nur lesen.</div>
  <div class="kz"><span><b>{len(dokumente)}</b> Bankdokumente</span>
    <span><b>{n_konten}</b> Konten</span>
    <span><b>{n_luecken}</b> Pflichtlücken</span>
    <span><b>{n_befunde}</b> Plausibilitätsbefunde</span>
    <span><b>{n_ok}</b> von {len(dokumente)} Dokumenten stimmen mit der Durchsicht</span>
  </div>
</header>
<main>{"".join(teile)}</main></body></html>"""
