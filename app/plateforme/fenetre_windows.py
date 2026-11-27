# -*- coding: utf-8 -*-
"""Windows : réglages de la fenêtre pywebview (EdgeChromium / WebView2). La barre de titre
reste celle de Windows : pas de feux tricolores à placer, ni de déplacement à gérer."""
import os
import threading


def identite(icone):
    """Logo et nom de MiroRecap dans la barre des tâches, groupés avec l'icône de l'app."""
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("fr.mirorecap.app")
    except Exception:
        pass


def surveiller_app(parent):
    """Ferme la fenêtre si l'app principale s'arrête (mise à jour, Quitter) :
    sinon elle resterait affichée, figée sur son dernier état."""
    try:
        import ctypes
        noyau = ctypes.windll.kernel32
        noyau.OpenProcess.restype = ctypes.c_void_p
        poignee = noyau.OpenProcess(0x00100000, False, parent)   # SYNCHRONIZE
        if not poignee:
            os._exit(0)   # déjà parti
        noyau.WaitForSingleObject(ctypes.c_void_p(poignee), 0xFFFFFFFF)
        os._exit(0)
    except Exception as e:
        print(f"Surveillance de l'app : {e}", flush=True)


def couleur_fond():
    """Fond de la fenêtre avant l'affichage de la page : clair ou sombre comme Windows."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as cle:
            clair, _ = winreg.QueryValueEx(cle, "AppsUseLightTheme")
        return "#ECECF1" if clair else "#1C1C1F"
    except Exception:
        return "#ECECF1"


OPTIONS_FENETRE = {}


def avant_creation(icone, quitter):
    identite(icone)


def apres_demarrage(icone):
    threading.Thread(target=surveiller_app, args=(os.getppid(),), daemon=True).start()


class Api:
    """Appelée par l'interface. La barre de titre de Windows gère déjà déplacement et agrandissement."""

    def deplacer(self):
        pass

    def agrandir(self):
        pass
