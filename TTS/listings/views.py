import hashlib
import logging
from datetime import timedelta

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from TTS.chatbot import TTSGenerationError, stop_obs_playback

from . import moderation, twitch
from .form import ModerationSettingsForm, PromptForm
from .models import ModerationSettings, TTSRequest, TwitchConnection
from .playback import play_message


logger = logging.getLogger(__name__)

# Délai sans nouvelles du bot au-delà duquel le panneau affiche « Bot arrêté ».
BOT_HEARTBEAT_TIMEOUT = timedelta(seconds=15)
# Nombre de demandes terminées affichées dans l'historique.
HISTORY_SIZE = 50
# Réglages modifiables par les interrupteurs du haut du panneau.
QUICK_TOGGLES = {"auto_approve", "accept_requests", "playback_paused"}


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
                else:
                    # La lecture est dans playback.py, partagée avec le bot.
                    audio_url = play_message(
                        message, form.sound_bite_tags, form.voice_tags
                    )
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


# --- Panneau de modération --------------------------------------------------
#
# Le panneau (/moderation/) ne joue jamais de son lui-même : ses boutons
# changent seulement le statut des demandes en base. C'est le worker du bot
# (listings/worker.py) qui joue ce qui est « en file ».
#
# Tous les boutons envoient un POST puis redirigent vers le panneau
# (« Post/Redirect/Get ») : actualiser la page ne renvoie pas l'action.


def _queue_context() -> dict:
    """Données affichées par la file : listes de demandes et état du bot."""
    connection = TwitchConnection.load()
    heartbeat = connection.bot_heartbeat
    return {
        "settings": ModerationSettings.load(),
        "connection": connection,
        # Le bot écrit l'heure toutes les secondes : sans nouvelle depuis 15 s,
        # on considère qu'il est arrêté.
        "bot_running": bool(heartbeat and timezone.now() - heartbeat < BOT_HEARTBEAT_TIMEOUT),
        "pending": TTSRequest.objects.filter(status=TTSRequest.Status.PENDING),
        "playing": TTSRequest.objects.filter(status=TTSRequest.Status.PLAYING).first(),
        "approved": TTSRequest.objects.filter(status=TTSRequest.Status.APPROVED).order_by(
            "decided_at", "created_at", "pk"
        ),
        "history": TTSRequest.objects.filter(
            status__in=[
                TTSRequest.Status.PLAYED,
                TTSRequest.Status.REJECTED,
                TTSRequest.Status.FAILED,
            ]
        ).order_by("-created_at", "-pk")[:HISTORY_SIZE],
    }


def moderation_panel(request):
    """Page complète du panneau ; en POST, enregistre le formulaire des réglages."""
    settings = ModerationSettings.load()
    if request.method == "POST":
        settings_form = ModerationSettingsForm(request.POST, instance=settings)
        if settings_form.is_valid():
            settings_form.save()
            messages.success(request, "Réglages enregistrés.")
            return redirect("moderation")
    else:
        settings_form = ModerationSettingsForm(instance=settings)

    context = _queue_context()
    context.update({
        "settings_form": settings_form,
        "twitch_redirect_uri": twitch.redirect_uri(),
    })
    return render(request, "listings/moderation.html", context)


def moderation_queue(request):
    """Fragment HTML de la file seule.

    Le script moderation.js le recharge toutes les 3 secondes pour afficher
    les nouvelles demandes sans recharger toute la page.
    """
    return render(request, "listings/_moderation_queue.html", _queue_context())


@require_POST
def moderation_toggle(request, field):
    """Interrupteurs du haut du panneau : inverse le réglage ``field``."""
    # Liste blanche : on ne laisse pas l'URL modifier n'importe quel champ.
    if field not in QUICK_TOGGLES:
        return redirect("moderation")
    settings = ModerationSettings.load()
    setattr(settings, field, not getattr(settings, field))
    settings.save(update_fields=[field])
    return redirect("moderation")


@require_POST
def moderation_action(request, pk, action):
    """Boutons d'une demande : approve, reject, remove, replay ou ban.

    La logique de chaque action est dans moderation.py.
    """
    tts_request = get_object_or_404(TTSRequest, pk=pk)
    if action == "approve":
        moderation.approve(tts_request)
    elif action in {"reject", "remove"}:
        moderation.reject(tts_request)
    elif action == "replay":
        moderation.replay(tts_request)
        messages.success(request, "Clip remis dans la file de lecture.")
    elif action == "ban" and tts_request.twitch_login:
        moderation.ban_user(tts_request.twitch_login)
        messages.success(request, f"{tts_request.author} est banni du TTS.")
    return redirect("moderation")


@require_POST
def moderation_skip(request):
    """Bouton « Passer » : coupe le clip dans OBS.

    Le worker, qui attend la fin du clip, voit la source s'arrêter et passe
    au suivant.
    """
    try:
        stop_obs_playback()
    except TTSGenerationError as exc:
        messages.error(request, str(exc))
    return redirect("moderation")


# --- Connexion du compte Twitch (OAuth, détails dans twitch.py) -------------


def twitch_connect(request):
    """Bouton « Connecter Twitch » : redirige vers la page d'autorisation de Twitch."""
    try:
        return redirect(twitch.build_authorize_url())
    except twitch.TwitchError as exc:
        messages.error(request, str(exc))
        return redirect("moderation")


def twitch_callback(request):
    """Adresse où Twitch renvoie le streamer après l'autorisation.

    Twitch ajoute ``?code=…&state=…`` (ou ``?error=…`` si le streamer a refusé).
    """
    if request.GET.get("error"):
        messages.error(request, "Connexion à Twitch annulée.")
        return redirect("moderation")
    try:
        connection = twitch.complete_authorization(
            request.GET.get("code", ""), request.GET.get("state", "")
        )
    except twitch.TwitchError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(
            request,
            f"Compte Twitch « {connection.login} » connecté. Relance le bot s'il tournait déjà.",
        )
    return redirect("moderation")


@require_POST
def twitch_disconnect(request):
    """Bouton « Déconnecter » : oublie les jetons Twitch."""
    twitch.disconnect()
    messages.success(request, "Compte Twitch déconnecté.")
    return redirect("moderation")
