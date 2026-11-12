import hashlib
import logging
from pathlib import Path

from django.http import JsonResponse
from django.contrib import messages
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from TTS.chatbot import (
    TTSGenerationError,
    VOICE_ID,
    send_audio_sequence_to_obs,
    send_audio_to_obs,
    url_tts,
)

from .form import PlayerVoiceForm, PromptForm, SOUND_BITE_TAG_PATTERN, VOICE_CHOICES
from .models import PlayerSlot
from .players import ensure_player_slots


logger = logging.getLogger(__name__)


def _get_clip_queue(request):
    """Lit la file d'attente en session et la normalise pour l'affichage HTML."""
    queued_clips = request.session.get("clip_queue", [])
    normalized_queue = []
    for clip in queued_clips:
        if not isinstance(clip, dict):
            continue
        label = str(clip.get("label", "Clip")).strip() or "Clip"
        status = "Joué" if clip.get("played") else "En attente"
        normalized_queue.append({
            "clip_key": str(clip.get("clip_key", "")),
            "label": label,
            "played": bool(clip.get("played")),
            "status": status,
        })
    request.session["clip_queue"] = normalized_queue[:10]
    return normalized_queue


def _clip_key(message: str) -> str:
    """Crée un identifiant stable pour comparer deux messages identiques entre eux."""
    normalized_message = " ".join(message.strip().split())
    return hashlib.sha256(normalized_message.casefold().encode("utf-8")).hexdigest()


def _clip_already_played(request, message: str) -> bool:
    """Retourne vrai si ce message a déjà été envoyé à OBS dans la session actuelle."""
    queue = _get_clip_queue(request)
    return any(item["clip_key"] == _clip_key(message) and item["played"] for item in queue)


def _queue_clip(request, message: str) -> None:
    """Ajoute un message à la file d'attente et le marque comme déjà joué."""
    clip_key = _clip_key(message)
    queue = _get_clip_queue(request)
    queued_entry = next((item for item in queue if item["clip_key"] == clip_key), None)
    if queued_entry is None:
        queue.insert(0, {
            "clip_key": clip_key,
            "label": " ".join(message.strip().split())[:80],
            "played": True,
            "status": "Joué",
        })
    else:
        queued_entry["played"] = True
        queued_entry["status"] = "Joué"
    request.session["clip_queue"] = queue[:10]


def index(request):
    """Affiche le formulaire et envoie le texte ou les balises vers OBS si les données sont valides."""
    audio_url = None
    error = None
    clip_queue = _get_clip_queue(request)

    if request.method == 'POST':
        form = PromptForm(request.POST)
        if form.is_valid():
            try:
                message = form.cleaned_data["message"]
                if _clip_already_played(request, message):
                    error = "Ce clip a déjà été joué. Il n'est pas renvoyé dans OBS."
                elif form.sound_bite_tags or form.voice_tags:
                    _send_message_with_tags(
                        message,
                        form.sound_bite_tags,
                        form.voice_tags,
                    )
                    _queue_clip(request, message)
                else:
                    audio_url = url_tts(message, VOICE_ID)
                    send_audio_to_obs(audio_url)
                    _queue_clip(request, message)
                clip_queue = _get_clip_queue(request)
            except TTSGenerationError as exc:
                logger.exception("Unable to generate or send TTS audio to OBS")
                error = str(exc)
    else:
        form = PromptForm()

    return render(
        request,
        "listings/index.html",
        {"form": form, "audio_url": audio_url, "error": error, "clip_queue": clip_queue},
    )


def players(request):
    ensure_player_slots()
    return render(
        request,
        "listings/players.html",
        {
            "players": PlayerSlot.objects.all(),
            "player_numbers": range(1, 4),
            "voice_choices": VOICE_CHOICES,
        },
    )


@never_cache
def players_status(request):
    ensure_player_slots()
    voice_labels = dict((voice_id, label) for voice_id, label in VOICE_CHOICES)
    return JsonResponse(
        {
            "players": [
                {
                    "number": slot.player_number,
                    "username": slot.display_name or slot.username or None,
                    "voice_id": slot.voice_id,
                    "voice_name": voice_labels[slot.voice_id],
                }
                for slot in PlayerSlot.objects.all()
            ]
        }
    )


@require_POST
def set_player_voice(request):
    form = PlayerVoiceForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Choisissez une place et une voix valides.")
        return redirect("players")

    player_number = form.cleaned_data["player_number"]
    slot, _ = PlayerSlot.objects.get_or_create(player_number=player_number)
    slot.voice_id = form.cleaned_data["voice_id"]
    slot.save(update_fields=["voice_id"])
    voice_name = dict(VOICE_CHOICES)[slot.voice_id]
    messages.success(request, f"Voix de Joueur {player_number} réglée sur {voice_name}.")
    return redirect("players")


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