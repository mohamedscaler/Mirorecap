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

echo "⬇️  Téléchargement de MiroRecap…"
curl -fL --progress-bar -o "$TMP/MiroRecap.dmg" "$DMG_URL"

echo "→ Installation…"
mkdir -p "$TMP/vol"
hdiutil attach "$TMP/MiroRecap.dmg" -nobrowse -readonly -mountpoint "$TMP/vol" -quiet

# Arrête la version en cours (ancienne ou nouvelle)
launchctl bootout "gui/$(id -u)" "$HOME/Library/LaunchAgents/fr.mirorecap.app.plist" 2>/dev/null || true
pkill -f "MiroRecap/app.py|MiroRecap/fenetre.py|MiroRecap.app/Contents/Resources/app/" 2>/dev/null || true
sleep 1

rm -rf "$CIBLE"
cp -R "$TMP/vol/MiroRecap.app" "$CIBLE"
xattr -dr com.apple.quarantine "$CIBLE" 2>/dev/null || true

# Anciennes versions installées en Terminal (la clé API et les réglages sont gardés)
rm -rf "$HOME/Applications/MiroRecap.app" "$HOME/Applications/CompteRendu.app"
rm -rf "$HOME/Library/Application Support/MiroRecap/.venv" "$HOME/Library/Application Support/MiroRecap/ui"

open "$CIBLE"
echo "✅ MiroRecap est installé dans Applications."
