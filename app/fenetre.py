#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fenêtre de MiroRecap : affiche l'interface servie par app.py (URL passée en argument)."""

import sys

url = sys.argv[1]

try:
    import webview
except ImportError:
    # Sans pywebview : on ouvre l'interface dans le navigateur par défaut
    import webbrowser
    webbrowser.open(url)
    sys.exit(0)

# Nom affiché dans la barre de menus de macOS (sinon « Python »)
try:
    from Foundation import NSBundle
    NSBundle.mainBundle().infoDictionary()["CFBundleName"] = "MiroRecap"
except Exception:
    pass

webview.create_window("MiroRecap", url, width=1080, height=720, min_size=(860, 580))
webview.start()
