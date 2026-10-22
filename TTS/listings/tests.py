from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase

from TTS.chatbot import (
    TTSGenerationError,
    VOICE_ID,
    _get_obs_local_file_path,
    _start_media_playback,
    url_tts,
)


class TTSFormTests(TestCase):
    def test_get_displays_message_form(self):
        response = self.client.get("/listings/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Message à convertir en audio")
        self.assertContains(response, 'name="message"')
        self.assertContains(response, '<select name="voice_id"')
        self.assertContains(
            response, f'<option value="{VOICE_ID}" selected>Spongey</option>'
        )
        self.assertContains(
            response,
            '<option value="c4ad44ae-8da9-4375-90c0-55a1e6f1fbc6">Atlas</option>',
        )
        self.assertContains(
            response,
            '<option value="0993f688-6719-4cf6-9769-fee7b77b1df5">Brian</option>',
        )
        self.assertContains(
            response,
            '<option value="68bc5eb0-fa1f-4d9b-b0bc-89f51edf5fb0">Yuki</option>',
        )

    @patch("listings.views.send_audio_to_obs")
    @patch("listings.views.url_tts")
    def test_valid_post_sends_audio_to_obs_and_displays_audio(
        self, generate_audio, send_to_obs
    ):
        generate_audio.return_value = "https://example.com/generated.wav"

        response = self.client.post(
            "/listings/", {"message": "Bonjour", "voice_id": VOICE_ID}
        )

        self.assertEqual(response.status_code, 200)
        generate_audio.assert_called_once_with("Bonjour", VOICE_ID)
        send_to_obs.assert_called_once_with("https://example.com/generated.wav")
        self.assertContains(response, "https://example.com/generated.wav")

    @patch("listings.views.send_audio_to_obs")
    @patch("listings.views.url_tts")
    def test_invalid_post_does_not_generate_audio(self, generate_audio, send_to_obs):
        response = self.client.post(
            "/listings/", {"message": "", "voice_id": VOICE_ID}
        )

        self.assertEqual(response.status_code, 200)
        generate_audio.assert_not_called()
        send_to_obs.assert_not_called()
        self.assertContains(response, "This field is required.")

    @patch("listings.views.send_audio_to_obs")
    @patch("listings.views.url_tts")
    def test_obs_error_is_displayed(self, generate_audio, send_to_obs):
        from TTS.chatbot import TTSGenerationError

        generate_audio.return_value = "https://example.com/generated.wav"
        send_to_obs.side_effect = TTSGenerationError("OBS service is unavailable.")

        response = self.client.post(
            "/listings/", {"message": "Bonjour", "voice_id": VOICE_ID}
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "OBS service is unavailable.")


class OBSPlaybackTests(TestCase):
    @patch.dict("os.environ", {"TTS_MONSTER_API_KEY": "test-key"})
    @patch("TTS.chatbot.requests.post")
    def test_url_tts_sends_selected_voice_id(self, post):
        post.return_value.json.return_value = {"url": "https://example.com/audio.wav"}

        audio_url = url_tts("Bonjour", "another-voice-id")

        self.assertEqual(audio_url, "https://example.com/audio.wav")
        self.assertEqual(post.call_args.kwargs["json"]["voice_id"], "another-voice-id")

    @patch("TTS.chatbot.subprocess.run")
    def test_windows_obs_receives_a_windows_path_from_wsl(self, run):
        resolved_path = SimpleNamespace(
            as_posix=lambda: "/mnt/c/audio/generated.wav"
        )
        audio_path = SimpleNamespace(resolve=lambda: resolved_path)
        run.return_value.stdout = "C:\\audio\\generated.wav\r\n"

        local_file = _get_obs_local_file_path(audio_path, "windows")

        self.assertEqual(local_file, "C:\\audio\\generated.wav")
        run.assert_called_once_with(
            ["wslpath", "-w", "/mnt/c/audio/generated.wav"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )

    def test_windows_obs_keeps_native_windows_path(self):
        resolved_path = SimpleNamespace(as_posix=lambda: "C:/audio/generated.wav")
        audio_path = SimpleNamespace(resolve=lambda: resolved_path)

        local_file = _get_obs_local_file_path(audio_path, "windows")
        self.assertEqual(local_file, "C:/audio/generated.wav")

    @patch("TTS.chatbot.time.sleep")
    def test_playback_retries_with_play_when_restart_does_not_start(
        self, sleep
    ):
        class FakeOBSClient:
            def __init__(self):
                self.actions = []

            def trigger_media_input_action(self, input_name, action):
                self.actions.append(action)

            def get_media_input_status(self, input_name):
                state = (
                    "OBS_MEDIA_STATE_PLAYING"
                    if self.actions[-1]
                    == "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_PLAY"
                    else "OBS_MEDIA_STATE_STOPPED"
                )
                return SimpleNamespace(media_state=state)

        client = FakeOBSClient()

        _start_media_playback(client, "TTS Audio")

        self.assertEqual(
            client.actions,
            [
                "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_RESTART",
                "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_PLAY",
            ],
        )

    @patch("TTS.chatbot.time.sleep")
    def test_playback_failure_is_reported(self, sleep):
        class FakeOBSClient:
            def trigger_media_input_action(self, input_name, action):
                pass

            def get_media_input_status(self, input_name):
                return SimpleNamespace(media_state="OBS_MEDIA_STATE_ERROR")

        with self.assertRaisesRegex(TTSGenerationError, "did not start playback"):
            _start_media_playback(FakeOBSClient(), "TTS Audio")
