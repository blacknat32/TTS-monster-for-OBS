import atexit
import errno
import logging
import os
import shutil
import subprocess
import tempfile
import threading
import time
import tomllib
import uuid
from pathlib import Path
from pathlib import PureWindowsPath
from typing import Any
from urllib.parse import urlparse

import requests


API_URL = "https://api.console.tts.monster/generate"
VOICE_ID = "faa92dd8-0517-49da-8f01-1fb03f0e0096"
OBS_CONFIG_PATH = Path(__file__).with_name("config file")
AUDIO_DIRECTORY = Path(__file__).resolve().parent.parent / "media" / "tts"
AUDIO_CLEANUP_DELAY_SECONDS = 5 * 60
AUDIO_CLEANUP_RETRY_SECONDS = 30

logger = logging.getLogger(__name__)
_obs_lock = threading.RLock()
_obs_client: Any | None = None


class TTSGenerationError(Exception):
    """Raised when the TTS service cannot generate audio."""


def _load_obs_settings() -> tuple[str, int, str, str]:
    try:
        config = tomllib.loads(OBS_CONFIG_PATH.read_text(encoding="utf-8"))
        connection = config["connection"]
        input_name = config["audio"]["input_name"]
        host = connection["host"]
        port = int(connection["port"])
        password = connection.get("password", "")
        if password in ("None", "null"):
            password = ""
    except (OSError, tomllib.TOMLDecodeError, KeyError, TypeError, ValueError) as exc:
        raise TTSGenerationError(
            "OBS connection settings are missing or invalid in TTS/config file."
        ) from exc

    return host, port, password, input_name


def connect_to_obs() -> None:
    global _obs_client

    host, port, password, _ = _load_obs_settings()
    try:
        import obsws_python
        from obsws_python.error import OBSSDKError
        from websocket import WebSocketTimeoutException
    except ImportError as exc:
        raise TTSGenerationError(
            "OBS WebSocket support is missing; install the project requirements."
        ) from exc

    with _obs_lock:
        if _obs_client is not None:
            return

        try:
            _obs_client = obsws_python.ReqClient(
                host=host,
                port=port,
                password=password,
                timeout=5,
            )
            logger.info("Connected to OBS WebSocket at %s:%s", host, port)
        except (OSError, WebSocketTimeoutException) as exc:
            raise TTSGenerationError(
                f"Cannot connect to OBS WebSocket at {host}:{port}. Start OBS, enable "
                "WebSocket Server in Tools > WebSocket Server Settings, and make "
                "sure its server port matches TTS/config file."
            ) from exc
        except OBSSDKError as exc:
            raise TTSGenerationError(
                f"OBS WebSocket error at {host}:{port}: {exc}. Check the WebSocket "
                "password and OBS connection settings."
            ) from exc


def disconnect_from_obs() -> None:
    global _obs_client

    with _obs_lock:
        client = _obs_client
        _obs_client = None
        if client is not None:
            try:
                client.disconnect()
            except Exception:
                logger.exception("Failed to close the OBS WebSocket connection")


def _wait_for_media_playback(client: Any, input_name: str) -> str:
    media_state = "OBS_MEDIA_STATE_NONE"
    for _ in range(20):
        media_state = client.get_media_input_status(input_name).media_state
        if media_state in {
            "OBS_MEDIA_STATE_PLAYING",
            "OBS_MEDIA_STATE_ENDED",
            "OBS_MEDIA_STATE_ERROR",
        }:
            return media_state
        time.sleep(0.1)
    return media_state


def _start_media_playback(client: Any, input_name: str) -> None:
    client.trigger_media_input_action(
        input_name, "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_RESTART"
    )
    media_state = _wait_for_media_playback(client, input_name)
    if media_state not in {"OBS_MEDIA_STATE_PLAYING", "OBS_MEDIA_STATE_ENDED"}:
        client.trigger_media_input_action(
            input_name, "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_PLAY"
        )
        media_state = _wait_for_media_playback(client, input_name)

    if media_state not in {"OBS_MEDIA_STATE_PLAYING", "OBS_MEDIA_STATE_ENDED"}:
        raise TTSGenerationError(
            f"OBS did not start playback for media input '{input_name}' "
            f"(state: {media_state}). Check the OBS Media Source and its audio settings."
        )


# --- Ajouts pour le bot Twitch ------------------------------------------------
# Le worker du bot joue les clips à la suite : il doit savoir quand un clip est
# fini avant d'envoyer le suivant à la source média, et le bouton « Passer »
# du panneau doit pouvoir couper le clip en cours.

# États de la source média d'OBS pendant lesquels un clip est encore en cours.
ACTIVE_MEDIA_STATES = {
    "OBS_MEDIA_STATE_OPENING",
    "OBS_MEDIA_STATE_BUFFERING",
    "OBS_MEDIA_STATE_PLAYING",
}


# État de la source média après un clic sur « Passer » (arrêt manuel). Une fin
# normale donne « OBS_MEDIA_STATE_ENDED ».
MEDIA_STATE_STOPPED = "OBS_MEDIA_STATE_STOPPED"


def wait_for_obs_playback_end(
    timeout_seconds: float = 300, poll_seconds: float = 0.25
) -> str:
    """Attend que la source média d'OBS ait fini (ou arrêté) de jouer le clip.

    Interroge OBS toutes les ``poll_seconds`` secondes. Au bout de
    ``timeout_seconds`` (5 minutes par défaut), on abandonne l'attente pour
    ne jamais bloquer la file indéfiniment.

    Retourne l'état final de la source. ``MEDIA_STATE_STOPPED`` veut dire que
    le clip a été coupé avec « Passer » : la suite du message ne doit pas
    être jouée.
    """
    _, _, _, input_name = _load_obs_settings()
    deadline = time.monotonic() + timeout_seconds
    media_state = ""
    while time.monotonic() < deadline:
        try:
            with _obs_lock:
                connect_to_obs()
                client = _obs_client
                if client is None:
                    return media_state
                media_state = client.get_media_input_status(input_name).media_state
        except TTSGenerationError:
            raise
        except Exception as exc:
            disconnect_from_obs()
            raise TTSGenerationError(
                f"Lost the OBS connection while waiting for playback: {exc}"
            ) from exc
        if media_state not in ACTIVE_MEDIA_STATES:
            return media_state
        time.sleep(poll_seconds)
    logger.warning("Playback did not end after %s seconds; moving on.", timeout_seconds)
    return media_state


def stop_obs_playback() -> None:
    """Coupe le clip en cours dans OBS (bouton « Passer » du panneau)."""
    _, _, _, input_name = _load_obs_settings()
    try:
        with _obs_lock:
            connect_to_obs()
            if _obs_client is not None:
                _obs_client.trigger_media_input_action(
                    input_name, "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_STOP"
                )
    except TTSGenerationError:
        raise
    except Exception as exc:
        disconnect_from_obs()
        raise TTSGenerationError(f"OBS could not stop the clip: {exc}") from exc


# --- Images des voix -----------------------------------------------------------
# Pendant un clip, OBS affiche les images de toutes les voix du message, dès le
# début, puis les masque toutes à la fin. Chaque image est
# une source de la scène dédiée (``[images] scene`` dans TTS/config file,
# « TTS Image » par défaut), nommée « <voix> OBS Image » : « Adam OBS Image »,
# « Kermit OBS Image »…
#
# Les noms sont comparés sans tenir compte des majuscules, des espaces ni de la
# ponctuation : « Adam OBS Image », « Adam, OBS Image » et « adam_obs_image »
# désignent la même source.
#
# Une voix sans source correspondante n'affiche simplement rien. Une erreur
# d'image ne doit jamais empêcher le son d'être joué : elle est seulement notée
# dans le terminal.

VOICE_IMAGE_SUFFIX = " OBS Image"
DEFAULT_IMAGE_SCENE = "TTS Image"


def voice_image_source_name(voice_label: str) -> str:
    """Nom de la source image d'une voix : « Adam » → « Adam OBS Image »."""
    return f"{voice_label}{VOICE_IMAGE_SUFFIX}"


def _comparable_name(name: str) -> str:
    """« Adam, OBS-Image » → « adamobsimage » : seules lettres et chiffres comptent."""
    return "".join(character for character in name.casefold() if character.isalnum())


def _load_image_scene() -> str:
    """Nom de la scène qui contient les images (section [images] du config file)."""
    try:
        config = tomllib.loads(OBS_CONFIG_PATH.read_text(encoding="utf-8"))
        scene = config.get("images", {}).get("scene", DEFAULT_IMAGE_SCENE)
    except (OSError, tomllib.TOMLDecodeError, AttributeError):
        return DEFAULT_IMAGE_SCENE
    return str(scene).strip() or DEFAULT_IMAGE_SCENE


def show_voice_images(voice_labels: list[str], all_voice_labels: list[str]) -> None:
    """Affiche les images de ``voice_labels`` et masque celles des autres voix.

    Toutes les voix d'un message sont affichées ensemble, dès le début du clip.
    Avec une liste vide, toutes les images sont masquées (fin du clip).
    """
    scene = _load_image_scene()
    wanted = {_comparable_name(voice_image_source_name(label)) for label in voice_labels}
    image_sources = {
        _comparable_name(voice_image_source_name(label)) for label in all_voice_labels
    }
    try:
        with _obs_lock:
            connect_to_obs()
            client = _obs_client
            if client is None:
                return
            for item in client.get_scene_item_list(scene).scene_items:
                name = _comparable_name(item.get("sourceName") or "")
                if name not in image_sources:
                    continue  # Autre source de la scène (texte, cadre…) : on n'y touche pas.
                visible = name in wanted
                if bool(item.get("sceneItemEnabled")) != visible:
                    client.set_scene_item_enabled(scene, item["sceneItemId"], visible)
    except TTSGenerationError as exc:
        logger.warning("Voice images not updated: %s", exc)
    except Exception as exc:
        # Scène introuvable, OBS fermé… : le son passe quand même.
        logger.warning(
            "Voice images not updated (scene %r, voices %r): %s", scene, voice_labels, exc
        )


def hide_voice_images(all_voice_labels: list[str]) -> None:
    """Masque toutes les images des voix."""
    show_voice_images([], all_voice_labels)


def _get_obs_local_file_path(audio_path: Path, obs_platform: str) -> str:
    resolved_path = audio_path.resolve()
    local_file = resolved_path.as_posix()
    if obs_platform.lower() == "windows" and local_file.startswith("/"):
        try:
            result = subprocess.run(
                ["wslpath", "-w", local_file],
                check=True,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise TTSGenerationError(
                "OBS runs on Windows, but the TTS app could not convert its file "
                "path to a Windows path. Run TTS in WSL with wslpath available."
            ) from exc
        local_file = result.stdout.strip()
        if not local_file:
            raise TTSGenerationError(
                "Could not convert the TTS audio path to a Windows path for OBS."
            )
    return local_file


def _get_previous_generated_audio_path(
    obs_local_file: str, obs_platform: str
) -> Path | None:
    if obs_platform.lower() == "windows" and os.name != "nt":
        if PureWindowsPath(obs_local_file).is_absolute():
            try:
                result = subprocess.run(
                    ["wslpath", "-u", obs_local_file],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                logger.warning(
                    "Unable to resolve the previous OBS audio path for cleanup: %s",
                    exc,
                )
                return None
            obs_local_file = result.stdout.strip()
            if not obs_local_file:
                logger.warning(
                    "wslpath returned an empty path for the previous OBS audio file."
                )
                return None

    try:
        previous_path = Path(obs_local_file).resolve(strict=True)
        audio_directory = AUDIO_DIRECTORY.resolve(strict=True)
    except OSError:
        return None

    if previous_path.parent != audio_directory:
        return None
    return previous_path


def _delete_replaced_generated_audio(
    previous_settings: Any,
    new_audio_path: Path,
    obs_platform: str,
) -> None:
    input_settings = getattr(previous_settings, "input_settings", None)
    if not isinstance(input_settings, dict):
        return
    previous_local_file = input_settings.get("local_file")
    if input_settings.get("is_local_file") is not True or not isinstance(
        previous_local_file, str
    ):
        return

    previous_audio_path = _get_previous_generated_audio_path(
        previous_local_file, obs_platform
    )
    if previous_audio_path is None or previous_audio_path == new_audio_path.resolve():
        return

    _delete_audio_file(previous_audio_path)


def _delete_audio_file(audio_path: Path) -> None:
    try:
        audio_path.unlink(missing_ok=True)
    except OSError as exc:
        if exc.errno in {errno.EACCES, errno.EBUSY, errno.EPERM} or getattr(
            exc, "winerror", None
        ) in {5, 32, 33}:
            logger.warning(
                "Generated audio file %s is temporarily unavailable for deletion; "
                "retrying in %s seconds: %s",
                audio_path,
                AUDIO_CLEANUP_RETRY_SECONDS,
                exc,
            )
            _schedule_audio_file_cleanup(
                audio_path, delay_seconds=AUDIO_CLEANUP_RETRY_SECONDS
            )
        else:
            logger.exception("Failed to delete generated audio file %s", audio_path)


def _schedule_audio_file_cleanup(
    audio_path: Path, delay_seconds: int = AUDIO_CLEANUP_DELAY_SECONDS
) -> None:
    cleanup_timer = threading.Timer(
        delay_seconds, _delete_audio_file, args=(audio_path,)
    )
    cleanup_timer.daemon = True
    cleanup_timer.start()


atexit.register(disconnect_from_obs)


def url_tts(message: str, voice_id: str = VOICE_ID) -> str:
    api_key = os.getenv("TTS_MONSTER_API_KEY")
    if not api_key:
        raise TTSGenerationError(
            "TTS_MONSTER_API_KEY is not configured on the server."
        )

    try:
        response = requests.post(
            API_URL,
            headers={
                "Content-Type": "application/json",
                "Authorization": api_key,
            },
            json={"voice_id": voice_id, "message": message},
            timeout=30,
        )
        response.raise_for_status()
    except requests.HTTPError as exc:
        response = exc.response
        status_code = response.status_code if response is not None else "unknown"
        provider_detail = None
        request_id = None
        if response is not None:
            try:
                error_data = response.json()
            except ValueError:
                error_data = None
            if isinstance(error_data, dict):
                for key in ("detail", "error", "message"):
                    value = error_data.get(key)
                    if isinstance(value, str) and value.strip():
                        provider_detail = value.strip()[:300]
                        break
            request_id = (
                response.headers.get("x-request-id")
                or response.headers.get("request-id")
            )

        diagnostic = ""
        if provider_detail:
            diagnostic += f" API detail: {provider_detail}"
        if request_id:
            diagnostic += f" Request ID: {request_id}."

        if isinstance(status_code, int) and status_code >= 500:
            raise TTSGenerationError(
                f"TTS Monster returned HTTP {status_code} (server error). "
                "The failure is on the TTS service side; try again later, and "
                f"contact TTS Monster support if it continues.{diagnostic}"
            ) from exc
        raise TTSGenerationError(
            f"TTS Monster rejected the request with HTTP {status_code}. "
            f"Check the selected voice and message.{diagnostic}"
        ) from exc
    except requests.RequestException as exc:
        raise TTSGenerationError("The TTS service request failed.") from exc

    try:
        response_data = response.json()
    except ValueError as exc:
        raise TTSGenerationError(
            "The TTS service returned an invalid response."
        ) from exc

    audio_url = response_data.get("url") if isinstance(response_data, dict) else None
    if not isinstance(audio_url, str) or not audio_url:
        raise TTSGenerationError(
            "The TTS service response did not include an audio URL."
        )

    return audio_url


def _send_local_audio_to_obs(
    audio_path: Path,
    obs_settings: tuple[str, int, str, str],
) -> Path:
    host, port, _, input_name = obs_settings
    if not audio_path.is_file():
        raise TTSGenerationError(f"Audio file does not exist: {audio_path.name}.")

    try:
        from obsws_python.error import (
            OBSSDKError,
            OBSSDKRequestError,
            OBSSDKTimeoutError,
        )
        from websocket import WebSocketTimeoutException
    except ImportError as exc:
        raise TTSGenerationError(
            "OBS WebSocket support is missing; install the project requirements."
        ) from exc

    try:
        with _obs_lock:
            connect_to_obs()
            client = _obs_client
            if client is None:
                raise TTSGenerationError(
                    "OBS WebSocket connection was not established."
                )
            obs_platform = client.get_version().platform
            previous_settings = client.get_input_settings(input_name)
            client.set_input_settings(
                input_name,
                {
                    "is_local_file": True,
                    "local_file": _get_obs_local_file_path(audio_path, obs_platform),
                },
                overlay=True,
            )
            _delete_replaced_generated_audio(
                previous_settings, audio_path.resolve(), obs_platform
            )
            _start_media_playback(client, input_name)
    except (OSError, WebSocketTimeoutException, OBSSDKTimeoutError) as exc:
        disconnect_from_obs()
        raise TTSGenerationError(
            f"Cannot connect to OBS WebSocket at {host}:{port}. Start OBS, enable "
            "WebSocket Server in Tools > WebSocket Server Settings, and make sure "
            "its server port matches TTS/config file."
        ) from exc
    except OBSSDKRequestError as exc:
        raise TTSGenerationError(
            f"OBS rejected {exc.req_name} (code {exc.code}): {exc}. "
            f"Check that an OBS Media Source named '{input_name}' exists and "
            "that OBS accepts the configured media file."
        ) from exc
    except OBSSDKError as exc:
        raise TTSGenerationError(
            f"OBS WebSocket error at {host}:{port}: {exc}. Check the WebSocket "
            "password and OBS connection settings."
        ) from exc

    return audio_path


def send_local_audio_to_obs(audio_path: Path) -> Path:
    """Play an existing local audio file through the configured OBS media source."""
    try:
        audio_path = audio_path.resolve(strict=True)
    except OSError as exc:
        raise TTSGenerationError(
            f"Audio file does not exist: {audio_path.name}."
        ) from exc

    return _send_local_audio_to_obs(audio_path, _load_obs_settings())


def send_audio_to_obs(audio_url: str) -> Path:
    obs_settings = _load_obs_settings()
    try:
        AUDIO_DIRECTORY.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise TTSGenerationError("The generated audio could not be saved for OBS.") from exc

    audio_path = _download_audio_file(
        audio_url, AUDIO_DIRECTORY, uuid.uuid4().hex, "generated audio"
    )
    sent_audio_path = _send_local_audio_to_obs(audio_path, obs_settings)
    _schedule_audio_file_cleanup(sent_audio_path)
    return sent_audio_path


def _download_audio_file(
    audio_url: str, directory: Path, filename: str, description: str
) -> Path:
    try:
        with requests.get(audio_url, stream=True, timeout=30) as response:
            response.raise_for_status()
            content_type = (
                response.headers.get("Content-Type", "").split(";", 1)[0].lower()
            )
            extension_by_type = {
                "audio/aac": ".aac",
                "audio/flac": ".flac",
                "audio/m4a": ".m4a",
                "audio/mpeg": ".mp3",
                "audio/ogg": ".ogg",
                "audio/wav": ".wav",
                "audio/x-wav": ".wav",
            }
            url_extension = Path(urlparse(audio_url).path).suffix.lower()
            extension = extension_by_type.get(content_type, url_extension)
            if extension not in {".aac", ".flac", ".m4a", ".mp3", ".ogg", ".wav"}:
                extension = ".wav"

            audio_path = directory / f"{filename}{extension}"
            with audio_path.open("wb") as audio_file:
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    if chunk:
                        audio_file.write(chunk)
    except requests.RequestException as exc:
        raise TTSGenerationError(
            f"The {description} could not be downloaded."
        ) from exc
    except OSError as exc:
        raise TTSGenerationError(f"The {description} could not be saved.") from exc

    return audio_path


def _find_ffmpeg() -> str | None:
    """Prefer an FFmpeg on PATH, otherwise use the one bundled by imageio-ffmpeg."""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is not None:
        return ffmpeg
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except (ImportError, RuntimeError):
        return None


def _compile_audio_files(audio_paths: list[Path], output_path: Path) -> Path:
    if not audio_paths:
        raise TTSGenerationError("There is no audio to combine.")

    ffmpeg = _find_ffmpeg()
    if ffmpeg is None:
        raise TTSGenerationError(
            "FFmpeg is required to combine message audio. Install the project "
            "requirements (imageio-ffmpeg) or put FFmpeg on PATH."
        )

    filter_parts = []
    concat_inputs = []
    for index in range(len(audio_paths)):
        filter_parts.append(
            f"[{index}:a:0]loudnorm=I=-16:TP=-1.5:LRA=11,"
            "aresample=44100,"
            f"aformat=sample_fmts=s16:channel_layouts=stereo[a{index}]"
        )
        concat_inputs.append(f"[a{index}]")
    filter_parts.append(
        f"{''.join(concat_inputs)}concat=n={len(audio_paths)}:v=0:a=1[out]"
    )

    command = [ffmpeg, "-y"]
    for audio_path in audio_paths:
        command.extend(["-i", str(audio_path)])
    command.extend(
        [
            "-filter_complex",
            ";".join(filter_parts),
            "-map",
            "[out]",
            "-c:a",
            "pcm_s16le",
            str(output_path),
        ]
    )

    try:
        subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except FileNotFoundError as exc:
        raise TTSGenerationError(
            "FFmpeg could not be started. Install FFmpeg and ensure it is on PATH."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise TTSGenerationError("Combining the message audio timed out.") from exc
    except subprocess.CalledProcessError as exc:
        details = (exc.stderr or "").strip()
        if details:
            details = details[-1000:]
            raise TTSGenerationError(
                f"FFmpeg could not combine the message audio: {details}"
            ) from exc
        raise TTSGenerationError("FFmpeg could not combine the message audio.") from exc
    except OSError as exc:
        raise TTSGenerationError("The combined audio file could not be saved.") from exc

    return output_path


def send_audio_sequence_to_obs(audio_sequence: list[str | Path]) -> Path:
    if not audio_sequence:
        raise TTSGenerationError("There is no audio to combine.")

    obs_settings = _load_obs_settings()
    with tempfile.TemporaryDirectory() as temporary_directory:
        temporary_path = Path(temporary_directory)
        audio_paths = []
        for index, segment in enumerate(audio_sequence):
            if isinstance(segment, Path):
                try:
                    audio_path = segment.resolve(strict=True)
                except OSError as exc:
                    raise TTSGenerationError(
                        f"Audio file does not exist: {segment.name}."
                    ) from exc
                audio_paths.append(audio_path)
            else:
                audio_paths.append(
                    _download_audio_file(
                        segment,
                        temporary_path,
                        f"segment_{index}",
                        "generated audio segment",
                    )
                )

        try:
            AUDIO_DIRECTORY.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise TTSGenerationError(
                "The combined audio directory could not be created."
            ) from exc

        output_path = AUDIO_DIRECTORY / f"{uuid.uuid4().hex}.wav"
        try:
            _compile_audio_files(audio_paths, output_path)
        except TTSGenerationError:
            try:
                output_path.unlink(missing_ok=True)
            except OSError:
                logger.exception("Failed to remove incomplete audio file %s", output_path)
            raise

    sent_audio_path = _send_local_audio_to_obs(output_path, obs_settings)
    _schedule_audio_file_cleanup(sent_audio_path)
    return sent_audio_path
