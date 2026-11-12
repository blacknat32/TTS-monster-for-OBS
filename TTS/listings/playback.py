"""Transforme un message validé (texte + balises) en audio joué dans OBS.

Utilisé à la fois par :

- la page ``/message/`` (le streamer envoie un message lui-même) ;
- le worker du bot Twitch (``worker.py``), qui joue les demandes du chat.

Le message est découpé en **sections**, une par voix. Exemple :

    « Salut (bruh) Kermit: Coucou (vine-boom) »
    → section SpongeBob : « Salut », son bruh
    → section Kermit    : « Coucou », son vine-boom

Les sections servent à savoir quelle voix lit quel texte, et quelles voix
apparaissent dans le message. Le son, lui, reste un seul clip assemblé.

Dès que le clip démarre, les images de ces voix (sources « SpongeBob OBS
Image » puis « Kermit OBS Image » ici) montent une par une du bas de l'écran,
puis redescendent ensemble à la fin. L'animation et ses réglages (durées,
décalage) sont dans ``VoiceImageAnimation``, dans ``TTS/chatbot.py``.
"""

from dataclasses import dataclass, field
from pathlib import Path

from TTS.chatbot import (
    VOICE_ID,
    VoiceImageAnimation,
    send_audio_sequence_to_obs,
    send_audio_to_obs,
    url_tts,
    wait_for_obs_playback_end,
)

from .form import SOUND_BITE_TAG_PATTERN, VOICE_CHOICES


# Identifiant TTS Monster → nom de la voix (« Adam »…), pour trouver son image.
VOICE_LABELS_BY_ID = {voice_id: label for voice_id, label in VOICE_CHOICES}
ALL_VOICE_LABELS = [label for _, label in VOICE_CHOICES]


@dataclass
class Section:
    """Un morceau du message dit par une seule voix.

    ``parts`` contient, dans l'ordre, du texte à lire (``str``) et des sons
    (``Path``).
    """

    voice_id: str
    parts: list[str | Path] = field(default_factory=list)


def split_into_sections(
    message: str,
    sound_bite_tags: dict[str, Path],
    voice_tags: dict[str, str],
) -> list[Section]:
    """Découpe le message en sections : une nouvelle à chaque changement de voix.

    Le texte placé avant toute voix est dit avec la voix par défaut
    (SpongeBob). Les sections vides (deux voix à la suite) sont ignorées.
    """
    sections = [Section(VOICE_ID)]
    position = 0
    for match in SOUND_BITE_TAG_PATTERN.finditer(message):
        text = message[position : match.start()]
        if text.strip():
            sections[-1].parts.append(text)
        normalized_tag = match.group(1).strip().casefold()
        if normalized_tag in sound_bite_tags:
            sections[-1].parts.append(sound_bite_tags[normalized_tag])
        else:
            sections.append(Section(voice_tags[normalized_tag]))
        position = match.end()

    remaining_text = message[position:]
    if remaining_text.strip():
        sections[-1].parts.append(remaining_text)
    return [section for section in sections if section.parts]


def play_message(
    message: str,
    sound_bite_tags: dict[str, Path],
    voice_tags: dict[str, str],
) -> str | None:
    """Joue le message dans OBS en un seul clip, avec les images de ses voix.

    1. Le clip est généré puis lancé dans OBS (texte et sons en un seul fichier).
    2. Dès qu'il démarre, les images des voix montent une par une, en
       arrière-plan : le son n'attend pas l'animation.
    3. On attend la fin du clip (ou le bouton « Passer »).
    4. Les images redescendent ensemble, puis sont masquées et remises à leur
       place, même en cas d'erreur.

    Retourne l'URL TTS Monster pour un message sans balise (affichée sur la
    page /message/), sinon ``None``.
    """
    sections = split_into_sections(message, sound_bite_tags, voice_tags)
    animation = VoiceImageAnimation(message_voice_labels(sections), ALL_VOICE_LABELS)
    audio_url = None
    try:
        if not sound_bite_tags and not voice_tags:
            # Message sans balise : un seul fichier TTS Monster, envoyé tel quel.
            audio_url = url_tts(message, VOICE_ID)
            send_audio_to_obs(audio_url)
        else:
            # Tout le texte (chaque morceau avec sa voix) et les sons, dans
            # l'ordre, assemblés et normalisés par FFmpeg en un seul fichier.
            send_audio_sequence_to_obs([
                url_tts(part, section.voice_id) if isinstance(part, str) else part
                for section in sections
                for part in section.parts
            ])
        # Le clip vient de démarrer dans OBS : on lance la montée des images.
        # On ne le fait pas avant, sinon les images monteraient pendant que
        # TTS Monster génère encore la voix.
        animation.start()
        wait_for_obs_playback_end()
    finally:
        # Descente, puis images masquées et remises en place, même en cas
        # d'erreur (sans effet si l'animation n'a pas démarré).
        animation.stop()
    return audio_url


def message_voice_labels(sections: list[Section]) -> list[str]:
    """Noms des voix utilisées dans le message, sans doublon, dans l'ordre."""
    labels: list[str] = []
    for section in sections:
        label = VOICE_LABELS_BY_ID.get(section.voice_id)
        if label and label not in labels:
            labels.append(label)
    return labels
