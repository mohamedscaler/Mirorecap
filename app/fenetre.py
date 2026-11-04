#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fenêtre de MiroRecap : affiche l'interface servie par app.py (URL passée en argument)."""

import sys
from pathlib import Path

url = sys.argv[1]
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


identite_macos()
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
