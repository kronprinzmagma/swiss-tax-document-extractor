#!/bin/bash
# Sucht korrekturen.json in den lokalen APFS-Schnappschuessen.
#
# macOS legt stuendlich Schnappschuesse des Datenvolumes an, sobald Time
# Machine eingerichtet ist — auch ohne angeschlossene Backup-Platte. Sie
# liegen auf der internen Platte und sind lesbar, man muss sie nur einhaengen.
#
# Das Skript haengt jeden Schnappschuss schreibgeschuetzt ein, zaehlt die
# Eintraege in korrekturen.json, haengt ihn wieder aus und zeigt eine Liste.
# Danach kann man den besten gezielt zurueckholen.
#
# Braucht sudo (Einhaengen ist eine privilegierte Operation) und aendert
# nichts: alle Mounts sind read-only, geschrieben wird nur nach --holen.
#
#   sudo bash scripts/schnappschuss_suche.sh
#   sudo bash scripts/schnappschuss_suche.sh --holen com.apple.TimeMachine.2026-09-09-114557.local

set -u

VOLUME="/System/Volumes/Data"
MNT="/tmp/steuer-schnappschuss"
REL="Users/$(stat -f%Su "$HOME")/Projekte/steuer-extraktor"
ZIEL_JSON="output/latest/json/korrekturen.json"
ZIEL_JSON2="output/latest/korrekturen.json"

if [ "$(id -u)" -ne 0 ]; then
  echo "Bitte mit sudo starten:  sudo bash $0" >&2
  exit 1
fi

zaehle() {   # $1 = Pfad zur JSON
  [ -f "$1" ] || { echo "-"; return; }
  /usr/bin/python3 - "$1" <<'PY' 2>/dev/null || echo "?"
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    print("?"); raise SystemExit
inhalt = ("soll","person","aussteller","zielwert_neu","ziffer_neu",
          "bestaetigt_betrag","bestaetigt_person","wert_betrag","wert_person",
          "neu","entfernt","label","kontext")
voll = sum(1 for e in d.values()
           if isinstance(e, dict) and any(e.get(f) for f in inhalt))
print(f"{len(d)} / {voll}")
PY
}

LETZTER_FEHLER=""
einhaengen() {  # $1 = Snapshot-Name
  mkdir -p "$MNT"
  LETZTER_FEHLER="$(mount_apfs -o ro -s "$1" "$VOLUME" "$MNT" 2>&1)"
}
# Ein verschluckter Fehler kostet mehr Zeit als jeder Fehler selbst: die
# erste Fassung meldete nur "nicht einhaengbar" und verschwieg, dass macOS
# dafuer Festplattenvollzugriff verlangt.

aushaengen() {
  umount "$MNT" 2>/dev/null || diskutil unmount force "$MNT" >/dev/null 2>&1
}

if [ "${1:-}" = "--holen" ]; then
  SNAP="${2:-}"
  [ -n "$SNAP" ] || { echo "Aufruf: sudo bash $0 --holen <snapshot-name>" >&2; exit 1; }
  aushaengen
  einhaengen "$SNAP"
  if ! mount | grep -q " $MNT "; then
    echo "Konnte $SNAP nicht einhaengen: $LETZTER_FEHLER" >&2
    exit 1
  fi
  ok=0
  for rel in "$ZIEL_JSON" "$ZIEL_JSON2"; do
    quelle="$MNT/$REL/$rel"
    if [ -f "$quelle" ]; then
      name="korrekturen-aus-${SNAP##*TimeMachine.}"
      name="${name%.local}.json"
      ziel="$HOME/Projekte/steuer-extraktor/output/latest/$name"
      cp "$quelle" "$ziel"
      chown "$(stat -f%Su "$HOME")" "$ziel"
      echo "geholt: $ziel   ($(zaehle "$quelle") Eintraege / mit Inhalt)"
      ok=1
      break
    fi
  done
  [ "$ok" = 1 ] || echo "In $SNAP liegt keine korrekturen.json."
  aushaengen
  echo
  echo "Danach:  make korrekturen-zurueck-pruefen"
  exit 0
fi

echo "Schnappschuesse mit korrekturen.json   (Eintraege / davon mit Inhalt)"
echo "──────────────────────────────────────────────────────────────────────"
aushaengen
for snap in $(tmutil listlocalsnapshots "$VOLUME" 2>/dev/null | tail -n +2); do
  einhaengen "$snap"
  if mount | grep -q " $MNT "; then
    a=$(zaehle "$MNT/$REL/$ZIEL_JSON")
    b=$(zaehle "$MNT/$REL/$ZIEL_JSON2")
    printf "   %-46s  json/: %-12s latest/: %s\n" "${snap##*TimeMachine.}" "$a" "$b"
    aushaengen
  else
    printf "   %-46s  %s\n" "${snap##*TimeMachine.}" "$LETZTER_FEHLER"
    if echo "$LETZTER_FEHLER" | grep -q "not permitted"; then
      echo
      echo "macOS verweigert das Einhaengen. Das Programm, in dem dieses"
      echo "Terminal laeuft, braucht Festplattenvollzugriff:"
      echo "   Systemeinstellungen → Datenschutz & Sicherheit →"
      echo "   Festplattenvollzugriff → Terminal (bzw. die genutzte App)"
      echo "   aktivieren, App neu starten, Skript erneut ausfuehren."
      echo
      echo "Alternative ohne Rechtevergabe: Finder oeffnen, ⇧⌘G →"
      echo "   ~/Projekte/steuer-extraktor/output/latest/json"
      echo "   dann Time Machine oeffnen und auf gestern zurueckblaettern."
      break
    fi
  fi
done
echo
echo "Den besten holen:"
echo "   sudo bash scripts/schnappschuss_suche.sh --holen <name aus der Liste>"
echo "   (Name inkl. 'com.apple.TimeMachine.' davor und '.local' dahinter)"
