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
find dist/code -name "__pycache__" -prune -exec rm -rf {} +
(cd dist/code && zip -qr -X ../code.zip .)
SHA="$(sha256sum dist/code.zip | cut -d' ' -f1)"
TAILLE="$(stat -c %s dist/code.zip 2>/dev/null || stat -f %z dist/code.zip)"
printf '{"version": "%s", "moteur": "%s", "sha256": "%s", "taille": %s}\n' "$VERSION" "$MOTEUR" "$SHA" "$TAILLE" > dist/maj.json
cat dist/maj.json
