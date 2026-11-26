# -*- coding: utf-8 -*-
"""Transcription d'un WAV factice (voix de synthèse française, voix.wav) avec faster-whisper,
d'abord par le module de plateforme, puis par le chemin complet de l'app (app.transcrire).
Modèle « Normal » (small, ~500 Mo, téléchargé une fois)."""
import unicodedata

import soundfile as sf

MODELE = "Systran/faster-whisper-small"


def _sans_accents(texte):
    return "".join(c for c in unicodedata.normalize("NFD", texte.lower()) if unicodedata.category(c) != "Mn")


class _Suivi:
    """Imite la barre façon tqdm que l'app passe à Whisper."""
    vu = []

    def __init__(self, total=0, **_):
        self.total, self.n = total, 0

    def update(self, n=1):
        self.n += n
        _Suivi.vu.append(self.n / self.total)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def test_whisper_faster_transcrit(voix):
    from huggingface_hub import snapshot_download
    from pathlib import Path
    from plateforme import whisper_faster as w
    dossier = Path(snapshot_download(MODELE))
    assert w.modele_complet(dossier)
    son, frequence = sf.read(voix, dtype="float32")
    assert frequence == 16000
    segments = w.transcrire(son, str(dossier), "fr", "Vocabulaire : MiroRecap, boulangerie Martin.", _Suivi, mots=True)
    texte = _sans_accents(" ".join(s["text"] for s in segments))
    print("Transcription :", texte)
    assert "boulangerie" in texte and "mardi" in texte
    assert segments[0]["words"] and {"word", "start", "end"} <= set(segments[0]["words"][0])
    assert {"start", "end", "text", "no_speech_prob", "avg_logprob"} <= set(segments[0])
    assert _Suivi.vu and abs(_Suivi.vu[-1] - 1.0) < 1e-6   # la barre de progression arrive au bout


def test_app_transcrit_un_fichier(voix, tmp_path):
    """Chemin complet de l'app : conversion (PyAV), découpage de la parole, Whisper, nettoyage."""
    import shutil
    import app
    copie = tmp_path / "appel.wav"
    shutil.copy(voix, copie)
    transcription = app.transcrire(copie, MODELE)
    print(transcription)
    assert transcription.startswith("[00:0")
    assert "boulangerie" in _sans_accents(transcription)
