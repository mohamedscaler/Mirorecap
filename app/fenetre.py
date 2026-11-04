#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fenêtre de MiroRecap : affiche l'interface servie par app.py (URL passée en argument)."""

import sys
from pathlib import Path

url = sys.argv[1]
JETON = sys.argv[2] if len(sys.argv) > 2 else ""   # pour parler à l'API de l'app
ICONE = Path(__file__).resolve().parent / "icone.png"

try:
    import webview
except ImportError:
    # Sans pywebview : on ouvre l'interface dans le navigateur par défaut
    import webbrowser
    webbrowser.open(url)
    sys.exit(0)


def identite_macos():
    """Remplace l'icône et le nom « Python » par ceux de MiroRecap (Dock, Cmd+Tab, barre de menus)."""
    try:
        from AppKit import NSApplication, NSImage
        from Foundation import NSBundle, NSProcessInfo
        NSBundle.mainBundle().infoDictionary()["CFBundleName"] = "MiroRecap"
        NSProcessInfo.processInfo().setProcessName_("MiroRecap")
        if ICONE.exists():
            image = NSImage.alloc().initWithContentsOfFile_(str(ICONE))
            NSApplication.sharedApplication().setApplicationIconImage_(image)
    except Exception:
        pass


def surveiller_app():
    """Ferme la fenêtre si l'app principale s'arrête (mise à jour, Quitter) :
    sinon elle resterait affichée, figée sur son dernier état."""
    import os
    import time
    parent = os.getppid()
    while True:
        time.sleep(2)
        if os.getppid() != parent:   # parent disparu : le système nous a rattachés à launchd
            os._exit(0)


def apres_demarrage():
    # pywebview peut réinitialiser l'icône au lancement : on la réapplique sur le fil principal
    try:
        from PyObjCTools import AppHelper
        AppHelper.callAfter(identite_macos)
    except Exception:
        pass
    import threading
    threading.Thread(target=surveiller_app, daemon=True).start()


def quitter_miro_recap():
    """Demande à l'app (barre des menus) de se fermer aussi. Elle reste ouverte si un
    enregistrement ou une analyse est en cours, pour ne rien perdre."""
    import urllib.request
    from urllib.parse import urlparse
    base = "{0.scheme}://{0.netloc}".format(urlparse(url))
    try:
        requete = urllib.request.Request(base + "/api/quitter", data=b"{}", method="POST",
                                         headers={"X-Jeton": JETON, "Content-Type": "application/json"})
        urllib.request.urlopen(requete, timeout=2).read()
    except Exception:
        pass


def intercepter_cmd_q():
    """Cmd+Q (menu Quitter) ferme toute l'app ; le bouton rouge ne ferme que la fenêtre."""
    try:
        import objc
        from webview.platforms.cocoa import BrowserView

        def applicationShouldTerminate_(self, app):
            quitter_miro_recap()
            return 1   # NSTerminateNow

        objc.classAddMethods(BrowserView.AppDelegate, [
            objc.selector(applicationShouldTerminate_, selector=b"applicationShouldTerminate:", signature=b"Q@:@")])
    except Exception as e:
        print(f"Cmd+Q : {e}", flush=True)


identite_macos()
intercepter_cmd_q()
# Liquid Glass : la fenêtre laisse voir le bureau flouté derrière (vibrancy macOS),
# l'interface pose ses panneaux de verre par-dessus.
def couleur_fond():
    try:
        from AppKit import NSApplication
        sombre = "Dark" in str(NSApplication.sharedApplication().effectiveAppearance().name())
        return "#1C1C1F" if sombre else "#ECECF1"
    except Exception:
        return "#ECECF1"


options = dict(width=1100, height=720, min_size=(880, 580), background_color=couleur_fond())
try:
    webview.create_window("MiroRecap", url, vibrancy=True, **options)
except TypeError:
    # Ancienne version de pywebview sans vibrancy : fenêtre normale
    webview.create_window("MiroRecap", url, **options)
webview.start(apres_demarrage)
