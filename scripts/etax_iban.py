#!/usr/bin/env python3
"""Die IBAN eines Dokuments — die einzige verlässliche Kontoidentität.

Bis hierher habe ich Konten über eine Ziffernfolge im Dateinamen
zusammengefasst. Die kann eine Kontonummer sein, genauso gut ein Datum: zwei
fremde Konten verschmolzen, und die Erträge stimmten nicht mehr. Ohne
Zusammenfassen wurde aus einem Konto mit zwei Belegen (Saldo- und
Zinsausweis) zwei halbe.

Die IBAN löst beides. Sie steht im Beleg, sie ist eindeutig, und das Formular
verlangt die Kontonummer ohnehin („bei Konto inkl. Nummer", Formular 340).
Erkannt wird sie mit dem Muster, das dieses Projekt für den Privacy-Gate
ohnehin führt — eine Quelle, nicht zwei.

**Die IBAN ist ein Personendatum.** Sie gehört in den Auszug, den nur der
Mensch sieht — nie in eine Ausgabe, die als teilbar beschrieben ist. Dieses
Skript gibt deshalb nur Zahlen aus; die IBANs selbst wandern in die
Brückendatei.

Aufruf::

    make ibans
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TRENNER = "─" * 70


def ibans_im_dokument(samples: Path, dokument: str) -> list[str]:
    """Alle IBANs eines Dokuments, in der Reihenfolge des Vorkommens."""
    from extractors.pii_patterns import IBAN_REAL_RE, is_iban_placeholder
    from scripts.ablage_nachweis import _json_zu, worte_aus

    worte = worte_aus(_json_zu(samples, dokument))
    if not worte:
        return []
    # Ueber den ganzen Text suchen, nicht Wort fuer Wort: pdfplumber trennt
    # IBANs an den Leerzeichen der ueblichen Vierergruppen.
    text = " ".join(w["text"] for w in worte)
    gefunden: list[str] = []
    for treffer in IBAN_REAL_RE.finditer(text):
        roh = treffer.group(0).replace(" ", "").upper()
        if is_iban_placeholder(roh):
            continue
        if roh not in gefunden:
            gefunden.append(roh)
    return gefunden


def sammle(samples: Path) -> dict:
    """Je Dokument die gefundenen IBANs."""
    from scripts.etax_daten import _zeilen_der_tabelle

    dokumente = sorted({str(z.get("pdf_name") or z.get("beleg") or "")
                        for z in _zeilen_der_tabelle(samples)})
    aus = {}
    for dokument in dokumente:
        if not dokument:
            continue
        aus[dokument] = ibans_im_dokument(samples, dokument)
    return aus


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--samples", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    je_dokument = sammle(args.samples)
    ziel = args.out or args.samples.parent / "ibans.json"
    ziel.write_text(json.dumps(je_dokument, indent=2, ensure_ascii=False),
                    encoding="utf-8")

    mit = {d: i for d, i in je_dokument.items() if i}
    eindeutig = {d: i for d, i in mit.items() if len(i) == 1}
    mehrere = {d: i for d, i in mit.items() if len(i) > 1}
    alle = {x for i in mit.values() for x in i}

    print("IBANs — nur Zahlen, dieser Teil ist teilbar")
    print(TRENNER)
    print(f"   {len(je_dokument):3}  Dokumente geprüft")
    print(f"   {len(eindeutig):3}  mit genau einer IBAN — eindeutige Zuordnung")
    print(f"   {len(mehrere):3}  mit mehreren IBANs — Zuordnung unklar")
    print(f"   {len(je_dokument) - len(mit):3}  ohne IBAN")
    print(f"   {len(alle):3}  verschiedene Konten insgesamt")

    # Wie viele Dokumente teilen sich eine IBAN? Das beantwortet die Frage,
    # ob eine Bank Saldo und Zins getrennt ausweist.
    von_mehreren: dict[str, int] = {}
    for dokument, ibans in eindeutig.items():
        von_mehreren[ibans[0]] = von_mehreren.get(ibans[0], 0) + 1
    geteilt = sum(1 for n in von_mehreren.values() if n > 1)
    print(f"   {geteilt:3}  Konto/Konten, zu denen es MEHRERE Dokumente gibt")

    print(f"\n→ {ziel}")
    print("\nDie Datei enthält IBANs. Sie bleibt lokal.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
