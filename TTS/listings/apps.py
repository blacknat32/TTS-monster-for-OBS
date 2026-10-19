import logging

from django.apps import AppConfig


logger = logging.getLogger(__name__)


class ListingsConfig(AppConfig):
    name = 'listings'

    def ready(self):
        from TTS.chatbot import TTSGenerationError, connect_to_obs

        try:
            connect_to_obs()
        except TTSGenerationError:
            logger.exception(
                "Could not connect to OBS during startup; it will retry when audio "
                "is sent."
            )
