from django.db import close_old_connections, transaction

from .models import PlayerSlot


def ensure_player_slots() -> None:
    try:
        for player_number in range(1, 4):
            PlayerSlot.objects.get_or_create(player_number=player_number)
    finally:
        close_old_connections()


def claim_player_slot(
    player_number: int, username: str, display_name: str
) -> bool:
    normalized_username = username.strip().casefold()
    display_name = display_name.strip() or username.strip()

    if player_number not in {1, 2, 3} or not normalized_username:
        return False

    try:
        with transaction.atomic():
            existing_slot = PlayerSlot.objects.filter(
                username=normalized_username
            ).first()
            target_slot = PlayerSlot.objects.filter(
                player_number=player_number
            ).first()

            if target_slot is not None and target_slot.username != normalized_username:
                return False

            if (
                existing_slot is not None
                and existing_slot.player_number != player_number
            ):
                existing_slot.delete()

            PlayerSlot.objects.update_or_create(
                player_number=player_number,
                defaults={
                    "username": normalized_username,
                    "display_name": display_name,
                },
            )
    finally:
        close_old_connections()

    return True


def clear_player_slots() -> None:
    try:
        for player_number in range(1, 4):
            PlayerSlot.objects.update_or_create(
                player_number=player_number,
                defaults={"username": None, "display_name": ""},
            )
    finally:
        close_old_connections()


def get_player_voice(username: str) -> str | None:
    normalized_username = username.strip().casefold()
    try:
        slot = PlayerSlot.objects.filter(username=normalized_username).first()
        return slot.voice_id if slot is not None else None
    finally:
        close_old_connections()
