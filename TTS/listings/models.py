"""Modèles de la base de données.

Trois modèles ont été ajoutés pour le bot Twitch :

- ``ModerationSettings`` : les réglages du panneau de modération (une seule ligne) ;
- ``TTSRequest`` : chaque demande de TTS et son avancement (à valider, en file,
  jouée…) ;
- ``TwitchConnection`` : les jetons de connexion au compte Twitch et l'état du bot
  (une seule ligne).

La base est partagée par deux processus : le serveur web (panneau) et le bot
(``manage.py twitch_bot``). C'est elle qui leur sert de « boîte aux lettres » :
le bot y dépose les demandes, le panneau les valide, le worker les joue.
"""

from django.db import models


class Voice(models.Model):
    name = models.CharField(max_length=100)


class ModerationSettings(models.Model):
    """Réglages de la file de modération.

    Il n'existe qu'une seule ligne dans la table (clé primaire 1) : on la
    récupère toujours avec ``ModerationSettings.load()``, qui la crée avec les
    valeurs par défaut au premier appel.
    """

    class ChatAccess(models.TextChoices):
        """Qui a le droit d'utiliser la commande du chat (ex. ``!tts``)."""

        EVERYONE = "everyone", "Tout le monde"
        SUBSCRIBERS = "subscribers", "Abonnés, VIP et modos"
        VIPS = "vips", "VIP et modos"
        MODERATORS = "moderators", "Modos uniquement"

    # --- Interrupteurs rapides (boutons en haut du panneau) -----------------

    # Mode automatique : les demandes valides partent directement dans la file
    # de lecture, sans clic du streamer. Exception : un message contenant un mot
    # interdit reste toujours « à valider » (voir moderation.submit_request).
    auto_approve = models.BooleanField(
        "Validation automatique",
        default=False,
        help_text=(
            "Les demandes partent directement dans la file de lecture. "
            "Celles qui contiennent un mot interdit restent à valider à la main."
        ),
    )
    # Coupe l'arrivée de nouvelles demandes (elles sont ignorées, pas stockées).
    accept_requests = models.BooleanField(
        "Accepter les demandes", default=True,
        help_text="Décoché : les nouvelles demandes du chat sont ignorées.",
    )
    # Met le worker en pause : les demandes validées restent « en file ».
    playback_paused = models.BooleanField(
        "Lecture en pause", default=False,
        help_text="Coché : les demandes validées attendent sans être jouées.",
    )

    # --- Réglages du formulaire (bas du panneau) ----------------------------

    # Les demandes du streamer et des modos sont validées d'office, ignorent le
    # cooldown et les mots interdits.
    trust_moderators = models.BooleanField(
        "Faire confiance aux modos", default=True,
        help_text="Les demandes des modos et du streamer sont validées d'office, sans cooldown.",
    )
    chat_command = models.CharField("Commande du chat", max_length=50, default="!tts",
                                    help_text="Vide : commande désactivée.", blank=True)
    chat_access = models.CharField(
        "Qui peut utiliser la commande", max_length=20,
        choices=ChatAccess.choices, default=ChatAccess.EVERYONE,
    )
    # Titre de la récompense de points de chaîne à écouter. Les autres
    # récompenses de la chaîne (ex. « Hydrate-toi ») sont ignorées.
    reward_title = models.CharField(
        "Récompense de points de chaîne", max_length=100, blank=True,
        help_text="Titre exact de la récompense (avec saisie de texte). Vide : désactivé.",
    )
    user_cooldown_seconds = models.PositiveIntegerField(
        "Cooldown par viewer (secondes)", default=60,
        help_text="S'applique à la commande du chat. Les récompenses ont leur propre cooldown dans Twitch.",
    )
    max_message_length = models.PositiveIntegerField("Longueur maximale", default=300)
    # Listes stockées en texte brut, une entrée par ligne, pour rester
    # modifiables facilement dans une zone de texte du panneau.
    banned_words = models.TextField(
        "Mots interdits", blank=True,
        help_text="Un par ligne. Une demande qui en contient reste à valider à la main.",
    )
    banned_users = models.TextField(
        "Viewers bannis du TTS", blank=True,
        help_text="Un pseudo Twitch par ligne. Leurs demandes sont ignorées.",
    )

    class Meta:
        verbose_name = "réglages de modération"
        verbose_name_plural = "réglages de modération"

    @classmethod
    def load(cls) -> "ModerationSettings":
        """Retourne l'unique ligne de réglages, en la créant si besoin."""
        settings, _ = cls.objects.get_or_create(pk=1)
        return settings

    @staticmethod
    def _lines(value: str) -> list[str]:
        """Découpe une zone de texte en lignes non vides, en minuscules."""
        return [line.strip().casefold() for line in value.splitlines() if line.strip()]

    def banned_word_list(self) -> list[str]:
        return self._lines(self.banned_words)

    def banned_user_list(self) -> list[str]:
        # On accepte « @pseudo » comme « pseudo ».
        return [login.lstrip("@") for login in self._lines(self.banned_users)]


class TTSRequest(models.Model):
    """Une demande de TTS venue du chat, d'une récompense ou du panneau.

    Cycle de vie d'une demande (champ ``status``) ::

        PENDING (à valider) ──approuver──▶ APPROVED (en file) ──worker──▶ PLAYING
              │                               │                            │
              └──refuser──▶ REJECTED ◀──retirer┘                PLAYED ◀───┴───▶ FAILED

    En mode automatique, une demande valide est créée directement en APPROVED.
    Une demande invalide (balise inconnue, trop longue) est créée en REJECTED,
    pour que le streamer voie dans l'historique pourquoi elle n'a pas été jouée.
    """

    class Source(models.TextChoices):
        CHAT = "chat", "Chat"
        REWARD = "reward", "Points de chaîne"
        PANEL = "panel", "Panneau"

    class Status(models.TextChoices):
        PENDING = "pending", "À valider"
        APPROVED = "approved", "En file"
        PLAYING = "playing", "En lecture"
        PLAYED = "played", "Joué"
        REJECTED = "rejected", "Refusé"
        FAILED = "failed", "Échec"

    source = models.CharField(max_length=10, choices=Source.choices)
    status = models.CharField(max_length=10, choices=Status.choices, db_index=True)
    # Identité Twitch de l'auteur. L'identifiant numérique ne change jamais
    # (contrairement au pseudo) : c'est lui qui sert pour le cooldown.
    twitch_user_id = models.CharField(max_length=50, blank=True)
    twitch_login = models.CharField(max_length=50, blank=True, db_index=True)
    display_name = models.CharField(max_length=100, blank=True)
    message = models.TextField()
    # Pourquoi la demande est bloquée « à valider » (ex. mot interdit).
    flag_reason = models.CharField(max_length=200, blank=True)
    # Pourquoi la demande a été refusée ou a échoué (affiché dans l'historique).
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    # Date d'approbation : la file de lecture est triée sur ce champ, pour
    # jouer les demandes dans l'ordre où elles ont été validées.
    decided_at = models.DateTimeField(null=True, blank=True)
    played_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at", "pk"]

    def __str__(self) -> str:
        return f"{self.display_name or self.twitch_login or 'panneau'}: {self.message[:40]}"

    @property
    def author(self) -> str:
        """Nom à afficher dans le panneau."""
        return self.display_name or self.twitch_login or "Panneau"


class TwitchConnection(models.Model):
    """Connexion au compte Twitch du streamer et état du bot.

    Une seule ligne (clé primaire 1). Elle est écrite par les deux processus :

    - le serveur web y enregistre les jetons OAuth quand le streamer clique sur
      « Connecter Twitch » ;
    - le bot y écrit son « battement de cœur » (``bot_heartbeat``) toutes les
      secondes et son état, pour que le panneau affiche « Bot actif ».
    """

    # Jeton d'accès (valable quelques heures) et jeton de renouvellement
    # (permet d'obtenir un nouveau jeton d'accès sans refaire la connexion).
    access_token = models.CharField(max_length=200, blank=True)
    refresh_token = models.CharField(max_length=200, blank=True)
    user_id = models.CharField(max_length=50, blank=True)
    login = models.CharField(max_length=50, blank=True)
    # Valeur aléatoire envoyée à Twitch au début de la connexion et vérifiée
    # au retour : protège contre une fausse redirection (attaque CSRF).
    oauth_state = models.CharField(max_length=64, blank=True)
    bot_heartbeat = models.DateTimeField(null=True, blank=True)
    bot_status = models.CharField(max_length=300, blank=True)

    @classmethod
    def load(cls) -> "TwitchConnection":
        """Retourne l'unique ligne, en la créant si besoin."""
        connection, _ = cls.objects.get_or_create(pk=1)
        return connection

    @classmethod
    def update_fields(cls, **fields) -> None:
        """Met à jour seulement les champs donnés.

        Un ``save()`` classique réécrirait toute la ligne et pourrait effacer ce
        que l'autre processus vient d'écrire (ex. le bot écrase les jetons tout
        juste enregistrés par le panneau). ``update()`` ne touche que ces champs.
        """
        cls.load()
        cls.objects.filter(pk=1).update(**fields)
