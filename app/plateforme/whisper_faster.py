# -*- coding: utf-8 -*-
"""Transcription locale avec faster-whisper (CTranslate2) : Windows, sur le processeur ou une
carte graphique NVIDIA. Même interface que whisper_mlx.py."""
import os
import threading

MODELES = [
    {"id": "Systran/faster-whisper-small", "nom": "Normal", "taille": "environ 500 Mo",
     "detail": "Très rapide et léger. Moins fiable sur les noms propres et les chiffres."},
    {"id": "mobiuslabsgmbh/faster-whisper-large-v3-turbo", "nom": "Turbo", "taille": "environ 1,6 Go",
     "detail": "Précis et rapide. Recommandé pour la plupart des appels."},
    {"id": "Systran/faster-whisper-large-v3", "nom": "Pro", "taille": "environ 3 Go",
     "detail": "Le plus précis (accents, bruit, jargon), environ deux fois plus lent."},
]
MODELE_RECOMMANDE = "mobiuslabsgmbh/faster-whisper-large-v3-turbo"
MODELE_PRO = "Systran/faster-whisper-large-v3"

_MODELES_CHARGES = {}          # dossier → WhisperModel (chargé une fois, gardé en mémoire)
_VERROU = threading.Lock()
_GPU_EN_PANNE = False          # la carte graphique a échoué une fois : on reste sur le processeur


def modele_complet(dossier):
    """Le modèle téléchargé contient-il tout ce qu'il faut ?"""
    return (dossier / "config.json").exists() and (dossier / "model.bin").exists()


def gpu_disponible():
    """Carte NVIDIA utilisable par CTranslate2 (pilote CUDA présent)."""
    if _GPU_EN_PANNE or os.environ.get("MIRORECAP_CPU") == "1":
        return False
    try:
        import ctranslate2
        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


# Sans carte NVIDIA, Whisper tourne sur le processeur : Turbo et Pro y sont nettement plus lents
DETAILS_PROCESSEUR = {
    "Systran/faster-whisper-small": "Recommandé sur ce PC (pas de carte NVIDIA) : rapide sur le processeur. "
                                    "Moins fiable sur les noms propres et les chiffres.",
    "mobiuslabsgmbh/faster-whisper-large-v3-turbo": "Précis, mais lent sur ce PC sans carte NVIDIA : "
                                                    "le compte rendu arrive nettement plus tard.",
    "Systran/faster-whisper-large-v3": "Le plus précis, mais très lent sur ce PC sans carte NVIDIA.",
}


def modele_recommande():
    """Turbo avec une carte NVIDIA, sinon le modèle léger (le processeur ne suit pas Turbo en direct)."""
    return MODELE_RECOMMANDE if gpu_disponible() else MODELES[0]["id"]


def modeles():
    """Liste des modèles affichée dans l'app, avec des détails adaptés quand il n'y a pas de carte NVIDIA."""
    if gpu_disponible():
        return MODELES
    return [dict(m, detail=DETAILS_PROCESSEUR.get(m["id"], m["detail"])) for m in MODELES]


def _charger(dossier, gpu):
    from faster_whisper import WhisperModel   # import tardif : lourd à charger
    cle = (str(dossier), gpu)
    with _VERROU:
        if cle not in _MODELES_CHARGES:
            _MODELES_CHARGES.clear()   # un seul modèle en mémoire (le Pro pèse 3 Go)
            if gpu:
                _MODELES_CHARGES[cle] = WhisperModel(str(dossier), device="cuda", compute_type="float16")
            else:
                coeurs = os.cpu_count() or 4
                _MODELES_CHARGES[cle] = WhisperModel(str(dossier), device="cpu", compute_type="int8",
                                                     cpu_threads=max(1, min(8, coeurs - 1)))
        return _MODELES_CHARGES[cle]


def _vers_dict(seg):
    """Segment faster-whisper → même dictionnaire que mlx_whisper."""
    mots = [{"word": m.word, "start": m.start, "end": m.end, "probability": m.probability}
            for m in (seg.words or [])]
    return {"id": seg.id, "seek": seg.seek, "start": seg.start, "end": seg.end, "text": seg.text,
            "tokens": list(seg.tokens), "temperature": seg.temperature, "avg_logprob": seg.avg_logprob,
            "compression_ratio": seg.compression_ratio, "no_speech_prob": seg.no_speech_prob, "words": mots}


def _transcrire_avec(model, audio, langue, consigne, suivi, mots):
    segments, infos = model.transcribe(
        audio,
        language=langue,
        beam_size=1,                        # recherche simple, comme mlx_whisper : bien plus rapide sur processeur
        initial_prompt=consigne,            # vocabulaire et noms des projets
        condition_on_previous_text=False,   # évite les boucles et les phrases inventées en série
        word_timestamps=mots,
        vad_filter=False,                   # l'app découpe déjà la parole elle-même
    )
    resultat = []
    total = max(1, int(round(infos.duration * 100)))   # avancement en centièmes de seconde
    with suivi(total=total, unit="cs", disable=False) as barre:
        fait = 0
        for seg in segments:                # la transcription se fait au fil de cette boucle
            resultat.append(_vers_dict(seg))
            avance = min(total, int(seg.end * 100)) - fait
            if avance > 0:
                barre.update(avance)
                fait += avance
        if total > fait:
            barre.update(total - fait)
    return resultat


def transcrire(audio, modele, langue, consigne, suivi, mots=False):
    """audio : tableau float32 16 kHz mono. suivi : classe façon tqdm qui reçoit l'avancement.
    mots : horodatage mot par mot. Retourne les segments (start, end, text, no_speech_prob,
    avg_logprob, words), comme whisper_mlx.transcrire."""
    global _GPU_EN_PANNE
    gpu = gpu_disponible()
    try:
        return _transcrire_avec(_charger(modele, gpu), audio, langue, consigne, suivi, mots)
    except Exception as e:
        if not gpu:
            raise
        # Bibliothèques CUDA absentes ou carte trop ancienne : on bascule sur le processeur
        print(f"Carte graphique inutilisable pour Whisper ({e}) : processeur", flush=True)
        _GPU_EN_PANNE = True
        return _transcrire_avec(_charger(modele, False), audio, langue, consigne, suivi, mots)


def precharger(modele):
    """Charge le modèle à l'avance : la première transcription le trouve déjà prêt."""
    _charger(modele, gpu_disponible())


def liberer():
    """Retire le modèle de la mémoire. Le prochain appel le recharge."""
    with _VERROU:
        if not _MODELES_CHARGES:
            return
        _MODELES_CHARGES.clear()
    import gc
    gc.collect()
