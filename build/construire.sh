#!/bin/bash
# Construit MiroRecap.app (autonome) puis MiroRecap.dmg.
# Lancé par GitHub Actions sur un Mac Apple Silicon (voir .github/workflows/dmg.yml).
set -euo pipefail
cd "$(dirname "$0")/.."

VERSION="$(tr -d '[:space:]' < app/VERSION)"
APP="dist/MiroRecap.app"
RES="$APP/Contents/Resources"
echo "▶ MiroRecap $VERSION"

rm -rf dist
mkdir -p "$APP/Contents/MacOS" "$RES/app"

# 1. Python autonome (python-build-standalone via uv), copié dans le bundle
uv python install 3.12
PY_SRC="$(dirname "$(dirname "$(uv python find 3.12)")")"
PY_SRC="$(cd "$PY_SRC" && pwd -P)"     # uv utilise un dossier raccourci : on prend le vrai
cp -R "$PY_SRC/" "$RES/python"
[ -L "$RES/python" ] && { echo "❌ Python copié comme raccourci"; exit 1; }
PY="$RES/python/bin/python3"
rm -f "$RES"/python/lib/python3.*/EXTERNALLY-MANAGED   # autorise pip dans ce Python
"$PY" -m ensurepip -q 2>/dev/null || true

# 2. Modules
"$PY" -m pip install -q --no-cache-dir --upgrade pip
"$PY" -m pip install -q --no-cache-dir -r app/requirements.txt
"$PY" -m pip install -q --no-cache-dir --no-deps mlx-whisper
"$PY" - <<'EOF'
# Vérifie que tout s'importe avant de livrer
import sys, rumps, sounddevice, soundfile, anthropic, webview, mlx.core, mlx_whisper
print("✓ imports OK depuis", sys.prefix)
EOF

# 3. Code de l'app
cp -R app/app.py app/fenetre.py app/VERSION app/icone.png app/ui "$RES/app/"

# 4. Lanceur natif
clang -O2 -fobjc-arc -arch arm64 -mmacosx-version-min=13.5 -framework Cocoa -framework UserNotifications -o "$APP/Contents/MacOS/MiroRecap" app/lanceur.m

# 5. Icône
ICONSET="dist/MiroRecap.iconset"
mkdir -p "$ICONSET"
for T in 16 32 128 256 512; do
  sips -z $T $T app/icone.png --out "$ICONSET/icon_${T}x${T}.png" >/dev/null
  sips -z $((T*2)) $((T*2)) app/icone.png --out "$ICONSET/icon_${T}x${T}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o "$RES/MiroRecap.icns"

# 6. Info.plist
cat > "$APP/Contents/Info.plist" <<EOF
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

# 6 ter. Exécutable Python nommé « MiroRecap » pour la fenêtre : macOS affiche ce nom dans le Dock
#        et le Cmd+Tab (au lieu de « python3.12 »). Même dossier, donc mêmes bibliothèques.
PY_REEL="$(cd "$RES/python/bin" && readlink python3 || echo python3)"
cp "$RES/python/bin/$PY_REEL" "$RES/python/bin/MiroRecap"

# 6 bis. Identité du Python embarqué : notifications au nom de MiroRecap (et non de l'Éditeur de script)
cat > "$RES/python/bin/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleIdentifier</key><string>fr.mirorecap.app</string>
  <key>CFBundleName</key><string>MiroRecap</string>
  <key>CFBundleDisplayName</key><string>MiroRecap</string>
</dict>
</plist>
EOF

# 7. Nettoyage (caches Python, tests inutiles) puis signature ad hoc
find "$RES/python" -name "__pycache__" -type d -prune -exec rm -rf {} +
codesign --force --deep --sign - "$APP" || echo "⚠ signature ad hoc incomplète"

# Contrôle : une app autonome pèse plusieurs centaines de Mo
TAILLE_MO=$(du -sm "$APP" | cut -f1)
echo "Taille de l'app : ${TAILLE_MO} Mo"
[ "$TAILLE_MO" -gt 80 ] || { echo "❌ App trop légère, Python ou les modules manquent"; exit 1; }

# 8. DMG : l'app + un raccourci vers Applications, à glisser
mkdir -p dist/dmg
cp -R "$APP" dist/dmg/
ln -s /Applications dist/dmg/Applications
# hdiutil échoue parfois sur les Mac de GitHub (« Resource busy ») : jusqu'à 5 essais
for ESSAI in 1 2 3 4 5; do
  if hdiutil create -volname "MiroRecap" -srcfolder dist/dmg -ov -format UDZO dist/MiroRecap.dmg; then break; fi
  [ "$ESSAI" = 5 ] && { echo "❌ Création du DMG impossible"; exit 1; }
  echo "hdiutil occupé, nouvel essai dans 10 s…"; sleep 10
done
du -h dist/MiroRecap.dmg
