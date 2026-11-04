#!/bin/bash
# Installe ou met à jour MiroRecap depuis la dernière release GitHub (MiroRecap.dmg).
#
# - Installation / migration :
#     /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/mohamedscaler/Mirorecap/main/install-distant.sh)"
# - Mise à jour depuis l'app (4.9+) : l'app télécharge le DMG, lance une copie de ce
#   script puis se ferme d'elle-même. Variables transmises :
#     MIRORECAP_DMG     DMG déjà téléchargé
#     MIRORECAP_PIDS    processus de l'app à attendre avant de remplacer quoi que ce soit
#     MIRORECAP_DISCRET 1 = relance dans la barre des menus seulement (mise à jour auto)
set -e

DEPOT="mohamedscaler/Mirorecap"
DMG_URL="https://github.com/$DEPOT/releases/latest/download/MiroRecap.dmg"
CIBLE="${MIRORECAP_CIBLE:-/Applications/MiroRecap.app}"
echo "$(date '+%Y-%m-%d %H:%M:%S') · début de l'installation"

if [ "$(uname -m)" != "arm64" ]; then
  echo "❌ Il faut un Mac avec puce Apple (M1 ou plus récent)."
  exit 1
fi

TMP="$(mktemp -d)"
trap 'hdiutil detach "$TMP/vol" -quiet 2>/dev/null || true; rm -rf "$TMP"' EXIT

# 1. Le DMG : déjà téléchargé par l'app (avec barre de progression), sinon on le télécharge
if [ -n "${MIRORECAP_DMG:-}" ] && [ -f "$MIRORECAP_DMG" ]; then
  mv "$MIRORECAP_DMG" "$TMP/MiroRecap.dmg"
else
  echo "⬇️  Téléchargement de MiroRecap…"
  curl -fL --progress-bar -o "$TMP/MiroRecap.dmg" "$DMG_URL"
fi

echo "→ Installation…"
mkdir -p "$TMP/vol"
hdiutil attach "$TMP/MiroRecap.dmg" -nobrowse -readonly -mountpoint "$TMP/vol" -quiet
NOUVELLE="$(tr -d '[:space:]' < "$TMP/vol/MiroRecap.app/Contents/Resources/app/VERSION")"
echo "   version à installer : $NOUVELLE"

# 2. Copie de la nouvelle version À CÔTÉ de l'ancienne (l'ancienne reste intacte si ça échoue)
rm -rf "$CIBLE.nouveau"
cp -R "$TMP/vol/MiroRecap.app" "$CIBLE.nouveau"
xattr -dr com.apple.quarantine "$CIBLE.nouveau" 2>/dev/null || true
[ -x "$CIBLE.nouveau/Contents/MacOS/MiroRecap" ] || { echo "❌ copie incomplète"; rm -rf "$CIBLE.nouveau"; exit 1; }

# 3. Arrêt complet de l'ancienne version
launchctl bootout "gui/$(id -u)" "$HOME/Library/LaunchAgents/fr.mirorecap.app.plist" 2>/dev/null || true

# 3a. Processus annoncés par l'app : elle se ferme d'elle-même, on attend (15 s max)
for _ in $(seq 1 30); do
  VIVANTS=""
  for PID in ${MIRORECAP_PIDS:-}; do kill -0 "$PID" 2>/dev/null && VIVANTS="$VIVANTS $PID"; done
  [ -z "$VIVANTS" ] && break
  sleep 0.5
done

# 3b. Tout ce qui tourne encore depuis le bundle (lanceur, moteur, fenêtre, capture du son)
MOTIF="MiroRecap\.app/Contents/|Resources/app/(app|fenetre)\.py|MiroRecap/(app|fenetre)\.py"
encore_lance() { pgrep -f "$MOTIF" >/dev/null || pgrep -x MiroRecap >/dev/null; }
if encore_lance; then
  pkill -TERM -f "$MOTIF" 2>/dev/null || true
  pkill -TERM -x MiroRecap 2>/dev/null || true
  for _ in $(seq 1 10); do encore_lance || break; sleep 0.5; done
fi
if encore_lance; then
  echo "   arrêt forcé de l'ancienne version"
  pkill -KILL -f "$MOTIF" 2>/dev/null || true
  pkill -KILL -x MiroRecap 2>/dev/null || true
  sleep 1
fi
if encore_lance; then
  echo "❌ impossible d'arrêter l'ancienne version, installation annulée"
  rm -rf "$CIBLE.nouveau"
  open "$CIBLE" 2>/dev/null || true
  exit 1
fi

# 4. Remplacement d'un seul coup (renommages instantanés), retour arrière si ça échoue
rm -rf "$CIBLE.ancien"
[ -d "$CIBLE" ] && mv "$CIBLE" "$CIBLE.ancien"
if ! mv "$CIBLE.nouveau" "$CIBLE"; then
  echo "❌ remplacement impossible, retour à l'ancienne version"
  [ -d "$CIBLE.ancien" ] && mv "$CIBLE.ancien" "$CIBLE"
  open "$CIBLE" 2>/dev/null || true
  exit 1
fi
rm -rf "$CIBLE.ancien"

# Anciennes versions installées en Terminal (la clé API et les réglages sont gardés)
rm -rf "$HOME/Applications/MiroRecap.app" "$HOME/Applications/CompteRendu.app"
rm -rf "$HOME/Library/Application Support/MiroRecap/.venv" "$HOME/Library/Application Support/MiroRecap/ui"

# 5. Relance (mise à jour auto : discrète, dans la barre des menus seulement)
if [ "${MIRORECAP_DISCRET:-}" = "1" ]; then
  open "$CIBLE" --args --demarrage
else
  open "$CIBLE"
fi

# 6. Contrôle : la version installée est bien la nouvelle, et elle tourne
INSTALLEE="$(tr -d '[:space:]' < "$CIBLE/Contents/Resources/app/VERSION")"
sleep 4
if pgrep -f "MiroRecap\.app/Contents/Resources/app/app\.py" >/dev/null; then
  echo "✅ MiroRecap $INSTALLEE est installé et lancé."
else
  echo "⚠️  MiroRecap $INSTALLEE est installé mais ne s'est pas relancé, nouvel essai"
  open "$CIBLE"
fi
