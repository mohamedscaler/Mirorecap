# -*- coding: utf-8 -*-
"""Windows : dossiers, notifications, Explorateur, Corbeille, détection des appels, démarrage.
Mêmes noms de fonctions que systeme_macos.py. Rien d'autre que la bibliothèque standard
(ctypes, winreg) : aucune dépendance en plus dans le moteur."""
import os
import subprocess
import sys
import threading
from pathlib import Path

DOSSIER_APP = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")) / "MiroRecap"
CONFIG_CLAUDE_DESKTOP = Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming")) / "Claude" / "claude_desktop_config.json"
NOM_GESTIONNAIRE = "l'Explorateur"
RAPPELS = False                 # pas d'app Rappels sur Windows : le bouton est masqué
ID_APP = "fr.mirorecap.app"     # AppUserModelID : regroupe les fenêtres dans la barre des tâches

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes
    import winreg


def _dossier_documents():
    """Dossier Documents réel (déplacé vers OneDrive ou un autre disque le cas échéant)."""
    try:
        # FOLDERID_Documents
        guid = (ctypes.c_ubyte * 16).from_buffer_copy(
            bytes.fromhex("D0 9A D3 FD 8F 23 AF 46 AD B4 6C 85 48 03 69 C7".replace(" ", "")))
        chemin = ctypes.c_wchar_p()
        if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(chemin)) == 0:
            resultat = chemin.value
            ctypes.windll.ole32.CoTaskMemFree(chemin)
            if resultat:
                return Path(resultat)
    except Exception:
        pass
    return Path.home() / "Documents"


def dossier_sortie_defaut():
    """Documents\\Comptes-rendus (synchronisé avec OneDrive si Documents y est sauvegardé)."""
    return _dossier_documents() / "Comptes-rendus"


# --- Notifications -------------------------------------------------------------
# Affichées par l'icône de la zone de notification (menus_windows.py la branche ici) :
# elles portent le nom et le logo de MiroRecap.

_ICONE = None


def brancher_icone(icone):
    global _ICONE
    _ICONE = icone


def notifier(titre, message):
    try:
        if _ICONE is not None:
            _ICONE.notify(str(message)[:255], str(titre)[:63])
            return
    except Exception:
        pass
    print(f"Notification : {titre} · {message}", flush=True)


# --- Fichiers ------------------------------------------------------------------------

def ouvrir(cible, reveler=False):
    """Ouvre un dossier, un fichier ou une adresse (https:, mailto:) ; reveler : le sélectionne dans l'Explorateur."""
    try:
        if reveler:
            subprocess.Popen(["explorer", "/select,", str(cible)])
        else:
            os.startfile(str(cible))
    except Exception as e:
        print(f"Ouverture de {cible} : {e}", flush=True)


def mettre_a_la_corbeille(chemin, chemin_libre):
    """Envoie un fichier dans la Corbeille (récupérable), sinon le supprime."""
    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", wintypes.LPCWSTR),
                    ("pTo", wintypes.LPCWSTR), ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", wintypes.BOOL),
                    ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]

    try:
        FO_DELETE, FOF_ALLOWUNDO, FOF_NOCONFIRMATION, FOF_SILENT, FOF_NOERRORUI = 3, 0x40, 0x10, 0x4, 0x400
        op = SHFILEOPSTRUCTW(None, FO_DELETE, str(Path(chemin).resolve()) + "\0", None,
                              FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI, False, None, None)
        if ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op)) == 0 and not Path(chemin).exists():
            return
    except Exception as e:
        print(f"Corbeille : {e}", flush=True)
    Path(chemin).unlink(missing_ok=True)


def telecharger_depuis_le_cloud(chemin_icloud):
    """Pas de fichiers iCloud en attente sur Windows (OneDrive télécharge à l'ouverture)."""
    return None


def date_creation(infos):
    return getattr(infos, "st_birthtime", None) or infos.st_ctime   # st_ctime = création sur Windows


# --- Rappels -----------------------------------------------------------------------

def ajouter_rappels(liste, note, actions):
    raise ValueError("l'app Rappels n'existe pas sur Windows : copie les actions depuis le compte rendu")


# --- Détection des appels ------------------------------------------------------------
# Windows note dans le registre quelles apps utilisent le micro (icône de micro de la barre
# des tâches) : LastUsedTimeStop = 0 tant que l'app l'utilise.

APPS_VISIO = {
    "zoom": "Zoom", "ms-teams": "Teams", "teams": "Teams", "msteams": "Teams", "discord": "Discord",
    "slack": "Slack", "whatsapp": "WhatsApp", "webex": "Webex", "ciscocollabhost": "Webex", "skype": "Skype",
    "telegram": "Telegram", "signal": "Signal", "chrome": "Chrome", "msedge": "Edge", "firefox": "Firefox",
    "brave": "Brave", "arc": "Arc", "opera": "Opera",
}
NAVIGATEURS = {"Chrome", "Edge", "Firefox", "Brave", "Arc", "Opera"}
CLE_MICRO = r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone"


def _cle_active(cle):
    try:
        fin, _ = winreg.QueryValueEx(cle, "LastUsedTimeStop")
        debut, _ = winreg.QueryValueEx(cle, "LastUsedTimeStart")
        return fin == 0 and debut > 0
    except OSError:
        return False


def _sous_cles(cle):
    i = 0
    while True:
        try:
            yield winreg.EnumKey(cle, i)
        except OSError:
            return
        i += 1


def apps_utilisant_le_micro():
    """Noms (exécutable ou paquet) des apps qui utilisent le micro en ce moment, sauf MiroRecap."""
    nous = {Path(sys.executable).name.lower(), "python.exe", "pythonw.exe", "mirorecap.exe"}
    trouvees = []
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, CLE_MICRO) as racine:
            for nom in list(_sous_cles(racine)):
                with winreg.OpenKey(racine, nom) as cle:
                    if nom == "NonPackaged":
                        for exe in list(_sous_cles(cle)):
                            with winreg.OpenKey(cle, exe) as sous:
                                if _cle_active(sous):
                                    fichier = exe.replace("#", "\\").rsplit("\\", 1)[-1].lower()
                                    if fichier not in nous:
                                        trouvees.append(fichier)
                    elif _cle_active(cle):
                        trouvees.append(nom.lower())
    except OSError:
        pass
    return trouvees


def micro_utilise_ailleurs():
    """True si une autre app utilise le micro (Teams, Zoom, Meet dans le navigateur…)."""
    return bool(apps_utilisant_le_micro())


def _nom_visio(app):
    base = app.split("_")[0].split(".")[-1] if not app.endswith(".exe") else app[:-4]
    for cle, nom in APPS_VISIO.items():
        if base == cle or cle in app.replace(" ", ""):
            return nom
    return None


def app_de_visio():
    """Nom de l'app de visio qui utilise le micro (une app dédiée plutôt qu'un navigateur), sinon None."""
    noms = [n for n in (_nom_visio(a) for a in apps_utilisant_le_micro()) if n]
    dediees = [n for n in noms if n not in NAVIGATEURS]
    return dediees[0] if dediees else None


# --- Processus de l'app ------------------------------------------------------------------

def python_fenetre():
    """MiroRecap.exe (copie de pythonw.exe avec l'icône) : c'est ce nom et ce logo qu'affiche Windows."""
    return Path(sys.executable)


def python_moteur(code_app):
    """Python de l'app (avec console : le connecteur MCP parle par stdin/stdout)."""
    candidat = code_app.parent / "python" / "python.exe"
    return candidat if candidat.exists() else Path(sys.executable)


def identifier_processus():
    """Logo et nom de MiroRecap dans la barre des tâches (et non ceux de Python)."""
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(ID_APP)
    except Exception:
        pass


EVENEMENT_OUVRIR = "Local\\MiroRecapOuvrir"
MUTEX_INSTANCE = "Local\\MiroRecapInstance"
_MUTEX = None


def instance_unique():
    """Une seule MiroRecap à la fois : si elle tourne déjà, on lui demande d'ouvrir sa fenêtre
    et on s'arrête. Après une mise à jour (relance), on attend que l'ancienne soit partie."""
    global _MUTEX
    import time
    noyau = ctypes.WinDLL("kernel32", use_last_error=True)
    noyau.CreateMutexW.restype = wintypes.HANDLE
    fin = time.monotonic() + (15 if os.environ.get("MIRORECAP_RELANCE") == "1" else 0)
    os.environ.pop("MIRORECAP_RELANCE", None)
    while True:
        _MUTEX = noyau.CreateMutexW(None, True, MUTEX_INSTANCE)
        if ctypes.get_last_error() != 183:   # ERROR_ALREADY_EXISTS
            return True
        noyau.CloseHandle(_MUTEX)
        _MUTEX = None
        if time.monotonic() > fin:
            break
        time.sleep(0.5)
    noyau.OpenEventW.restype = wintypes.HANDLE
    evenement = noyau.OpenEventW(0x0002, False, EVENEMENT_OUVRIR)   # EVENT_MODIFY_STATE
    if evenement:
        noyau.SetEvent(evenement)
        noyau.CloseHandle(evenement)
    return False


def installer_signaux(fenetre_demandee, arret):
    """Nouveau clic sur MiroRecap alors qu'elle tourne : l'autre lancement signale l'événement
    « ouvrir » (voir instance_unique). Fermeture de session : Windows arrête le processus."""
    noyau = ctypes.windll.kernel32
    noyau.CreateEventW.restype = wintypes.HANDLE
    evenement = noyau.CreateEventW(None, False, False, EVENEMENT_OUVRIR)

    def ecouter():
        while True:
            if noyau.WaitForSingleObject(evenement, 0xFFFFFFFF) == 0:
                fenetre_demandee()

    if evenement:
        threading.Thread(target=ecouter, daemon=True).start()


def cacher_du_dock():
    """Pas de Dock : l'app vit dans la zone de notification. On règle seulement son identité."""
    identifier_processus()


def relancer(args, env=None):
    """Remplace ce processus par un nouveau (mise à jour du code). os.execv découpe mal les
    chemins avec des espaces sur Windows : on lance le nouveau processus puis on quitte."""
    env = dict(env or os.environ, MIRORECAP_RELANCE="1")
    subprocess.Popen([str(a) for a in args], env=env, close_fds=True,
                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    os._exit(0)


def preparer_installation(code_app, dossier_app):
    """Instance unique, puis lancement à l'ouverture de session (clé Run de l'utilisateur)."""
    if not instance_unique():
        print("MiroRecap tourne déjà : fenêtre demandée à l'instance ouverte", flush=True)
        os._exit(0)
    exe = Path(sys.executable)
    if exe.name.lower() != "mirorecap.exe":
        return   # lancé hors de l'app installée (tests, développement)
    demarreur = code_app / "demarrage.py"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0,
                            winreg.KEY_SET_VALUE) as cle:
            winreg.SetValueEx(cle, "MiroRecap", 0, winreg.REG_SZ, f'"{exe}" "{demarreur}" --demarrage')
    except OSError as e:
        print(f"Démarrage automatique : {e}", flush=True)


# --- Mise à jour complète ----------------------------------------------------------------

NOM_INSTALLATEUR = "MiroRecap-Setup.exe"


def lancer_installateur(fichier, journal, automatique):
    """Lance l'installateur téléchargé en mode silencieux : il attend la fermeture de MiroRecap,
    remplace l'app (réglages, projets et comptes rendus conservés) puis la relance."""
    commande = [str(fichier), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS",
                f"/LOG={journal}"]
    if automatique:
        commande.append("/DISCRET=1")
    subprocess.Popen(commande, close_fds=True,
                     creationflags=getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
