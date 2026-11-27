# -*- coding: utf-8 -*-
"""Tout ce qui dépend du système d'exploitation, rangé à part du reste de l'app.

app.py et fenetre.py n'appellent jamais macOS directement : ils passent par les modules
choisis ici. Les modules Windows ont les mêmes noms de fonctions.

    systeme  : dossiers, notifications, Finder, Corbeille, Rappels, démarrage automatique…
    audio    : conversion des fichiers audio, capture du son de l'ordinateur
    whisper  : moteur de transcription locale et liste des modèles
    menus    : icône dans la barre des menus (chargé à part : voir charger_menus)
    fenetre  : réglages natifs de la fenêtre (chargé à part par fenetre.py)
"""
import sys

# Numéro du moteur (Python + modules) livré avec l'app : il diffère entre Mac et Windows.
# Une mise à jour légère du code n'est installée que si elle est prévue pour ce moteur.
FICHIER_MOTEUR = "MOTEUR_WINDOWS" if sys.platform == "win32" else "MOTEUR"
CLE_MOTEUR = "moteur_windows" if sys.platform == "win32" else "moteur"   # champ de maj.json

MAC = sys.platform == "darwin"
WINDOWS = sys.platform == "win32"

if MAC:
    from . import systeme_macos as systeme
    from . import audio_macos as audio
    from . import whisper_mlx as whisper
elif WINDOWS:
    from . import systeme_windows as systeme
    from . import audio_windows as audio
    from . import whisper_faster as whisper
else:
    raise ImportError(f"MiroRecap ne fonctionne pas encore sur ce système ({sys.platform})")


def charger_menus():
    """Module de l'icône de la barre des menus (import lourd : seulement pour l'app principale)."""
    if MAC:
        from . import menus_macos
        return menus_macos
    from . import menus_windows
    return menus_windows


def charger_fenetre():
    """Réglages natifs de la fenêtre (seulement pour le processus de la fenêtre)."""
    if MAC:
        from . import fenetre_macos
        return fenetre_macos
    from . import fenetre_windows
    return fenetre_windows
