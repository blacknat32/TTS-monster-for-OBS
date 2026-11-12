# Brain-Rot Simulator — TTS Monster pour OBS

[![Tests](https://github.com/blacknat32/TTS-monster-for-OBS/actions/workflows/tests.yml/badge.svg)](https://github.com/blacknat32/TTS-monster-for-OBS/actions/workflows/tests.yml)

Une application web qui transforme un message texte en voix grâce à l'API
[TTS Monster](https://tts.monster), puis le **joue directement dans OBS**.

Tu écris un message, tu choisis une ou plusieurs voix, tu glisses des effets
sonores au milieu, et le tout est assemblé en un seul clip envoyé à OBS en un
clic.

```
{SpongeBob} Salut le chat ! {vine-boom} {Kermit} Bienvenue dans le stream.
```

> SpongeBob dit « Salut le chat ! », on entend le *vine boom*, puis Kermit dit
> « Bienvenue dans le stream. »

Tes **viewers Twitch** peuvent aussi l'utiliser : ils tapent `!tts …` dans le
chat (ou utilisent une récompense de points de chaîne), et tu gardes le
contrôle depuis un **panneau de modération**, en validation manuelle ou
automatique. Voir [Bot Twitch et modération](#bot-twitch-et-modération).

## Sommaire

- [Fonctionnalités](#fonctionnalités)
- [Comment ça marche](#comment-ça-marche)
- [Prérequis](#prérequis)
- [Installation](#installation)
- [Configuration](#configuration)
- [Utilisation](#utilisation)
- [Bot Twitch et modération](#bot-twitch-et-modération)
- [Mettre à jour le projet](#mettre-à-jour-le-projet)
- [Dépannage](#dépannage)
- [Pour les développeurs](#pour-les-développeurs)
- [Idées pour la suite](#idées-pour-la-suite)

## Fonctionnalités

- **15 voix TTS Monster** (SpongeBob, Kermit, Trump, Dagoth…), changeables au
  milieu d'un message.
- **Effets sonores** : chaque fichier audio de `TTS/media/Sound_Bites` devient
  une balise utilisable dans le message.
- **Envoi automatique dans OBS** via OBS WebSocket : la source média est mise à
  jour puis lancée, sans rien toucher dans OBS.
- **Volume homogène** : dans un message avec balises, chaque morceau (voix et
  sons) est normalisé à −16 LUFS pour qu'aucun ne soit trop fort ou trop faible.
- **Interface pratique** : balises insérables au clic, recherche de sons,
  compteur de caractères, affichage adapté au mobile.
- **File d'attente** des 10 derniers clips, avec protection contre l'envoi du
  même message deux fois.
- **Nettoyage automatique** des clips générés. Les sons de `Sound_Bites` ne sont
  jamais supprimés.
- **Bot Twitch** : les viewers lancent des TTS avec une commande du chat
  (`!tts`) ou une récompense de points de chaîne.
- **Panneau de modération** : validation manuelle ou automatique, file de
  lecture, historique, mots interdits, cooldown et bannissement.

## Comment ça marche

```
 Page web (/message/)
        │  message + balises
        ▼
 Django ── analyse les balises {voix} et {son}
        │
        ├─▶ API TTS Monster : génère chaque morceau de texte avec sa voix
        ├─▶ Sound_Bites     : récupère les effets sonores
        │
        ▼
 FFmpeg ── assemble et normalise les morceaux en un seul fichier .wav
        │
        ▼
 OBS WebSocket ── met le fichier dans la source « TTS Audio » et lance la lecture
```

- Un message **sans balise** est généré en un seul morceau par TTS Monster et
  envoyé tel quel à OBS : FFmpeg n'intervient pas.
- FFmpeg est **installé automatiquement** avec les dépendances Python (paquet
  `imageio-ffmpeg`). Il n'y a rien à installer à part, ni à ajouter au `PATH`.
  Si un FFmpeg est déjà présent dans ton `PATH`, c'est lui qui est utilisé.
- Le fichier audio est enregistré sur le disque puis lu par OBS : **OBS doit
  donc tourner sur le même PC que l'application**.

## Prérequis

| Outil | Pourquoi |
|---|---|
| [Python 3.12+](https://www.python.org/downloads/) | Fait tourner l'application (Django 6). |
| [OBS Studio 28+](https://obsproject.com/) | Lit le son. Le serveur WebSocket est intégré depuis la version 28. |
| Une clé API [TTS Monster](https://console.tts.monster) | Génère les voix. Chaque message consomme des caractères de ton quota. |

## Installation

Toutes les commandes se lancent **depuis la racine du projet**.

Quand une commande diffère selon le système, elle est donnée pour
**Windows (PowerShell)** et pour **Linux / macOS** (bash, zsh). Les autres
fonctionnent telles quelles partout : PowerShell accepte aussi les `/` dans les
chemins.

### 1. Récupérer le projet

```bash
git clone https://github.com/blacknat32/TTS-monster-for-OBS.git
cd TTS-monster-for-OBS
```

### 2. Créer et activer un environnement virtuel

Le venv isole les dépendances du projet du reste de ton PC.

**Windows (PowerShell)**

```powershell
python -m venv myenv
.\myenv\Scripts\Activate.ps1
```

> Si PowerShell bloque l'activation, utilise l'invite de commandes (`cmd`) avec
> `myenv\Scripts\activate.bat`.

**Linux / macOS**

```bash
python3 -m venv myenv
source myenv/bin/activate
```

> Sous Debian / Ubuntu, si `venv` est introuvable, installe-le d'abord :
> `sudo apt install python3-venv`.

Dans les deux cas, le début de la ligne du terminal doit afficher `(myenv)`.
Une fois le venv activé, la commande `python` désigne le Python du venv, y
compris sous Linux et macOS.

### 3. Installer les dépendances

```bash
python -m pip install -r requirements.txt
```

Cette commande installe Django, le client OBS WebSocket et FFmpeg.

### 4. Créer la base de données

À faire une seule fois :

```bash
python TTS/manage.py migrate
```

### 5. Créer tes fichiers de configuration

**Windows (PowerShell)**

```powershell
Copy-Item .env.example .env
Copy-Item "TTS/TTS/config file.example" "TTS/TTS/config file"
```

**Linux / macOS**

```bash
cp .env.example .env
cp "TTS/TTS/config file.example" "TTS/TTS/config file"
```

Remplis-les ensuite comme expliqué dans [Configuration](#configuration).

## Configuration

L'application utilise deux fichiers **personnels**. Ils sont ignorés par Git :
tes secrets ne seront jamais publiés, et chacun garde ses propres réglages.

| Fichier | Contenu | Modèle (versionné) |
|---|---|---|
| `.env` | Ta clé API TTS Monster | `.env.example` |
| `TTS/TTS/config file` | Connexion à ton OBS | `TTS/TTS/config file.example` |

### La clé API TTS Monster (`.env`)

Ouvre `.env` à la racine du projet et colle ta clé après le `=` :

```
TTS_MONSTER_API_KEY=ttsm_ta_cle
```

Ta clé se trouve sur [console.tts.monster](https://console.tts.monster).
Ne la mets **jamais** directement dans le code.

> Une variable d'environnement système `TTS_MONSTER_API_KEY` déjà définie est
> prioritaire sur le `.env`.

### La connexion à OBS (`TTS/TTS/config file`)

```toml
[connection]
host = "localhost"      # OBS tourne sur le même PC
port = 4455             # port du serveur WebSocket d'OBS
password = ""           # mot de passe WebSocket d'OBS ("" si désactivé)

[audio]
input_name = "TTS Audio"  # nom exact de la source média dans OBS
```

Le port et le mot de passe se trouvent dans OBS : **Outils → Paramètres du
serveur WebSocket → Afficher les informations de connexion**.

### Préparer OBS

1. **Outils → Paramètres du serveur WebSocket** : coche *Activer le serveur
   WebSocket*.
2. Dans ta scène, ajoute une **Source média** nommée exactement `TTS Audio`
   (ou le nom choisi dans `input_name`).
3. **Mélangeur audio → ⋮ → Propriétés audio avancées** : sur la ligne
   `TTS Audio`, règle *Monitoring audio* sur **Monitoring et sortie**.

> ⚠️ L'étape 3 est indispensable : avec *Monitoring désactivé*, le son part
> dans le stream, mais **tu ne l'entends pas** chez toi.

## Utilisation

### Lancer l'application

1. Lance OBS.
2. Dans un terminal, à la racine du projet, active le venv :

   - Windows (PowerShell) : `.\myenv\Scripts\Activate.ps1`
   - Linux / macOS : `source myenv/bin/activate`

3. Lance le serveur :

   ```bash
   python TTS/manage.py runserver
   ```

4. Ouvre [http://127.0.0.1:8000/message/](http://127.0.0.1:8000/message/).

Pour arrêter le serveur : `Ctrl + C` dans le terminal.

### L'interface

- **Message** : écris ton texte, puis clique sur **Envoyer sur OBS ▶**. Le
  compteur indique le nombre de caractères utilisés (1000 maximum).
- **Voix** 🎙 et **Sons** 🔊 : un clic sur un bouton insère la balise à la fin
  du message. La barre de recherche filtre les sons par nom.
- **File d'attente** : les 10 derniers clips envoyés pendant ta session.

### Les balises

Les balises s'écrivent entre accolades `{ }`. Elles ne sont pas lues à voix
haute : ce sont des instructions.

| Balise | Effet | Exemple |
|---|---|---|
| `{NomDeVoix}` | Change la voix pour **tout le texte qui suit**, jusqu'à la prochaine balise de voix. | `{Kermit} Coucou !` |
| `{nom-du-son}` | Joue l'effet sonore à cet endroit du message. | `C'est fini {bruh}` |

- Sans balise de voix, le texte est lu avec la voix **SpongeBob**.
- Un même son peut être utilisé plusieurs fois dans un message.
- Les balises ne sont pas sensibles à la casse : `{kermit}` = `{Kermit}`.
- Un message composé uniquement de balises de voix, sans texte ni son, est
  refusé.

### Ajouter tes propres sons

Dépose un fichier `.mp3`, `.wav`, `.ogg`, `.flac`, `.m4a` ou `.aac` dans
`TTS/media/Sound_Bites/`. Son nom sans l'extension devient la balise :
`airhorn.mp3` → `{airhorn}`. Recharge la page pour le voir apparaître.

Deux règles à respecter :
- un son ne peut pas porter le même nom qu'une voix (ex. `Kermit.mp3`) ;
- deux sons ne peuvent pas porter le même nom avec des extensions différentes
  (ex. `bruh.mp3` et `bruh.wav`).

## Bot Twitch et modération

Les viewers envoient leurs demandes **depuis le chat Twitch** ; tout tourne sur
ton PC, à côté d'OBS. Aucune URL publique n'est nécessaire.

```
 Chat Twitch : !tts {Kermit} Salut {bruh}      ou      récompense de points de chaîne
        │  EventSub (WebSocket)
        ▼
 Bot (manage.py twitch_bot) ── droits, cooldown, bannis, mots interdits
        │
        ├─ mode manuel ──▶ « À valider » ──▶ tu approuves ou refuses dans le panneau
        └─ mode auto ────▶ directement dans la file de lecture
                                  │
                                  ▼
          un clip à la fois dans OBS (le suivant attend la fin du précédent)
```

### Configurer Twitch (une seule fois)

1. Va sur [dev.twitch.tv/console/apps](https://dev.twitch.tv/console/apps) →
   **Register Your Application** :
   - *OAuth Redirect URLs* : `http://localhost:8000/twitch/callback/`
   - *Category* : *Chat Bot* ; *Client Type* : **Confidential**
2. Copie le *Client ID* et génère un *Client Secret*, puis ajoute-les dans `.env` :

   ```
   TWITCH_CLIENT_ID=ton_client_id
   TWITCH_CLIENT_SECRET=ton_client_secret
   ```

3. Mets à jour la base : `python TTS/manage.py migrate`.
4. Lance le serveur, ouvre
   [http://localhost:8000/moderation/](http://localhost:8000/moderation/) et
   clique sur **Connecter Twitch**. Connecte-toi avec **le compte de ta chaîne**.

> Ouvre bien le panneau via `localhost` (et pas `127.0.0.1`) : Twitch renvoie
> vers l'URL de redirection exacte déclarée à l'étape 1.

### Pendant le stream

Il faut **deux terminaux** (venv activé dans chacun) :

```bash
python TTS/manage.py runserver
```

```bash
python TTS/manage.py twitch_bot
```

Le premier sert le panneau, le second écoute Twitch **et joue les clips dans
OBS**. Le panneau affiche « ● Bot actif » quand le second tourne.

> Sans le bot, les demandes s'accumulent dans le panneau mais **rien n'est joué**.

### Côté viewers

Dans le chat, la commande suivie du message (mêmes balises que sur la page
`/message/`) :

```
!tts {Kermit} Salut le stream ! {vine-boom}
```

Avec une récompense de points de chaîne, le viewer écrit simplement son message
(avec ou sans balises) dans la zone de texte de la récompense.

Pour leur indiquer les voix et sons disponibles, tu peux par exemple ajouter
une commande `!sons` dans ton bot de chat habituel avec la liste des balises.

### Choisir entre validation manuelle et automatique

| Mode | Ce qui se passe | Quand l'utiliser |
|---|---|---|
| **Manuel** (par défaut) | Chaque demande attend dans « À valider » jusqu'à ce que tu cliques sur *Approuver*. | Grosse audience, ou quand tu peux garder un œil sur le panneau (ou un modo le fait). |
| **Automatique** | Les demandes partent directement dans la file de lecture. | Petite communauté de confiance, ou commande réservée aux abonnés / VIP. |

Dans les deux modes, une demande avec un **mot interdit** attend ta
validation. Pense à remplir la liste *Mots interdits* avant de passer en
automatique.

Pour tester sans Twitch (par exemple le bouton *Rejouer*) :
`python TTS/manage.py twitch_bot --no-twitch`.

### Le panneau (`/moderation/`)

- **Interrupteurs** :
  - *Validation manuelle / automatique* : en auto, les demandes partent
    directement dans la file, sans vérification. Une demande qui contient un
    **mot interdit reste toujours à valider à la main**.
  - *Demandes ouvertes / fermées* : coupe le TTS d'un clic.
  - *Lecture active / en pause* : les demandes validées attendent.
- **À valider** : Approuver, Refuser ou Bannir le viewer.
- **File de lecture** : *Passer* coupe le clip en cours dans OBS ; *Retirer*
  enlève un clip en attente.
- **Historique** : les 50 derniers clips joués, refusés ou en échec, avec
  *Rejouer*.
- **Réglages** : commande du chat, qui peut l'utiliser (tout le monde, abonnés,
  VIP, modos), récompense de points de chaîne, cooldown par viewer, longueur
  maximale, confiance aux modos, mots interdits, viewers bannis.

Les demandes des modos et du streamer sont validées d'office si *Faire
confiance aux modos* est coché.

### Points de chaîne

Les récompenses de points de chaîne sont réservées aux comptes **affiliés ou
partenaires**. Dans le tableau de bord Twitch, crée une récompense avec
**« Exiger une saisie de texte du viewer »**, puis recopie son titre exact dans
*Réglages → Récompense de points de chaîne*.

### Limites

- Le bot ne répond pas dans le chat (pas de message « cooldown » ou « refusé »).
- Une récompense refusée n'est pas remboursée automatiquement en points.
- Le panneau n'a pas de mot de passe : il est prévu pour rester en local.

## Mettre à jour le projet

Récupère les dernières modifications, active le venv (voir
[Lancer l'application](#lancer-lapplication)), puis mets à jour les dépendances
et la base de données :

```bash
git pull
python -m pip install -r requirements.txt
python TTS/manage.py migrate
```

Si l'ancien design s'affiche encore dans le navigateur, force le rechargement
avec `Ctrl + Shift + R` (`Cmd + Shift + R` sur macOS) : le navigateur a gardé
l'ancien CSS en cache.

## Dépannage

### Le son ne sort pas

| Symptôme | Solution |
|---|---|
| Le clip est envoyé mais **rien ne s'entend** | Règle *Monitoring audio* sur *Monitoring et sortie* (voir [Préparer OBS](#préparer-obs)) et vérifie que la source `TTS Audio` est dans la scène active. |
| `Cannot connect to OBS WebSocket` | OBS est-il lancé ? Le serveur WebSocket est-il activé ? `host`, `port` et `password` de `config file` correspondent-ils à OBS ? |
| `OBS rejected ...` | Aucune source nommée `TTS Audio` (ou ton `input_name`) n'existe dans OBS. |
| `OBS did not start playback ...` | Vérifie que `TTS Audio` est bien une **Source média** (pas une source VLC). |

### Le serveur ou la page ne fonctionne pas

| Symptôme | Solution |
|---|---|
| `No module named 'django'` / `'obsws_python'` / `'dotenv'` | Le venv n'est pas activé, ou les dépendances ne sont pas à jour : `python -m pip install -r requirements.txt`. |
| `no such table: django_session` | Lance `python TTS/manage.py migrate`. |
| Quelle adresse ouvrir ? | `/moderation/` pour le panneau (l'adresse racine `/` y redirige), `/message/` pour envoyer un message toi-même. |
| L'ancien design s'affiche | Recharge sans cache : `Ctrl + Shift + R` (`Cmd + Shift + R` sur macOS). |
| `python: command not found` (Linux / macOS) | Le venv n'est pas activé : `source myenv/bin/activate`. Hors venv, utilise `python3`. |

### Erreurs affichées dans la page

| Message | Solution |
|---|---|
| `TTS_MONSTER_API_KEY is not configured` | Vérifie que `.env` existe à la racine du projet et contient ta clé, puis relance le serveur. |
| `The TTS service request failed.` | Clé invalide, quota TTS Monster épuisé, ou pas de connexion Internet. |
| `FFmpeg is required…` | Les dépendances ne sont pas à jour : `python -m pip install -r requirements.txt`. |
| `Balise inconnue : {…}` | La balise ne correspond à aucune voix ni à aucun son. Vérifie l'orthographe. |
| « Ce clip a déjà été joué » | Le même message a déjà été envoyé pendant cette session. Modifie-le légèrement. |

### Bot Twitch

| Symptôme | Solution |
|---|---|
| `TWITCH_CLIENT_ID et TWITCH_CLIENT_SECRET doivent être renseignés` | Ajoute-les dans `.env`, puis relance le serveur. |
| Twitch affiche `redirect_mismatch` | L'URL de redirection de ton application Twitch doit être exactement `http://localhost:8000/twitch/callback/` (ou la valeur de `TWITCH_REDIRECT_URI`). |
| « La réponse de Twitch ne correspond pas à la demande » | Recommence depuis le bouton *Connecter Twitch* du panneau, ouvert via `localhost`. |
| Le panneau affiche « ○ Bot arrêté » | Lance `python TTS/manage.py twitch_bot` dans un second terminal. |
| `!tts` ne crée aucune demande | Vérifie la commande et *Qui peut utiliser la commande* dans les réglages, le cooldown, la liste des bannis et l'interrupteur *Demandes ouvertes*. Le terminal du bot indique pourquoi une demande est ignorée. |
| Les points de chaîne ne font rien | Le titre de la récompense doit être recopié exactement dans les réglages, et la récompense doit demander une saisie de texte. Réservé aux affiliés et partenaires. |
| Les demandes restent « En file » | Le bot est arrêté, ou la lecture est en pause. |
| `Abonnement … refusé par Twitch (HTTP 403)` | Reconnecte Twitch depuis le panneau avec le compte de ta chaîne. |

## Pour les développeurs

### Structure du projet

```
TTS-monster-for-OBS/
├── .github/
│   ├── workflows/tests.yml       # CI : tests à chaque push
│   └── scripts/test_report.py    # rapport des tests affiché dans GitHub
├── .env.example                  # modèle pour la clé API
├── requirements.txt
└── TTS/
    ├── manage.py
    ├── TTS/
    │   ├── chatbot.py            # API TTS Monster, FFmpeg et OBS WebSocket
    │   ├── config file.example   # modèle de configuration OBS
    │   ├── settings.py           # réglages Django, chargement du .env
    │   └── urls.py
    ├── listings/
    │   ├── form.py               # formulaire, liste des voix, analyse des balises
    │   ├── playback.py           # message + balises → audio dans OBS
    │   ├── models.py             # demandes TTS, réglages, connexion Twitch
    │   ├── moderation.py         # règles d'entrée dans la file (auto / manuel)
    │   ├── twitch.py             # OAuth et EventSub (chat, points de chaîne)
    │   ├── worker.py             # lecture des demandes validées, une par une
    │   ├── management/commands/twitch_bot.py
    │   ├── views.py              # page /message/ et panneau /moderation/
    │   ├── templates/listings/
    │   ├── static/listings/      # style.css, prompt-tags.js, moderation.js
    │   ├── tests.py
    │   └── tests_moderation.py
    └── media/
        ├── Sound_Bites/          # effets sonores utilisables en balise
        └── tts/                  # clips générés (temporaires, ignorés par Git)
```

### Ajouter une voix

Les voix sont listées dans `VOICE_CHOICES`, dans `TTS/listings/form.py`. Chaque
entrée associe l'identifiant de la voix chez TTS Monster au nom affiché, qui
sert aussi de balise.

### Lancer les tests

```bash
python TTS/manage.py test listings
```

Les tests n'appellent ni TTS Monster ni OBS : ces services sont simulés.

### Intégration continue (GitHub Actions)

Le workflow `.github/workflows/tests.yml` lance automatiquement les tests, sous
Windows et sous Ubuntu, à **chaque push** et sur **chaque pull request**. Le
résultat apparaît dans l'onglet **Actions** du dépôt et sur la pull request.

À la fin de chaque exécution, un **rapport des tests** est affiché sur la page de
l'exécution (onglet *Summary*) : nombre de tests réussis, en échec et ignorés,
détail des erreurs et durée de chaque test. Le rapport brut au format JUnit est
aussi téléchargeable dans la section *Artifacts*.

Pour générer le même rapport en local :

```bash
python -m pip install unittest-xml-reporting
python TTS/manage.py test listings --testrunner xmlrunner.extra.djangotestrunner.XMLTestRunner
python .github/scripts/test_report.py test-reports/junit.xml
```

La branche `main` est protégée : une pull request ne peut être fusionnée que si
les tests passent. Pour travailler :

1. crée une branche et pousse tes modifications ;
2. ouvre une pull request vers `main` ;
3. attends que les tests soient verts ✅, puis fusionne.

## Idées pour la suite

- **Bot Twitch** : réponses dans le chat, remboursement des récompenses
  refusées, déclenchement par bits.
- **Images réactives** affichées dans OBS pendant la lecture (liées à la voix ou
  choisies par l'utilisateur).
- **Indicateur de connexion OBS** dans l'interface.
