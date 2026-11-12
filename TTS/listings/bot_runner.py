"""Démarrage et arrêt du bot (client Twitch + worker de lecture).

Utilisé par les deux commandes :

- ``manage.py twitch_bot`` : le bot seul (le panneau tourne à part avec
  ``runserver``) ;
- ``manage.py stream`` : le bot et le panneau dans une seule commande.

Le bot tourne dans deux threads en arrière-plan :

- ``tts-worker`` : joue dans OBS les demandes validées (``worker.py``) ;
- ``twitch`` : reçoit le chat et les récompenses (``twitch.py``).
"""

import logging
import threading

from .models import TwitchConnection
from .twitch import EventSubClient
from .worker import run_worker


def configure_logging() -> None:
    """Affiche dans le terminal ce que fait le bot, avec l'heure."""
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S"
    )


class BotRunner:
    def __init__(self, with_twitch: bool = True):
        self.with_twitch = with_twitch
        # Signal partagé : quand il est levé, les deux boucles s'arrêtent.
        self.stop_event = threading.Event()
        self.threads: list[threading.Thread] = []

    def start(self) -> None:
        """Lance le worker et, sauf option contraire, le client Twitch."""
        self._start_thread("tts-worker", run_worker, self.stop_event)
        if self.with_twitch:
            self._start_thread("twitch", EventSubClient(self.stop_event).run_forever)
        else:
            TwitchConnection.update_fields(bot_status="Lecture seule (sans Twitch)")

    def _start_thread(self, name, target, *args) -> None:
        # daemon=True : si le programme se ferme, ces threads ne le bloquent pas.
        thread = threading.Thread(target=target, args=args, name=name, daemon=True)
        thread.start()
        self.threads.append(thread)

    def wait_forever(self) -> None:
        """Attend jusqu'à Ctrl+C.

        On attend par petites tranches de 0,5 s : sous Windows, une attente
        sans limite de temps ne serait pas interrompue par Ctrl+C.
        """
        while not self.stop_event.wait(0.5):
            pass

    def stop(self) -> None:
        """Arrêt propre : on laisse aux threads 10 secondes pour finir."""
        self.stop_event.set()
        for thread in self.threads:
            thread.join(timeout=10)
        TwitchConnection.update_fields(bot_status="Arrêté", bot_heartbeat=None)
