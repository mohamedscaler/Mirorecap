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


def modele_complet(dossier):
    """Le modèle téléchargé contient-il tout ce qu'il faut ?"""
    return (dossier / "config.json").exists() and any(dossier.glob("weights.*"))


def transcrire(audio, modele, langue, consigne, suivi):
    """audio : tableau float32 16 kHz mono. suivi : classe façon tqdm qui reçoit l'avancement.
    Retourne les segments de Whisper (start, text, no_speech_prob, avg_logprob)."""
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
    )
    return resultat.get("segments", [])
