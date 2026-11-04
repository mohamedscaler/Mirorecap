#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Démarreur de MiroRecap : choisit quel code lancer.

Le moteur (Python, MLX, Whisper…) est dans l'app et change rarement. Le code
(app.py, interface…) se met à jour à part, en quelques Ko, dans :
    ~/Library/Application Support/MiroRecap/code/actuel

Ce fichier reste dans l'app et n'est jamais remplacé par une mise à jour du code.
Il lance le code téléchargé s'il est plus récent et compatible avec le moteur,
sinon celui livré avec l'app. Si le code téléchargé plante au démarrage, il est
écarté et l'app repart sur la version précédente : on ne reste jamais bloqué.
"""
import os
import runpy
import shutil
import sys
import time
import traceback
from pathlib import Path

CODE_APP = Path(__file__).resolve().parent                      # code livré dans l'app
DOSSIER = Path.home() / "Library" / "Application Support" / "MiroRecap" / "code"
ESSAIS_MAX = 3   # démarrages ratés tolérés avant d'écarter un code téléchargé


def lire(chemin, defaut=""):
    try:
        return chemin.read_text(encoding="utf-8").strip()
    except OSError:
        return defaut


def version(dossier):
    return tuple(int(x) for x in lire(dossier / "VERSION", "0").split(".") if x.isdigit())


def compatible(dossier):
    """Code complet, prévu pour ce moteur, et pas marqué défectueux."""
    return ((dossier / "app.py").exists()
            and lire(dossier / "MOTEUR") == lire(CODE_APP / "MOTEUR")
            and not (dossier / ".defectueux").exists())


def ecarter(dossier, raison):
    print(f"[démarrage] code {lire(dossier / 'VERSION')} écarté : {raison}", flush=True)
    try:
        (dossier / ".defectueux").write_text(raison, encoding="utf-8")
    except OSError:
        pass


def choisir():
    """Dossier de code à lancer : le plus récent parmi actuel, précédent et celui de l'app."""
    candidats = [CODE_APP]
    for nom in ("actuel", "precedent"):
        d = DOSSIER / nom
        if d.is_dir() and compatible(d):
            candidats.append(d)
    return max(candidats, key=version)   # à égalité, celui de l'app (premier) gagne


def lancer():
    code = choisir()
    telecharge = code != CODE_APP

    if telecharge:
        # Compteur de démarrages : remis à zéro par app.py une fois bien lancé
        compteur = code / ".essais"
        essais = int(lire(compteur, "0") or 0) + 1
        if essais > ESSAIS_MAX:
            ecarter(code, f"{ESSAIS_MAX} démarrages ratés")
            return relancer()
        compteur.write_text(str(essais), encoding="utf-8")

    os.environ["MIRORECAP_CODE_APP"] = str(CODE_APP)   # où trouver les outils de l'app
    sys.path.insert(0, str(code))
    sys.argv[0] = str(code / "app.py")
    try:
        runpy.run_path(str(code / "app.py"), run_name="__main__")
    except SystemExit:
        raise
    except BaseException:
        traceback.print_exc()
        sys.stdout.flush()
        if not telecharge:
            raise
        ecarter(code, "erreur au démarrage")
        relancer()


def relancer():
    """Redémarre ce processus (même PID : le lanceur natif ne voit rien) sur un autre code."""
    time.sleep(0.5)
    os.execv(sys.executable, [sys.executable, str(Path(__file__).resolve())])


if __name__ == "__main__":
    lancer()
