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
    {"id": "mlx-community/whisper-large-v3-turbo", "nom": "Turbo", "detail": "Précis et rapide. Recommandé."},
    {"id": "mlx-community/whisper-large-v3-mlx", "nom": "Large v3", "detail": "Le plus précis, environ deux fois plus lent."},
    {"id": "mlx-community/whisper-small-mlx", "nom": "Small", "detail": "Très rapide, moins fiable sur les noms et les chiffres."},
]

CONFIG_DEFAUT = {
    "cle_api_anthropic": "",
    # Vide = iCloud Drive/Comptes-rendus si iCloud Drive est actif, sinon ~/Comptes-rendus
    "dossier_sortie": "",
    "modele_whisper": MODELES_WHISPER[0]["id"],
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


def derniere_version():
    """Version publiée sur GitHub, ou None si pas de réseau."""
    try:
        requete = urllib.request.Request(URL_VERSION, headers={"Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(requete, timeout=10) as r:
            release = json.loads(r.read().decode())
        # On ne propose la release que si son DMG est déjà construit
        if not any(a.get("name") == "MiroRecap.dmg" for a in release.get("assets", [])):
            return None
        return release.get("tag_name", "").lstrip("v") or None
    except Exception:
        return None


def ouvrir_finder(chemin, reveler=False):
    subprocess.run(["open", "-R", str(chemin)] if reveler else ["open", str(chemin)], check=False)


# ---------------------------------------------------------------------------
# Enregistrement micro
# ---------------------------------------------------------------------------

CAPTURE_SYSTEME = DOSSIER_CODE.parent.parent / "MacOS" / "capture-systeme"
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


def transcrire(chemin_audio):
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

    resultat = mlx_whisper.transcribe(
        audio,
        path_or_hf_repo=str(assurer_modele()),
        language=CONFIG["langue"],
    )
    lignes = []
    for seg in resultat.get("segments", []):
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
SESSION_CONNEXION = {"maitre": None, "proc": None}  # terminal de « claude setup-token » en cours


def connecter_claude_code():
    """Installe Claude Code si besoin, puis lance « claude setup-token » : le navigateur
    s'ouvre, l'utilisateur se connecte avec son compte Claude, et MiroRecap récupère le
    jeton (valable un an) sans passer par le Terminal."""
    import fcntl
    import pty
    import select
    import struct
    import termios

    if CONNEXION["etat"] in ("installation", "navigateur"):
        return  # déjà en cours

    def tache():
        try:
            claude = trouver_claude_code()
            if not claude:
                CONNEXION.update(etat="installation", message="Installation de Claude Code…")
                subprocess.run(["/bin/bash", "-c", "curl -fsSL https://claude.ai/install.sh | bash"],
                               capture_output=True, text=True, timeout=900)
                claude = trouver_claude_code()
                if not claude:
                    raise RuntimeError("l'installation de Claude Code a échoué")

            CONNEXION.update(etat="navigateur", url="",
                             message="Connecte-toi dans le navigateur, puis colle ici le code affiché.")
            # setup-token veut un vrai terminal : on lui en donne un, large pour que le jeton ne soit pas coupé
            maitre, esclave = pty.openpty()
            fcntl.ioctl(esclave, termios.TIOCSWINSZ, struct.pack("HHHH", 50, 1000, 0, 0))
            proc = subprocess.Popen([claude, "setup-token"], stdin=esclave, stdout=esclave, stderr=esclave,
                                    env=env_claude_code(), close_fds=True, start_new_session=True)
            os.close(esclave)
            SESSION_CONNEXION.update(maitre=maitre, proc=proc)
            sortie, fin = b"", time.time() + 900
            while time.time() < fin:
                pret, _, _ = select.select([maitre], [], [], 1)
                if pret:
                    try:
                        bloc = os.read(maitre, 4096)
                    except OSError:
                        break
                    if not bloc:
                        break
                    sortie += bloc
                    texte = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", sortie.decode(errors="ignore"))
                    # Lien de connexion, au cas où le navigateur ne s'est pas ouvert tout seul
                    lien = re.search(r"https://\S*oauth/authorize\S+", texte)
                    if lien and not CONNEXION["url"]:
                        CONNEXION["url"] = lien.group(0)
                    # Code refusé : Claude Code propose « Press Enter to retry ». On relance
                    # (nouvelle page de connexion) et on repart de zéro pour la suite.
                    if "Press Enter to retry" in texte:
                        os.write(maitre, b"\r")
                        sortie = b""
                        CONNEXION.update(url="", message="Code refusé ou expiré. Reconnecte-toi dans la page "
                                                         "qui vient de s'ouvrir et colle le nouveau code.")
                        continue
                    jeton = re.search(r"sk-ant-oat[0-9A-Za-z_\-]+", texte)
                    if jeton:
                        CONFIG["jeton_claude_code"] = jeton.group(0)
                        sauver_config(CONFIG)
                        FICHIER_CONFIG.chmod(0o600)
                        CONNEXION.update(etat="ok", message="Connecté à ton abonnement Claude.")
                        notifier("Abonnement Claude connecté", "MiroRecap utilise maintenant ton abonnement.")
                        break
                elif proc.poll() is not None:
                    break
            proc.terminate()
            SESSION_CONNEXION.update(maitre=None, proc=None)
            os.close(maitre)
            if CONNEXION["etat"] == "annule":
                CONNEXION.update(etat="", message="", url="")
                return
            if CONNEXION["etat"] != "ok":
                raise RuntimeError("connexion non terminée (fenêtre du navigateur fermée ou délai dépassé)")
        except Exception as e:
            print(f"Connexion Claude Code : {e}", flush=True)
            CONNEXION.update(etat="echec", message=f"Échec : {e}")

    threading.Thread(target=tache, daemon=True).start()


def envoyer_code_claude(code):
    """Transmet à « claude setup-token » le code affiché par le navigateur après la connexion."""
    code = (code or "").strip()
    maitre = SESSION_CONNEXION["maitre"]
    if not code:
        raise ValueError("colle le code affiché dans le navigateur")
    if maitre is None:
        raise ValueError("aucune connexion en cours, clique d'abord sur Se connecter")
    os.write(maitre, code.encode() + b"\r")
    CONNEXION["message"] = "Vérification du code…"


def annuler_connexion_claude():
    proc = SESSION_CONNEXION["proc"]
    if proc and proc.poll() is None:
        CONNEXION["etat"] = "annule"
        proc.terminate()
    else:
        CONNEXION.update(etat="", message="", url="")


def deconnecter_claude_code():
    CONFIG.pop("jeton_claude_code", None)
    sauver_config(CONFIG)
    CONNEXION.update(etat="", message="")


def moteur_effectif():
    """Moteur réellement utilisé : 'abonnement', 'api', ou None si aucun n'est prêt."""
    choix = CONFIG.get("moteur", "auto")
    claude = trouver_claude_code()
    cle = CONFIG.get("cle_api_anthropic")
    connecte = bool(claude and CONFIG.get("jeton_claude_code"))
    if choix == "abonnement":
        return "abonnement" if claude else None
    if choix == "api":
        return "api" if cle else None
    if connecte:
        return "abonnement"
    return "api" if cle else ("abonnement" if claude else None)


def instructions_analyse(noms, projets):
    liste = "\n".join(f"- {nom}" + (f" : {desc}" if desc else "") for nom, desc in projets.items())
    return f"""Tu analyses des transcriptions automatiques d'appels et de rendez-vous pro (elles peuvent contenir des erreurs de reconnaissance).

Projets existants :
{liste or "(aucun projet créé pour l'instant)"}
- {PROJET_PAR_DEFAUT} : rien ne correspond clairement

Choisis le projet dont parle l'enregistrement, en t'appuyant sur les noms de clients, d'entreprises, de produits et les sujets évoqués. En cas de doute, prends "{PROJET_PAR_DEFAUT}".

Rédige le compte rendu en markdown, en français, sans titre principal, avec ces sections (omets celles qui seraient vides) :
## Contexte
## Points clés
## Décisions
## Actions à faire
(qui / quoi / deadline)
## Chiffres
(budget, prix, délais)
## Points flous à clarifier
## Mail de suivi
(court, vouvoiement)

N'invente rien : si une info est incertaine ou mal transcrite, signale-le.

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


def analyser(transcription, date):
    """Rédige le compte rendu et choisit le projet. Retourne {projet, titre, compte_rendu}."""
    moteur = moteur_effectif()
    if not moteur:
        raise RuntimeError("aucun moteur d'analyse prêt : connecte ton abonnement Claude ou ajoute une clé API (Réglages)")

    projets = lister_projets()
    noms = list(projets.keys()) + [PROJET_PAR_DEFAUT]
    systeme = instructions_analyse(noms, projets)
    contenu = f"Date de l'enregistrement : {date:%d/%m/%Y %H:%M}\n\nTranscription :\n{transcription}"

    if moteur == "abonnement":
        try:
            donnees = analyser_abonnement(systeme, contenu)
        except Exception as e:
            # Mode Automatique : si l'abonnement échoue (pas connecté, limite atteinte…)
            # et qu'une clé API existe, on passe par l'API plutôt que d'échouer
            if CONFIG.get("moteur", "auto") == "auto" and CONFIG.get("cle_api_anthropic"):
                print(f"Abonnement indisponible ({e}), analyse via la clé API.", flush=True)
                donnees = analyser_api(systeme, contenu, noms)
            else:
                raise
    else:
        donnees = analyser_api(systeme, contenu, noms)
    if donnees.get("projet") not in noms:
        donnees["projet"] = PROJET_PAR_DEFAUT
    for cle in ("titre", "compte_rendu"):
        if not str(donnees.get(cle, "")).strip():
            raise RuntimeError(f"réponse de Claude incomplète ({cle} manquant)")
    return donnees


def analyser_abonnement(systeme, contenu):
    """Analyse via Claude Code en mode non interactif, avec le compte Claude (Pro/Max) de l'utilisateur."""
    import tempfile

    claude = trouver_claude_code()
    consigne = (
        systeme
        + "\n\nRéponds UNIQUEMENT par un objet JSON valide, sans texte avant ni après, de la forme :\n"
        + '{"projet": "<nom exact d\'un des projets listés>", "titre": "<titre court, 3 à 8 mots>", '
        + '"compte_rendu": "<compte rendu en markdown>", "contexte_claude": "<bloc <appel>…</appel>>"}\n\n'
        + contenu
    )
    # Dossier vide : Claude Code n'a aucun fichier à lire, il se contente de répondre
    with tempfile.TemporaryDirectory() as dossier_vide:
        r = subprocess.run(
            [claude, "-p", "Suis les instructions reçues en entrée et réponds uniquement en JSON.",
             "--output-format", "json", "--model", CONFIG.get("modele_abonnement", "sonnet"),
             "--max-turns", "1"],
            input=consigne, capture_output=True, text=True, timeout=900, cwd=dossier_vide,
            env=env_claude_code(),
        )
    sortie = r.stdout.strip()
    if r.returncode != 0 and not sortie:
        detail = (r.stderr.strip().splitlines() or ["erreur inconnue"])[-1]
        raise RuntimeError(f"Claude Code : {detail} (es-tu bien connecté ? Réglages → Se connecter)")

    # Sortie JSON de Claude Code : la réponse du modèle est dans "result"
    texte = sortie
    try:
        enveloppe = json.loads(sortie)
        if isinstance(enveloppe, dict):
            if enveloppe.get("is_error"):
                resultat = enveloppe.get("result") or "erreur"
                if "login" in resultat.lower():
                    resultat = "pas connecté à ton compte Claude (Réglages → Se connecter, puis /login)"
                raise RuntimeError(f"Claude Code : {resultat}")
            texte = enveloppe.get("result", "") or ""
    except json.JSONDecodeError:
        pass

    # On extrait l'objet JSON de la réponse (au cas où il serait entouré de texte ou de ```)
    debut, fin = texte.find("{"), texte.rfind("}")
    if debut == -1 or fin <= debut:
        raise RuntimeError("Claude n'a pas renvoyé de JSON exploitable")
    return json.loads(texte[debut:fin + 1])


def analyser_api(systeme, contenu, noms):
    """Analyse via l'API Anthropic (clé API, facturée à l'usage)."""
    import anthropic

    # Outil forcé = Claude renvoie forcément un JSON propre (projet, titre, compte rendu)
    outil = {
        "name": "classer_compte_rendu",
        "description": "Enregistre le compte rendu dans le bon projet.",
        "input_schema": {
            "type": "object",
            "properties": {
                "projet": {"type": "string", "enum": noms},
                "titre": {"type": "string", "description": "Titre court et précis, 3 à 8 mots"},
                "compte_rendu": {"type": "string", "description": "Compte rendu en markdown"},
                "contexte_claude": {"type": "string", "description": "Bloc <appel>…</appel> dense, à coller dans Claude"},
            },
            "required": ["projet", "titre", "compte_rendu", "contexte_claude"],
        },
    }
    client = anthropic.Anthropic(api_key=CONFIG["cle_api_anthropic"])
    reponse = client.messages.create(
        model=CONFIG["modele_claude"],
        max_tokens=4096,
        system=systeme,
        tools=[outil],
        tool_choice={"type": "tool", "name": "classer_compte_rendu"},
        messages=[{"role": "user", "content": contenu}],
    )
    return next(b for b in reponse.content if b.type == "tool_use").input


# ---------------------------------------------------------------------------
# Rangement
# ---------------------------------------------------------------------------

def ranger(chemin_audio, date, transcription, analyse):
    dossier = DOSSIER_SORTIE / analyse["projet"]
    (dossier / "audio").mkdir(parents=True, exist_ok=True)

    base = f"{date:%Y-%m-%d_%Hh%M}_{slugifier(analyse['titre'])}"
    audio_final = chemin_libre(dossier / "audio" / f"{base}{chemin_audio.suffix.lower()}")
    shutil.move(str(chemin_audio), str(audio_final))

    md = chemin_libre(dossier / f"{base}.md")
    md.write_text(
        f"# {analyse['titre']}\n\n"
        f"- **Date** : {date:%d/%m/%Y à %H:%M}\n"
        f"- **Projet** : {analyse['projet']}\n"
        f"- **Audio** : [audio/{audio_final.name}](audio/{audio_final.name})\n\n"
        f"{analyse['compte_rendu'].strip()}\n\n"
        + (f"## Contexte pour Claude\n\n```xml\n{analyse['contexte_claude'].strip()}\n```\n\n" if analyse.get("contexte_claude") else "")
        + f"---\n\n## Transcription complète\n\n{transcription}\n",
        encoding="utf-8",
    )
    return md


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


def lire_compte_rendu(projet, fichier):
    if not fichier.endswith(".md") or "/" in fichier:
        raise ValueError("fichier invalide")
    chemin = dossier_projet(projet) / fichier
    return {"markdown": chemin.read_text(encoding="utf-8")}


def compter_cr(dossier):
    return sum(1 for f in dossier.glob("*.md") if not f.name.startswith("_"))


def section_markdown(texte, titre):
    """Contenu d'une section « ## titre » (jusqu'à la section suivante ou au ---)."""
    m = re.search(rf"^##\s+{re.escape(titre)}\s*$(.*?)(?=^##\s|^---\s*$|\Z)", texte, re.M | re.S)
    return m.group(1).strip() if m else ""


def appeler_claude_texte(systeme, contenu):
    """Demande une réponse texte à Claude (abonnement ou clé API)."""
    import tempfile

    moteur = moteur_effectif()
    if moteur == "abonnement":
        with tempfile.TemporaryDirectory() as dossier_vide:
            r = subprocess.run(
                [trouver_claude_code(), "-p", "Suis les instructions reçues en entrée.",
                 "--output-format", "json", "--model", CONFIG.get("modele_abonnement", "sonnet"), "--max-turns", "1"],
                input=systeme + "\n\n" + contenu, capture_output=True, text=True, timeout=900,
                cwd=dossier_vide, env=env_claude_code(),
            )
        try:
            enveloppe = json.loads(r.stdout)
        except json.JSONDecodeError:
            raise RuntimeError(f"Claude Code : {(r.stderr.strip().splitlines() or ['erreur'])[-1]}")
        if enveloppe.get("is_error"):
            raise RuntimeError(f"Claude Code : {enveloppe.get('result') or 'erreur'}")
        return enveloppe.get("result", "")
    if moteur == "api":
        import anthropic
        client = anthropic.Anthropic(api_key=CONFIG["cle_api_anthropic"])
        reponse = client.messages.create(model=CONFIG["modele_claude"], max_tokens=4096, system=systeme,
                                         messages=[{"role": "user", "content": contenu}])
        return "".join(b.text for b in reponse.content if b.type == "text")
    raise RuntimeError("aucun moteur d'analyse prêt (Réglages → Analyse par Claude)")


SYNTHESES = {}   # état des synthèses en cours, par projet


def lancer_synthese(nom):
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
            synthese = appeler_claude_texte(systeme, contenu).strip()
            (dossier / "_synthese.md").write_text(
                f"<!-- {dt.datetime.now():%Y-%m-%dT%H:%M} · {len(fichiers)} comptes rendus -->\n{synthese}\n", encoding="utf-8")
            SYNTHESES[nom] = "ok"
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
    return {
        "nom": nom, "description": desc, "modifiable": nom != PROJET_PAR_DEFAUT,
        "comptes_rendus": cr, "actions": actions[:40],
        "synthese": synthese, "date_synthese": date_synthese,
        "synthese_etat": SYNTHESES.get(nom, ""),
        "synthese_perimee": bool(synthese) and bool(cr) and cr[0]["date"] > date_synthese,
    }


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
        resultat.append({"nom": nom, "description": desc, "nb": compter_cr(DOSSIER_SORTIE / nom), "modifiable": True})
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
    }


def sauver_reglages(donnees):
    redemarrage = False
    if donnees.get("cle", "").strip():
        CONFIG["cle_api_anthropic"] = donnees["cle"].strip()
    if donnees.get("moteur") in ("auto", "abonnement", "api"):
        CONFIG["moteur"] = donnees["moteur"]
    if donnees.get("modele_whisper") in {m["id"] for m in MODELES_WHISPER}:
        change = donnees["modele_whisper"] != CONFIG["modele_whisper"]
        CONFIG["modele_whisper"] = donnees["modele_whisper"]
        if change:
            sauver_config(CONFIG)
            preparer_modele_en_fond()   # télécharge tout de suite, pas au prochain appel
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
        self.maj_progression = {"phase": "", "fait": 0, "total": 0}   # téléchargement du DMG
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
        # Juste après une mise à jour : on l'annonce
        precedente = CONFIG.pop("version_precedente", None)
        if precedente and precedente != VERSION:
            sauver_config(CONFIG)
            notifier(f"MiroRecap {VERSION} est installé", "Mise à jour terminée, tout est prêt.")
        # Premier lancement : le modèle Whisper se télécharge tout de suite, en fond
        preparer_modele_en_fond()
        # L'UI de la barre de menus ne doit être modifiée que depuis le thread principal
        self.timer = rumps.Timer(self.tic, 1)
        self.timer.start()

        # Clic sur l'icône de l'app alors qu'elle tourne : le lanceur envoie SIGUSR1
        import signal
        self.fenetre_demandee = False
        signal.signal(signal.SIGUSR1, lambda *_: setattr(self, "fenetre_demandee", True))

        # Premier lancement (rien de configuré) : réglages ; ouverture par l'utilisateur : fenêtre
        if moteur_effectif() is None:
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
            "son_systeme": SON_SYSTEME,
        }

    # --- Mises à jour ---------------------------------------------------------

    def verifier_maj_maintenant(self):
        """Compare la version locale à celle de GitHub. Notifie une seule fois par nouvelle version."""
        v = derniere_version()
        if v and version_tuple(v) > version_tuple(VERSION):
            if self.maj != v:
                if not CONFIG.get("maj_auto", True):
                    notifier("Mise à jour disponible", f"MiroRecap {v} est sortie. 🎙 → Mettre à jour")
            self.maj = v
        return self.maj

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
        threading.Thread(target=self._installer_maj, args=(automatique,), daemon=True).start()

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

        # 2. Installation : l'installateur arrête l'app, la remplace et la relance
        self.maj_progression["phase"] = "installation"
        CONFIG["version_precedente"] = VERSION   # pour annoncer la nouveauté au redémarrage
        sauver_config(CONFIG)
        env = dict(os.environ, MIRORECAP_DMG=str(dmg))
        if automatique:
            env["MIRORECAP_DISCRET"] = "1"   # relance sans ouvrir la fenêtre
        journal = open(DOSSIER_APP / "maj.log", "w")
        # Session séparée : l'installateur survit à l'arrêt de l'app.
        # Si l'installation échoue, on relance au moins l'app existante.
        subprocess.Popen(
            ["/bin/bash", "-c", f'curl -fsSL "{URL_INSTALL}" | bash || open -a MiroRecap'],
            stdin=subprocess.DEVNULL, stdout=journal, stderr=subprocess.STDOUT,
            start_new_session=True, env=env,
        )

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
            self.bouton.title = "Démarrer l'enregistrement"

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
        self.fenetre = subprocess.Popen([str(python), str(DOSSIER_CODE / "fenetre.py"), url])

    def ouvrir_dernier(self, _):
        if self.dernier:
            ouvrir_finder(DOSSIER_SORTIE / self.dernier["projet"] / self.dernier["fichier"], reveler=True)

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

    def travailleur(self):
        while True:
            chemin, date = self.file_traitement.get()
            transcription = None
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
                    assurer_modele()
                self.statut = "Transcription…"
                transcription = transcrire(chemin)
                if not transcription:
                    raise ValueError("aucune parole détectée")
                self.statut = "Analyse…"
                analyse = analyser(transcription, date)
                md = ranger(chemin, date, transcription, analyse)
                self.dernier = {"projet": analyse["projet"], "fichier": md.name, "titre": analyse["titre"]}
                notifier(f"Classé dans {analyse['projet']}", analyse["titre"])
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
                self.en_file.discard(chemin)
                self.tailles.pop(chemin, None)
                self.statut = ""


def preparer_installation():
    """Lancement au démarrage du Mac + nettoyage des anciennes versions installées par script."""
    bundle = DOSSIER_CODE.parent.parent.parent          # …/MiroRecap.app
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
