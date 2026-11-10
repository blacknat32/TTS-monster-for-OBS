import asyncio
from types import SimpleNamespace
from unittest.mock import Mock, PropertyMock, patch

from django.core.management.base import CommandError
from django.test import SimpleTestCase
from listings.form import VOICE_CHOICES

from listings.management.commands.twitch_chat import (
    TOKEN_VALIDATION_URL,
    TwitchChatReader,
    _validate_access_token,
)
from TTS.chatbot import TTSGenerationError, VOICE_ID


class TwitchAccessTokenTests(SimpleTestCase):
    @patch("listings.management.commands.twitch_chat.requests.get")
    def test_validates_client_id_and_chat_read_scope(self, get):
        response = Mock(status_code=200)
        response.json.return_value = {
            "client_id": "test-client-id",
            "scopes": ["chat:read"],
        }
        get.return_value = response

        _validate_access_token("test-token", "test-client-id")

        get.assert_called_once_with(
            TOKEN_VALIDATION_URL,
            headers={"Authorization": "OAuth test-token"},
            timeout=10,
        )

    @patch("listings.management.commands.twitch_chat.requests.get")
    def test_rejects_a_mismatched_client_id(self, get):
        response = Mock(status_code=200)
        response.json.return_value = {
            "client_id": "different-client-id",
            "scopes": ["chat:read"],
        }
        get.return_value = response

        with self.assertRaisesMessage(CommandError, "Client_ID does not match"):
            _validate_access_token("test-token", "test-client-id")

    @patch("listings.management.commands.twitch_chat.requests.get")
    def test_rejects_token_without_chat_read_scope(self, get):
        response = Mock(status_code=200)
        response.json.return_value = {
            "client_id": "test-client-id",
            "scopes": [],
        }
        get.return_value = response

        with self.assertRaisesMessage(CommandError, "chat:read permission"):
            _validate_access_token("test-token", "test-client-id")


class TwitchChatReaderTests(SimpleTestCase):
    def setUp(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            self.reader = TwitchChatReader(token="test-token", channel="streamer")
        finally:
            asyncio.set_event_loop(None)
            loop.close()

    def test_confirms_chat_channel_connection_in_terminal(self):
        with (
            patch("builtins.print") as print_message,
            patch.object(
                TwitchChatReader,
                "nick",
                new_callable=PropertyMock,
                return_value="test-bot",
            ),
        ):
            asyncio.run(self.reader.event_ready())

        print_message.assert_called_once_with(
            "Connecté au chat Twitch #streamer en tant que test-bot. "
            "Le bot lit les messages avec TTS Monster.",
            flush=True,
        )

    def test_reads_user_messages_with_tts_and_plays_them_in_obs(self):
        message = SimpleNamespace(
            echo=False,
            author=SimpleNamespace(name="viewer"),
            channel=SimpleNamespace(name="streamer"),
            content="hello chat",
        )

        async def run_in_thread(function, *args):
            return function(*args)

        selected_voice_id = VOICE_CHOICES[1][0]
        with (
            patch("builtins.print") as print_message,
            patch(
                "listings.management.commands.twitch_chat.get_player_voice",
                return_value=selected_voice_id,
            ) as get_player_voice,
            patch(
                "listings.management.commands.twitch_chat.url_tts",
                return_value="https://example.com/audio.wav",
            ) as url_tts,
            patch(
                "listings.management.commands.twitch_chat.send_audio_to_obs"
            ) as send_audio_to_obs,
            patch(
                "listings.management.commands.twitch_chat.asyncio.to_thread",
                side_effect=run_in_thread,
            ),
        ):
            asyncio.run(self.reader.event_message(message))

        print_message.assert_called_once_with(
            "[streamer] viewer: hello chat", flush=True
        )
        get_player_voice.assert_called_once_with("viewer")
        url_tts.assert_called_once_with("hello chat", selected_voice_id)
        send_audio_to_obs.assert_called_once_with("https://example.com/audio.wav")

    def test_ignores_messages_echoed_by_the_bot(self):
        message = SimpleNamespace(
            echo=True,
            author=SimpleNamespace(name="bot"),
            channel=SimpleNamespace(name="streamer"),
            content="bot message",
        )

        with (
            patch("builtins.print") as print_message,
            patch(
                "listings.management.commands.twitch_chat.asyncio.to_thread"
            ) as to_thread,
        ):
            asyncio.run(self.reader.event_message(message))

        print_message.assert_not_called()
        to_thread.assert_not_called()

    def test_logs_tts_errors_and_keeps_listening(self):
        message = SimpleNamespace(
            echo=False,
            author=SimpleNamespace(name="viewer"),
            channel=SimpleNamespace(name="streamer"),
            content="hello chat",
        )

        async def fail_in_thread(function, *args):
            raise TTSGenerationError("TTS Monster is unavailable.")

        with (
            patch("builtins.print"),
            patch(
                "listings.management.commands.twitch_chat.asyncio.to_thread",
                side_effect=fail_in_thread,
            ),
            patch(
                "listings.management.commands.twitch_chat.logger.exception"
            ) as log_exception,
        ):
            asyncio.run(self.reader.event_message(message))

        log_exception.assert_called_once()
