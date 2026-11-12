"""Transforme un message validé (texte + balises) en audio joué dans OBS.

Ce code était avant dans ``views.py`` (fonction ``_send_message_with_tags``).
Il a été déplacé ici, sans changement de comportement, pour être utilisé à la
fois par :

- la page ``/message/`` (le streamer envoie un message lui-même) ;
- le worker du bot Twitch (``worker.py``), qui joue les demandes du chat.
"""

from pathlib import Path

from TTS.chatbot import (
    VOICE_ID,
    send_audio_sequence_to_obs,
    send_audio_to_obs,
    url_tts,
)

from .form import SOUND_BITE_TAG_PATTERN


def play_message(
    message: str,
    sound_bite_tags: dict[str, Path],
    voice_tags: dict[str, str],
) -> str | None:
    """Joue le message dans OBS.

    Retourne l'URL TTS Monster pour un message sans balise, sinon ``None``
    (le clip assemblé n'existe que sur le disque local).
    """
    if sound_bite_tags or voice_tags:
        _send_message_with_tags(message, sound_bite_tags, voice_tags)
        return None

    audio_url = url_tts(message, VOICE_ID)
    send_audio_to_obs(audio_url)
    return audio_url


def _send_message_with_tags(
    message: str,
    sound_bite_tags: dict[str, Path],
    voice_tags: dict[str, str],
) -> None:
    """Construit une séquence audio en mixant du texte, des sons et des changements de voix."""
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
