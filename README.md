# 🎙 MiroRecap

Une app Mac qui enregistre tes appels et RDV et les transcrit sur ton Mac avec Whisper. Elle demande ensuite à Claude un compte rendu (points clés, actions, mail de suivi) et le range automatiquement dans le bon projet.

Tu la pilotes depuis l'icône 🎙 de la barre de menus ou depuis sa fenêtre, où tu enregistres, lis tes comptes rendus, gères tes projets et modifies tes réglages.

## Ce qu'il faut

- Un Mac avec puce Apple (M1 ou plus récent), sous macOS 13.5 minimum
- **Pour l'analyse par Claude, au choix :**
  - un abonnement Claude **Pro ou Max** : MiroRecap passe par Claude Code, sans clé API (recommandé) ;
  - ou une clé API Anthropic, facturée à l'usage, à créer sur [console.anthropic.com](https://console.anthropic.com).

## Installation

1. Télécharge **MiroRecap.dmg** depuis la [dernière release](https://github.com/mohamedscaler/Mirorecap/releases/latest).
2. Ouvre-le et glisse **MiroRecap** dans le dossier **Applications**.
3. Ouvre MiroRecap depuis Applications ou le Launchpad.
4. **Première ouverture uniquement** : comme l'app n'est pas signée par Apple, macOS la bloque. Clique sur **OK**, puis va dans **Réglages Système → Confidentialité et sécurité**. Descends jusqu'au message sur MiroRecap, clique sur **Ouvrir quand même** et confirme.

Rien d'autre à installer : Python, Whisper et tous les modules sont dans l'app.

Au premier lancement, la fenêtre s'ouvre sur les Réglages :
1. **Avec ton abonnement Claude** : clique sur **Installer et connecter**. Le Terminal s'ouvre et installe Claude Code. Quand il te le demande, choisis **Claude account** et connecte-toi dans le navigateur, puis tape `/exit`.
   **Ou avec une clé API** : colle-la dans « Clé API Anthropic ».
2. Accepte l'accès au micro et à iCloud Drive quand macOS le demande.
   Au premier enregistrement, macOS demande aussi « Enregistrement de l'écran et audio système » : c'est ce qui capte la voix de tes interlocuteurs en visio (seul l'audio est gardé). Accepte puis relance MiroRecap.

MiroRecap se lance tout seul à chaque démarrage du Mac. La première transcription télécharge le modèle Whisper (internet nécessaire), donc elle prend plus de temps. Ensuite, la transcription se fait hors ligne.

## Utilisation

- **Ouvrir la fenêtre** : 🎙 → Ouvrir MiroRecap.
- **Enregistrer** : avec le bouton rouge de la fenêtre, ou 🎙 → Démarrer l'enregistrement. Le compte rendu arrive quelques minutes après l'arrêt, avec une notification.
- **Depuis l'iPhone** : enregistre avec le Dictaphone, ou enregistre un appel. Ensuite Partager → Enregistrer dans Fichiers → iCloud Drive → Comptes-rendus → `_A-traiter`. Le Mac le traite automatiquement, à condition d'être allumé.
- **Projets** : onglet Projets. Donne à chaque projet un nom et une phrase de description, Claude s'en sert pour classer. Si rien ne colle, le compte rendu va dans `Divers`.
- **Lire tes comptes rendus** : onglet Comptes rendus (recherche, filtre par projet, bouton « Copier »), ou dans l'app Fichiers de l'iPhone.

## Mises à jour

MiroRecap vérifie toutes les 6 heures s'il existe une nouvelle version. Si oui, un bandeau s'affiche : un clic suffit, et l'app se met à jour puis se relance toute seule. Tes réglages, projets et comptes rendus sont conservés.

## Astuces

- Dans le Finder, fais un clic droit sur le dossier Comptes-rendus → **Conserver téléchargé**. Sinon, les fichiers envoyés depuis l'iPhone peuvent mettre du temps à arriver.
- Si un traitement échoue, l'audio est gardé. Va dans Réglages → Erreurs : le message s'affiche, et le bouton **Relancer** retraite le fichier.
- En cas de problème, le journal se trouve dans `~/Library/Application Support/MiroRecap/app.log`.

## ⚠️ Légal

En France, préviens toujours ton interlocuteur avant d'enregistrer une conversation.

## Désinstaller

Quitte MiroRecap (🎙 → Quitter), puis mets **MiroRecap** à la corbeille depuis Applications. Pour tout effacer, réglages compris (tes comptes rendus restent) :

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/mohamedscaler/Mirorecap/main/desinstaller.sh)"
```

---

### Publier une nouvelle version (pour le mainteneur)

Sur GitHub : **Releases → Draft a new release**, crée un tag `vX.Y` plus grand que le précédent, puis clique sur **Publish release**. GitHub construit tout seul `MiroRecap.dmg` (environ 3 minutes) et l'ajoute à la release. Les apps installées proposent alors la mise à jour.
