#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Démarreur de MiroRecap : choisit quel code lancer.

Le moteur (Python, MLX, Whisper…) est dans l'app et change rarement. Le code
(app.py, interface…) se met à jour à part, en quelques Ko, dans :
    ~/Library/Application Support/MiroRecap/code/actuel   (Mac)
    %LOCALAPPDATA%/MiroRecap/code/actuel                   (Windows)

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

WINDOWS = sys.platform == "win32"
CODE_APP = Path(__file__).resolve().parent                      # code livré dans l'app
if WINDOWS:
    DOSSIER_APP = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")) / "MiroRecap"
else:
    DOSSIER_APP = Path.home() / "Library" / "Application Support" / "MiroRecap"
DOSSIER = DOSSIER_APP / "code"
# Le moteur diffère entre Mac et Windows : chacun a son numéro
FICHIER_MOTEUR = "MOTEUR_WINDOWS" if WINDOWS else "MOTEUR"
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
            and lire(dossier / FICHIER_MOTEUR) == lire(CODE_APP / FICHIER_MOTEUR)
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


def journal_windows():
    """Windows : MiroRecap.exe (pythonw) n'a pas de console, donc pas de sortie. Les messages
    vont dans app.log (comme le lanceur natif du Mac) ; l'ancien journal est gardé à côté."""
    if not WINDOWS or (sys.stdout is not None and sys.stderr is not None):
        return
    try:
        DOSSIER_APP.mkdir(parents=True, exist_ok=True)
        journal = DOSSIER_APP / "app.log"
        if journal.exists() and journal.stat().st_size > 5_000_000:
            journal.replace(DOSSIER_APP / "app.ancien.log")
        sys.stdout = sys.stderr = open(journal, "a", encoding="utf-8", buffering=1)
    except OSError:
        pass


def utf8_windows():
    """Windows lit et écrit les fichiers en cp1252 par défaut : les comptes rendus (accents)
    seraient abîmés. On relance Python en mode UTF-8, et ses processus fils (fenêtre) en héritent."""
    if WINDOWS and not sys.flags.utf8_mode:
        import subprocess
        os.environ["PYTHONUTF8"] = "1"
        subprocess.Popen([sys.executable, "-X", "utf8", str(Path(__file__).resolve()), *sys.argv[1:]])
        os._exit(0)


def lancer():
    utf8_windows()
    journal_windows()
    # Lancé par l'utilisateur (menu Démarrer, raccourci) : on ouvre la fenêtre. Pas au démarrage
    # de la session (--demarrage) ni après une mise à jour du code (l'app a déjà décidé)
    if WINDOWS and "--demarrage" not in sys.argv and os.environ.get("MIRORECAP_RELANCE") != "1":
        os.environ["MIRORECAP_FENETRE"] = "1"
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
    if WINDOWS:
        # os.execv découpe mal les chemins avec des espaces sur Windows : nouveau processus, puis on quitte
        import subprocess
        subprocess.Popen([sys.executable, str(Path(__file__).resolve())],
                         env=dict(os.environ, MIRORECAP_RELANCE="1"))
        os._exit(0)
    os.execv(sys.executable, [sys.executable, str(Path(__file__).resolve())])


if __name__ == "__main__":
    lancer()
