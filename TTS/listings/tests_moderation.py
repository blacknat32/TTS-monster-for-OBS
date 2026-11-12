from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from TTS.chatbot import TTSGenerationError, wait_for_obs_playback_end

from . import twitch
from .models import ModerationSettings, TTSRequest, TwitchConnection
from .moderation import Submission, ban_user, submit_request
from .worker import process_next_request, recover_interrupted_requests


class SoundBiteDirectoryMixin:
    def setUp(self):
        super().setUp()
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        (Path(directory.name) / "bruh.mp3").touch()
        directory_patch = patch("listings.form.SOUND_BITE_DIRECTORY", Path(directory.name))
        directory_patch.start()
        self.addCleanup(directory_patch.stop)
        self.settings_row = ModerationSettings.load()

    def configure(self, **fields):
        for name, value in fields.items():
            setattr(self.settings_row, name, value)
        self.settings_row.save()


def chat(message, login="viewer", user_id="42", badges=()):
    return Submission(
        source=TTSRequest.Source.CHAT,
        message=message,
        twitch_user_id=user_id,
        twitch_login=login,
        display_name=login.title(),
        badges=set(badges),
    )


class SubmitRequestTests(SoundBiteDirectoryMixin, TestCase):
    def test_manual_mode_puts_request_in_pending(self):
        result = submit_request(chat("Salut le stream"))

        self.assertEqual(result.request.status, TTSRequest.Status.PENDING)

    def test_auto_mode_approves_request_without_manual_check(self):
        self.configure(auto_approve=True)

        result = submit_request(chat("Salut le stream {bruh}"))

        self.assertEqual(result.request.status, TTSRequest.Status.APPROVED)
        self.assertIsNotNone(result.request.decided_at)

    def test_banned_word_is_held_for_review_even_in_auto_mode(self):
        self.configure(auto_approve=True, banned_words="Gros Mot\nautre")

        result = submit_request(chat("Voici un GROS MOT"))

        self.assertEqual(result.request.status, TTSRequest.Status.PENDING)
        self.assertIn("gros mot", result.request.flag_reason)

    def test_invalid_tag_is_recorded_as_rejected(self):
        result = submit_request(chat("Coucou {inconnu}"))

        self.assertEqual(result.request.status, TTSRequest.Status.REJECTED)
        self.assertIn("Balise inconnue", result.request.error)

    def test_message_over_configured_length_is_rejected(self):
        self.configure(max_message_length=10)

        result = submit_request(chat("Ce message est bien trop long"))

        self.assertEqual(result.request.status, TTSRequest.Status.REJECTED)
        self.assertIn("trop long", result.request.error)

    def test_cooldown_ignores_second_chat_request(self):
        self.configure(user_cooldown_seconds=60)
        submit_request(chat("Premier"))

        result = submit_request(chat("Deuxième"))

        self.assertIsNone(result.request)
        self.assertEqual(TTSRequest.objects.count(), 1)

    def test_cooldown_expires(self):
        self.configure(user_cooldown_seconds=60)
        first = submit_request(chat("Premier")).request
        TTSRequest.objects.filter(pk=first.pk).update(
            created_at=timezone.now() - timedelta(seconds=61)
        )

        self.assertIsNotNone(submit_request(chat("Deuxième")).request)

    def test_rewards_are_not_subject_to_chat_cooldown(self):
        self.configure(user_cooldown_seconds=60)
        submit_request(chat("Premier"))
        reward = chat("Récompense")
        reward.source = TTSRequest.Source.REWARD

        self.assertIsNotNone(submit_request(reward).request)

    def test_banned_user_is_ignored(self):
        self.configure(banned_users="@Viewer")

        self.assertIsNone(submit_request(chat("Coucou")).request)

    def test_closed_requests_are_ignored(self):
        self.configure(accept_requests=False)

        self.assertIsNone(submit_request(chat("Coucou")).request)

    def test_chat_access_restricts_to_subscribers(self):
        self.configure(chat_access=ModerationSettings.ChatAccess.SUBSCRIBERS)

        self.assertIsNone(submit_request(chat("Coucou")).request)
        self.assertIsNotNone(
            submit_request(chat("Coucou", login="sub", user_id="7", badges={"subscriber"})).request
        )

    def test_trusted_moderator_skips_queue_and_banned_words(self):
        self.configure(banned_words="mot", user_cooldown_seconds=60)
        badges = {"moderator"}

        first = submit_request(chat("un mot", login="modo", badges=badges)).request
        second = submit_request(chat("encore", login="modo", badges=badges)).request

        self.assertEqual(first.status, TTSRequest.Status.APPROVED)
        self.assertEqual(second.status, TTSRequest.Status.APPROVED)

    def test_ban_user_rejects_their_waiting_requests(self):
        request = submit_request(chat("Coucou")).request

        ban_user("viewer")

        request.refresh_from_db()
        self.assertEqual(request.status, TTSRequest.Status.REJECTED)
        self.assertIn("viewer", ModerationSettings.load().banned_user_list())


class WorkerTests(SoundBiteDirectoryMixin, TestCase):
    def make_request(self, message="Salut", status=TTSRequest.Status.APPROVED, **fields):
        return TTSRequest.objects.create(
            source=TTSRequest.Source.CHAT, status=status, message=message,
            decided_at=timezone.now(), **fields,
        )

    @patch("listings.worker.wait_for_obs_playback_end")
    @patch("listings.worker.play_message")
    def test_plays_oldest_approved_request(self, play, wait):
        first = self.make_request("Premier")
        self.make_request("Second")

        self.assertTrue(process_next_request())

        play.assert_called_once_with("Premier", {}, {})
        wait.assert_called_once()
        first.refresh_from_db()
        self.assertEqual(first.status, TTSRequest.Status.PLAYED)
        self.assertIsNotNone(first.played_at)

    @patch("listings.worker.wait_for_obs_playback_end")
    @patch("listings.worker.play_message")
    def test_pending_requests_are_not_played(self, play, wait):
        self.make_request(status=TTSRequest.Status.PENDING)

        self.assertFalse(process_next_request())
        play.assert_not_called()

    @patch("listings.worker.play_message")
    def test_paused_playback_keeps_requests_waiting(self, play):
        self.configure(playback_paused=True)
        request = self.make_request()

        self.assertFalse(process_next_request())

        request.refresh_from_db()
        self.assertEqual(request.status, TTSRequest.Status.APPROVED)
        play.assert_not_called()

    @patch("listings.worker.wait_for_obs_playback_end")
    @patch("listings.worker.play_message", side_effect=TTSGenerationError("OBS absent"))
    def test_failure_is_recorded(self, play, wait):
        request = self.make_request()

        process_next_request()

        request.refresh_from_db()
        self.assertEqual(request.status, TTSRequest.Status.FAILED)
        self.assertEqual(request.error, "OBS absent")

    @patch("listings.worker.play_message")
    def test_tag_removed_since_request_fails_cleanly(self, play):
        request = self.make_request("Coucou {disparu}")

        process_next_request()

        request.refresh_from_db()
        self.assertEqual(request.status, TTSRequest.Status.FAILED)
        play.assert_not_called()

    def test_interrupted_playing_request_is_marked_failed(self):
        request = self.make_request(status=TTSRequest.Status.PLAYING)

        recover_interrupted_requests()

        request.refresh_from_db()
        self.assertEqual(request.status, TTSRequest.Status.FAILED)


class PlaybackWaitTests(TestCase):
    @patch("TTS.chatbot.time.sleep")
    @patch("TTS.chatbot.connect_to_obs")
    @patch("TTS.chatbot._load_obs_settings", return_value=("localhost", 4455, "", "TTS Audio"))
    def test_waits_until_media_is_no_longer_playing(self, settings, connect, sleep):
        client = MagicMock()
        client.get_media_input_status.side_effect = [
            SimpleNamespace(media_state="OBS_MEDIA_STATE_PLAYING"),
            SimpleNamespace(media_state="OBS_MEDIA_STATE_PLAYING"),
            SimpleNamespace(media_state="OBS_MEDIA_STATE_ENDED"),
        ]
        with patch("TTS.chatbot._obs_client", client):
            wait_for_obs_playback_end()

        self.assertEqual(client.get_media_input_status.call_count, 3)
        self.assertEqual(sleep.call_count, 2)


class TwitchEventTests(SoundBiteDirectoryMixin, TestCase):
    def chat_event(self, text, badges=()):
        return {
            "chatter_user_id": "42",
            "chatter_user_login": "viewer",
            "chatter_user_name": "Viewer",
            "message": {"text": text},
            "badges": [{"set_id": badge} for badge in badges],
        }

    def test_parse_chat_command(self):
        self.assertEqual(twitch.parse_chat_command("!TTS  coucou toi", "!tts"), "coucou toi")
        self.assertIsNone(twitch.parse_chat_command("!ttsx coucou", "!tts"))
        self.assertIsNone(twitch.parse_chat_command("coucou", "!tts"))
        self.assertIsNone(twitch.parse_chat_command("!tts coucou", ""))

    def test_chat_command_creates_request(self):
        twitch.handle_event(twitch.CHAT_EVENT, self.chat_event("!tts Salut {bruh}"))

        request = TTSRequest.objects.get()
        self.assertEqual(request.message, "Salut {bruh}")
        self.assertEqual(request.twitch_login, "viewer")
        self.assertEqual(request.source, TTSRequest.Source.CHAT)

    def test_regular_chat_message_is_ignored(self):
        twitch.handle_event(twitch.CHAT_EVENT, self.chat_event("bonjour"))

        self.assertFalse(TTSRequest.objects.exists())

    def test_configured_reward_creates_request(self):
        self.configure(reward_title="Fais parler le stream", auto_approve=True)
        event = {
            "user_id": "42", "user_login": "viewer", "user_name": "Viewer",
            "user_input": "Coucou", "reward": {"title": "fais parler le stream"},
        }

        twitch.handle_event(twitch.REWARD_EVENT, event)

        request = TTSRequest.objects.get()
        self.assertEqual(request.source, TTSRequest.Source.REWARD)
        self.assertEqual(request.status, TTSRequest.Status.APPROVED)

    def test_other_reward_is_ignored(self):
        self.configure(reward_title="TTS")
        event = {"user_id": "1", "user_input": "x", "reward": {"title": "Hydrate"}}

        twitch.handle_event(twitch.REWARD_EVENT, event)

        self.assertFalse(TTSRequest.objects.exists())


class TwitchOAuthTests(TestCase):
    def setUp(self):
        env = patch.dict("os.environ", {
            "TWITCH_CLIENT_ID": "client", "TWITCH_CLIENT_SECRET": "secret",
        })
        env.start()
        self.addCleanup(env.stop)

    def test_connect_redirects_to_twitch_with_state(self):
        response = self.client.get(reverse("twitch_connect"))

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].startswith(twitch.AUTHORIZE_URL))
        self.assertIn("user%3Aread%3Achat", response["Location"])
        self.assertTrue(TwitchConnection.load().oauth_state)

    @patch("listings.twitch.requests.get")
    @patch("listings.twitch.requests.post")
    def test_callback_stores_tokens(self, post, get):
        TwitchConnection.update_fields(oauth_state="expected")
        post.return_value = MagicMock(
            status_code=200, json=lambda: {"access_token": "a", "refresh_token": "r"}
        )
        get.return_value = MagicMock(
            status_code=200, json=lambda: {"user_id": "9", "login": "streamer", "client_id": "client"}
        )

        response = self.client.get(reverse("twitch_callback"), {"code": "c", "state": "expected"})

        self.assertRedirects(response, reverse("moderation"))
        connection = TwitchConnection.load()
        self.assertEqual(
            (connection.access_token, connection.refresh_token, connection.login),
            ("a", "r", "streamer"),
        )
        self.assertEqual(connection.oauth_state, "")

    @patch("listings.twitch.requests.post")
    def test_callback_with_wrong_state_is_refused(self, post):
        TwitchConnection.update_fields(oauth_state="expected")

        self.client.get(reverse("twitch_callback"), {"code": "c", "state": "forged"})

        post.assert_not_called()
        self.assertEqual(TwitchConnection.load().access_token, "")

    @patch("listings.twitch.requests.get")
    @patch("listings.twitch.requests.post")
    def test_expired_token_is_refreshed(self, post, get):
        TwitchConnection.update_fields(access_token="old", refresh_token="r1")
        post.return_value = MagicMock(
            status_code=200, json=lambda: {"access_token": "new", "refresh_token": "r2"}
        )
        get.side_effect = [
            MagicMock(status_code=401),
            MagicMock(status_code=200, json=lambda: {"user_id": "9", "login": "s", "client_id": "c"}),
        ]

        token, identity = twitch.get_valid_access_token()

        self.assertEqual(token, "new")
        self.assertEqual(TwitchConnection.load().refresh_token, "r2")


class ModerationPanelTests(SoundBiteDirectoryMixin, TestCase):
    def pending(self, message="Coucou"):
        return TTSRequest.objects.create(
            source=TTSRequest.Source.CHAT, status=TTSRequest.Status.PENDING,
            message=message, twitch_login="viewer", display_name="Viewer",
        )

    def test_panel_lists_pending_requests(self):
        self.pending("Message à modérer")

        response = self.client.get(reverse("moderation"))

        self.assertContains(response, "Message à modérer")
        self.assertContains(response, "Validation manuelle")

    def test_root_redirects_to_panel(self):
        self.assertRedirects(self.client.get("/"), reverse("moderation"))

    def test_approve_and_reject(self):
        approved, rejected = self.pending(), self.pending()

        self.client.post(reverse("moderation_action", args=[approved.pk, "approve"]))
        self.client.post(reverse("moderation_action", args=[rejected.pk, "reject"]))

        approved.refresh_from_db()
        rejected.refresh_from_db()
        self.assertEqual(approved.status, TTSRequest.Status.APPROVED)
        self.assertEqual(rejected.status, TTSRequest.Status.REJECTED)

    def test_actions_require_post(self):
        request = self.pending()

        response = self.client.get(reverse("moderation_action", args=[request.pk, "approve"]))

        self.assertEqual(response.status_code, 405)

    def test_replay_creates_new_approved_copy(self):
        played = self.pending()
        TTSRequest.objects.filter(pk=played.pk).update(status=TTSRequest.Status.PLAYED)

        self.client.post(reverse("moderation_action", args=[played.pk, "replay"]))

        self.assertEqual(
            TTSRequest.objects.filter(status=TTSRequest.Status.APPROVED, message="Coucou").count(), 1
        )

    def test_toggle_auto_mode(self):
        self.client.post(reverse("moderation_toggle", args=["auto_approve"]))

        self.assertTrue(ModerationSettings.load().auto_approve)

    def test_unknown_toggle_is_ignored(self):
        response = self.client.post(reverse("moderation_toggle", args=["banned_users"]))

        self.assertRedirects(response, reverse("moderation"))

    def test_settings_form_saves(self):
        response = self.client.post(reverse("moderation"), {
            "chat_command": "!parle",
            "chat_access": "vips",
            "reward_title": "",
            "user_cooldown_seconds": 10,
            "max_message_length": 200,
            "banned_words": "",
            "banned_users": "",
        })

        self.assertRedirects(response, reverse("moderation"))
        settings = ModerationSettings.load()
        self.assertEqual(settings.chat_command, "!parle")
        self.assertFalse(settings.trust_moderators)

    @patch("listings.views.stop_obs_playback")
    def test_skip_stops_obs(self, stop):
        self.client.post(reverse("moderation_skip"))

        stop.assert_called_once()

    def test_queue_fragment(self):
        self.pending("Fragment")

        response = self.client.get(reverse("moderation_queue"))

        self.assertContains(response, "Fragment")
        self.assertContains(response, "Bot arrêté")
