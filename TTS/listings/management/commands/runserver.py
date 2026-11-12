import logging
import os

from django.contrib.staticfiles.management.commands.runserver import (
    Command as DjangoRunserverCommand,
)

from listings.players import clear_player_slots
from listings.twitch_runtime import TwitchChatRuntime


logger = logging.getLogger(__name__)


class Command(DjangoRunserverCommand):
    def inner_run(self, *args, **options):
        self._twitch_runtime = None
        try:
            return super().inner_run(*args, **options)
        finally:
            try:
                if self._twitch_runtime is not None:
                    self._twitch_runtime.stop()
            finally:
                try:
                    clear_player_slots()
                except Exception:
                    logger.exception("Could not clear Twitch player slots on shutdown.")

    def get_handler(self, *args, **options):
        handler = super().get_handler(*args, **options)
        channel = os.getenv("Twitch_Channel", "").strip().lstrip("#@").lower()
        if channel:
            self._twitch_runtime = TwitchChatRuntime(channel)
            self._twitch_runtime.start()
            logger.info("Twitch chat reader is starting for #%s.", channel)
        else:
            logger.warning(
                "Twitch chat reader was not started: Twitch_Channel is not set "
                "in the repository-root .env file."
            )
        return handler
