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


def apres_demarrage():
    # pywebview peut réinitialiser l'icône au lancement : on la réapplique sur le fil principal
    try:
        from PyObjCTools import AppHelper
        AppHelper.callAfter(identite_macos)
    except Exception:
        pass


identite_macos()
webview.create_window("MiroRecap", url, width=1100, height=720, min_size=(880, 580))
webview.start(apres_demarrage)
