#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Connecteur MiroRecap pour Claude (serveur MCP, protocole JSON-RPC sur stdin/stdout).

Claude (app Mac, Claude Code) le lance de lui-même et l'interroge pour lire tes
comptes rendus : projets, recherche, actions à faire, points d'étape.
Lecture seule. Fonctionne même quand MiroRecap est fermé. Aucune dépendance.
"""
import json
import re
import sys
from datetime import datetime
from pathlib import Path

VERSION_PROTOCOLE = "2025-06-18"
DOSSIER_APP = Path.home() / "Library" / "Application Support" / "MiroRecap"
ICLOUD = Path.home() / "Library" / "Mobile Documents" / "com~apple~CloudDocs"
MOTIF_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})_(\d{2})h(\d{2})")


# --- Lecture des comptes rendus ---------------------------------------------------

def dossier_sortie():
    """Même règle que l'app : dossier choisi, sinon iCloud Drive, sinon ~/Comptes-rendus."""
    try:
        config = json.loads((DOSSIER_APP / "config.json").read_text(encoding="utf-8"))
    except Exception:
        config = {}
    if config.get("dossier_sortie"):
        return Path(config["dossier_sortie"]).expanduser()
    if ICLOUD.exists():
        return ICLOUD / "Comptes-rendus"
    return Path.home() / "Comptes-rendus"


def projets():
    racine = dossier_sortie()
    if not racine.exists():
        return []
    return sorted((d for d in racine.iterdir() if d.is_dir() and not d.name.startswith(("_", "."))),
                  key=lambda d: d.name.lower())


def fichiers_cr(dossier):
    return sorted((f for f in dossier.glob("*.md") if not f.name.startswith("_")), key=lambda f: f.name, reverse=True)


def date_cr(f):
    m = MOTIF_DATE.match(f.name)
    return datetime(*map(int, m.groups())) if m else datetime.fromtimestamp(f.stat().st_mtime)


def titre_cr(texte, f):
    premiere = texte.split("\n", 1)[0]
    return premiere[2:].strip() if premiere.startswith("# ") else f.stem


def sans_transcription(texte):
    return texte.split("\n---\n## Transcription complète")[0].split("\n---\n\n## Transcription complète")[0]


def section(texte, titre):
    m = re.search(rf"^## {re.escape(titre)}\s*\n(.*?)(?=^## |\n---\n|\Z)", texte, flags=re.M | re.S)
    return m.group(1).strip() if m else ""


def bloc_contexte(texte):
    m = re.search(r"```xml\n(.*?)\n```", texte, flags=re.S)
    return m.group(1).strip() if m else ""


def trouver_projet(nom):
    for d in projets():
        if d.name.lower() == (nom or "").strip().lower():
            return d
    noms = ", ".join(d.name for d in projets())
    raise ValueError(f"projet « {nom} » introuvable. Projets existants : {noms}")


def tous_les_cr(projet=None, depuis=None):
    dossiers = [trouver_projet(projet)] if projet else projets()
    elements = []
    for d in dossiers:
        for f in fichiers_cr(d):
            date = date_cr(f)
            if depuis and date < depuis:
                continue
            elements.append((date, d, f))
    elements.sort(key=lambda e: e[0], reverse=True)
    return elements


def lire_date(texte):
    if not texte:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(texte.strip(), fmt)
        except ValueError:
            pass
    raise ValueError("date attendue au format AAAA-MM-JJ")


# --- Outils -----------------------------------------------------------------------

def outil_lister_projets(_):
    lignes = []
    for d in projets():
        crs = fichiers_cr(d)
        desc = (d / "description.txt").read_text(encoding="utf-8").strip() if (d / "description.txt").exists() else ""
        dernier = f", dernier appel le {date_cr(crs[0]):%d/%m/%Y}" if crs else ""
        point = " · point d'étape disponible" if (d / "_synthese.md").exists() else ""
        lignes.append(f"- {d.name} : {len(crs)} compte(s) rendu(s){dernier}{point}" + (f"\n  {desc}" if desc else ""))
    return "\n".join(lignes) or "Aucun projet pour l'instant."


def outil_derniers(args):
    nombre = max(1, min(int(args.get("nombre") or 5), 30))
    elements = tous_les_cr(args.get("projet"), lire_date(args.get("depuis")))[:nombre]
    if not elements:
        return "Aucun compte rendu trouvé."
    morceaux = []
    for date, d, f in elements:
        texte = f.read_text(encoding="utf-8")
        resume = bloc_contexte(texte) or sans_transcription(texte)[:1500]
        morceaux.append(f"### {titre_cr(texte, f)}\nprojet: {d.name} · date: {date:%d/%m/%Y %H:%M} · fichier: {f.name}\n{resume}")
    return "\n\n".join(morceaux)


def outil_chercher(args):
    requete = (args.get("requete") or "").strip()
    if not requete:
        raise ValueError("requete vide")
    termes = [t for t in re.findall(r"\w+", requete.lower()) if len(t) > 1]
    resultats = []
    for date, d, f in tous_les_cr(args.get("projet"), lire_date(args.get("depuis"))):
        texte = f.read_text(encoding="utf-8")
        bas = texte.lower()
        titre = titre_cr(texte, f)
        # Score : titre et compte rendu comptent plus que la transcription
        resume = sans_transcription(texte).lower()
        score = sum(5 * titre.lower().count(t) + 2 * resume.count(t) + bas.count(t) for t in termes)
        if requete.lower() in bas:
            score += 10
        if not score:
            continue
        i = min((bas.find(t) for t in termes if t in bas), default=0)
        extrait = re.sub(r"\s+", " ", texte[max(0, i - 160): i + 240]).strip()
        resultats.append((score, date, d, f, titre, extrait))
    resultats.sort(key=lambda r: (r[0], r[1]), reverse=True)
    limite = max(1, min(int(args.get("limite") or 8), 30))
    if not resultats:
        return f"Aucun compte rendu ne parle de « {requete} »."
    return "\n\n".join(f"- {titre} (projet: {d.name}, {date:%d/%m/%Y}, fichier: {f.name})\n  … {extrait} …"
                       for _, date, d, f, titre, extrait in resultats[:limite])


def outil_lire(args):
    d = trouver_projet(args.get("projet"))
    nom = Path(args.get("fichier") or "").name
    f = d / nom
    if not nom.endswith(".md") or not f.exists():
        raise ValueError(f"compte rendu « {nom} » introuvable dans {d.name}")
    texte = f.read_text(encoding="utf-8")
    return texte if args.get("avec_transcription") else sans_transcription(texte)


def outil_actions(args):
    morceaux = []
    for date, d, f in tous_les_cr(args.get("projet"), lire_date(args.get("depuis")))[:40]:
        texte = f.read_text(encoding="utf-8")
        actions = section(texte, "Actions à faire")
        if actions:
            morceaux.append(f"### {d.name} · {titre_cr(texte, f)} ({date:%d/%m/%Y})\n{actions}")
    return "\n\n".join(morceaux) or "Aucune action trouvée."


def outil_point(args):
    d = trouver_projet(args.get("projet"))
    parties = []
    if (d / "description.txt").exists():
        parties.append("Description : " + (d / "description.txt").read_text(encoding="utf-8").strip())
    if (d / "_synthese.md").exists():
        synthese = re.sub(r"<!--.*?-->", "", (d / "_synthese.md").read_text(encoding="utf-8"), flags=re.S).strip()
        parties.append("Point d'étape (rédigé par MiroRecap) :\n" + synthese)
    parties.append("Derniers appels :\n" + outil_derniers({"projet": d.name, "nombre": 5}))
    return "\n\n".join(parties)


OUTILS = {
    "lister_projets": (outil_lister_projets, "Liste les projets MiroRecap (clients, sujets) avec leur description, le nombre de comptes rendus et la date du dernier appel.", {}),
    "derniers_comptes_rendus": (outil_derniers, "Résumés des derniers appels et rendez-vous enregistrés (tous projets ou un seul), du plus récent au plus ancien.", {
        "projet": {"type": "string", "description": "Nom du projet (optionnel)"},
        "nombre": {"type": "integer", "description": "Combien (5 par défaut, 30 max)"},
        "depuis": {"type": "string", "description": "Date AAAA-MM-JJ (optionnel)"}}),
    "chercher_comptes_rendus": (outil_chercher, "Recherche dans tous les comptes rendus et transcriptions d'appels (nom de client, sujet, montant, mot-clé). Renvoie les plus pertinents avec un extrait.", {
        "requete": {"type": "string", "description": "Mots à chercher"},
        "projet": {"type": "string", "description": "Limiter à un projet (optionnel)"},
        "depuis": {"type": "string", "description": "Date AAAA-MM-JJ (optionnel)"},
        "limite": {"type": "integer", "description": "Nombre de résultats (8 par défaut)"}}),
    "lire_compte_rendu": (outil_lire, "Lit un compte rendu complet (décisions, actions, chiffres, mail préparé). Transcription incluse sur demande.", {
        "projet": {"type": "string"}, "fichier": {"type": "string", "description": "Nom du fichier .md"},
        "avec_transcription": {"type": "boolean", "description": "Inclure la transcription mot à mot"}}),
    "actions_a_faire": (outil_actions, "Liste les actions à faire relevées dans les appels (qui / quoi / échéance), tous projets ou un seul.", {
        "projet": {"type": "string", "description": "Nom du projet (optionnel)"},
        "depuis": {"type": "string", "description": "Date AAAA-MM-JJ (optionnel)"}}),
    "point_projet": (outil_point, "Fait le point sur un projet : description, point d'étape et derniers appels.", {
        "projet": {"type": "string", "description": "Nom du projet"}}),
}
REQUIS = {"chercher_comptes_rendus": ["requete"], "lire_compte_rendu": ["projet", "fichier"], "point_projet": ["projet"]}


# --- Protocole MCP (JSON-RPC 2.0, un message JSON par ligne) ----------------------

def liste_outils():
    return [{"name": nom, "description": desc,
             "inputSchema": {"type": "object", "properties": props, "required": REQUIS.get(nom, [])},
             "annotations": {"readOnlyHint": True}}
            for nom, (_, desc, props) in OUTILS.items()]


def traiter(message):
    methode, ident = message.get("method"), message.get("id")
    if ident is None:
        return None   # notification (initialized, cancelled…) : pas de réponse
    if methode == "initialize":
        version = (message.get("params") or {}).get("protocolVersion") or VERSION_PROTOCOLE
        resultat = {"protocolVersion": version, "capabilities": {"tools": {}},
                    "serverInfo": {"name": "mirorecap", "version": "1.0"},
                    "instructions": "Comptes rendus d'appels et de rendez-vous pro de l'utilisateur, classés par projet "
                                    "(clients). Utilise ces outils pour retrouver ce qui a été dit, décidé ou promis."}
    elif methode == "tools/list":
        resultat = {"tools": liste_outils()}
    elif methode == "tools/call":
        params = message.get("params") or {}
        nom, args = params.get("name"), params.get("arguments") or {}
        if nom not in OUTILS:
            return {"jsonrpc": "2.0", "id": ident, "error": {"code": -32602, "message": f"outil inconnu : {nom}"}}
        try:
            texte = OUTILS[nom][0](args)
            resultat = {"content": [{"type": "text", "text": texte[:60000]}], "isError": False}
        except Exception as e:
            resultat = {"content": [{"type": "text", "text": f"Erreur : {e}"}], "isError": True}
    elif methode == "ping":
        resultat = {}
    else:
        return {"jsonrpc": "2.0", "id": ident, "error": {"code": -32601, "message": f"méthode inconnue : {methode}"}}
    return {"jsonrpc": "2.0", "id": ident, "result": resultat}


def main():
    for ligne in sys.stdin:
        ligne = ligne.strip()
        if not ligne:
            continue
        try:
            message = json.loads(ligne)
        except json.JSONDecodeError:
            continue
        messages = message if isinstance(message, list) else [message]
        reponses = [r for r in (traiter(m) for m in messages) if r]
        if reponses:
            sys.stdout.write(json.dumps(reponses if isinstance(message, list) else reponses[0], ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
