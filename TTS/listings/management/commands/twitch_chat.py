import asyncio
import logging
import os
import re

import requests
from django.core.management.base import BaseCommand, CommandError
from twitchio import Message
from twitchio.ext import commands

from TTS.chatbot import TTSGenerationError, VOICE_ID, send_audio_to_obs, url_tts
from listings.players import claim_player_slot, clear_player_slots, get_player_voice


TOKEN_VALIDATION_URL = "https://id.twitch.tv/oauth2/validate"
PLAYER_COMMAND_PATTERN = re.compile(r"!player([1-3])", re.IGNORECASE)
logger = logging.getLogger(__name__)


def _speak_chat_message(text: str, voice_id: str = VOICE_ID) -> None:
    audio_url = url_tts(text, voice_id)
    send_audio_to_obs(audio_url)


def _validate_access_token(token: str, client_id: str) -> None:
    try:
        response = requests.get(
            TOKEN_VALIDATION_URL,
            headers={"Authorization": f"OAuth {token}"},
            timeout=10,
        )
    except requests.RequestException as exc:
        raise CommandError(
            "Could not validate the Twitch access token. Check your connection."
        ) from exc

    if response.status_code == 401:
        raise CommandError(
            "Twitch rejected Access_token. Check that it is valid and has the "
            "chat:read scope."
        )

    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        raise CommandError(
            f"Twitch token validation failed with HTTP {response.status_code}."
        ) from exc

    try:
        payload = response.json()
    except ValueError as exc:
        raise CommandError(
            "Twitch returned an invalid token validation response."
        ) from exc

    if not isinstance(payload, dict):
        raise CommandError("Twitch returned an invalid token validation response.")

    if payload.get("client_id") != client_id:
        raise CommandError(
            "Client_ID does not match the application that issued Access_token."
        )

    scopes = payload.get("scopes")
    if not isinstance(scopes, list) or "chat:read" not in scopes:
        raise CommandError(
            "Access_token must include the Twitch chat:read permission."
        )


class TwitchChatReader(commands.Bot):
    def __init__(
        self,
        *,
        token: str,
        channel: str,
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        self.channel_name = channel.strip().lstrip("#@").lower()
        super().__init__(
            token=token,
            prefix="!",
            initial_channels=[self.channel_name],
            loop=loop,
        )
        self._tts_lock = asyncio.Lock()

    async def event_ready(self) -> None:
        print(
            f"Connecté au chat Twitch #{self.channel_name} "
            f"en tant que {self.nick}. Le bot lit les messages avec TTS Monster.",
            flush=True,
        )

    async def event_message(self, message: Message) -> None:
        if message.echo or message.author is None or message.channel is None:
            return

        text = message.content.strip()
        if not text:
            return

        print(
            f"[{message.channel.name}] {message.author.name}: {text}",
            flush=True,
        )
        player_command = PLAYER_COMMAND_PATTERN.fullmatch(text)
        if player_command:
            player_number = int(player_command.group(1))
            claimed = await asyncio.to_thread(
                claim_player_slot,
                player_number,
                message.author.name,
                message.author.display_name,
            )
            if claimed:
                print(
                    f"{message.author.name} joined as player {player_number}.",
                    flush=True,
                )
            else:
                print(
                    f"Player {player_number} is already taken.",
                    flush=True,
                )
            return

        async with self._tts_lock:
            try:
                voice_id = await asyncio.to_thread(
                    get_player_voice,
                    message.author.name,
                )
                await asyncio.to_thread(
                    _speak_chat_message,
                    text,
                    voice_id or VOICE_ID,
                )
            except TTSGenerationError:
                logger.exception(
                    "Could not generate or play Twitch chat message with TTS Monster."
                )


class Command(BaseCommand):
    help = "Read Twitch chat messages aloud through TTS Monster and OBS."

    def add_arguments(self, parser) -> None:
        parser.add_argument("channel", help="Twitch channel login name to read")

    def handle(self, *args, **options) -> None:
        token = os.getenv("Access_token", "").strip()
        client_id = os.getenv("Client_ID", "").strip()
        if not token or not client_id:
            raise CommandError(
                "Set Access_token and Client_ID in the repository-root .env file."
            )

        if token.lower().startswith("oauth:"):
            token = token[6:]

        channel = options["channel"].strip().lstrip("#@").lower()
        if not channel:
            raise CommandError("Provide a Twitch channel login name.")

        _validate_access_token(token, client_id)

        clear_player_slots()
        self.stdout.write(f"Connecting to #{channel}...")
        try:
            TwitchChatReader(token=token, channel=channel).run()
        finally:
            clear_player_slots()
