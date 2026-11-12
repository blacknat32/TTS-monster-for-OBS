"""Règles de modération : qui peut demander un TTS, et où la demande atterrit.

Ce module ne parle ni à Twitch ni à OBS. Il reçoit une demande déjà extraite
du chat (``Submission``), applique les réglages du panneau, puis enregistre la
demande avec le bon statut. C'est ici que se joue la différence entre le mode
manuel (statut « à valider ») et le mode automatique (statut « en file »).

Il contient aussi les actions des boutons du panneau : approuver, refuser,
rejouer, bannir.
"""

from dataclasses import dataclass, field
from datetime import timedelta

from django.utils import timezone

from .form import PromptForm
from .models import ModerationSettings, TTSRequest


# Badges Twitch (``set_id``) du streamer et des modérateurs.
MODERATOR_BADGES = {"broadcaster", "moderator"}

# Pour chaque niveau d'accès à la commande du chat : les badges qui donnent le
# droit de l'utiliser. ``None`` veut dire « tout le monde ». « founder » est le
# badge des tout premiers abonnés d'une chaîne : ce sont aussi des abonnés.
ACCESS_BADGES = {
    ModerationSettings.ChatAccess.EVERYONE: None,
    ModerationSettings.ChatAccess.SUBSCRIBERS: {"subscriber", "founder", "vip"} | MODERATOR_BADGES,
    ModerationSettings.ChatAccess.VIPS: {"vip"} | MODERATOR_BADGES,
    ModerationSettings.ChatAccess.MODERATORS: MODERATOR_BADGES,
}


@dataclass
class Submission:
    """Une demande reçue, avant toute vérification."""

    source: str  # TTSRequest.Source : chat, récompense ou panneau
    message: str  # texte à lire, avec ses balises {voix} et {son}
    twitch_user_id: str = ""
    twitch_login: str = ""
    display_name: str = ""
    badges: set[str] = field(default_factory=set)  # ex. {"subscriber", "vip"}


@dataclass
class SubmitResult:
    """Résultat de ``submit_request``.

    ``request`` vaut ``None`` quand la demande a été ignorée ; ``reason``
    explique alors pourquoi (affiché dans le terminal du bot).
    """

    request: TTSRequest | None
    reason: str = ""


def submit_request(submission: Submission) -> SubmitResult:
    """Applique les règles et enregistre la demande si elle est retenue.

    Ordre des vérifications :

    1. Ignorée sans trace : message vide, demandes fermées, viewer banni,
       droits insuffisants ou cooldown. On n'enregistre rien, pour qu'un
       viewer qui spamme ne remplisse pas l'historique.
    2. Refusée avec trace (REJECTED) : message trop long ou balise inconnue.
       Le streamer voit la raison dans l'historique.
    3. Retenue :
       - mot interdit → toujours « à valider », même en mode automatique ;
       - mode automatique, ou modo de confiance → « en file » directement ;
       - sinon (mode manuel) → « à valider ».
    """
    settings = ModerationSettings.load()
    message = submission.message.strip()
    login = submission.twitch_login.casefold()
    is_moderator = bool(submission.badges & MODERATOR_BADGES)
    trusted = settings.trust_moderators and is_moderator

    # --- 1. Demandes ignorées sans être enregistrées ------------------------
    if not message:
        return SubmitResult(None, "message vide")
    if not settings.accept_requests:
        return SubmitResult(None, "les demandes sont désactivées")
    if login and login in settings.banned_user_list():
        return SubmitResult(None, "viewer banni du TTS")

    # Droits et cooldown ne concernent que la commande du chat : une récompense
    # de points de chaîne a déjà un coût et un cooldown réglés dans Twitch.
    if submission.source == TTSRequest.Source.CHAT:
        allowed_badges = ACCESS_BADGES.get(settings.chat_access)
        if allowed_badges is not None and not submission.badges & allowed_badges:
            return SubmitResult(None, "droits insuffisants pour la commande")
        if not trusted and _in_cooldown(submission, settings.user_cooldown_seconds):
            return SubmitResult(None, "cooldown en cours")

    request = TTSRequest(
        source=submission.source,
        twitch_user_id=submission.twitch_user_id,
        twitch_login=submission.twitch_login,
        display_name=submission.display_name,
        message=message,
    )

    # --- 2. Demandes invalides : enregistrées comme refusées -----------------
    error = _validation_error(message, settings.max_message_length)
    if error:
        request.status = TTSRequest.Status.REJECTED
        request.error = error
        request.decided_at = timezone.now()
        request.save()
        return SubmitResult(request, error)

    # --- 3. Demandes valides : à valider ou directement en file -------------
    banned_word = _find_banned_word(message, settings.banned_word_list())
    if banned_word and not trusted:
        # Garde-fou du mode automatique : un humain doit relire ce message.
        request.status = TTSRequest.Status.PENDING
        request.flag_reason = f"Mot interdit : « {banned_word} »"
    elif settings.auto_approve or trusted:
        request.status = TTSRequest.Status.APPROVED
        request.decided_at = timezone.now()
    else:
        request.status = TTSRequest.Status.PENDING
    request.save()
    return SubmitResult(request)


def _in_cooldown(submission: Submission, cooldown_seconds: int) -> bool:
    """Vrai si ce viewer a déjà utilisé la commande pendant le cooldown.

    On compte toutes ses demandes récentes, même refusées : sinon, envoyer des
    messages invalides en boucle permettrait de contourner le cooldown.
    """
    if not cooldown_seconds or not submission.twitch_user_id:
        return False
    since = timezone.now() - timedelta(seconds=cooldown_seconds)
    return TTSRequest.objects.filter(
        twitch_user_id=submission.twitch_user_id,
        source=TTSRequest.Source.CHAT,
        created_at__gte=since,
    ).exists()


def _validation_error(message: str, max_length: int) -> str:
    """Retourne l'erreur du message, ou une chaîne vide s'il est valide.

    On réutilise le formulaire de la page /message/ : les balises sont vérifiées
    exactement de la même façon que quand le streamer écrit lui-même.
    """
    if len(message) > max_length:
        return f"Message trop long ({len(message)} / {max_length} caractères)."
    form = PromptForm({"message": message})
    if form.is_valid():
        return ""
    return " ".join(str(error) for error in form.errors.get("message", [])) or "Message invalide."


def _find_banned_word(message: str, banned_words: list[str]) -> str:
    """Retourne le premier mot interdit trouvé dans le message (ou "").

    La recherche se fait sur des morceaux de texte, sans tenir compte des
    majuscules : « con » bloque aussi « déconner ». C'est voulu : un faux
    positif ne fait que demander une validation manuelle, alors qu'un mot
    raté serait lu en direct.
    """
    folded = message.casefold()
    return next((word for word in banned_words if word in folded), "")


# --- Actions des boutons du panneau -----------------------------------------


def approve(request: TTSRequest) -> bool:
    """Bouton « Approuver » : la demande passe dans la file de lecture."""
    return _decide(request, TTSRequest.Status.APPROVED)


def reject(request: TTSRequest) -> bool:
    """Boutons « Refuser » et « Retirer » : la demande ne sera pas jouée."""
    return _decide(request, TTSRequest.Status.REJECTED)


def _decide(request: TTSRequest, status: str) -> bool:
    """Change le statut, seulement si la demande n'est pas déjà jouée.

    Le filtre sur le statut évite un conflit avec le worker : si le clip a
    commencé entre l'affichage du panneau et le clic, rien ne change.
    """
    updated = TTSRequest.objects.filter(
        pk=request.pk,
        status__in=[TTSRequest.Status.PENDING, TTSRequest.Status.APPROVED],
    ).update(status=status, decided_at=timezone.now())
    return bool(updated)


def replay(request: TTSRequest) -> TTSRequest:
    """Bouton « Rejouer » : remet une copie de la demande en file.

    On crée une copie plutôt que de modifier l'originale, pour garder
    l'historique intact.
    """
    return TTSRequest.objects.create(
        source=request.source,
        status=TTSRequest.Status.APPROVED,
        twitch_user_id=request.twitch_user_id,
        twitch_login=request.twitch_login,
        display_name=request.display_name,
        message=request.message,
        decided_at=timezone.now(),
    )


def ban_user(login: str) -> None:
    """Bouton « Bannir » : ajoute le viewer à la liste des bannis.

    Ses demandes encore en attente (à valider ou en file) sont refusées.
    """
    login = login.strip().lstrip("@")
    if not login:
        return
    settings = ModerationSettings.load()
    if login.casefold() in settings.banned_user_list():
        return
    settings.banned_users = "\n".join(
        line for line in [settings.banned_users.strip(), login] if line
    )
    settings.save(update_fields=["banned_users"])
    TTSRequest.objects.filter(
        twitch_login__iexact=login,
        status__in=[TTSRequest.Status.PENDING, TTSRequest.Status.APPROVED],
    ).update(status=TTSRequest.Status.REJECTED, decided_at=timezone.now(),
             error="Viewer banni du TTS.")
