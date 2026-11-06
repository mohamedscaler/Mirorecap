# MiroRecap — guide pour Claude

App Mac (Apple Silicon) : enregistre appels et rendez-vous (micro + son de l'ordinateur), transcrit
en local avec Whisper (MLX), puis l'IA (Claude par abonnement ou clé API, ou ChatGPT) rédige le compte
rendu et le range dans le bon projet (sous-dossiers de `Comptes-rendus`, iCloud Drive par défaut).
Une version Windows est en préparation : tout ce qui touche au système passe par `app/plateforme/`.

## Structure

- `app/app.py` — cœur : enregistrement, file de traitement, transcription, analyse IA, rangement,
  mises à jour, API locale (127.0.0.1, jeton `X-Jeton`) utilisée par la fenêtre.
- `app/demarrage.py` — démarreur livré dans l'app : choisit le code à lancer (celui de l'app ou une
  mise à jour légère dans `~/Library/Application Support/MiroRecap/code/actuel`), revient en arrière
  si le nouveau code plante. Fait partie du moteur.
- `app/fenetre.py` — fenêtre pywebview (processus séparé) qui affiche `app/ui/index.html`.
- `app/ui/index.html` — toute l'interface (HTML/CSS/JS dans un seul fichier).
- `app/plateforme/` — code propre au système. `__init__.py` choisit les modules ; sur Mac :
  `systeme_macos.py` (dossiers, notifications, Finder, Corbeille, Rappels, démarrage), `audio_macos.py`
  (afconvert, outil de capture), `whisper_mlx.py`, `menus_macos.py` (icône rumps), `fenetre_macos.py` (Cocoa).
  Dans app.py, toujours appeler `plateforme.audio.…`, `plateforme.systeme.…` (jamais `audio`/`systeme`
  seuls : des variables locales portent ces noms).
- `app/capture-systeme.m` — outil natif qui capte le son de l'ordinateur (voix des interlocuteurs).
- `app/lanceur.m` — exécutable natif de l'app (notifications, Dock, lance Python).
- `app/mcp_serveur.py` — connecteur MCP : Claude lit les comptes rendus.
- `build/construire.sh` — construit MiroRecap.app + DMG (GitHub Actions, `dmg.yml`).
- `build/paquet_code.sh` — mise à jour légère `code.zip` + `maj.json` (`code.yml`).
- `docs/` — site de présentation (GitHub Pages).

## Règle MOTEUR (à vérifier à chaque changement)

- **Code seul** (app.py, fenetre.py, plateforme/, ui/, mcp_serveur.py) → augmenter `app/VERSION`
  uniquement. Les apps installées se mettent à jour avec `code.zip`.
- **Moteur** (`app/requirements.txt`, `app/lanceur.m`, `app/capture-systeme.m`, `app/demarrage.py`,
  `build/construire.sh`, Info.plist) → augmenter aussi `app/MOTEUR` : les apps attendent le DMG.
- Un nouveau fichier de code doit être ajouté à la fois dans `construire.sh` et `paquet_code.sh`.

## Workflow

- `main` = production : `install-distant.sh` et les releases partent de main. **Jamais de travail
  direct sur main.** Travailler sur `dev` (ou une branche dédiée).
- dev → tests (agent `testeur`) → relecture (agent `relecteur`) → PR vers main → release `vX.Y`
  sur GitHub (les workflows construisent code.zip et le DMG).
- Ne rien publier (push sur main, release) sans l'accord de l'utilisateur.

## Tester sans toucher à l'app de l'utilisateur

- Python de l'app (tous les modules) : `/Applications/MiroRecap.app/Contents/Resources/python/bin/python3`.
- Lancer le code dans un dossier isolé : `HOME=<dossier temporaire>` avec un `config.json` minimal
  (`accueil_fait`, `cle_api_anthropic`, `detection_appels: "rien"`, `maj_auto: false`, `dossier_sortie`),
  et `HF_HUB_CACHE=~/.cache/huggingface/hub` pour réutiliser les modèles Whisper déjà téléchargés.
- Voix factices : `say -v Thomas -o x.aiff "…"` puis `afconvert -f WAVE -d LEI16@16000 -c 1`.
- Ne jamais lancer une deuxième instance complète (barre des menus) pendant que l'app tourne.

## Conventions

- Code, commentaires, messages et commits en français, dans le ton simple de l'existant.
- Pas de nouvelle dépendance sans vraie raison (elle impose un nouveau MOTEUR et un DMG).
- Textes visibles par l'utilisateur : clairs, sans jargon, avec l'action à faire.
