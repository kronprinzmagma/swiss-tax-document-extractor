"""Tests fuer die geteilte PLZ-vs-Jahr-Heuristik in extractors/pii_patterns.

Hintergrund: 4-stellige Zahlen 2000–2099 sind mehrdeutig (Jahreszahl vs.
CH-PLZ in den Kantonen NE/JU). Scrubber (anonymize_belege), Gate
(privacy_gate) und Grader (grade_run) teilen sich diese eine Funktion —
hier wird ihr Verhalten festgenagelt.
"""

import pytest

from extractors.pii_patterns import is_plz_token_year_not_plz


# (plz_token, following_token, erwartet_jahr)
FAELLE = [
    # Jahreszahl + Ort, dessen reale PLZ NICHT 2xxx ist → Jahr (False Positive
    # aus dem Praxis-Lauf: Briefkopf "Kontoauszug 2023 Bern").
    ("2023", "Bern", True),
    ("2022", "Zürich", True),
    ("2024", "Basel", True),
    # Jahreszahl + Nicht-Ort → Jahr.
    ("2022", "Steuerausweis", True),
    ("2024", "Veranlagung", True),
    ("2023", None, True),
    # Echte 2xxx-PLZ + Ort aus dem 2xxx-Bereich → PLZ (melden/anonymisieren).
    ("2000", "Neuchâtel", False),
    ("2000", "Neuchatel", False),
    ("2502", "Biel", False),
    ("2300", "La Chaux-de-Fonds", False),
    ("2800", "Delémont", False),
    # Interpunktion am Folge-Token darf die Erkennung nicht verhindern.
    ("2502", "Biel,", False),
    # Ausserhalb 2000–2099: nie Jahres-mehrdeutig → immer PLZ-Kandidat.
    ("8001", "Zürich", False),
    ("3000", "Bern", False),
    ("1950", "Sion", False),
    # Kein 4-stelliger Digit-Token → keine Aussage (False).
    ("202", "Bern", False),
    ("20234", "Bern", False),
    ("zwei", "Bern", False),
]


@pytest.mark.parametrize("plz_token,following_token,erwartet", FAELLE)
def test_is_plz_token_year_not_plz(plz_token, following_token, erwartet):
    assert is_plz_token_year_not_plz(plz_token, following_token) is erwartet


# --------------------------------------------------------------------------- #
# Namens-Varianten: Einzeltokens mehrteiliger Namensfelder (Quick 260611-kle)  #
# --------------------------------------------------------------------------- #

from extractors.pii_patterns import (
    generate_name_variant_pairs,
    generate_name_variants,
)


def test_variants_einfacher_name_unveraendert():
    """Bestehendes Verhalten für einteilige Namen bleibt erhalten."""
    v = generate_name_variants("Hans", "Muster")
    for erwartet in [
        "Hans", "Muster", "Hans Muster", "Muster, Hans", "Herr Hans Muster",
        "H. Muster", "HansMuster", "MusterHans", "Hans.Muster", "Hans-Muster",
    ]:
        assert erwartet in v
    # Längste zuerst (Map-Ersetzung trifft Glued vor Standalone).
    assert v == sorted(v, key=lambda x: -len(x))


def test_variants_doppel_vorname_enthaelt_einzeltokens():
    """Regression: Einzeltokens eines Doppel-Vornamens sind Varianten.

    Genau diese Lücke liess einen Zweitnamen die Anonymisierung überleben,
    ohne dass der Privacy-Gate ihn kannte.
    """
    v = generate_name_variants("Bruno Kasimir", "Testmann")
    assert "Bruno" in v
    assert "Kasimir" in v
    assert "Kasimir Testmann" in v
    assert "KasimirTestmann" in v       # Glued-Kombination
    assert "Testmann, Kasimir" in v
    assert "Bruno Kasimir Testmann" in v  # Gesamtphrase weiterhin


def test_variants_mehrteiliger_nachname_enthaelt_einzeltokens():
    v = generate_name_variants("Lena", "Testfrau Alt")
    assert "Testfrau" in v
    assert "Alt" in v
    assert "Lena Alt" in v


def test_variant_pairs_einzeltoken_mappt_auf_fantasie():
    """Anonymizer-Map: jeder Original-Token mappt auf den Fantasie-Namen."""
    pairs = dict(generate_name_variant_pairs(
        "Bruno Kasimir", "Testmann", "Felix", "Beispiel"))
    assert pairs["Kasimir"] == "Felix"
    assert pairs["Bruno"] == "Felix"
    assert pairs["Testmann"] == "Beispiel"
    assert pairs["Kasimir Testmann"] == "Felix Beispiel"
    assert pairs["Bruno Kasimir Testmann"] == "Felix Beispiel"


# --------------------------------------------------------------------------- #
# R1: Leistungserbringer-/Drittpersonen-Namen in Tabellenzeilen (260612-m8t)  #
# --------------------------------------------------------------------------- #
# Belege (z.B. KK-Leistungsabrechnungen) listen Drittpersonen als
# kapitalisierte Namens-Zeile DIREKT gefolgt von Datum + Betrag — ohne
# nachgestellten Ort, daher fasst THIRD_PARTY_NAME_RE sie nicht. Das neue
# Pattern erkennt "Name(-Name)? Datum Betrag" und scrubbt nur den Namensteil.
# Synthetische Beispiele — KEINE echten Namen.

from extractors.pii_patterns import (
    LEISTUNGSERBRINGER_PERSON_RE,
    LEISTUNGS_LABEL_WHITELIST,
)


def test_leistungserbringer_person_match_einfach():
    """Vorname-Nachname (mit Bindestrich) + Datum + Betrag → Match."""
    m = LEISTUNGSERBRINGER_PERSON_RE.search(
        "Beispiel Musterfrau-Test 22.12.2023 77.06 75.01 2.05"
    )
    assert m is not None
    assert "Beispiel" in m.group(0)
    assert "Musterfrau-Test" in m.group(0)


def test_leistungserbringer_person_match_drei_tokens():
    """Drei kapitalisierte Namens-Tokens + Datum + Betrag → Match."""
    m = LEISTUNGSERBRINGER_PERSON_RE.search(
        "Anna Beispiel Mustermann 01.03.2023 150.00"
    )
    assert m is not None


def test_leistungserbringer_whitelist_gelistet():
    """Label-Wörter sind in der Whitelist hinterlegt (Anonymizer überspringt sie)."""
    assert "Selbstbehalt" in LEISTUNGS_LABEL_WHITELIST
    assert "Franchise" in LEISTUNGS_LABEL_WHITELIST
    assert "Total" in LEISTUNGS_LABEL_WHITELIST


def test_leistungserbringer_ohne_betrag_kein_match():
    """Name + Datum ohne Betrag → kein Match (zu wenig Tabellenkontext)."""
    assert LEISTUNGSERBRINGER_PERSON_RE.search("Beispiel Mustermann 22.12.2023") is None


def test_leistungserbringer_satz_kein_match():
    """Gewöhnlicher Satz ohne Datum+Betrag → kein Match."""
    assert LEISTUNGSERBRINGER_PERSON_RE.search(
        "Der Betrag wurde am Stichtag ausbezahlt."
    ) is None


# --------------------------------------------------------------------------- #
# R2: OCR-verstümmelte Telefonnummern hinter Telefon-Label (260612-m8t)        #
# --------------------------------------------------------------------------- #
# Scan-OCR liefert hinter "Telefon" oft Müll-Trennzeichen (Anführungszeichen,
# Plus) statt sauberer Spaces: 'Telefon "4 43 244 6854'. PHONE_RE verlangt
# strikte Trenner und fasst das nicht. PHONE_OCR_RE matcht NUR mit Label-Prefix.

from extractors.pii_patterns import PHONE_OCR_RE


def test_phone_ocr_match_mit_anfuehrungszeichen():
    """OCR-Müll hinter Telefon-Label (Anführungszeichen-Präfix) → Match."""
    m = PHONE_OCR_RE.search('Telefon "4 43 244 6854')
    assert m is not None
    digits = "".join(ch for ch in m.group(0) if ch.isdigit())
    assert 9 <= len(digits) <= 11


def test_phone_ocr_match_tel_kurzform():
    """Kurzform-Label 'Tel.' mit Plus-Präfix → Match."""
    m = PHONE_OCR_RE.search("Tel. +41 44 123 45 67")
    assert m is not None


def test_phone_ocr_ohne_label_kein_match():
    """Ziffernfolge OHNE Telefon-Label → kein Match (Valoren/Identifier-Schutz)."""
    assert PHONE_OCR_RE.search('Valor "4 43 244 6854') is None
    assert PHONE_OCR_RE.search("4 43 244 6854") is None


def test_phone_ocr_label_allein_bleibt():
    """'Telefon' ohne folgende Ziffern → kein Match."""
    assert PHONE_OCR_RE.search("Telefon und Fax auf Anfrage") is None


def test_variant_pairs_template_paarung_strukturell_korrekt():
    """Reverse-/Glued-Formen mappen auf dieselbe Form des Fantasie-Namens."""
    pairs = dict(generate_name_variant_pairs("Hans", "Muster", "Felix", "Beispiel"))
    assert pairs["Muster, Hans"] == "Beispiel, Felix"
    assert pairs["MusterHans"] == "BeispielFelix"
    assert pairs["Herr Hans Muster"] == "Herr Felix Beispiel"


def test_variant_pairs_laengste_originale_zuerst():
    pairs = generate_name_variant_pairs("Bruno Kasimir", "Testmann", "Felix", "Beispiel")
    origs = [o for o, _ in pairs]
    assert origs == sorted(origs, key=lambda x: -len(x))
    assert len(origs) == len(set(origs))  # dedupliziert


# --------------------------------------------------------------------------- #
# Anonymizer-Lücken: Drittnamen, Adressen, Domains (Quick 260612-l2g, P1a-c)   #
# --------------------------------------------------------------------------- #
# Alle Strings sind rein synthetisch — keine echten Sample-Werte.

from extractors.pii_patterns import (
    DOMAIN_RE,
    PLZ_ORT_GLUED_RE,
    THIRD_PARTY_NAME_RE,
    is_domain_whitelisted,
)


# P1a — Drittpersonen-Namen "Nachname Vorname, Ort"
def test_third_party_name_re_matcht_namens_zeile():
    assert THIRD_PARTY_NAME_RE.search("Beispiel-Muster Erika, Musterstadt")
    assert THIRD_PARTY_NAME_RE.search("Testmann Bruno, Teststadt")


def test_third_party_name_re_ignoriert_gewoehnliche_wortfolge():
    """Konservativ: ohne Komma+Ort darf kein Satz matchen (kein False Positive)."""
    assert THIRD_PARTY_NAME_RE.search("Der Betrag wird ausbezahlt") is None
    assert THIRD_PARTY_NAME_RE.search("Total Ertrag Wertschriften") is None
    # Komma ohne nachgestellten Ort (Grossbuchstabe-Wort) → kein Match.
    assert THIRD_PARTY_NAME_RE.search("Guthaben, brutto") is None


# P1b — Adressen ohne Leerzeichen (PLZ+Ort glued)
def test_plz_ort_glued_re_matcht_glued_adresse():
    assert PLZ_ORT_GLUED_RE.search("8000Musterstadt")
    assert PLZ_ORT_GLUED_RE.search("8041Zuerich")


def test_plz_ort_glued_re_ignoriert_reine_zahl():
    assert PLZ_ORT_GLUED_RE.search("Steuerjahr 2023") is None
    assert PLZ_ORT_GLUED_RE.search("Betrag 1234.50") is None


# P1c — Domains
def test_domain_re_matcht_institut_domain():
    assert DOMAIN_RE.search("www.beispielbank.ch")
    assert DOMAIN_RE.search("Kontakt: www.muster.com")


def test_domain_whitelist_bleibt_stehen():
    assert is_domain_whitelisted("www.example.com") is True
    assert is_domain_whitelisted("www.example.ch") is True
    assert is_domain_whitelisted("www.beispielbank.ch") is False


# --------------------------------------------------------------------------- #
# Anonymizer-Anwendung: anonymize_word_text scrubt die neuen Pattern (P1a-c)   #
# --------------------------------------------------------------------------- #

from scripts.anonymize_belege import (
    _scrub_filename_stem,
    anonymize_word_text,
)


def _anon(text: str) -> str:
    """Hilfsaufruf mit leeren Maps — testet nur die generischen Pattern-Pässe."""
    return anonymize_word_text(text, person_token_map={}, amount_map={}, scale_factor=0.5)


def test_anonymize_word_text_scrubt_drittnamen():
    out = _anon("Beispiel-Muster Erika, Musterstadt")
    assert "Beispiel-Muster" not in out
    assert "Erika" not in out
    assert "PERSON-X" in out


def test_anonymize_word_text_scrubt_glued_adresse():
    out = _anon("Beispielstrasse34")
    assert "Beispielstrasse34" not in out
    assert "Musterstrasse" in out
    out2 = _anon("Teststr. 87")
    assert "Teststr. 87" not in out2
    out3 = _anon("8000Musterstadt")
    assert "8000Musterstadt" not in out3
    assert "Musterhausen" in out3


def test_anonymize_word_text_scrubt_domain_aber_whitelist_bleibt():
    out = _anon("www.beispielbank.ch")
    assert "beispielbank" not in out
    assert "INST-X" in out
    # Whitelist bleibt unangetastet.
    assert _anon("www.example.com") == "www.example.com"


# P2 — Dateinamen-Scrub
def test_scrub_filename_stem_ersetzt_firm_und_family():
    firm_map = {"BeispielFirma": "INST-A", "MusterPartei": "INST-B"}
    family_pairs = [("RealName", "Hans Muster")]
    out = _scrub_filename_stem("Lohnausweis_BeispielFirma", firm_map, family_pairs)
    assert "BeispielFirma" not in out
    assert "INST-A" in out
    out2 = _scrub_filename_stem("Spende_MusterPartei", firm_map, family_pairs)
    assert "MusterPartei" not in out2
    assert "INST-B" in out2
    out3 = _scrub_filename_stem("Beleg_RealName", firm_map, family_pairs)
    assert "RealName" not in out3
    # Gescrubbter Stem ist ungleich Original (wird so im Mapping dokumentiert).
    assert out3 != "Beleg_RealName"
