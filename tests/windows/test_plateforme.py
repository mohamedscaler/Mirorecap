# -*- coding: utf-8 -*-
"""Modules Windows de plateforme/ : dossiers, conversion, capture du son du PC (simulée),
détection d'appel par le registre, Corbeille, instance unique, connecteur MCP."""
import json
import subprocess
import sys
import threading
import time
import types
import winreg

import numpy as np
import soundfile as sf


def test_modules_windows_choisis():
    import plateforme
    assert plateforme.WINDOWS
    assert plateforme.systeme.__name__.endswith("systeme_windows")
    assert plateforme.audio.__name__.endswith("audio_windows")
    assert plateforme.whisper.__name__.endswith("whisper_faster")
    assert plateforme.FICHIER_MOTEUR == "MOTEUR_WINDOWS"
    assert plateforme.charger_menus().__name__.endswith("menus_windows")
    assert plateforme.charger_fenetre().__name__.endswith("fenetre_windows")
    assert [m["nom"] for m in plateforme.whisper.MODELES] == ["Normal", "Turbo", "Pro"]


def test_dossiers(tmp_path):
    from plateforme import systeme_windows as s
    import os
    assert str(s.DOSSIER_APP).startswith(os.environ["LOCALAPPDATA"])
    assert s.dossier_sortie_defaut().name == "Comptes-rendus"
    assert s.CONFIG_CLAUDE_DESKTOP.name == "claude_desktop_config.json"


def _sinus(frequence, duree, canaux):
    t = np.arange(int(frequence * duree)) / frequence
    son = (0.3 * np.sin(2 * np.pi * 440 * t)).astype("float32")
    return np.stack([son] * canaux, axis=1) if canaux > 1 else son


def test_conversion_wav(tmp_path):
    from plateforme import audio_windows as a
    source, cible = tmp_path / "stereo.wav", tmp_path / "16k.wav"
    sf.write(source, _sinus(44100, 1.0, 2), 44100)
    ok, erreur = a.convertir_wav_16k(source, cible)
    assert ok, erreur
    infos = sf.info(cible)
    assert (infos.samplerate, infos.channels) == (16000, 1)
    assert abs(infos.duration - 1.0) < 0.05


def test_conversion_m4a(tmp_path):
    """m4a (AAC) : format des mémos vocaux de l'iPhone, illisible sans ffmpeg (ici PyAV)."""
    import av
    from plateforme import audio_windows as a
    source, cible = tmp_path / "memo.m4a", tmp_path / "16k.wav"
    with av.open(str(source), "w") as conteneur:
        flux = conteneur.add_stream("aac", rate=44100)
        flux.layout = "mono"
        son = _sinus(44100, 2.0, 1)
        for debut in range(0, len(son), 1024):
            trame = av.AudioFrame.from_ndarray(son[debut:debut + 1024].reshape(1, -1), format="fltp", layout="mono")
            trame.sample_rate = 44100
            for paquet in flux.encode(trame):
                conteneur.mux(paquet)
        for paquet in flux.encode(None):
            conteneur.mux(paquet)
    ok, erreur = a.convertir_wav_16k(source, cible)
    assert ok, erreur
    lu, frequence = sf.read(cible, dtype="float32")
    assert frequence == 16000 and lu.ndim == 1
    assert abs(len(lu) / 16000 - 2.0) < 0.15
    assert np.abs(lu).max() > 0.1


def test_conversion_fichier_illisible(tmp_path):
    from plateforme import audio_windows as a
    faux = tmp_path / "faux.mp3"
    faux.write_bytes(b"pas un fichier audio")
    ok, erreur = a.convertir_wav_16k(faux, tmp_path / "x.wav")
    assert not ok and erreur


class _FluxSimule:
    """Faux flux WASAPI : envoie du son 0,5 s, puis plus rien (comme quand rien ne joue)."""

    def __init__(self, rappel, frequence, canaux):
        self.rappel, self.frequence, self.canaux = rappel, frequence, canaux
        self.actif = True

    def start_stream(self):
        def envoyer():
            bloc = (np.ones((self.frequence // 20, self.canaux)) * 8000).astype("<i2").tobytes()
            for _ in range(10):   # 10 × 50 ms
                if not self.actif:
                    return
                self.rappel(bloc, self.frequence // 20, {}, 0)
                time.sleep(0.05)
        threading.Thread(target=envoyer, daemon=True).start()

    def stop_stream(self):
        self.actif = False

    def close(self):
        pass


def _faux_pyaudiowpatch():
    module = types.ModuleType("pyaudiowpatch")
    module.paWASAPI, module.paInt16, module.paContinue = 13, 8, 0

    # Comme la vraie bibliothèque : chaque sortie WASAPI a une entrée « [Loopback] » à part
    peripheriques = [
        {"index": 0, "name": "Haut-parleurs", "maxOutputChannels": 2, "maxInputChannels": 0,
         "defaultSampleRate": 48000.0},
        {"index": 1, "name": "Casque", "maxOutputChannels": 2, "maxInputChannels": 0,
         "defaultSampleRate": 44100.0},
        {"index": 2, "name": "Haut-parleurs [Loopback]", "isLoopbackDevice": True, "maxOutputChannels": 0,
         "maxInputChannels": 2, "defaultSampleRate": 48000.0},
        {"index": 3, "name": "Casque [Loopback]", "isLoopbackDevice": True, "maxOutputChannels": 0,
         "maxInputChannels": 2, "defaultSampleRate": 44100.0},
    ]

    class PyAudio:
        ouverts = []   # périphériques ouverts, pour vérifier la sortie captée

        def get_host_api_info_by_type(self, _):
            return {"index": 0, "deviceCount": len(peripheriques), "defaultOutputDevice": 0}

        def get_device_info_by_host_api_device_index(self, _api, i):
            return peripheriques[i]

        def get_device_info_by_index(self, i):
            return peripheriques[i]

        def get_loopback_device_info_generator(self):
            return (d for d in peripheriques if d.get("isLoopbackDevice"))

        def open(self, rate, channels, stream_callback, input_device_index=None, **_):
            PyAudio.ouverts.append(input_device_index)
            return _FluxSimule(stream_callback, rate, channels)

        def terminate(self):
            pass

    module.PyAudio = PyAudio
    return module


def test_sortie_choisie_captee(tmp_path, monkeypatch):
    """La liste ne montre que les vraies sorties, et la capture suit la sortie choisie
    (sinon celle par défaut, ou celle par défaut si la choisie est débranchée)."""
    faux = _faux_pyaudiowpatch()
    monkeypatch.setitem(sys.modules, "pyaudiowpatch", faux)
    from plateforme import audio_windows as a
    assert a.liste_sorties() == (["Haut-parleurs", "Casque"], "Haut-parleurs")
    for nom, attendu in (("Casque", 3), ("", 2), ("Débranché", 2)):
        capture = a.lancer_capture(tmp_path / f"{attendu}-{nom or 'defaut'}.wav", nom)
        time.sleep(0.2)
        capture.terminate()
        assert capture.wait(timeout=5) == 0
        assert faux.PyAudio.ouverts[-1] == attendu, (nom, faux.PyAudio.ouverts)


def test_capture_comble_les_silences(tmp_path, monkeypatch):
    """La piste du son du PC doit durer aussi longtemps que l'enregistrement, même quand
    WASAPI n'envoie plus rien : sinon elle se décale par rapport au micro au mixage."""
    monkeypatch.setitem(sys.modules, "pyaudiowpatch", _faux_pyaudiowpatch())
    from plateforme import audio_windows as a
    fichier = tmp_path / "systeme.wav"
    capture = a.lancer_capture(fichier)
    time.sleep(0.3)
    assert capture.poll() is None
    assert a.niveau_capture(fichier) > 0.5        # jauge en direct pendant l'enregistrement
    time.sleep(1.5)
    capture.terminate()
    assert capture.wait(timeout=5) == 0
    son, frequence = sf.read(fichier, dtype="int16")
    assert frequence == 48000 and son.ndim == 1
    duree = len(son) / frequence
    assert 1.5 <= duree <= 2.1, duree
    assert son[: frequence // 4].min() == 8000     # moyenne des deux canaux
    assert not son[-frequence // 2:].any()          # puis du silence comblé


def test_capture_lue_pendant_ecriture(tmp_path, monkeypatch):
    """La transcription en direct lit le WAV pendant qu'il s'écrit (LecteurWavEnCours)."""
    monkeypatch.setitem(sys.modules, "pyaudiowpatch", _faux_pyaudiowpatch())
    from plateforme import audio_windows as a
    fichier = tmp_path / "systeme.wav"
    capture = a.lancer_capture(fichier)
    time.sleep(0.4)
    with open(fichier, "rb") as f:
        entete = f.read(64)
        assert entete[:4] == b"RIFF" and b"data" in entete
        f.seek(0, 2)
        assert f.tell() > 44 + 48000 * 2 * 0.2
    capture.terminate()
    capture.wait(timeout=5)


def test_capture_impossible_dit_pourquoi(tmp_path, monkeypatch):
    faux = _faux_pyaudiowpatch()

    def refuser(self, *_a, **_k):
        raise OSError("aucune sortie audio")
    faux.PyAudio.open = refuser
    monkeypatch.setitem(sys.modules, "pyaudiowpatch", faux)
    from plateforme import audio_windows as a
    capture = a.lancer_capture(tmp_path / "x.wav")
    assert capture.wait(timeout=4) == 1
    assert "aucune sortie audio" in capture.stderr.read()


def test_capture_reelle_ne_plante_pas(tmp_path):
    """Sur le runner il n'y a souvent pas de carte son : la capture doit échouer proprement
    (code 1 + message) ou tourner, jamais lever d'exception."""
    from plateforme import audio_windows as a
    assert a.capture_disponible()
    capture = a.lancer_capture(tmp_path / "reel.wav")
    try:
        code = capture.wait(timeout=3)
        print("capture réelle :", code, capture.stderr.read())
        assert code == 1
    except subprocess.TimeoutExpired:
        capture.terminate()
        assert capture.wait(timeout=5) == 0


CLE_TEST = r"C:#Programmes de test#Zoom#bin#Zoom.exe"


def test_detection_appel_par_le_registre():
    from plateforme import systeme_windows as s
    chemin = s.CLE_MICRO + r"\NonPackaged\\" + CLE_TEST
    cle = winreg.CreateKey(winreg.HKEY_CURRENT_USER, chemin)
    try:
        winreg.SetValueEx(cle, "LastUsedTimeStart", 0, winreg.REG_QWORD, 133_800_000_000_000_000)
        winreg.SetValueEx(cle, "LastUsedTimeStop", 0, winreg.REG_QWORD, 0)
        assert "zoom.exe" in s.apps_utilisant_le_micro()
        assert s.micro_utilise_ailleurs()
        assert s.app_de_visio() == "Zoom"
        winreg.SetValueEx(cle, "LastUsedTimeStop", 0, winreg.REG_QWORD, 133_800_000_100_000_000)
        assert "zoom.exe" not in s.apps_utilisant_le_micro()
    finally:
        winreg.CloseKey(cle)
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, chemin)


def test_corbeille(tmp_path):
    from plateforme import systeme_windows as s
    fichier = tmp_path / "audio à jeter.wav"
    fichier.write_bytes(b"RIFF")
    s.mettre_a_la_corbeille(fichier, lambda p: p)
    assert not fichier.exists()


def test_rappels_desactives():
    from plateforme import systeme_windows as s
    assert s.RAPPELS is False
    try:
        s.ajouter_rappels("MiroRecap", "", ["x"])
        assert False, "aurait dû refuser"
    except ValueError as e:
        assert "Windows" in str(e)


def test_instance_unique(code_app):
    """Deuxième lancement : il réveille la première instance (fenêtre) et s'arrête."""
    script = ("import sys, time; sys.path.insert(0, sys.argv[1]); from plateforme import systeme_windows as s;"
              "ouvert = []; s.installer_signaux(lambda: ouvert.append(1), None) if sys.argv[2] == '1' else None;"
              "print(s.instance_unique(), flush=True); time.sleep(float(sys.argv[3]));"
              "print('ouverte' if ouvert else 'rien', flush=True)")
    premiere = subprocess.Popen([sys.executable, "-c", script, str(code_app), "1", "6"],
                                stdout=subprocess.PIPE, text=True)
    assert premiere.stdout.readline().strip() == "True"
    seconde = subprocess.run([sys.executable, "-c", script, str(code_app), "0", "0"],
                             capture_output=True, text=True, timeout=30)
    assert seconde.stdout.splitlines()[0] == "False"
    reste, _ = premiere.communicate(timeout=30)
    assert reste.strip() == "ouverte"


def test_connecteur_mcp(code_app):
    requetes = "\n".join(json.dumps(r) for r in (
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    )) + "\n"
    r = subprocess.run([sys.executable, str(code_app / "mcp_serveur.py")], input=requetes.encode("utf-8"),
                       capture_output=True, timeout=30)
    lignes = [json.loads(l) for l in r.stdout.decode("utf-8").splitlines() if l.strip()]
    assert lignes[0]["result"]["serverInfo"]["name"] == "mirorecap"
    assert lignes[1]["result"]["tools"]
