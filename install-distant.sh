#!/bin/bash
# Installation en une ligne depuis GitHub :
# /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/mohamedscaler/Mirorecap/main/install-distant.sh)"
set -e

DEPOT="mohamedscaler/Mirorecap"
BRANCHE="main"

# Télécharge le projet dans un dossier temporaire (supprimé à la fin)
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "⬇️  Téléchargement de MiroRecap…"
curl -fsSL "https://github.com/$DEPOT/archive/refs/heads/$BRANCHE.tar.gz" | tar -xz -C "$TMP" --strip-components=1

bash "$TMP/installer.sh"
