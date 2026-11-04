#!/bin/bash
# Installe ou met à jour MiroRecap depuis la dernière release GitHub (MiroRecap.dmg).
# Utilisé par la mise à jour intégrée à l'app, et pour migrer les anciennes versions.
#   /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/mohamedscaler/Mirorecap/main/install-distant.sh)"
set -e

DEPOT="mohamedscaler/Mirorecap"
DMG_URL="https://github.com/$DEPOT/releases/latest/download/MiroRecap.dmg"
CIBLE="/Applications/MiroRecap.app"

if [ "$(uname -m)" != "arm64" ]; then
  echo "❌ Il faut un Mac avec puce Apple (M1 ou plus récent)."
  exit 1
fi

TMP="$(mktemp -d)"
trap 'hdiutil detach "$TMP/vol" -quiet 2>/dev/null || true; rm -rf "$TMP"' EXIT

# DMG déjà téléchargé par l'app (mise à jour avec barre de progression), sinon on le télécharge
if [ -n "${MIRORECAP_DMG:-}" ] && [ -f "$MIRORECAP_DMG" ]; then
  mv "$MIRORECAP_DMG" "$TMP/MiroRecap.dmg"
else
  echo "⬇️  Téléchargement de MiroRecap…"
  curl -fL --progress-bar -o "$TMP/MiroRecap.dmg" "$DMG_URL"
fi

echo "→ Installation…"
mkdir -p "$TMP/vol"
hdiutil attach "$TMP/MiroRecap.dmg" -nobrowse -readonly -mountpoint "$TMP/vol" -quiet

# Arrête la version en cours (ancienne ou nouvelle)
launchctl bootout "gui/$(id -u)" "$HOME/Library/LaunchAgents/fr.mirorecap.app.plist" 2>/dev/null || true
# Tout ce qui tourne depuis le bundle : lanceur, Python (moteur + fenêtre), capture du son
arreter_app() {
  pkill "$1" -f "MiroRecap.app/Contents/" 2>/dev/null || true
  pkill "$1" -f "Resources/app/(app|fenetre)\.py" 2>/dev/null || true
  pkill "$1" -f "MiroRecap/(app|fenetre)\.py" 2>/dev/null || true   # très anciennes versions
  pkill "$1" -x MiroRecap 2>/dev/null || true
}
arreter_app -TERM
for _ in 1 2 3 4 5; do
  pgrep -f "MiroRecap.app/Contents/|Resources/app/(app|fenetre)\.py" >/dev/null || break
  sleep 1
done
arreter_app -KILL   # ce qui refuse de s'arrêter
sleep 1

rm -rf "$CIBLE"
cp -R "$TMP/vol/MiroRecap.app" "$CIBLE"
xattr -dr com.apple.quarantine "$CIBLE" 2>/dev/null || true

# Anciennes versions installées en Terminal (la clé API et les réglages sont gardés)
rm -rf "$HOME/Applications/MiroRecap.app" "$HOME/Applications/CompteRendu.app"
rm -rf "$HOME/Library/Application Support/MiroRecap/.venv" "$HOME/Library/Application Support/MiroRecap/ui"

# Mise à jour automatique : relance discrète (barre des menus seulement)
if [ "${MIRORECAP_DISCRET:-}" = "1" ]; then
  open "$CIBLE" --args --demarrage
else
  open "$CIBLE"
fi
echo "✅ MiroRecap est installé dans Applications."
