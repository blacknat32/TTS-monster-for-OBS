"""Commande ``python TTS/manage.py twitch_bot`` : le bot seul, sans le panneau.

Le plus simple pendant un stream est ``python TTS/manage.py stream``, qui lance
le panneau et le bot ensemble. Cette commande reste utile pour faire tourner
le bot séparément, par exemple dans un second terminal pendant que tu
modifies le code avec ``runserver``, qui redémarre à chaque changement.

Elle lance deux choses en parallèle (voir ``listings/bot_runner.py``) :

- le **worker** : joue dans OBS les demandes validées, une par une ;
- le **client Twitch** : reçoit les messages du chat et les récompenses.

Option ``--no-twitch`` : lance seulement le worker. Pratique pour tester le
bouton « Rejouer » du panneau sans compte Twitch connecté.
"""

from django.core.management.base import BaseCommand

from listings.bot_runner import BotRunner, configure_logging


class Command(BaseCommand):
    help = (
        "Écoute le chat Twitch et les points de chaîne, puis joue dans OBS les "
        "demandes validées. Laisse cette commande tourner pendant le stream."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--no-twitch",
            action="store_true",
            help="Lance seulement la lecture (utile pour tester avec le panneau).",
        )

    def handle(self, *args, **options):
        configure_logging()
        runner = BotRunner(with_twitch=not options["no_twitch"])
        runner.start()
        self.stdout.write(self.style.SUCCESS("Bot démarré.  (Ctrl+C pour arrêter)"))
        try:
            runner.wait_forever()
        except KeyboardInterrupt:
            self.stdout.write("\nArrêt du bot…")
        finally:
            runner.stop()
