# -*- coding: utf-8 -*-
"""Tout ce qui dépend du système d'exploitation, rangé à part du reste de l'app.

app.py et fenetre.py n'appellent jamais macOS directement : ils passent par les modules
choisis ici. Une version Windows fournira des modules aux mêmes noms de fonctions.

    systeme  : dossiers, notifications, Finder, Corbeille, Rappels, démarrage automatique…
    audio    : conversion des fichiers audio, capture du son de l'ordinateur
    whisper  : moteur de transcription locale et liste des modèles
    menus    : icône dans la barre des menus (chargé à part : voir charger_menus)
    fenetre  : réglages natifs de la fenêtre (chargé à part par fenetre.py)
"""
import sys

MAC = sys.platform == "darwin"
WINDOWS = sys.platform == "win32"

if MAC:
    from . import systeme_macos as systeme
    from . import audio_macos as audio
    from . import whisper_mlx as whisper
else:
    raise ImportError(f"MiroRecap ne fonctionne pas encore sur ce système ({sys.platform})")


def charger_menus():
    """Module de l'icône de la barre des menus (import lourd : seulement pour l'app principale)."""
    if MAC:
        from . import menus_macos
        return menus_macos


def charger_fenetre():
    """Réglages natifs de la fenêtre (seulement pour le processus de la fenêtre)."""
    if MAC:
        from . import fenetre_macos
        return fenetre_macos
