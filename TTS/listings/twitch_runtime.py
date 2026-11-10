import asyncio
import logging
import os
import threading
from concurrent.futures import TimeoutError as FutureTimeoutError

from django.core.management.base import CommandError
from django.db import close_old_connections

from listings.management.commands.twitch_chat import (
    TwitchChatReader,
    _validate_access_token,
)
from listings.players import clear_player_slots


logger = logging.getLogger(__name__)


class TwitchChatRuntime:
    def __init__(self, channel: str) -> None:
        self.channel = channel
        self._loop: asyncio.AbstractEventLoop | None = None
        self._bot: TwitchChatReader | None = None
        self._thread = threading.Thread(
            target=self._run,
            name="twitch-chat-reader",
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        loop = self._loop
        bot = self._bot
        if loop is not None and bot is not None and loop.is_running():
            future = asyncio.run_coroutine_threadsafe(self._close_bot(bot), loop)
            try:
                future.result(timeout=10)
            except FutureTimeoutError:
                logger.error("Timed out while stopping the Twitch chat reader.")
            except Exception:
                logger.exception("Could not stop the Twitch chat reader cleanly.")

        self._thread.join(timeout=10)
        if self._thread.is_alive():
            logger.error("Twitch chat reader thread did not stop.")

    async def _close_bot(self, bot: TwitchChatReader) -> None:
        try:
            await bot.close()
        finally:
            asyncio.get_running_loop().call_soon(asyncio.get_running_loop().stop)

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop

        try:
            token = os.getenv("Access_token", "").strip()
            client_id = os.getenv("Client_ID", "").strip()
            if not token or not client_id:
                raise CommandError(
                    "Set Access_token and Client_ID in the repository-root .env file."
                )
            if token.lower().startswith("oauth:"):
                token = token[6:]

            _validate_access_token(token, client_id)
            clear_player_slots()
            self._bot = TwitchChatReader(
                token=token,
                channel=self.channel,
                loop=loop,
            )
            logger.info("Starting Twitch chat reader for #%s.", self.channel)
            self._bot.run()
        except Exception:
            logger.exception(
                "Twitch chat reader stopped unexpectedly for #%s.",
                self.channel,
            )
        finally:
            try:
                clear_player_slots()
            except Exception:
                logger.exception("Could not clear Twitch player slots on shutdown.")
            close_old_connections()
            if not loop.is_closed():
                loop.close()
            asyncio.set_event_loop(None)
