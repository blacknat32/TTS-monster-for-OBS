"""
URL configuration for TTS project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.1/topics/http/urls/
"""
from django.contrib import admin
from django.urls import path
from django.views.generic import RedirectView

from listings import views

urlpatterns = [
    # L'adresse racine mène au panneau de modération.
    path('', RedirectView.as_view(pattern_name='moderation', permanent=False)),
    path('admin/', admin.site.urls),
    # Page d'origine : le streamer écrit et envoie un message lui-même.
    path('message/', views.index, name='listings'),
    # Panneau de modération des demandes du chat Twitch.
    path('moderation/', views.moderation_panel, name='moderation'),
    # Fragment de la file, rechargé toutes les 3 s par moderation.js.
    path('moderation/queue/', views.moderation_queue, name='moderation_queue'),
    path('moderation/toggle/<str:field>/', views.moderation_toggle, name='moderation_toggle'),
    path('moderation/skip/', views.moderation_skip, name='moderation_skip'),
    path(
        'moderation/request/<int:pk>/<str:action>/',
        views.moderation_action,
        name='moderation_action',
    ),
    # Connexion du compte Twitch (OAuth). L'adresse « callback » doit être
    # déclarée dans l'application Twitch : http://localhost:8000/twitch/callback/
    path('twitch/connect/', views.twitch_connect, name='twitch_connect'),
    path('twitch/callback/', views.twitch_callback, name='twitch_callback'),
    path('twitch/disconnect/', views.twitch_disconnect, name='twitch_disconnect'),
]
