import atexit
import logging
import os
import threading
import time
import tomllib
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests


API_URL = "https://api.console.tts.monster/generate"
VOICE_ID = "faa92dd8-0517-49da-8f01-1fb03f0e0096"
OBS_CONFIG_PATH = Path(__file__).with_name("config file")
AUDIO_DIRECTORY = Path(__file__).resolve().parent.parent / "media" / "tts"

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


atexit.register(disconnect_from_obs)


def url_tts(message: str) -> str:
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
            json={"voice_id": VOICE_ID, "message": message},
            timeout=30,
        )
        response.raise_for_status()
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


def send_audio_to_obs(audio_url: str) -> Path:
    host, port, _, input_name = _load_obs_settings()

    audio_path = None
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

            AUDIO_DIRECTORY.mkdir(parents=True, exist_ok=True)
            audio_path = AUDIO_DIRECTORY / f"{uuid.uuid4().hex}{extension}"
            with audio_path.open("wb") as audio_file:
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    if chunk:
                        audio_file.write(chunk)
    except requests.RequestException as exc:
        raise TTSGenerationError("The generated audio could not be downloaded.") from exc
    except OSError as exc:
        raise TTSGenerationError("The generated audio could not be saved for OBS.") from exc

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
            client.send(
                "SetInputSettings",
                {
                    "inputName": input_name,
                    "inputSettings": {
                        "is_local_file": True,
                        "local_file": audio_path.resolve().as_posix(),
                    },
                    "overlay": True,
                },
            )
            # Give OBS time to load the new file before restarting playback.
            time.sleep(0.3)
            client.send(
                "TriggerMediaInputAction",
                {
                    "inputName": input_name,
                    "mediaAction": "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_RESTART",
                },
            )
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
