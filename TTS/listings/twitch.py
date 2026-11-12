"""Tout ce qui parle à Twitch.

Ce module a trois parties :

1. **OAuth** : la connexion du compte Twitch du streamer, déclenchée par le
   bouton « Connecter Twitch » du panneau. Twitch nous donne en retour des
   jetons, enregistrés dans ``TwitchConnection``.
2. **Événements** : la transformation d'un message du chat ou d'une
   récompense de points de chaîne en demande de TTS (``handle_event``).
3. **Client EventSub** : la connexion permanente à Twitch qui reçoit ces
   événements en direct (``EventSubClient``).

Pourquoi EventSub en WebSocket ? C'est le bot qui ouvre la connexion vers
Twitch, comme un navigateur. Twitch n'a donc pas besoin de joindre le PC du
streamer : aucun port à ouvrir, aucune URL publique, aucun hébergement.

Documentation Twitch :
- OAuth : https://dev.twitch.tv/docs/authentication/getting-tokens-oauth/
- EventSub WebSocket : https://dev.twitch.tv/docs/eventsub/handling-websocket-events/
"""

import json
import logging
import os
import secrets
import threading
from collections import deque
from urllib.parse import urlencode

import requests

from .models import ModerationSettings, TTSRequest, TwitchConnection
from .moderation import Submission, submit_request


logger = logging.getLogger(__name__)

AUTHORIZE_URL = "https://id.twitch.tv/oauth2/authorize"
TOKEN_URL = "https://id.twitch.tv/oauth2/token"
VALIDATE_URL = "https://id.twitch.tv/oauth2/validate"
SUBSCRIPTIONS_URL = "https://api.twitch.tv/helix/eventsub/subscriptions"
# Twitch envoie un message « keepalive » au moins toutes les 30 s quand il ne
# se passe rien : s'il n'arrive plus, la connexion est considérée comme morte.
EVENTSUB_URL = "wss://eventsub.wss.twitch.tv/ws?keepalive_timeout_seconds=30"
# Doit être identique, caractère pour caractère, à l'URL de redirection
# déclarée dans l'application Twitch (dev.twitch.tv/console/apps).
DEFAULT_REDIRECT_URI = "http://localhost:8000/twitch/callback/"
# Permissions demandées au streamer :
# - user:read:chat : lire les messages du chat ;
# - channel:read:redemptions : savoir quand une récompense est utilisée.
SCOPES = ["user:read:chat", "channel:read:redemptions"]
# Types d'événements EventSub écoutés.
CHAT_EVENT = "channel.chat.message"
REWARD_EVENT = "channel.channel_points_custom_reward_redemption.add"


class TwitchError(Exception):
    """Erreur de configuration ou de connexion, avec un message lisible par le streamer."""


# --- 1. OAuth : connexion du compte Twitch ----------------------------------
#
# Déroulé quand le streamer clique sur « Connecter Twitch » :
#   1. build_authorize_url() l'envoie sur la page de Twitch « Autoriser l'app » ;
#   2. Twitch le renvoie sur /twitch/callback/?code=…&state=… ;
#   3. complete_authorization() échange ce code contre des jetons et les
#      enregistre en base.
# Ensuite, get_valid_access_token() vérifie le jeton à chaque démarrage du bot
# et le renouvelle automatiquement quand il a expiré.


def client_credentials() -> tuple[str, str]:
    """Lit l'identifiant et le secret de l'application Twitch dans ``.env``."""
    client_id = os.getenv("TWITCH_CLIENT_ID", "").strip()
    client_secret = os.getenv("TWITCH_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        raise TwitchError(
            "TWITCH_CLIENT_ID et TWITCH_CLIENT_SECRET doivent être renseignés dans .env."
        )
    return client_id, client_secret


def redirect_uri() -> str:
    """URL de retour après la connexion (modifiable avec TWITCH_REDIRECT_URI)."""
    return os.getenv("TWITCH_REDIRECT_URI", "").strip() or DEFAULT_REDIRECT_URI


def build_authorize_url() -> str:
    """Construit l'adresse de la page Twitch « Autoriser l'application »."""
    client_id, _ = client_credentials()
    # Valeur aléatoire vérifiée au retour : seule une connexion lancée depuis
    # notre panneau peut enregistrer des jetons. Elle est stockée en base et
    # pas dans la session du navigateur, car le panneau peut être ouvert sur
    # 127.0.0.1 alors que Twitch renvoie vers localhost (cookies différents).
    state = secrets.token_urlsafe(24)
    TwitchConnection.update_fields(oauth_state=state)
    query = urlencode({
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri(),
        "scope": " ".join(SCOPES),
        "state": state,
        # Toujours afficher l'écran de Twitch, pour pouvoir choisir le compte.
        "force_verify": "true",
    })
    return f"{AUTHORIZE_URL}?{query}"


def complete_authorization(code: str, state: str) -> TwitchConnection:
    """Retour de Twitch : échange le code contre des jetons et les enregistre."""
    connection = TwitchConnection.load()
    # compare_digest compare en temps constant (bonne pratique pour les secrets).
    if not state or not connection.oauth_state or not secrets.compare_digest(
        state, connection.oauth_state
    ):
        raise TwitchError("La réponse de Twitch ne correspond pas à la demande. Réessaie.")
    client_id, client_secret = client_credentials()
    tokens = _token_request({
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri(),
    })
    # On demande à Twitch à quel compte appartient le jeton (pseudo, id).
    identity = validate_token(tokens["access_token"])
    if identity is None:
        raise TwitchError("Twitch a refusé le jeton obtenu. Réessaie.")
    TwitchConnection.update_fields(
        access_token=tokens["access_token"],
        refresh_token=tokens.get("refresh_token", ""),
        user_id=identity["user_id"],
        login=identity["login"],
        oauth_state="",  # usage unique
    )
    return TwitchConnection.load()


def disconnect() -> None:
    """Bouton « Déconnecter » : oublie les jetons enregistrés."""
    TwitchConnection.update_fields(access_token="", refresh_token="", user_id="", login="")


def _token_request(data: dict) -> dict:
    """Appelle l'adresse de Twitch qui délivre les jetons."""
    try:
        response = requests.post(TOKEN_URL, data=data, timeout=15)
    except requests.RequestException as exc:
        raise TwitchError("Impossible de joindre Twitch pour obtenir un jeton.") from exc
    if response.status_code != 200:
        raise TwitchError(
            f"Twitch a refusé la demande de jeton (HTTP {response.status_code})."
        )
    tokens = response.json()
    if not tokens.get("access_token"):
        raise TwitchError("Twitch n'a pas renvoyé de jeton.")
    return tokens


def validate_token(access_token: str) -> dict | None:
    """Vérifie un jeton auprès de Twitch.

    Retourne l'identité du compte (``login``, ``user_id``, ``client_id``…), ou
    ``None`` si le jeton est expiré ou révoqué.
    """
    try:
        response = requests.get(
            VALIDATE_URL, headers={"Authorization": f"OAuth {access_token}"}, timeout=15
        )
    except requests.RequestException as exc:
        raise TwitchError("Impossible de joindre Twitch pour vérifier le jeton.") from exc
    if response.status_code == 401:
        return None
    if response.status_code != 200:
        raise TwitchError(f"Vérification du jeton Twitch impossible (HTTP {response.status_code}).")
    return response.json()


def refresh_access_token() -> str:
    """Obtient un nouveau jeton d'accès grâce au jeton de renouvellement."""
    connection = TwitchConnection.load()
    if not connection.refresh_token:
        raise TwitchError("Compte Twitch non connecté : clique sur « Connecter Twitch » dans le panneau.")
    client_id, client_secret = client_credentials()
    tokens = _token_request({
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "refresh_token",
        "refresh_token": connection.refresh_token,
    })
    # Twitch peut aussi fournir un nouveau jeton de renouvellement : on le garde.
    TwitchConnection.update_fields(
        access_token=tokens["access_token"],
        refresh_token=tokens.get("refresh_token") or connection.refresh_token,
    )
    return tokens["access_token"]


def get_valid_access_token() -> tuple[str, dict]:
    """Retourne un jeton valide (renouvelé si besoin) et l'identité du compte."""
    connection = TwitchConnection.load()
    if not connection.access_token:
        raise TwitchError("Compte Twitch non connecté : clique sur « Connecter Twitch » dans le panneau.")
    identity = validate_token(connection.access_token)
    if identity is not None:
        return connection.access_token, identity
    access_token = refresh_access_token()
    identity = validate_token(access_token)
    if identity is None:
        raise TwitchError("Le jeton Twitch a été révoqué : reconnecte Twitch dans le panneau.")
    return access_token, identity


# --- 2. Événements : du chat à la demande de TTS ----------------------------


def parse_chat_command(text: str, command: str) -> str | None:
    """Retourne le texte après la commande, ou ``None`` si ce n'est pas la commande.

    Exemples avec la commande « !tts » :
    - « !tts Salut »   → « Salut »
    - « !TTS Salut »   → « Salut » (majuscules ignorées)
    - « !ttsx Salut »  → None (autre commande)
    - « Salut !tts »   → None (la commande doit être au début)
    """
    command = command.strip()
    if not command:
        return None
    parts = text.strip().split(maxsplit=1)
    if not parts or parts[0].casefold() != command.casefold():
        return None
    return parts[1] if len(parts) > 1 else ""


def handle_event(subscription_type: str, event: dict) -> None:
    """Transforme un événement Twitch en demande de TTS.

    Les champs lus dans ``event`` sont ceux documentés par Twitch pour
    ``channel.chat.message`` et
    ``channel.channel_points_custom_reward_redemption.add``.
    """
    settings = ModerationSettings.load()

    if subscription_type == CHAT_EVENT:
        # Tous les messages du chat arrivent ici : on ne garde que ceux qui
        # commencent par la commande.
        text = (event.get("message") or {}).get("text", "")
        message = parse_chat_command(text, settings.chat_command)
        if message is None:
            return
        submission = Submission(
            source=TTSRequest.Source.CHAT,
            message=message,
            twitch_user_id=event.get("chatter_user_id", ""),
            twitch_login=event.get("chatter_user_login", ""),
            display_name=event.get("chatter_user_name", ""),
            # Badges du viewer (abonné, VIP, modo…) pour vérifier ses droits.
            badges={badge.get("set_id", "") for badge in event.get("badges") or []},
        )
    elif subscription_type == REWARD_EVENT:
        # Toutes les récompenses de la chaîne arrivent ici : on ne garde que
        # celle dont le titre est configuré dans le panneau.
        title = (event.get("reward") or {}).get("title", "")
        if not settings.reward_title or title.casefold() != settings.reward_title.strip().casefold():
            return
        submission = Submission(
            source=TTSRequest.Source.REWARD,
            message=event.get("user_input", ""),  # texte saisi par le viewer
            twitch_user_id=event.get("user_id", ""),
            twitch_login=event.get("user_login", ""),
            display_name=event.get("user_name", ""),
        )
    else:
        return

    # Les règles (mode auto, cooldown, mots interdits…) sont dans moderation.py.
    result = submit_request(submission)
    if result.request is None:
        logger.info("Demande de %s ignorée : %s", submission.twitch_login, result.reason)
    else:
        logger.info(
            "Demande de %s : %s", submission.twitch_login, result.request.get_status_display()
        )


# --- 3. Client EventSub : la connexion permanente à Twitch ------------------
#
# Protocole EventSub WebSocket, en résumé :
#   1. on se connecte à EVENTSUB_URL ; Twitch répond « session_welcome » avec
#      un identifiant de session ;
#   2. dans les 10 secondes, on s'abonne aux événements voulus en appelant
#      l'API Helix avec cet identifiant de session ;
#   3. Twitch envoie ensuite :
#      - « notification » : un événement (message du chat, récompense) ;
#      - « session_keepalive » : « je suis toujours là » ;
#      - « session_reconnect » : « reconnecte-toi à cette autre adresse »
#        (maintenance de Twitch ; les abonnements sont conservés) ;
#      - « revocation » : un abonnement a été annulé (ex. accès retiré).


class EventSubClient:
    def __init__(self, stop_event: threading.Event):
        self.stop_event = stop_event
        self.access_token = ""
        self.client_id = ""
        self.user_id = ""
        self.login = ""
        # Twitch peut envoyer deux fois le même événement : on mémorise les
        # derniers identifiants pour ne pas créer deux fois la même demande.
        self._seen_message_ids: deque[str] = deque(maxlen=500)

    def run_forever(self) -> None:
        """Reste connecté à Twitch, avec reconnexion automatique en cas de coupure."""
        delay = 2
        while not self.stop_event.is_set():
            try:
                self._run_session()
                delay = 2
            except TwitchError as exc:
                # Problème de configuration (jeton, droits) : inutile de
                # réessayer très vite, on affiche l'erreur dans le panneau.
                logger.error("%s", exc)
                TwitchConnection.update_fields(bot_status=f"Erreur : {exc}")
                delay = 30
            except Exception as exc:
                # Coupure réseau ou de Twitch : on réessaie avec une attente
                # qui double à chaque échec (2 s, 4 s, 8 s… jusqu'à 60 s).
                logger.warning("Connexion Twitch perdue (%s), reconnexion…", exc)
                TwitchConnection.update_fields(bot_status="Reconnexion à Twitch…")
                delay = min(delay * 2, 60)
            self.stop_event.wait(delay)

    def _run_session(self) -> None:
        """Une connexion complète : connexion, abonnements, réception des messages."""
        import websocket  # paquet websocket-client

        self.access_token, identity = get_valid_access_token()
        self.client_id = identity["client_id"]
        self.user_id = identity["user_id"]
        self.login = identity["login"]

        ws, session = self._open(EVENTSUB_URL)
        try:
            self._subscribe_all(session["id"])
            TwitchConnection.update_fields(bot_status=f"Connecté au chat de {self.login}")
            while not self.stop_event.is_set():
                try:
                    raw = ws.recv()
                except websocket.WebSocketTimeoutException:
                    # Ni événement ni keepalive dans le délai : connexion morte.
                    raise ConnectionError("plus de nouvelles de Twitch (keepalive dépassé)")
                if not raw:
                    raise ConnectionError("Twitch a fermé la connexion")
                message = json.loads(raw)
                message_type = message.get("metadata", {}).get("message_type")
                if message_type == "session_reconnect":
                    # On ouvre la nouvelle connexion AVANT de fermer l'ancienne,
                    # pour ne perdre aucun événement.
                    url = message["payload"]["session"]["reconnect_url"]
                    new_ws, _ = self._open(url)
                    ws.close()
                    ws = new_ws
                elif message_type == "notification":
                    self._on_notification(message)
                elif message_type == "revocation":
                    subscription = message.get("payload", {}).get("subscription", {})
                    raise TwitchError(
                        f"Twitch a révoqué l'abonnement {subscription.get('type')} "
                        f"({subscription.get('status')}). Reconnecte Twitch dans le panneau."
                    )
                # « session_keepalive » : rien à faire, recevoir suffit.
        finally:
            ws.close()

    def _open(self, url: str):
        """Ouvre une connexion WebSocket et attend le message de bienvenue."""
        import websocket

        ws = websocket.create_connection(url, timeout=15)
        welcome = json.loads(ws.recv())
        if welcome.get("metadata", {}).get("message_type") != "session_welcome":
            ws.close()
            raise ConnectionError("réponse inattendue de Twitch à la connexion")
        session = welcome["payload"]["session"]
        # Délai d'attente = intervalle de keepalive + marge de 10 secondes.
        ws.settimeout((session.get("keepalive_timeout_seconds") or 30) + 10)
        return ws, session

    def _on_notification(self, message: dict) -> None:
        """Traite un événement, en ignorant les doublons."""
        message_id = message.get("metadata", {}).get("message_id")
        if message_id in self._seen_message_ids:
            return
        self._seen_message_ids.append(message_id)
        payload = message.get("payload", {})
        try:
            handle_event(payload.get("subscription", {}).get("type", ""), payload.get("event", {}))
        except Exception:
            # Un événement mal formé ne doit pas couper la connexion.
            logger.exception("Impossible de traiter l'événement Twitch")

    def _subscribe_all(self, session_id: str) -> None:
        """S'abonne au chat et aux récompenses de la chaîne du streamer."""
        settings = ModerationSettings.load()
        # Lire le chat de sa propre chaîne : le streamer est à la fois la
        # chaîne (broadcaster) et le lecteur (user).
        self._subscribe(session_id, CHAT_EVENT, {
            "broadcaster_user_id": self.user_id,
            "user_id": self.user_id,
        })
        try:
            self._subscribe(session_id, REWARD_EVENT, {"broadcaster_user_id": self.user_id})
        except TwitchError as exc:
            # Les points de chaîne n'existent que pour les affiliés et
            # partenaires : sans eux, le bot continue avec le chat seul. On ne
            # prévient que si une récompense est configurée.
            if settings.reward_title:
                logger.warning("Points de chaîne indisponibles : %s", exc)

    def _subscribe(self, session_id: str, subscription_type: str, condition: dict) -> None:
        """Crée un abonnement EventSub rattaché à notre session WebSocket."""
        response = requests.post(
            SUBSCRIPTIONS_URL,
            headers={
                "Authorization": f"Bearer {self.access_token}",
                "Client-Id": self.client_id,
            },
            json={
                "type": subscription_type,
                "version": "1",
                "condition": condition,
                "transport": {"method": "websocket", "session_id": session_id},
            },
            timeout=15,
        )
        if response.status_code not in (200, 202):
            try:
                detail = response.json().get("message", "")
            except ValueError:
                detail = ""
            raise TwitchError(
                f"Abonnement {subscription_type} refusé par Twitch "
                f"(HTTP {response.status_code}) {detail}".strip()
            )
