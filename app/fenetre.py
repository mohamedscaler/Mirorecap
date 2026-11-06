#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fenêtre de MiroRecap : affiche l'interface servie par app.py (URL passée en argument).
Les réglages propres au système (Cocoa sur Mac) sont dans plateforme/fenetre_<système>.py."""

import sys
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

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

import plateforme

natif = plateforme.charger_fenetre()


def appeler_app(chemin):
    """POST sur l'API de l'app (elle peut être déjà en train de quitter ou de redémarrer)."""
    base = "{0.scheme}://{0.netloc}".format(urlparse(url))
    try:
        requete = urllib.request.Request(base + chemin, data=b"{}", method="POST",
                                         headers={"X-Jeton": JETON, "Content-Type": "application/json"})
        urllib.request.urlopen(requete, timeout=2).read()
    except Exception:
        pass


def quitter_miro_recap():
    """Demande à l'app (barre des menus) de se fermer aussi. Elle reste ouverte si un
    enregistrement ou une analyse est en cours, pour ne rien perdre."""
    appeler_app("/api/quitter")


def signaler_fermeture():
    """Fenêtre fermée (bouton rouge) : l'app décide si elle quitte aussi (réglage « Fermeture »)."""
    appeler_app("/api/fenetre-fermee")


natif.avant_creation(ICONE, quitter_miro_recap)
options = dict(width=1100, height=720, min_size=(880, 580), background_color=natif.couleur_fond())
try:
    fenetre = webview.create_window("MiroRecap", url, js_api=natif.Api(), **natif.OPTIONS_FENETRE, **options)
except TypeError:
    # Ancienne version de pywebview sans ces options (vibrancy…) : fenêtre normale
    fenetre = webview.create_window("MiroRecap", url, js_api=natif.Api(), **options)
try:
    fenetre.events.closed += signaler_fermeture
except Exception as e:
    print(f"Fermeture de la fenêtre : {e}", flush=True)
webview.start(natif.apres_demarrage, (ICONE,))
