#!/bin/bash
# Désinstalle MiroRecap (les comptes rendus déjà créés sont conservés)
PLIST="$HOME/Library/LaunchAgents/fr.mirorecap.app.plist"

launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || true
pkill -f "MiroRecap/app.py" 2>/dev/null || true
rm -f "$PLIST"
rm -rf "$HOME/Applications/MiroRecap.app" "$HOME/Library/Application Support/MiroRecap"

echo "✅ MiroRecap désinstallé. Tes comptes rendus sont conservés."
