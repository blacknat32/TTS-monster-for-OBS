from unittest.mock import patch

from django.test import TestCase


class TTSFormTests(TestCase):
    def test_get_displays_message_form(self):
        response = self.client.get("/listings/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Message à convertir en audio")
        self.assertContains(response, 'name="message"')

    @patch("listings.views.send_audio_to_obs")
    @patch("listings.views.url_tts")
    def test_valid_post_sends_audio_to_obs_and_displays_audio(
        self, generate_audio, send_to_obs
    ):
        generate_audio.return_value = "https://example.com/generated.wav"

        response = self.client.post("/listings/", {"message": "Bonjour"})

        self.assertEqual(response.status_code, 200)
        generate_audio.assert_called_once_with("Bonjour")
        send_to_obs.assert_called_once_with("https://example.com/generated.wav")
        self.assertContains(response, "https://example.com/generated.wav")

    @patch("listings.views.send_audio_to_obs")
    @patch("listings.views.url_tts")
    def test_invalid_post_does_not_generate_audio(self, generate_audio, send_to_obs):
        response = self.client.post("/listings/", {"message": ""})

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

        response = self.client.post("/listings/", {"message": "Bonjour"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "OBS service is unavailable.")
