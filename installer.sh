#!/bin/bash
# Installe (ou met à jour) MiroRecap. Relancer ce script = mise à jour, la clé API est conservée.
set -e
cd "$(dirname "$0")"

DEST="$HOME/Library/Application Support/MiroRecap"
BUNDLE="$HOME/Applications/MiroRecap.app"
PLIST="$HOME/Library/LaunchAgents/fr.mirorecap.app.plist"

echo "🎙  Installation de MiroRecap $(cat app/VERSION 2>/dev/null)…"

# 1. Mac Apple Silicon obligatoire (Whisper tourne sur la puce)
if [ "$(uname -m)" != "arm64" ]; then
  echo "❌ Il faut un Mac avec puce Apple (M1 ou plus récent)."
  exit 1
fi

# 2. Homebrew (parfois installé mais absent du PATH de ce terminal)
if ! command -v brew >/dev/null 2>&1 && [ -x /opt/homebrew/bin/brew ]; then
  eval "$(/opt/homebrew/bin/brew shellenv)"
fi
if ! command -v brew >/dev/null 2>&1; then
  echo "→ Installation de Homebrew (ton mot de passe Mac va être demandé, il ne s'affiche pas quand tu tapes)…"
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  eval "$(/opt/homebrew/bin/brew shellenv)"
fi

# 3. ffmpeg (lecture des .m4a) + Python 3.12
echo "→ Dépendances système (ffmpeg, Python)…"
brew list ffmpeg >/dev/null 2>&1 || brew install ffmpeg
brew list python@3.12 >/dev/null 2>&1 || brew install python@3.12
PY="$(brew --prefix python@3.12)/bin/python3.12"

# 4. Arrête une éventuelle version en cours
launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || true
pkill -f "compte-rendu/app.py|CompteRendu/app.py|MiroRecap/app.py|MiroRecap/fenetre.py" 2>/dev/null || true

# Ancienne version "CompteRendu" : on récupère la clé API puis on la retire
ANCIEN="$HOME/Library/Application Support/CompteRendu"
if [ -f "$ANCIEN/config.json" ] && [ ! -f "$DEST/config.json" ]; then
  mkdir -p "$DEST" && cp "$ANCIEN/config.json" "$DEST/config.json"
fi
launchctl bootout "gui/$(id -u)" "$HOME/Library/LaunchAgents/fr.compterendu.app.plist" 2>/dev/null || true
rm -rf "$ANCIEN" "$HOME/Applications/CompteRendu.app" "$HOME/Library/LaunchAgents/fr.compterendu.app.plist"

# 5. Code + environnement Python
echo "→ Installation des modules Python (quelques minutes)…"
mkdir -p "$DEST"
cp app/app.py app/fenetre.py app/requirements.txt app/VERSION app/icone.png "$DEST/"
rm -rf "$DEST/ui" && cp -R app/ui "$DEST/ui"
[ -x "$DEST/.venv/bin/python" ] || "$PY" -m venv "$DEST/.venv"
"$DEST/.venv/bin/pip" install --upgrade pip -q
"$DEST/.venv/bin/pip" install -r "$DEST/requirements.txt" -q

# 6. App macOS (lanceur natif : nécessaire pour l'autorisation micro)
echo "→ Création de l'app…"
rm -rf "$BUNDLE"
mkdir -p "$BUNDLE/Contents/MacOS"
clang -O2 -o "$BUNDLE/Contents/MacOS/MiroRecap" app/lanceur.c

# Icône de l'app (Finder, Launchpad) : .icns construit à partir du PNG
mkdir -p "$BUNDLE/Contents/Resources"
ICONSET="$(mktemp -d)/MiroRecap.iconset"
mkdir -p "$ICONSET"
for T in 16 32 128 256 512; do
  sips -z $T $T app/icone.png --out "$ICONSET/icon_${T}x${T}.png" >/dev/null
  sips -z $((T*2)) $((T*2)) app/icone.png --out "$ICONSET/icon_${T}x${T}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o "$BUNDLE/Contents/Resources/MiroRecap.icns" || true
VERSION="$(tr -d '[:space:]' < app/VERSION)"
cat > "$BUNDLE/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>MiroRecap</string>
  <key>CFBundleDisplayName</key><string>MiroRecap</string>
  <key>CFBundleIdentifier</key><string>fr.mirorecap.app</string>
  <key>CFBundleExecutable</key><string>MiroRecap</string>
  <key>CFBundleIconFile</key><string>MiroRecap</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>$VERSION</string>
  <key>CFBundleVersion</key><string>$VERSION</string>
  <key>LSMinimumSystemVersion</key><string>13.5</string>
  <key>LSUIElement</key><true/>
  <key>NSMicrophoneUsageDescription</key><string>MiroRecap enregistre tes appels et réunions pour les transcrire.</string>
</dict>
</plist>
EOF
codesign --force --sign - "$BUNDLE" >/dev/null 2>&1 || true
xattr -dr com.apple.quarantine "$BUNDLE" "$DEST" 2>/dev/null || true

# 7. Lancement automatique à l'ouverture de session (+ lancement immédiat)
mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>fr.mirorecap.app</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/open</string>
    <string>-a</string>
    <string>$BUNDLE</string>
  </array>
  <key>RunAtLoad</key><true/>
</dict>
</plist>
EOF
launchctl bootstrap "gui/$(id -u)" "$PLIST"

echo ""
echo "✅ Installé ! Le 🎙 apparaît dans la barre de menus en haut."
echo "   Au premier lancement : colle ta clé API et accepte l'accès au micro."
