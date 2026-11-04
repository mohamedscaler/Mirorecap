#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MiroRecap — app macOS (Mac Apple Silicon) : icône 🎙 dans la barre de menus + fenêtre.

Flux : audio (enregistré ici ou déposé dans _A-traiter)
       → transcription locale Whisper (MLX)
       → Claude rédige le compte rendu et choisit le projet
       → rangement dans <Comptes-rendus>/<Projet>/

Architecture :
- ce processus gère l'enregistrement, la file de traitement et l'icône de la barre de menus ;
- il sert aussi une petite API locale (127.0.0.1, protégée par jeton) ;
- la fenêtre (fenetre.py) est un processus séparé qui affiche ui/index.html et parle à cette API.

Les projets = les sous-dossiers du dossier Comptes-rendus.
Un fichier description.txt dans un dossier projet aide Claude à mieux classer.
"""

import datetime as dt
import http.server
import json
import os
import queue
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import unicodedata
import urllib.parse
import urllib.error
import urllib.request
from pathlib import Path

import rumps
import sounddevice as sd
import numpy as np
import soundfile as sf

# ---------------------------------------------------------------------------
# Configuration (stockée hors du dossier partageable : chacun a sa clé)
# ---------------------------------------------------------------------------

DOSSIER_CODE = Path(__file__).resolve().parent
# Code livré dans l'app (Contents/Resources/app). Différent de DOSSIER_CODE quand le code
# vient d'une mise à jour téléchargée (~/Library/Application Support/MiroRecap/code/actuel)
CODE_APP = Path(os.environ.get("MIRORECAP_CODE_APP") or DOSSIER_CODE)
DOSSIER_APP = Path.home() / "Library" / "Application Support" / "MiroRecap"

# Mises à jour : on compare app/VERSION local avec celui de la branche main sur GitHub
DEPOT = "mohamedscaler/Mirorecap"
URL_VERSION = f"https://api.github.com/repos/{DEPOT}/releases/latest"
URL_INSTALL = f"https://raw.githubusercontent.com/{DEPOT}/main/install-distant.sh"
_fichier_version = DOSSIER_CODE / "VERSION"
VERSION = _fichier_version.read_text().strip() if _fichier_version.exists() else "0"
URL_DMG = f"https://github.com/{DEPOT}/releases/latest/download/MiroRecap.dmg"
INTERVALLE_VERIF = 3600  # vérifie toutes les heures
FICHIER_CONFIG = DOSSIER_APP / "config.json"
ICLOUD = Path.home() / "Library" / "Mobile Documents" / "com~apple~CloudDocs"

MODELES_WHISPER = [
    {"id": "mlx-community/whisper-small-mlx", "nom": "Normal", "taille": "environ 500 Mo",
     "detail": "Très rapide et léger. Moins fiable sur les noms propres et les chiffres."},
    {"id": "mlx-community/whisper-large-v3-turbo", "nom": "Turbo", "taille": "environ 1,6 Go",
     "detail": "Précis et rapide. Recommandé pour la plupart des appels."},
    {"id": "mlx-community/whisper-large-v3-mlx", "nom": "Pro", "taille": "environ 3 Go",
     "detail": "Le plus précis (accents, bruit, jargon), environ deux fois plus lent."},
]
MODELE_RECOMMANDE = "mlx-community/whisper-large-v3-turbo"

CONFIG_DEFAUT = {
    "cle_api_anthropic": "",
    # Vide = iCloud Drive/Comptes-rendus si iCloud Drive est actif, sinon ~/Comptes-rendus
    "dossier_sortie": "",
    "modele_whisper": MODELE_RECOMMANDE,
    "modele_choisi": False,   # le modèle Whisper est choisi au premier lancement
    # Durée de conservation des audios rangés, en jours (0 = toujours). Les comptes rendus restent.
    "conservation_audio": 0,
    # ChatGPT (Codex CLI) et clé API OpenAI
    "chatgpt_connecte": False,
    "cle_api_openai": "",
    "modele_openai": "gpt-5",
    # Ton contexte : utilisé par l'IA pour comprendre les appels et rédiger les mails
    "contexte_perso": "",
    "signature_mail": "",
    "accueil_fait": False,   # assistant de bienvenue terminé
    # Quand un appel commence (micro utilisé par une autre app) : "proposer", "auto" ou "rien"
    "detection_appels": "proposer",
    # Noms propres, marques, jargon : aide Whisper à les reconnaître
    "vocabulaire": "",
    "modele_claude": "claude-sonnet-5-5",
    # Moteur d'analyse : "auto" (abonnement si Claude Code est installé, sinon clé API),
    # "abonnement" (Claude Code, compte Claude Pro/Max) ou "api" (clé API Anthropic)
    "moteur": "auto",
    "modele_abonnement": "sonnet",
    "langue": "fr",
    # Enregistre aussi le son qui sort du Mac (voix des interlocuteurs en visio)
    "son_systeme": True,
    # Installe les mises à jour toute seule dès qu'elles sortent (quand l'app ne fait rien)
    "maj_auto": True,
}


def charger_config():
    config = dict(CONFIG_DEFAUT)
    if FICHIER_CONFIG.exists():
        config.update(json.loads(FICHIER_CONFIG.read_text(encoding="utf-8")))
    return config


def sauver_config(config):
    DOSSIER_APP.mkdir(parents=True, exist_ok=True)
    FICHIER_CONFIG.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")
    FICHIER_CONFIG.chmod(0o600)  # clé API et jeton : lisibles par toi seul


CONFIG = charger_config()

if CONFIG["dossier_sortie"]:
    DOSSIER_SORTIE = Path(CONFIG["dossier_sortie"]).expanduser()
elif ICLOUD.exists():
    DOSSIER_SORTIE = ICLOUD / "Comptes-rendus"   # accessible depuis l'iPhone (app Fichiers)
else:
    DOSSIER_SORTIE = Path.home() / "Comptes-rendus"

DOSSIER_IMPORT = DOSSIER_SORTIE / "_A-traiter"
DOSSIER_ENREG = DOSSIER_SORTIE / "_En-cours"
DOSSIER_ERREURS = DOSSIER_SORTIE / "_Erreurs"
PROJET_PAR_DEFAUT = "Divers"
EXTENSIONS_AUDIO = {".m4a", ".mp3", ".wav", ".aac", ".caf", ".mp4", ".mov",
                    ".ogg", ".opus", ".flac", ".webm"}

for _d in (DOSSIER_IMPORT, DOSSIER_ENREG, DOSSIER_ERREURS, DOSSIER_SORTIE / PROJET_PAR_DEFAUT):
    _d.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Utilitaires
# ---------------------------------------------------------------------------

def notifier(titre, message):
    """Notification au nom de MiroRecap (nom et logo de l'app, un clic ouvre la fenêtre).
    Dans l'app installée, c'est le lanceur natif qui l'affiche ; jamais de script ni de Python."""
    if os.environ.get("MIRORECAP_LANCEUR") == "1":
        try:
            from Foundation import NSDistributedNotificationCenter
            NSDistributedNotificationCenter.defaultCenter().postNotificationName_object_userInfo_deliverImmediately_(
                "fr.mirorecap.notifier", None, {"titre": titre, "message": message}, True)
            return
        except Exception:
            pass
    try:
        rumps.notification("MiroRecap", titre, message, sound=False)
    except Exception:
        pass  # pas de notification plutôt qu'une notification au nom d'un autre programme


def slugifier(texte, longueur_max=60):
    """'Appel client : devis Shopify' -> 'appel-client-devis-shopify'"""
    texte = unicodedata.normalize("NFKD", texte).encode("ascii", "ignore").decode()
    texte = re.sub(r"[^a-zA-Z0-9]+", "-", texte).strip("-").lower()
    return texte[:longueur_max].rstrip("-") or "sans-titre"


def chemin_libre(chemin):
    """Évite d'écraser un fichier existant en ajoutant -2, -3…"""
    if not chemin.exists():
        return chemin
    i = 2
    while True:
        candidat = chemin.with_name(f"{chemin.stem}-{i}{chemin.suffix}")
        if not candidat.exists():
            return candidat
        i += 1


def nom_valide(nom):
    """Nom de projet utilisable comme dossier (pas de / ni de : ni de dossier technique)."""
    nom = re.sub(r"[/:\\]", "-", (nom or "").strip())
    if not nom or nom.startswith((".", "_")):
        raise ValueError("nom de projet invalide")
    return nom


def dossier_projet(nom):
    """Chemin d'un projet existant, en refusant toute sortie du dossier Comptes-rendus."""
    chemin = (DOSSIER_SORTIE / nom).resolve()
    if chemin.parent != DOSSIER_SORTIE.resolve() or not chemin.is_dir() or nom.startswith(("_", ".")):
        raise ValueError("projet introuvable")
    return chemin


def lister_projets():
    """{nom: description} pour chaque sous-dossier projet (hors _dossiers techniques et Divers)."""
    projets = {}
    for d in sorted(DOSSIER_SORTIE.iterdir(), key=lambda p: p.name.lower()):
        if d.is_dir() and not d.name.startswith(("_", ".")) and d.name != PROJET_PAR_DEFAUT:
            fichier_desc = d / "description.txt"
            projets[d.name] = fichier_desc.read_text(encoding="utf-8").strip() if fichier_desc.exists() else ""
    return projets


def compter_erreurs():
    return sum(1 for f in DOSSIER_ERREURS.iterdir() if f.suffix.lower() in EXTENSIONS_AUDIO)


def relancer_erreurs():
    n = 0
    for f in DOSSIER_ERREURS.iterdir():
        if f.suffix.lower() in EXTENSIONS_AUDIO:
            shutil.move(str(f), str(chemin_libre(DOSSIER_IMPORT / f.name)))
            n += 1
        elif f.suffix == ".txt":
            f.unlink()  # transcription partielle : elle sera refaite
    return n


def version_tuple(v):
    """'3.10' -> (3, 10) pour comparer correctement les versions."""
    return tuple(int(x) for x in re.findall(r"\d+", v)) or (0,)


DOSSIER_CODES = DOSSIER_APP / "code"   # mises à jour du code (actuel, precedent)


def moteur_local():
    """Numéro du moteur livré dans l'app (Python + modules). Une mise à jour du code
    seul n'est possible que si elle est prévue pour ce même moteur."""
    try:
        return (CODE_APP / "MOTEUR").read_text().strip() or "1"
    except OSError:
        return "1"


def derniere_release():
    """Dernière version publiée et comment l'installer, ou None (rien à proposer / pas de réseau).
    Retour : {"version", "type": "code" | "complete", "code": url, "sha256", "taille"}."""
    try:
        requete = urllib.request.Request(URL_VERSION, headers={"Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(requete, timeout=10) as r:
            release = json.loads(r.read().decode())
        version = release.get("tag_name", "").lstrip("v")
        assets = {a.get("name"): a.get("browser_download_url") for a in release.get("assets", [])}
        if not version:
            return None
        # Mise à jour légère : le code seul (quelques centaines de Ko), si le moteur est le même
        if "maj.json" in assets and "code.zip" in assets:
            with urllib.request.urlopen(assets["maj.json"], timeout=15) as r:
                infos = json.loads(r.read().decode())
            if str(infos.get("moteur")) == moteur_local() and infos.get("sha256"):
                return {"version": version, "type": "code", "code": assets["code.zip"],
                        "sha256": infos["sha256"], "taille": infos.get("taille", 0)}
        # Sinon, l'app complète, une fois son DMG construit
        if "MiroRecap.dmg" in assets:
            return {"version": version, "type": "complete"}
        return None
    except Exception:
        return None


def ouvrir_finder(chemin, reveler=False):
    subprocess.run(["open", "-R", str(chemin)] if reveler else ["open", str(chemin)], check=False)


# ---------------------------------------------------------------------------
# Enregistrement micro
# ---------------------------------------------------------------------------

# Outil de capture : celui livré avec la dernière mise à jour du code s'il existe, sinon celui de l'app
CAPTURE_SYSTEME = next((c for c in (DOSSIER_CODE / "capture-systeme", CODE_APP.parent.parent / "MacOS" / "capture-systeme")
                        if c.exists()), CODE_APP.parent.parent / "MacOS" / "capture-systeme")
DOSSIER_CAPTURE = DOSSIER_APP / "capture"   # pistes « son système » le temps du mixage

# État de la capture du son système, affiché dans la fenêtre :
# "" (pas encore utilisée), "ok", "refusee", "off" (désactivée), "indisponible"
SON_SYSTEME = {"etat": "", "message": ""}


class Enregistreur:
    """Enregistre le micro par défaut dans un .wav, et en parallèle le son du Mac
    (outil natif capture-systeme) dans une seconde piste, mélangée à l'arrêt.
    Le callback audio pousse les blocs dans une file, un thread séparé les écrit
    sur le disque : le callback ne bloque jamais (sinon trous dans l'audio)."""

    def __init__(self):
        self.actif = False
        self.debut = None
        self.chemin = None
        self._file = queue.Queue()
        self._flux = None
        self._ecrivain = None

    def demarrer(self):
        frequence = int(sd.query_devices(kind="input")["default_samplerate"])
        self.chemin = chemin_libre(DOSSIER_ENREG / f"{dt.datetime.now():%Y-%m-%d_%Hh%M}.wav")
        fichier = sf.SoundFile(self.chemin, mode="w", samplerate=frequence, channels=1, subtype="PCM_16")

        def ecrire():
            with fichier:
                # Continue tant qu'on enregistre OU qu'il reste des blocs à écrire
                while self.actif or not self._file.empty():
                    try:
                        fichier.write(self._file.get(timeout=0.5))
                    except queue.Empty:
                        pass

        def callback(indata, frames, time, status):
            self._file.put(indata.copy())

        self.actif = True
        self.debut = dt.datetime.now()
        self._ecrivain = threading.Thread(target=ecrire, daemon=True)
        self._ecrivain.start()
        self._flux = sd.InputStream(samplerate=frequence, channels=1, dtype="int16", callback=callback)
        self._flux.start()
        self._demarrer_son_systeme()

    def _demarrer_son_systeme(self):
        self.systeme, self._capture = None, None
        if not CONFIG.get("son_systeme", True):
            SON_SYSTEME.update(etat="off", message="")
            return
        if not CAPTURE_SYSTEME.exists():
            SON_SYSTEME.update(etat="indisponible", message="Outil de capture absent (lancement hors de l'app ?)")
            return
        DOSSIER_CAPTURE.mkdir(parents=True, exist_ok=True)
        self.systeme = DOSSIER_CAPTURE / (self.chemin.stem + ".wav")
        self._capture = subprocess.Popen(
            [str(CAPTURE_SYSTEME), str(self.systeme)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
        )
        capture = self._capture

        def surveiller():
            # Un refus arrive en moins d'une seconde ; au-delà, la capture tourne
            try:
                code = capture.wait(timeout=4)
            except subprocess.TimeoutExpired:
                SON_SYSTEME.update(etat="ok", message="")
                return
            erreur = (capture.stderr.read() or "").strip()
            print(f"capture-systeme ({code}) : {erreur}", flush=True)
            deja_refusee = SON_SYSTEME["etat"] == "refusee"
            SON_SYSTEME.update(etat="refusee", message=erreur)
            if not deja_refusee:
                notifier("Seul ton micro est enregistré",
                         "Pour capter la voix de tes interlocuteurs en visio : Réglages Système → "
                         "Confidentialité et sécurité → Enregistrement de l'écran et audio système → MiroRecap.")

        threading.Thread(target=surveiller, daemon=True).start()

    def arreter(self):
        self._flux.stop()
        self._flux.close()
        self.actif = False
        if self._capture and self._capture.poll() is None:
            self._capture.terminate()   # SIGTERM : l'outil finalise son fichier WAV
            try:
                self._capture.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._capture.kill()
        self._ecrivain.join()  # attend que tout soit bien écrit
        systeme = self.systeme if self.systeme and self.systeme.exists() else None
        return self.chemin, self.debut, systeme


def tester_micro():
    """Enregistre 1,5 s au micro (déclenche la demande d'autorisation la première fois)
    et dit si du son arrive."""
    try:
        if Api.app:
            Api.app.test_micro_en_cours = True
        frequence = int(sd.query_devices(kind="input")["default_samplerate"])
        audio = sd.rec(int(frequence * 1.5), samplerate=frequence, channels=1, dtype="float32")
        sd.wait()
        niveau = float(np.abs(audio).max()) if audio.size else 0.0
    except Exception as e:
        return {"ok": False, "message": f"Micro inaccessible : {e}"}
    finally:
        if Api.app:
            threading.Timer(4, lambda: setattr(Api.app, "test_micro_en_cours", False)).start()
    if niveau < 0.0005:
        return {"ok": False, "message": "Aucun son reçu. Autorise MiroRecap dans Réglages Système → "
                                        "Confidentialité et sécurité → Microphone, puis réessaie."}
    return {"ok": True, "message": "Micro OK."}


def tester_son_systeme():
    """Capture 3 s du son du Mac en jouant un son système : déclenche la demande d'autorisation
    « Enregistrement de l'écran et audio système » au nom de MiroRecap et vérifie le résultat."""
    import tempfile
    if not CAPTURE_SYSTEME.exists():
        return {"ok": False, "message": "Outil de capture absent."}
    with tempfile.TemporaryDirectory() as tmp:
        fichier = Path(tmp) / "test.wav"
        proc = subprocess.Popen([str(CAPTURE_SYSTEME), str(fichier)], stdout=subprocess.DEVNULL,
                                stderr=subprocess.PIPE, text=True)
        try:
            code = proc.wait(timeout=2)
            erreur = (proc.stderr.read() or "").strip()
            SON_SYSTEME.update(etat="refusee", message=erreur)
            return {"ok": False, "message": "Pas encore autorisé. Accepte la demande de macOS (ou active MiroRecap "
                                            "dans Réglages Système → Confidentialité et sécurité → Enregistrement "
                                            "de l'écran et audio système), quitte et rouvre MiroRecap, puis réessaie."}
        except subprocess.TimeoutExpired:
            pass
        for _ in range(2):
            subprocess.run(["afplay", "/System/Library/Sounds/Glass.aiff"], check=False, timeout=5)
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        bilan = (proc.stderr.read() or "").strip().splitlines()
        bilan = bilan[-1] if bilan else ""
        print(f"Test son du Mac : {bilan}", flush=True)
        try:
            audio, _ = sf.read(str(fichier), dtype="float32")
            niveau = float(np.abs(audio).max()) if len(audio) else 0.0
        except Exception:
            niveau = 0.0
    if niveau < 0.001:
        return {"ok": False, "message": "La capture tourne mais aucun son n'a été reçu. Vérifie que le volume "
                                        f"du Mac n'est pas coupé, puis réessaie. ({bilan or 'aucun bloc audio'})"}
    SON_SYSTEME.update(etat="ok", message="")
    return {"ok": True, "message": "Son du Mac OK : la voix de tes interlocuteurs sera enregistrée."}


def mixer_pistes(micro, systeme):
    """Mélange la piste micro et la piste son système en un seul WAV 16 kHz mono,
    qui remplace la piste micro. 16 kHz suffit pour la voix (c'est ce que lit Whisper)."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        pistes = []
        for i, source in enumerate((micro, systeme)):
            cible = Path(tmp) / f"{i}.wav"
            r = subprocess.run(
                ["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", str(source), str(cible)],
                capture_output=True, text=True,
            )
            if r.returncode != 0:
                raise RuntimeError(f"conversion impossible : {r.stderr.strip()}")
            pistes.append(cible)
        mix = Path(tmp) / "mix.wav"
        # Par blocs de 30 s : un appel d'une heure ne charge pas tout en mémoire
        bloc = 16000 * 30
        with sf.SoundFile(pistes[0]) as a, sf.SoundFile(pistes[1]) as b, \
                sf.SoundFile(mix, "w", samplerate=16000, channels=1, subtype="PCM_16") as sortie:
            while True:
                xa, xb = a.read(bloc, dtype="float32"), b.read(bloc, dtype="float32")
                if not len(xa) and not len(xb):
                    break
                somme = np.zeros(max(len(xa), len(xb)), dtype="float32")
                somme[:len(xa)] += xa
                somme[:len(xb)] += xb
                sortie.write(np.clip(somme, -1.0, 1.0))
        shutil.move(str(mix), str(micro))


# ---------------------------------------------------------------------------
# Détection des appels : le micro est utilisé par une autre app (Meet, Zoom, Teams…)
# ---------------------------------------------------------------------------

APPS_VISIO = {
    "us.zoom.xos": "Zoom", "com.microsoft.teams2": "Teams", "com.microsoft.teams": "Teams",
    "com.apple.FaceTime": "FaceTime", "com.hnc.Discord": "Discord", "com.tinyspeck.slackmacgap": "Slack",
    "net.whatsapp.WhatsApp": "WhatsApp", "desktop.WhatsApp": "WhatsApp", "com.cisco.webexmeetingsapp": "Webex",
    "com.skype.skype": "Skype", "ru.keepcoder.Telegram": "Telegram", "com.google.Chrome": "Chrome",
    "com.apple.Safari": "Safari", "company.thebrowser.Browser": "Arc", "com.brave.Browser": "Brave",
    "org.mozilla.firefox": "Firefox", "com.microsoft.edgemac": "Edge",
}
NAVIGATEURS = {"Chrome", "Safari", "Arc", "Brave", "Firefox", "Edge"}


def _fourcc(code):
    return int.from_bytes(code.encode(), "big")


def micro_utilise_ailleurs():
    """True si le micro par défaut est en cours d'utilisation (par n'importe quelle app).
    CoreAudio : kAudioDevicePropertyDeviceIsRunningSomewhere sur le micro par défaut."""
    import ctypes
    import ctypes.util

    class Adresse(ctypes.Structure):
        _fields_ = [("mSelector", ctypes.c_uint32), ("mScope", ctypes.c_uint32), ("mElement", ctypes.c_uint32)]

    global _COREAUDIO
    try:
        _COREAUDIO
    except NameError:
        _COREAUDIO = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreAudio"))
    lire = _COREAUDIO.AudioObjectGetPropertyData
    lire.argtypes = [ctypes.c_uint32, ctypes.POINTER(Adresse), ctypes.c_uint32, ctypes.c_void_p,
                     ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p]
    lire.restype = ctypes.c_int32

    glob = _fourcc("glob")
    appareil = ctypes.c_uint32(0)
    taille = ctypes.c_uint32(4)
    if lire(1, ctypes.byref(Adresse(_fourcc("dIn "), glob, 0)), 0, None, ctypes.byref(taille), ctypes.byref(appareil)) != 0:
        return False
    actif = ctypes.c_uint32(0)
    taille = ctypes.c_uint32(4)
    if lire(appareil.value, ctypes.byref(Adresse(_fourcc("gone"), glob, 0)), 0, None, ctypes.byref(taille), ctypes.byref(actif)) != 0:
        return False
    return bool(actif.value)


def app_de_visio():
    """Nom de l'app de visio ouverte au premier plan (ou la plus probable), sinon None."""
    try:
        from AppKit import NSWorkspace
        espace = NSWorkspace.sharedWorkspace()
        devant = espace.frontmostApplication()
        ouvertes = [str(a.bundleIdentifier() or "") for a in espace.runningApplications()]
        if devant and str(devant.bundleIdentifier() or "") in APPS_VISIO:
            nom = APPS_VISIO[str(devant.bundleIdentifier())]
            if nom not in NAVIGATEURS:
                return nom
        dediees = [APPS_VISIO[b] for b in ouvertes if b in APPS_VISIO and APPS_VISIO[b] not in NAVIGATEURS]
        return dediees[0] if dediees else None
    except Exception:
        return None


class DetecteurAppels:
    """Surveille le micro toutes les 3 s. Un appel est « détecté » quand une autre app utilise
    le micro depuis au moins 6 s (on ignore les usages très brefs : dictée, Siri…)."""

    def __init__(self, app):
        self.app = app
        self.depuis = None          # début de l'utilisation du micro par une autre app
        self.propose = False        # proposition déjà faite pour cet appel
        self.appel = None           # {"app": nom, "debut": iso} pendant un appel détecté
        threading.Thread(target=self.boucle, daemon=True).start()

    def boucle(self):
        time.sleep(15)
        while True:
            try:
                self.verifier()
            except Exception as e:
                print(f"Détection des appels : {e}", flush=True)
                time.sleep(60)
            time.sleep(3)

    def verifier(self):
        mode = CONFIG.get("detection_appels", "proposer")
        # Pendant notre propre enregistrement ou un test, le micro est utilisé par nous : on ne sait pas
        if self.app.enreg.actif:
            self.propose = True   # appel déjà enregistré : pas de nouvelle proposition après l'arrêt
            return
        if mode == "rien" or getattr(self.app, "test_micro_en_cours", False):
            self.depuis, self.appel = None, None
            return
        if not micro_utilise_ailleurs():
            self.depuis, self.propose, self.appel = None, False, None
            return
        if self.depuis is None:
            self.depuis = time.time()
            return
        if self.propose or time.time() - self.depuis < 6:
            return
        self.propose = True
        nom = app_de_visio()
        self.appel = {"app": nom or "", "debut": dt.datetime.now().isoformat(timespec="seconds")}
        if mode == "auto":
            self.app.basculer_enregistrement()
            notifier("Enregistrement lancé", f"Appel détecté{' sur ' + nom if nom else ''}. Pense à prévenir ton interlocuteur.")
        else:
            notifier(f"Appel détecté{' sur ' + nom if nom else ''}",
                     "Clique ici puis sur Enregistrer pour en faire un compte rendu.")


# ---------------------------------------------------------------------------
# Transcription locale (Whisper MLX)
# ---------------------------------------------------------------------------

# Téléchargement du modèle Whisper, suivi pour l'afficher (fenêtre + barre de menus)
MODELE = {"etat": "", "modele": "", "fait": 0, "total": 0, "message": ""}
VERROU_MODELE = threading.Lock()


def dossier_modele(repo):
    from huggingface_hub import constants
    return Path(constants.HF_HUB_CACHE) / ("models--" + repo.replace("/", "--"))


def modele_local(repo):
    """Chemin du modèle s'il est complet sur le Mac, sinon None."""
    from huggingface_hub import snapshot_download
    try:
        dossier = Path(snapshot_download(repo_id=repo, local_files_only=True))
    except Exception:
        return None
    complet = (dossier / "config.json").exists() and any(dossier.glob("weights.*"))
    return dossier if complet else None


def assurer_modele(repo=None):
    """Télécharge le modèle Whisper s'il manque, en suivant la progression.
    Retourne son dossier local. Un seul téléchargement à la fois."""
    repo = repo or CONFIG["modele_whisper"]
    with VERROU_MODELE:
        local = modele_local(repo)
        if local:
            MODELE.update(etat="pret", modele=repo, message="")
            return local
        from huggingface_hub import HfApi, snapshot_download
        total = 0
        try:
            infos = HfApi().model_info(repo, files_metadata=True)
            total = sum(f.size or 0 for f in infos.siblings)
        except Exception:
            pass  # taille inconnue : on affichera seulement les Mo téléchargés
        MODELE.update(etat="telechargement", modele=repo, fait=0, total=total, message="")
        blobs = dossier_modele(repo) / "blobs"
        fini = threading.Event()

        def suivre():
            # Taille des fichiers déjà écrits dans le cache (y compris ceux en cours)
            while not fini.wait(1):
                fait = 0
                for f in blobs.glob("*") if blobs.exists() else []:
                    try:
                        fait += f.stat().st_size
                    except OSError:
                        pass  # fichier renommé entre-temps
                MODELE["fait"] = min(fait, total) if total else fait

        threading.Thread(target=suivre, daemon=True).start()
        try:
            snapshot_download(repo_id=repo)
        except Exception as e:
            MODELE.update(etat="erreur", message=str(e))
            raise RuntimeError("téléchargement de Whisper impossible, vérifie ta connexion internet") from e
        finally:
            fini.set()
        MODELE.update(etat="pret", fait=total, message="")
        notifier("Transcription prête", "Le modèle Whisper est installé sur ton Mac.")
        return modele_local(repo) or repo


def preparer_modele_en_fond():
    def tache():
        try:
            assurer_modele()
        except Exception as e:
            print(f"Téléchargement de Whisper : {e}", flush=True)
    threading.Thread(target=tache, daemon=True).start()


def texte_progression():
    if MODELE["total"]:
        return f"{int(100 * MODELE['fait'] / MODELE['total'])} %"
    return f"{MODELE['fait'] // 1_000_000} Mo"


# Avancement du traitement en cours, affiché dans la fenêtre (ligne grisée + barre en haut)
TRAITEMENT = {"etape": "", "progression": None, "duree_audio": 0, "debut": "", "date": "", "amelioration": ""}


class _SuiviTranscription:
    """Remplace la barre de progression texte de Whisper : on récupère l'avancement réel."""
    def __init__(self, total=0, **_):
        self.total, self.n = (total or 1), 0
        TRAITEMENT["progression"] = 0.0

    def update(self, n=1):
        self.n += n
        TRAITEMENT["progression"] = min(1.0, self.n / self.total)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def close(self):
        pass


# Phrases que Whisper invente sur les silences (apprises des sous-titres de vidéos)
PHRASES_PARASITES = re.compile(
    r"sous-titr|amara\.org|merci d'avoir regard|abonnez-vous|n'oubliez pas de (vous abonner|liker)|"
    r"radio-canada|transcription par|thanks for watching|subtitles by|♪", re.I)


def consigne_whisper():
    """Texte d'amorce pour Whisper : vocabulaire de l'utilisateur + noms des projets.
    Whisper écrit alors ces mots correctement quand il les entend."""
    mots = [m.strip() for m in re.split(r"[,;\n]", CONFIG.get("vocabulaire", "")) if m.strip()]
    try:
        mots += [nom for nom in lister_projets()]
    except Exception:
        pass
    vus, uniques = set(), []
    for m in mots:
        if m.lower() not in vus:
            vus.add(m.lower())
            uniques.append(m)
    texte = ", ".join(uniques)[:600]   # Whisper n'en lit qu'environ 220 jetons
    return f"Appel professionnel en français. Vocabulaire : {texte}." if texte else None


def nettoyer_segments(segments):
    """Retire les phrases parasites et les répétitions en boucle typiques de Whisper."""
    propres, precedent, repetitions = [], None, 0
    for seg in segments:
        texte = seg["text"].strip()
        if not texte or PHRASES_PARASITES.search(texte):
            continue
        if seg.get("no_speech_prob", 0) > 0.8 and seg.get("avg_logprob", 0) < -1.0:
            continue   # probablement du silence ou du bruit
        if texte == precedent:
            repetitions += 1
            if repetitions >= 1:
                continue   # même phrase répétée : boucle de Whisper
        else:
            repetitions = 0
        precedent = texte
        propres.append(seg)
    return propres


def transcrire(chemin_audio, modele=None):
    """Retourne la transcription horodatée : '[mm:ss] texte' par ligne.
    Au premier usage d'un modèle, ses fichiers sont téléchargés (~/.cache/huggingface)."""
    import tempfile
    import mlx_whisper  # import tardif : lourd à charger

    # afconvert est fourni avec macOS : il lit m4a, mp3, wav, caf, mp4… sans ffmpeg
    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "audio.wav"
        r = subprocess.run(
            ["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", str(chemin_audio), str(wav)],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            raise RuntimeError(f"format audio illisible ({r.stderr.strip() or chemin_audio.suffix})")
        audio, _ = sf.read(str(wav), dtype="float32")
    TRAITEMENT["duree_audio"] = int(len(audio) / 16000)

    # Suivi de l'avancement : Whisper fait avancer notre compteur au lieu d'une barre texte
    import types
    module = sys.modules.get("mlx_whisper.transcribe")
    if module is not None and hasattr(module, "tqdm"):
        module.tqdm = types.SimpleNamespace(tqdm=_SuiviTranscription)
    resultat = mlx_whisper.transcribe(
        audio,
        path_or_hf_repo=str(assurer_modele(modele)),
        language=CONFIG["langue"],
        verbose=False,                      # active le suivi (sans rien afficher)
        initial_prompt=consigne_whisper(),  # vocabulaire et noms des projets
        condition_on_previous_text=False,   # évite les boucles et les phrases inventées en série
    )
    lignes = []
    for seg in nettoyer_segments(resultat.get("segments", [])):
        texte = seg["text"].strip()
        if texte:
            m, s = divmod(int(seg["start"]), 60)
            lignes.append(f"[{m:02d}:{s:02d}] {texte}")
    return "\n".join(lignes)


# ---------------------------------------------------------------------------
# Analyse par Claude : compte rendu + classement
# ---------------------------------------------------------------------------

def trouver_claude_code():
    """Chemin du programme Claude Code, ou None s'il n'est pas installé."""
    candidats = [
        shutil.which("claude"),
        Path.home() / ".local" / "bin" / "claude",
        Path.home() / ".claude" / "local" / "claude",
        Path("/opt/homebrew/bin/claude"),
        Path("/usr/local/bin/claude"),
    ]
    for c in candidats:
        if c and Path(c).exists():
            return str(c)
    return None


def env_claude_code():
    """Environnement pour Claude Code : le jeton de l'abonnement si MiroRecap l'a obtenu."""
    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)  # sinon Claude Code utiliserait une clé API à la place de l'abonnement
    if CONFIG.get("jeton_claude_code"):
        env["CLAUDE_CODE_OAUTH_TOKEN"] = CONFIG["jeton_claude_code"]
    return env


CONNEXION = {"etat": "", "message": "", "url": ""}   # suivi affiché dans les Réglages


FICHIER_CONNEXION = DOSSIER_APP / "connexion.log"   # sortie de setup-token, effacée aussitôt lue


def connecter_claude_code():
    """Installe Claude Code si besoin, puis ouvre une fenêtre Terminal avec
    « claude setup-token » : le navigateur s'ouvre, l'utilisateur se connecte, colle le
    code dans le Terminal (c'est l'outil officiel qui le gère), et MiroRecap récupère
    tout seul le jeton (valable un an) dans l'enregistrement de la session."""
    import shlex

    if CONNEXION["etat"] in ("installation", "terminal"):
        return  # déjà en cours

    def tache():
        try:
            claude = trouver_claude_code()
            if not claude:
                CONNEXION.update(etat="installation", message="Installation de Claude Code…", url="")
                subprocess.run(["/bin/bash", "-c", "curl -fsSL https://claude.ai/install.sh | bash"],
                               capture_output=True, text=True, timeout=900)
                claude = trouver_claude_code()
                if not claude:
                    raise RuntimeError("l'installation de Claude Code a échoué")

            FICHIER_CONNEXION.unlink(missing_ok=True)
            commande = DOSSIER_APP / "Connexion à Claude.command"
            # « script » enregistre la session tout en gardant un vrai terminal ;
            # fenêtre élargie à 220 colonnes pour que le jeton tienne sur une ligne
            commande.write_text(f"""#!/bin/bash
printf '\\e[8;30;220t'
clear
echo "MiroRecap · connexion à ton abonnement Claude"
echo
echo "  1. Connecte-toi dans le navigateur qui s'ouvre et autorise l'accès"
echo "  2. Copie le code affiché, colle-le ici puis appuie sur Entrée"
echo
unset ANTHROPIC_API_KEY
script -q -F {shlex.quote(str(FICHIER_CONNEXION))} {shlex.quote(claude)} setup-token
echo
echo "Terminé : tu peux fermer cette fenêtre et revenir dans MiroRecap."
""", encoding="utf-8")
            commande.chmod(0o700)
            subprocess.run(["open", "-a", "Terminal", str(commande)], check=False)
            CONNEXION.update(etat="terminal", url="",
                             message="Suis les 2 étapes dans la fenêtre Terminal qui vient de s'ouvrir.")

            fin = time.time() + 900
            while time.time() < fin and CONNEXION["etat"] == "terminal":
                time.sleep(1)
                if not FICHIER_CONNEXION.exists():
                    continue
                texte = FICHIER_CONNEXION.read_text(encoding="utf-8", errors="ignore")
                texte = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07", "", texte)
                jeton = re.search(r"sk-ant-oat[0-9A-Za-z_\-]{20,}", texte)
                if jeton:
                    time.sleep(1)   # laisse le jeton finir de s'écrire
                    texte = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "",
                                   FICHIER_CONNEXION.read_text(encoding="utf-8", errors="ignore"))
                    jeton = re.search(r"sk-ant-oat[0-9A-Za-z_\-]{20,}", texte)
                    CONFIG["jeton_claude_code"] = jeton.group(0)
                    sauver_config(CONFIG)
                    CONNEXION.update(etat="ok", message="Connecté à ton abonnement Claude.")
                    notifier("Abonnement Claude connecté", "MiroRecap utilise maintenant ton abonnement.")
                    return
            if CONNEXION["etat"] == "annule":
                CONNEXION.update(etat="", message="")
                return
            if CONNEXION["etat"] == "terminal":
                raise RuntimeError("connexion non terminée (délai dépassé)")
        except Exception as e:
            print(f"Connexion Claude Code : {e}", flush=True)
            CONNEXION.update(etat="echec", message=f"Échec : {e}")
        finally:
            FICHIER_CONNEXION.unlink(missing_ok=True)   # le jeton ne reste pas sur le disque

    threading.Thread(target=tache, daemon=True).start()


def envoyer_code_claude(code):
    """Jeton collé à la main (affiché par « claude setup-token » à la fin)."""
    code = (code or "").strip()
    if not re.fullmatch(r"sk-ant-oat[0-9A-Za-z_\-]{20,}", code):
        raise ValueError("colle le jeton complet affiché à la fin dans le Terminal (il commence par sk-ant-oat)")
    CONFIG["jeton_claude_code"] = code
    sauver_config(CONFIG)
    CONNEXION.update(etat="ok", message="Connecté à ton abonnement Claude.", url="")


def annuler_connexion_claude():
    CONNEXION.update(etat="annule" if CONNEXION["etat"] == "terminal" else "", message="", url="")


def deconnecter_claude_code():
    CONFIG.pop("jeton_claude_code", None)
    sauver_config(CONFIG)
    CONNEXION.update(etat="", message="")


# --- Moteurs d'analyse ------------------------------------------------------------
# "abonnement" : Claude Code avec le compte Claude (Pro/Max)
# "chatgpt"    : Codex CLI avec le compte ChatGPT (Plus/Pro/Business…)
# "api"        : clé API Anthropic          "openai" : clé API OpenAI

MOTEURS = ("abonnement", "chatgpt", "api", "openai")


def moteurs_prets():
    """Moteurs utilisables maintenant, dans l'ordre de préférence du mode Automatique."""
    prets = []
    if trouver_claude_code() and CONFIG.get("jeton_claude_code"):
        prets.append("abonnement")
    if trouver_codex() and CONFIG.get("chatgpt_connecte"):
        prets.append("chatgpt")
    if CONFIG.get("cle_api_anthropic"):
        prets.append("api")
    if CONFIG.get("cle_api_openai"):
        prets.append("openai")
    # Claude Code installé et connecté autrement (« claude » puis /login dans le Terminal)
    if not prets and trouver_claude_code():
        prets.append("abonnement")
    return prets


def moteur_effectif():
    """Moteur réellement utilisé, ou None si aucun n'est prêt."""
    choix = CONFIG.get("moteur", "auto")
    prets = moteurs_prets()
    if choix in MOTEURS:
        if choix == "abonnement":
            return "abonnement" if trouver_claude_code() else None
        return choix if choix in prets else None
    return prets[0] if prets else None


def moteurs_a_essayer():
    """Le moteur choisi, puis (en mode Automatique) les autres prêts en secours."""
    principal = moteur_effectif()
    if not principal:
        return []
    if CONFIG.get("moteur", "auto") != "auto":
        return [principal]
    return [principal] + [m for m in moteurs_prets() if m != principal]


NOMS_MOTEURS = {"abonnement": "Claude", "chatgpt": "ChatGPT", "api": "Claude (clé API)", "openai": "OpenAI (clé API)"}


# --- Contexte donné à l'IA -----------------------------------------------------------

def bloc_profil():
    """Qui est l'utilisateur : rempli dans Réglages → Ton contexte."""
    lignes = []
    if CONFIG.get("contexte_perso", "").strip():
        lignes.append(CONFIG["contexte_perso"].strip())
    if CONFIG.get("signature_mail", "").strip():
        lignes.append("Signature à utiliser en bas des mails :\n" + CONFIG["signature_mail"].strip())
    return "\n\n".join(lignes)


def historique_projets(limite=6000):
    """Ce qu'on sait déjà de chaque projet (synthèse « Où on en est », sinon derniers appels),
    pour classer juste et pour que le mail tienne compte de ce qui s'est déjà dit."""
    morceaux = []
    for nom in lister_projets():
        dossier = DOSSIER_SORTIE / nom
        synthese = dossier / "_synthese.md"
        if synthese.exists():
            texte = re.sub(r"<!--.*?-->", "", synthese.read_text(encoding="utf-8"), flags=re.S).strip()
            morceaux.append(f"### {nom} (point d'étape)\n{texte[:900]}")
            continue
        derniers = sorted((f for f in dossier.glob("*.md") if not f.name.startswith("_")), reverse=True)[:3]
        if derniers:
            titres = []
            for f in derniers:
                premiere = f.read_text(encoding="utf-8").split("\n", 1)[0]
                titres.append(f"- {f.name[:10]} : {premiere.lstrip('# ').strip()}")
            morceaux.append(f"### {nom} (derniers appels)\n" + "\n".join(titres))
    texte = "\n\n".join(morceaux)
    return texte[:limite]


SCHEMA_ANALYSE_CHAMPS = {
    "projet": "nom exact d'un des projets listés",
    "titre": "titre court, 3 à 8 mots",
    "compte_rendu": "compte rendu en markdown",
    "contexte_claude": "bloc <appel>…</appel>",
    "mail_destinataire": "à qui envoyer le mail (nom, rôle), vide si aucun mail",
    "mail_objet": "objet du mail, vide si aucun mail",
    "mail_corps": "texte du mail prêt à envoyer avec la signature, vide si aucun mail",
    "nouveau_projet": "nom court du client ou du sujet si aucun projet existant ne correspond (sinon vide)",
    "nouveau_projet_description": "une phrase décrivant ce nouveau projet (sinon vide)",
    "statut": "où en est la relation client après cet appel",
    "interlocuteurs": "une ligne par personne côté client : Nom · rôle · coordonnées (sinon vide)",
    "budget": "budget ou montant discuté (sinon vide)",
    "besoin": "le besoin du client en une phrase (sinon vide)",
}
STATUTS = ["prospect", "devis", "en_cours", "termine", "perdu"]


def instructions_analyse(noms, projets):
    liste = "\n".join(f"- {nom}" + (f" : {desc}" if desc else "") for nom, desc in projets.items())
    profil = bloc_profil()
    historique = historique_projets()
    return f"""Tu analyses des transcriptions automatiques d'appels et de rendez-vous pro (elles peuvent contenir des erreurs de reconnaissance).

{"Qui je suis (c'est moi qui enregistre, utilise-le pour comprendre l'appel et rédiger le mail) :" + chr(10) + profil if profil else "Je n'ai pas encore décrit mon activité."}

Projets existants :
{liste or "(aucun projet créé pour l'instant)"}
- {PROJET_PAR_DEFAUT} : rien ne correspond clairement

{"Ce qu'on sait déjà des projets :" + chr(10) + historique if historique else ""}

Choisis le projet dont parle l'enregistrement, en t'appuyant sur les noms de clients, d'entreprises, de produits, les sujets évoqués et l'historique ci-dessus. En cas de doute, prends "{PROJET_PAR_DEFAUT}".

Rédige le compte rendu en markdown, en français, sans titre principal, avec ces sections (omets celles qui seraient vides) :
## Contexte
## Points clés
## Décisions
## Actions à faire
(qui / quoi / deadline)
## Chiffres
(budget, prix, délais)
## Points flous à clarifier

N'invente rien : si une info est incertaine ou mal transcrite, signale-le.

Nouveau client : si l'appel concerne clairement un client, une entreprise ou un sujet récurrent qui n'a pas encore
de projet, mets le projet "{PROJET_PAR_DEFAUT}" et propose son nom dans « nouveau_projet » (nom de l'entreprise ou du
client, court, sans « projet » devant) avec une phrase de description. Sinon laisse ces deux champs vides.

Fiche client (pour le projet choisi ou proposé) :
- « statut » : prospect (premier contact, découverte), devis (un devis ou une proposition est attendu ou envoyé),
  en_cours (accord donné, travail lancé), termine (livré, clos), perdu (refus, abandon) ; vide si l'appel ne le dit pas ;
- « interlocuteurs » : les personnes côté client (nom, rôle, téléphone ou mail s'ils sont dits) ;
- « budget » et « besoin » : seulement s'ils sont évoqués.

Mail à préparer : si l'appel appelle un mail de ma part (envoyer un devis, un récapitulatif, des documents promis, confirmer un rendez-vous, relancer, remercier après une première prise de contact…), rédige-le, prêt à envoyer :
- adressé à la bonne personne, avec ce qui a été dit et convenu (dates, montants, prochaines étapes), en tenant compte de l'historique du projet ;
- dans mon ton (tutoiement ou vouvoiement selon notre relation dans l'appel), clair et court ;
- terminé par ma signature si je l'ai donnée ;
- entre crochets ce qui manque et que je dois compléter, par exemple [montant du devis].
Si aucun mail n'est utile, laisse les champs du mail vides.

Rédige aussi un bloc « contexte_claude » : la mémoire de cet appel destinée à être collée plus tard dans une conversation avec Claude. Il doit être dense, factuel, sans phrases de politesse ni mise en forme markdown, et tenir en 250 mots maximum. Utilise exactement ces balises, et omets celles qui seraient vides :
<appel date="JJ/MM/AAAA HH:MM" projet="nom du projet">
<interlocuteurs>noms, rôles, entreprises</interlocuteurs>
<objet>le sujet de l'appel en une phrase</objet>
<faits>- faits et besoins exprimés</faits>
<decisions>- décisions prises</decisions>
<actions>- qui : quoi (échéance)</actions>
<chiffres>- budgets, prix, quantités, délais</chiffres>
<questions_ouvertes>- ce qui reste à trancher ou à vérifier</questions_ouvertes>
<citations>- phrases exactes importantes, entre guillemets</citations>
</appel>"""


def schema_json(noms):
    """Schéma strict de la réponse (champs tous présents, texte vide si sans objet)."""
    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(SCHEMA_ANALYSE_CHAMPS),
        "properties": {
            cle: ({"type": "string", "enum": noms, "description": desc} if cle == "projet"
                  else {"type": "string", "enum": [""] + STATUTS, "description": desc} if cle == "statut"
                  else {"type": "string", "description": desc})
            for cle, desc in SCHEMA_ANALYSE_CHAMPS.items()
        },
    }


def consigne_json(systeme, contenu):
    exemple = "{" + ", ".join(f'"{c}": "<{d}>"' for c, d in SCHEMA_ANALYSE_CHAMPS.items()) + "}"
    return (systeme + "\n\nRéponds UNIQUEMENT par un objet JSON valide, sans texte avant ni après, de la forme :\n"
            + exemple + "\n\n" + contenu)


def extraire_json(texte, source):
    debut, fin = texte.find("{"), texte.rfind("}")
    if debut == -1 or fin <= debut:
        raise RuntimeError(f"{source} n'a pas renvoyé de JSON exploitable")
    return json.loads(texte[debut:fin + 1])


def analyser(transcription, date):
    """Rédige le compte rendu (et le mail éventuel) et choisit le projet."""
    moteurs = moteurs_a_essayer()
    if not moteurs:
        raise RuntimeError("aucun moteur d'analyse prêt : connecte Claude ou ChatGPT, ou ajoute une clé API (Réglages)")

    projets = lister_projets()
    noms = list(projets.keys()) + [PROJET_PAR_DEFAUT]
    systeme = instructions_analyse(noms, projets)
    contenu = f"Date de l'enregistrement : {date:%d/%m/%Y %H:%M}\n\nTranscription :\n{transcription}"

    fonctions = {"abonnement": analyser_abonnement, "chatgpt": analyser_chatgpt,
                 "api": analyser_api, "openai": analyser_openai}
    erreurs = []
    for moteur in moteurs:
        try:
            donnees = fonctions[moteur](systeme, contenu, noms)
            break
        except Exception as e:
            # Mode Automatique : un moteur indisponible (pas connecté, limite atteinte…) → le suivant
            print(f"Analyse via {NOMS_MOTEURS[moteur]} impossible : {e}", flush=True)
            erreurs.append(f"{NOMS_MOTEURS[moteur]} : {e}")
    else:
        raise RuntimeError(" · ".join(erreurs))

    if donnees.get("projet") not in noms:
        donnees["projet"] = PROJET_PAR_DEFAUT
    for cle in ("titre", "compte_rendu"):
        if not str(donnees.get(cle, "")).strip():
            raise RuntimeError(f"réponse incomplète ({cle} manquant)")
    donnees["moteur"] = moteur
    return donnees


def executer_claude_code(consigne, sortie_json=True):
    """Lance Claude Code en mode non interactif ; retourne le texte de la réponse."""
    import tempfile
    claude = trouver_claude_code()
    # Dossier vide : Claude Code n'a aucun fichier à lire, il se contente de répondre
    with tempfile.TemporaryDirectory() as dossier_vide:
        r = subprocess.run(
            [claude, "-p", "Suis les instructions reçues en entrée." + (" Réponds uniquement en JSON." if sortie_json else ""),
             "--output-format", "json", "--model", CONFIG.get("modele_abonnement", "sonnet"), "--max-turns", "1"],
            input=consigne, capture_output=True, text=True, timeout=900, cwd=dossier_vide,
            env=env_claude_code(),
        )
    sortie = r.stdout.strip()
    if r.returncode != 0 and not sortie:
        detail = (r.stderr.strip().splitlines() or ["erreur inconnue"])[-1]
        raise RuntimeError(f"{detail} (es-tu bien connecté ? Réglages → Se connecter)")
    try:
        enveloppe = json.loads(sortie)
    except json.JSONDecodeError:
        return sortie
    if isinstance(enveloppe, dict):
        if enveloppe.get("is_error"):
            resultat = enveloppe.get("result") or "erreur"
            if "login" in resultat.lower():
                resultat = "pas connecté à ton compte Claude (Réglages → Se connecter)"
            raise RuntimeError(resultat)
        return enveloppe.get("result", "") or ""
    return sortie


def analyser_abonnement(systeme, contenu, noms):
    """Analyse via Claude Code, avec le compte Claude (Pro/Max) de l'utilisateur."""
    return extraire_json(executer_claude_code(consigne_json(systeme, contenu)), "Claude")


def executer_codex(consigne, schema=None):
    """Lance Codex (ChatGPT) en mode non interactif, sans accès aux fichiers ; retourne sa réponse."""
    import tempfile
    codex = trouver_codex()
    if not codex:
        raise RuntimeError("Codex n'est pas installé (Réglages → Se connecter à ChatGPT)")
    with tempfile.TemporaryDirectory() as dossier_vide:
        sortie = Path(dossier_vide) / "reponse.txt"
        commande = [codex, "exec", "--skip-git-repo-check", "--ephemeral", "-s", "read-only",
                    "--color", "never", "-o", str(sortie)]
        if schema:
            fichier_schema = Path(dossier_vide) / "schema.json"
            fichier_schema.write_text(json.dumps(schema), encoding="utf-8")
            commande += ["--output-schema", str(fichier_schema)]
        r = subprocess.run(commande + ["-"], input=consigne, capture_output=True, text=True,
                           timeout=900, cwd=dossier_vide, env=env_codex())
        texte = sortie.read_text(encoding="utf-8").strip() if sortie.exists() else ""
    if not texte:
        detail = (r.stderr.strip().splitlines() or r.stdout.strip().splitlines() or ["erreur inconnue"])[-1]
        if "login" in detail.lower() or "auth" in detail.lower():
            CONFIG["chatgpt_connecte"] = False
            sauver_config(CONFIG)
            detail = "pas connecté à ChatGPT (Réglages → Se connecter à ChatGPT)"
        raise RuntimeError(detail)
    return texte


def analyser_chatgpt(systeme, contenu, noms):
    """Analyse via Codex CLI, avec le compte ChatGPT de l'utilisateur."""
    if not chatgpt_connecte_verifie():   # sinon Codex réessaierait le réseau en boucle
        CONFIG["chatgpt_connecte"] = False
        sauver_config(CONFIG)
        raise RuntimeError("pas connecté à ChatGPT (Réglages → Se connecter à ChatGPT)")
    return extraire_json(executer_codex(consigne_json(systeme, contenu), schema_json(noms)), "ChatGPT")


def analyser_api(systeme, contenu, noms):
    """Analyse via l'API Anthropic (clé API, facturée à l'usage)."""
    import anthropic

    # Outil forcé = Claude renvoie forcément un JSON propre
    schema = schema_json(noms)
    schema["required"] = ["projet", "titre", "compte_rendu", "contexte_claude"]
    outil = {"name": "classer_compte_rendu", "description": "Enregistre le compte rendu dans le bon projet.",
             "input_schema": schema}
    client = anthropic.Anthropic(api_key=CONFIG["cle_api_anthropic"])
    reponse = client.messages.create(
        model=CONFIG["modele_claude"],
        max_tokens=6000,
        system=systeme,
        tools=[outil],
        tool_choice={"type": "tool", "name": "classer_compte_rendu"},
        messages=[{"role": "user", "content": contenu}],
    )
    return next(b for b in reponse.content if b.type == "tool_use").input


def appeler_openai(messages, schema=None):
    """API OpenAI (clé API, facturée à l'usage), sans dépendance supplémentaire."""
    corps = {"model": CONFIG.get("modele_openai", "gpt-5"), "messages": messages}
    if schema:
        corps["response_format"] = {"type": "json_schema",
                                    "json_schema": {"name": "compte_rendu", "strict": True, "schema": schema}}
    requete = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions", data=json.dumps(corps).encode(),
        headers={"Authorization": f"Bearer {CONFIG['cle_api_openai']}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(requete, timeout=900) as r:
            reponse = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode())["error"]["message"]
        except Exception:
            detail = str(e)
        raise RuntimeError(f"OpenAI : {detail}")
    return reponse["choices"][0]["message"]["content"] or ""


def analyser_openai(systeme, contenu, noms):
    texte = appeler_openai([{"role": "system", "content": systeme}, {"role": "user", "content": contenu}],
                           schema_json(noms))
    return extraire_json(texte, "OpenAI")


def appeler_ia_texte(systeme, contenu):
    """Réponse texte libre (synthèse « Où on en est ») avec le moteur disponible."""
    erreurs = []
    for moteur in moteurs_a_essayer():
        try:
            if moteur == "abonnement":
                return executer_claude_code(systeme + "\n\n" + contenu, sortie_json=False)
            if moteur == "chatgpt":
                return executer_codex(systeme + "\n\n" + contenu)
            if moteur == "api":
                import anthropic
                client = anthropic.Anthropic(api_key=CONFIG["cle_api_anthropic"])
                reponse = client.messages.create(model=CONFIG["modele_claude"], max_tokens=4096, system=systeme,
                                                 messages=[{"role": "user", "content": contenu}])
                return "".join(b.text for b in reponse.content if b.type == "text")
            if moteur == "openai":
                return appeler_openai([{"role": "system", "content": systeme}, {"role": "user", "content": contenu}])
        except Exception as e:
            erreurs.append(f"{NOMS_MOTEURS[moteur]} : {e}")
    raise RuntimeError(" · ".join(erreurs) or "aucun moteur d'analyse prêt (Réglages → Analyse)")


# --- Connexion à ChatGPT (Codex CLI) -------------------------------------------------

DOSSIER_BIN = Path.home() / ".local" / "bin"
URL_CODEX = "https://github.com/openai/codex/releases/latest/download/codex-aarch64-apple-darwin.tar.gz"
CONNEXION_GPT = {"etat": "", "message": "", "url": ""}


def trouver_codex():
    for c in (shutil.which("codex"), DOSSIER_BIN / "codex", Path("/opt/homebrew/bin/codex"), Path("/usr/local/bin/codex")):
        if c and Path(c).exists():
            return str(c)
    return None


def env_codex():
    env = dict(os.environ)
    env.pop("OPENAI_API_KEY", None)   # sinon Codex utiliserait une clé à la place du compte ChatGPT
    return env


def installer_codex():
    """Télécharge Codex CLI (programme officiel d'OpenAI) dans ~/.local/bin."""
    import tarfile
    import tempfile
    DOSSIER_BIN.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / "codex.tar.gz"
        with urllib.request.urlopen(URL_CODEX, timeout=120) as r, open(archive, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            fait = 0
            while True:
                bloc = r.read(1 << 20)
                if not bloc:
                    break
                f.write(bloc)
                fait += len(bloc)
                if total:
                    CONNEXION_GPT["message"] = f"Installation de ChatGPT pour MiroRecap… {int(100 * fait / total)} %"
        with tarfile.open(archive) as t:
            membre = next(m for m in t.getmembers() if m.isfile() and m.name.split("/")[-1].startswith("codex"))
            membre.name = "codex"
            t.extract(membre, tmp)
        cible = DOSSIER_BIN / "codex"
        shutil.move(str(Path(tmp) / "codex"), str(cible))
        cible.chmod(0o755)
    return str(cible)


def chatgpt_connecte_verifie():
    codex = trouver_codex()
    if not codex:
        return False
    try:
        r = subprocess.run([codex, "login", "status"], capture_output=True, text=True, timeout=20, env=env_codex())
        texte = (r.stdout + r.stderr).lower()
        return r.returncode == 0 and "logged in" in texte and "not logged in" not in texte
    except Exception:
        return False


def connecter_chatgpt():
    """Installe Codex si besoin puis lance « codex login » : le navigateur s'ouvre, l'utilisateur
    se connecte à son compte ChatGPT, et la connexion se termine toute seule."""
    if CONNEXION_GPT["etat"] in ("installation", "navigateur"):
        return

    def tache():
        proc = None
        try:
            codex = trouver_codex()
            if not codex:
                CONNEXION_GPT.update(etat="installation", message="Installation de ChatGPT pour MiroRecap…", url="")
                codex = installer_codex()
            if chatgpt_connecte_verifie():
                CONFIG["chatgpt_connecte"] = True
                sauver_config(CONFIG)
                CONNEXION_GPT.update(etat="ok", message="Connecté à ton compte ChatGPT.", url="")
                return
            CONNEXION_GPT.update(etat="navigateur", url="",
                                 message="Connecte-toi à ton compte ChatGPT dans le navigateur qui vient de s'ouvrir.")
            proc = subprocess.Popen([codex, "login"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL, text=True, env=env_codex())

            def lire_sortie():
                for ligne in proc.stdout:
                    lien = re.search(r"https://\S*auth\S*", ligne)
                    if lien and not CONNEXION_GPT["url"]:
                        CONNEXION_GPT["url"] = lien.group(0)
            threading.Thread(target=lire_sortie, daemon=True).start()

            fin = time.time() + 900
            while time.time() < fin and CONNEXION_GPT["etat"] == "navigateur":
                if proc.poll() is not None:
                    break
                time.sleep(1)
            if CONNEXION_GPT["etat"] == "annule":
                CONNEXION_GPT.update(etat="", message="", url="")
                return
            if chatgpt_connecte_verifie():
                CONFIG["chatgpt_connecte"] = True
                sauver_config(CONFIG)
                CONNEXION_GPT.update(etat="ok", message="Connecté à ton compte ChatGPT.", url="")
                notifier("ChatGPT connecté", "MiroRecap peut maintenant analyser avec ton compte ChatGPT.")
            else:
                raise RuntimeError("connexion non terminée")
        except Exception as e:
            print(f"Connexion ChatGPT : {e}", flush=True)
            CONNEXION_GPT.update(etat="echec", message=f"Échec : {e}")
        finally:
            if proc and proc.poll() is None:
                proc.terminate()

    threading.Thread(target=tache, daemon=True).start()


def deconnecter_chatgpt():
    codex = trouver_codex()
    if codex:
        subprocess.run([codex, "logout"], capture_output=True, timeout=20, env=env_codex())
    CONFIG["chatgpt_connecte"] = False
    sauver_config(CONFIG)
    CONNEXION_GPT.update(etat="", message="", url="")


# --- Connecteur pour Claude (MCP) : Claude lit tes comptes rendus ------------------

LANCEUR_MCP = DOSSIER_APP / "mcp.py"
CONFIG_CLAUDE_DESKTOP = Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"


def python_moteur():
    """Python de l'app (dans le bundle) : celui qui lancera le connecteur."""
    candidat = CODE_APP.parent / "python" / "bin" / "python3"
    return str(candidat if candidat.exists() else Path(sys.executable))


def ecrire_lanceur_mcp():
    """Petit script stable lancé par Claude : il prend le connecteur du code le plus récent
    (mise à jour légère ou app), donc il reste valable après chaque mise à jour."""
    LANCEUR_MCP.parent.mkdir(parents=True, exist_ok=True)
    LANCEUR_MCP.write_text(f"""# Connecteur MiroRecap pour Claude (généré par MiroRecap, ne pas modifier)
import runpy
from pathlib import Path

def version(d):
    try:
        return tuple(int(x) for x in (d / "VERSION").read_text().strip().split("."))
    except Exception:
        return (0,)

candidats = [Path.home() / "Library" / "Application Support" / "MiroRecap" / "code" / "actuel", Path({str(CODE_APP)!r})]
valides = [d for d in candidats if (d / "mcp_serveur.py").exists() and not (d / ".defectueux").exists()]
runpy.run_path(str(max(valides, key=version) / "mcp_serveur.py"), run_name="__main__")
""", encoding="utf-8")


def mcp_installe():
    try:
        config = json.loads(CONFIG_CLAUDE_DESKTOP.read_text(encoding="utf-8"))
        return "mirorecap" in (config.get("mcpServers") or {})
    except Exception:
        return False


def connecter_mcp():
    """Ajoute MiroRecap aux connecteurs de l'app Claude (Mac) et de Claude Code."""
    ecrire_lanceur_mcp()
    entree = {"command": python_moteur(), "args": [str(LANCEUR_MCP)]}
    # App Claude : fichier de configuration des connecteurs locaux (copie de sécurité d'abord)
    config = {}
    if CONFIG_CLAUDE_DESKTOP.exists():
        texte = CONFIG_CLAUDE_DESKTOP.read_text(encoding="utf-8")
        try:
            config = json.loads(texte) if texte.strip() else {}
        except json.JSONDecodeError:
            raise RuntimeError("le fichier de configuration de Claude est illisible, MiroRecap n'y touche pas")
        sauvegarde = CONFIG_CLAUDE_DESKTOP.with_name("claude_desktop_config.avant-mirorecap.json")
        if not sauvegarde.exists():
            sauvegarde.write_text(texte, encoding="utf-8")
    CONFIG_CLAUDE_DESKTOP.parent.mkdir(parents=True, exist_ok=True)
    config.setdefault("mcpServers", {})["mirorecap"] = entree
    CONFIG_CLAUDE_DESKTOP.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")
    # Claude Code (s'il est installé), pour tous les dossiers
    claude = trouver_claude_code()
    if claude:
        subprocess.run([claude, "mcp", "remove", "--scope", "user", "mirorecap"], capture_output=True, timeout=30)
        subprocess.run([claude, "mcp", "add", "--scope", "user", "mirorecap", "--", entree["command"], *entree["args"]],
                       capture_output=True, timeout=30)
    return {"ok": True}


def deconnecter_mcp():
    if CONFIG_CLAUDE_DESKTOP.exists():
        try:
            config = json.loads(CONFIG_CLAUDE_DESKTOP.read_text(encoding="utf-8"))
            (config.get("mcpServers") or {}).pop("mirorecap", None)
            CONFIG_CLAUDE_DESKTOP.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass
    claude = trouver_claude_code()
    if claude:
        subprocess.run([claude, "mcp", "remove", "--scope", "user", "mirorecap"], capture_output=True, timeout=30)
    return {"ok": True}


def annuler_connexion_chatgpt():
    CONNEXION_GPT.update(etat="annule" if CONNEXION_GPT["etat"] == "navigateur" else "", message="", url="")


# ---------------------------------------------------------------------------
# Rangement
# ---------------------------------------------------------------------------

def section_mail(analyse):
    corps = str(analyse.get("mail_corps") or "").strip()
    if not corps:
        return ""
    entete = ""
    if str(analyse.get("mail_destinataire") or "").strip():
        entete += f"**À** : {analyse['mail_destinataire'].strip()}\n"
    if str(analyse.get("mail_objet") or "").strip():
        entete += f"**Objet** : {analyse['mail_objet'].strip()}\n"
    return f"## Mail à envoyer\n\n{entete + chr(10) if entete else ''}{corps}\n\n"


def ranger(chemin_audio, date, transcription, analyse):
    dossier = DOSSIER_SORTIE / analyse["projet"]
    (dossier / "audio").mkdir(parents=True, exist_ok=True)

    base = f"{date:%Y-%m-%d_%Hh%M}_{slugifier(analyse['titre'])}"
    audio_final = chemin_libre(dossier / "audio" / f"{base}{chemin_audio.suffix.lower()}")
    shutil.move(str(chemin_audio), str(audio_final))

    md = chemin_libre(dossier / f"{base}.md")
    ligne_audio = f"[audio/{audio_final.name}](audio/{audio_final.name})"
    md.write_text(contenu_compte_rendu(date, transcription, analyse, ligne_audio), encoding="utf-8")
    return md


def contenu_compte_rendu(date, transcription, analyse, ligne_audio, note=""):
    return (
        f"# {analyse['titre']}\n\n"
        f"- **Date** : {date:%d/%m/%Y à %H:%M}\n"
        f"- **Projet** : {analyse['projet']}\n"
        f"- **Audio** : {ligne_audio}\n"
        + (f"- **Transcription** : {note}\n" if note else "")
        + f"\n{analyse['compte_rendu'].strip()}\n\n"
        + section_mail(analyse)
        + (f"## Contexte pour Claude\n\n```xml\n{analyse['contexte_claude'].strip()}\n```\n\n" if analyse.get("contexte_claude") else "")
        + f"---\n\n## Transcription complète\n\n{transcription}\n"
    )


# ---------------------------------------------------------------------------
# Amélioration d'un compte rendu existant
# ---------------------------------------------------------------------------

MODELE_PRO = "mlx-community/whisper-large-v3-mlx"


def decouper(texte, taille=9000):
    """Découpe une transcription en morceaux de lignes entières (pour la correction par l'IA)."""
    morceaux, courant = [], ""
    for ligne in texte.splitlines(keepends=True):
        if courant and len(courant) + len(ligne) > taille:
            morceaux.append(courant)
            courant = ""
        courant += ligne
    if courant.strip():
        morceaux.append(courant)
    return morceaux


def corriger_transcription(transcription, projet=""):
    """L'IA corrige les mots mal reconnus, sans résumer ni reformuler, horodatages gardés."""
    profil = bloc_profil()
    vocabulaire = consigne_whisper() or ""
    systeme = f"""Tu corriges une transcription automatique (Whisper) d'un appel professionnel en français.
Corrige UNIQUEMENT les erreurs de reconnaissance vocale : mots mal entendus, noms propres mal écrits
(clients, entreprises, marques, outils), chiffres et montants mal transcrits, ponctuation.
Règles strictes :
- ne résume pas, ne reformule pas, n'ajoute rien, ne supprime aucune information ;
- garde exactement une ligne par ligne d'origine et son horodatage [mm:ss] au début ;
- supprime seulement les phrases manifestement parasites (« sous-titres réalisés par… », répétitions en boucle) ;
- si tu n'es pas sûr d'une correction, laisse le mot tel quel ;
- réponds uniquement par la transcription corrigée, sans commentaire.

Projet concerné : {projet or "inconnu"}
{vocabulaire}
{("Contexte sur l'utilisateur :" + chr(10) + profil) if profil else ""}"""
    corrige = []
    for morceau in decouper(transcription):
        reponse = appeler_ia_texte(systeme, morceau).strip()
        reponse = re.sub(r"^```\w*\n|\n```$", "", reponse).strip()
        # Garde-fou : une réponse trop courte serait un résumé, on garde alors l'original
        if len(reponse) < 0.6 * len(morceau.strip()):
            print("Correction ignorée pour un morceau (réponse trop courte)", flush=True)
            reponse = morceau.strip()
        corrige.append(reponse)
    return "\n".join(corrige)


def lire_parties_compte_rendu(md):
    """(date, ligne audio, transcription) d'un compte rendu existant."""
    texte = md.read_text(encoding="utf-8")
    m = MOTIF_DATE.match(md.name)
    date = dt.datetime(*map(int, m.groups())) if m else dt.datetime.fromtimestamp(md.stat().st_mtime)
    audio = re.search(r"^- \*\*Audio\*\* : (.*)$", texte, flags=re.M)
    parties = re.split(r"\n## Transcription complète\s*\n", texte, maxsplit=1)
    transcription = parties[1].strip() if len(parties) == 2 else ""
    return date, (audio.group(1).strip() if audio else "non conservé"), transcription


def ameliorer_compte_rendu(projet, fichier, mode):
    """mode « corriger » : correction IA de la transcription existante.
    mode « pro » : nouvelle transcription avec le modèle Pro puis correction IA.
    Le compte rendu est ensuite réécrit à partir de la transcription améliorée."""
    md = chemin_compte_rendu(projet, fichier)
    date, ligne_audio, transcription = lire_parties_compte_rendu(md)
    etapes = []
    if mode == "pro":
        audio = audio_du_compte_rendu(md)
        if not audio:
            raise ValueError("l'audio de ce compte rendu n'existe plus : seule la correction par l'IA est possible")
        TRAITEMENT.update(etape="transcription", progression=0.0)
        transcription = transcrire(audio, MODELE_PRO)
        etapes.append("retranscrite avec le modèle Pro")
    if not transcription.strip():
        raise ValueError("pas de transcription à améliorer")
    TRAITEMENT.update(etape="correction", progression=None)
    transcription = corriger_transcription(transcription, projet)
    etapes.append("corrigée par l'IA")
    TRAITEMENT.update(etape="analyse", progression=None)
    analyse = analyser(transcription, date)
    if projet != PROJET_PAR_DEFAUT:
        analyse["projet"] = projet   # on garde le classement choisi
    note = f"{' puis '.join(etapes)} le {dt.datetime.now():%d/%m/%Y}"
    contenu = contenu_compte_rendu(date, transcription, analyse, ligne_audio, note)
    if analyse["projet"] == projet:
        md.write_text(contenu, encoding="utf-8")
        return {"projet": projet, "fichier": md.name, "titre": analyse["titre"]}
    # Reclassé (venait de Divers) : on déplace d'abord, puis on réécrit
    deplace = deplacer_compte_rendu(projet, fichier, analyse["projet"])
    cible = dossier_projet(deplace["projet"]) / deplace["fichier"]
    _, ligne_audio, _ = lire_parties_compte_rendu(cible)
    cible.write_text(contenu_compte_rendu(date, transcription, analyse, ligne_audio, note), encoding="utf-8")
    return {"projet": deplace["projet"], "fichier": cible.name, "titre": analyse["titre"]}


# ---------------------------------------------------------------------------
# Données pour la fenêtre
# ---------------------------------------------------------------------------

MOTIF_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})_(\d{2})h(\d{2})")


def liste_comptes_rendus():
    """Tous les comptes rendus, du plus récent au plus ancien."""
    elements = []
    for d in DOSSIER_SORTIE.iterdir():
        if not d.is_dir() or d.name.startswith(("_", ".")):
            continue
        for md in d.glob("*.md"):
            if md.name.startswith("_"):
                continue
            premiere = md.read_text(encoding="utf-8").split("\n", 1)[0]
            titre = premiere[2:].strip() if premiere.startswith("# ") else md.stem
            m = MOTIF_DATE.match(md.name)
            date = dt.datetime(*map(int, m.groups())) if m else dt.datetime.fromtimestamp(md.stat().st_mtime)
            elements.append({
                "projet": d.name,
                "fichier": md.name,
                "titre": titre,
                "date": date.isoformat(timespec="minutes"),
            })
    elements.sort(key=lambda e: e["date"], reverse=True)
    return elements


def chemin_compte_rendu(projet, fichier):
    if not fichier.endswith(".md") or "/" in fichier or fichier.startswith(("_", ".")):
        raise ValueError("fichier invalide")
    chemin = dossier_projet(projet) / fichier
    if not chemin.exists():
        raise ValueError("compte rendu introuvable")
    return chemin


def audio_du_compte_rendu(md):
    """Fichier audio lié à un compte rendu (ligne « Audio » du .md), s'il existe encore."""
    m = re.search(r"\(audio/([^)/]+)\)", md.read_text(encoding="utf-8"))
    if m:
        audio = md.parent / "audio" / m.group(1)
        if audio.exists():
            return audio
    return None


def mettre_a_la_corbeille(chemin):
    """Envoie un fichier dans la Corbeille du Mac (récupérable), sinon le supprime."""
    try:
        from Foundation import NSFileManager, NSURL
        ok, _, _ = NSFileManager.defaultManager().trashItemAtURL_resultingItemURL_error_(
            NSURL.fileURLWithPath_(str(chemin)), None, None)
        if ok:
            return
    except Exception:
        pass
    corbeille = Path.home() / ".Trash"
    if corbeille.is_dir():
        shutil.move(str(chemin), str(chemin_libre(corbeille / chemin.name)))
    else:
        chemin.unlink(missing_ok=True)


def marquer_audio_supprime(md, raison="supprimé"):
    texte = md.read_text(encoding="utf-8")
    texte = re.sub(r"^- \*\*Audio\*\* : .*$", f"- **Audio** : {raison}", texte, count=1, flags=re.M)
    md.write_text(texte, encoding="utf-8")


def supprimer_audio(projet, fichier):
    md = chemin_compte_rendu(projet, fichier)
    audio = audio_du_compte_rendu(md)
    if not audio:
        raise ValueError("pas d'audio pour ce compte rendu")
    mettre_a_la_corbeille(audio)
    marquer_audio_supprime(md, f"supprimé le {dt.datetime.now():%d/%m/%Y}")
    return {"ok": True}


def supprimer_compte_rendu(projet, fichier):
    md = chemin_compte_rendu(projet, fichier)
    audio = audio_du_compte_rendu(md)
    if audio:
        mettre_a_la_corbeille(audio)
    mettre_a_la_corbeille(md)
    return {"ok": True}


def nettoyer_audios():
    """Envoie à la Corbeille les audios rangés plus vieux que la durée choisie.
    Les comptes rendus et leurs transcriptions restent."""
    jours = int(CONFIG.get("conservation_audio") or 0)
    if jours <= 0 or not DOSSIER_SORTIE.exists():
        return 0
    limite = time.time() - jours * 86400
    supprimes = 0
    for projet in DOSSIER_SORTIE.iterdir():
        dossier_audio = projet / "audio"
        if projet.name.startswith(("_", ".")) or not dossier_audio.is_dir():
            continue
        anciens = [a for a in dossier_audio.iterdir()
                   if a.is_file() and not a.name.startswith(".") and a.stat().st_mtime < limite]
        if not anciens:
            continue
        noms = {a.name for a in anciens}
        for a in anciens:
            mettre_a_la_corbeille(a)
            supprimes += 1
        # La ligne « Audio » des comptes rendus concernés l'indique
        for md in projet.glob("*.md"):
            m = re.search(r"\(audio/([^)/]+)\)", md.read_text(encoding="utf-8"))
            if m and m.group(1) in noms:
                marquer_audio_supprime(md, f"supprimé automatiquement après {jours} jours")
    if supprimes:
        print(f"Conservation : {supprimes} audio(s) de plus de {jours} jours mis à la corbeille", flush=True)
    return supprimes


def extraire_mail(texte):
    """(destinataire, objet, corps) de la section « Mail à envoyer », ou None."""
    bloc = re.search(r"^## Mail à envoyer\s*\n(.*?)(?=^## |\n---\n|\Z)", texte, flags=re.M | re.S)
    if not bloc:
        return None
    contenu = bloc.group(1).strip()
    a = re.search(r"^\*\*À\*\* : (.*)$", contenu, flags=re.M)
    objet = re.search(r"^\*\*Objet\*\* : (.*)$", contenu, flags=re.M)
    corps = re.sub(r"^\*\*(À|Objet)\*\* : .*\n?", "", contenu, flags=re.M).strip()
    return (a.group(1).strip() if a else "", objet.group(1).strip() if objet else "", corps)


def ouvrir_dans_mail(projet, fichier):
    """Ouvre un nouveau message dans l'app Mail (ou la messagerie par défaut), prérempli."""
    md = chemin_compte_rendu(projet, fichier)
    mail = extraire_mail(md.read_text(encoding="utf-8"))
    if not mail:
        raise ValueError("pas de mail préparé dans ce compte rendu")
    destinataire, objet, corps = mail
    adresse = re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", destinataire)
    lien = "mailto:" + (adresse.group(0) if adresse else "") + "?" + urllib.parse.urlencode(
        {"subject": objet, "body": corps}, quote_via=urllib.parse.quote)
    subprocess.run(["open", lien], check=False)
    return {"ok": True}


def extraire_actions(texte):
    """Lignes de la section « Actions à faire » (puces), sans le texte d'aide."""
    bloc = re.search(r"^## Actions à faire\s*\n(.*?)(?=^## |\n---\n|\Z)", texte, flags=re.M | re.S)
    if not bloc:
        return []
    actions = []
    for ligne in bloc.group(1).splitlines():
        m = re.match(r"^\s*(?:[-*•]|\d+[.)])\s+(?:\[ \]\s*)?(.+)$", ligne)
        if m:
            actions.append(re.sub(r"\*\*(.+?)\*\*", r"\1", m.group(1)).strip())
    return actions


SCRIPT_RAPPELS = """on run argv
    set nomListe to item 1 of argv
    set note to item 2 of argv
    tell application "Reminders"
        if not (exists list nomListe) then make new list with properties {name:nomListe}
        repeat with i from 3 to count of argv
            make new reminder in list nomListe with properties {name:(item i of argv), body:note}
        end repeat
    end tell
end run"""


def actions_vers_rappels(projet, fichier):
    """Ajoute les actions d'un compte rendu dans Rappels (liste « MiroRecap », synchronisée
    avec l'iPhone). Le texte passe en arguments séparés : aucun risque d'injection."""
    md = chemin_compte_rendu(projet, fichier)
    texte = md.read_text(encoding="utf-8")
    actions = extraire_actions(texte)
    if not actions:
        raise ValueError("aucune action à faire dans ce compte rendu")
    premiere = texte.split("\n", 1)[0].lstrip("# ").strip()
    note = f"{projet} · {premiere}"
    r = subprocess.run(["osascript", "-e", SCRIPT_RAPPELS, "MiroRecap", note, *actions],
                       capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        erreur = r.stderr.strip()
        if "-1743" in erreur or "not allowed" in erreur.lower() or "autoris" in erreur.lower():
            raise ValueError("autorise MiroRecap à utiliser Rappels : Réglages Système → Confidentialité et "
                             "sécurité → Automatisation (ou Rappels), puis réessaie")
        raise ValueError(f"Rappels : {erreur or 'erreur inconnue'}")
    return {"ok": True, "ajoutees": len(actions)}


def lire_compte_rendu(projet, fichier):
    chemin = chemin_compte_rendu(projet, fichier)
    return {"markdown": chemin.read_text(encoding="utf-8"),
            "audio": audio_du_compte_rendu(chemin) is not None}


def compter_cr(dossier):
    return sum(1 for f in dossier.glob("*.md") if not f.name.startswith("_"))


def section_markdown(texte, titre):
    """Contenu d'une section « ## titre » (jusqu'à la section suivante ou au ---)."""
    m = re.search(rf"^##\s+{re.escape(titre)}\s*$(.*?)(?=^##\s|^---\s*$|\Z)", texte, re.M | re.S)
    return m.group(1).strip() if m else ""


SYNTHESES = {}   # état des synthèses en cours, par projet


def lancer_synthese(nom, silencieux=False):
    """« Où on en est » : Claude relit tous les comptes rendus du projet et fait le point."""
    dossier = dossier_projet(nom)
    if SYNTHESES.get(nom) == "en_cours":
        return

    def tache():
        SYNTHESES[nom] = "en_cours"
        try:
            fichiers = sorted(f for f in dossier.glob("*.md") if not f.name.startswith("_"))
            if not fichiers:
                raise RuntimeError("aucun compte rendu dans ce projet")
            morceaux = []
            for f in fichiers:
                texte = f.read_text(encoding="utf-8").split("\n---\n")[0]   # sans la transcription
                morceaux.append(f"=== {f.stem} ===\n{texte}")
            contenu = "\n\n".join(morceaux)[-120000:]   # les plus récents en priorité si c'est très long
            desc = lister_projets().get(nom, "")
            systeme = f"""Tu fais le point sur un projet à partir de tous ses comptes rendus d'appels, du plus ancien au plus récent. Projet : {nom}{f" ({desc})" if desc else ""}.
Rédige en français, en markdown, sans titre principal, avec ces sections (omets celles qui seraient vides) :
## Où on en est
(trois phrases maximum : la situation actuelle)
## Décisions prises
## Actions en cours
(qui : quoi (échéance) ; si un appel plus récent montre qu'une action est faite, ne la liste pas)
## Chiffres clés
## Prochaines étapes
## Points à clarifier
Quand deux appels se contredisent, garde le plus récent et signale le changement. N'invente rien."""
            synthese = appeler_ia_texte(systeme, contenu).strip()
            (dossier / "_synthese.md").write_text(
                f"<!-- {dt.datetime.now():%Y-%m-%dT%H:%M} · {len(fichiers)} comptes rendus -->\n{synthese}\n", encoding="utf-8")
            SYNTHESES[nom] = "ok"
            if not silencieux:
                notifier(f"Point d'étape prêt : {nom}", "La synthèse du projet est à jour.")
        except Exception as e:
            print(f"Synthèse {nom} : {e}", flush=True)
            SYNTHESES[nom] = f"echec : {e}"

    threading.Thread(target=tache, daemon=True).start()


def lire_projet(nom):
    """Tout ce qu'il faut pour la page d'un projet."""
    dossier = dossier_projet(nom)
    cr = [c for c in liste_comptes_rendus() if c["projet"] == nom]
    actions = []
    for c in cr:
        texte = (dossier / c["fichier"]).read_text(encoding="utf-8")
        for ligne in section_markdown(texte, "Actions à faire").splitlines():
            ligne = re.sub(r"^\s*(?:[-*]|\d+[.)])\s*", "", ligne).strip()
            if ligne and not ligne.startswith("("):
                actions.append({"texte": ligne, "titre": c["titre"], "fichier": c["fichier"], "date": c["date"]})
    synthese, date_synthese = "", ""
    fichier_synthese = dossier / "_synthese.md"
    if fichier_synthese.exists():
        brut = fichier_synthese.read_text(encoding="utf-8")
        m = re.match(r"<!-- (\S+) · (\d+) comptes rendus -->\n", brut)
        date_synthese = m.group(1) if m else ""
        synthese = brut[m.end():] if m else brut
    desc = "Ce qui ne correspond à aucun projet." if nom == PROJET_PAR_DEFAUT else lister_projets().get(nom, "")
    fiche = lire_fiche(nom) if nom != PROJET_PAR_DEFAUT else None
    if fiche:
        actions = sorted(fiche["actions"], key=lambda a: (a["fait"], a.get("date", "")), reverse=False)
        actions = [a for a in actions if not a["fait"]][::-1] + [a for a in actions if a["fait"]][::-1]
        fiche["jours_sans_contact"] = jours_sans_contact(fiche)
    return {
        "fiche": fiche,
        "nom": nom, "description": desc, "modifiable": nom != PROJET_PAR_DEFAUT,
        "comptes_rendus": cr, "actions": actions[:40],
        "synthese": synthese, "date_synthese": date_synthese,
        "synthese_etat": SYNTHESES.get(nom, ""),
        "synthese_perimee": bool(synthese) and bool(cr) and cr[0]["date"] > date_synthese,
    }


# ---------------------------------------------------------------------------
# Fiche client d'un projet (_fiche.json) : statut, interlocuteurs, actions à cocher…
# ---------------------------------------------------------------------------

def _id_action(texte):
    import hashlib
    return hashlib.sha1(re.sub(r"\W+", " ", texte.lower()).strip().encode()).hexdigest()[:10]


def actions_depuis_compte_rendu(md):
    texte = md.read_text(encoding="utf-8")
    m = MOTIF_DATE.match(md.name)
    date = dt.datetime(*map(int, m.groups())).isoformat(timespec="minutes") if m else ""
    titre = texte.split("\n", 1)[0].lstrip("# ").strip()
    return [{"id": _id_action(a), "texte": a, "fait": False, "fichier": md.name, "titre": titre, "date": date}
            for a in extraire_actions(texte)]


def lire_fiche(nom):
    """Fiche du projet ; créée à la volée (depuis les comptes rendus existants) si elle n'existe pas."""
    dossier = dossier_projet(nom)
    fichier = dossier / "_fiche.json"
    if fichier.exists():
        try:
            return json.loads(fichier.read_text(encoding="utf-8"))
        except Exception:
            pass
    fiche = {"statut": "", "interlocuteurs": [], "budget": "", "besoin": "", "dernier_contact": "", "actions": [],
             "relance_notifiee": ""}
    for md in sorted((f for f in dossier.glob("*.md") if not f.name.startswith("_")), key=lambda f: f.name):
        for a in actions_depuis_compte_rendu(md):
            if a["id"] not in {x["id"] for x in fiche["actions"]}:
                fiche["actions"].append(a)
        m = MOTIF_DATE.match(md.name)
        if m:
            fiche["dernier_contact"] = max(fiche["dernier_contact"], dt.datetime(*map(int, m.groups())).isoformat(timespec="minutes"))
    return fiche


def ecrire_fiche(nom, fiche):
    (dossier_projet(nom) / "_fiche.json").write_text(json.dumps(fiche, ensure_ascii=False, indent=1), encoding="utf-8")


def mettre_a_jour_fiche(nom, analyse, date, md):
    """Après un appel : statut, interlocuteurs, budget, besoin, dernier contact et nouvelles actions."""
    if nom == PROJET_PAR_DEFAUT:
        return
    fiche = lire_fiche(nom)
    if analyse.get("statut") in STATUTS:
        fiche["statut"] = analyse["statut"]
    for ligne in str(analyse.get("interlocuteurs") or "").splitlines():
        ligne = ligne.strip(" -•\t")
        cle = ligne.split("·")[0].strip().lower()
        if ligne and cle not in {x.split("·")[0].strip().lower() for x in fiche["interlocuteurs"]}:
            fiche["interlocuteurs"].append(ligne)
    for champ in ("budget", "besoin"):
        if str(analyse.get(champ) or "").strip():
            fiche[champ] = analyse[champ].strip()
    fiche["dernier_contact"] = max(fiche.get("dernier_contact") or "", date.isoformat(timespec="minutes"))
    connues = {a["id"] for a in fiche["actions"]}
    fiche["actions"] += [a for a in actions_depuis_compte_rendu(md) if a["id"] not in connues]
    fiche["relance_notifiee"] = ""
    ecrire_fiche(nom, fiche)


def modifier_fiche(nom, donnees):
    if nom == PROJET_PAR_DEFAUT:
        raise ValueError("Divers n'a pas de fiche")
    fiche = lire_fiche(nom)
    if donnees.get("statut") in STATUTS + [""]:
        fiche["statut"] = donnees["statut"]
    if donnees.get("action"):
        for a in fiche["actions"]:
            if a["id"] == donnees["action"]:
                a["fait"] = bool(donnees.get("fait"))
    ecrire_fiche(nom, fiche)
    return lire_projet(nom)


def jours_sans_contact(fiche):
    try:
        return (dt.datetime.now() - dt.datetime.fromisoformat(fiche["dernier_contact"])).days
    except Exception:
        return None


def verifier_relances():
    """Projets actifs sans nouvelles depuis 10 jours : une notification par semaine au plus."""
    for nom in lister_projets():
        try:
            fiche = lire_fiche(nom)
            jours = jours_sans_contact(fiche)
            if fiche.get("statut") not in ("prospect", "devis", "en_cours") or jours is None or jours < 10:
                continue
            deja = fiche.get("relance_notifiee") or ""
            if deja and (dt.datetime.now() - dt.datetime.fromisoformat(deja)).days < 7:
                continue
            notifier(f"Pas de nouvelles de {nom}", f"Dernier échange il y a {jours} jours. Une relance ?")
            fiche["relance_notifiee"] = dt.datetime.now().isoformat(timespec="minutes")
            ecrire_fiche(nom, fiche)
        except Exception as e:
            print(f"Relance {nom} : {e}", flush=True)


# ---------------------------------------------------------------------------
# Propositions de nouveaux projets (client détecté dans un appel rangé dans Divers)
# ---------------------------------------------------------------------------

FICHIER_PROPOSITIONS = DOSSIER_APP / "propositions.json"


def lire_propositions():
    try:
        return json.loads(FICHIER_PROPOSITIONS.read_text(encoding="utf-8"))
    except Exception:
        return []


def ajouter_proposition(analyse, fichier):
    nom = re.sub(r"[/\\:]", " ", str(analyse.get("nouveau_projet") or "")).strip()[:60]
    if not nom or nom.lower() in {p.lower() for p in lister_projets()} or nom == PROJET_PAR_DEFAUT:
        return None
    propositions = lire_propositions()
    for p in propositions:
        if p["nom"].lower() == nom.lower():   # même client : on regroupe les appels
            p["fichiers"].append(fichier)
            break
    else:
        propositions.append({"nom": nom, "description": str(analyse.get("nouveau_projet_description") or "").strip(),
                             "fichiers": [fichier],
                             "fiche": {k: analyse.get(k, "") for k in ("statut", "interlocuteurs", "budget", "besoin")}})
    FICHIER_PROPOSITIONS.parent.mkdir(parents=True, exist_ok=True)
    FICHIER_PROPOSITIONS.write_text(json.dumps(propositions, ensure_ascii=False, indent=1), encoding="utf-8")
    return nom


def traiter_proposition(nom, action):
    propositions = lire_propositions()
    prop = next((p for p in propositions if p["nom"] == nom), None)
    if not prop:
        raise ValueError("proposition introuvable")
    restantes = [p for p in propositions if p is not prop]
    FICHIER_PROPOSITIONS.write_text(json.dumps(restantes, ensure_ascii=False, indent=1), encoding="utf-8")
    if action != "creer":
        return {"ok": True}
    if nom not in lister_projets():
        creer_projet(nom, prop.get("description", ""))
    deplaces = []
    for fichier in prop["fichiers"]:
        try:
            deplaces.append(deplacer_compte_rendu(PROJET_PAR_DEFAUT, fichier, nom)["fichier"])
        except Exception as e:
            print(f"Proposition {nom} : {fichier} non déplacé ({e})", flush=True)
    # Fiche initiale à partir de ce que l'appel a appris
    for f in deplaces:
        md = dossier_projet(nom) / f
        m = MOTIF_DATE.match(f)
        date = dt.datetime(*map(int, m.groups())) if m else dt.datetime.now()
        mettre_a_jour_fiche(nom, prop.get("fiche", {}), date, md)
    if deplaces:
        lancer_synthese(nom, silencieux=True)
    return {"ok": True, "projet": nom, "deplaces": len(deplaces)}


def deplacer_compte_rendu(projet, fichier, vers):
    """Change un compte rendu de projet (avec son audio)."""
    if not fichier.endswith(".md") or "/" in fichier or fichier.startswith("_"):
        raise ValueError("fichier invalide")
    source = dossier_projet(projet) / fichier
    cible_dossier = dossier_projet(vers)
    texte = source.read_text(encoding="utf-8")
    m = re.search(r"\(audio/([^)]+)\)", texte)
    if m and (source.parent / "audio" / m.group(1)).exists():
        (cible_dossier / "audio").mkdir(exist_ok=True)
        audio_cible = chemin_libre(cible_dossier / "audio" / m.group(1))
        shutil.move(str(source.parent / "audio" / m.group(1)), str(audio_cible))
        texte = texte.replace(f"audio/{m.group(1)}", f"audio/{audio_cible.name}")
    texte = re.sub(r"^- \*\*Projet\*\* : .*$", f"- **Projet** : {vers}", texte, count=1, flags=re.M)
    texte = re.sub(r'(<appel[^>]*projet=")[^"]*(")', rf"\g<1>{vers}\g<2>", texte, count=1)
    cible = chemin_libre(cible_dossier / fichier)
    cible.write_text(texte, encoding="utf-8")
    source.unlink()
    return {"projet": vers, "fichier": cible.name}


def liste_projets_detaillee():
    resultat = []
    for nom, desc in lister_projets().items():
        fiche = lire_fiche(nom)
        resultat.append({"nom": nom, "description": desc, "nb": compter_cr(DOSSIER_SORTIE / nom), "modifiable": True,
                         "statut": fiche.get("statut", ""), "jours_sans_contact": jours_sans_contact(fiche),
                         "actions_ouvertes": sum(1 for a in fiche["actions"] if not a["fait"])})
    resultat.append({
        "nom": PROJET_PAR_DEFAUT,
        "description": "Ce qui ne correspond à aucun projet.",
        "nb": compter_cr(DOSSIER_SORTIE / PROJET_PAR_DEFAUT),
        "modifiable": False,
    })
    return resultat


def creer_projet(nom, description):
    nom = nom_valide(nom)
    dossier = DOSSIER_SORTIE / nom
    if dossier.exists():
        raise ValueError("ce projet existe déjà")
    dossier.mkdir()
    if description.strip():
        (dossier / "description.txt").write_text(description.strip() + "\n", encoding="utf-8")


def modifier_projet(ancien, nom, description):
    if ancien == PROJET_PAR_DEFAUT:
        raise ValueError("le projet Divers ne se modifie pas")
    dossier = dossier_projet(ancien)
    nom = nom_valide(nom)
    if nom != ancien:
        cible = DOSSIER_SORTIE / nom
        if cible.exists():
            raise ValueError("un projet porte déjà ce nom")
        dossier = dossier.rename(cible)
    fichier_desc = dossier / "description.txt"
    if description.strip():
        fichier_desc.write_text(description.strip() + "\n", encoding="utf-8")
    elif fichier_desc.exists():
        fichier_desc.unlink()


def lire_reglages():
    cle = CONFIG.get("cle_api_anthropic", "")
    return {
        "cle_definie": bool(cle),
        "cle_apercu": f"{cle[:7]}…{cle[-4:]}" if len(cle) > 12 else "",
        "modele_whisper": CONFIG["modele_whisper"],
        "modeles": MODELES_WHISPER,
        "dossier_actuel": str(DOSSIER_SORTIE),
        "dossier_personnalise": CONFIG.get("dossier_sortie", ""),
        "erreurs": compter_erreurs(),
        "moteur": CONFIG.get("moteur", "auto"),
        "moteur_effectif": moteur_effectif(),
        "claude_code": trouver_claude_code(),
        "claude_connecte": bool(CONFIG.get("jeton_claude_code")),
        "connexion": CONNEXION,
        "son_systeme": CONFIG.get("son_systeme", True),
        "maj_auto": CONFIG.get("maj_auto", True),
        "conservation_audio": int(CONFIG.get("conservation_audio") or 0),
        "chatgpt_connecte": bool(CONFIG.get("chatgpt_connecte")),
        "codex": bool(trouver_codex()),
        "connexion_gpt": CONNEXION_GPT,
        "cle_openai_definie": bool(CONFIG.get("cle_api_openai")),
        "cle_openai_apercu": (lambda c: f"{c[:7]}…{c[-4:]}" if len(c) > 12 else "")(CONFIG.get("cle_api_openai", "")),
        "contexte_perso": CONFIG.get("contexte_perso", ""),
        "signature_mail": CONFIG.get("signature_mail", ""),
        "accueil_fait": bool(CONFIG.get("accueil_fait")),
        "modele_choisi": bool(CONFIG.get("modele_choisi")),
        "mcp_installe": mcp_installe(),
        "detection_appels": CONFIG.get("detection_appels", "proposer"),
        "vocabulaire": CONFIG.get("vocabulaire", ""),
    }


def sauver_reglages(donnees):
    redemarrage = False
    if donnees.get("cle", "").strip():
        CONFIG["cle_api_anthropic"] = donnees["cle"].strip()
    if donnees.get("cle_openai", "").strip():
        CONFIG["cle_api_openai"] = donnees["cle_openai"].strip()
    for champ in ("contexte_perso", "signature_mail", "vocabulaire"):
        if isinstance(donnees.get(champ), str):
            CONFIG[champ] = donnees[champ].strip()[:4000]
    if donnees.get("accueil_fait") is True:
        CONFIG["accueil_fait"] = True
    if donnees.get("moteur") in ("auto",) + MOTEURS:
        CONFIG["moteur"] = donnees["moteur"]
    if donnees.get("modele_whisper") in {m["id"] for m in MODELES_WHISPER}:
        change = donnees["modele_whisper"] != CONFIG["modele_whisper"]
        CONFIG["modele_whisper"] = donnees["modele_whisper"]
        premier = not CONFIG.get("modele_choisi")
        CONFIG["modele_choisi"] = True
        if change or premier:
            sauver_config(CONFIG)
            preparer_modele_en_fond()   # télécharge tout de suite, pas au prochain appel
    if donnees.get("detection_appels") in ("proposer", "auto", "rien"):
        CONFIG["detection_appels"] = donnees["detection_appels"]
    if donnees.get("conservation_audio") in (0, 7, 30, 90):
        CONFIG["conservation_audio"] = donnees["conservation_audio"]
        sauver_config(CONFIG)
        threading.Thread(target=nettoyer_audios, daemon=True).start()
    if isinstance(donnees.get("maj_auto"), bool):
        CONFIG["maj_auto"] = donnees["maj_auto"]
    if isinstance(donnees.get("son_systeme"), bool):
        CONFIG["son_systeme"] = donnees["son_systeme"]
    if "dossier_sortie" in donnees and donnees["dossier_sortie"].strip() != CONFIG.get("dossier_sortie", ""):
        CONFIG["dossier_sortie"] = donnees["dossier_sortie"].strip()
        redemarrage = True  # les chemins sont calculés au démarrage
    sauver_config(CONFIG)
    return {"ok": True, "redemarrage": redemarrage}


# ---------------------------------------------------------------------------
# API locale (127.0.0.1 uniquement, jeton obligatoire)
# ---------------------------------------------------------------------------

JETON = secrets.token_urlsafe(24)


class Api(http.server.BaseHTTPRequestHandler):
    app = None  # renseigné au démarrage

    def log_message(self, *args):
        pass  # pas de log à chaque requête

    def _envoyer(self, code, corps, type_mime):
        self.send_response(code)
        self.send_header("Content-Type", type_mime)
        self.send_header("Content-Length", str(len(corps)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(corps)

    def _json(self, code, donnees):
        self._envoyer(code, json.dumps(donnees, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def _autorise(self, page):
        # Anti « DNS rebinding » : l'hôte doit être exactement 127.0.0.1:<port>
        if self.headers.get("Host") != f"127.0.0.1:{self.server.server_port}":
            return False
        # La page elle-même est servie sans jeton (elle le contient), l'API exige le jeton
        return page or secrets.compare_digest(self.headers.get("X-Jeton", ""), JETON)

    def _corps(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n)) if n else {}

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        page = url.path in ("/", "/index.html")
        if not self._autorise(page):
            return self._json(403, {"erreur": "accès refusé"})
        q = {k: v[0] for k, v in urllib.parse.parse_qs(url.query).items()}
        try:
            if page:
                html = (DOSSIER_CODE / "ui" / "index.html").read_text(encoding="utf-8").replace("__JETON__", JETON)
                return self._envoyer(200, html.encode(), "text/html; charset=utf-8")
            routes = {
                "/api/etat": lambda: self.app.etat(),
                "/api/comptes-rendus": liste_comptes_rendus,
                "/api/compte-rendu": lambda: lire_compte_rendu(q.get("projet", ""), q.get("fichier", "")),
                "/api/projets": liste_projets_detaillee,
                "/api/projet": lambda: lire_projet(q.get("nom", "")),
                "/api/reglages": lire_reglages,
            }
            if url.path not in routes:
                return self._json(404, {"erreur": "introuvable"})
            self._json(200, routes[url.path]())
        except Exception as e:
            self._json(400, {"erreur": str(e)})

    def do_POST(self):
        if not self._autorise(False):
            return self._json(403, {"erreur": "accès refusé"})
        try:
            d = self._corps()
            if self.path == "/api/enregistrement":
                self.app.basculer_enregistrement()
                return self._json(200, self.app.etat())
            if self.path == "/api/projets":
                creer_projet(d.get("nom", ""), d.get("description", ""))
                return self._json(200, liste_projets_detaillee())
            if self.path == "/api/projets/synthese":
                lancer_synthese(d.get("nom", ""))
                return self._json(200, {"ok": True})
            if self.path == "/api/chatgpt":
                action = d.get("action")
                if action == "deconnecter":
                    deconnecter_chatgpt()
                elif action == "annuler":
                    annuler_connexion_chatgpt()
                elif action == "ouvrir":
                    if CONNEXION_GPT.get("url", "").startswith("https://"):
                        subprocess.run(["open", CONNEXION_GPT["url"]], check=False)
                else:
                    connecter_chatgpt()
                return self._json(200, lire_reglages())
            if self.path == "/api/test-micro":
                return self._json(200, tester_micro())
            if self.path == "/api/test-son":
                return self._json(200, tester_son_systeme())
            if self.path == "/api/fiche":
                return self._json(200, modifier_fiche(d.get("nom", ""), d))
            if self.path == "/api/propositions":
                return self._json(200, traiter_proposition(d.get("nom", ""), d.get("action", "")))
            if self.path == "/api/ameliorer":
                return self._json(200, self.app.demander_amelioration(d.get("projet", ""), d.get("fichier", ""), d.get("mode", "")))
            if self.path == "/api/mail":
                return self._json(200, ouvrir_dans_mail(d.get("projet", ""), d.get("fichier", "")))
            if self.path == "/api/rappels":
                return self._json(200, actions_vers_rappels(d.get("projet", ""), d.get("fichier", "")))
            if self.path == "/api/mcp":
                (deconnecter_mcp if d.get("action") == "deconnecter" else connecter_mcp)()
                return self._json(200, lire_reglages())
            if self.path == "/api/quitter":
                return self._json(200, self.app.quitter_depuis_fenetre())
            if self.path == "/api/supprimer":
                if isinstance(d.get("elements"), list):   # sélection multiple
                    faits, erreurs = 0, []
                    for e in d["elements"][:500]:
                        try:
                            supprimer_compte_rendu(str(e.get("projet", "")), str(e.get("fichier", "")))
                            faits += 1
                        except Exception as err:
                            erreurs.append(f"{e.get('fichier')} : {err}")
                    return self._json(200, {"supprimes": faits, "erreurs": erreurs})
                if d.get("quoi") == "audio":
                    return self._json(200, supprimer_audio(d.get("projet", ""), d.get("fichier", "")))
                return self._json(200, supprimer_compte_rendu(d.get("projet", ""), d.get("fichier", "")))
            if self.path == "/api/deplacer":
                return self._json(200, deplacer_compte_rendu(d.get("projet", ""), d.get("fichier", ""), d.get("vers", "")))
            if self.path == "/api/projets/modifier":
                modifier_projet(d.get("ancien", ""), d.get("nom", ""), d.get("description", ""))
                return self._json(200, liste_projets_detaillee())
            if self.path == "/api/reglages":
                return self._json(200, sauver_reglages(d))
            if self.path == "/api/erreurs/relancer":
                return self._json(200, {"relances": relancer_erreurs()})
            if self.path == "/api/claude-code":
                if d.get("action") == "deconnecter":
                    deconnecter_claude_code()
                elif d.get("action") == "code":
                    try:
                        envoyer_code_claude(d.get("code"))
                    except ValueError as e:
                        return self._json(400, {"erreur": str(e)})
                elif d.get("action") == "annuler":
                    annuler_connexion_claude()
                elif d.get("action") == "ouvrir":
                    if CONNEXION.get("url", "").startswith("https://"):
                        subprocess.run(["open", CONNEXION["url"]], check=False)
                else:
                    connecter_claude_code()
                return self._json(200, lire_reglages())
            if self.path == "/api/maj/verifier":
                self.app.verifier_maj_maintenant()
                return self._json(200, self.app.etat())
            if self.path == "/api/maj":
                self.app.lancer_maj()
                return self._json(200, {"ok": True})
            if self.path == "/api/ouvrir":
                cible = d.get("cible")
                if cible == "dossier":
                    ouvrir_finder(DOSSIER_SORTIE)
                elif cible == "import":
                    ouvrir_finder(DOSSIER_IMPORT)
                elif cible == "erreurs":
                    ouvrir_finder(DOSSIER_ERREURS)
                elif cible == "console":
                    subprocess.run(["open", "https://console.anthropic.com/settings/keys"], check=False)
                elif cible == "compte-rendu":
                    chemin = dossier_projet(d.get("projet", "")) / d.get("fichier", "")
                    if chemin.suffix == ".md" and chemin.exists():
                        ouvrir_finder(chemin, reveler=True)
                return self._json(200, {"ok": True})
            self._json(404, {"erreur": "introuvable"})
        except Exception as e:
            self._json(400, {"erreur": str(e)})


# ---------------------------------------------------------------------------
# App barre de menus
# ---------------------------------------------------------------------------

@rumps.notifications
def clic_notification(_info):
    """Clic sur une notification de MiroRecap : ouvre la fenêtre."""
    if APP:
        APP.ouvrir_fenetre()


APP = None


class App(rumps.App):
    def __init__(self):
        super().__init__("MiroRecap", title="🎙", quit_button=None)
        self.enreg = Enregistreur()
        self.verrou = threading.Lock()   # l'enregistrement se pilote depuis le menu ET la fenêtre
        self.file_traitement = queue.Queue()
        self.en_file = set()             # fichiers déjà en file (évite les doublons)
        self.tailles = {}                # taille des fichiers d'import au dernier scan
        self.icloud_demandes = set()     # fichiers iCloud dont on a déjà demandé le téléchargement
        self.statut = ""                 # écrit par le thread de traitement, lu par l'UI
        self.dernier = None              # {"projet", "fichier", "titre"} du dernier compte rendu
        self.fenetre = None
        self.maj = None                  # numéro de la nouvelle version si disponible
        self.derniere_erreur = ""        # message de la dernière erreur de traitement
        self.maj_en_cours = False
        self.maj_progression = {"phase": "", "fait": 0, "total": 0}   # téléchargement
        self.maj_infos = None
        self.derniere_amelioration = None            # comment installer la nouvelle version (derniere_release)
        self._compteur = 0

        self.bouton = rumps.MenuItem("Démarrer l'enregistrement", callback=lambda _: self.basculer_enregistrement())
        self.item_dernier = rumps.MenuItem("Dernier compte rendu : —", callback=self.ouvrir_dernier)
        self.item_maj = rumps.MenuItem(f"Version {VERSION} · Rechercher une mise à jour", callback=self.clic_maj)
        self.menu = [
            self.bouton,
            rumps.MenuItem("Ouvrir MiroRecap", callback=lambda _: self.ouvrir_fenetre()),
            None,
            rumps.MenuItem("Ouvrir les comptes rendus dans le Finder", callback=lambda _: ouvrir_finder(DOSSIER_SORTIE)),
            rumps.MenuItem("Ouvrir le dossier d'import", callback=lambda _: ouvrir_finder(DOSSIER_IMPORT)),
            None,
            self.item_dernier,
            None,
            self.item_maj,
            rumps.MenuItem("Quitter MiroRecap", callback=self.quitter),
        ]

        # API locale sur un port libre choisi par le système
        Api.app = self
        self.serveur = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Api)
        threading.Thread(target=self.serveur.serve_forever, daemon=True).start()

        # Enregistrements interrompus (app quittée ou mise à jour pendant un traitement) :
        # on les remet en traitement au lieu de les laisser bloqués dans _En-cours
        for f in DOSSIER_ENREG.iterdir():
            if f.suffix.lower() in EXTENSIONS_AUDIO and f.stat().st_size > 1024:
                shutil.move(str(f), str(chemin_libre(DOSSIER_IMPORT / f.name)))

        # Pistes « son système » d'un enregistrement interrompu : inutilisables seules
        if DOSSIER_CAPTURE.exists():
            shutil.rmtree(DOSSIER_CAPTURE, ignore_errors=True)
        self.pistes_systeme = {}         # piste micro → piste son système à mélanger

        threading.Thread(target=self.travailleur, daemon=True).start()
        threading.Thread(target=self.boucle_maj, daemon=True).start()
        threading.Thread(target=self.boucle_nettoyage, daemon=True).start()
        self.detecteur = DetecteurAppels(self)
        # Code téléchargé lancé avec succès : le démarreur ne le comptera pas comme raté
        if DOSSIER_CODE != CODE_APP:
            threading.Timer(10, lambda: (DOSSIER_CODE / ".essais").unlink(missing_ok=True)).start()
        if LANCEUR_MCP.exists():
            try:
                ecrire_lanceur_mcp()
            except Exception:
                pass
        # Juste après une mise à jour : on l'annonce
        precedente = CONFIG.pop("version_precedente", None)
        if precedente and precedente != VERSION:
            sauver_config(CONFIG)
            notifier(f"MiroRecap {VERSION} est installé", "Mise à jour terminée, tout est prêt.")
        # Modèle Whisper : choisi par l'utilisateur au premier lancement (fenêtre de choix).
        # Installations existantes dont le modèle est déjà sur le Mac : rien à demander.
        if not CONFIG.get("modele_choisi") and modele_local(CONFIG["modele_whisper"]):
            CONFIG["modele_choisi"] = True
            sauver_config(CONFIG)
        # Installation d'avant l'assistant (5.3) déjà configurée : on ne le montre pas
        try:
            cles_fichier = json.loads(FICHIER_CONFIG.read_text(encoding="utf-8")) if FICHIER_CONFIG.exists() else {}
        except Exception:
            cles_fichier = {}
        if "accueil_fait" not in cles_fichier and (CONFIG.get("modele_choisi") or CONFIG.get("jeton_claude_code")
                                                   or CONFIG.get("cle_api_anthropic")):
            CONFIG["accueil_fait"] = True
            sauver_config(CONFIG)
        if CONFIG.get("modele_choisi"):
            preparer_modele_en_fond()
        # L'UI de la barre de menus ne doit être modifiée que depuis le thread principal
        self.timer = rumps.Timer(self.tic, 1)
        self.timer.start()

        # Clic sur l'icône de l'app alors qu'elle tourne : le lanceur envoie SIGUSR1
        import signal
        self.fenetre_demandee = False
        signal.signal(signal.SIGUSR1, lambda *_: setattr(self, "fenetre_demandee", True))

        # Premier lancement (rien de configuré) : choix du modèle puis réglages ; ouverture par l'utilisateur : fenêtre
        if not CONFIG.get("accueil_fait") or moteur_effectif() is None:
            self.ouvrir_fenetre("reglages")
        elif os.environ.get("MIRORECAP_FENETRE") == "1":
            self.ouvrir_fenetre()

    # --- État partagé avec la fenêtre --------------------------------------

    def etat(self):
        duree = int((dt.datetime.now() - self.enreg.debut).total_seconds()) if self.enreg.actif else 0
        return {
            "enregistrement": self.enreg.actif,
            "duree": duree,
            "statut": self.statut,
            "en_attente": self.file_traitement.qsize(),
            "erreurs": compter_erreurs(),
            "cle_ok": moteur_effectif() is not None,
            "moteur": moteur_effectif(),
            "dernier": self.dernier,
            "version": VERSION,
            "maj": self.maj,
            "maj_en_cours": self.maj_en_cours,
            "maj_progression": self.maj_progression,
            "derniere_erreur": self.derniere_erreur,
            "modele": MODELE,
            "choix_modele": not CONFIG.get("modele_choisi"),
            "accueil": not CONFIG.get("accueil_fait"),
            "traitement": dict(TRAITEMENT) if TRAITEMENT["etape"] else None,
            "derniere_amelioration": self.derniere_amelioration,
            "propositions": [{"nom": p["nom"], "description": p.get("description", ""), "nb": len(p["fichiers"])}
                             for p in lire_propositions()],
            "appel_detecte": (self.detecteur.appel if getattr(self, "detecteur", None) and not self.enreg.actif else None),
            "son_systeme": SON_SYSTEME,
        }

    # --- Mises à jour ---------------------------------------------------------

    def verifier_maj_maintenant(self):
        """Compare la version locale à celle de GitHub. Notifie une seule fois par nouvelle version."""
        rel = derniere_release()
        v = rel["version"] if rel else None
        if v and version_tuple(v) > version_tuple(VERSION):
            self.maj_infos = rel
            if self.maj != v:
                if not CONFIG.get("maj_auto", True):
                    notifier("Mise à jour disponible", f"MiroRecap {v} est sortie. 🎙 → Mettre à jour")
            self.maj = v
        return self.maj

    def boucle_nettoyage(self):
        time.sleep(60)
        while True:
            try:
                nettoyer_audios()
            except Exception as e:
                print(f"Conservation des audios : {e}", flush=True)
            verifier_relances()
            time.sleep(6 * 3600)

    def occupee(self):
        return self.enreg.actif or bool(self.statut) or not self.file_traitement.empty()

    def boucle_maj(self):
        time.sleep(30)  # laisse l'app démarrer tranquillement
        while True:
            self.verifier_maj_maintenant()
            # Mise à jour automatique : on attend que l'app soit libre (vérifié toutes les 2 min)
            while self.maj and CONFIG.get("maj_auto", True) and not self.maj_en_cours:
                if not self.occupee():
                    try:
                        self.lancer_maj(automatique=True)
                    except Exception as e:
                        print(f"Mise à jour automatique : {e}", flush=True)
                    break
                time.sleep(120)
            time.sleep(INTERVALLE_VERIF)

    def clic_maj(self, _):
        if self.maj:
            try:
                self.lancer_maj()
            except Exception as e:
                notifier("Mise à jour impossible pour l'instant", str(e))
        else:
            def verifier():
                if not self.verifier_maj_maintenant():
                    notifier("MiroRecap est à jour", f"Tu as la dernière version ({VERSION}).")
            threading.Thread(target=verifier, daemon=True).start()

    def lancer_maj(self, automatique=False):
        """Télécharge le DMG dans l'app (avec progression), puis lance l'installateur en
        arrière-plan : il arrête l'app, la remplace (réglages, projets et comptes rendus
        conservés) puis la relance."""
        if not self.maj:
            raise RuntimeError("aucune mise à jour disponible")
        if self.enreg.actif:
            raise RuntimeError("arrête d'abord l'enregistrement en cours")
        if self.statut or not self.file_traitement.empty():
            raise RuntimeError("attends la fin du traitement en cours")
        if self.maj_en_cours:
            return
        self.maj_en_cours = True
        self.maj_progression = {"phase": "telechargement", "fait": 0, "total": 0}
        if not automatique:
            notifier("Mise à jour en cours", f"Téléchargement de MiroRecap {self.maj}…")
        leger = bool(self.maj_infos and self.maj_infos.get("type") == "code")
        cible = self._installer_code if leger else self._installer_maj
        threading.Thread(target=cible, args=(automatique,), daemon=True).start()

    def _installer_code(self, automatique):
        """Mise à jour légère : télécharge le nouveau code, le vérifie, le range dans
        code/actuel (l'ancien passe en code/precedent) puis redémarre le moteur.
        L'app dans Applications n'est pas touchée."""
        import hashlib
        import zipfile
        infos = self.maj_infos
        DOSSIER_CODES.mkdir(parents=True, exist_ok=True)
        nouveau = DOSSIER_CODES / "nouveau"
        archive = DOSSIER_CODES / "code.zip"
        try:
            with urllib.request.urlopen(infos["code"], timeout=60) as reponse, open(archive, "wb") as f:
                self.maj_progression["total"] = int(reponse.headers.get("Content-Length") or infos.get("taille") or 0)
                empreinte = hashlib.sha256()
                while True:
                    bloc = reponse.read(64 * 1024)
                    if not bloc:
                        break
                    f.write(bloc)
                    empreinte.update(bloc)
                    self.maj_progression["fait"] += len(bloc)
            if empreinte.hexdigest() != infos["sha256"]:
                raise RuntimeError("fichier téléchargé corrompu (empreinte différente)")
            shutil.rmtree(nouveau, ignore_errors=True)
            with zipfile.ZipFile(archive) as z:
                z.extractall(nouveau)
            # zipfile ne garde pas les droits d'exécution : on les remet sur l'outil natif
            outil = nouveau / "capture-systeme"
            if outil.exists():
                outil.chmod(0o755)
            version_zip = (nouveau / "VERSION").read_text().strip()
            if not (nouveau / "app.py").exists() or version_zip != infos["version"]:
                raise RuntimeError("archive incomplète")
            if (nouveau / "MOTEUR").read_text().strip() != moteur_local():
                raise RuntimeError("code prévu pour un autre moteur")
            # Rotation : actuel → precedent, nouveau → actuel (renommages instantanés)
            actuel, precedent = DOSSIER_CODES / "actuel", DOSSIER_CODES / "precedent"
            shutil.rmtree(precedent, ignore_errors=True)
            if actuel.exists():
                actuel.rename(precedent)
            nouveau.rename(actuel)
        except Exception as e:
            shutil.rmtree(nouveau, ignore_errors=True)
            self.maj_en_cours = False
            self.maj_progression = {"phase": "", "fait": 0, "total": 0}
            notifier("Mise à jour impossible pour l'instant", str(e))
            return
        finally:
            archive.unlink(missing_ok=True)

        self.maj_progression["phase"] = "redemarrage"
        CONFIG["version_precedente"] = VERSION
        sauver_config(CONFIG)
        time.sleep(1)   # laisse la fenêtre afficher « Redémarrage… »
        self.redemarrer_moteur(fenetre=not automatique)

    def redemarrer_moteur(self, fenetre=True):
        """Relance le moteur sur le nouveau code, dans le même processus (exec) :
        le lanceur natif ne s'arrête pas, l'app reste dans la barre des menus."""
        fenetre_ouverte = bool(self.fenetre and self.fenetre.poll() is None)
        try:
            if fenetre_ouverte:
                self.fenetre.terminate()
                self.fenetre.wait(timeout=3)
        except Exception:
            pass
        try:
            self.serveur.shutdown()
            self.serveur.server_close()
        except Exception:
            pass
        if fenetre and fenetre_ouverte:
            os.environ["MIRORECAP_FENETRE"] = "1"
        else:
            os.environ.pop("MIRORECAP_FENETRE", None)
        sys.stdout.flush()
        demarreur = CODE_APP / "demarrage.py"
        script = demarreur if demarreur.exists() else DOSSIER_CODE / "app.py"
        os.execv(sys.executable, [sys.executable, str(script)])

    def _installer_maj(self, automatique):
        dossier = DOSSIER_APP / "maj"
        dossier.mkdir(parents=True, exist_ok=True)
        dmg = dossier / "MiroRecap.dmg"
        try:
            # 1. Téléchargement suivi (barre de progression dans la fenêtre et la barre des menus)
            with urllib.request.urlopen(URL_DMG, timeout=60) as reponse, open(dmg, "wb") as fichier:
                self.maj_progression["total"] = int(reponse.headers.get("Content-Length") or 0)
                while True:
                    bloc = reponse.read(1 << 20)
                    if not bloc:
                        break
                    fichier.write(bloc)
                    self.maj_progression["fait"] += len(bloc)
            if dmg.stat().st_size < 50_000_000:
                raise RuntimeError("fichier téléchargé incomplet")
        except Exception as e:
            self.maj_en_cours = False
            self.maj_progression = {"phase": "", "fait": 0, "total": 0}
            dmg.unlink(missing_ok=True)
            notifier("Mise à jour impossible pour l'instant", str(e))
            return

        # 2. Installation, comme les apps Mac classiques : on lance l'installateur, puis
        #    l'app se ferme d'elle-même. L'installateur attend qu'elle soit vraiment
        #    arrêtée avant de remplacer quoi que ce soit, puis relance la nouvelle version.
        self.maj_progression["phase"] = "installation"
        CONFIG["version_precedente"] = VERSION   # pour annoncer la nouveauté au redémarrage
        sauver_config(CONFIG)
        pids = [os.getpid()]
        if os.environ.get("MIRORECAP_LANCEUR") == "1":
            pids.append(os.getppid())            # le lanceur natif
        if self.fenetre and self.fenetre.poll() is None:
            pids.append(self.fenetre.pid)
        env = dict(os.environ, MIRORECAP_DMG=str(dmg), MIRORECAP_PIDS=" ".join(map(str, pids)))
        if automatique:
            env["MIRORECAP_DISCRET"] = "1"   # relance sans ouvrir la fenêtre
        # Installateur livré avec l'app, copié hors du bundle (le bundle va être remplacé)
        embarque = DOSSIER_CODE / "install-distant.sh"
        if embarque.exists():
            installateur = dossier / "installer.sh"
            shutil.copy(embarque, installateur)
            commande = ["/bin/bash", str(installateur)]
        else:
            commande = ["/bin/bash", "-c", f'curl -fsSL "{URL_INSTALL}" | bash']
        journal = open(DOSSIER_APP / "maj.log", "w")
        # Session séparée : l'installateur survit à la fermeture de l'app
        subprocess.Popen(commande, stdin=subprocess.DEVNULL, stdout=journal, stderr=subprocess.STDOUT,
                         start_new_session=True, env=env)
        time.sleep(1.5)   # laisse la fenêtre afficher « Installation… »
        self.fermer_pour_maj()

    def fermer_pour_maj(self):
        """Ferme l'app proprement pour laisser l'installateur la remplacer."""
        try:
            if self.fenetre and self.fenetre.poll() is None:
                self.fenetre.terminate()
                self.fenetre.wait(timeout=3)
        except Exception:
            pass
        try:
            self.serveur.shutdown()
        except Exception:
            pass
        sys.stdout.flush()
        os._exit(0)   # le lanceur natif s'arrête avec nous

    def basculer_enregistrement(self):
        with self.verrou:
            if not self.enreg.actif:
                self.enreg.demarrer()
            else:
                chemin, debut, systeme = self.enreg.arreter()
                if systeme:
                    self.pistes_systeme[chemin] = systeme
                self.en_file.add(chemin)
                self.file_traitement.put((chemin, debut))

    # --- Barre de menus -------------------------------------------------------

    def tic(self, _):
        if self.fenetre_demandee:
            self.fenetre_demandee = False
            self.ouvrir_fenetre()

        if self.enreg.actif:
            m, s = divmod(int((dt.datetime.now() - self.enreg.debut).total_seconds()), 60)
            self.title = f"🔴 {m:02d}:{s:02d}"
            self.bouton.title = "Arrêter et analyser"
        else:
            if MODELE["etat"] == "telechargement":
                self.title = f"⬇ Whisper {texte_progression()}"
            elif self.statut:
                self.title = f"⏳ {self.statut}"
            else:
                self.title = "🎙"
            appel = getattr(self, "detecteur", None) and self.detecteur.appel
            self.bouton.title = (f"Enregistrer l'appel {appel['app']}" if appel and appel["app"]
                                 else "Enregistrer cet appel" if appel else "Démarrer l'enregistrement")

        if self.dernier:
            self.item_dernier.title = f"Dernier : {self.dernier['projet']} / {self.dernier['titre']}"

        if self.maj_en_cours:
            mp = self.maj_progression
            if mp["phase"] == "telechargement" and mp["total"]:
                pct = int(100 * mp["fait"] / mp["total"])
                self.item_maj.title = f"Téléchargement de la mise à jour… {pct} %"
                if not self.enreg.actif and not self.statut:
                    self.title = f"⬇ {pct} %"
            else:
                self.item_maj.title = "Installation de la mise à jour…"
        elif self.maj:
            self.item_maj.title = f"Mettre à jour vers la version {self.maj}"

        # Scan du dossier d'import toutes les 5 secondes
        self._compteur += 1
        if self._compteur % 5 == 0:
            self.scanner_import()

    def ouvrir_fenetre(self, vue=""):
        # Une seule fenêtre : si elle est déjà ouverte, on la relance au premier plan
        if self.fenetre and self.fenetre.poll() is None:
            self.fenetre.terminate()
        url = f"http://127.0.0.1:{self.serveur.server_port}/" + (f"#{vue}" if vue else "")
        # Python nommé « MiroRecap » (créé à la construction) : c'est ce nom qu'affiche le Dock
        python = Path(sys.executable).with_name("MiroRecap")
        if not python.exists():
            python = Path(sys.executable)
        self.fenetre = subprocess.Popen([str(python), str(DOSSIER_CODE / "fenetre.py"), url, JETON])

    def ouvrir_dernier(self, _):
        if self.dernier:
            ouvrir_finder(DOSSIER_SORTIE / self.dernier["projet"] / self.dernier["fichier"], reveler=True)

    def quitter_depuis_fenetre(self):
        """Cmd+Q dans la fenêtre : on quitte tout, sauf si un enregistrement ou une analyse tourne."""
        if self.enreg.actif:
            notifier("MiroRecap reste ouvert", "Un enregistrement est en cours : il continue dans la barre des menus.")
            return {"quitte": False}
        if self.statut or not self.file_traitement.empty():
            notifier("MiroRecap reste ouvert", "Une analyse est en cours : MiroRecap la termine dans la barre des menus.")
            return {"quitte": False}

        def fermer():
            time.sleep(0.4)   # laisse la fenêtre se fermer d'abord
            try:
                from PyObjCTools import AppHelper
                AppHelper.callAfter(rumps.quit_application)   # sur le fil principal
                time.sleep(3)
            except Exception:
                pass
            os._exit(0)   # au cas où l'arrêt normal n'aurait pas abouti
        threading.Thread(target=fermer, daemon=True).start()
        return {"quitte": True}

    def quitter(self, _):
        if self.fenetre and self.fenetre.poll() is None:
            self.fenetre.terminate()
        rumps.quit_application()

    # --- Import automatique --------------------------------------------------

    def scanner_import(self):
        """Met en file les audios déposés dans _A-traiter une fois leur copie
        terminée (taille identique entre deux scans)."""
        for f in DOSSIER_IMPORT.iterdir():
            # Fichier iCloud pas encore téléchargé ('.nom.m4a.icloud') : on force le téléchargement
            if f.name.startswith(".") and f.name.endswith(".icloud"):
                reel = f.with_name(f.name[1:-len(".icloud")])
                if reel not in self.icloud_demandes:
                    self.icloud_demandes.add(reel)
                    subprocess.run(["brctl", "download", str(reel)], check=False)
                continue
            if f.name.startswith(".") or f.suffix.lower() not in EXTENSIONS_AUDIO or f in self.en_file:
                continue
            infos = f.stat()
            if infos.st_size > 0 and self.tailles.get(f) == infos.st_size:
                self.en_file.add(f)
                self.icloud_demandes.discard(f)
                date = dt.datetime.fromtimestamp(getattr(infos, "st_birthtime", infos.st_mtime))
                self.file_traitement.put((f, date))
            else:
                self.tailles[f] = infos.st_size

    # --- Traitement (thread de fond) ------------------------------------------

    def traiter_amelioration(self, tache):
        TRAITEMENT.update(etape="preparation", progression=None, duree_audio=0, amelioration=tache["fichier"],
                          debut=dt.datetime.now().isoformat(timespec="seconds"), date="")
        self.statut = "Amélioration…"
        try:
            r = ameliorer_compte_rendu(tache["projet"], tache["fichier"], tache["mode"])
            self.dernier = {"projet": r["projet"], "fichier": r["fichier"], "titre": r["titre"]}
            self.derniere_amelioration = {"ok": True, **r}
            notifier("Compte rendu amélioré", r["titre"])
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.derniere_amelioration = {"ok": False, "erreur": str(e), "fichier": tache["fichier"]}
            notifier("Amélioration impossible", str(e))
        finally:
            TRAITEMENT.update(etape="", progression=None, amelioration="")
            self.statut = ""

    def demander_amelioration(self, projet, fichier, mode):
        if mode not in ("corriger", "pro"):
            raise ValueError("mode inconnu")
        chemin_compte_rendu(projet, fichier)   # vérifie que le fichier existe
        if not moteur_effectif():
            raise ValueError("aucun moteur d'analyse prêt (Réglages → Analyse par l'IA)")
        self.file_traitement.put({"projet": projet, "fichier": fichier, "mode": mode})
        return {"ok": True, "en_attente": self.file_traitement.qsize()}

    def travailleur(self):
        while True:
            tache = self.file_traitement.get()
            if isinstance(tache, dict):          # amélioration d'un compte rendu existant
                self.traiter_amelioration(tache)
                continue
            chemin, date = tache
            transcription = None
            TRAITEMENT.update(etape="preparation", progression=None, duree_audio=0,
                              debut=dt.datetime.now().isoformat(timespec="seconds"),
                              date=date.isoformat(timespec="minutes"))
            try:
                systeme = self.pistes_systeme.pop(chemin, None)
                if systeme:
                    self.statut = "Mixage…"
                    try:
                        mixer_pistes(chemin, systeme)
                    except Exception as e:
                        print(f"Mixage impossible, micro seul : {e}", flush=True)
                    finally:
                        systeme.unlink(missing_ok=True)
                if MODELE["etat"] != "pret":
                    self.statut = "Téléchargement de Whisper…"
                    TRAITEMENT.update(etape="modele", progression=None)
                    assurer_modele()
                self.statut = "Transcription…"
                TRAITEMENT.update(etape="transcription", progression=0.0)
                transcription = transcrire(chemin)
                if not transcription:
                    raise ValueError("aucune parole détectée")
                self.statut = "Analyse…"
                TRAITEMENT.update(etape="analyse", progression=None)
                analyse = analyser(transcription, date)
                md = ranger(chemin, date, transcription, analyse)
                self.dernier = {"projet": analyse["projet"], "fichier": md.name, "titre": analyse["titre"]}
                nouveau = ajouter_proposition(analyse, md.name) if analyse["projet"] == PROJET_PAR_DEFAUT else None
                if nouveau:
                    notifier(f"Nouveau client : {nouveau}", "Ouvre MiroRecap pour créer son projet en un clic.")
                else:
                    notifier(f"Classé dans {analyse['projet']}", analyse["titre"])
                try:
                    mettre_a_jour_fiche(analyse["projet"], analyse, date, md)
                    if analyse["projet"] != PROJET_PAR_DEFAUT:
                        lancer_synthese(analyse["projet"], silencieux=True)   # point d'étape toujours à jour
                except Exception as e:
                    print(f"Fiche {analyse['projet']} : {e}", flush=True)
            except Exception as e:
                # Erreur complète dans app.log + gardée pour l'afficher dans les Réglages
                import traceback
                print(f"[{dt.datetime.now():%Y-%m-%d %H:%M:%S}] Erreur sur {chemin.name} :", flush=True)
                traceback.print_exc()
                sys.stdout.flush()
                self.derniere_erreur = f"{dt.datetime.now():%d/%m %H:%M} · {e}"
                # Rien n'est perdu : l'audio (et la transcription si dispo) part dans _Erreurs
                destination = chemin_libre(DOSSIER_ERREURS / chemin.name)
                if chemin.exists():
                    shutil.move(str(chemin), str(destination))
                if transcription:
                    destination.with_suffix(".txt").write_text(transcription, encoding="utf-8")
                notifier("Erreur de traitement", f"{chemin.name} : {e}")
            finally:
                TRAITEMENT.update(etape="", progression=None)
                self.en_file.discard(chemin)
                self.tailles.pop(chemin, None)
                self.statut = ""


def preparer_installation():
    """Lancement au démarrage du Mac + nettoyage des anciennes versions installées par script."""
    bundle = CODE_APP.parent.parent.parent              # …/MiroRecap.app
    if bundle.suffix != ".app":
        return
    agent = Path.home() / "Library" / "LaunchAgents" / "fr.mirorecap.app.plist"
    try:
        agent.parent.mkdir(parents=True, exist_ok=True)
        agent.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>fr.mirorecap.app</string>
  <key>ProgramArguments</key>
  <array><string>/usr/bin/open</string><string>-a</string><string>{bundle}</string><string>--args</string><string>--demarrage</string></array>
  <key>RunAtLoad</key><true/>
</dict>
</plist>
""")
    except Exception:
        pass
    # Anciennes versions (installateur en Terminal) : app dans ~/Applications et Python à part.
    # La config (clé API, réglages) est conservée.
    ancienne = Path.home() / "Applications" / "MiroRecap.app"
    if ancienne.exists() and ancienne.resolve() != bundle.resolve():
        shutil.rmtree(ancienne, ignore_errors=True)
    for nom in (".venv", "ui"):
        shutil.rmtree(DOSSIER_APP / nom, ignore_errors=True)
    for nom in ("app.py", "fenetre.py", "requirements.txt", "VERSION", "icone.png"):
        (DOSSIER_APP / nom).unlink(missing_ok=True)


if __name__ == "__main__":
    preparer_installation()
    # Pas d'icône dans le Dock pour ce processus : il vit dans la barre de menus
    try:
        from AppKit import NSApplication
        NSApplication.sharedApplication().setActivationPolicy_(1)  # 1 = Accessory
    except Exception:
        pass
    APP = App()
    APP.run()
