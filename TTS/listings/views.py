import logging

from django.shortcuts import render

from TTS.chatbot import TTSGenerationError, send_audio_to_obs, url_tts

from .form import PromptForm


logger = logging.getLogger(__name__)


def index(request):
    audio_url = None
    error = None

    if request.method == 'POST':
        form = PromptForm(request.POST)
        if form.is_valid():
            try:
                audio_url = url_tts(
                    form.cleaned_data["message"], form.cleaned_data["voice_id"]
                )
                send_audio_to_obs(audio_url)
            except TTSGenerationError as exc:
                logger.exception("Unable to generate or send TTS audio to OBS")
                error = str(exc)
    else:
        form = PromptForm()

    return render(
        request,
        "listings/index.html",
        {"form": form, "audio_url": audio_url, "error": error},
    )