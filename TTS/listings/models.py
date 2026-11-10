from django.db import models

from TTS.chatbot import VOICE_ID


# Create your models here.
class Voice(models.Model):
    name = models.CharField(max_length=100)


class PlayerSlot(models.Model):
    player_number = models.PositiveSmallIntegerField(primary_key=True)
    username = models.CharField(max_length=25, unique=True, null=True, blank=True)
    display_name = models.CharField(max_length=50, blank=True, default="")
    voice_id = models.CharField(max_length=36, default=VOICE_ID)

    class Meta:
        ordering = ["player_number"]