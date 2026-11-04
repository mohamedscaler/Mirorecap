# 🎙 MiroRecap

Une app Mac qui enregistre tes appels et RDV et les transcrit sur ton Mac avec Whisper. Elle demande ensuite à Claude un compte rendu (points clés, actions, mail de suivi) et le range automatiquement dans le bon projet.

Tu la pilotes depuis l'icône 🎙 de la barre de menus ou depuis sa fenêtre, où tu enregistres, lis tes comptes rendus, gères tes projets et modifies tes réglages.

## Ce qu'il faut

- Un Mac avec puce Apple (M1 ou plus récent), sous macOS 13.5 minimum
- **Au choix, pour l'analyse par Claude :**
  - un abonnement Claude **Pro ou Max** : MiroRecap passe par Claude Code, sans clé API (recommandé) ;
  - ou une clé API Anthropic, facturée à l'usage, à créer sur [console.anthropic.com](https://console.anthropic.com).

## Installation

**Option 1 : double-clic**

1. Télécharge **Installer MiroRecap.command** depuis la page [Releases](https://github.com/mohamedscaler/Mirorecap/releases).
2. Double-clique dessus.
3. Comme le fichier n'est pas signé par Apple, macOS le bloque la première fois. Va dans **Réglages Système → Confidentialité et sécurité**, descends jusqu'au message sur MiroRecap, clique sur **Ouvrir quand même** et confirme.
4. Une fenêtre de Terminal s'ouvre et installe tout. Laisse-la travailler.

**Option 2 : Terminal**

Ouvre le Terminal (Cmd + Espace, tape « Terminal ») et colle :

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/mohamedscaler/Mirorecap/main/install-distant.sh)"
```

Dans les deux cas, ça installe tout, Homebrew et Whisper compris. Ton mot de passe Mac peut être demandé : il ne s'affiche pas quand tu tapes, c'est normal. Compte quelques minutes.

Au premier lancement, la fenêtre de MiroRecap s'ouvre sur les Réglages :
1. **Avec ton abonnement Claude** : clique sur **Installer et connecter**. Le Terminal s'ouvre et installe Claude Code. Quand il te le demande, choisis **Claude account** et connecte-toi dans le navigateur, puis tape `/exit`.
   **Ou avec une clé API** : colle-la dans « Clé API Anthropic ».
2. Accepte l'accès au micro et à iCloud Drive quand macOS le demande.

L'app se relance toute seule à chaque démarrage du Mac. La première transcription télécharge le modèle Whisper (internet nécessaire), donc elle prend plus de temps. Ensuite, la transcription se fait hors ligne.

## Utilisation

- **Ouvrir la fenêtre** : 🎙 → Ouvrir MiroRecap.
- **Enregistrer** : avec le gros bouton rouge de la fenêtre, ou 🎙 → Démarrer l'enregistrement. Le compte rendu arrive quelques minutes après l'arrêt, avec une notification.
- **Depuis l'iPhone** : enregistre avec le Dictaphone, ou enregistre un appel. Ensuite Partager → Enregistrer dans Fichiers → iCloud Drive → Comptes-rendus → `_A-traiter`. Le Mac le traite automatiquement, à condition d'être allumé.
- **Projets** : dans la fenêtre, onglet Projets. Donne à chaque projet un nom et une phrase de description, Claude s'en sert pour classer. Si rien ne colle, le compte rendu va dans `Divers`.
- **Lire tes comptes rendus** : dans la fenêtre, onglet Comptes rendus (avec recherche, filtre par projet et bouton « Copier »), ou dans l'app Fichiers de l'iPhone.

## Astuces

- Dans le Finder, fais un clic droit sur le dossier Comptes-rendus → **Conserver téléchargé**. Sinon, les fichiers envoyés depuis l'iPhone peuvent mettre du temps à arriver.
- Si un traitement échoue, l'audio est gardé. Va dans Réglages → Erreurs → Relancer le traitement.
- Dans les Réglages, tu peux aussi choisir le modèle Whisper : Turbo (recommandé), Large v3 (plus précis, plus lent) ou Small (très rapide).
- En cas de problème, le journal se trouve dans `~/Library/Application Support/MiroRecap/app.log`.

## ⚠️ Légal

En France, préviens toujours ton interlocuteur avant d'enregistrer une conversation.

## Mise à jour / désinstallation

- **Mettre à jour** : relance l'installateur. Ta clé API et tes projets sont conservés.
- **Désinstaller** :

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/mohamedscaler/Mirorecap/main/desinstaller.sh)"
```

Tes comptes rendus sont conservés.
