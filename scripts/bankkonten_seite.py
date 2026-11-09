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
import re
from decimal import Decimal
from pathlib import Path

from extractors import bankobjekte, rohtabelle
from extractors.bankobjekte import VST_GRENZE
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
tr.fehlt td.w,tr.fehlt td.h,tr.widerspruch td.w,tr.widerspruch td.h{color:var(--bad)}
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
             "fehlt": "fehlt", "leer": "nicht ausgewiesen",
             "widerspruch": "Widerspruch"}


def _e(x) -> str:
    return html.escape(str(x), quote=True)


def _wert(w) -> str:
    if w is None:
        return ""
    if isinstance(w, Decimal):
        return anzeige(w)
    return str(w)


_TYPEN = ("bank_zinsausweis", "hypothek_zinsbestaetigung")


def _lade(samples: Path, pdf_dir: Path | None = None):
    """Bankdokumente des Laufs → (Objekte, Dokumentinfo, Adressen, Übergangen).

    Nur Dokumente, deren PDF noch im Ordner liegt — ein altes Word-JSON nach
    Umbenennen oder Verschieben erschien sonst als zweites Dokument. Und
    inhaltsgleiche Dokumente unter zwei Namen nur einmal.
    """
    import hashlib
    from extractors.belegordner import alle_pdfs
    vorhanden = ({p.name for p in alle_pdfs(pdf_dir)}
                 if pdf_dir and pdf_dir.is_dir() else None)
    gesehen: set[str] = set()
    uebergangen = {"ohne PDF im Ordner": 0, "inhaltsgleich": 0}
    from scripts.etax_daten import _zeilen_der_tabelle
    info: dict[str, dict] = {}
    for z in _zeilen_der_tabelle(samples):
        e = info.setdefault(str(z.get("pdf_name") or ""),
                            {"bank": "", "person": "", "salden": []})
        e["bank"] = e["bank"] or str(z.get("aussteller") or "")
        e["person"] = e["person"] or str(z.get("person") or "")
        if z.get("zielwert") in ("Saldo 31.12.", "Steuerwert 31.12.",
                                 "Hypothekarschuld 31.12."):
            wert = betrag(z.get("betrag"))
            if wert is not None:
                e["salden"].append(wert)
    dokumente, adressen = [], {}
    for pfad in sorted(samples.glob("*.json")):
        if pfad.name.startswith("_") or pfad.name.endswith(".truth.json"):
            continue
        try:
            d = json.loads(pfad.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(d, dict):
            continue
        name = str(d.get("pdf_name") or pfad.stem + ".pdf")
        if name not in info:
            continue
        if vorhanden is not None and name not in vorhanden:
            uebergangen["ohne PDF im Ordner"] += 1
            continue
        tabelle = rohtabelle.baue(d)
        fingerabdruck = hashlib.sha256(
            "\n".join(z.text for z in tabelle.zeilen).encode()).hexdigest()
        if fingerabdruck in gesehen:
            uebergangen["inhaltsgleich"] += 1
            continue
        gesehen.add(fingerabdruck)
        hypothek = any(bankobjekte._HYPOTHEK_RE.search(z.text)
                       for z in tabelle.zeilen)
        if d.get("belegtyp") not in _TYPEN and not hypothek:
            continue
        dokumente.append((name, tabelle))
        _, adr = bankobjekte._bank(tabelle, info[name]["bank"])
        adressen[name] = adr.wert if adr else None
    return bankobjekte.objekte(dokumente), info, adressen, uebergangen


def _zeile(titel, wert, herkunft, anker, pflicht=False, befunde=()):
    stelle = (f'S. {anker.get("seite")}: {_e(anker.get("snippet", ""))}'
              if anker and anker.get("snippet") else "")
    pruef = "".join(f'<span class="b {b[0]}">{_e(b[1])}</span>' for b in befunde)
    w = _wert(wert)
    return (f'<tr class="{_e(herkunft)}"><td class="f">'
            f'{"<span class=p>*</span> " if pflicht else ""}{_e(titel)}</td>'
            f'<td class="w">{_e(w) if w else "<span class=leer>—</span>"}</td>'
            f'<td class="h">{_e(_HERKUNFT.get(herkunft, herkunft))}</td>'
            f'<td class="a">{pruef}{stelle}</td></tr>')


def bankkonten_seite(samples: Path, mit_pdf: bool = True,
                     pdf_dir: Path | None = None) -> str:
    objekte, info, adressen, uebergangen = _lade(samples, pdf_dir)
    karten = {"Konto": [], "Hypothek": []}
    luecken = widersprueche = stimmt = vergleichbar = 0
    for o in sorted(objekte, key=lambda o: (o.art, o.quellen[0])):
        dok0 = o.quellen[0][0]
        bank = info[dok0]["bank"]
        person = _PERSON.get(info[dok0]["person"], info[dok0]["person"])
        zeilen, fehlend = [], 0

        def feld(titel, schluessel, pflicht=False, extra=()):
            nonlocal fehlend
            wert, herkunft, anker = bankobjekte.einwert(o.werte.get(schluessel))
            befunde = list(extra)
            if herkunft == "widerspruch":
                befunde.append(("fehler", "die Seiten nennen verschiedene Werte"))
            if pflicht and herkunft in ("fehlt", "widerspruch"):
                fehlend += 1
            zeilen.append(_zeile(titel, wert, herkunft, anker, pflicht, befunde))
            return wert, herkunft

        kennung = o.kennung
        k_befund = []
        if "_iban_ungueltig" in o.werte:
            k_befund.append(("fehler", f"IBAN {o.werte['_iban_ungueltig'][0].wert}"
                             " — nicht zum Zusammenlegen benutzt"))
        zeilen.append(_zeile("Bank, Gesellschaft" if o.art == "Konto"
                             else "Gläubiger", bank, "ablage" if bank else "fehlt",
                             None, True))
        if o.art == "Hypothek":
            adr = adressen.get(dok0)
            zeilen.append(_zeile("Adresse des Gläubigers", adr,
                                 "dokument" if adr else "fehlt", None, True))
            fehlend += 0 if adr else 1
        if o.art == "Hypothek":
            nr, h, a = bankobjekte.einwert(o.werte.get("nummer"))
            zeilen.append(_zeile("Hypothekar-Nr.", nr, h, a, True))
            fehlend += 0 if nr else 1
        zeilen.append(_zeile("Konto-/Depot-Nr." if o.art == "Konto" else "IBAN",
                             kennung.wert if kennung else None,
                             kennung.herkunft if kennung else
                             ("fehlt" if o.art == "Konto" else "leer"),
                             kennung.anker if kennung else None,
                             o.art == "Konto", k_befund))
        fehlend += 0 if kennung or o.art != "Konto" else 1
        zeilen.append(_zeile("Bezeichnung", o.bezeichnung.wert if o.bezeichnung else None,
                             o.bezeichnung.herkunft if o.bezeichnung else "leer",
                             o.bezeichnung.anker if o.bezeichnung else None))
        zeilen.append(_zeile("Währung", o.waehrung.wert, o.waehrung.herkunft,
                             o.waehrung.anker, True))
        if o.art == "Konto":
            saldo, _ = feld("Total Guthaben (Steuerwert 31.12.)", "saldo", True)
            zins, h = feld("Zins (Bruttoertrag)", "zins")
            kol, _, _ = bankobjekte.einwert(o.werte.get("kolonne"))
            if zins is not None and h != "widerspruch":
                kolonne = kol or ("A" if zins > VST_GRENZE else "B")
                zeilen.append(_zeile("→ Kolonne", "A (mit VSt)" if kolonne == "A"
                                     else "B (ohne VSt)",
                                     "dokument" if kol else "berechnet", None))
                if (isinstance(saldo, Decimal) and saldo > 0
                        and zins / saldo > bankobjekte.ZINSSATZ_OBERGRENZE):
                    zeilen.append(_zeile("Prüfung", "", "fehlt", None, False,
                                         [("warnung", "Zins über 5 % des Saldos — "
                                           "Saldo oder Zins falsch gelesen?")]))
        else:
            saldo, _ = feld("Schuld am 31.12.", "schuld", True)
            periode, _, p_anker = bankobjekte.einwert(o.werte.get("periode"))
            hinweis = []
            if periode and not re.search(r"01\.01\.\d{4}\s*–\s*31\.12\.", str(periode)):
                hinweis.append(("warnung", "Zins nur für diese Periode — für das "
                                "Steuerjahr fehlt die übrige Abrechnung"))
            feld("Schuldzinsen", "zins", True, hinweis)
            zeilen.append(_zeile("Zinsperiode", periode,
                                 "dokument" if periode else "leer", p_anker))
            saetze = []
            for w in o.werte.get("zinssatz", []):
                if w.wert not in saetze:
                    saetze.append(w.wert)
            zeilen.append(_zeile("Zinssatz", " / ".join(saetze) or None,
                                 "dokument" if saetze else "leer",
                                 o.werte["zinssatz"][0].anker if saetze else None))
            marchzins, h, a = bankobjekte.einwert(o.werte.get("marchzins"))
            if marchzins is not None:
                zeilen.append(_zeile("Marchzins (aufgelaufen)", marchzins, h, a))
            kontrollen = [w.wert for w in o.werte.get("_kontrolle", [])]
            summe = [w.wert for w in o.werte.get("_summe_stimmt", [])]
            if kontrollen or summe:
                gut = all(kontrollen) and all(summe)
                zeilen.append(_zeile(
                    "Nachrechnung", "stimmt" if gut else "stimmt nicht",
                    "berechnet" if gut else "widerspruch", None, False,
                    [] if gut else [("fehler", "Kapital × Satz × Tage / 360 bzw. "
                                     "Summe der Zeilen ≠ Rechnungsbetrag — "
                                     "falsch gelesen?")]))
        # Abgleich mit der Durchsicht
        salden = [w for d, _ in o.quellen for w in info[d]["salden"]]
        if salden and isinstance(saldo, Decimal):
            vergleichbar += 1
            stimmt += saldo in salden
        luecken += fehlend
        # Mehrere Zinssätze im Jahr und Hilfswerte sind kein Widerspruch.
        widersprueche += sum(1 for k, v in o.werte.items()
                             if k != "zinssatz" and not k.startswith("_")
                             and bankobjekte.einwert(v)[1] == "widerspruch")
        quellen = ", ".join(
            (f'<a href="/pdf/{_e(d)}#page={s}" target="_blank" rel="noopener">'
             f'{_e(d)} S. {s}</a>') if mit_pdf else f"{_e(d)} S. {s}"
            for d, s in o.quellen)
        titel = " · ".join(t for t in (bank, o.bezeichnung.wert if o.bezeichnung else "",
                                      person) if t)
        karten[o.art].append(
            f'<div class="konto"><h3>{_e(titel)} <span>· {fehlend} '
            f'Pflichtlücke{"n" if fehlend != 1 else ""}</span></h3>'
            f'<p class="adr">Aus: {quellen}</p><table>{"".join(zeilen)}</table></div>')

    teile = []
    for art, ueberschrift in (("Konto", "Konten"), ("Hypothek", "Hypotheken")):
        teile.append(f'<section><div class="dk"><span class="t">{ueberschrift} '
                     f'({len(karten[art])})</span></div>'
                     + ("".join(karten[art]) or '<p class="adr">keine gefunden</p>')
                     + "</section>")
    n_konten, n_hyp = len(karten["Konto"]), len(karten["Hypothek"])
    n_dok = len({d for o in objekte for d, _ in o.quellen})
    return f"""<!doctype html>
<html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Bankkonten</title><style>{_CSS}</style></head><body>
<header>
  <h1>Bankkonten und Hypotheken — aus den Dokumenten gelesen</h1>
  <div class="sub">Jede Seite gehört zu einem Konto oder einer Hypothek; Seiten
    mit derselben IBAN bzw. Nummer sind dasselbe Objekt. Je Objekt die Felder
    der ZHprivateTax-Maske. <span class="p">*</span> = Pflichtfeld. Nur lesen.</div>
  <div class="kz"><span><b>{n_dok}</b> Dokumente</span>
    <span><b>{n_konten}</b> Konten</span>
    <span><b>{n_hyp}</b> Hypotheken</span>
    <span><b>{luecken}</b> Pflichtlücken</span>
    <span><b>{widersprueche}</b> Widersprüche</span>
    <span><b>{stimmt}</b> von {vergleichbar} Salden wie in der Durchsicht</span>
  </div>
  <div class="sub">Übergangen: {uebergangen["ohne PDF im Ordner"]} Word-JSON ohne
    PDF im Ordner, {uebergangen["inhaltsgleich"]} inhaltsgleiche Dokumente.</div>
</header>
<main>{"".join(teile)}</main></body></html>"""
