# -*- coding: utf-8 -*-
"""Windows : conversion audio (PyAV, livré avec faster-whisper) et capture du son du PC
(WASAPI loopback avec PyAudioWPatch).

La capture tourne dans un fil du processus, mais se pilote comme l'outil natif du Mac
(poll, wait, terminate, kill, stderr) : app.py ne voit pas la différence."""
import io
import math
import os
import queue
import struct
import subprocess
import threading
import time
import wave

TEXTE_MICRO_REFUSE = ("Aucun son reçu. Ouvre Paramètres → Confidentialité et sécurité → Microphone, "
                      "active « Accès au microphone » et « Autoriser les applications de bureau à accéder "
                      "au microphone », puis réessaie.")
TEXTE_CAPTURE_REFUSEE = ("Seul ton micro est enregistré",
                         "Le son du PC n'a pas pu être capté. Vérifie qu'un haut-parleur ou un casque est "
                         "actif (Paramètres → Système → Son), puis relance l'enregistrement.")
TEXTE_TEST_REFUSE = ("Impossible de capter le son du PC. Vérifie qu'une sortie audio (haut-parleurs ou casque) "
                     "est active dans Paramètres → Système → Son, puis réessaie.")


# --- Conversion ----------------------------------------------------------------------

def convertir_wav_16k(source, cible):
    """Convertit n'importe quel audio (m4a, mp3, wav, mp4, webm…) en WAV 16 kHz mono.
    PyAV (ffmpeg intégré) est déjà installé avec faster-whisper. Retourne (ok, message d'erreur)."""
    try:
        import av
        with av.open(str(source)) as conteneur, wave.open(str(cible), "wb") as sortie:
            if not conteneur.streams.audio:
                return False, "aucune piste audio dans ce fichier"
            sortie.setnchannels(1)
            sortie.setsampwidth(2)
            sortie.setframerate(16000)
            reechantillonneur = av.AudioResampler(format="s16", layout="mono", rate=16000)
            for trame in conteneur.decode(audio=0):
                for morceau in reechantillonneur.resample(trame):
                    sortie.writeframes(morceau.to_ndarray().tobytes())
            for morceau in reechantillonneur.resample(None):   # vide le tampon
                sortie.writeframes(morceau.to_ndarray().tobytes())
        return True, ""
    except ImportError:
        return _convertir_sans_av(source, cible)
    except Exception as e:
        return False, str(e)


def _convertir_sans_av(source, cible):
    """Secours sans PyAV : formats lus par libsndfile (wav, flac, ogg, mp3)."""
    try:
        import numpy as np
        import soundfile as sf
        from scipy.signal import resample_poly
        son, frequence = sf.read(str(source), dtype="float32", always_2d=True)
        son = son.mean(axis=1)
        if frequence != 16000:
            pgcd = math.gcd(int(frequence), 16000)
            son = resample_poly(son, 16000 // pgcd, int(frequence) // pgcd).astype("float32")
        sf.write(str(cible), np.clip(son, -1, 1), 16000, subtype="PCM_16")
        return True, ""
    except Exception as e:
        return False, str(e)


# --- Capture du son du PC (WASAPI loopback) ------------------------------------------

def _peripherique_loopback(pa):
    """Entrée « loopback » de la sortie audio par défaut (ce qu'on entend dans le casque)."""
    import pyaudiowpatch as pyaudio
    wasapi = pa.get_host_api_info_by_type(pyaudio.paWASAPI)
    sortie = pa.get_device_info_by_index(wasapi["defaultOutputDevice"])
    if sortie.get("isLoopbackDevice"):
        return sortie
    for boucle in pa.get_loopback_device_info_generator():
        if sortie["name"] in boucle["name"]:
            return boucle
    raise RuntimeError(f"pas de capture possible pour la sortie « {sortie['name']} »")


def capture_disponible():
    try:
        import pyaudiowpatch  # noqa: F401
        return True
    except ImportError:
        return False


class CaptureSysteme:
    """Enregistre le son du PC dans un WAV 16 bits mono, à la fréquence de la carte son.
    WASAPI n'envoie rien quand aucun son ne joue : on comble avec du silence, pour que la
    piste reste calée sur celle du micro (elles sont mélangées à l'arrêt)."""
    TROU_MAX = 0.2   # au-delà de 0,2 s sans données, on écrit du silence

    def __init__(self, fichier):
        self.fichier = str(fichier)
        self.returncode = None
        self.pid = None
        self.stderr = io.StringIO()
        self._arret = threading.Event()
        self._pret = threading.Event()
        self._blocs = queue.Queue()
        self._fil = threading.Thread(target=self._tourner, daemon=True)
        self._fil.start()

    # --- même interface que subprocess.Popen ---
    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self._fil.join(timeout)
        if self._fil.is_alive():
            raise subprocess.TimeoutExpired("capture du son du PC", timeout)
        return self.returncode

    def terminate(self):
        self._arret.set()

    kill = terminate

    # --- capture ---
    def _echouer(self, message):
        self.stderr.write(message)
        self.stderr.seek(0)
        self.returncode = 1

    def _tourner(self):
        try:
            import numpy as np
            import pyaudiowpatch as pyaudio
        except ImportError as e:
            return self._echouer(f"capture indisponible : {e}")
        pa = flux = None
        try:
            pa = pyaudio.PyAudio()
            source = _peripherique_loopback(pa)
            frequence = int(source["defaultSampleRate"])
            canaux = max(1, int(source["maxInputChannels"]))

            def rappel(donnees, trames, infos, statut):
                self._blocs.put(donnees)
                return (None, pyaudio.paContinue)

            flux = pa.open(format=pyaudio.paInt16, channels=canaux, rate=frequence, input=True,
                           input_device_index=source["index"], frames_per_buffer=frequence // 20,
                           stream_callback=rappel)
            flux.start_stream()
        except Exception as e:
            if pa is not None:
                pa.terminate()
            return self._echouer(f"capture du son du PC impossible : {e}")

        ecrites, code = 0, 0
        depart = time.monotonic()
        try:
            with open(self.fichier, "wb") as brut, wave.open(brut, "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(frequence)
                wav.writeframes(b"")   # écrit l'en-tête tout de suite (lu pendant l'enregistrement)
                brut.flush()
                while not self._arret.is_set():
                    try:
                        donnees = self._blocs.get(timeout=0.05)
                        son = np.frombuffer(donnees, dtype="<i2").reshape(-1, canaux)
                        mono = son.mean(axis=1).astype("<i2") if canaux > 1 else son[:, 0]
                        wav.writeframes(mono.tobytes())
                        ecrites += len(mono)
                    except queue.Empty:
                        attendues = int((time.monotonic() - depart) * frequence)
                        if attendues - ecrites > self.TROU_MAX * frequence:
                            manque = attendues - ecrites
                            wav.writeframes(b"\0\0" * manque)
                            ecrites += manque
                    brut.flush()
        except Exception as e:
            self.stderr.write(f"écriture de la capture : {e}\n")
            code = 1
        finally:
            try:
                flux.stop_stream()
                flux.close()
            except Exception:
                pass
            pa.terminate()
        self.stderr.write(f"{ecrites / frequence:.1f} s capturées à {frequence} Hz")
        self.stderr.seek(0)
        self.returncode = code


def lancer_capture(fichier):
    """Démarre la capture du son du PC dans un WAV. Elle s'arrête par terminate() (le fichier est
    finalisé) ; un code de sortie non nul en moins de 4 s = capture impossible (message dans stderr)."""
    return CaptureSysteme(fichier)


def jouer_son_test():
    """Petit carillon sur la sortie par défaut (le thème de sons de Windows peut être muet)."""
    import numpy as np
    import sounddevice as sd
    frequence = 48000
    t = np.arange(int(frequence * 0.5)) / frequence
    note = (0.25 * np.sin(2 * np.pi * 880 * t) * np.exp(-4 * t)).astype("float32")
    sd.play(note, frequence)
    sd.wait()


def niveau_capture(fichier):
    """Niveau (0 à 1) du son du PC : volume des ~0,1 dernières secondes du WAV en cours d'écriture
    (16 bits mono). 0 si le fichier n'existe pas encore ou ne grandit pas."""
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
        rms = math.sqrt(sum(v * v for v in valeurs) / n) / 32768.0
        return round(min(1.0, max(0.0, (20 * math.log10(rms + 1e-9) + 60) / 60)), 3)
    except Exception:
        return 0.0
