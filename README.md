Ce read me est là pour moi seul et sera modifié lorsque le projet sera avancé.
je ferais un second read me pour ceux qui désireront reprendre le code



Voici mon projet pour l'interface personnel de "TTS-Monster".

Depuis ici. on va pouvoir choisir une "voix" puis écrire un prompt qui sera lu par cette "voix".

On aura accès aussi a des "sons" qu'on pourra incorporer dans le prompt.

Si plusieur voix sont désiré, écrire le nom de la voix dans le prompt permettra de switcher de voix.

Tous les prompt pourront soit:

- etre télécharger
- etre diffusé sur OBS.

Des images réactives seront soit:

- lié au voix
- à choix des utilisateur

Ces images seront là pour transmettre les messages TTS. Ces images sont un choix.



L'API, ici, sera celui de "TTS-Monster".



The generated audio is now sent to OBS over OBS WebSocket: the server downloads the clip, updates a Media Source’s file, and triggers playback.

To use it:

Install the project dependencies: python -m pip install -r requirements.txt
In OBS, enable the WebSocket server.
Add an OBS Media Source named TTS Audio (or change input_name in config file). If OBS requires a password, set it in that config too.
Start Django with TTS_MONSTER_API_KEY set.

The integration is in chatbot.py, and the form calls it from views.py.


Si tu lis ce message. c'est que tu squatte mon ordi. PAS BIEN PAS BIENBebou je t'aime.