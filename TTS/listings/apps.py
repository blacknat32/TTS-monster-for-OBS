import logging
import sys

from django.apps import AppConfig


logger = logging.getLogger(__name__)


class ListingsConfig(AppConfig):
    name = 'listings'

    def ready(self):
        # Only warm up the OBS connection for the web server: commands such as
        # test, check or migrate must not touch the real OBS. The connection is
        # opened lazily anyway when audio is sent.
        if "runserver" not in sys.argv:
            return

        from TTS.chatbot import TTSGenerationError, connect_to_obs

        try:
            connect_to_obs()
        except TTSGenerationError:
            logger.exception(
                "Could not connect to OBS during startup; it will retry when audio "
                "is sent."
            )
