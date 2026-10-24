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

Reference sound files in `TTS/media/Sound_Bites` directly in the message using
their filename without the extension inside braces. For example, if the folder
contains `bruh.mp3`, write `Hello {bruh} and welcome!` to play that sound between
the spoken text. Change the voice in the same way using its displayed label,
such as `{Adam}` or `{Kermit}`. The voice remains active until the next voice
tag; text before the first voice tag uses the default SpongeBob voice. Available
voice and sound tags are listed below the message field, and the same sound can
be used multiple times. The complete sequence is combined into one WAV file
before it is sent to OBS. Install FFmpeg and make sure the `ffmpeg` command is
available on PATH. Each speech and sound segment is loudness-normalized to -16
LUFS during compilation so sound bites play at a volume closer to the TTS.


Si tu lis ce message. c'est que tu squatte mon ordi. PAS BIEN PAS BIENBebou je t'aime.