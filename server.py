#!/usr/bin/env python3
"""Plex Open Web: a small authenticated web UI for a local Plex server.

This app uses the Plex API with your server token, then proxies metadata,
posters, and media parts through its own cookie-authenticated web server. It
does not bypass Plex DRM or account systems; it is intended for a server and
media library you control.
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import hmac
import http.cookies
import io
import json
import mimetypes
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"
COOKIE_NAME = "plex_open_session"
STREAM_CHUNK_SIZE = 64 * 1024
TRANSCODE_STARTUP_CHUNK_SIZE = 32 * 1024
DEFAULT_PREFS = (
    "/var/lib/plexmediaserver/Library/Application Support/"
    "Plex Media Server/Preferences.xml"
)


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


load_dotenv(ROOT / ".env")


def env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


class Settings:
    host = os.environ.get("APP_HOST", "0.0.0.0")
    port = env_int("APP_PORT", 5055)
    app_password = os.environ.get("APP_PASSWORD", "")
    app_secret = os.environ.get("APP_SECRET") or secrets.token_urlsafe(48)
    secure_cookie = env_bool("APP_SECURE_COOKIE", False)
    disable_auth = env_bool("APP_DISABLE_AUTH", False)
    session_days = env_int("APP_SESSION_DAYS", 30)
    plex_base_url = os.environ.get("PLEX_BASE_URL", "http://127.0.0.1:32400").rstrip("/")
    plex_token = os.environ.get("PLEX_TOKEN", "")
    plex_preferences_path = os.environ.get("PLEX_PREFERENCES_PATH", DEFAULT_PREFS)
    request_timeout = env_int("PLEX_REQUEST_TIMEOUT", 30)
    stream_timeout = env_int("PLEX_STREAM_TIMEOUT", 3600)
    ffmpeg_path = os.environ.get("FFMPEG_PATH", "ffmpeg")
    saved_media_dir = os.environ.get("SAVED_MEDIA_DIR", str(ROOT.parent / "plex-open-web-saved"))
    opensubtitles_base_url = os.environ.get(
        "OPENSUBTITLES_BASE_URL", "https://api.opensubtitles.com/api/v1"
    )
    opensubtitles_api_key = os.environ.get("OPENSUBTITLES_API_KEY", "")
    opensubtitles_username = os.environ.get("OPENSUBTITLES_USERNAME", "")
    opensubtitles_password = os.environ.get("OPENSUBTITLES_PASSWORD", "")
    opensubtitles_user_agent = os.environ.get("OPENSUBTITLES_USER_AGENT", "PlexOpenWeb v0.1")


def read_plex_token_from_preferences(path: str) -> str:
    try:
        text = Path(path).read_text(errors="ignore")
    except OSError:
        return ""
    match = re.search(r'PlexOnlineToken="([^"]+)"', text)
    return match.group(1) if match else ""


if not Settings.plex_token:
    Settings.plex_token = read_plex_token_from_preferences(Settings.plex_preferences_path)


def json_bytes(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def b64url_decode(data: str) -> bytes:
    pad = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode((data + pad).encode("ascii"))


def sign_payload(payload: str) -> str:
    digest = hmac.new(Settings.app_secret.encode("utf-8"), payload.encode("ascii"), hashlib.sha256)
    return b64url(digest.digest())


def make_session_cookie() -> str:
    now = int(time.time())
    payload = b64url(json_bytes({"iat": now, "exp": now + Settings.session_days * 86400}))
    return f"{payload}.{sign_payload(payload)}"


def verify_session_cookie(value: str) -> bool:
    if not value or "." not in value:
        return False
    payload, signature = value.rsplit(".", 1)
    expected = sign_payload(payload)
    if not hmac.compare_digest(signature, expected):
        return False
    try:
        data = json.loads(b64url_decode(payload).decode("utf-8"))
    except (ValueError, json.JSONDecodeError):
        return False
    return int(data.get("exp", 0)) >= int(time.time())


def parse_cookies(header: str) -> Dict[str, str]:
    cookies = http.cookies.SimpleCookie()
    cookies.load(header or "")
    return {key: morsel.value for key, morsel in cookies.items()}


def safe_static_path(url_path: str) -> Optional[Path]:
    if url_path == "/":
        url_path = "/index.html"
    rel = urllib.parse.unquote(url_path.lstrip("/"))
    target = (STATIC_DIR / rel).resolve()
    try:
        target.relative_to(STATIC_DIR.resolve())
    except ValueError:
        return None
    if target.is_dir():
        target = target / "index.html"
    return target if target.exists() and target.is_file() else None


def safe_plex_path(path: str, prefix: str = "/") -> Optional[str]:
    if not path or not path.startswith(prefix) or "\x00" in path:
        return None
    return urllib.parse.quote(path, safe="/:@!$&'()*+,;=-._~%")


def to_int(value: Optional[str]) -> Optional[int]:
    if value in (None, ""):
        return None
    try:
        return int(float(value))
    except ValueError:
        return None


def to_float(value: Optional[str]) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except ValueError:
        return None


def read_stream_chunk(stream: Any, chunk_size: int = STREAM_CHUNK_SIZE) -> bytes:
    read1 = getattr(stream, "read1", None)
    if callable(read1):
        return read1(chunk_size)
    return stream.read(chunk_size)


def copy_stream(stream: Any, output: Any, chunk_size: int = STREAM_CHUNK_SIZE) -> None:
    while True:
        chunk = read_stream_chunk(stream, chunk_size)
        if not chunk:
            break
        output.write(chunk)
        output.flush()


def write_chunked(output: Any, chunk: bytes) -> None:
    if not chunk:
        return
    output.write(f"{len(chunk):x}\r\n".encode("ascii"))
    output.write(chunk)
    output.write(b"\r\n")
    output.flush()


def copy_chunked_stream(stream: Any, output: Any, chunk_size: int = STREAM_CHUNK_SIZE) -> None:
    while True:
        chunk = read_stream_chunk(stream, chunk_size)
        if not chunk:
            break
        write_chunked(output, chunk)


def finish_chunked(output: Any) -> None:
    output.write(b"0\r\n\r\n")
    output.flush()


def unix_date(value: Optional[str]) -> Optional[str]:
    stamp = to_int(value)
    if not stamp:
        return None
    return time.strftime("%Y-%m-%d", time.localtime(stamp))


class PlexClient:
    def __init__(self, base_url: str, token: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token

    def _url(self, path: str, params: Optional[Dict[str, Any]] = None) -> str:
        if not path.startswith("/"):
            path = "/" + path
        query: Dict[str, Any] = dict(params or {})
        query["X-Plex-Token"] = self.token
        return f"{self.base_url}{path}?{urllib.parse.urlencode(query, doseq=True)}"

    def open(
        self,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        timeout: Optional[int] = None,
    ):
        if not self.token:
            raise RuntimeError("PLEX_TOKEN is not configured and could not be read from Preferences.xml")
        request_headers = {
            "Accept": "application/xml, */*",
            "X-Plex-Product": "Plex Open Web",
            "X-Plex-Client-Identifier": "plex-open-web",
        }
        request_headers.update(headers or {})
        request = urllib.request.Request(self._url(path, params), headers=request_headers)
        return urllib.request.urlopen(request, timeout=timeout or Settings.request_timeout)

    def xml(self, path: str, params: Optional[Dict[str, Any]] = None) -> ET.Element:
        with self.open(path, params=params) as response:
            return ET.fromstring(response.read())


PLEX = PlexClient(Settings.plex_base_url, Settings.plex_token)
OPENSUBTITLES_SESSION: Dict[str, Any] = {
    "token": "",
    "base_url": "",
    "expires_at": 0,
    "user": None,
}


def library_from_xml(elem: ET.Element) -> Dict[str, Any]:
    return {
        "key": elem.get("key"),
        "title": elem.get("title"),
        "type": elem.get("type"),
        "agent": elem.get("agent"),
        "scanner": elem.get("scanner"),
        "updatedAt": to_int(elem.get("updatedAt")),
        "createdAt": to_int(elem.get("createdAt")),
    }


def image_url(path: Optional[str]) -> Optional[str]:
    if not path:
        return None
    return "/api/image?" + urllib.parse.urlencode({"path": path})


LANGUAGE_CODES = {
    "eng": "en",
    "spa": "es",
    "fre": "fr",
    "fra": "fr",
    "ger": "de",
    "deu": "de",
    "ita": "it",
    "por": "pt",
    "dut": "nl",
    "nld": "nl",
    "gre": "el",
    "ell": "el",
    "jpn": "ja",
    "kor": "ko",
    "chi": "zh",
    "zho": "zh",
    "rus": "ru",
    "ara": "ar",
    "hin": "hi",
}
LANGUAGE_NAMES = {
    "el": "Greek",
    "en": "English",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "it": "Italian",
    "pt": "Portuguese",
    "nl": "Dutch",
    "ja": "Japanese",
    "ko": "Korean",
    "zh": "Chinese",
    "ru": "Russian",
    "ar": "Arabic",
    "hi": "Hindi",
}
TEXT_SUBTITLE_CODECS = {"srt", "subrip", "vtt", "webvtt", "ass", "ssa"}
LOCAL_SUBTITLE_EXTENSIONS = {"srt", "vtt", "ass", "ssa"}
BROWSER_AUDIO_CODECS = {"aac", "mp3", "mp4a", "opus"}
BROWSER_VIDEO_CODECS = {"h264", "avc1"}
TRANSCODE_AUDIO_CODECS = {"ac3", "eac3", "dca", "dts", "truehd", "mlp", "flac"}
SAVE_JOBS: Dict[str, Dict[str, Any]] = {}
SAVE_JOBS_LOCK = threading.Lock()


def subtitle_codec_aliases(codec: Optional[str]) -> set:
    normalized = (codec or "").strip().lower()
    aliases = {normalized} if normalized else set()
    if normalized == "srt":
        aliases.add("subrip")
    elif normalized == "subrip":
        aliases.add("srt")
    elif normalized == "vtt":
        aliases.add("webvtt")
    elif normalized == "webvtt":
        aliases.add("vtt")
    return aliases


def part_stream_url(part_key: Optional[str]) -> Optional[str]:
    if not part_key:
        return None
    return "/api/stream?" + urllib.parse.urlencode({"partKey": part_key})


def compatible_stream_url(part_key: Optional[str]) -> Optional[str]:
    if not part_key:
        return None
    return "/api/stream-compatible?" + urllib.parse.urlencode({"partKey": part_key})


def subtitle_url(stream_key: Optional[str], codec: Optional[str]) -> Optional[str]:
    if not stream_key:
        return None
    return "/api/subtitle?" + urllib.parse.urlencode(
        {"streamKey": stream_key, "codec": (codec or "").lower()}
    )


def embedded_subtitle_url(
    rating_key: Optional[str],
    part_id: Optional[str],
    stream_id: Optional[str],
    stream_index: Optional[str],
    codec: Optional[str],
) -> Optional[str]:
    if not rating_key or not part_id or not stream_id or stream_index in (None, ""):
        return None
    return "/api/embedded-subtitle?" + urllib.parse.urlencode(
        {
            "ratingKey": rating_key,
            "partId": part_id,
            "streamId": stream_id,
            "streamIndex": stream_index,
            "codec": (codec or "").lower(),
        }
    )


def language_code(value: Optional[str]) -> str:
    code = (value or "und").lower()
    return LANGUAGE_CODES.get(code, code)


def subtitle_language_name(code: Optional[str]) -> str:
    normalized = language_code(code)
    return LANGUAGE_NAMES.get(normalized, normalized.upper() if normalized != "und" else "Subtitle")


def opensubtitles_language_code(value: Optional[str], default: str = "el") -> str:
    code = language_code(value or default).strip().lower()
    return code if code and code != "und" else default


def bool_attr(elem: ET.Element, name: str) -> bool:
    return elem.get(name) == "1"


def truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def subtitle_from_stream(
    elem: ET.Element,
    rating_key: Optional[str] = None,
    part_elem: Optional[ET.Element] = None,
) -> Optional[Dict[str, Any]]:
    if elem.get("streamType") != "3":
        return None
    codec = (elem.get("codec") or elem.get("format") or "").lower()
    stream_key = elem.get("key")
    stream_id = elem.get("id")
    stream_index = elem.get("index")
    part_id = part_elem.get("id") if part_elem is not None else None
    sidecar_url = subtitle_url(stream_key, codec) if codec in TEXT_SUBTITLE_CODECS else None
    embedded_url = (
        embedded_subtitle_url(rating_key, part_id, stream_id, stream_index, codec)
        if codec in TEXT_SUBTITLE_CODECS and not sidecar_url
        else None
    )
    subtitle_stream_url = sidecar_url or embedded_url
    supported = codec in TEXT_SUBTITLE_CODECS and bool(subtitle_stream_url)
    label_parts = [
        elem.get("displayTitle") or elem.get("language") or elem.get("title") or "Subtitle",
        elem.get("title") if elem.get("title") != elem.get("displayTitle") else None,
    ]
    if bool_attr(elem, "forced"):
        label_parts.append("Forced")
    if bool_attr(elem, "hearingImpaired"):
        label_parts.append("SDH")
    label = " - ".join(part for part in label_parts if part)
    return {
        "id": elem.get("id"),
        "key": stream_key,
        "partId": part_id,
        "streamId": stream_id,
        "streamIndex": to_int(stream_index),
        "codec": codec or None,
        "language": elem.get("language"),
        "languageCode": elem.get("languageCode"),
        "srclang": language_code(elem.get("languageCode")),
        "title": elem.get("title"),
        "displayTitle": elem.get("displayTitle"),
        "label": label,
        "default": bool_attr(elem, "default"),
        "selected": bool_attr(elem, "selected"),
        "forced": bool_attr(elem, "forced"),
        "hearingImpaired": bool_attr(elem, "hearingImpaired"),
        "external": bool_attr(elem, "external") or bool(stream_key),
        "embedded": bool(embedded_url),
        "supported": supported,
        "source": "embedded" if embedded_url else ("plex" if stream_key else None),
        "subtitleUrl": subtitle_stream_url if supported else None,
    }


def local_subtitle_url(rating_key: str, filename: str, codec: str) -> str:
    return "/api/local-subtitle?" + urllib.parse.urlencode(
        {"ratingKey": rating_key, "name": filename, "codec": codec}
    )


def subtitle_filename_tokens(media_path: Path, subtitle_path: Path) -> List[str]:
    stem = subtitle_path.stem
    prefix = media_path.stem
    if stem == prefix:
        return []
    if stem.startswith(prefix):
        stem = stem[len(prefix) :]
    stem = stem.strip(" ._-")
    return [token.lower() for token in re.split(r"[ ._-]+", stem) if token]


def infer_sidecar_language(media_path: Path, subtitle_path: Path) -> str:
    ignored = {"default", "forced", "sdh", "cc", "hi", "opensubtitles"}
    for token in subtitle_filename_tokens(media_path, subtitle_path):
        if token in ignored or token.startswith("opensubtitles"):
            continue
        code = language_code(token)
        if token in LANGUAGE_CODES or code in LANGUAGE_NAMES:
            return code
    return "und"


def local_subtitle_from_path(
    rating_key: str,
    media_path: Path,
    subtitle_path: Path,
    selected: bool = False,
) -> Optional[Dict[str, Any]]:
    codec = subtitle_path.suffix.lower().lstrip(".")
    if codec not in LOCAL_SUBTITLE_EXTENSIONS:
        return None
    tokens = set(subtitle_filename_tokens(media_path, subtitle_path))
    srclang = infer_sidecar_language(media_path, subtitle_path)
    source = "OpenSubtitles" if any(token.startswith("opensubtitles") for token in tokens) else "Local"
    label_parts = [subtitle_language_name(srclang), source]
    if "forced" in tokens:
        label_parts.append("Forced")
    if tokens.intersection({"sdh", "cc", "hi"}):
        label_parts.append("SDH")
    label = " - ".join(part for part in label_parts if part)
    return {
        "id": f"local:{subtitle_path.name}",
        "key": subtitle_path.name,
        "codec": codec,
        "language": subtitle_language_name(srclang) if srclang != "und" else None,
        "languageCode": srclang,
        "srclang": srclang,
        "title": subtitle_path.name,
        "displayTitle": label,
        "label": label,
        "default": "default" in tokens,
        "selected": selected,
        "forced": "forced" in tokens,
        "hearingImpaired": bool(tokens.intersection({"sdh", "cc", "hi"})),
        "external": True,
        "supported": True,
        "source": source.lower(),
        "subtitleUrl": local_subtitle_url(rating_key, subtitle_path.name, codec),
    }


def local_subtitles_for_part(rating_key: Optional[str], part_file: Optional[str]) -> List[Dict[str, Any]]:
    if not rating_key or not part_file:
        return []
    media_path = Path(part_file)
    if not media_path.is_absolute():
        return []
    try:
        candidates = sorted(media_path.parent.iterdir(), key=lambda path: path.name.lower())
    except OSError:
        return []
    subtitles: List[Dict[str, Any]] = []
    for candidate in candidates:
        if not candidate.is_file():
            continue
        if candidate.suffix.lower().lstrip(".") not in LOCAL_SUBTITLE_EXTENSIONS:
            continue
        if candidate.stem != media_path.stem and not candidate.name.startswith(f"{media_path.stem}."):
            continue
        subtitle = local_subtitle_from_path(rating_key, media_path, candidate)
        if subtitle:
            subtitles.append(subtitle)
    return subtitles


def subtitles_for_part(
    rating_key: Optional[str],
    part_elem: ET.Element,
) -> List[Dict[str, Any]]:
    subtitles: List[Dict[str, Any]] = []
    for stream in part_elem.findall("Stream"):
        subtitle = subtitle_from_stream(stream, rating_key, part_elem)
        if subtitle is not None:
            subtitles.append(subtitle)
    subtitles.extend(local_subtitles_for_part(rating_key, part_elem.get("file")))
    return subtitles


def has_preferred_supported_subtitle(subtitles: List[Dict[str, Any]]) -> bool:
    return any(
        subtitle.get("supported")
        and (subtitle.get("selected") or subtitle.get("default") or subtitle.get("forced"))
        for subtitle in subtitles
    )


def media_details(media_elem: Optional[ET.Element], part_elem: ET.Element) -> Dict[str, Any]:
    return {
        "partId": part_elem.get("id"),
        "partKey": part_elem.get("key"),
        "container": media_elem.get("container") if media_elem is not None else None,
        "videoCodec": media_elem.get("videoCodec") if media_elem is not None else None,
        "audioCodec": media_elem.get("audioCodec") if media_elem is not None else None,
        "videoResolution": media_elem.get("videoResolution") if media_elem is not None else None,
        "bitrate": to_int(media_elem.get("bitrate")) if media_elem is not None else None,
        "file": Path(part_elem.get("file", "")).name if part_elem.get("file") else None,
        "size": to_int(part_elem.get("size")),
        "duration": to_int(part_elem.get("duration")),
    }


def first_part(elem: ET.Element) -> Tuple[Optional[str], Dict[str, Any], List[Dict[str, Any]]]:
    candidates: List[Tuple[ET.Element, ET.Element, List[Dict[str, Any]]]] = []
    for media in elem.findall("Media"):
        part = media.find("Part")
        if part is not None:
            candidates.append((media, part, subtitles_for_part(elem.get("ratingKey"), part)))
    if not candidates:
        return None, {}, []

    selected_media, selected_part, selected_subtitles = candidates[0]
    if not has_preferred_supported_subtitle(selected_subtitles):
        for media, part, subtitles in candidates[1:]:
            if has_preferred_supported_subtitle(subtitles):
                selected_media, selected_part, selected_subtitles = media, part, subtitles
                break

    return selected_part.get("key"), media_details(selected_media, selected_part), selected_subtitles


def playback_info(part_key: Optional[str], media: Dict[str, Any]) -> Dict[str, Any]:
    audio_codec = (media.get("audioCodec") or "").lower()
    video_codec = (media.get("videoCodec") or "").lower()
    needs_audio_transcode = audio_codec in TRANSCODE_AUDIO_CODECS or (
        audio_codec and audio_codec not in BROWSER_AUDIO_CODECS
    )
    return {
        "audioCodec": audio_codec or None,
        "videoCodec": video_codec or None,
        "directStreamUrl": part_stream_url(part_key),
        "compatibleStreamUrl": compatible_stream_url(part_key) if needs_audio_transcode else part_stream_url(part_key),
        "audioTranscodeRequired": needs_audio_transcode,
        "audioTranscodeReason": (
            f"{audio_codec.upper()} audio is not reliably supported by browser video playback"
            if needs_audio_transcode and audio_codec
            else None
        ),
    }


def saved_playback_dir(create: bool = False) -> Path:
    path = Path(Settings.saved_media_dir).expanduser()
    if not path.is_absolute():
        path = ROOT / path
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def saved_playback_id(rating_key: Optional[str], part_key: Optional[str]) -> Optional[str]:
    if not rating_key or not part_key:
        return None
    return hashlib.sha256(f"{rating_key}:{part_key}".encode("utf-8")).hexdigest()[:24]


def saved_playback_paths(playback_id: str, create: bool = False) -> Tuple[Path, Path, Path]:
    base = saved_playback_dir(create=create)
    return base / f"{playback_id}.mp4", base / f"{playback_id}.tmp.mp4", base / f"{playback_id}.json"


def saved_playback_stream_url(playback_id: str) -> str:
    return "/api/saved-stream?" + urllib.parse.urlencode({"id": playback_id})


def saved_playback_status(
    rating_key: Optional[str],
    part_key: Optional[str],
    media: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    playback_id = saved_playback_id(rating_key, part_key)
    if not playback_id:
        return {"state": "unavailable", "ready": False}

    final_path, _, _ = saved_playback_paths(playback_id)
    with SAVE_JOBS_LOCK:
        job = dict(SAVE_JOBS.get(playback_id) or {})
    if final_path.is_file():
        return {
            "id": playback_id,
            "state": "ready",
            "ready": True,
            "streamUrl": saved_playback_stream_url(playback_id),
            "bytes": final_path.stat().st_size,
            "updatedAt": int(final_path.stat().st_mtime),
            "media": media or {},
        }
    if job:
        return {
            "id": playback_id,
            "state": job.get("state", "saving"),
            "ready": False,
            "startedAt": job.get("startedAt"),
            "message": job.get("message"),
            "media": media or {},
        }
    return {"id": playback_id, "state": "missing", "ready": False, "media": media or {}}


def saved_playback_command(part_key: str, media: Dict[str, Any], output_path: Path) -> List[str]:
    plex_path = safe_plex_path(part_key, prefix="/library/parts/")
    if not plex_path:
        raise ValueError("bad_part_key")
    input_url = PLEX._url(plex_path, {"download": "1"})
    video_codec = (media.get("videoCodec") or "").lower()
    audio_codec = (media.get("audioCodec") or "").lower()
    transcode_video = video_codec not in BROWSER_VIDEO_CODECS
    transcode_audio = audio_codec in TRANSCODE_AUDIO_CODECS or (
        audio_codec and audio_codec not in BROWSER_AUDIO_CODECS
    )
    command = [
        Settings.ffmpeg_path,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-nostdin",
        "-fflags",
        "+genpts",
        "-i",
        input_url,
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        "-sn",
        "-dn",
    ]
    if transcode_video:
        command.extend(
            [
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "23",
                "-pix_fmt",
                "yuv420p",
            ]
        )
    else:
        command.extend(["-c:v", "copy"])
    if transcode_audio:
        command.extend(["-c:a", "aac", "-b:a", "160k", "-ac", "2"])
    else:
        command.extend(["-c:a", "copy"])
    command.extend(["-movflags", "+faststart", "-f", "mp4", str(output_path)])
    return command


def metadata_item_for_rating_key(rating_key: str) -> Optional[Dict[str, Any]]:
    elem = metadata_item_element(rating_key)
    return item_from_xml(elem) if elem is not None else None


def run_saved_playback_job(
    playback_id: str,
    rating_key: str,
    part_key: str,
    media: Dict[str, Any],
    title: Optional[str],
) -> None:
    final_path, temp_path, meta_path = saved_playback_paths(playback_id, create=True)
    try:
        if final_path.is_file():
            with SAVE_JOBS_LOCK:
                SAVE_JOBS.pop(playback_id, None)
            return
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass
        command = saved_playback_command(part_key, media, temp_path)
        process = subprocess.run(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            timeout=Settings.stream_timeout,
        )
        if process.returncode != 0:
            message = process.stderr.decode("utf-8", errors="ignore").strip()
            raise RuntimeError(message or "ffmpeg could not save a compatible playback file")
        temp_path.replace(final_path)
        meta_path.write_text(
            json.dumps(
                {
                    "id": playback_id,
                    "ratingKey": rating_key,
                    "partKey": part_key,
                    "title": title,
                    "media": media,
                    "createdAt": int(time.time()),
                    "bytes": final_path.stat().st_size,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    except Exception as exc:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass
        with SAVE_JOBS_LOCK:
            SAVE_JOBS[playback_id] = {
                "state": "error",
                "ready": False,
                "startedAt": SAVE_JOBS.get(playback_id, {}).get("startedAt"),
                "message": str(exc)[:500],
            }
        return
    with SAVE_JOBS_LOCK:
        SAVE_JOBS.pop(playback_id, None)


def start_saved_playback(item: Dict[str, Any]) -> Dict[str, Any]:
    rating_key = item.get("ratingKey")
    part_key = item.get("partKey")
    media = item.get("media") or {}
    playback_id = saved_playback_id(rating_key, part_key)
    if not playback_id or not part_key:
        return {"state": "unavailable", "ready": False}
    status = saved_playback_status(rating_key, part_key, media)
    if status.get("ready"):
        return status
    already_saving = False
    with SAVE_JOBS_LOCK:
        existing = SAVE_JOBS.get(playback_id)
        if existing and existing.get("state") == "saving":
            already_saving = True
        else:
            SAVE_JOBS[playback_id] = {"state": "saving", "ready": False, "startedAt": int(time.time())}
    if already_saving:
        return saved_playback_status(rating_key, part_key, media)
    thread = threading.Thread(
        target=run_saved_playback_job,
        args=(playback_id, str(rating_key), str(part_key), media, item.get("title")),
        daemon=True,
    )
    thread.start()
    return saved_playback_status(rating_key, part_key, media)


def delete_saved_playback(item: Dict[str, Any]) -> Dict[str, Any]:
    rating_key = item.get("ratingKey")
    part_key = item.get("partKey")
    media = item.get("media") or {}
    playback_id = saved_playback_id(rating_key, part_key)
    if not playback_id:
        return {"state": "unavailable", "ready": False}
    with SAVE_JOBS_LOCK:
        job = SAVE_JOBS.get(playback_id)
        if job and job.get("state") == "saving":
            delete_blocked = True
        else:
            delete_blocked = False
            SAVE_JOBS.pop(playback_id, None)
    if delete_blocked:
        return {**saved_playback_status(rating_key, part_key, media), "deleteBlocked": True}
    for path in saved_playback_paths(playback_id):
        try:
            path.unlink()
        except FileNotFoundError:
            pass
    return saved_playback_status(rating_key, part_key, media)


def parse_range_header(value: Optional[str], size: int) -> Optional[Tuple[int, int]]:
    if not value:
        return None
    match = re.match(r"bytes=(\d*)-(\d*)$", value.strip())
    if not match:
        return None
    start_raw, end_raw = match.groups()
    if not start_raw and not end_raw:
        return None
    if start_raw:
        start = int(start_raw)
        end = int(end_raw) if end_raw else size - 1
    else:
        suffix = int(end_raw)
        if suffix <= 0:
            return None
        start = max(0, size - suffix)
        end = size - 1
    if start >= size or end < start:
        return None
    return start, min(end, size - 1)


def external_ids_from_xml(elem: ET.Element) -> Tuple[List[Dict[str, str]], Dict[str, str]]:
    guids: List[Dict[str, str]] = []
    ids: Dict[str, str] = {}
    for child in elem.findall("Guid"):
        raw = child.get("id") or ""
        if "://" not in raw:
            continue
        source, value = raw.split("://", 1)
        source = source.lower()
        if not source or not value:
            continue
        guids.append({"source": source, "id": value})
        if source in {"imdb", "tmdb", "tvdb"}:
            ids[f"{source}Id"] = value
    return guids, ids


def item_from_xml(elem: ET.Element) -> Dict[str, Any]:
    part_key, media, subtitles = first_part(elem)
    title = elem.get("title") or elem.get("parentTitle") or elem.get("grandparentTitle") or "Untitled"
    item_type = elem.get("type") or elem.tag.lower()
    guids, external_ids = external_ids_from_xml(elem)
    item = {
        "ratingKey": elem.get("ratingKey"),
        "key": elem.get("key"),
        "type": item_type,
        "title": title,
        "sortTitle": elem.get("titleSort"),
        "year": to_int(elem.get("year")),
        "summary": elem.get("summary"),
        "tagline": elem.get("tagline"),
        "contentRating": elem.get("contentRating"),
        "rating": to_float(elem.get("rating")),
        "audienceRating": to_float(elem.get("audienceRating")),
        "duration": to_int(elem.get("duration")),
        "durationText": duration_text(to_int(elem.get("duration"))),
        "addedAt": to_int(elem.get("addedAt")),
        "addedDate": unix_date(elem.get("addedAt")),
        "updatedAt": to_int(elem.get("updatedAt")),
        "viewCount": to_int(elem.get("viewCount")) or 0,
        "lastViewedAt": to_int(elem.get("lastViewedAt")),
        "lastViewedDate": unix_date(elem.get("lastViewedAt")),
        "originallyAvailableAt": elem.get("originallyAvailableAt"),
        "librarySectionID": elem.get("librarySectionID"),
        "librarySectionTitle": elem.get("librarySectionTitle"),
        "parentRatingKey": elem.get("parentRatingKey"),
        "grandparentRatingKey": elem.get("grandparentRatingKey"),
        "parentTitle": elem.get("parentTitle"),
        "grandparentTitle": elem.get("grandparentTitle"),
        "index": to_int(elem.get("index")),
        "parentIndex": to_int(elem.get("parentIndex")),
        "leafCount": to_int(elem.get("leafCount")),
        "viewedLeafCount": to_int(elem.get("viewedLeafCount")),
        "childCount": to_int(elem.get("childCount")),
        "thumb": elem.get("thumb"),
        "art": elem.get("art"),
        "posterUrl": image_url(elem.get("thumb")),
        "artUrl": image_url(elem.get("art")),
        "partKey": part_key,
        "streamUrl": part_stream_url(part_key),
        "compatibleStreamUrl": compatible_stream_url(part_key),
        "playback": playback_info(part_key, media),
        "savedPlayback": saved_playback_status(elem.get("ratingKey"), part_key, media),
        "subtitles": subtitles,
        "media": media,
        "guids": guids,
    }
    item.update(external_ids)
    return item


def duration_text(ms: Optional[int]) -> Optional[str]:
    if not ms:
        return None
    minutes = max(1, round(ms / 60000))
    hours, rem = divmod(minutes, 60)
    if hours:
        return f"{hours}h {rem}m" if rem else f"{hours}h"
    return f"{rem}m"


def decode_subtitle(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-16", "cp1253", "cp1252", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def srt_to_vtt(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip("\ufeff\n ")
    if normalized.lstrip().upper().startswith("WEBVTT"):
        return normalized if normalized.endswith("\n") else normalized + "\n"
    lines = ["WEBVTT", ""]
    for raw_block in re.split(r"\n{2,}", normalized):
        block = raw_block.strip()
        if not block:
            continue
        block_lines = block.split("\n")
        if block_lines and re.fullmatch(r"\d+", block_lines[0].strip()):
            block_lines = block_lines[1:]
        if not block_lines:
            continue
        timing_index = next((i for i, line in enumerate(block_lines) if "-->" in line), None)
        if timing_index is None:
            continue
        timing = block_lines[timing_index].replace(",", ".")
        cue_lines = [line.strip("\ufeff") for line in block_lines[timing_index + 1 :]]
        lines.append(timing)
        lines.extend(cue_lines)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def ass_timestamp_to_vtt(value: str) -> str:
    match = re.match(r"(?:(\d+):)?(\d{1,2}):(\d{2})[.](\d{1,2})", value.strip())
    if not match:
        return "00:00:00.000"
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2))
    seconds = int(match.group(3))
    centiseconds = int(match.group(4).ljust(2, "0")[:2])
    return f"{hours:02}:{minutes:02}:{seconds:02}.{centiseconds * 10:03}"


def ass_to_vtt(text: str) -> str:
    lines = ["WEBVTT", ""]
    fields: List[str] = []
    for raw_line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw_line.strip()
        if line.lower().startswith("format:"):
            fields = [part.strip().lower() for part in line.split(":", 1)[1].split(",")]
        elif line.lower().startswith("dialogue:"):
            payload = line.split(":", 1)[1].lstrip()
            parts = payload.split(",", max(0, len(fields) - 1)) if fields else payload.split(",", 9)
            if fields and len(parts) == len(fields):
                row = dict(zip(fields, parts))
                start, end, text_value = row.get("start"), row.get("end"), row.get("text")
            elif len(parts) >= 10:
                start, end, text_value = parts[1], parts[2], parts[9]
            else:
                continue
            if not start or not end or text_value is None:
                continue
            clean_text = re.sub(r"\{[^}]*\}", "", text_value).replace(r"\N", "\n").replace(r"\n", "\n")
            lines.append(f"{ass_timestamp_to_vtt(start)} --> {ass_timestamp_to_vtt(end)}")
            lines.extend(clean_text.split("\n"))
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def subtitle_to_vtt(data: bytes, codec: str) -> bytes:
    text = decode_subtitle(data)
    normalized_codec = codec.lower()
    if normalized_codec in {"ass", "ssa"} or "[script info]" in text[:300].lower():
        return ass_to_vtt(text).encode("utf-8")
    return srt_to_vtt(text).encode("utf-8")


class OpenSubtitlesError(Exception):
    def __init__(self, message: str, status: int = 502, upstream_status: Optional[int] = None) -> None:
        super().__init__(message)
        self.status = status
        self.upstream_status = upstream_status


def normalize_opensubtitles_base(value: str) -> str:
    base = (value or "https://api.opensubtitles.com/api/v1").strip().rstrip("/")
    if not urllib.parse.urlparse(base).scheme:
        base = "https://" + base
    parsed = urllib.parse.urlparse(base)
    if parsed.path in {"", "/"}:
        base = base.rstrip("/") + "/api/v1"
    return base.rstrip("/")


def opensubtitles_base_url() -> str:
    return normalize_opensubtitles_base(
        str(OPENSUBTITLES_SESSION.get("base_url") or Settings.opensubtitles_base_url)
    )


def opensubtitles_url(path: str, base_url: Optional[str] = None) -> str:
    base = normalize_opensubtitles_base(base_url or opensubtitles_base_url())
    return f"{base}/{path.lstrip('/')}"


def opensubtitles_headers(auth: bool = False, json_body: bool = False) -> Dict[str, str]:
    headers = {
        "Api-Key": Settings.opensubtitles_api_key,
        "User-Agent": Settings.opensubtitles_user_agent,
        "Accept": "application/json",
    }
    if json_body:
        headers["Content-Type"] = "application/json"
    if auth:
        headers["Authorization"] = f"Bearer {opensubtitles_token()}"
    return headers


def opensubtitles_configured(require_login: bool = False) -> Tuple[bool, str]:
    if not Settings.opensubtitles_api_key:
        return False, "OpenSubtitles API key is not configured."
    if require_login and (not Settings.opensubtitles_username or not Settings.opensubtitles_password):
        return False, "OpenSubtitles username and password are not configured."
    return True, ""


def opensubtitles_token() -> str:
    configured, message = opensubtitles_configured(require_login=True)
    if not configured:
        raise OpenSubtitlesError(message, status=400)
    now = int(time.time())
    token = str(OPENSUBTITLES_SESSION.get("token") or "")
    expires_at = int(OPENSUBTITLES_SESSION.get("expires_at") or 0)
    if token and expires_at > now + 60:
        return token

    payload = {
        "username": Settings.opensubtitles_username,
        "password": Settings.opensubtitles_password,
    }
    request = urllib.request.Request(
        opensubtitles_url("/login", Settings.opensubtitles_base_url),
        data=json_bytes(payload),
        headers=opensubtitles_headers(json_body=True),
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=Settings.request_timeout) as response:
            data = json.loads(response.read(1024 * 1024).decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read(4096).decode("utf-8", errors="ignore")
        raise OpenSubtitlesError(body or "OpenSubtitles login failed.", status=502, upstream_status=exc.code)
    except OSError as exc:
        raise OpenSubtitlesError(str(exc), status=502)

    token = str(data.get("token") or "")
    if not token:
        raise OpenSubtitlesError("OpenSubtitles login did not return a token.", status=502)
    OPENSUBTITLES_SESSION.update(
        {
            "token": token,
            "base_url": data.get("base_url") or Settings.opensubtitles_base_url,
            "expires_at": now + 12 * 3600,
            "user": data.get("user"),
        }
    )
    return token


def opensubtitles_json(
    method: str,
    path: str,
    params: Optional[Dict[str, Any]] = None,
    payload: Optional[Dict[str, Any]] = None,
    auth: bool = False,
    retry: bool = True,
) -> Dict[str, Any]:
    url = opensubtitles_url(path)
    if params:
        url = f"{url}?{urllib.parse.urlencode(params, doseq=True)}"
    request = urllib.request.Request(
        url,
        data=json_bytes(payload) if payload is not None else None,
        headers=opensubtitles_headers(auth=auth, json_body=payload is not None),
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=Settings.request_timeout) as response:
            body = response.read(4 * 1024 * 1024)
            return json.loads(body.decode("utf-8")) if body else {}
    except urllib.error.HTTPError as exc:
        body = exc.read(4096).decode("utf-8", errors="ignore")
        if auth and retry and exc.code == 401:
            OPENSUBTITLES_SESSION.update({"token": "", "expires_at": 0})
            return opensubtitles_json(method, path, params=params, payload=payload, auth=auth, retry=False)
        raise OpenSubtitlesError(body or "OpenSubtitles request failed.", status=502, upstream_status=exc.code)
    except OSError as exc:
        raise OpenSubtitlesError(str(exc), status=502)


def metadata_item_element(rating_key: str) -> Optional[ET.Element]:
    root = PLEX.xml(f"/library/metadata/{urllib.parse.quote(rating_key)}", params={"includeGuids": "1"})
    for elem in root.iter():
        if elem.tag in {"Video", "Directory", "Track"} and elem.get("ratingKey") == rating_key:
            return elem
    for elem in root:
        if elem.tag in {"Video", "Directory", "Track"}:
            return elem
    return None


def first_part_file(elem: ET.Element) -> Optional[Path]:
    for media in elem.findall("Media"):
        part = media.find("Part")
        if part is not None and part.get("file"):
            return Path(part.get("file", ""))
    return None


def imdb_numeric(imdb_id: Optional[str]) -> Optional[str]:
    if not imdb_id:
        return None
    match = re.search(r"tt0*(\d+)", imdb_id, re.I)
    if match:
        return match.group(1)
    digits = re.sub(r"\D+", "", imdb_id).lstrip("0")
    return digits or None


def subtitle_search_params(elem: ET.Element, query_text: str, language: str) -> Dict[str, Any]:
    _, ids = external_ids_from_xml(elem)
    params: Dict[str, Any] = {}
    if language and language != "all":
        params["languages"] = language
    imdb_id = imdb_numeric(ids.get("imdbId"))
    if imdb_id:
        params["imdb_id"] = imdb_id
    elif ids.get("tmdbId"):
        params["tmdb_id"] = ids["tmdbId"]
    if query_text:
        params["query"] = query_text
    else:
        title = elem.get("grandparentTitle") if elem.get("type") == "episode" else elem.get("title")
        if title:
            params["query"] = title
    if elem.get("type") == "episode":
        if elem.get("parentIndex"):
            params["season_number"] = elem.get("parentIndex")
        if elem.get("index"):
            params["episode_number"] = elem.get("index")
    elif elem.get("year"):
        params["year"] = elem.get("year")
    return params


def normalize_opensubtitles_result(entry: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    attrs = entry.get("attributes") or {}
    files = attrs.get("files") or []
    file_info = next((file for file in files if file.get("file_id")), files[0] if files else None)
    if not file_info or not file_info.get("file_id"):
        return None
    language = opensubtitles_language_code(attrs.get("language"), default="und")
    release = attrs.get("release") or file_info.get("file_name") or attrs.get("feature_details", {}).get("title")
    return {
        "id": entry.get("id") or attrs.get("subtitle_id"),
        "subtitleId": attrs.get("subtitle_id"),
        "fileId": file_info.get("file_id"),
        "fileName": file_info.get("file_name"),
        "language": language,
        "languageName": subtitle_language_name(language),
        "release": release,
        "downloads": attrs.get("download_count") or attrs.get("new_download_count"),
        "fps": attrs.get("fps"),
        "rating": attrs.get("ratings"),
        "votes": attrs.get("votes"),
        "hearingImpaired": truthy(attrs.get("hearing_impaired")),
        "trusted": truthy(attrs.get("from_trusted")),
        "foreignPartsOnly": truthy(attrs.get("foreign_parts_only")),
        "aiTranslated": truthy(attrs.get("ai_translated")) or truthy(attrs.get("machine_translated")),
        "uploadDate": attrs.get("upload_date"),
    }


def content_disposition_filename(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    match = re.search(r"filename\*=UTF-8''([^;]+)", value, re.I)
    if match:
        return urllib.parse.unquote(match.group(1).strip().strip('"'))
    match = re.search(r'filename="?([^";]+)"?', value, re.I)
    if match:
        return match.group(1).strip()
    return None


def subtitle_payload_ext(filename: Optional[str], data: bytes) -> str:
    suffix = Path(filename or "").suffix.lower().lstrip(".")
    if suffix in LOCAL_SUBTITLE_EXTENSIONS:
        return suffix
    sample = decode_subtitle(data[:4096]).lstrip().lower()
    if sample.startswith("webvtt"):
        return "vtt"
    if "[script info]" in sample or sample.startswith("[v4"):
        return "ass"
    return "srt"


def unpack_subtitle_payload(
    data: bytes,
    filename: Optional[str] = None,
) -> Tuple[bytes, str, Optional[str]]:
    if data.startswith(b"\x1f\x8b"):
        data = gzip.decompress(data)
    if zipfile.is_zipfile(io.BytesIO(data)):
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = [
                name
                for name in archive.namelist()
                if Path(name).suffix.lower().lstrip(".") in LOCAL_SUBTITLE_EXTENSIONS
            ]
            if not names:
                raise OpenSubtitlesError("Downloaded archive did not contain a supported subtitle file.", status=502)
            filename = names[0]
            data = archive.read(filename)
    ext = subtitle_payload_ext(filename, data)
    return decode_subtitle(data).strip("\ufeff").encode("utf-8"), ext, filename


def download_opensubtitles_file(file_id: Any) -> Tuple[bytes, str, Optional[str], Dict[str, Any]]:
    try:
        normalized_file_id: Any = int(str(file_id))
    except ValueError:
        normalized_file_id = str(file_id)
    payload = {
        "file_id": normalized_file_id,
        "sub_format": "srt",
        "strip_html": True,
        "cleanup_links": True,
        "remove_adds": True,
    }
    data = opensubtitles_json("POST", "/download", payload=payload, auth=True)
    link = data.get("link")
    if not link:
        raise OpenSubtitlesError("OpenSubtitles did not return a download link.", status=502)
    request = urllib.request.Request(str(link), headers={"User-Agent": Settings.opensubtitles_user_agent})
    try:
        with urllib.request.urlopen(request, timeout=Settings.stream_timeout) as response:
            raw = response.read(30 * 1024 * 1024)
            filename = content_disposition_filename(response.headers.get("Content-Disposition")) or data.get("file_name")
    except urllib.error.HTTPError as exc:
        body = exc.read(4096).decode("utf-8", errors="ignore")
        raise OpenSubtitlesError(body or "Subtitle download failed.", status=502, upstream_status=exc.code)
    except OSError as exc:
        raise OpenSubtitlesError(str(exc), status=502)
    subtitle_data, ext, inner_name = unpack_subtitle_payload(raw, filename)
    return subtitle_data, ext, inner_name or filename, data


def subtitle_save_path(media_path: Path, language: str, file_id: Any, ext: str) -> Path:
    safe_language = re.sub(r"[^a-z0-9-]+", "", opensubtitles_language_code(language)) or "und"
    safe_file_id = re.sub(r"[^A-Za-z0-9_-]+", "", str(file_id)) or secrets.token_hex(4)
    safe_ext = ext if ext in LOCAL_SUBTITLE_EXTENSIONS else "srt"
    return media_path.parent / f"{media_path.stem}.{safe_language}.opensubtitles-{safe_file_id}.{safe_ext}"


def resolve_local_subtitle(rating_key: str, filename: str) -> Tuple[Optional[Path], Optional[Path]]:
    if not rating_key or not filename or "/" in filename or "\\" in filename or "\x00" in filename:
        return None, None
    elem = metadata_item_element(rating_key)
    if elem is None:
        return None, None
    media_path = first_part_file(elem)
    if media_path is None or not media_path.is_absolute():
        return None, None
    candidate = (media_path.parent / filename).resolve()
    try:
        candidate.relative_to(media_path.parent.resolve())
    except ValueError:
        return None, None
    if candidate.suffix.lower().lstrip(".") not in LOCAL_SUBTITLE_EXTENSIONS:
        return None, None
    if candidate.stem != media_path.stem and not candidate.name.startswith(f"{media_path.stem}."):
        return None, None
    if not candidate.is_file():
        return None, None
    return media_path, candidate


def resolve_embedded_subtitle(
    rating_key: str,
    part_id: str,
    stream_id: str,
    stream_index: Optional[int],
    requested_codec: str,
) -> Optional[Tuple[Path, int, str]]:
    if not rating_key or not part_id or not stream_id or stream_index is None:
        return None
    elem = metadata_item_element(rating_key)
    if elem is None:
        return None
    requested_aliases = subtitle_codec_aliases(requested_codec)
    for media in elem.findall("Media"):
        part = media.find("Part")
        if part is None or part.get("id") != part_id:
            continue
        media_path = Path(part.get("file", ""))
        if not media_path.is_absolute() or not media_path.is_file():
            return None
        for stream in part.findall("Stream"):
            if stream.get("streamType") != "3" or stream.get("id") != stream_id:
                continue
            actual_index = to_int(stream.get("index"))
            actual_codec = (stream.get("codec") or stream.get("format") or "").lower()
            if actual_index != stream_index or actual_codec not in TEXT_SUBTITLE_CODECS:
                return None
            if requested_aliases and actual_codec not in requested_aliases:
                return None
            return media_path, actual_index, actual_codec
    return None


def extract_embedded_subtitle(media_path: Path, stream_index: int) -> bytes:
    command = [
        Settings.ffmpeg_path,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-i",
        str(media_path),
        "-map",
        f"0:{stream_index}",
        "-f",
        "webvtt",
        "pipe:1",
    ]
    process = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        timeout=min(Settings.stream_timeout, 120),
    )
    if process.returncode != 0:
        message = process.stderr.decode("utf-8", errors="ignore").strip()
        raise RuntimeError(message or "ffmpeg could not extract the embedded subtitle stream")
    if len(process.stdout) > 30 * 1024 * 1024:
        raise ValueError("subtitle_too_large")
    return process.stdout


def items_from_container(root: ET.Element, recursive: bool = False) -> List[Dict[str, Any]]:
    nodes: Iterable[ET.Element]
    if recursive:
        nodes = root.iter()
    else:
        nodes = list(root)
    seen = set()
    items: List[Dict[str, Any]] = []
    for elem in nodes:
        if elem.tag not in {"Video", "Directory", "Track"}:
            continue
        rating_key = elem.get("ratingKey")
        if not rating_key or rating_key in seen:
            continue
        seen.add(rating_key)
        items.append(item_from_xml(elem))
    return items


class AppHandler(BaseHTTPRequestHandler):
    server_version = "PlexOpenWeb/0.1"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Methods", "GET,POST,HEAD,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type,Range")
        self.send_header("Access-Control-Allow-Credentials", "true")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:
        self.route("GET")

    def do_HEAD(self) -> None:
        self.route("HEAD")

    def do_POST(self) -> None:
        self.route("POST")

    def route(self, method: str) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)
        try:
            if path == "/api/health":
                self.handle_health(method)
            elif path == "/api/me":
                self.handle_me(method)
            elif path == "/api/login":
                self.handle_login(method)
            elif path == "/api/logout":
                self.handle_logout(method)
            elif path.startswith("/api/"):
                self.require_auth()
                self.handle_api(method, path, query)
            elif path == "/api/image":
                self.require_auth()
                self.handle_image(method, query)
            else:
                self.handle_static(method, path)
        except AuthError:
            self.send_json({"error": "authentication_required"}, status=401)
        except urllib.error.HTTPError as exc:
            message = exc.read(4096).decode("utf-8", errors="ignore") if method != "HEAD" else ""
            self.send_json({"error": "plex_http_error", "status": exc.code, "message": message}, status=502)
        except Exception as exc:
            traceback.print_exc()
            self.send_json({"error": "server_error", "message": str(exc)}, status=500)

    def is_authenticated(self) -> bool:
        if Settings.disable_auth:
            return True
        cookies = parse_cookies(self.headers.get("Cookie", ""))
        return verify_session_cookie(cookies.get(COOKIE_NAME, ""))

    def require_auth(self) -> None:
        if not self.is_authenticated():
            raise AuthError()

    def send_json(self, payload: Any, status: int = 200, headers: Optional[Dict[str, str]] = None) -> None:
        body = json_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def read_json(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return {}
        raw = self.rfile.read(min(length, 1024 * 1024))
        return json.loads(raw.decode("utf-8"))

    def handle_static(self, method: str, path: str) -> None:
        if method not in {"GET", "HEAD"}:
            self.send_json({"error": "method_not_allowed"}, status=405)
            return
        target = safe_static_path(path)
        if target is None:
            target = STATIC_DIR / "index.html"
        body = b"" if method == "HEAD" else target.read_bytes()
        mime, _ = mimetypes.guess_type(str(target))
        self.send_response(200)
        self.send_header("Content-Type", mime or "application/octet-stream")
        cache_control = "public, max-age=3600"
        if target.name == "index.html" or target.suffix in {".css", ".js"}:
            cache_control = "no-cache"
        self.send_header("Cache-Control", cache_control)
        self.send_header("Content-Length", str(target.stat().st_size))
        self.end_headers()
        if method != "HEAD":
            self.wfile.write(body)

    def handle_health(self, method: str) -> None:
        if method not in {"GET", "HEAD"}:
            self.send_json({"error": "method_not_allowed"}, status=405)
            return
        payload = {
            "ok": True,
            "authRequired": not Settings.disable_auth,
            "plexConfigured": bool(Settings.plex_token),
            "plexBaseUrl": Settings.plex_base_url,
            "opensubtitlesConfigured": bool(Settings.opensubtitles_api_key),
        }
        self.send_json(payload)

    def handle_me(self, method: str) -> None:
        if method not in {"GET", "HEAD"}:
            self.send_json({"error": "method_not_allowed"}, status=405)
            return
        self.send_json(
            {
                "authenticated": self.is_authenticated(),
                "authRequired": not Settings.disable_auth,
                "serverName": os.environ.get("APP_NAME", "Plex Open Web"),
            }
        )

    def handle_login(self, method: str) -> None:
        if method != "POST":
            self.send_json({"error": "method_not_allowed"}, status=405)
            return
        if Settings.disable_auth:
            self.send_json({"authenticated": True})
            return
        if not Settings.app_password:
            self.send_json({"error": "APP_PASSWORD is not configured"}, status=500)
            return
        payload = self.read_json()
        password = str(payload.get("password", "")).strip()
        if not hmac.compare_digest(password, Settings.app_password):
            self.send_json({"error": "invalid_password"}, status=403)
            return
        cookie = http.cookies.SimpleCookie()
        cookie[COOKIE_NAME] = make_session_cookie()
        cookie[COOKIE_NAME]["path"] = "/"
        cookie[COOKIE_NAME]["httponly"] = True
        cookie[COOKIE_NAME]["samesite"] = "Lax"
        cookie[COOKIE_NAME]["max-age"] = Settings.session_days * 86400
        if Settings.secure_cookie:
            cookie[COOKIE_NAME]["secure"] = True
        self.send_json({"authenticated": True}, headers={"Set-Cookie": cookie.output(header="").strip()})

    def handle_logout(self, method: str) -> None:
        if method != "POST":
            self.send_json({"error": "method_not_allowed"}, status=405)
            return
        cookie = http.cookies.SimpleCookie()
        cookie[COOKIE_NAME] = ""
        cookie[COOKIE_NAME]["path"] = "/"
        cookie[COOKIE_NAME]["max-age"] = 0
        self.send_json({"authenticated": False}, headers={"Set-Cookie": cookie.output(header="").strip()})

    def handle_api(self, method: str, path: str, query: Dict[str, List[str]]) -> None:
        if path == "/api/subtitle-download":
            self.api_subtitle_download(method)
            return
        if path == "/api/saved-playback":
            self.api_saved_playback(method, query)
            return
        if method not in {"GET", "HEAD"}:
            self.send_json({"error": "method_not_allowed"}, status=405)
            return
        if path == "/api/server":
            self.api_server()
        elif path == "/api/libraries":
            self.api_libraries()
        elif path.startswith("/api/library/"):
            self.api_library(path, query)
        elif path.startswith("/api/metadata/"):
            rating_key = path.rsplit("/", 1)[-1]
            self.api_metadata(rating_key)
        elif path.startswith("/api/children/"):
            rating_key = path.rsplit("/", 1)[-1]
            self.api_children(rating_key)
        elif path == "/api/search":
            self.api_search(query)
        elif path == "/api/image":
            self.handle_image(method, query)
        elif path == "/api/stream":
            self.handle_stream(method, query)
        elif path == "/api/stream-compatible":
            self.handle_stream_compatible(method, query)
        elif path == "/api/saved-stream":
            self.handle_saved_stream(method, query)
        elif path == "/api/subtitle":
            self.handle_subtitle(method, query)
        elif path == "/api/local-subtitle":
            self.handle_local_subtitle(method, query)
        elif path == "/api/embedded-subtitle":
            self.handle_embedded_subtitle(method, query)
        elif path == "/api/subtitle-search":
            self.api_subtitle_search(method, query)
        else:
            self.send_json({"error": "not_found"}, status=404)

    def api_server(self) -> None:
        root = PLEX.xml("/")
        self.send_json(
            {
                "friendlyName": root.get("friendlyName"),
                "machineIdentifier": root.get("machineIdentifier"),
                "platform": root.get("platform"),
                "version": root.get("version"),
                "updatedAt": root.get("updatedAt"),
            }
        )

    def api_libraries(self) -> None:
        root = PLEX.xml("/library/sections")
        sections = [library_from_xml(child) for child in root.findall("Directory")]
        self.send_json({"libraries": sections})

    def api_library(self, path: str, query: Dict[str, List[str]]) -> None:
        section_key = path[len("/api/library/") :].strip("/").split("/", 1)[0]
        if not section_key:
            self.send_json({"error": "missing_section"}, status=400)
            return
        view = one(query, "view", "all")
        start = max(0, to_int(one(query, "start", "0")) or 0)
        limit = min(300, max(1, to_int(one(query, "limit", "48")) or 48))
        sort = one(query, "sort", "")
        params: Dict[str, Any] = {
            "includeGuids": "1",
            "includeCollections": "1",
            "X-Plex-Container-Start": start,
            "X-Plex-Container-Size": limit,
        }
        endpoint = f"/library/sections/{urllib.parse.quote(section_key)}/all"
        if view == "recent":
            endpoint = f"/library/sections/{urllib.parse.quote(section_key)}/recentlyAdded"
        elif view == "unwatched":
            params["unwatched"] = "1"
        if sort:
            params["sort"] = sort
        root = PLEX.xml(endpoint, params=params)
        self.send_json(
            {
                "library": section_key,
                "view": view,
                "start": start,
                "limit": limit,
                "size": to_int(root.get("size")),
                "totalSize": to_int(root.get("totalSize")),
                "items": items_from_container(root),
            }
        )

    def api_metadata(self, rating_key: str) -> None:
        root = PLEX.xml(f"/library/metadata/{urllib.parse.quote(rating_key)}", params={"includeGuids": "1"})
        items = items_from_container(root)
        self.send_json({"item": items[0] if items else None})

    def api_children(self, rating_key: str) -> None:
        root = PLEX.xml(f"/library/metadata/{urllib.parse.quote(rating_key)}/children")
        self.send_json(
            {
                "parentTitle": root.get("parentTitle") or root.get("grandparentTitle"),
                "parentRatingKey": root.get("parentRatingKey"),
                "items": items_from_container(root),
            }
        )

    def api_search(self, query: Dict[str, List[str]]) -> None:
        search_query = one(query, "query", "").strip()
        if len(search_query) < 2:
            self.send_json({"items": []})
            return
        root = PLEX.xml("/search", params={"query": search_query, "includeCollections": "1"})
        items = items_from_container(root, recursive=True)
        self.send_json({"query": search_query, "items": items[:150]})

    def handle_image(self, method: str, query: Dict[str, List[str]]) -> None:
        if method not in {"GET", "HEAD"}:
            self.send_json({"error": "method_not_allowed"}, status=405)
            return
        self.require_auth()
        raw_path = one(query, "path", "")
        plex_path = safe_plex_path(raw_path)
        if not plex_path:
            self.send_json({"error": "bad_image_path"}, status=400)
            return
        with PLEX.open(plex_path, timeout=Settings.request_timeout) as response:
            data = b"" if method == "HEAD" else response.read()
            self.send_response(response.status)
            self.send_header("Content-Type", response.headers.get("Content-Type", "image/jpeg"))
            self.send_header("Cache-Control", "private, max-age=3600")
            self.send_header("Content-Length", response.headers.get("Content-Length", str(len(data))))
            self.end_headers()
            if method != "HEAD":
                self.wfile.write(data)

    def handle_stream(self, method: str, query: Dict[str, List[str]]) -> None:
        if method not in {"GET", "HEAD"}:
            self.send_json({"error": "method_not_allowed"}, status=405)
            return
        self.require_auth()
        part_key = one(query, "partKey", "")
        plex_path = safe_plex_path(part_key, prefix="/library/parts/")
        if not plex_path:
            self.send_json({"error": "bad_part_key"}, status=400)
            return
        headers = {}
        if self.headers.get("Range"):
            headers["Range"] = self.headers["Range"]
        with PLEX.open(
            plex_path,
            params={"download": "1"},
            headers=headers,
            timeout=Settings.stream_timeout,
        ) as response:
            self.send_response(response.status)
            for header in [
                "Content-Type",
                "Content-Length",
                "Content-Range",
                "Accept-Ranges",
                "Last-Modified",
                "ETag",
            ]:
                value = response.headers.get(header)
                if value:
                    self.send_header(header, value)
            self.send_header("Cache-Control", "private, max-age=0")
            self.end_headers()
            if method == "HEAD":
                return
            try:
                copy_stream(response, self.wfile)
            except (BrokenPipeError, ConnectionResetError):
                return

    def handle_stream_compatible(self, method: str, query: Dict[str, List[str]]) -> None:
        if method not in {"GET", "HEAD"}:
            self.send_json({"error": "method_not_allowed"}, status=405)
            return
        self.require_auth()
        part_key = one(query, "partKey", "")
        plex_path = safe_plex_path(part_key, prefix="/library/parts/")
        if not plex_path:
            self.send_json({"error": "bad_part_key"}, status=400)
            return
        if method == "HEAD":
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Cache-Control", "private, max-age=0")
            self.end_headers()
            return

        input_url = PLEX._url(plex_path, {"download": "1"})
        quality = one(query, "quality", "").strip().lower()
        remote_quality = quality in {"remote", "low", "480p"}
        command = [
            Settings.ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-fflags",
            "+genpts",
            "-i",
            input_url,
            "-map",
            "0:v:0",
            "-map",
            "0:a:0?",
            "-sn",
            "-dn",
        ]
        if remote_quality:
            command.extend(
                [
                    "-vf",
                    "scale=-2:480",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "veryfast",
                    "-profile:v",
                    "main",
                    "-b:v",
                    "900k",
                    "-maxrate",
                    "1100k",
                    "-bufsize",
                    "1800k",
                    "-g",
                    "48",
                    "-keyint_min",
                    "48",
                    "-sc_threshold",
                    "0",
                ]
            )
        else:
            command.extend(
                [
                    "-c:v",
                    "copy",
                ]
            )
        command.extend(
            [
                "-c:a",
                "aac",
                "-b:a",
                "96k" if remote_quality else "192k",
                "-ac",
                "2",
                "-movflags",
                "frag_keyframe+empty_moov+default_base_moof",
                "-f",
                "mp4",
                "pipe:1",
            ]
        )
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
        )
        assert process.stdout is not None
        try:
            first_chunk = read_stream_chunk(process.stdout, TRANSCODE_STARTUP_CHUNK_SIZE)
            if not first_chunk:
                process.wait(timeout=3)
                self.send_json({"error": "transcode_failed"}, status=502)
                return
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Cache-Control", "private, max-age=0")
            self.send_header("Accept-Ranges", "none")
            self.send_header("Transfer-Encoding", "chunked")
            self.send_header(
                "X-Playback-Mode",
                "remote-480p-aac" if remote_quality else "audio-transcode-aac",
            )
            self.end_headers()
            write_chunked(self.wfile, first_chunk)
            copy_chunked_stream(process.stdout, self.wfile)
            finish_chunked(self.wfile)
        except (BrokenPipeError, ConnectionResetError):
            return
        finally:
            if process.poll() is None:
                process.kill()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.terminate()

    def api_saved_playback(self, method: str, query: Dict[str, List[str]]) -> None:
        if method not in {"GET", "HEAD", "POST"}:
            self.send_json({"error": "method_not_allowed"}, status=405)
            return
        payload: Dict[str, Any] = {}
        if method == "POST":
            payload = self.read_json()
            rating_key = str(payload.get("ratingKey") or "").strip()
        else:
            rating_key = one(query, "ratingKey", "").strip()
        if not rating_key:
            self.send_json({"error": "missing_rating_key"}, status=400)
            return
        item = metadata_item_for_rating_key(rating_key)
        if not item:
            self.send_json({"error": "not_found"}, status=404)
            return

        action = str(payload.get("action") or "save").strip().lower() if method == "POST" else "status"
        if method == "POST" and action == "delete":
            status = delete_saved_playback(item)
            if status.get("deleteBlocked"):
                self.send_json({"error": "save_in_progress", "savedPlayback": status}, status=409)
                return
        elif method == "POST":
            status = start_saved_playback(item)
        else:
            status = saved_playback_status(item.get("ratingKey"), item.get("partKey"), item.get("media"))
        item["savedPlayback"] = status
        self.send_json(
            {
                "savedPlayback": status,
                "item": {
                    "ratingKey": item.get("ratingKey"),
                    "title": item.get("title"),
                    "partKey": item.get("partKey"),
                    "media": item.get("media"),
                },
            }
        )

    def handle_saved_stream(self, method: str, query: Dict[str, List[str]]) -> None:
        if method not in {"GET", "HEAD"}:
            self.send_json({"error": "method_not_allowed"}, status=405)
            return
        self.require_auth()
        playback_id = one(query, "id", "").strip().lower()
        if not re.fullmatch(r"[a-f0-9]{24}", playback_id):
            self.send_json({"error": "bad_saved_id"}, status=400)
            return
        final_path, _, _ = saved_playback_paths(playback_id)
        if not final_path.is_file():
            self.send_json({"error": "saved_playback_not_found"}, status=404)
            return
        size = final_path.stat().st_size
        range_header = self.headers.get("Range")
        selected_range = parse_range_header(range_header, size)
        if range_header and selected_range is None:
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{size}")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        start, end = selected_range or (0, size - 1)
        length = end - start + 1
        self.send_response(206 if selected_range else 200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "private, max-age=0")
        self.send_header("Content-Length", str(length))
        if selected_range:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if method == "HEAD":
            return
        try:
            with final_path.open("rb") as handle:
                handle.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = handle.read(min(STREAM_CHUNK_SIZE, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            return

    def handle_subtitle(self, method: str, query: Dict[str, List[str]]) -> None:
        if method not in {"GET", "HEAD"}:
            self.send_json({"error": "method_not_allowed"}, status=405)
            return
        self.require_auth()
        stream_key = one(query, "streamKey", "")
        codec = one(query, "codec", "srt").lower()
        plex_path = safe_plex_path(stream_key, prefix="/library/streams/")
        if not plex_path:
            self.send_json({"error": "bad_subtitle_key"}, status=400)
            return
        with PLEX.open(plex_path, timeout=Settings.request_timeout) as response:
            raw = b"" if method == "HEAD" else response.read(25 * 1024 * 1024)
        body = b"" if method == "HEAD" else subtitle_to_vtt(raw, codec)
        self.send_response(200)
        self.send_header("Content-Type", "text/vtt; charset=utf-8")
        self.send_header("Cache-Control", "private, max-age=3600")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if method != "HEAD":
            self.wfile.write(body)

    def handle_local_subtitle(self, method: str, query: Dict[str, List[str]]) -> None:
        if method not in {"GET", "HEAD"}:
            self.send_json({"error": "method_not_allowed"}, status=405)
            return
        self.require_auth()
        rating_key = one(query, "ratingKey", "")
        filename = one(query, "name", "")
        codec = one(query, "codec", Path(filename).suffix.lower().lstrip(".") or "srt").lower()
        _, subtitle_path = resolve_local_subtitle(rating_key, filename)
        if subtitle_path is None:
            self.send_json({"error": "bad_local_subtitle"}, status=400)
            return
        if subtitle_path.stat().st_size > 30 * 1024 * 1024:
            self.send_json({"error": "subtitle_too_large"}, status=413)
            return
        raw = b"" if method == "HEAD" else subtitle_path.read_bytes()
        body = b"" if method == "HEAD" else subtitle_to_vtt(raw, codec)
        self.send_response(200)
        self.send_header("Content-Type", "text/vtt; charset=utf-8")
        self.send_header("Cache-Control", "private, max-age=3600")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if method != "HEAD":
            self.wfile.write(body)

    def handle_embedded_subtitle(self, method: str, query: Dict[str, List[str]]) -> None:
        if method not in {"GET", "HEAD"}:
            self.send_json({"error": "method_not_allowed"}, status=405)
            return
        self.require_auth()
        rating_key = one(query, "ratingKey", "")
        part_id = one(query, "partId", "")
        stream_id = one(query, "streamId", "")
        stream_index = to_int(one(query, "streamIndex", ""))
        codec = one(query, "codec", "").lower()
        resolved = resolve_embedded_subtitle(rating_key, part_id, stream_id, stream_index, codec)
        if resolved is None:
            self.send_json({"error": "bad_embedded_subtitle"}, status=400)
            return
        media_path, actual_index, _ = resolved
        try:
            body = b"" if method == "HEAD" else extract_embedded_subtitle(media_path, actual_index)
        except FileNotFoundError:
            self.send_json({"error": "ffmpeg_unavailable"}, status=500)
            return
        except subprocess.TimeoutExpired:
            self.send_json({"error": "subtitle_extract_timeout"}, status=504)
            return
        except ValueError as exc:
            if str(exc) == "subtitle_too_large":
                self.send_json({"error": "subtitle_too_large"}, status=413)
                return
            raise
        except RuntimeError as exc:
            self.send_json({"error": "subtitle_extract_failed", "message": str(exc)[:500]}, status=502)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/vtt; charset=utf-8")
        self.send_header("Cache-Control", "private, max-age=3600")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if method != "HEAD":
            self.wfile.write(body)

    def api_subtitle_search(self, method: str, query: Dict[str, List[str]]) -> None:
        if method not in {"GET", "HEAD"}:
            self.send_json({"error": "method_not_allowed"}, status=405)
            return
        configured, message = opensubtitles_configured(require_login=False)
        if not configured:
            self.send_json({"configured": False, "results": [], "message": message})
            return
        rating_key = one(query, "ratingKey", "").strip()
        if not rating_key:
            self.send_json({"error": "missing_rating_key"}, status=400)
            return
        elem = metadata_item_element(rating_key)
        if elem is None:
            self.send_json({"error": "metadata_not_found"}, status=404)
            return
        language = opensubtitles_language_code(one(query, "language", "el"), default="el")
        query_text = one(query, "query", "").strip()
        params = subtitle_search_params(elem, query_text, language)
        try:
            data = opensubtitles_json("GET", "/subtitles", params=params)
        except OpenSubtitlesError as exc:
            self.send_json(
                {
                    "configured": True,
                    "error": "opensubtitles_error",
                    "message": str(exc),
                    "upstreamStatus": exc.upstream_status,
                },
                status=exc.status,
            )
            return
        results = [
            result
            for result in (normalize_opensubtitles_result(entry) for entry in data.get("data", []))
            if result is not None
        ]
        self.send_json({"configured": True, "query": params, "results": results[:30]})

    def api_subtitle_download(self, method: str) -> None:
        if method != "POST":
            self.send_json({"error": "method_not_allowed"}, status=405)
            return
        configured, message = opensubtitles_configured(require_login=True)
        if not configured:
            self.send_json({"configured": False, "message": message}, status=400)
            return
        payload = self.read_json()
        rating_key = str(payload.get("ratingKey", "")).strip()
        file_id = payload.get("fileId")
        language = opensubtitles_language_code(str(payload.get("language") or "el"), default="el")
        if not rating_key or not file_id:
            self.send_json({"error": "missing_subtitle_download_fields"}, status=400)
            return
        elem = metadata_item_element(rating_key)
        if elem is None:
            self.send_json({"error": "metadata_not_found"}, status=404)
            return
        media_path = first_part_file(elem)
        if media_path is None or not media_path.is_absolute():
            self.send_json({"error": "media_path_unavailable"}, status=400)
            return
        try:
            subtitle_data, ext, source_name, download_meta = download_opensubtitles_file(file_id)
            target = subtitle_save_path(media_path, language, file_id, ext)
            target.write_bytes(subtitle_data)
        except OpenSubtitlesError as exc:
            self.send_json(
                {
                    "configured": True,
                    "error": "opensubtitles_error",
                    "message": str(exc),
                    "upstreamStatus": exc.upstream_status,
                },
                status=exc.status,
            )
            return
        except OSError as exc:
            self.send_json({"error": "subtitle_save_failed", "message": str(exc)}, status=500)
            return
        subtitle = local_subtitle_from_path(rating_key, media_path, target, selected=True)
        self.send_json(
            {
                "ok": True,
                "configured": True,
                "savedName": target.name,
                "sourceName": source_name,
                "remainingDownloads": download_meta.get("remaining"),
                "subtitle": subtitle,
            }
        )


class AuthError(Exception):
    pass


def one(query: Dict[str, List[str]], key: str, default: str = "") -> str:
    values = query.get(key)
    if not values:
        return default
    return values[0]


def main() -> None:
    if not STATIC_DIR.exists():
        raise SystemExit(f"Missing static directory: {STATIC_DIR}")
    if not Settings.disable_auth and not Settings.app_password:
        print("Warning: APP_PASSWORD is not set. Login will fail until configured.", file=sys.stderr)
    if not Settings.plex_token:
        print("Warning: PLEX_TOKEN is not set and Preferences.xml was not readable.", file=sys.stderr)
    httpd = ThreadingHTTPServer((Settings.host, Settings.port), AppHandler)
    print(f"Plex Open Web listening on http://{Settings.host}:{Settings.port}", flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
