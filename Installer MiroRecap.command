#!/bin/bash
# Installateur MiroRecap : double-clique sur ce fichier.
# Il télécharge la dernière version depuis GitHub et l'installe.

clear
echo ""
echo "  🎙  MiroRecap"
echo "  Installation en cours, garde cette fenêtre ouverte."
echo ""

/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/mohamedscaler/Mirorecap/main/install-distant.sh)"
CODE=$?

echo ""
if [ $CODE -eq 0 ]; then
  echo "  Terminé. Tu peux fermer cette fenêtre."
else
  echo "  L'installation a échoué. Envoie une capture de cette fenêtre à Miro."
fi
echo ""
read -n 1 -s -r -p "  Appuie sur une touche pour fermer…"
