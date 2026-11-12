"""Worker de lecture : joue les demandes validées une par une dans OBS.

Il tourne dans le processus du bot (``manage.py twitch_bot``), dans un thread à
part. Toutes les secondes, il regarde dans la base s'il y a une demande « en
file » (APPROVED), la joue, attend la fin du clip, puis passe à la suivante.

Pourquoi attendre la fin du clip ? OBS n'a qu'une source média « TTS Audio » :
envoyer un nouveau clip pendant la lecture couperait le précédent.

C'est le seul endroit qui joue les demandes du chat. Le panneau, lui, se
contente de changer leur statut dans la base.
"""

import logging
import threading

from django.db import close_old_connections
from django.utils import timezone

from TTS.chatbot import TTSGenerationError, wait_for_obs_playback_end

from .form import PromptForm
from .models import ModerationSettings, TTSRequest, TwitchConnection
from .playback import play_message


logger = logging.getLogger(__name__)


def recover_interrupted_requests() -> None:
    """Nettoie les demandes restées « en lecture » au démarrage.

    Si le bot a été fermé (Ctrl+C, plantage) en plein clip, la demande est
    restée en PLAYING. On la marque en échec pour qu'elle ne bloque rien ; le
    streamer peut la rejouer depuis l'historique.
    """
    TTSRequest.objects.filter(status=TTSRequest.Status.PLAYING).update(
        status=TTSRequest.Status.FAILED, error="Interrompu (le bot a été arrêté)."
    )


def claim_next_request() -> TTSRequest | None:
    """Réserve la plus ancienne demande validée et la passe « en lecture ».

    La réservation se fait avec un ``update`` conditionnel (« passe en PLAYING
    seulement si elle est encore APPROVED »). Si le streamer a cliqué sur
    « Retirer » au même moment, l'update ne trouve plus la demande et on passe
    à la suivante : elle ne sera jamais jouée par erreur.
    """
    candidates = TTSRequest.objects.filter(status=TTSRequest.Status.APPROVED).order_by(
        "decided_at", "created_at", "pk"
    )
    for request in candidates[:5]:
        claimed = TTSRequest.objects.filter(
            pk=request.pk, status=TTSRequest.Status.APPROVED
        ).update(status=TTSRequest.Status.PLAYING)
        if claimed:
            request.status = TTSRequest.Status.PLAYING
            return request
    return None


def play_request(request: TTSRequest) -> None:
    """Joue une demande déjà réservée, puis enregistre « joué » ou « échec »."""
    try:
        # Le message est revalidé : entre la demande et la lecture, un son a pu
        # être renommé ou supprimé de Sound_Bites.
        form = PromptForm({"message": request.message})
        if not form.is_valid():
            raise TTSGenerationError(
                " ".join(str(e) for e in form.errors.get("message", []))
                or "Message invalide."
            )
        # Même code que la page /message/ : TTS Monster, FFmpeg puis OBS.
        play_message(form.cleaned_data["message"], form.sound_bite_tags, form.voice_tags)
        # Bloque jusqu'à la fin du clip (ou jusqu'au bouton « Passer »).
        wait_for_obs_playback_end()
    except TTSGenerationError as exc:
        # Erreur prévue (OBS fermé, quota TTS Monster…) : on la garde pour
        # l'afficher dans l'historique du panneau.
        logger.warning("TTS request %s failed: %s", request.pk, exc)
        request.status = TTSRequest.Status.FAILED
        request.error = str(exc)
    else:
        request.status = TTSRequest.Status.PLAYED
        request.error = ""
    request.played_at = timezone.now()
    request.save(update_fields=["status", "error", "played_at"])


def process_next_request() -> bool:
    """Joue la prochaine demande, sauf si la lecture est en pause.

    Retourne vrai si une demande a été traitée (on enchaîne alors directement
    sur la suivante, sans attendre).
    """
    if ModerationSettings.load().playback_paused:
        return False
    request = claim_next_request()
    if request is None:
        return False
    play_request(request)
    return True


def run_worker(stop_event: threading.Event, poll_seconds: float = 1.0) -> None:
    """Boucle principale du worker, jusqu'à ce que ``stop_event`` soit levé."""
    recover_interrupted_requests()
    while not stop_event.is_set():
        # Ce thread vit longtemps : on laisse Django fermer les connexions à la
        # base devenues inutilisables.
        close_old_connections()
        try:
            # « Battement de cœur » : le panneau affiche « Bot actif » tant que
            # cette date a moins de 15 secondes.
            TwitchConnection.update_fields(bot_heartbeat=timezone.now())
            if process_next_request():
                continue
        except Exception:
            # Une erreur imprévue ne doit pas arrêter le worker pour la soirée.
            logger.exception("Unexpected error in the playback worker")
        stop_event.wait(poll_seconds)
