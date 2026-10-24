import logging
from pathlib import Path

from django.shortcuts import render

from TTS.chatbot import (
    TTSGenerationError,
    VOICE_ID,
    send_audio_sequence_to_obs,
    send_audio_to_obs,
    url_tts,
)

from .form import PromptForm, SOUND_BITE_TAG_PATTERN


logger = logging.getLogger(__name__)


def index(request):
    audio_url = None
    error = None

    if request.method == 'POST':
        form = PromptForm(request.POST)
        if form.is_valid():
            try:
                message = form.cleaned_data["message"]
                if form.sound_bite_tags or form.voice_tags:
                    _send_message_with_tags(
                        message,
                        form.sound_bite_tags,
                        form.voice_tags,
                    )
                else:
                    audio_url = url_tts(message, VOICE_ID)
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


def _send_message_with_tags(
    message: str,
    sound_bite_tags: dict[str, Path],
    voice_tags: dict[str, str],
) -> None:
    audio_sequence: list[str | Path] = []
    position = 0
    voice_id = VOICE_ID
    for match in SOUND_BITE_TAG_PATTERN.finditer(message):
        normalized_tag = match.group(1).strip().casefold()
        text = message[position : match.start()]
        if text.strip():
            audio_sequence.append(url_tts(text, voice_id))
        if normalized_tag in sound_bite_tags:
            audio_sequence.append(sound_bite_tags[normalized_tag])
        else:
            voice_id = voice_tags[normalized_tag]
        position = match.end()

    remaining_text = message[position:]
    if remaining_text.strip():
        audio_sequence.append(url_tts(remaining_text, voice_id))
    send_audio_sequence_to_obs(audio_sequence)