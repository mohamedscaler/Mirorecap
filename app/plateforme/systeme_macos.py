# -*- coding: utf-8 -*-
"""macOS : dossiers, notifications, Finder, Corbeille, Rappels, détection des appels, démarrage."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

DOSSIER_APP = Path.home() / "Library" / "Application Support" / "MiroRecap"
ICLOUD = Path.home() / "Library" / "Mobile Documents" / "com~apple~CloudDocs"
CONFIG_CLAUDE_DESKTOP = Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
NOM_GESTIONNAIRE = "Finder"


def dossier_sortie_defaut():
    """iCloud Drive/Comptes-rendus si iCloud Drive est actif (visible sur l'iPhone), sinon ~/Comptes-rendus."""
    return ICLOUD / "Comptes-rendus" if ICLOUD.exists() else Path.home() / "Comptes-rendus"


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
        import rumps
        rumps.notification("MiroRecap", titre, message, sound=False)
    except Exception:
        pass  # pas de notification plutôt qu'une notification au nom d'un autre programme


def ouvrir(cible, reveler=False):
    """Ouvre un dossier, un fichier ou une adresse (https:, mailto:) ; reveler : le montre dans le Finder."""
    subprocess.run(["open", "-R", str(cible)] if reveler else ["open", str(cible)], check=False)


def mettre_a_la_corbeille(chemin, chemin_libre):
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


def telecharger_depuis_le_cloud(chemin_icloud):
    """Fichier iCloud pas encore téléchargé ('.nom.m4a.icloud') : retourne le vrai chemin et
    demande son téléchargement. None si ce n'est pas un fichier iCloud en attente."""
    if not (chemin_icloud.name.startswith(".") and chemin_icloud.name.endswith(".icloud")):
        return None
    reel = chemin_icloud.with_name(chemin_icloud.name[1:-len(".icloud")])
    subprocess.run(["brctl", "download", str(reel)], check=False)
    return reel


def date_creation(infos):
    return getattr(infos, "st_birthtime", infos.st_mtime)


# --- Rappels -----------------------------------------------------------------

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


def ajouter_rappels(liste, note, actions):
    """Ajoute les actions dans l'app Rappels (synchronisée avec l'iPhone).
    Le texte passe en arguments séparés : aucun risque d'injection."""
    r = subprocess.run(["osascript", "-e", SCRIPT_RAPPELS, liste, note, *actions],
                       capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        erreur = r.stderr.strip()
        if "-1743" in erreur or "not allowed" in erreur.lower() or "autoris" in erreur.lower():
            raise ValueError("autorise MiroRecap à utiliser Rappels : Réglages Système → Confidentialité et "
                             "sécurité → Automatisation (ou Rappels), puis réessaie")
        raise ValueError(f"Rappels : {erreur or 'erreur inconnue'}")


# --- Détection des appels ------------------------------------------------------

APPS_VISIO = {
    "us.zoom.xos": "Zoom", "com.microsoft.teams2": "Teams", "com.microsoft.teams": "Teams",
    "com.apple.FaceTime": "FaceTime", "com.hnc.Discord": "Discord", "com.tinyspeck.slackmacgap": "Slack",
    "net.whatsapp.WhatsApp": "WhatsApp", "desktop.WhatsApp": "WhatsApp", "com.cisco.webexmeetingsapp": "Webex",
    "com.skype.skype": "Skype", "ru.keepcoder.Telegram": "Telegram", "com.google.Chrome": "Chrome",
    "com.apple.Safari": "Safari", "company.thebrowser.Browser": "Arc", "com.brave.Browser": "Brave",
    "org.mozilla.firefox": "Firefox", "com.microsoft.edgemac": "Edge",
}
NAVIGATEURS = {"Chrome", "Safari", "Arc", "Brave", "Firefox", "Edge"}
_COREAUDIO = None


def _fourcc(code):
    return int.from_bytes(code.encode(), "big")


def micro_utilise_ailleurs():
    """True si le micro par défaut est en cours d'utilisation (par n'importe quelle app).
    CoreAudio : kAudioDevicePropertyDeviceIsRunningSomewhere sur le micro par défaut."""
    import ctypes
    import ctypes.util
    global _COREAUDIO

    class Adresse(ctypes.Structure):
        _fields_ = [("mSelector", ctypes.c_uint32), ("mScope", ctypes.c_uint32), ("mElement", ctypes.c_uint32)]

    if _COREAUDIO is None:
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


# --- Processus de l'app ------------------------------------------------------------

def python_fenetre():
    """Python nommé « MiroRecap » (créé à la construction) : c'est ce nom qu'affiche le Dock."""
    python = Path(sys.executable).with_name("MiroRecap")
    return python if python.exists() else Path(sys.executable)


def installer_signaux(fenetre_demandee, arret):
    """Clic sur l'icône de l'app alors qu'elle tourne : le lanceur natif envoie SIGUSR1.
    Fermeture (Quitter du Dock, extinction du Mac…) : il envoie SIGTERM."""
    import signal
    signal.signal(signal.SIGUSR1, lambda *_: fenetre_demandee())
    signal.signal(signal.SIGTERM, lambda *_: arret())


def cacher_du_dock():
    """Pas d'icône dans le Dock pour le processus principal : il vit dans la barre de menus."""
    try:
        from AppKit import NSApplication
        NSApplication.sharedApplication().setActivationPolicy_(1)  # 1 = Accessory
    except Exception:
        pass


def preparer_installation(code_app, dossier_app):
    """Lancement au démarrage du Mac + nettoyage des anciennes versions installées par script."""
    bundle = code_app.parent.parent.parent              # …/MiroRecap.app
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
        shutil.rmtree(dossier_app / nom, ignore_errors=True)
    for nom in ("app.py", "fenetre.py", "requirements.txt", "VERSION", "icone.png"):
        (dossier_app / nom).unlink(missing_ok=True)
