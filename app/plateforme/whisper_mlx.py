# -*- coding: utf-8 -*-
"""Transcription locale avec Whisper MLX (Mac Apple Silicon)."""
import sys
import types

MODELES = [
    {"id": "mlx-community/whisper-small-mlx", "nom": "Normal", "taille": "environ 500 Mo",
     "detail": "Très rapide et léger. Moins fiable sur les noms propres et les chiffres."},
    {"id": "mlx-community/whisper-large-v3-turbo", "nom": "Turbo", "taille": "environ 1,6 Go",
     "detail": "Précis et rapide. Recommandé pour la plupart des appels."},
    {"id": "mlx-community/whisper-large-v3-mlx", "nom": "Pro", "taille": "environ 3 Go",
     "detail": "Le plus précis (accents, bruit, jargon), environ deux fois plus lent."},
]
MODELE_RECOMMANDE = "mlx-community/whisper-large-v3-turbo"
MODELE_PRO = "mlx-community/whisper-large-v3-mlx"


def modele_recommande():
    """Modèle proposé par défaut (au premier lancement)."""
    return MODELE_RECOMMANDE


def modeles():
    """Liste des modèles affichée dans l'app."""
    return MODELES


def modele_complet(dossier):
    """Le modèle téléchargé contient-il tout ce qu'il faut ?"""
    return (dossier / "config.json").exists() and any(dossier.glob("weights.*"))


def transcrire(audio, modele, langue, consigne, suivi, mots=False):
    """audio : tableau float32 16 kHz mono. suivi : classe façon tqdm qui reçoit l'avancement.
    mots : horodatage mot par mot (segments calés précisément, utile quand l'audio a de longs silences).
    Retourne les segments de Whisper (start, end, text, no_speech_prob, avg_logprob, words)."""
    import mlx_whisper  # import tardif : lourd à charger
    module = sys.modules.get("mlx_whisper.transcribe")
    if module is not None and hasattr(module, "tqdm"):
        module.tqdm = types.SimpleNamespace(tqdm=suivi)
    resultat = mlx_whisper.transcribe(
        audio,
        path_or_hf_repo=modele,
        language=langue,
        verbose=False,                      # active le suivi (sans rien afficher)
        initial_prompt=consigne,            # vocabulaire et noms des projets
        condition_on_previous_text=False,   # évite les boucles et les phrases inventées en série
        word_timestamps=mots,
    )
    return resultat.get("segments", [])


def liberer():
    """Retire le modèle de la mémoire. mlx_whisper le garde sinon jusqu'à la fermeture de l'app
    (1,5 à 3 Go, plus la réserve de mémoire graphique de MLX). Le prochain appel le recharge."""
    module = sys.modules.get("mlx_whisper.transcribe")
    gardien = getattr(module, "ModelHolder", None)
    if gardien is None or gardien.model is None:
        return
    gardien.model = None
    gardien.model_path = None
    import gc
    import mlx.core as mx
    gc.collect()
    mx.clear_cache()
