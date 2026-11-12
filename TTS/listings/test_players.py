import asyncio
import os
from types import SimpleNamespace
from unittest.mock import patch

from django.core.management import get_commands, load_command_class
from django.test import TestCase
from django.urls import reverse

from listings.management.commands.twitch_chat import TwitchChatReader
from listings.management.commands.runserver import Command as RunserverCommand
from listings.form import VOICE_CHOICES
from listings.models import PlayerSlot
from listings.players import (
    claim_player_slot,
    clear_player_slots,
    get_player_voice,
)


class PlayerSlotTests(TestCase):
    def tearDown(self):
        clear_player_slots()

    def test_claims_an_available_slot(self):
        self.assertTrue(claim_player_slot(1, "viewer", "Viewer"))

        player = PlayerSlot.objects.get(player_number=1)
        self.assertEqual(player.username, "viewer")
        self.assertEqual(player.display_name, "Viewer")

    def test_does_not_replace_a_slot_claimed_by_another_user(self):
        claim_player_slot(1, "first", "First")

        self.assertFalse(claim_player_slot(1, "second", "Second"))

        self.assertEqual(PlayerSlot.objects.get(player_number=1).username, "first")

    def test_user_can_move_to_another_available_slot(self):
        claim_player_slot(1, "viewer", "Viewer")

        self.assertTrue(claim_player_slot(2, "VIEWER", "Viewer"))

        self.assertFalse(PlayerSlot.objects.filter(player_number=1).exists())
        self.assertEqual(
            PlayerSlot.objects.get(player_number=2).username,
            "viewer",
        )

    def test_rejects_slot_numbers_outside_the_three_players(self):
        self.assertFalse(claim_player_slot(4, "viewer", "Viewer"))
        self.assertFalse(PlayerSlot.objects.exists())

    def test_status_page_shows_three_slots_and_current_players(self):
        claim_player_slot(2, "viewer", "Viewer")
        player_two_voice = VOICE_CHOICES[1]
        PlayerSlot.objects.filter(player_number=2).update(voice_id=player_two_voice[0])

        response = self.client.get(reverse("players_status"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "players": [
                    {
                        "number": 1,
                        "username": None,
                        "voice_id": VOICE_CHOICES[0][0],
                        "voice_name": VOICE_CHOICES[0][1],
                    },
                    {
                        "number": 2,
                        "username": "Viewer",
                        "voice_id": player_two_voice[0],
                        "voice_name": player_two_voice[1],
                    },
                    {
                        "number": 3,
                        "username": None,
                        "voice_id": VOICE_CHOICES[0][0],
                        "voice_name": VOICE_CHOICES[0][1],
                    },
                ]
            },
        )
        self.assertEqual(
            response["Cache-Control"],
            "max-age=0, no-cache, no-store, must-revalidate, private",
        )

    def test_status_page_shows_login_when_display_name_is_missing(self):
        claim_player_slot(3, "viewer", "Viewer")
        PlayerSlot.objects.filter(player_number=3).update(display_name="")

        response = self.client.get(reverse("players_status"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["players"][2]["username"], "viewer")

    def test_players_page_loads_live_status_script(self):
        response = self.client.get(reverse("players"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-player="1"')
        self.assertContains(response, 'data-player="2"')
        self.assertContains(response, 'data-player="3"')
        self.assertContains(response, "!player1")
        self.assertContains(response, "!player2")
        self.assertContains(response, "!player3")
        self.assertContains(response, "listings/players.js")
        self.assertContains(response, 'name="voice_id"')
        self.assertContains(response, "SpongeBob")
        self.assertContains(response, "Kermit")

    def test_chat_page_links_to_players_page(self):
        response = self.client.get(reverse("listings"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("players"))

    def test_saves_voice_choice_from_voice_choices_for_empty_player_slot(self):
        voice_id, voice_name = VOICE_CHOICES[1]

        response = self.client.post(
            reverse("set_player_voice"),
            {"player_number": "1", "voice_id": voice_id},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            f"Joueur 1 réglée sur {voice_name}",
        )
        slot = PlayerSlot.objects.get(player_number=1)
        self.assertIsNone(slot.username)
        self.assertEqual(slot.voice_id, voice_id)

    def test_rejects_voice_that_is_not_in_voice_choices(self):
        response = self.client.post(
            reverse("set_player_voice"),
            {"player_number": "1", "voice_id": "not-a-voice"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("players"))
        self.assertFalse(PlayerSlot.objects.exists())

    def test_clearing_player_slots_preserves_voice_choices(self):
        voice_id = VOICE_CHOICES[1][0]
        claim_player_slot(1, "viewer", "Viewer")
        PlayerSlot.objects.filter(player_number=1).update(voice_id=voice_id)

        clear_player_slots()

        slot = PlayerSlot.objects.get(player_number=1)
        self.assertIsNone(slot.username)
        self.assertEqual(slot.voice_id, voice_id)

    def test_finds_selected_voice_for_assigned_player(self):
        voice_id = VOICE_CHOICES[1][0]
        claim_player_slot(1, "viewer", "Viewer")
        PlayerSlot.objects.filter(player_number=1).update(voice_id=voice_id)

        self.assertEqual(get_player_voice("VIEWER"), voice_id)
        self.assertIsNone(get_player_voice("other"))


class TwitchPlayerCommandTests(TestCase):
    def setUp(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            self.bot = TwitchChatReader(token="test-token", channel="streamer")
        finally:
            asyncio.set_event_loop(None)
            loop.close()

    def test_player_command_claims_slot_without_speaking_the_command(self):
        message = SimpleNamespace(
            echo=False,
            author=SimpleNamespace(name="viewer", display_name="Viewer"),
            channel=SimpleNamespace(name="streamer"),
            content="!player2",
        )

        async def run_in_thread(*args):
            if args[0] is get_player_voice:
                return VOICE_CHOICES[1][0]
            return True

        with (
            patch(
                "listings.management.commands.twitch_chat.asyncio.to_thread",
                side_effect=run_in_thread,
            ) as to_thread,
            patch(
                "listings.management.commands.twitch_chat._speak_chat_message"
            ) as speak,
        ):
            asyncio.run(self.bot.event_message(message))

        self.assertEqual(
            to_thread.call_args.args,
            (
                claim_player_slot,
                2,
                "viewer",
                "Viewer",
            ),
        )
        speak.assert_not_called()


class TwitchRunserverTests(TestCase):
    def test_django_loads_the_twitch_enabled_runserver_command(self):
        provider = get_commands()["runserver"]

        self.assertEqual(provider, "listings")
        command = load_command_class(provider, "runserver")
        self.assertIsInstance(command, RunserverCommand)

    @patch.dict(os.environ, {"Twitch_Channel": " #Streamer "})
    @patch("listings.management.commands.runserver.TwitchChatRuntime")
    @patch("django.core.management.commands.runserver.Command.get_handler")
    def test_runserver_starts_twitch_reader_for_configured_channel(
        self, get_handler, runtime_class
    ):
        handler = object()
        get_handler.return_value = handler
        command = RunserverCommand()

        result = command.get_handler(
            use_static_handler=False,
            insecure_serving=False,
        )

        self.assertIs(result, handler)
        runtime_class.assert_called_once_with("streamer")
        runtime_class.return_value.start.assert_called_once_with()

    @patch("listings.management.commands.runserver.clear_player_slots")
    @patch("django.contrib.staticfiles.management.commands.runserver.Command.inner_run")
    def test_runserver_clears_player_slots_when_the_app_stops(
        self, inner_run, clear_slots
    ):
        inner_run.return_value = "stopped"
        command = RunserverCommand()

        result = command.inner_run()

        self.assertEqual(result, "stopped")
        clear_slots.assert_called_once_with()
