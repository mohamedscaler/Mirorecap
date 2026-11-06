# -*- coding: utf-8 -*-
"""macOS : conversion audio (afconvert) et capture du son du Mac (outil natif ScreenCaptureKit)."""
import os
import subprocess
from pathlib import Path

DOSSIER_CODE = Path(__file__).resolve().parent.parent
CODE_APP = Path(os.environ.get("MIRORECAP_CODE_APP") or DOSSIER_CODE)
# Outil de capture : celui livré avec la dernière mise à jour du code s'il existe, sinon celui de l'app
_DANS_APP = CODE_APP.parent.parent / "MacOS" / "capture-systeme"
OUTIL_CAPTURE = next((c for c in (DOSSIER_CODE / "capture-systeme", _DANS_APP) if c.exists()), _DANS_APP)

TEXTE_MICRO_REFUSE = ("Aucun son reçu. Autorise MiroRecap dans Réglages Système → "
                      "Confidentialité et sécurité → Microphone, puis réessaie.")
TEXTE_CAPTURE_REFUSEE = ("Seul ton micro est enregistré",
                         "Pour capter la voix de tes interlocuteurs en visio : Réglages Système → "
                         "Confidentialité et sécurité → Enregistrement de l'écran et audio système → MiroRecap.")
TEXTE_TEST_REFUSE = ("Pas encore autorisé. Accepte la demande de macOS (ou active MiroRecap "
                     "dans Réglages Système → Confidentialité et sécurité → Enregistrement "
                     "de l'écran et audio système), quitte et rouvre MiroRecap, puis réessaie.")


def convertir_wav_16k(source, cible):
    """Convertit n'importe quel audio (m4a, mp3, wav, caf, mp4…) en WAV 16 kHz mono.
    afconvert est fourni avec macOS : pas besoin de ffmpeg. Retourne (ok, message d'erreur)."""
    r = subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", str(source), str(cible)],
                       capture_output=True, text=True)
    return r.returncode == 0, r.stderr.strip()


def capture_disponible():
    return OUTIL_CAPTURE.exists()


def lancer_capture(fichier):
    """Démarre la capture du son du Mac dans un WAV. Le processus s'arrête par terminate()
    (il finalise alors son fichier) ; un code de sortie non nul en moins de 4 s = refus."""
    return subprocess.Popen([str(OUTIL_CAPTURE), str(fichier)], stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)


def jouer_son_test():
    subprocess.run(["afplay", "/System/Library/Sounds/Glass.aiff"], check=False, timeout=5)


def niveau_capture(fichier):
    """Niveau (0 à 1) du son du Mac : pic des ~0,1 dernières secondes du WAV en cours d'écriture
    (16 bits mono). 0 si le fichier n'existe pas encore ou ne grandit pas."""
    import struct
    try:
        taille = os.path.getsize(fichier)
        with open(fichier, "rb") as f:
            debut = f.read(8192)
            pos = debut.find(b"data")
            if pos < 0:
                return 0.0
            origine = pos + 8   # début des échantillons
            lecture = 9600      # 0,1 s à 48 kHz en 16 bits
            depart = max(origine, taille - lecture)
            depart = origine + ((depart - origine) // 2) * 2   # reste aligné sur les échantillons
            f.seek(depart)
            donnees = f.read(taille - depart)
        n = len(donnees) // 2
        if n == 0:
            return 0.0
        valeurs = struct.unpack("<%dh" % n, donnees[:n * 2])
        return round(max(abs(v) for v in valeurs) / 32768.0, 3)
    except Exception:
        return 0.0
