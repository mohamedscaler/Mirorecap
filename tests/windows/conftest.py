# -*- coding: utf-8 -*-
"""Tests de la version Windows (runner GitHub windows-latest, avec le Python de l'app installée).

    <app>\\python\\python.exe -m pytest tests\\windows -v

MIRORECAP_APP : dossier du code testé (par défaut app/ du dépôt). Les données de l'app
(%LOCALAPPDATA%\\MiroRecap) vont dans un dossier temporaire : rien ne touche à une vraie installation."""
import os
import sys
import tempfile
from pathlib import Path

import pytest

if sys.platform != "win32":
    collect_ignore_glob = ["test_*.py"]   # tests propres à Windows

RACINE = Path(__file__).resolve().parents[2]
CODE = Path(os.environ.get("MIRORECAP_APP") or (RACINE / "app"))
DONNEES = Path(tempfile.mkdtemp(prefix="mirorecap-tests-"))

# Avant tout import de plateforme : DOSSIER_APP est calculé au chargement
os.environ["LOCALAPPDATA"] = str(DONNEES)
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
sys.path.insert(0, str(CODE))


@pytest.fixture
def code_app():
    return CODE


@pytest.fixture
def voix():
    return Path(__file__).resolve().parent / "voix.wav"
