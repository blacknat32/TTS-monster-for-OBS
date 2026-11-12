"""Commande ``python TTS/manage.py stream`` : tout lancer en une seule commande.

Elle démarre dans le même programme :

- le **bot** (client Twitch + lecture dans OBS), en arrière-plan ;
- le **panneau** (serveur web Django), au premier plan.

Un seul terminal suffit donc pendant le stream. ``Ctrl+C`` arrête les deux.

Port du panneau : celui de ``TWITCH_REDIRECT_URI`` dans ``.env`` s'il est
renseigné, sinon 8000. On peut aussi le forcer : ``manage.py stream 9000``.

Le serveur web est lancé sans rechargement automatique (``--noreload``) : en
mode rechargement, Django relance l'application dans un second processus à
chaque modification du code, ce qui ferait tourner le bot en double et jouer
chaque clip deux fois. Pour développer avec le rechargement automatique,
utilise plutôt ``runserver`` et ``twitch_bot`` dans deux terminaux.
"""

from urllib.parse import urlparse

from django.core.management import call_command
from django.core.management.base import BaseCommand

from listings.bot_runner import BotRunner, configure_logging
from listings.twitch import redirect_uri


DEFAULT_PORT = "8000"


def default_port() -> str:
    """Port du panneau quand aucun n'est donné dans la commande.

    On prend celui de l'URL de redirection Twitch (TWITCH_REDIRECT_URI dans
    .env) : la connexion Twitch ne fonctionne que si le panneau tourne sur ce
    port. Ainsi, pour changer de port, il suffit de modifier .env (et l'URL
    déclarée chez Twitch). Sans port dans l'URL, on garde 8000.
    """
    try:
        port = urlparse(redirect_uri()).port
    except ValueError:  # URL mal écrite dans .env
        port = None
    return str(port) if port else DEFAULT_PORT


class Command(BaseCommand):
    help = (
        "Lance le panneau de modération et le bot Twitch en une seule commande. "
        "Laisse-la tourner pendant le stream."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "addrport",
            nargs="?",
            default=None,
            help=(
                "Port (ou adresse:port) du panneau, comme pour runserver. Par défaut : "
                "le port de TWITCH_REDIRECT_URI dans .env, sinon 8000."
            ),
        )
        parser.add_argument(
            "--no-twitch",
            action="store_true",
            help="Ne se connecte pas à Twitch (panneau et lecture seulement).",
        )

    def handle(self, *args, **options):
        configure_logging()
        addrport = options["addrport"] or default_port()
        port = addrport.rsplit(":", 1)[-1]

        runner = BotRunner(with_twitch=not options["no_twitch"])
        runner.start()
        self.stdout.write(self.style.SUCCESS(
            f"Bot démarré. Panneau : http://localhost:{port}/moderation/  (Ctrl+C pour tout arrêter)"
        ))
        try:
            # Bloque jusqu'à Ctrl+C (runserver se termine alors par SystemExit).
            call_command("runserver", addrport, use_reloader=False)
        except KeyboardInterrupt:
            pass
        finally:
            self.stdout.write("Arrêt du bot…")
            runner.stop()
