#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MiroRecap — app macOS (Mac Apple Silicon) : icône 🎙 dans la barre de menus + fenêtre.

Flux : audio (enregistré ici ou déposé dans _A-traiter)
       → transcription locale Whisper (MLX)
       → Claude rédige le compte rendu et choisit le projet
       → rangement dans <Comptes-rendus>/<Projet>/

Architecture :
- ce processus gère l'enregistrement, la file de traitement et l'icône de la barre de menus ;
- il sert aussi une petite API locale (127.0.0.1, protégée par jeton) ;
- la fenêtre (fenetre.py) est un processus séparé qui affiche ui/index.html et parle à cette API.

Les projets = les sous-dossiers du dossier Comptes-rendus.
Un fichier description.txt dans un dossier projet aide Claude à mieux classer.
"""

import datetime as dt
import http.server
import json
import queue
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import unicodedata
import urllib.parse
import urllib.request
from pathlib import Path

import rumps
import sounddevice as sd
import soundfile as sf

# ---------------------------------------------------------------------------
# Configuration (stockée hors du dossier partageable : chacun a sa clé)
# ---------------------------------------------------------------------------

DOSSIER_CODE = Path(__file__).resolve().parent
DOSSIER_APP = Path.home() / "Library" / "Application Support" / "MiroRecap"

# Mises à jour : on compare app/VERSION local avec celui de la branche main sur GitHub
DEPOT = "mohamedscaler/Mirorecap"
URL_VERSION = f"https://raw.githubusercontent.com/{DEPOT}/main/app/VERSION"
URL_INSTALL = f"https://raw.githubusercontent.com/{DEPOT}/main/install-distant.sh"
_fichier_version = DOSSIER_CODE / "VERSION"
VERSION = _fichier_version.read_text().strip() if _fichier_version.exists() else "0"
INTERVALLE_VERIF = 6 * 3600  # vérifie toutes les 6 heures
FICHIER_CONFIG = DOSSIER_APP / "config.json"
ICLOUD = Path.home() / "Library" / "Mobile Documents" / "com~apple~CloudDocs"

MODELES_WHISPER = [
    {"id": "mlx-community/whisper-large-v3-turbo", "nom": "Turbo", "detail": "Précis et rapide. Recommandé."},
    {"id": "mlx-community/whisper-large-v3-mlx", "nom": "Large v3", "detail": "Le plus précis, environ deux fois plus lent."},
    {"id": "mlx-community/whisper-small-mlx", "nom": "Small", "detail": "Très rapide, moins fiable sur les noms et les chiffres."},
]

CONFIG_DEFAUT = {
    "cle_api_anthropic": "",
    # Vide = iCloud Drive/Comptes-rendus si iCloud Drive est actif, sinon ~/Comptes-rendus
    "dossier_sortie": "",
    "modele_whisper": MODELES_WHISPER[0]["id"],
    "modele_claude": "claude-sonnet-5-5",
    # Moteur d'analyse : "auto" (abonnement si Claude Code est installé, sinon clé API),
    # "abonnement" (Claude Code, compte Claude Pro/Max) ou "api" (clé API Anthropic)
    "moteur": "auto",
    "modele_abonnement": "sonnet",
    "langue": "fr",
}


def charger_config():
    config = dict(CONFIG_DEFAUT)
    if FICHIER_CONFIG.exists():
        config.update(json.loads(FICHIER_CONFIG.read_text(encoding="utf-8")))
    return config


def sauver_config(config):
    DOSSIER_APP.mkdir(parents=True, exist_ok=True)
    FICHIER_CONFIG.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")


CONFIG = charger_config()

if CONFIG["dossier_sortie"]:
    DOSSIER_SORTIE = Path(CONFIG["dossier_sortie"]).expanduser()
elif ICLOUD.exists():
    DOSSIER_SORTIE = ICLOUD / "Comptes-rendus"   # accessible depuis l'iPhone (app Fichiers)
else:
    DOSSIER_SORTIE = Path.home() / "Comptes-rendus"

DOSSIER_IMPORT = DOSSIER_SORTIE / "_A-traiter"
DOSSIER_ENREG = DOSSIER_SORTIE / "_En-cours"
DOSSIER_ERREURS = DOSSIER_SORTIE / "_Erreurs"
PROJET_PAR_DEFAUT = "Divers"
EXTENSIONS_AUDIO = {".m4a", ".mp3", ".wav", ".aac", ".caf", ".mp4", ".mov",
                    ".ogg", ".opus", ".flac", ".webm"}

for _d in (DOSSIER_IMPORT, DOSSIER_ENREG, DOSSIER_ERREURS, DOSSIER_SORTIE / PROJET_PAR_DEFAUT):
    _d.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Utilitaires
# ---------------------------------------------------------------------------

def notifier(titre, message):
    """Notification macOS via osascript (fiable même hors app empaquetée)."""
    def echapper(s):
        return s.replace("\\", "\\\\").replace('"', '\\"')
    subprocess.run(
        ["osascript", "-e", f'display notification "{echapper(message)}" with title "{echapper(titre)}"'],
        check=False,
    )


def slugifier(texte, longueur_max=60):
    """'Appel client : devis Shopify' -> 'appel-client-devis-shopify'"""
    texte = unicodedata.normalize("NFKD", texte).encode("ascii", "ignore").decode()
    texte = re.sub(r"[^a-zA-Z0-9]+", "-", texte).strip("-").lower()
    return texte[:longueur_max].rstrip("-") or "sans-titre"


def chemin_libre(chemin):
    """Évite d'écraser un fichier existant en ajoutant -2, -3…"""
    if not chemin.exists():
        return chemin
    i = 2
    while True:
        candidat = chemin.with_name(f"{chemin.stem}-{i}{chemin.suffix}")
        if not candidat.exists():
            return candidat
        i += 1


def nom_valide(nom):
    """Nom de projet utilisable comme dossier (pas de / ni de : ni de dossier technique)."""
    nom = re.sub(r"[/:\\]", "-", (nom or "").strip())
    if not nom or nom.startswith((".", "_")):
        raise ValueError("nom de projet invalide")
    return nom


def dossier_projet(nom):
    """Chemin d'un projet existant, en refusant toute sortie du dossier Comptes-rendus."""
    chemin = (DOSSIER_SORTIE / nom).resolve()
    if chemin.parent != DOSSIER_SORTIE.resolve() or not chemin.is_dir() or nom.startswith(("_", ".")):
        raise ValueError("projet introuvable")
    return chemin


def lister_projets():
    """{nom: description} pour chaque sous-dossier projet (hors _dossiers techniques et Divers)."""
    projets = {}
    for d in sorted(DOSSIER_SORTIE.iterdir(), key=lambda p: p.name.lower()):
        if d.is_dir() and not d.name.startswith(("_", ".")) and d.name != PROJET_PAR_DEFAUT:
            fichier_desc = d / "description.txt"
            projets[d.name] = fichier_desc.read_text(encoding="utf-8").strip() if fichier_desc.exists() else ""
    return projets


def compter_erreurs():
    return sum(1 for f in DOSSIER_ERREURS.iterdir() if f.suffix.lower() in EXTENSIONS_AUDIO)


def relancer_erreurs():
    n = 0
    for f in DOSSIER_ERREURS.iterdir():
        if f.suffix.lower() in EXTENSIONS_AUDIO:
            shutil.move(str(f), str(chemin_libre(DOSSIER_IMPORT / f.name)))
            n += 1
        elif f.suffix == ".txt":
            f.unlink()  # transcription partielle : elle sera refaite
    return n


def version_tuple(v):
    """'3.10' -> (3, 10) pour comparer correctement les versions."""
    return tuple(int(x) for x in re.findall(r"\d+", v)) or (0,)


def derniere_version():
    """Version publiée sur GitHub, ou None si pas de réseau."""
    try:
        with urllib.request.urlopen(URL_VERSION, timeout=10) as r:
            return r.read().decode().strip()
    except Exception:
        return None


def ouvrir_finder(chemin, reveler=False):
    subprocess.run(["open", "-R", str(chemin)] if reveler else ["open", str(chemin)], check=False)


# ---------------------------------------------------------------------------
# Enregistrement micro
# ---------------------------------------------------------------------------

class Enregistreur:
    """Enregistre le micro par défaut dans un .wav.
    Le callback audio pousse les blocs dans une file, un thread séparé les écrit
    sur le disque : le callback ne bloque jamais (sinon trous dans l'audio)."""

    def __init__(self):
        self.actif = False
        self.debut = None
        self.chemin = None
        self._file = queue.Queue()
        self._flux = None
        self._ecrivain = None

    def demarrer(self):
        frequence = int(sd.query_devices(kind="input")["default_samplerate"])
        self.chemin = chemin_libre(DOSSIER_ENREG / f"{dt.datetime.now():%Y-%m-%d_%Hh%M}.wav")
        fichier = sf.SoundFile(self.chemin, mode="w", samplerate=frequence, channels=1, subtype="PCM_16")

        def ecrire():
            with fichier:
                # Continue tant qu'on enregistre OU qu'il reste des blocs à écrire
                while self.actif or not self._file.empty():
                    try:
                        fichier.write(self._file.get(timeout=0.5))
                    except queue.Empty:
                        pass

        def callback(indata, frames, time, status):
            self._file.put(indata.copy())

        self.actif = True
        self.debut = dt.datetime.now()
        self._ecrivain = threading.Thread(target=ecrire, daemon=True)
        self._ecrivain.start()
        self._flux = sd.InputStream(samplerate=frequence, channels=1, dtype="int16", callback=callback)
        self._flux.start()

    def arreter(self):
        self._flux.stop()
        self._flux.close()
        self.actif = False
        self._ecrivain.join()  # attend que tout soit bien écrit
        return self.chemin, self.debut


# ---------------------------------------------------------------------------
# Transcription locale (Whisper MLX)
# ---------------------------------------------------------------------------

def transcrire(chemin_audio):
    """Retourne la transcription horodatée : '[mm:ss] texte' par ligne.
    Au premier usage d'un modèle, ses fichiers sont téléchargés (~/.cache/huggingface)."""
    import mlx_whisper  # import tardif : lourd à charger

    resultat = mlx_whisper.transcribe(
        str(chemin_audio),
        path_or_hf_repo=CONFIG["modele_whisper"],
        language=CONFIG["langue"],
    )
    lignes = []
    for seg in resultat.get("segments", []):
        texte = seg["text"].strip()
        if texte:
            m, s = divmod(int(seg["start"]), 60)
            lignes.append(f"[{m:02d}:{s:02d}] {texte}")
    return "\n".join(lignes)


# ---------------------------------------------------------------------------
# Analyse par Claude : compte rendu + classement
# ---------------------------------------------------------------------------

def trouver_claude_code():
    """Chemin du programme Claude Code, ou None s'il n'est pas installé."""
    candidats = [
        shutil.which("claude"),
        Path.home() / ".local" / "bin" / "claude",
        Path.home() / ".claude" / "local" / "claude",
        Path("/opt/homebrew/bin/claude"),
        Path("/usr/local/bin/claude"),
    ]
    for c in candidats:
        if c and Path(c).exists():
            return str(c)
    return None


def moteur_effectif():
    """Moteur réellement utilisé : 'abonnement', 'api', ou None si aucun n'est prêt."""
    choix = CONFIG.get("moteur", "auto")
    claude = trouver_claude_code()
    cle = CONFIG.get("cle_api_anthropic")
    if choix == "abonnement":
        return "abonnement" if claude else None
    if choix == "api":
        return "api" if cle else None
    return "abonnement" if claude else ("api" if cle else None)


def instructions_analyse(noms, projets):
    liste = "\n".join(f"- {nom}" + (f" : {desc}" if desc else "") for nom, desc in projets.items())
    return f"""Tu analyses des transcriptions automatiques d'appels et de rendez-vous pro (elles peuvent contenir des erreurs de reconnaissance).

Projets existants :
{liste or "(aucun projet créé pour l'instant)"}
- {PROJET_PAR_DEFAUT} : rien ne correspond clairement

Choisis le projet dont parle l'enregistrement, en t'appuyant sur les noms de clients, d'entreprises, de produits et les sujets évoqués. En cas de doute, prends "{PROJET_PAR_DEFAUT}".

Rédige le compte rendu en markdown, en français, sans titre principal, avec ces sections (omets celles qui seraient vides) :
## Contexte
## Points clés
## Décisions
## Actions à faire
(qui / quoi / deadline)
## Chiffres
(budget, prix, délais)
## Points flous à clarifier
## Mail de suivi
(court, vouvoiement)

N'invente rien : si une info est incertaine ou mal transcrite, signale-le."""


def analyser(transcription, date):
    """Rédige le compte rendu et choisit le projet. Retourne {projet, titre, compte_rendu}."""
    moteur = moteur_effectif()
    if not moteur:
        raise RuntimeError("aucun moteur d'analyse prêt : connecte ton abonnement Claude ou ajoute une clé API (Réglages)")

    projets = lister_projets()
    noms = list(projets.keys()) + [PROJET_PAR_DEFAUT]
    systeme = instructions_analyse(noms, projets)
    contenu = f"Date de l'enregistrement : {date:%d/%m/%Y %H:%M}\n\nTranscription :\n{transcription}"

    donnees = analyser_abonnement(systeme, contenu) if moteur == "abonnement" else analyser_api(systeme, contenu, noms)
    if donnees.get("projet") not in noms:
        donnees["projet"] = PROJET_PAR_DEFAUT
    for cle in ("titre", "compte_rendu"):
        if not str(donnees.get(cle, "")).strip():
            raise RuntimeError(f"réponse de Claude incomplète ({cle} manquant)")
    return donnees


def analyser_abonnement(systeme, contenu):
    """Analyse via Claude Code en mode non interactif, avec le compte Claude (Pro/Max) de l'utilisateur."""
    import tempfile

    claude = trouver_claude_code()
    consigne = (
        systeme
        + "\n\nRéponds UNIQUEMENT par un objet JSON valide, sans texte avant ni après, de la forme :\n"
        + '{"projet": "<nom exact d\'un des projets listés>", "titre": "<titre court, 3 à 8 mots>", '
        + '"compte_rendu": "<compte rendu en markdown>"}\n\n'
        + contenu
    )
    # Dossier vide : Claude Code n'a aucun fichier à lire, il se contente de répondre
    with tempfile.TemporaryDirectory() as dossier_vide:
        r = subprocess.run(
            [claude, "-p", "Suis les instructions reçues en entrée et réponds uniquement en JSON.",
             "--output-format", "json", "--model", CONFIG.get("modele_abonnement", "sonnet"),
             "--max-turns", "1"],
            input=consigne, capture_output=True, text=True, timeout=900, cwd=dossier_vide,
        )
    sortie = r.stdout.strip()
    if r.returncode != 0 and not sortie:
        detail = (r.stderr.strip().splitlines() or ["erreur inconnue"])[-1]
        raise RuntimeError(f"Claude Code : {detail} (es-tu bien connecté ? Réglages → Se connecter)")

    # Sortie JSON de Claude Code : la réponse du modèle est dans "result"
    texte = sortie
    try:
        enveloppe = json.loads(sortie)
        if isinstance(enveloppe, dict):
            if enveloppe.get("is_error"):
                raise RuntimeError(f"Claude Code : {enveloppe.get('result') or 'erreur'}")
            texte = enveloppe.get("result", "") or ""
    except json.JSONDecodeError:
        pass

    # On extrait l'objet JSON de la réponse (au cas où il serait entouré de texte ou de ```)
    debut, fin = texte.find("{"), texte.rfind("}")
    if debut == -1 or fin <= debut:
        raise RuntimeError("Claude n'a pas renvoyé de JSON exploitable")
    return json.loads(texte[debut:fin + 1])


def analyser_api(systeme, contenu, noms):
    """Analyse via l'API Anthropic (clé API, facturée à l'usage)."""
    import anthropic

    # Outil forcé = Claude renvoie forcément un JSON propre (projet, titre, compte rendu)
    outil = {
        "name": "classer_compte_rendu",
        "description": "Enregistre le compte rendu dans le bon projet.",
        "input_schema": {
            "type": "object",
            "properties": {
                "projet": {"type": "string", "enum": noms},
                "titre": {"type": "string", "description": "Titre court et précis, 3 à 8 mots"},
                "compte_rendu": {"type": "string", "description": "Compte rendu en markdown"},
            },
            "required": ["projet", "titre", "compte_rendu"],
        },
    }
    client = anthropic.Anthropic(api_key=CONFIG["cle_api_anthropic"])
    reponse = client.messages.create(
        model=CONFIG["modele_claude"],
        max_tokens=4096,
        system=systeme,
        tools=[outil],
        tool_choice={"type": "tool", "name": "classer_compte_rendu"},
        messages=[{"role": "user", "content": contenu}],
    )
    return next(b for b in reponse.content if b.type == "tool_use").input


# ---------------------------------------------------------------------------
# Rangement
# ---------------------------------------------------------------------------

def ranger(chemin_audio, date, transcription, analyse):
    dossier = DOSSIER_SORTIE / analyse["projet"]
    (dossier / "audio").mkdir(parents=True, exist_ok=True)

    base = f"{date:%Y-%m-%d_%Hh%M}_{slugifier(analyse['titre'])}"
    audio_final = chemin_libre(dossier / "audio" / f"{base}{chemin_audio.suffix.lower()}")
    shutil.move(str(chemin_audio), str(audio_final))

    md = chemin_libre(dossier / f"{base}.md")
    md.write_text(
        f"# {analyse['titre']}\n\n"
        f"- **Date** : {date:%d/%m/%Y à %H:%M}\n"
        f"- **Projet** : {analyse['projet']}\n"
        f"- **Audio** : [audio/{audio_final.name}](audio/{audio_final.name})\n\n"
        f"{analyse['compte_rendu'].strip()}\n\n"
        f"---\n\n## Transcription complète\n\n{transcription}\n",
        encoding="utf-8",
    )
    return md


# ---------------------------------------------------------------------------
# Données pour la fenêtre
# ---------------------------------------------------------------------------

MOTIF_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})_(\d{2})h(\d{2})")


def liste_comptes_rendus():
    """Tous les comptes rendus, du plus récent au plus ancien."""
    elements = []
    for d in DOSSIER_SORTIE.iterdir():
        if not d.is_dir() or d.name.startswith(("_", ".")):
            continue
        for md in d.glob("*.md"):
            premiere = md.read_text(encoding="utf-8").split("\n", 1)[0]
            titre = premiere[2:].strip() if premiere.startswith("# ") else md.stem
            m = MOTIF_DATE.match(md.name)
            date = dt.datetime(*map(int, m.groups())) if m else dt.datetime.fromtimestamp(md.stat().st_mtime)
            elements.append({
                "projet": d.name,
                "fichier": md.name,
                "titre": titre,
                "date": date.isoformat(timespec="minutes"),
            })
    elements.sort(key=lambda e: e["date"], reverse=True)
    return elements


def lire_compte_rendu(projet, fichier):
    if not fichier.endswith(".md") or "/" in fichier:
        raise ValueError("fichier invalide")
    chemin = dossier_projet(projet) / fichier
    return {"markdown": chemin.read_text(encoding="utf-8")}


def liste_projets_detaillee():
    resultat = []
    for nom, desc in lister_projets().items():
        resultat.append({"nom": nom, "description": desc, "nb": len(list((DOSSIER_SORTIE / nom).glob("*.md"))), "modifiable": True})
    resultat.append({
        "nom": PROJET_PAR_DEFAUT,
        "description": "Ce qui ne correspond à aucun projet.",
        "nb": len(list((DOSSIER_SORTIE / PROJET_PAR_DEFAUT).glob("*.md"))),
        "modifiable": False,
    })
    return resultat


def creer_projet(nom, description):
    nom = nom_valide(nom)
    dossier = DOSSIER_SORTIE / nom
    if dossier.exists():
        raise ValueError("ce projet existe déjà")
    dossier.mkdir()
    if description.strip():
        (dossier / "description.txt").write_text(description.strip() + "\n", encoding="utf-8")


def modifier_projet(ancien, nom, description):
    if ancien == PROJET_PAR_DEFAUT:
        raise ValueError("le projet Divers ne se modifie pas")
    dossier = dossier_projet(ancien)
    nom = nom_valide(nom)
    if nom != ancien:
        cible = DOSSIER_SORTIE / nom
        if cible.exists():
            raise ValueError("un projet porte déjà ce nom")
        dossier = dossier.rename(cible)
    fichier_desc = dossier / "description.txt"
    if description.strip():
        fichier_desc.write_text(description.strip() + "\n", encoding="utf-8")
    elif fichier_desc.exists():
        fichier_desc.unlink()


def lire_reglages():
    cle = CONFIG.get("cle_api_anthropic", "")
    return {
        "cle_definie": bool(cle),
        "cle_apercu": f"{cle[:7]}…{cle[-4:]}" if len(cle) > 12 else "",
        "modele_whisper": CONFIG["modele_whisper"],
        "modeles": MODELES_WHISPER,
        "dossier_actuel": str(DOSSIER_SORTIE),
        "dossier_personnalise": CONFIG.get("dossier_sortie", ""),
        "erreurs": compter_erreurs(),
        "moteur": CONFIG.get("moteur", "auto"),
        "moteur_effectif": moteur_effectif(),
        "claude_code": trouver_claude_code(),
    }


def ouvrir_terminal(commande):
    """Ouvre le Terminal et y lance une commande (installation / connexion de Claude Code)."""
    commande = commande.replace("\\", "\\\\").replace('"', '\\"')
    subprocess.run(["osascript",
                    "-e", f'tell application "Terminal" to do script "{commande}"',
                    "-e", 'tell application "Terminal" to activate'], check=False)


def sauver_reglages(donnees):
    redemarrage = False
    if donnees.get("cle", "").strip():
        CONFIG["cle_api_anthropic"] = donnees["cle"].strip()
    if donnees.get("moteur") in ("auto", "abonnement", "api"):
        CONFIG["moteur"] = donnees["moteur"]
    if donnees.get("modele_whisper") in {m["id"] for m in MODELES_WHISPER}:
        CONFIG["modele_whisper"] = donnees["modele_whisper"]
    if "dossier_sortie" in donnees and donnees["dossier_sortie"].strip() != CONFIG.get("dossier_sortie", ""):
        CONFIG["dossier_sortie"] = donnees["dossier_sortie"].strip()
        redemarrage = True  # les chemins sont calculés au démarrage
    sauver_config(CONFIG)
    return {"ok": True, "redemarrage": redemarrage}


# ---------------------------------------------------------------------------
# API locale (127.0.0.1 uniquement, jeton obligatoire)
# ---------------------------------------------------------------------------

JETON = secrets.token_urlsafe(24)


class Api(http.server.BaseHTTPRequestHandler):
    app = None  # renseigné au démarrage

    def log_message(self, *args):
        pass  # pas de log à chaque requête

    def _envoyer(self, code, corps, type_mime):
        self.send_response(code)
        self.send_header("Content-Type", type_mime)
        self.send_header("Content-Length", str(len(corps)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(corps)

    def _json(self, code, donnees):
        self._envoyer(code, json.dumps(donnees, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def _autorise(self, page):
        # Anti « DNS rebinding » : l'hôte doit être exactement 127.0.0.1:<port>
        if self.headers.get("Host") != f"127.0.0.1:{self.server.server_port}":
            return False
        # La page elle-même est servie sans jeton (elle le contient), l'API exige le jeton
        return page or secrets.compare_digest(self.headers.get("X-Jeton", ""), JETON)

    def _corps(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n)) if n else {}

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        page = url.path in ("/", "/index.html")
        if not self._autorise(page):
            return self._json(403, {"erreur": "accès refusé"})
        q = {k: v[0] for k, v in urllib.parse.parse_qs(url.query).items()}
        try:
            if page:
                html = (DOSSIER_CODE / "ui" / "index.html").read_text(encoding="utf-8").replace("__JETON__", JETON)
                return self._envoyer(200, html.encode(), "text/html; charset=utf-8")
            routes = {
                "/api/etat": lambda: self.app.etat(),
                "/api/comptes-rendus": liste_comptes_rendus,
                "/api/compte-rendu": lambda: lire_compte_rendu(q.get("projet", ""), q.get("fichier", "")),
                "/api/projets": liste_projets_detaillee,
                "/api/reglages": lire_reglages,
            }
            if url.path not in routes:
                return self._json(404, {"erreur": "introuvable"})
            self._json(200, routes[url.path]())
        except Exception as e:
            self._json(400, {"erreur": str(e)})

    def do_POST(self):
        if not self._autorise(False):
            return self._json(403, {"erreur": "accès refusé"})
        try:
            d = self._corps()
            if self.path == "/api/enregistrement":
                self.app.basculer_enregistrement()
                return self._json(200, self.app.etat())
            if self.path == "/api/projets":
                creer_projet(d.get("nom", ""), d.get("description", ""))
                return self._json(200, liste_projets_detaillee())
            if self.path == "/api/projets/modifier":
                modifier_projet(d.get("ancien", ""), d.get("nom", ""), d.get("description", ""))
                return self._json(200, liste_projets_detaillee())
            if self.path == "/api/reglages":
                return self._json(200, sauver_reglages(d))
            if self.path == "/api/erreurs/relancer":
                return self._json(200, {"relances": relancer_erreurs()})
            if self.path == "/api/claude-code":
                if d.get("action") == "installer":
                    # Installateur officiel, puis lancement de claude pour la connexion au compte
                    ouvrir_terminal("curl -fsSL https://claude.ai/install.sh | bash && ~/.local/bin/claude")
                else:
                    ouvrir_terminal(f"{trouver_claude_code() or 'claude'}")
                return self._json(200, {"ok": True})
            if self.path == "/api/maj/verifier":
                self.app.verifier_maj_maintenant()
                return self._json(200, self.app.etat())
            if self.path == "/api/maj":
                self.app.lancer_maj()
                return self._json(200, {"ok": True})
            if self.path == "/api/ouvrir":
                cible = d.get("cible")
                if cible == "dossier":
                    ouvrir_finder(DOSSIER_SORTIE)
                elif cible == "import":
                    ouvrir_finder(DOSSIER_IMPORT)
                elif cible == "erreurs":
                    ouvrir_finder(DOSSIER_ERREURS)
                elif cible == "console":
                    subprocess.run(["open", "https://console.anthropic.com/settings/keys"], check=False)
                elif cible == "compte-rendu":
                    chemin = dossier_projet(d.get("projet", "")) / d.get("fichier", "")
                    if chemin.suffix == ".md" and chemin.exists():
                        ouvrir_finder(chemin, reveler=True)
                return self._json(200, {"ok": True})
            self._json(404, {"erreur": "introuvable"})
        except Exception as e:
            self._json(400, {"erreur": str(e)})


# ---------------------------------------------------------------------------
# App barre de menus
# ---------------------------------------------------------------------------

class App(rumps.App):
    def __init__(self):
        super().__init__("MiroRecap", title="🎙", quit_button=None)
        self.enreg = Enregistreur()
        self.verrou = threading.Lock()   # l'enregistrement se pilote depuis le menu ET la fenêtre
        self.file_traitement = queue.Queue()
        self.en_file = set()             # fichiers déjà en file (évite les doublons)
        self.tailles = {}                # taille des fichiers d'import au dernier scan
        self.icloud_demandes = set()     # fichiers iCloud dont on a déjà demandé le téléchargement
        self.statut = ""                 # écrit par le thread de traitement, lu par l'UI
        self.dernier = None              # {"projet", "fichier", "titre"} du dernier compte rendu
        self.fenetre = None
        self.maj = None                  # numéro de la nouvelle version si disponible
        self.derniere_erreur = ""        # message de la dernière erreur de traitement
        self.maj_en_cours = False
        self._compteur = 0

        self.bouton = rumps.MenuItem("Démarrer l'enregistrement", callback=lambda _: self.basculer_enregistrement())
        self.item_dernier = rumps.MenuItem("Dernier compte rendu : —", callback=self.ouvrir_dernier)
        self.item_maj = rumps.MenuItem(f"Version {VERSION} · Rechercher une mise à jour", callback=self.clic_maj)
        self.menu = [
            self.bouton,
            rumps.MenuItem("Ouvrir MiroRecap", callback=lambda _: self.ouvrir_fenetre()),
            None,
            rumps.MenuItem("Ouvrir les comptes rendus dans le Finder", callback=lambda _: ouvrir_finder(DOSSIER_SORTIE)),
            rumps.MenuItem("Ouvrir le dossier d'import", callback=lambda _: ouvrir_finder(DOSSIER_IMPORT)),
            None,
            self.item_dernier,
            None,
            self.item_maj,
            rumps.MenuItem("Quitter MiroRecap", callback=self.quitter),
        ]

        # API locale sur un port libre choisi par le système
        Api.app = self
        self.serveur = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Api)
        threading.Thread(target=self.serveur.serve_forever, daemon=True).start()

        threading.Thread(target=self.travailleur, daemon=True).start()
        threading.Thread(target=self.boucle_maj, daemon=True).start()
        # L'UI de la barre de menus ne doit être modifiée que depuis le thread principal
        self.timer = rumps.Timer(self.tic, 1)
        self.timer.start()

        # Premier lancement (pas de clé) : on ouvre directement les réglages
        if moteur_effectif() is None:
            self.ouvrir_fenetre("reglages")

    # --- État partagé avec la fenêtre --------------------------------------

    def etat(self):
        duree = int((dt.datetime.now() - self.enreg.debut).total_seconds()) if self.enreg.actif else 0
        return {
            "enregistrement": self.enreg.actif,
            "duree": duree,
            "statut": self.statut,
            "en_attente": self.file_traitement.qsize(),
            "erreurs": compter_erreurs(),
            "cle_ok": moteur_effectif() is not None,
            "moteur": moteur_effectif(),
            "dernier": self.dernier,
            "version": VERSION,
            "maj": self.maj,
            "maj_en_cours": self.maj_en_cours,
            "derniere_erreur": self.derniere_erreur,
        }

    # --- Mises à jour ---------------------------------------------------------

    def verifier_maj_maintenant(self):
        """Compare la version locale à celle de GitHub. Notifie une seule fois par nouvelle version."""
        v = derniere_version()
        if v and version_tuple(v) > version_tuple(VERSION):
            if self.maj != v:
                notifier("Mise à jour disponible", f"MiroRecap {v} est sortie. 🎙 → Mettre à jour")
            self.maj = v
        return self.maj

    def boucle_maj(self):
        time.sleep(30)  # laisse l'app démarrer tranquillement
        while True:
            self.verifier_maj_maintenant()
            time.sleep(INTERVALLE_VERIF)

    def clic_maj(self, _):
        if self.maj:
            try:
                self.lancer_maj()
            except Exception as e:
                notifier("Mise à jour impossible pour l'instant", str(e))
        else:
            def verifier():
                if not self.verifier_maj_maintenant():
                    notifier("MiroRecap est à jour", f"Tu as la dernière version ({VERSION}).")
            threading.Thread(target=verifier, daemon=True).start()

    def lancer_maj(self):
        """Lance l'installateur en arrière-plan. Il arrête l'app, la met à jour
        (clé, projets et comptes rendus conservés) puis la relance."""
        if not self.maj:
            raise RuntimeError("aucune mise à jour disponible")
        if self.enreg.actif:
            raise RuntimeError("arrête d'abord l'enregistrement en cours")
        if self.statut or not self.file_traitement.empty():
            raise RuntimeError("attends la fin du traitement en cours")
        self.maj_en_cours = True
        notifier("Mise à jour en cours", "MiroRecap va se relancer tout seul dans quelques minutes.")
        journal = open(DOSSIER_APP / "maj.log", "w")
        # Session séparée : l'installateur survit à l'arrêt de l'app.
        # Si l'installation échoue, on relance au moins l'app existante.
        subprocess.Popen(
            ["/bin/bash", "-c",
             f'curl -fsSL "{URL_INSTALL}" | bash || open -a "$HOME/Applications/MiroRecap.app"'],
            stdin=subprocess.DEVNULL, stdout=journal, stderr=subprocess.STDOUT,
            start_new_session=True,
        )

    def basculer_enregistrement(self):
        with self.verrou:
            if not self.enreg.actif:
                self.enreg.demarrer()
            else:
                chemin, debut = self.enreg.arreter()
                self.en_file.add(chemin)
                self.file_traitement.put((chemin, debut))

    # --- Barre de menus -------------------------------------------------------

    def tic(self, _):
        if self.enreg.actif:
            m, s = divmod(int((dt.datetime.now() - self.enreg.debut).total_seconds()), 60)
            self.title = f"🔴 {m:02d}:{s:02d}"
            self.bouton.title = "Arrêter et analyser"
        else:
            self.title = f"⏳ {self.statut}" if self.statut else "🎙"
            self.bouton.title = "Démarrer l'enregistrement"

        if self.dernier:
            self.item_dernier.title = f"Dernier : {self.dernier['projet']} / {self.dernier['titre']}"

        if self.maj_en_cours:
            self.item_maj.title = "Mise à jour en cours…"
        elif self.maj:
            self.item_maj.title = f"Mettre à jour vers la version {self.maj}"

        # Scan du dossier d'import toutes les 5 secondes
        self._compteur += 1
        if self._compteur % 5 == 0:
            self.scanner_import()

    def ouvrir_fenetre(self, vue=""):
        # Une seule fenêtre : si elle est déjà ouverte, on la relance au premier plan
        if self.fenetre and self.fenetre.poll() is None:
            self.fenetre.terminate()
        url = f"http://127.0.0.1:{self.serveur.server_port}/" + (f"#{vue}" if vue else "")
        self.fenetre = subprocess.Popen([sys.executable, str(DOSSIER_CODE / "fenetre.py"), url])

    def ouvrir_dernier(self, _):
        if self.dernier:
            ouvrir_finder(DOSSIER_SORTIE / self.dernier["projet"] / self.dernier["fichier"], reveler=True)

    def quitter(self, _):
        if self.fenetre and self.fenetre.poll() is None:
            self.fenetre.terminate()
        rumps.quit_application()

    # --- Import automatique --------------------------------------------------

    def scanner_import(self):
        """Met en file les audios déposés dans _A-traiter une fois leur copie
        terminée (taille identique entre deux scans)."""
        for f in DOSSIER_IMPORT.iterdir():
            # Fichier iCloud pas encore téléchargé ('.nom.m4a.icloud') : on force le téléchargement
            if f.name.startswith(".") and f.name.endswith(".icloud"):
                reel = f.with_name(f.name[1:-len(".icloud")])
                if reel not in self.icloud_demandes:
                    self.icloud_demandes.add(reel)
                    subprocess.run(["brctl", "download", str(reel)], check=False)
                continue
            if f.name.startswith(".") or f.suffix.lower() not in EXTENSIONS_AUDIO or f in self.en_file:
                continue
            infos = f.stat()
            if infos.st_size > 0 and self.tailles.get(f) == infos.st_size:
                self.en_file.add(f)
                self.icloud_demandes.discard(f)
                date = dt.datetime.fromtimestamp(getattr(infos, "st_birthtime", infos.st_mtime))
                self.file_traitement.put((f, date))
            else:
                self.tailles[f] = infos.st_size

    # --- Traitement (thread de fond) ------------------------------------------

    def travailleur(self):
        while True:
            chemin, date = self.file_traitement.get()
            transcription = None
            try:
                self.statut = "Transcription…"
                transcription = transcrire(chemin)
                if not transcription:
                    raise ValueError("aucune parole détectée")
                self.statut = "Analyse…"
                analyse = analyser(transcription, date)
                md = ranger(chemin, date, transcription, analyse)
                self.dernier = {"projet": analyse["projet"], "fichier": md.name, "titre": analyse["titre"]}
                notifier(f"Classé dans {analyse['projet']}", analyse["titre"])
            except Exception as e:
                # Erreur complète dans app.log + gardée pour l'afficher dans les Réglages
                import traceback
                print(f"[{dt.datetime.now():%Y-%m-%d %H:%M:%S}] Erreur sur {chemin.name} :", flush=True)
                traceback.print_exc()
                sys.stdout.flush()
                self.derniere_erreur = f"{dt.datetime.now():%d/%m %H:%M} · {e}"
                # Rien n'est perdu : l'audio (et la transcription si dispo) part dans _Erreurs
                destination = chemin_libre(DOSSIER_ERREURS / chemin.name)
                if chemin.exists():
                    shutil.move(str(chemin), str(destination))
                if transcription:
                    destination.with_suffix(".txt").write_text(transcription, encoding="utf-8")
                notifier("Erreur de traitement", f"{chemin.name} : {e}")
            finally:
                self.en_file.discard(chemin)
                self.tailles.pop(chemin, None)
                self.statut = ""


if __name__ == "__main__":
    # Pas d'icône dans le Dock pour ce processus : il vit dans la barre de menus
    try:
        from AppKit import NSApplication
        NSApplication.sharedApplication().setActivationPolicy_(1)  # 1 = Accessory
    except Exception:
        pass
    App().run()
