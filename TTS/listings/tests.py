import errno
import math
import os
import sys
import unittest
import wave
from array import array
from pathlib import Path
from struct import pack
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import requests
from django.test import TestCase
from django.urls import reverse

from TTS.chatbot import (
    TTSGenerationError,
    VOICE_ID,
    AUDIO_CLEANUP_DELAY_SECONDS,
    AUDIO_CLEANUP_RETRY_SECONDS,
    _compile_audio_files,
    _find_ffmpeg,
    _delete_audio_file,
    _delete_replaced_generated_audio,
    _download_audio_file,
    _get_obs_local_file_path,
    _get_previous_generated_audio_path,
    _load_obs_settings,
    _schedule_audio_file_cleanup,
    _send_local_audio_to_obs,
    _start_media_playback,
    send_audio_sequence_to_obs,
    send_audio_to_obs,
    url_tts,
)
from listings.form import PromptForm


class TTSFormTests(TestCase):
    def setUp(self):
        self.sound_bites = TemporaryDirectory()
        self.addCleanup(self.sound_bites.cleanup)
        self.sound_bite_directory = Path(self.sound_bites.name)
        (self.sound_bite_directory / "bruh.mp3").touch()
        (self.sound_bite_directory / "wasted.mp3").touch()
        (self.sound_bite_directory / "notes.txt").touch()

        form_directory_patch = patch(
            "listings.form.SOUND_BITE_DIRECTORY", self.sound_bite_directory
        )
        form_directory_patch.start()
        self.addCleanup(form_directory_patch.stop)

    def test_get_displays_message_form(self):
        response = self.client.get(reverse("listings"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Message:")
        self.assertContains(response, 'name="message"')
        self.assertContains(response, "{bruh}")
        self.assertContains(response, "{Adam}")
        self.assertContains(response, 'aria-label="Balises voix"')
        self.assertContains(response, 'aria-label="Balises audio"')
        self.assertContains(
            response,
            'type="button" class="tag-button chip chip-voice" data-tag="{Adam}"',
        )
        self.assertContains(
            response,
            'type="button" class="tag-button chip chip-sound" data-tag="{bruh}"',
        )
        self.assertNotContains(response, 'data-tag="{notes}"')
        self.assertContains(response, "listings/prompt-tags.js")

    @patch("listings.views.send_audio_to_obs")
    @patch("listings.views.url_tts")
    def test_valid_post_sends_audio_to_obs_and_displays_audio(
        self, generate_audio, send_to_obs
    ):
        generate_audio.return_value = "https://example.com/generated.wav"

        response = self.client.post(
            reverse("listings"), {"message": "Bonjour"}
        )

        self.assertEqual(response.status_code, 200)
        generate_audio.assert_called_once_with("Bonjour", VOICE_ID)
        send_to_obs.assert_called_once_with("https://example.com/generated.wav")
        self.assertContains(response, "https://example.com/generated.wav")

    @patch("listings.views.send_audio_sequence_to_obs")
    @patch("listings.views.url_tts")
    def test_sound_tags_play_tts_and_sound_bites_in_message_order(
        self, generate_audio, send_sequence_to_obs
    ):
        generate_audio.side_effect = [
            "https://example.com/before.wav",
            "https://example.com/after.wav",
        ]

        response = self.client.post(
            reverse("listings"),
            {
                "message": "Before {bruh} after {wasted}",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            generate_audio.call_args_list,
            [
                (("Before ", VOICE_ID), {}),
                ((" after ", VOICE_ID), {}),
            ],
        )
        send_sequence_to_obs.assert_called_once_with(
            [
                "https://example.com/before.wav",
                self.sound_bite_directory / "bruh.mp3",
                "https://example.com/after.wav",
                self.sound_bite_directory / "wasted.mp3",
            ]
        )

    @patch("listings.views.url_tts")
    @patch("listings.views.send_audio_sequence_to_obs")
    def test_message_with_only_sound_tag_plays_sound_without_tts(
        self, send_sequence_to_obs, generate_audio
    ):
        response = self.client.post(
            reverse("listings"),
            {"message": "{bruh}"},
        )

        self.assertEqual(response.status_code, 200)
        send_sequence_to_obs.assert_called_once_with(
            [self.sound_bite_directory / "bruh.mp3"]
        )
        generate_audio.assert_not_called()

    @patch("listings.views.send_audio_sequence_to_obs")
    @patch("listings.views.url_tts")
    def test_voice_tags_switch_voice_for_following_text(
        self, generate_audio, send_sequence_to_obs
    ):
        generate_audio.side_effect = [
            "https://example.com/default.wav",
            "https://example.com/adam.wav",
            "https://example.com/brian.wav",
        ]

        response = self.client.post(
            reverse("listings"),
            {"message": "Default {Adam}Adam speaks {Brian}Brian speaks"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            generate_audio.call_args_list,
            [
                (("Default ", VOICE_ID), {}),
                (("Adam speaks ", "c4ad44ae-8da9-4375-90c0-55a1e6f1fbc6"), {}),
                (("Brian speaks", "0993f688-6719-4cf6-9769-fee7b77b1df5"), {}),
            ],
        )
        send_sequence_to_obs.assert_called_once_with(
            [
                "https://example.com/default.wav",
                "https://example.com/adam.wav",
                "https://example.com/brian.wav",
            ]
        )

    def test_unknown_tag_is_rejected(self):
        response = self.client.post(
            reverse("listings"),
            {"message": "Before {not_a_sound} after"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Balise inconnue : {not_a_sound}.")

    @patch("listings.views.send_audio_to_obs")
    @patch("listings.views.url_tts")
    def test_invalid_post_does_not_generate_audio(self, generate_audio, send_to_obs):
        response = self.client.post(
            reverse("listings"), {"message": ""}
        )

        self.assertEqual(response.status_code, 200)
        generate_audio.assert_not_called()
        send_to_obs.assert_not_called()
        self.assertContains(response, "This field is required.")

    def test_form_lists_voice_and_audio_tags_separately(self):
        form = PromptForm()

        self.assertEqual(form.sound_bite_names, ["bruh", "wasted"])
        self.assertIn("Adam", form.voice_labels)
        self.assertNotIn("bruh", form.voice_labels)

    @patch("listings.views.send_audio_to_obs")
    @patch("listings.views.url_tts")
    def test_obs_error_is_displayed(self, generate_audio, send_to_obs):
        generate_audio.return_value = "https://example.com/generated.wav"
        send_to_obs.side_effect = TTSGenerationError("OBS service is unavailable.")

        response = self.client.post(
            reverse("listings"), {"message": "Bonjour"}
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "OBS service is unavailable.")

    @patch("listings.views.send_audio_to_obs")
    @patch("listings.views.url_tts")
    def test_duplicate_clip_is_skipped_before_sending_to_obs(self, generate_audio, send_to_obs):
        generate_audio.return_value = "https://example.com/generated.wav"

        first_response = self.client.post(reverse("listings"), {"message": "Bonjour"})
        second_response = self.client.post(reverse("listings"), {"message": "Bonjour"})

        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(second_response.status_code, 200)
        self.assertEqual(send_to_obs.call_count, 1)
        self.assertContains(second_response, "Ce clip a déjà été joué.")
        self.assertContains(second_response, "File d'attente")
        self.assertContains(second_response, "Bonjour")

    @patch("listings.views.send_audio_sequence_to_obs")
    @patch("listings.views.url_tts")
    def test_tags_are_case_insensitive(self, generate_audio, send_sequence_to_obs):
        generate_audio.return_value = "https://example.com/adam.wav"

        response = self.client.post(reverse("listings"), {"message": "{BRUH} {adam}Salut"})

        self.assertEqual(response.status_code, 200)
        generate_audio.assert_called_once_with(
            "Salut", "c4ad44ae-8da9-4375-90c0-55a1e6f1fbc6"
        )
        send_sequence_to_obs.assert_called_once_with(
            [self.sound_bite_directory / "bruh.mp3", "https://example.com/adam.wav"]
        )

    def test_sound_named_like_a_voice_is_rejected(self):
        (self.sound_bite_directory / "Adam.mp3").touch()

        form = PromptForm(data={"message": "Salut {Adam}"})

        self.assertFalse(form.is_valid())
        self.assertIn("à la fois à un son et à une voix", str(form.errors))

    def test_sound_names_shared_by_several_files_are_rejected(self):
        (self.sound_bite_directory / "bruh.wav").touch()

        form = PromptForm(data={"message": "Salut {bruh}"})

        self.assertFalse(form.is_valid())
        self.assertIn("correspond à plusieurs sons", str(form.errors))

    def test_message_with_only_voice_tags_is_rejected(self):
        form = PromptForm(data={"message": "{Adam} {Brian}"})

        self.assertFalse(form.is_valid())
        self.assertIn("Ajoutez du texte ou une balise de son.", str(form.errors))

    @patch("listings.views.url_tts")
    def test_message_longer_than_limit_is_rejected(self, generate_audio):
        response = self.client.post(reverse("listings"), {"message": "a" * 1001})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "at most 1000 characters")
        generate_audio.assert_not_called()

    @patch("listings.views.send_audio_to_obs")
    @patch("listings.views.url_tts")
    def test_duplicate_detection_ignores_case_and_extra_spaces(
        self, generate_audio, send_to_obs
    ):
        generate_audio.return_value = "https://example.com/generated.wav"

        self.client.post(reverse("listings"), {"message": "Bonjour le chat"})
        response = self.client.post(
            reverse("listings"), {"message": "  bonjour   LE chat "}
        )

        self.assertEqual(send_to_obs.call_count, 1)
        self.assertContains(response, "Ce clip a déjà été joué.")

    @patch("listings.views.send_audio_to_obs")
    @patch("listings.views.url_tts")
    def test_failed_clip_is_not_marked_as_played(self, generate_audio, send_to_obs):
        generate_audio.return_value = "https://example.com/generated.wav"
        send_to_obs.side_effect = [TTSGenerationError("OBS is closed."), None]

        first_response = self.client.post(reverse("listings"), {"message": "Bonjour"})
        second_response = self.client.post(reverse("listings"), {"message": "Bonjour"})

        self.assertContains(first_response, "OBS is closed.")
        self.assertEqual(send_to_obs.call_count, 2)
        self.assertNotContains(second_response, "Ce clip a déjà été joué.")

    @patch("listings.views.send_audio_sequence_to_obs")
    @patch("listings.views.url_tts")
    def test_tagged_message_error_is_displayed(self, generate_audio, send_sequence_to_obs):
        generate_audio.return_value = "https://example.com/generated.wav"
        send_sequence_to_obs.side_effect = TTSGenerationError("FFmpeg failed.")

        response = self.client.post(reverse("listings"), {"message": "Salut {bruh}"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "FFmpeg failed.")

    @patch("listings.views.send_audio_to_obs")
    @patch("listings.views.url_tts")
    def test_queue_keeps_only_the_ten_latest_clips(self, generate_audio, send_to_obs):
        generate_audio.return_value = "https://example.com/generated.wav"

        for index in range(12):
            self.client.post(reverse("listings"), {"message": f"Message {index}"})

        queue = self.client.session["clip_queue"]
        self.assertEqual(len(queue), 10)
        self.assertEqual(queue[0]["label"], "Message 11")
        self.assertEqual(queue[-1]["label"], "Message 2")


class OBSPlaybackTests(TestCase):
    def test_replacing_obs_path_deletes_old_generated_audio_after_update(self):
        with TemporaryDirectory() as directory:
            audio_directory = Path(directory) / "tts"
            audio_directory.mkdir()
            old_audio = audio_directory / "old.wav"
            new_audio = audio_directory / "new.wav"
            old_audio.touch()
            new_audio.touch()
            client = MagicMock()
            client.get_version.return_value = SimpleNamespace(platform="linux")
            client.get_input_settings.return_value = SimpleNamespace(
                input_settings={
                    "is_local_file": True,
                    "local_file": str(old_audio),
                }
            )
            client.set_input_settings.side_effect = lambda *args, **kwargs: (
                self.assertTrue(old_audio.exists())
            )

            with (
                patch("TTS.chatbot.AUDIO_DIRECTORY", audio_directory),
                patch("TTS.chatbot._obs_client", client),
                patch("TTS.chatbot.connect_to_obs"),
                patch("TTS.chatbot._start_media_playback"),
            ):
                _send_local_audio_to_obs(
                    new_audio, ("localhost", 4455, "", "TTS Audio")
                )

            self.assertFalse(old_audio.exists())
            client.set_input_settings.assert_called_once()

    def test_failed_obs_path_update_keeps_old_generated_audio(self):
        with TemporaryDirectory() as directory:
            audio_directory = Path(directory) / "tts"
            audio_directory.mkdir()
            old_audio = audio_directory / "old.wav"
            new_audio = audio_directory / "new.wav"
            old_audio.touch()
            new_audio.touch()
            client = MagicMock()
            client.get_version.return_value = SimpleNamespace(platform="linux")
            client.get_input_settings.return_value = SimpleNamespace(
                input_settings={
                    "is_local_file": True,
                    "local_file": str(old_audio),
                }
            )
            client.set_input_settings.side_effect = OSError("OBS unavailable")

            with (
                patch("TTS.chatbot.AUDIO_DIRECTORY", audio_directory),
                patch("TTS.chatbot._obs_client", client),
                patch("TTS.chatbot.connect_to_obs"),
                patch("TTS.chatbot.disconnect_from_obs"),
                patch("TTS.chatbot._delete_audio_file") as delete_audio,
            ):
                with self.assertRaisesRegex(TTSGenerationError, "Cannot connect to OBS"):
                    _send_local_audio_to_obs(
                        new_audio, ("localhost", 4455, "", "TTS Audio")
                    )

            self.assertTrue(old_audio.exists())
            delete_audio.assert_not_called()

    def test_replaced_generated_audio_is_deleted_but_sound_bites_are_preserved(self):
        with TemporaryDirectory() as directory:
            audio_directory = Path(directory) / "tts"
            audio_directory.mkdir()
            old_audio = audio_directory / "old.wav"
            new_audio = audio_directory / "new.wav"
            old_audio.touch()
            new_audio.touch()
            sound_bite = Path(directory) / "Sound_Bites" / "bruh.mp3"
            sound_bite.parent.mkdir()
            sound_bite.touch()

            with patch("TTS.chatbot.AUDIO_DIRECTORY", audio_directory):
                _delete_replaced_generated_audio(
                    SimpleNamespace(
                        input_settings={
                            "is_local_file": True,
                            "local_file": str(old_audio),
                        }
                    ),
                    new_audio,
                    "linux",
                )
                _delete_replaced_generated_audio(
                    SimpleNamespace(
                        input_settings={
                            "is_local_file": True,
                            "local_file": str(sound_bite),
                        }
                    ),
                    new_audio,
                    "linux",
                )

            self.assertFalse(old_audio.exists())
            self.assertTrue(new_audio.exists())
            self.assertTrue(sound_bite.exists())

    def test_previous_path_outside_generated_audio_directory_is_ignored(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            audio_directory = root / "tts"
            audio_directory.mkdir()
            unrelated_audio = root / "Sound_Bites" / "bruh.mp3"
            unrelated_audio.parent.mkdir()
            unrelated_audio.touch()

            with patch("TTS.chatbot.AUDIO_DIRECTORY", audio_directory):
                previous_path = _get_previous_generated_audio_path(
                    str(unrelated_audio), "linux"
                )

            self.assertIsNone(previous_path)

    @patch("TTS.chatbot.threading.Timer")
    def test_generated_audio_cleanup_is_scheduled_after_five_minutes(self, timer):
        audio_path = Path("generated.wav")

        _schedule_audio_file_cleanup(audio_path)

        timer.assert_called_once_with(
            AUDIO_CLEANUP_DELAY_SECONDS,
            _delete_audio_file,
            args=(audio_path,),
        )
        self.assertEqual(AUDIO_CLEANUP_DELAY_SECONDS, 300)
        self.assertTrue(timer.return_value.daemon)
        timer.return_value.start.assert_called_once_with()

    def test_cleanup_deletes_generated_audio_file(self):
        with TemporaryDirectory() as directory:
            audio_path = Path(directory) / "generated.wav"
            audio_path.touch()

            _delete_audio_file(audio_path)

            self.assertFalse(audio_path.exists())

    @patch("TTS.chatbot.Path.unlink")
    @patch("TTS.chatbot.threading.Timer")
    def test_cleanup_retries_when_generated_file_is_locked(self, timer, unlink):
        audio_path = Path("generated.wav")
        unlink.side_effect = PermissionError(errno.EACCES, "file is in use")

        _delete_audio_file(audio_path)

        timer.assert_called_once_with(
            AUDIO_CLEANUP_RETRY_SECONDS,
            _delete_audio_file,
            args=(audio_path,),
        )
        timer.return_value.start.assert_called_once_with()

    @patch.dict("os.environ", {"TTS_MONSTER_API_KEY": "test-key"})
    @patch("TTS.chatbot.requests.post")
    def test_url_tts_sends_selected_voice_id(self, post):
        post.return_value.json.return_value = {"url": "https://example.com/audio.wav"}

        audio_url = url_tts("Bonjour", "another-voice-id")

        self.assertEqual(audio_url, "https://example.com/audio.wav")
        self.assertEqual(post.call_args.kwargs["json"]["voice_id"], "another-voice-id")

    @patch.dict("os.environ", {"TTS_MONSTER_API_KEY": "test-key"})
    @patch("TTS.chatbot.requests.post")
    def test_url_tts_reports_upstream_server_error_status(self, post):
        response = requests.Response()
        response.status_code = 500
        response._content = b'{"error": "Temporary synthesis failure"}'
        response.headers["x-request-id"] = "test-request-123"
        post.return_value.raise_for_status.side_effect = requests.HTTPError(
            response=response
        )

        with self.assertRaisesRegex(
            TTSGenerationError,
            "TTS Monster returned HTTP 500 \\(server error\\).*"
            "API detail: Temporary synthesis failure.*Request ID: test-request-123",
        ):
            url_tts("Test message")

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

    @unittest.skipUnless(_find_ffmpeg(), "FFmpeg is not installed")
    def test_compile_audio_files_concatenates_different_wav_formats(self):
        with TemporaryDirectory() as directory:
            working_directory = Path(directory)
            first_audio = working_directory / "first.wav"
            second_audio = working_directory / "second.wav"
            output_audio = working_directory / "combined.wav"
            self._write_test_wav(first_audio, sample_rate=8000, duration=0.1)
            self._write_test_wav(second_audio, sample_rate=22050, duration=0.2)

            _compile_audio_files([first_audio, second_audio], output_audio)

            with wave.open(str(output_audio), "rb") as combined:
                self.assertEqual(combined.getframerate(), 44100)
                self.assertEqual(combined.getnchannels(), 2)
                self.assertAlmostEqual(combined.getnframes() / 44100, 0.3, delta=0.02)

    @unittest.skipUnless(_find_ffmpeg(), "FFmpeg is not installed")
    def test_compile_audio_files_normalizes_each_segment_to_similar_volume(self):
        with TemporaryDirectory() as directory:
            working_directory = Path(directory)
            quiet_audio = working_directory / "quiet.wav"
            loud_audio = working_directory / "loud.wav"
            output_audio = working_directory / "combined.wav"
            self._write_test_wav(
                quiet_audio, sample_rate=44100, duration=4, amplitude=0.02
            )
            self._write_test_wav(
                loud_audio, sample_rate=44100, duration=4, amplitude=0.7
            )

            _compile_audio_files([quiet_audio, loud_audio], output_audio)

            with wave.open(str(output_audio), "rb") as combined:
                segment_frames = combined.getnframes() // 2
                first_segment = array(
                    "h", combined.readframes(segment_frames)
                )
                second_segment = array(
                    "h", combined.readframes(segment_frames)
                )

            first_rms = self._rms(first_segment)
            second_rms = self._rms(second_segment)
            self.assertLess(abs(first_rms - second_rms), max(first_rms, second_rms) * 0.1)

    @patch("TTS.chatbot._send_local_audio_to_obs")
    @patch("TTS.chatbot._compile_audio_files")
    @patch("TTS.chatbot._load_obs_settings")
    @patch("TTS.chatbot.requests.get")
    def test_audio_sequence_compiles_then_sends_one_clip_to_obs(
        self, get_audio, load_settings, compile_audio, send_to_obs
    ):
        response = MagicMock()
        response.headers = {"Content-Type": "audio/wav"}
        response.iter_content.return_value = [b"tts-audio"]
        get_audio.return_value.__enter__.return_value = response
        load_settings.return_value = ("localhost", 4455, "", "TTS Audio")
        send_to_obs.side_effect = lambda audio_path, settings: audio_path

        with (
            TemporaryDirectory() as sound_directory,
            TemporaryDirectory() as output_directory,
        ):
            sound_bite = Path(sound_directory) / "sound.mp3"
            sound_bite.touch()
            with (
                patch("TTS.chatbot.AUDIO_DIRECTORY", Path(output_directory)),
                patch("TTS.chatbot._schedule_audio_file_cleanup") as schedule_cleanup,
            ):
                result = send_audio_sequence_to_obs(
                    ["https://example.com/tts.wav", sound_bite]
                )

        self.assertEqual(get_audio.call_count, 1)
        self.assertEqual(compile_audio.call_count, 1)
        audio_paths, output_path = compile_audio.call_args.args
        self.assertEqual(result, output_path)
        self.assertEqual(len(audio_paths), 2)
        # The sequence resolves sound paths; on Windows runners the temp
        # directory has a short 8.3 alias (RUNNER~1) that resolve() expands.
        self.assertEqual(audio_paths[1], sound_bite.resolve())
        self.assertEqual(output_path.suffix, ".wav")
        send_to_obs.assert_called_once_with(output_path, load_settings.return_value)
        schedule_cleanup.assert_called_once_with(output_path)

    @patch("TTS.chatbot._schedule_audio_file_cleanup")
    @patch("TTS.chatbot._send_local_audio_to_obs")
    @patch("TTS.chatbot._load_obs_settings")
    @patch("TTS.chatbot.requests.get")
    def test_generated_audio_schedules_cleanup_after_obs_playback(
        self, get_audio, load_settings, send_to_obs, schedule_cleanup
    ):
        response = MagicMock()
        response.headers = {"Content-Type": "audio/wav"}
        response.iter_content.return_value = [b"tts-audio"]
        get_audio.return_value.__enter__.return_value = response
        obs_settings = ("localhost", 4455, "", "TTS Audio")
        load_settings.return_value = obs_settings
        send_to_obs.side_effect = lambda path, settings: path

        with TemporaryDirectory() as directory, patch(
            "TTS.chatbot.AUDIO_DIRECTORY", Path(directory)
        ):
            audio_path = send_audio_to_obs("https://example.com/tts.wav")
            self.assertTrue(audio_path.is_file())

        send_to_obs.assert_called_once_with(audio_path, obs_settings)
        schedule_cleanup.assert_called_once_with(audio_path)

    @staticmethod
    def _write_test_wav(path, sample_rate, duration, amplitude=0.0):
        frame_count = int(sample_rate * duration)
        frames = (
            pack(
                "<h",
                int(
                    amplitude
                    * 32767
                    * math.sin(2 * math.pi * 440 * index / sample_rate)
                ),
            )
            for index in range(frame_count)
        )
        with wave.open(str(path), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(sample_rate)
            audio.writeframes(b"".join(frames))

    @staticmethod
    def _rms(samples):
        return math.sqrt(sum(sample * sample for sample in samples) / len(samples))

    def test_obs_receives_local_file_flag_and_path(self):
        with TemporaryDirectory() as directory:
            audio_path = Path(directory) / "new.wav"
            audio_path.touch()
            client = MagicMock()
            client.get_version.return_value = SimpleNamespace(platform="linux")

            with (
                patch("TTS.chatbot._obs_client", client),
                patch("TTS.chatbot.connect_to_obs"),
                patch("TTS.chatbot._start_media_playback"),
            ):
                _send_local_audio_to_obs(
                    audio_path, ("localhost", 4455, "", "TTS Audio")
                )

            # Sans "is_local_file", OBS ignore le chemin et reste à 00:00.
            client.set_input_settings.assert_called_once_with(
                "TTS Audio",
                {
                    "is_local_file": True,
                    "local_file": audio_path.resolve().as_posix(),
                },
                overlay=True,
            )


class TTSMonsterAPITests(TestCase):
    def test_missing_api_key_is_reported_without_calling_the_api(self):
        with (
            patch.dict(os.environ),
            patch("TTS.chatbot.requests.post") as post,
        ):
            os.environ.pop("TTS_MONSTER_API_KEY", None)
            with self.assertRaisesRegex(TTSGenerationError, "TTS_MONSTER_API_KEY"):
                url_tts("Bonjour")

        post.assert_not_called()

    @patch.dict("os.environ", {"TTS_MONSTER_API_KEY": "test-key"})
    @patch("TTS.chatbot.requests.post")
    def test_rejected_request_reports_status_and_api_detail(self, post):
        response = requests.Response()
        response.status_code = 401
        response._content = b'{"detail": "Invalid API key"}'
        post.return_value.raise_for_status.side_effect = requests.HTTPError(
            response=response
        )

        with self.assertRaisesRegex(
            TTSGenerationError,
            "rejected the request with HTTP 401.*API detail: Invalid API key",
        ):
            url_tts("Bonjour")

    @patch.dict("os.environ", {"TTS_MONSTER_API_KEY": "test-key"})
    @patch("TTS.chatbot.requests.post")
    def test_network_error_is_reported(self, post):
        post.side_effect = requests.ConnectionError("no network")

        with self.assertRaisesRegex(TTSGenerationError, "request failed"):
            url_tts("Bonjour")

    @patch.dict("os.environ", {"TTS_MONSTER_API_KEY": "test-key"})
    @patch("TTS.chatbot.requests.post")
    def test_response_without_audio_url_is_reported(self, post):
        post.return_value.json.return_value = {"status": "ok"}

        with self.assertRaisesRegex(TTSGenerationError, "did not include an audio URL"):
            url_tts("Bonjour")


class OBSSettingsTests(TestCase):
    def _load(self, content):
        with TemporaryDirectory() as directory:
            config_path = Path(directory) / "config file"
            config_path.write_text(content, encoding="utf-8")
            with patch("TTS.chatbot.OBS_CONFIG_PATH", config_path):
                return _load_obs_settings()

    def test_valid_config_is_loaded(self):
        settings = self._load(
            '[connection]\nhost = "localhost"\nport = 4455\npassword = "secret"\n'
            '[audio]\ninput_name = "TTS Audio"\n'
        )

        self.assertEqual(settings, ("localhost", 4455, "secret", "TTS Audio"))

    def test_placeholder_password_means_no_password(self):
        settings = self._load(
            '[connection]\nhost = "localhost"\nport = 4455\npassword = "None"\n'
            '[audio]\ninput_name = "TTS Audio"\n'
        )

        self.assertEqual(settings[2], "")

    def test_incomplete_config_is_reported(self):
        with self.assertRaisesRegex(TTSGenerationError, "missing or invalid"):
            self._load('[connection]\nhost = "localhost"\n')

    def test_missing_config_file_is_reported(self):
        with (
            TemporaryDirectory() as directory,
            patch("TTS.chatbot.OBS_CONFIG_PATH", Path(directory) / "config file"),
        ):
            with self.assertRaisesRegex(TTSGenerationError, "missing or invalid"):
                _load_obs_settings()


class AudioFileTests(TestCase):
    def _download(self, audio_url, content_type):
        response = MagicMock()
        response.headers = {"Content-Type": content_type}
        response.iter_content.return_value = [b"audio-bytes"]
        with (
            TemporaryDirectory() as directory,
            patch("TTS.chatbot.requests.get") as get_audio,
        ):
            get_audio.return_value.__enter__.return_value = response
            audio_path = _download_audio_file(
                audio_url, Path(directory), "clip", "test audio"
            )
            return audio_path.name, audio_path.read_bytes()

    def test_download_uses_extension_from_content_type(self):
        name, content = self._download("https://example.com/clip", "audio/mpeg")

        self.assertEqual(name, "clip.mp3")
        self.assertEqual(content, b"audio-bytes")

    def test_download_falls_back_to_wav_for_unknown_types(self):
        name, _ = self._download("https://example.com/clip", "application/octet-stream")

        self.assertEqual(name, "clip.wav")

    def test_ffmpeg_on_path_is_preferred(self):
        with patch("TTS.chatbot.shutil.which", return_value="C:/tools/ffmpeg.exe"):
            self.assertEqual(_find_ffmpeg(), "C:/tools/ffmpeg.exe")

    def test_bundled_ffmpeg_is_used_when_none_is_on_path(self):
        bundled = SimpleNamespace(get_ffmpeg_exe=lambda: "bundled-ffmpeg")
        with (
            patch("TTS.chatbot.shutil.which", return_value=None),
            patch.dict(sys.modules, {"imageio_ffmpeg": bundled}),
        ):
            self.assertEqual(_find_ffmpeg(), "bundled-ffmpeg")

    def test_missing_ffmpeg_is_reported(self):
        with (
            patch("TTS.chatbot.shutil.which", return_value=None),
            patch.dict(sys.modules, {"imageio_ffmpeg": None}),
        ):
            self.assertIsNone(_find_ffmpeg())
            with self.assertRaisesRegex(TTSGenerationError, "FFmpeg is required"):
                _compile_audio_files([Path("a.wav")], Path("out.wav"))

    @patch("TTS.chatbot._send_local_audio_to_obs")
    @patch("TTS.chatbot._load_obs_settings")
    def test_failed_compilation_removes_partial_file_and_skips_obs(
        self, load_settings, send_to_obs
    ):
        load_settings.return_value = ("localhost", 4455, "", "TTS Audio")

        def fail_after_writing(audio_paths, output_path):
            output_path.write_bytes(b"partial")
            raise TTSGenerationError("FFmpeg crashed.")

        with (
            TemporaryDirectory() as sound_directory,
            TemporaryDirectory() as output_directory,
        ):
            sound_bite = Path(sound_directory) / "bruh.mp3"
            sound_bite.touch()
            with (
                patch("TTS.chatbot.AUDIO_DIRECTORY", Path(output_directory)),
                patch("TTS.chatbot._compile_audio_files", fail_after_writing),
            ):
                with self.assertRaisesRegex(TTSGenerationError, "FFmpeg crashed."):
                    send_audio_sequence_to_obs([sound_bite])

            self.assertEqual(list(Path(output_directory).iterdir()), [])
        send_to_obs.assert_not_called()
