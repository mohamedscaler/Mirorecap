#!/bin/bash
# Construit la mise à jour légère : dist/code.zip (le code de l'app, sans le moteur)
# et dist/maj.json (version, moteur requis, empreinte SHA256 pour vérifier le téléchargement).
set -euo pipefail
cd "$(dirname "$0")/.."
VERSION="$(tr -d '[:space:]' < app/VERSION)"
MOTEUR="$(tr -d '[:space:]' < app/MOTEUR)"
rm -rf dist/code dist/code.zip dist/maj.json
mkdir -p dist/code
cp -R app/app.py app/fenetre.py app/demarrage.py app/VERSION app/MOTEUR app/icone.png app/ui dist/code/
cp install-distant.sh dist/code/
# Outil natif de capture du son du Mac : compilé ici quand on est sur un Mac (GitHub Actions),
# pour pouvoir le corriger avec une mise à jour légère
if [ "$(uname)" = "Darwin" ]; then
  clang -O2 -fobjc-arc -arch arm64 -mmacosx-version-min=13.5 -framework Foundation -framework ScreenCaptureKit \
    -framework CoreMedia -framework AudioToolbox -o dist/code/capture-systeme app/capture-systeme.m
  codesign --force --sign - dist/code/capture-systeme
fi
find dist/code -name "__pycache__" -prune -exec rm -rf {} +
(cd dist/code && zip -qr -X ../code.zip .)
SHA="$(python3 -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' dist/code.zip)"
TAILLE="$(python3 -c 'import os,sys; print(os.path.getsize(sys.argv[1]))' dist/code.zip)"
printf '{"version": "%s", "moteur": "%s", "sha256": "%s", "taille": %s}\n' "$VERSION" "$MOTEUR" "$SHA" "$TAILLE" > dist/maj.json
cat dist/maj.json
