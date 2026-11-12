"""Commande ``python TTS/manage.py twitch_bot``.

C'est le programme à laisser tourner pendant le stream, dans un second
terminal (le premier fait tourner le panneau avec ``runserver``). Il lance
deux choses en parallèle :

- le **worker** (thread en arrière-plan) : joue dans OBS les demandes validées,
  une par une (voir ``listings/worker.py``) ;
- le **client Twitch** (thread principal) : reçoit les messages du chat et les
  récompenses, et les transforme en demandes (voir ``listings/twitch.py``).

Option ``--no-twitch`` : lance seulement le worker. Pratique pour tester le
bouton « Rejouer » du panneau sans compte Twitch connecté.

Pourquoi un processus séparé du serveur web ? Le serveur de développement de
Django (``runserver``) redémarre à chaque modification du code et peut lancer
plusieurs copies de l'application : un bot démarré à l'intérieur risquerait de
tourner en double et de jouer chaque clip deux fois.
"""

import logging
import threading

from django.core.management.base import BaseCommand

from listings.models import TwitchConnection
from listings.twitch import EventSubClient
from listings.worker import run_worker


class Command(BaseCommand):
    help = (
        "Écoute le chat Twitch et les points de chaîne, puis joue dans OBS les "
        "demandes validées. Laisse cette commande tourner pendant le stream."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--no-twitch",
            action="store_true",
            help="Lance seulement la lecture (utile pour tester avec le panneau).",
        )

    def handle(self, *args, **options):
        # Affiche dans le terminal ce que fait le bot (demande reçue, ignorée,
        # jouée, reconnexion…), avec l'heure.
        logging.basicConfig(
            level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S"
        )
        # Signal partagé : quand il est levé (Ctrl+C), les deux boucles s'arrêtent.
        stop_event = threading.Event()
        worker = threading.Thread(
            target=run_worker, args=(stop_event,), name="tts-worker", daemon=True
        )
        worker.start()
        self.stdout.write(self.style.SUCCESS("Lecture des demandes validées : active."))

        try:
            if options["no_twitch"]:
                TwitchConnection.update_fields(bot_status="Lecture seule (sans Twitch)")
                stop_event.wait()  # attend Ctrl+C
            else:
                self.stdout.write("Connexion à Twitch…  (Ctrl+C pour arrêter)")
                EventSubClient(stop_event).run_forever()
        except KeyboardInterrupt:
            self.stdout.write("\nArrêt du bot…")
        finally:
            # Arrêt propre : on laisse le worker finir le clip en cours (10 s
            # maximum), puis le panneau affichera « Bot arrêté ».
            stop_event.set()
            worker.join(timeout=10)
            TwitchConnection.update_fields(bot_status="Arrêté", bot_heartbeat=None)
