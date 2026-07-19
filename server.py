#!/usr/bin/env python3
"""Plex Open Web: a small authenticated web UI for a local Plex server.

This app uses the Plex API with your server token, then proxies metadata,
posters, and media parts through its own cookie-authenticated web server. It
does not bypass Plex DRM or account systems; it is intended for a server and
media library you control.
"""

from __future__ import annotations

import base64
import collections
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
import shutil
import stat
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
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple


ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"
APP_VERSION = "0.16.0"
COOKIE_NAME = "plex_open_session"
MY_LIST_MAX_ITEMS = 500
MY_LIST_LOCK = threading.Lock()
STREAM_CHUNK_SIZE = 64 * 1024
TRANSCODE_STARTUP_CHUNK_SIZE = 32 * 1024
HLS_SEGMENT_PATTERN = re.compile(r"segment-\d{5}\.ts")
MATCH_GUID_PATTERN = re.compile(r"^plex://(movie|show)/[A-Za-z0-9._-]{1,160}$")
MATCH_LANGUAGE_PATTERN = re.compile(r"^[a-z]{2}(?:-[A-Z]{2})?$")
JSON_COMPRESSION_MIN_BYTES = 1024
BROWSE_CACHE_CONTROL = "private, max-age=3, stale-if-error=86400"
POSTER_WIDTH = 480
POSTER_HEIGHT = 720
ART_WIDTH = 1280
ART_HEIGHT = 720
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
    hls_cache_ttl = env_int("HLS_CACHE_TTL", 4 * 60 * 60)
    hls_cache_max_bytes = env_int("HLS_CACHE_MAX_BYTES", 6 * 1024 * 1024 * 1024)
    hls_startup_timeout = env_int("HLS_STARTUP_TIMEOUT", 15)
    hls_transcode_timeout = env_int("HLS_TRANSCODE_TIMEOUT", 4 * 60 * 60)
    data_dir = Path(os.environ.get("APP_DATA_DIR", str(ROOT.parent / "plex-open-web-data")))
    media_delete_enabled = env_bool("MEDIA_DELETE_ENABLED", False)
    media_delete_roots = os.environ.get("MEDIA_DELETE_ROOTS", "")
    media_delete_plan_ttl = env_int("MEDIA_DELETE_PLAN_TTL", 300)
    qbittorrent_backup_dir = os.environ.get("QBITTORRENT_BACKUP_DIR", "")
    opensubtitles_base_url = os.environ.get(
        "OPENSUBTITLES_BASE_URL", "https://api.opensubtitles.com/api/v1"
    )
    opensubtitles_api_key = os.environ.get("OPENSUBTITLES_API_KEY", "")
    opensubtitles_username = os.environ.get("OPENSUBTITLES_USERNAME", "")
    opensubtitles_password = os.environ.get("OPENSUBTITLES_PASSWORD", "")
    opensubtitles_user_agent = os.environ.get(
        "OPENSUBTITLES_USER_AGENT", f"PlexOpenWeb v{APP_VERSION}"
    )


class TimedResultCache:
    """Small bounded cache that also coalesces concurrent identical work."""

    def __init__(self, max_entries: int = 128) -> None:
        self.max_entries = max_entries
        self.lock = threading.Lock()
        self.values: "collections.OrderedDict[str, Tuple[float, Any]]" = collections.OrderedDict()
        self.inflight: Dict[str, Dict[str, Any]] = {}
        self.generation = 0

    def get_or_load(self, key: str, ttl: float, loader: Callable[[], Any]) -> Any:
        with self.lock:
            now = time.monotonic()
            cached = self.values.get(key)
            if cached and cached[0] > now:
                self.values.move_to_end(key)
                return cached[1]
            if cached:
                self.values.pop(key, None)
            pending = self.inflight.get(key)
            owner = pending is None
            if owner:
                pending = {"event": threading.Event(), "generation": self.generation}
                self.inflight[key] = pending

        if not owner:
            pending["event"].wait(Settings.request_timeout + 5)
            if "error" in pending:
                raise pending["error"]
            if "result" in pending:
                return pending["result"]
            return self.get_or_load(key, ttl, loader)

        try:
            result = loader()
        except BaseException as exc:
            with self.lock:
                pending["error"] = exc
                self.inflight.pop(key, None)
                pending["event"].set()
            raise

        with self.lock:
            pending["result"] = result
            if pending["generation"] == self.generation:
                self.values[key] = (time.monotonic() + ttl, result)
                self.values.move_to_end(key)
                while len(self.values) > self.max_entries:
                    self.values.popitem(last=False)
            self.inflight.pop(key, None)
            pending["event"].set()
        return result

    def clear(self) -> None:
        with self.lock:
            self.generation += 1
            self.values.clear()


API_CACHE = TimedResultCache()
BROWSE_EXECUTOR = ThreadPoolExecutor(max_workers=8, thread_name_prefix="plex-browse")
STATIC_CACHE_LOCK = threading.Lock()
STATIC_CACHE: Dict[Path, Tuple[int, bytes]] = {}


MY_LIST_FILE = Settings.data_dir / "my-list.json"
MEDIA_DELETE_LOG_FILE = Settings.data_dir / "media-delete-log.jsonl"
MEDIA_DELETE_LOCK = threading.Lock()
VIDEO_EXTENSIONS = {
    ".3g2", ".3gp", ".asf", ".avi", ".flv", ".m2ts", ".m4v", ".mkv", ".mov",
    ".mp4", ".mpeg", ".mpg", ".mts", ".ogm", ".ogv", ".ts", ".vob", ".webm", ".wmv",
}
MEDIA_SIDECAR_EXTENSIONS = {
    ".ass", ".idx", ".jpeg", ".jpg", ".nfo", ".png", ".sfv", ".srt", ".ssa",
    ".sub", ".txt", ".vtt", ".webp",
}


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


PLEX_IMAGE_QUERY_KEYS = {"width", "height", "minSize", "upscale", "quality"}


def plex_image_request(path: str) -> Tuple[Optional[str], Dict[str, str]]:
    parsed = urllib.parse.urlsplit(path)
    plex_path = safe_plex_path(parsed.path)
    if not plex_path:
        return None, {}
    query = urllib.parse.parse_qs(parsed.query, keep_blank_values=False)
    params = {
        key: values[-1]
        for key, values in query.items()
        if key in PLEX_IMAGE_QUERY_KEYS and values
    }
    if plex_path == "/photo/:/transcode":
        source = query.get("url", [""])[-1]
        source_path = safe_plex_path(urllib.parse.urlsplit(source).path)
        if not source_path:
            return None, {}
        plex_path = source_path
    return plex_path, params


def plex_image_upstream_request(path: str) -> Tuple[Optional[str], Dict[str, str]]:
    source_path, params = plex_image_request(path)
    if not source_path:
        return None, {}
    if not params:
        return source_path, {}
    return "/photo/:/transcode", {"url": source_path, **params}


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


def format_bytes(value: int) -> str:
    amount = float(max(0, value))
    units = ["B", "KB", "MB", "GB", "TB"]
    unit = units[0]
    for candidate in units:
        unit = candidate
        if amount < 1024 or candidate == units[-1]:
            break
        amount /= 1024
    return f"{amount:.0f} {unit}" if unit == "B" else f"{amount:.1f} {unit}"


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
        method: Optional[str] = None,
    ):
        if not self.token:
            raise RuntimeError("PLEX_TOKEN is not configured and could not be read from Preferences.xml")
        request_headers = {
            "Accept": "application/xml, */*",
            "X-Plex-Product": "Plex Open Web",
            "X-Plex-Client-Identifier": "plex-open-web",
        }
        request_headers.update(headers or {})
        request = urllib.request.Request(self._url(path, params), headers=request_headers, method=method)
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
        "language": elem.get("language"),
        "updatedAt": to_int(elem.get("updatedAt")),
        "createdAt": to_int(elem.get("createdAt")),
    }


def cached_server_info() -> Dict[str, Any]:
    def load() -> Dict[str, Any]:
        root = PLEX.xml("/")
        return {
            "friendlyName": root.get("friendlyName"),
            "machineIdentifier": root.get("machineIdentifier"),
            "platform": root.get("platform"),
            "version": root.get("version"),
            "updatedAt": root.get("updatedAt"),
        }

    return API_CACHE.get_or_load(f"server:{id(PLEX)}", 30.0, load)


def cached_libraries() -> List[Dict[str, Any]]:
    def load() -> List[Dict[str, Any]]:
        root = PLEX.xml("/library/sections")
        return [library_from_xml(child) for child in root.findall("Directory")]

    return API_CACHE.get_or_load(f"libraries:{id(PLEX)}", 15.0, load)


def image_url(
    path: Optional[str],
    width: int = POSTER_WIDTH,
    height: int = POSTER_HEIGHT,
    quality: int = 88,
) -> Optional[str]:
    if not path:
        return None
    plex_path, params = plex_image_request(path)
    if not plex_path:
        return None
    params.setdefault("width", str(width))
    params.setdefault("height", str(height))
    params.setdefault("minSize", "1")
    params.setdefault("upscale", "0")
    params.setdefault("quality", str(quality))
    resized_path = plex_path
    if params:
        resized_path += "?" + urllib.parse.urlencode(params)
    return "/api/image?" + urllib.parse.urlencode({"path": resized_path})


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
HLS_JOBS: Dict[str, Dict[str, Any]] = {}
HLS_JOBS_LOCK = threading.Lock()


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


def compatible_stream_url(
    part_key: Optional[str],
    transcode_video: bool = False,
) -> Optional[str]:
    if not part_key:
        return None
    params = {"partKey": part_key}
    if transcode_video:
        params["video"] = "h264"
    return "/api/stream-compatible?" + urllib.parse.urlencode(params)


def original_download_url(rating_key: Optional[str]) -> Optional[str]:
    if not rating_key:
        return None
    return "/api/download-original?" + urllib.parse.urlencode({"ratingKey": rating_key})


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
    needs_video_transcode = bool(video_codec and video_codec not in BROWSER_VIDEO_CODECS)
    needs_compatible_stream = bool(needs_audio_transcode or needs_video_transcode)
    return {
        "audioCodec": audio_codec or None,
        "videoCodec": video_codec or None,
        "directStreamUrl": part_stream_url(part_key),
        "compatibleStreamUrl": (
            compatible_stream_url(part_key, needs_video_transcode)
            if needs_compatible_stream
            else part_stream_url(part_key)
        ),
        "compatibilityTranscodeRequired": needs_compatible_stream,
        "audioTranscodeRequired": needs_audio_transcode,
        "audioTranscodeReason": (
            f"{audio_codec.upper()} audio is not reliably supported by browser video playback"
            if needs_audio_transcode and audio_codec
            else None
        ),
        "videoTranscodeRequired": needs_video_transcode,
        "videoTranscodeReason": (
            f"{video_codec.upper()} video is not reliably supported by browser streaming"
            if needs_video_transcode
            else None
        ),
    }


def watched_threshold_reached(time_ms: Optional[int], duration_ms: Optional[int]) -> bool:
    if not time_ms or not duration_ms or duration_ms <= 0:
        return False
    remaining_ms = max(0, duration_ms - time_ms)
    return time_ms >= duration_ms * 0.9 or remaining_ms <= 120000


def update_plex_progress(rating_key: str, time_ms: int) -> None:
    PLEX.open(
        "/:/progress",
        params={
            "key": rating_key,
            "identifier": "com.plexapp.plugins.library",
            "time": max(60001, time_ms),
            "state": "stopped",
        },
        method="PUT",
        timeout=Settings.request_timeout,
    ).close()


def mark_plex_watched(rating_key: str) -> None:
    PLEX.open(
        "/:/scrobble",
        params={"identifier": "com.plexapp.plugins.library", "key": rating_key},
        timeout=Settings.request_timeout,
    ).close()


def mark_plex_unwatched(rating_key: str) -> None:
    PLEX.open(
        "/:/unscrobble",
        params={"identifier": "com.plexapp.plugins.library", "key": rating_key},
        timeout=Settings.request_timeout,
    ).close()


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
        stat = final_path.stat()
        return {
            "id": playback_id,
            "state": "ready",
            "ready": True,
            "streamUrl": saved_playback_stream_url(playback_id),
            "bytes": stat.st_size,
            "updatedAt": int(stat.st_mtime),
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


def compatible_video_command_args(
    remote_quality: bool = False,
    transcode_video: bool = False,
) -> List[str]:
    if remote_quality:
        return [
            "-vf",
            "scale=-2:480",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-profile:v",
            "main",
            "-pix_fmt",
            "yuv420p",
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
    if transcode_video:
        return [
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "23",
            "-profile:v",
            "high",
            "-pix_fmt",
            "yuv420p",
            "-g",
            "48",
            "-keyint_min",
            "48",
            "-sc_threshold",
            "0",
        ]
    return ["-c:v", "copy"]


def compatible_stream_command(
    part_key: str,
    remote_quality: bool = False,
    transcode_video: bool = False,
) -> List[str]:
    plex_path = safe_plex_path(part_key, prefix="/library/parts/")
    if not plex_path:
        raise ValueError("bad_part_key")
    input_url = PLEX._url(plex_path, {"download": "1"})
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
    command.extend(compatible_video_command_args(remote_quality, transcode_video))
    command.extend(
        [
            "-c:a",
            "aac",
            "-b:a",
            "96k" if remote_quality else "192k",
            "-ac",
            "2",
            "-movflags",
            "frag_keyframe+delay_moov+default_base_moof",
            "-f",
            "mp4",
            "pipe:1",
        ]
    )
    return command


def hls_stream_id(
    part_key: str,
    remote_quality: bool = False,
    transcode_video: bool = False,
) -> str:
    quality = "480p" if remote_quality else "original"
    video_mode = "h264" if remote_quality or transcode_video else "copy"
    cache_key = f"hls-v2:{quality}:{video_mode}:{part_key}"
    return hashlib.sha256(cache_key.encode("utf-8")).hexdigest()[:24]


def hls_cache_dir(create: bool = False) -> Path:
    path = saved_playback_dir(create=create) / "hls"
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def hls_session_dir(playback_id: str, create: bool = False) -> Path:
    path = hls_cache_dir(create=create) / playback_id
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def hls_stream_command(
    part_key: str,
    output_dir: Path,
    remote_quality: bool = False,
    transcode_video: bool = False,
) -> List[str]:
    plex_path = safe_plex_path(part_key, prefix="/library/parts/")
    if not plex_path:
        raise ValueError("bad_part_key")
    input_url = PLEX._url(plex_path, {"download": "1"})
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
    command.extend(compatible_video_command_args(remote_quality, transcode_video))
    command.extend(
        [
            "-c:a",
            "aac",
            "-b:a",
            "96k" if remote_quality else "192k",
            "-ac",
            "2",
            "-hls_time",
            "4",
            "-hls_list_size",
            "0",
            "-hls_playlist_type",
            "event",
            "-hls_segment_type",
            "mpegts",
            "-hls_flags",
            "independent_segments+temp_file",
            "-hls_segment_filename",
            str(output_dir / "segment-%05d.ts"),
            str(output_dir / "index.m3u8"),
        ]
    )
    return command


def hls_manifest_text(playback_id: str, raw_manifest: str) -> str:
    lines: List[str] = []
    has_start = "#EXT-X-START:" in raw_manifest
    for raw_line in raw_manifest.splitlines():
        line = raw_line.strip()
        if line and not line.startswith("#"):
            name = Path(line).name
            if not HLS_SEGMENT_PATTERN.fullmatch(name):
                raise ValueError("invalid_hls_segment")
            line = "/api/hls-segment?" + urllib.parse.urlencode(
                {"id": playback_id, "name": name}
            )
        lines.append(line)
        if line == "#EXTM3U" and not has_start:
            lines.append("#EXT-X-START:TIME-OFFSET=0,PRECISE=YES")
    return "\n".join(lines) + "\n"


def ready_hls_manifest(session_dir: Path) -> Optional[str]:
    manifest_path = session_dir / "index.m3u8"
    try:
        raw = manifest_path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError, UnicodeDecodeError):
        return None
    segment_names = [
        Path(line.strip()).name
        for line in raw.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if not segment_names or not all(
        HLS_SEGMENT_PATTERN.fullmatch(name) and (session_dir / name).is_file()
        for name in segment_names
    ):
        return None
    return raw


def hls_directory_size(path: Path) -> int:
    total = 0
    try:
        children = list(path.iterdir())
    except OSError:
        return 0
    for child in children:
        try:
            if child.is_file() and not child.is_symlink():
                total += child.stat().st_size
        except OSError:
            continue
    return total


def touch_hls_session(path: Path) -> None:
    try:
        os.utime(path, None)
    except OSError:
        pass


def prune_hls_cache(keep_ids: Optional[set] = None) -> None:
    root = hls_cache_dir()
    if not root.is_dir():
        return
    keep = set(keep_ids or set())
    with HLS_JOBS_LOCK:
        keep.update(
            playback_id
            for playback_id, job in HLS_JOBS.items()
            if job.get("state") == "generating"
        )
    now = time.time()
    sessions: List[Tuple[float, int, Path]] = []
    try:
        candidates = list(root.iterdir())
    except OSError:
        return
    for path in candidates:
        if not path.is_dir() or not re.fullmatch(r"[a-f0-9]{24}", path.name):
            continue
        try:
            modified = path.stat().st_mtime
        except OSError:
            continue
        if path.name not in keep and now - modified > max(60, Settings.hls_cache_ttl):
            shutil.rmtree(path, ignore_errors=True)
            with HLS_JOBS_LOCK:
                HLS_JOBS.pop(path.name, None)
            continue
        sessions.append((modified, hls_directory_size(path), path))

    total = sum(size for _, size, _ in sessions)
    maximum = max(256 * 1024 * 1024, Settings.hls_cache_max_bytes)
    for _, size, path in sorted(sessions):
        if total <= maximum:
            break
        if path.name in keep:
            continue
        shutil.rmtree(path, ignore_errors=True)
        total -= size
        with HLS_JOBS_LOCK:
            HLS_JOBS.pop(path.name, None)


def run_hls_job(
    playback_id: str,
    part_key: str,
    remote_quality: bool,
    transcode_video: bool,
) -> None:
    session_dir = hls_session_dir(playback_id, create=True)
    try:
        process = subprocess.run(
            hls_stream_command(part_key, session_dir, remote_quality, transcode_video),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            timeout=Settings.hls_transcode_timeout,
        )
        if process.returncode != 0:
            message = process.stderr.decode("utf-8", errors="ignore").strip()
            raise RuntimeError(message or "ffmpeg could not create the HLS stream")
        with HLS_JOBS_LOCK:
            HLS_JOBS[playback_id] = {"state": "ready", "message": None}
        touch_hls_session(session_dir)
        prune_hls_cache({playback_id})
    except Exception as exc:
        with HLS_JOBS_LOCK:
            HLS_JOBS[playback_id] = {"state": "error", "message": str(exc)[:500]}


def ensure_hls_stream(
    part_key: str,
    remote_quality: bool = False,
    transcode_video: bool = False,
) -> Tuple[str, Path, str]:
    plex_path = safe_plex_path(part_key, prefix="/library/parts/")
    if not plex_path:
        raise ValueError("bad_part_key")
    playback_id = hls_stream_id(plex_path, remote_quality, transcode_video)
    session_dir = hls_session_dir(playback_id)
    manifest = ready_hls_manifest(session_dir)
    if manifest and "#EXT-X-ENDLIST" in manifest:
        touch_hls_session(session_dir)
        return playback_id, session_dir, manifest

    start_job = False
    with HLS_JOBS_LOCK:
        job = HLS_JOBS.get(playback_id)
        if not job or job.get("state") not in {"generating", "ready"}:
            HLS_JOBS[playback_id] = {"state": "generating", "message": None}
            start_job = True
    if start_job:
        prune_hls_cache({playback_id})
        shutil.rmtree(session_dir, ignore_errors=True)
        session_dir.mkdir(parents=True, exist_ok=True)
        threading.Thread(
            target=run_hls_job,
            args=(playback_id, plex_path, remote_quality, transcode_video),
            daemon=True,
            name=f"plex-hls-{playback_id[:8]}",
        ).start()

    deadline = time.monotonic() + max(2, Settings.hls_startup_timeout)
    while time.monotonic() < deadline:
        manifest = ready_hls_manifest(session_dir)
        if manifest:
            touch_hls_session(session_dir)
            return playback_id, session_dir, manifest
        with HLS_JOBS_LOCK:
            job = dict(HLS_JOBS.get(playback_id) or {})
        if job.get("state") == "error":
            raise RuntimeError(job.get("message") or "HLS stream failed")
        time.sleep(0.05)
    raise TimeoutError("HLS stream did not start in time")


def metadata_item_for_rating_key(rating_key: str) -> Optional[Dict[str, Any]]:
    elem = metadata_item_element(rating_key)
    return item_from_xml(elem) if elem is not None else None


def normalize_match_language(value: Any) -> Optional[str]:
    raw = str(value or "").strip().replace("_", "-")
    if not raw:
        return None
    parts = raw.split("-", 1)
    normalized = parts[0].lower()
    if len(parts) == 2:
        normalized += f"-{parts[1].upper()}"
    if not MATCH_LANGUAGE_PATTERN.fullmatch(normalized):
        raise ValueError("invalid_language")
    return normalized


def normalize_match_year(value: Any) -> Optional[int]:
    raw = str(value or "").strip()
    if not raw:
        return None
    if not re.fullmatch(r"\d{4}", raw):
        raise ValueError("invalid_year")
    year = int(raw)
    if year < 1800 or year > 2200:
        raise ValueError("invalid_year")
    return year


def matching_library_for_item(item: Dict[str, Any]) -> Dict[str, Any]:
    section_id = str(item.get("librarySectionID") or "")
    return next(
        (library for library in cached_libraries() if str(library.get("key") or "") == section_id),
        {},
    )


def match_poster_url(value: Optional[str]) -> Optional[str]:
    raw = str(value or "").strip()
    if raw.startswith("/"):
        return image_url(raw)
    parsed = urllib.parse.urlsplit(raw)
    if parsed.scheme == "https" and parsed.hostname == "images.plex.tv":
        return raw
    return None


def match_candidate_from_xml(
    elem: ET.Element,
    expected_type: str,
    current_guid: Optional[str],
    rank: int,
) -> Optional[Dict[str, Any]]:
    guid = str(elem.get("guid") or "").strip()
    match = MATCH_GUID_PATTERN.fullmatch(guid)
    name = str(elem.get("name") or elem.get("title") or "").strip()
    candidate_type = str(elem.get("type") or expected_type).strip().lower()
    if not match or match.group(1) != expected_type or candidate_type != expected_type or not name:
        return None
    return {
        "guid": guid,
        "name": name[:300],
        "year": to_int(elem.get("year")),
        "summary": str(elem.get("summary") or "")[:3000] or None,
        "posterUrl": match_poster_url(elem.get("thumb")),
        "type": candidate_type,
        "rank": rank,
        "best": rank == 1,
        "current": guid == current_guid,
    }


def search_plex_matches(
    item: Dict[str, Any],
    title: str,
    year: Optional[int],
    language: Optional[str],
) -> Dict[str, Any]:
    rating_key = str(item.get("ratingKey") or "")
    item_type = str(item.get("type") or "")
    library = matching_library_for_item(item)
    selected_language = language or normalize_match_language(library.get("language"))
    params: Dict[str, Any] = {"manual": 1, "title": title}
    if year is not None:
        params["year"] = year
    if library.get("agent"):
        params["agent"] = library["agent"]
    if selected_language:
        params["language"] = selected_language
    root = PLEX.xml(
        f"/library/metadata/{urllib.parse.quote(rating_key)}/matches",
        params=params,
    )
    results: List[Dict[str, Any]] = []
    for elem in root.findall("SearchResult"):
        candidate = match_candidate_from_xml(
            elem,
            item_type,
            item.get("guid"),
            len(results) + 1,
        )
        if candidate is not None:
            results.append(candidate)
        if len(results) >= 25:
            break
    return {
        "ratingKey": rating_key,
        "type": item_type,
        "currentGuid": item.get("guid"),
        "title": title,
        "year": year,
        "language": selected_language,
        "agent": library.get("agent"),
        "results": results,
    }


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


def item_from_xml(
    elem: ET.Element,
    *,
    include_saved_playback: bool = True,
    include_guids: bool = True,
) -> Dict[str, Any]:
    part_key, media, subtitles = first_part(elem)
    title = elem.get("title") or elem.get("parentTitle") or elem.get("grandparentTitle") or "Untitled"
    item_type = elem.get("type") or elem.tag.lower()
    guids, external_ids = external_ids_from_xml(elem) if include_guids else ([], {})
    playback = playback_info(part_key, media)
    item = {
        "ratingKey": elem.get("ratingKey"),
        "key": elem.get("key"),
        "guid": elem.get("guid"),
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
        "viewOffset": to_int(elem.get("viewOffset")) or 0,
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
        "subtype": elem.get("subtype"),
        "smart": elem.get("smart") in {"1", "true", "True"},
        "collections": [
            {"id": child.get("id"), "tag": child.get("tag")}
            for child in elem.findall("Collection")
            if child.get("tag")
        ],
        "thumb": elem.get("thumb"),
        "art": elem.get("art"),
        "posterUrl": image_url(elem.get("thumb")),
        "artUrl": image_url(elem.get("art"), ART_WIDTH, ART_HEIGHT, 86),
        "partKey": part_key,
        "streamUrl": part_stream_url(part_key),
        "compatibleStreamUrl": playback["compatibleStreamUrl"],
        "downloadOriginalUrl": original_download_url(elem.get("ratingKey")) if part_key else None,
        "playback": playback,
        "savedPlayback": saved_playback_status(elem.get("ratingKey"), part_key, media)
        if include_saved_playback
        else {"state": "unknown", "ready": False},
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


def first_part_element(elem: ET.Element) -> Optional[ET.Element]:
    for media in elem.findall("Media"):
        part = media.find("Part")
        if part is not None:
            return part
    return None


class MediaDeletionError(Exception):
    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


def configured_media_delete_roots() -> List[Path]:
    raw = Settings.media_delete_roots
    values = raw if isinstance(raw, (list, tuple)) else str(raw or "").split(os.pathsep)
    roots: List[Path] = []
    seen = set()
    for value in values:
        if not str(value).strip():
            continue
        root = Path(str(value).strip()).expanduser().resolve()
        if root in seen or not root.is_dir():
            continue
        seen.add(root)
        roots.append(root)
    return sorted(roots, key=lambda path: len(path.parts), reverse=True)


def approved_media_root(path: Path, roots: List[Path]) -> Optional[Path]:
    for root in roots:
        try:
            path.relative_to(root)
            return root
        except ValueError:
            continue
    return None


def media_delete_display_path(path: Path, roots: List[Path]) -> str:
    root = approved_media_root(path, roots)
    if root is None:
        return path.name
    relative = path.relative_to(root)
    return f"{root.name}/{relative}" if str(relative) != "." else root.name


def media_part_paths(elem: ET.Element) -> List[Path]:
    paths: List[Path] = []
    seen = set()
    for media in elem.findall("Media"):
        for part in media.findall("Part"):
            value = str(part.get("file") or "").strip()
            if not value:
                continue
            path = Path(value)
            if path in seen:
                continue
            seen.add(path)
            paths.append(path)
    return paths


def media_sidecars(video_path: Path) -> List[Path]:
    matches: List[Path] = []
    prefix = video_path.stem.casefold() + "."
    try:
        candidates = list(video_path.parent.iterdir())
    except OSError:
        return matches
    for candidate in candidates:
        try:
            is_file = candidate.is_file() and not candidate.is_symlink()
        except OSError:
            continue
        if not is_file or candidate.suffix.casefold() not in MEDIA_SIDECAR_EXTENSIONS:
            continue
        name = candidate.name.casefold()
        if candidate.stem.casefold() == video_path.stem.casefold() or name.startswith(prefix):
            matches.append(candidate.resolve())
    return matches


def find_approved_hardlinks(paths: List[Path], roots: List[Path]) -> Tuple[List[Path], int]:
    inode_stats: Dict[Tuple[int, int], os.stat_result] = {}
    locations: Dict[Tuple[int, int], set] = {}
    for path in paths:
        info = path.stat()
        key = (info.st_dev, info.st_ino)
        inode_stats[key] = info
        locations.setdefault(key, set()).add(path)

    wanted = {key for key, info in inode_stats.items() if info.st_nlink > len(locations[key])}
    for device, inode in wanted:
        device_roots = []
        for root in roots:
            try:
                if root.stat().st_dev == device:
                    device_roots.append(root)
            except OSError:
                continue
        if not device_roots:
            continue
        command = [
            "find",
            *[str(root) for root in device_roots],
            "-xdev",
            "-type",
            "f",
            "-inum",
            str(inode),
            "-print0",
        ]
        try:
            result = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                timeout=max(5, min(60, Settings.request_timeout)),
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            result = None
        if result is not None:
            candidates = [Path(os.fsdecode(value)) for value in result.stdout.split(b"\0") if value]
        else:
            candidates = []
            for root in device_roots:
                for directory, dirnames, filenames in os.walk(root, followlinks=False):
                    dirnames[:] = [
                        name for name in dirnames if not (Path(directory) / name).is_symlink()
                    ]
                    candidates.extend(Path(directory) / filename for filename in filenames)
        for candidate in candidates:
            try:
                info = candidate.lstat()
            except OSError:
                continue
            if (
                info.st_dev == device
                and info.st_ino == inode
                and stat.S_ISREG(info.st_mode)
                and not candidate.is_symlink()
            ):
                locations[(device, inode)].add(candidate.resolve())

    missing = sum(max(0, inode_stats[key].st_nlink - len(found)) for key, found in locations.items())
    all_paths = sorted({path for found in locations.values() for path in found}, key=str)
    return all_paths, missing


def auxiliary_movie_video(path: Path) -> bool:
    return bool(re.search(r"(?:^|[ ._\-])(sample|trailer|featurette|extra)(?:[ ._\-]|$)", path.stem, re.I))


def dedicated_movie_folders(
    original_paths: List[Path],
    all_video_paths: List[Path],
    roots: List[Path],
) -> Tuple[List[Path], bool]:
    target_inodes = {(path.stat().st_dev, path.stat().st_ino) for path in all_video_paths}
    folders = set()
    shared_folder_found = False
    for path in original_paths:
        root = approved_media_root(path, roots)
        if root is None:
            continue
        relative = path.relative_to(root)
        if len(relative.parts) < 2:
            shared_folder_found = True
            continue
        candidate = root / relative.parts[0]
        if not candidate.is_dir() or candidate.is_symlink():
            shared_folder_found = True
            continue
        safe = True
        for directory, dirnames, filenames in os.walk(candidate, followlinks=False):
            dirnames[:] = [
                name for name in dirnames if not (Path(directory) / name).is_symlink()
            ]
            for filename in filenames:
                nested = Path(directory) / filename
                if nested.suffix.casefold() not in VIDEO_EXTENSIONS:
                    continue
                try:
                    info = nested.stat()
                except OSError:
                    safe = False
                    break
                if (info.st_dev, info.st_ino) not in target_inodes and not auxiliary_movie_video(nested):
                    safe = False
                    break
            if not safe:
                break
        if safe:
            folders.add(candidate.resolve())
        else:
            shared_folder_found = True
    return sorted(folders, key=str), shared_folder_found


def _bdecode(raw: bytes) -> Any:
    def parse(index: int) -> Tuple[Any, int]:
        marker = raw[index : index + 1]
        if marker == b"i":
            end = raw.index(b"e", index)
            return int(raw[index + 1 : end]), end + 1
        if marker == b"l":
            values = []
            index += 1
            while raw[index : index + 1] != b"e":
                value, index = parse(index)
                values.append(value)
            return values, index + 1
        if marker == b"d":
            values = {}
            index += 1
            while raw[index : index + 1] != b"e":
                key, index = parse(index)
                value, index = parse(index)
                values[key] = value
            return values, index + 1
        colon = raw.index(b":", index)
        length = int(raw[index:colon])
        start = colon + 1
        return raw[start : start + length], start + length

    value, end = parse(0)
    if end != len(raw):
        raise ValueError("trailing_bencode_data")
    return value


def _bvalue(mapping: Dict[bytes, Any], key: str, default: Any = None) -> Any:
    return mapping.get(key.encode("utf-8"), default)


def _btext(value: Any) -> str:
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else str(value or "")


def qbittorrent_records_for_paths(paths: List[Path]) -> List[Dict[str, Any]]:
    raw_dir = str(Settings.qbittorrent_backup_dir or "").strip()
    if not raw_dir:
        return []
    backup_dir = Path(raw_dir).expanduser()
    if not backup_dir.is_dir():
        return []
    targets = {path.resolve() for path in paths}
    records: List[Dict[str, Any]] = []
    for resume_path in sorted(backup_dir.glob("*.fastresume")):
        torrent_path = resume_path.with_suffix(".torrent")
        if not torrent_path.is_file():
            continue
        try:
            resume = _bdecode(resume_path.read_bytes())
            torrent = _bdecode(torrent_path.read_bytes())
            info = _bvalue(torrent, "info", {})
            if not isinstance(resume, dict) or not isinstance(info, dict):
                continue
            save_path = Path(
                _btext(_bvalue(resume, "qBt-savePath") or _bvalue(resume, "save_path"))
            ).expanduser()
            torrent_name = _btext(_bvalue(info, "name") or _bvalue(resume, "name"))
            files = _bvalue(info, "files")
            relative_paths: List[Path] = []
            if isinstance(files, list):
                for entry in files:
                    components = _bvalue(entry, "path", []) if isinstance(entry, dict) else []
                    if isinstance(components, list) and components:
                        relative_paths.append(Path(*[_btext(component) for component in components]))
            else:
                relative_paths.append(Path(torrent_name))
            candidates = set()
            for relative in relative_paths:
                candidates.add((save_path / relative).resolve())
                if torrent_name:
                    candidates.add((save_path / torrent_name / relative).resolve())
            matched = targets.intersection(candidates)
            if not matched:
                continue
            completed_time = int(_bvalue(resume, "completed_time", 0) or 0)
            records.append(
                {
                    "name": _btext(_bvalue(resume, "qBt-name") or _bvalue(resume, "name") or torrent_name),
                    "complete": completed_time > 0,
                    "containsOtherFiles": len(relative_paths) > len(matched),
                    "fileCount": len(relative_paths),
                }
            )
        except (OSError, ValueError, TypeError, IndexError):
            continue
    return records


def media_delete_fingerprint(files: List[Path], folders: List[Path]) -> Tuple[str, List[Path]]:
    entries: List[Tuple[Any, ...]] = []
    regular_files = set(files)
    for folder in folders:
        try:
            root_info = folder.lstat()
        except OSError as exc:
            raise MediaDeletionError("media_changed", f"Could not inspect {folder.name}: {exc}", 409)
        entries.append((str(folder), root_info.st_mode, root_info.st_size, root_info.st_mtime_ns, root_info.st_ino))
        for directory, dirnames, filenames in os.walk(folder, followlinks=False):
            dirnames.sort()
            filenames.sort()
            for name in dirnames + filenames:
                path = Path(directory) / name
                try:
                    info = path.lstat()
                except OSError as exc:
                    raise MediaDeletionError("media_changed", f"Could not inspect {path.name}: {exc}", 409)
                entries.append((str(path), info.st_mode, info.st_size, info.st_mtime_ns, info.st_ino))
                if stat.S_ISREG(info.st_mode):
                    regular_files.add(path)
    for path in files:
        try:
            info = path.lstat()
        except OSError as exc:
            raise MediaDeletionError("media_changed", f"Could not inspect {path.name}: {exc}", 409)
        entries.append((str(path), info.st_mode, info.st_size, info.st_mtime_ns, info.st_ino))
    digest = hashlib.sha256(json_bytes(sorted(entries, key=lambda entry: entry[0]))).hexdigest()
    return digest, sorted(regular_files, key=str)


def media_delete_title(elem: ET.Element) -> str:
    title = elem.get("title") or "Untitled"
    if elem.get("type") != "episode":
        return title
    season = to_int(elem.get("parentIndex"))
    episode = to_int(elem.get("index"))
    code = "".join(
        [f"S{season:02}" if season is not None else "", f"E{episode:02}" if episode is not None else ""]
    )
    show = elem.get("grandparentTitle") or elem.get("parentTitle")
    return " - ".join(part for part in [" ".join(part for part in [show, code] if part), title] if part)


def build_media_delete_plan(rating_key: str) -> Dict[str, Any]:
    if not Settings.media_delete_enabled:
        raise MediaDeletionError(
            "media_deletion_disabled",
            "Deleting original media is not enabled on this server.",
            403,
        )
    if not re.fullmatch(r"\d+", rating_key):
        raise MediaDeletionError("invalid_rating_key", "Invalid Plex item identifier.")
    roots = configured_media_delete_roots()
    if not roots:
        raise MediaDeletionError(
            "media_delete_roots_missing",
            "No approved media folders are configured for deletion.",
            503,
        )
    elem = metadata_item_element(rating_key)
    if elem is None:
        raise MediaDeletionError("metadata_not_found", "This Plex item no longer exists.", 404)
    item_type = str(elem.get("type") or "")
    if item_type not in {"movie", "episode"}:
        raise MediaDeletionError(
            "unsupported_media_type",
            "Only movies and individual TV episodes can be deleted.",
        )
    raw_paths = media_part_paths(elem)
    if not raw_paths:
        raise MediaDeletionError("media_file_unavailable", "Plex did not provide an original media file.", 404)

    original_paths: List[Path] = []
    for raw_path in raw_paths:
        if not raw_path.is_absolute() or raw_path.is_symlink():
            raise MediaDeletionError("unsafe_media_path", "The original media path is not safe to delete.", 409)
        try:
            path = raw_path.resolve(strict=True)
        except OSError:
            raise MediaDeletionError("media_file_missing", f"{raw_path.name} is already missing from disk.", 409)
        if approved_media_root(path, roots) is None:
            raise MediaDeletionError(
                "media_outside_approved_roots",
                f"{path.name} is outside the folders approved for deletion.",
                403,
            )
        info = path.stat()
        if not stat.S_ISREG(info.st_mode):
            raise MediaDeletionError("unsafe_media_path", f"{path.name} is not a regular media file.", 409)
        original_paths.append(path)

    all_video_paths, missing_links = find_approved_hardlinks(original_paths, roots)
    if missing_links:
        raise MediaDeletionError(
            "hardlinks_outside_approved_roots",
            "This media has linked copies outside the approved folders, so a complete deletion cannot be guaranteed.",
            409,
        )

    sidecars = sorted(
        {sidecar for video_path in all_video_paths for sidecar in media_sidecars(video_path)},
        key=str,
    )
    folders: List[Path] = []
    shared_folder_found = False
    if item_type == "movie":
        folders, shared_folder_found = dedicated_movie_folders(original_paths, all_video_paths, roots)

    def inside_deleted_folder(path: Path) -> bool:
        return approved_media_root(path, folders) is not None

    files = sorted(
        {path for path in all_video_paths + sidecars if not inside_deleted_folder(path)},
        key=str,
    )
    if not files and not folders:
        raise MediaDeletionError("nothing_to_delete", "No original media remains on disk.", 409)
    for path in files:
        if approved_media_root(path, roots) is None:
            raise MediaDeletionError("unsafe_media_path", f"{path.name} is outside approved folders.", 403)
    for folder in folders:
        if folder in roots or approved_media_root(folder, roots) is None:
            raise MediaDeletionError("unsafe_media_folder", "A library root can never be deleted.", 403)

    fingerprint, all_files = media_delete_fingerprint(files, folders)
    digest = hashlib.sha256(
        json_bytes(
            {
                "ratingKey": rating_key,
                "type": item_type,
                "sectionKey": elem.get("librarySectionID"),
                "files": [str(path) for path in files],
                "folders": [str(path) for path in folders],
                "fingerprint": fingerprint,
            }
        )
    ).hexdigest()
    unique_bytes = 0
    seen_inodes = set()
    for path in all_files:
        try:
            info = path.stat()
        except OSError:
            continue
        inode = (info.st_dev, info.st_ino)
        if inode not in seen_inodes:
            seen_inodes.add(inode)
            unique_bytes += info.st_size

    torrents = qbittorrent_records_for_paths(all_video_paths)
    incomplete = [torrent for torrent in torrents if not torrent["complete"]]
    unwritable = []
    for path in files:
        if not os.access(path.parent, os.W_OK | os.X_OK):
            unwritable.append(path.parent)
    for folder in folders:
        if not os.access(folder.parent, os.W_OK | os.X_OK):
            unwritable.append(folder.parent)
        for directory, _, _ in os.walk(folder, followlinks=False):
            directory_path = Path(directory)
            if not os.access(directory_path, os.W_OK | os.X_OK):
                unwritable.append(directory_path)
                break
    warnings: List[str] = []
    hardlink_copies = max(0, len(all_video_paths) - len(original_paths))
    if hardlink_copies:
        warnings.append(
            f"{hardlink_copies} linked disk cop{'y' if hardlink_copies == 1 else 'ies'} will also be removed."
        )
    if shared_folder_found:
        warnings.append(
            "The containing folder has other video files, so only this title and its matching sidecars will be removed."
        )
    if torrents:
        warnings.append(
            "qBittorrent still tracks this download and may restore missing files after a forced recheck."
        )
    if incomplete:
        warnings.append("The download is still active in qBittorrent. Wait for it to finish or stop it first.")
    if unwritable:
        warnings.append(
            "The server account cannot write one or more containing folders, so disk deletion is blocked."
        )
    block_reason = ""
    if incomplete:
        block_reason = "The download is still active in qBittorrent. Stop it or wait for it to finish."
    elif unwritable:
        block_reason = "The server account cannot write the containing media folder."

    return {
        "enabled": True,
        "canDelete": not incomplete and not unwritable,
        "blockReason": block_reason or None,
        "ratingKey": rating_key,
        "type": item_type,
        "title": media_delete_title(elem),
        "sectionKey": elem.get("librarySectionID"),
        "fileCount": len(all_files),
        "folderCount": len(folders),
        "totalBytes": unique_bytes,
        "totalSizeText": format_bytes(unique_bytes),
        "hardLinkCopies": hardlink_copies,
        "files": [media_delete_display_path(path, roots) for path in files],
        "folders": [media_delete_display_path(path, roots) for path in folders],
        "warnings": warnings,
        "torrents": torrents,
        "confirmationPhrase": "DELETE",
        "_files": files,
        "_folders": folders,
        "_roots": roots,
        "_digest": digest,
        "_element": elem,
    }


def make_media_delete_token(plan: Dict[str, Any]) -> str:
    now = int(time.time())
    payload = b64url(
        json_bytes(
            {
                "ratingKey": plan["ratingKey"],
                "digest": plan["_digest"],
                "iat": now,
                "exp": now + max(30, Settings.media_delete_plan_ttl),
            }
        )
    )
    signature = hmac.new(
        Settings.app_secret.encode("utf-8"),
        f"media-delete:{payload}".encode("ascii"),
        hashlib.sha256,
    ).digest()
    return f"{payload}.{b64url(signature)}"


def verify_media_delete_token(token: str) -> Dict[str, Any]:
    if not token or "." not in token:
        raise MediaDeletionError("confirmation_expired", "Open a fresh deletion confirmation.", 409)
    payload, signature = token.rsplit(".", 1)
    expected = hmac.new(
        Settings.app_secret.encode("utf-8"),
        f"media-delete:{payload}".encode("ascii"),
        hashlib.sha256,
    ).digest()
    try:
        actual = b64url_decode(signature)
        data = json.loads(b64url_decode(payload).decode("utf-8"))
    except (ValueError, json.JSONDecodeError):
        raise MediaDeletionError("confirmation_invalid", "The deletion confirmation is invalid.", 409)
    if not hmac.compare_digest(actual, expected):
        raise MediaDeletionError("confirmation_invalid", "The deletion confirmation is invalid.", 409)
    if int(data.get("exp") or 0) < int(time.time()):
        raise MediaDeletionError("confirmation_expired", "The deletion preview expired. Review it again.", 409)
    return data


def public_media_delete_plan(plan: Dict[str, Any]) -> Dict[str, Any]:
    return {
        key: value
        for key, value in plan.items()
        if not key.startswith("_")
    } | {"confirmationToken": make_media_delete_token(plan)}


def append_media_delete_audit(plan: Dict[str, Any], result: Dict[str, Any]) -> None:
    MEDIA_DELETE_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "deletedAt": int(time.time()),
        "ratingKey": plan["ratingKey"],
        "type": plan["type"],
        "title": plan["title"],
        "fileCount": result["deletedFileCount"],
        "folderCount": result["deletedFolderCount"],
        "totalBytes": plan["totalBytes"],
        "files": plan["files"],
        "folders": plan["folders"],
    }
    with MEDIA_DELETE_LOG_FILE.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")


def execute_media_delete(plan: Dict[str, Any]) -> Dict[str, Any]:
    item = item_from_xml(plan["_element"])
    saved_status = delete_saved_playback(item)
    if saved_status.get("deleteBlocked"):
        raise MediaDeletionError(
            "save_in_progress",
            "A server copy is still being prepared for this item. Stop it and try again.",
            409,
        )
    for path in plan["_files"]:
        if not os.access(path.parent, os.W_OK | os.X_OK):
            raise MediaDeletionError("media_not_writable", f"Cannot remove {path.name} from disk.", 403)
    for folder in plan["_folders"]:
        if not os.access(folder.parent, os.W_OK | os.X_OK):
            raise MediaDeletionError("media_not_writable", f"Cannot remove {folder.name} from disk.", 403)

    deleted_file_count = plan["fileCount"]
    deleted_folder_count = len(plan["_folders"])
    prune_candidates: Dict[Path, Path] = {}
    for path in plan["_files"]:
        root = approved_media_root(path, plan["_roots"])
        if root is not None:
            prune_candidates[path.parent] = root
        path.unlink()
    for folder in sorted(plan["_folders"], key=lambda path: len(path.parts), reverse=True):
        shutil.rmtree(folder)

    pruned = 0
    for directory, root in sorted(prune_candidates.items(), key=lambda entry: len(entry[0].parts), reverse=True):
        current = directory
        while current != root:
            try:
                current.rmdir()
                pruned += 1
            except OSError:
                break
            current = current.parent

    warnings = list(plan["warnings"])
    try:
        update_my_list(plan["ratingKey"], False)
    except OSError:
        warnings.append("The item was deleted, but My List could not be updated.")

    plex_removed = False
    try:
        PLEX.open(
            f"/library/metadata/{urllib.parse.quote(plan['ratingKey'])}",
            method="DELETE",
        ).close()
        plex_removed = True
    except (urllib.error.HTTPError, OSError, RuntimeError):
        warnings.append("Plex will remove its stale library entry during the requested scan.")

    scan_started = False
    section_key = str(plan.get("sectionKey") or "")
    if re.fullmatch(r"\d+", section_key):
        try:
            PLEX.open(f"/library/sections/{urllib.parse.quote(section_key)}/refresh").close()
            scan_started = True
        except (urllib.error.HTTPError, OSError, RuntimeError):
            warnings.append("The files are gone, but Plex could not start a library scan automatically.")

    result = {
        "ok": True,
        "ratingKey": plan["ratingKey"],
        "type": plan["type"],
        "title": plan["title"],
        "deletedFileCount": deleted_file_count,
        "deletedFolderCount": deleted_folder_count,
        "prunedDirectoryCount": pruned,
        "deletedBytes": plan["totalBytes"],
        "deletedSizeText": plan["totalSizeText"],
        "plexRemoved": plex_removed,
        "scanStarted": scan_started,
        "warnings": warnings,
    }
    try:
        append_media_delete_audit(plan, result)
    except OSError:
        result["warnings"].append("The deletion completed, but its local audit entry could not be written.")
    API_CACHE.clear()
    return result


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


def safe_download_filename(value: Optional[str], fallback: str = "download") -> str:
    cleaned = re.sub(r"[\x00-\x1f/\\:*?\"<>|]+", " ", str(value or "")).strip(" .")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned[:180] or fallback


def content_disposition_attachment(filename: str) -> str:
    ascii_name = filename.encode("ascii", errors="ignore").decode("ascii") or "download"
    ascii_name = safe_download_filename(ascii_name, "download")
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{urllib.parse.quote(filename)}"


def subtitle_download_extension(codec: Optional[str]) -> str:
    normalized = (codec or "srt").strip().lower()
    if normalized in {"subrip", "srt"}:
        return "srt"
    if normalized in {"webvtt", "vtt"}:
        return "vtt"
    return normalized if normalized in LOCAL_SUBTITLE_EXTENSIONS else "srt"


def subtitle_download_label(subtitle: Dict[str, Any], index: int) -> str:
    parts = [
        subtitle.get("srclang") or subtitle.get("languageCode") or "und",
        subtitle.get("source") or "subtitle",
    ]
    if subtitle.get("forced"):
        parts.append("forced")
    if subtitle.get("hearingImpaired"):
        parts.append("sdh")
    parts.append(str(index + 1))
    return ".".join(safe_download_filename(str(part).lower(), "subtitle") for part in parts if part)


def unique_zip_name(name: str, used: set) -> str:
    candidate = name
    stem = str(Path(name).with_suffix(""))
    suffix = Path(name).suffix
    counter = 2
    while candidate in used:
        candidate = f"{stem}-{counter}{suffix}"
        counter += 1
    used.add(candidate)
    return candidate


def subtitle_download_entries(
    rating_key: str,
    media_path: Path,
    subtitles: List[Dict[str, Any]],
) -> List[Tuple[str, bytes]]:
    entries: List[Tuple[str, bytes]] = []
    used = {media_path.name}
    for index, subtitle in enumerate(subtitles):
        if not subtitle.get("supported"):
            continue
        try:
            source = subtitle.get("source")
            codec = subtitle_download_extension(subtitle.get("codec"))
            if source in {"local", "opensubtitles"} or str(subtitle.get("id") or "").startswith("local:"):
                _, subtitle_path = resolve_local_subtitle(rating_key, str(subtitle.get("key") or ""))
                if subtitle_path is None or subtitle_path.stat().st_size > 30 * 1024 * 1024:
                    continue
                name = subtitle_path.name
                data = subtitle_path.read_bytes()
            elif source == "embedded":
                resolved = resolve_embedded_subtitle(
                    rating_key,
                    str(subtitle.get("partId") or ""),
                    str(subtitle.get("streamId") or ""),
                    to_int(str(subtitle.get("streamIndex") if subtitle.get("streamIndex") is not None else "")),
                    str(subtitle.get("codec") or ""),
                )
                if resolved is None:
                    continue
                resolved_media_path, stream_index, _ = resolved
                if resolved_media_path != media_path:
                    continue
                codec = "vtt"
                data = extract_embedded_subtitle(media_path, stream_index)
                name = f"{media_path.stem}.{subtitle_download_label(subtitle, index)}.{codec}"
            else:
                stream_key = str(subtitle.get("key") or "")
                plex_path = safe_plex_path(stream_key, prefix="/library/streams/")
                if not plex_path:
                    continue
                with PLEX.open(plex_path, timeout=Settings.request_timeout) as response:
                    data = response.read(30 * 1024 * 1024 + 1)
                if len(data) > 30 * 1024 * 1024:
                    continue
                name = f"{media_path.stem}.{subtitle_download_label(subtitle, index)}.{codec}"
            safe_name = safe_download_filename(name, f"subtitle-{index + 1}.{codec}")
            entries.append((unique_zip_name(safe_name, used), data))
        except Exception:
            traceback.print_exc()
            continue
    return entries


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


def items_from_container(
    root: ET.Element,
    recursive: bool = False,
    detailed: bool = False,
) -> List[Dict[str, Any]]:
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
        items.append(
            item_from_xml(
                elem,
                include_saved_playback=detailed,
                include_guids=detailed,
            )
        )
    return items


def _read_my_list_keys() -> List[str]:
    try:
        payload = json.loads(MY_LIST_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return []
    values = payload.get("ratingKeys", []) if isinstance(payload, dict) else payload
    if not isinstance(values, list):
        return []
    keys: List[str] = []
    seen = set()
    for value in values:
        key = str(value).strip()
        if not re.fullmatch(r"\d+", key) or key in seen:
            continue
        seen.add(key)
        keys.append(key)
        if len(keys) >= MY_LIST_MAX_ITEMS:
            break
    return keys


def my_list_keys() -> List[str]:
    with MY_LIST_LOCK:
        return _read_my_list_keys()


def update_my_list(rating_key: str, saved: bool) -> List[str]:
    with MY_LIST_LOCK:
        keys = [key for key in _read_my_list_keys() if key != rating_key]
        if saved:
            keys.insert(0, rating_key)
        keys = keys[:MY_LIST_MAX_ITEMS]
        MY_LIST_FILE.parent.mkdir(parents=True, exist_ok=True)
        temporary = MY_LIST_FILE.with_suffix(".tmp")
        temporary.write_text(
            json.dumps({"ratingKeys": keys, "updatedAt": int(time.time())}, separators=(",", ":")),
            encoding="utf-8",
        )
        os.replace(temporary, MY_LIST_FILE)
        return keys


def metadata_items_for_rating_keys(rating_keys: List[str]) -> List[Dict[str, Any]]:
    found: Dict[str, Dict[str, Any]] = {}
    for start in range(0, len(rating_keys), 50):
        chunk = rating_keys[start : start + 50]
        if not chunk:
            continue
        joined = urllib.parse.quote(",".join(chunk), safe=",")
        root = PLEX.xml(f"/library/metadata/{joined}", params={"includeGuids": "1"})
        for item in items_from_container(root):
            rating_key = str(item.get("ratingKey") or "")
            if rating_key:
                found[rating_key] = item
    return [found[key] for key in rating_keys if key in found]


def episode_neighbors(rating_key: str) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]], int, int]:
    current = metadata_item_element(rating_key)
    if current is None:
        raise LookupError("metadata_not_found")
    if current.get("type") != "episode":
        raise ValueError("unsupported_media_type")
    show_key = str(current.get("grandparentRatingKey") or "").strip()
    if not re.fullmatch(r"\d+", show_key):
        raise LookupError("show_not_found")
    root = PLEX.xml(
        f"/library/metadata/{urllib.parse.quote(show_key)}/allLeaves",
        params={"includeGuids": "1"},
    )
    episodes = [item for item in items_from_container(root) if item.get("type") == "episode"]
    position = next(
        (index for index, item in enumerate(episodes) if str(item.get("ratingKey") or "") == rating_key),
        -1,
    )
    if position < 0:
        raise LookupError("episode_not_in_show")
    previous_item = episodes[position - 1] if position > 0 else None
    next_item = episodes[position + 1] if position + 1 < len(episodes) else None
    return previous_item, next_item, position, len(episodes)


def my_list_items(section_key: str = "") -> Tuple[List[str], List[Dict[str, Any]]]:
    keys = my_list_keys()
    items = metadata_items_for_rating_keys(keys)
    if section_key:
        items = [item for item in items if str(item.get("librarySectionID") or "") == section_key]
    key_set = set(keys)
    for item in items:
        item["inMyList"] = str(item.get("ratingKey") or "") in key_set
    return keys, items


def library_genres_for_section(section_key: str) -> List[Dict[str, Any]]:
    def load() -> List[Dict[str, Any]]:
        root = PLEX.xml(f"/library/sections/{urllib.parse.quote(section_key)}/genre")
        genres = [
            {"key": item.get("key"), "title": item.get("title")}
            for item in root.findall("Directory")
            if item.get("key") and item.get("title")
        ]
        genres.sort(key=lambda item: item["title"].casefold())
        return genres

    return API_CACHE.get_or_load(f"genres:{id(PLEX)}:{section_key}", 300.0, load)


def library_page_for_section(section_key: str, query: Dict[str, List[str]]) -> Dict[str, Any]:
    view = one(query, "view", "all")
    start = max(0, to_int(one(query, "start", "0")) or 0)
    limit = min(300, max(1, to_int(one(query, "limit", "48")) or 48))
    sort = one(query, "sort", "")
    genre = one(query, "genre", "").strip()
    if genre and not re.fullmatch(r"\d+", genre):
        raise ValueError("invalid_genre")
    if view == "mylist":
        keys, items = my_list_items(section_key)
        total = len(items)
        page = items[start : start + limit]
        return {
            "library": section_key,
            "view": view,
            "ratingKeys": keys,
            "genre": None,
            "start": start,
            "limit": limit,
            "size": len(page),
            "totalSize": total,
            "items": page,
        }

    params: Dict[str, Any] = {
        "includeCollections": "1",
        "X-Plex-Container-Start": start,
        "X-Plex-Container-Size": limit,
    }
    endpoint = f"/library/sections/{urllib.parse.quote(section_key)}/all"
    if view == "continue":
        endpoint = f"/library/sections/{urllib.parse.quote(section_key)}/onDeck"
        start = 0
        limit = 300
        params["X-Plex-Container-Start"] = start
        params["X-Plex-Container-Size"] = limit
    elif view == "recent":
        endpoint = f"/library/sections/{urllib.parse.quote(section_key)}/recentlyAdded"
    elif view == "unwatched":
        params["unwatched"] = "1"
    elif view == "collections":
        endpoint = f"/library/sections/{urllib.parse.quote(section_key)}/collections"
        params["sort"] = "titleSort"
    active_genre = genre if view != "collections" else ""
    if active_genre:
        params["genre"] = active_genre
    if sort and view not in {"continue", "collections"}:
        params["sort"] = sort
    cache_key = ":".join(
        ["library", str(id(PLEX)), section_key, view, sort, active_genre, str(start), str(limit)]
    )

    def load() -> Dict[str, Any]:
        root = PLEX.xml(endpoint, params=params)
        items = items_from_container(root)
        if view == "continue":
            items = [item for item in items if not item.get("viewCount")]
        return {
            "library": section_key,
            "view": view,
            "genre": active_genre or None,
            "start": start,
            "limit": limit,
            "size": len(items) if view == "continue" else to_int(root.get("size")),
            "totalSize": len(items) if view == "continue" else to_int(root.get("totalSize")),
            "items": items,
        }

    return API_CACHE.get_or_load(cache_key, 3.0, load)


def browse_bundle(section_key: str, query: Dict[str, List[str]]) -> Dict[str, Any]:
    genres_future = BROWSE_EXECUTOR.submit(library_genres_for_section, section_key)
    page_future = BROWSE_EXECUTOR.submit(library_page_for_section, section_key, query)
    return {
        "library": section_key,
        "genres": genres_future.result(),
        "page": page_future.result(),
    }


class AppHandler(BaseHTTPRequestHandler):
    server_version = f"PlexOpenWeb/{APP_VERSION}"
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
            elif path == "/api/bootstrap":
                self.api_bootstrap(method, query)
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
        except (BrokenPipeError, ConnectionResetError):
            return
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

    def send_json(
        self,
        payload: Any,
        status: int = 200,
        headers: Optional[Dict[str, str]] = None,
        cache_control: str = "no-store",
    ) -> None:
        body = json_bytes(payload)
        compressed = False
        accept_encoding = self.headers.get("Accept-Encoding", "") if hasattr(self, "headers") else ""
        if len(body) >= JSON_COMPRESSION_MIN_BYTES and "gzip" in accept_encoding.lower():
            body = gzip.compress(body, compresslevel=4)
            compressed = True
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", cache_control)
        self.send_header("X-Content-Type-Options", "nosniff")
        if compressed:
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Vary", "Accept-Encoding")
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
        stamp = target.stat().st_mtime_ns
        with STATIC_CACHE_LOCK:
            cached = STATIC_CACHE.get(target)
            if cached and cached[0] == stamp:
                body = cached[1]
            else:
                body = target.read_bytes()
                STATIC_CACHE[target] = (stamp, body)
        if target.name == "index.html":
            body = body.replace(b"__APP_VERSION__", APP_VERSION.encode("utf-8"))
        mime, _ = mimetypes.guess_type(str(target))
        self.send_response(200)
        self.send_header("Content-Type", mime or "application/octet-stream")
        cache_control = "public, max-age=3600"
        version = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get("v", [""])[0]
        if target.name == "index.html":
            cache_control = "no-cache"
        elif target.suffix in {".css", ".js"}:
            cache_control = (
                "public, max-age=31536000, immutable" if version == APP_VERSION else "no-cache"
            )
        self.send_header("Cache-Control", cache_control)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if method != "HEAD":
            self.wfile.write(body)

    def handle_health(self, method: str) -> None:
        if method not in {"GET", "HEAD"}:
            self.send_json({"error": "method_not_allowed"}, status=405)
            return
        payload = {
            "ok": True,
            "version": APP_VERSION,
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
                "version": APP_VERSION,
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
        if method == "POST":
            API_CACHE.clear()
        if path == "/api/my-list":
            self.api_my_list(method, query)
            return
        if path == "/api/collection-membership":
            self.api_collection_membership(method, query)
            return
        if path == "/api/collection-management":
            self.api_collection_management(method)
            return
        if path == "/api/library-scan":
            self.api_library_scan(method)
            return
        if path == "/api/media-delete":
            self.api_media_delete(method, query)
            return
        if path == "/api/media-match":
            self.api_media_match(method, query)
            return
        if path == "/api/watch-state":
            self.api_watch_state(method)
            return
        if path == "/api/subtitle-download":
            self.api_subtitle_download(method)
            return
        if path == "/api/saved-playback":
            self.api_saved_playback(method, query)
            return
        if path == "/api/playback-progress":
            self.api_playback_progress(method)
            return
        if method not in {"GET", "HEAD"}:
            self.send_json({"error": "method_not_allowed"}, status=405)
            return
        if path == "/api/server":
            self.api_server()
        elif path == "/api/libraries":
            self.api_libraries()
        elif path == "/api/random-item":
            self.api_random_item(query)
        elif path == "/api/episode-neighbors":
            self.api_episode_neighbors(query)
        elif path.startswith("/api/library/") and path.endswith("/genres"):
            self.api_library_genres(path)
        elif path.startswith("/api/browse/"):
            self.api_browse(path, query)
        elif path.startswith("/api/library/"):
            self.api_library(path, query)
        elif path.startswith("/api/metadata/"):
            rating_key = path.rsplit("/", 1)[-1]
            self.api_metadata(rating_key, query)
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
        elif path == "/api/hls-segment":
            self.handle_hls_segment(method, query)
        elif path == "/api/saved-stream":
            self.handle_saved_stream(method, query)
        elif path == "/api/download-original":
            self.handle_original_download(method, query)
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
        self.send_json(cached_server_info())

    def api_bootstrap(self, method: str, query: Dict[str, List[str]]) -> None:
        if method not in {"GET", "HEAD"}:
            self.send_json({"error": "method_not_allowed"}, status=405)
            return
        authenticated = self.is_authenticated()
        payload: Dict[str, Any] = {
            "authenticated": authenticated,
            "authRequired": not Settings.disable_auth,
            "version": APP_VERSION,
            "mediaDeletionEnabled": Settings.media_delete_enabled,
        }
        if not authenticated:
            self.send_json(payload)
            return

        libraries = cached_libraries()
        requested_key = one(query, "libraryKey", "").strip()
        selected = next(
            (library for library in libraries if str(library.get("key") or "") == requested_key),
            libraries[0] if libraries else None,
        )
        payload.update(
            {
                "server": cached_server_info(),
                "libraries": libraries,
                "ratingKeys": my_list_keys(),
                "selectedLibraryKey": selected.get("key") if selected else None,
            }
        )
        include_browse = one(query, "includeBrowse", "").strip().lower() in {"1", "true", "yes", "on"}
        if include_browse and selected and re.fullmatch(r"\d+", str(selected.get("key") or "")):
            try:
                payload["browse"] = browse_bundle(str(selected["key"]), query)
            except ValueError as exc:
                self.send_json({"error": str(exc)}, status=400)
                return
        self.send_json(payload)

    def api_libraries(self) -> None:
        self.send_json({"libraries": cached_libraries()})

    def api_library_genres(self, path: str) -> None:
        section_key = path[len("/api/library/") : -len("/genres")].strip("/")
        if not re.fullmatch(r"\d+", section_key):
            self.send_json({"error": "invalid_section"}, status=400)
            return
        self.send_json(
            {"library": section_key, "genres": library_genres_for_section(section_key)},
            cache_control=BROWSE_CACHE_CONTROL,
        )

    def api_browse(self, path: str, query: Dict[str, List[str]]) -> None:
        section_key = path[len("/api/browse/") :].strip("/").split("/", 1)[0]
        if not re.fullmatch(r"\d+", section_key):
            self.send_json({"error": "invalid_section"}, status=400)
            return
        try:
            payload = browse_bundle(section_key, query)
        except ValueError as exc:
            self.send_json({"error": str(exc)}, status=400)
            return
        self.send_json(payload, cache_control=BROWSE_CACHE_CONTROL)

    def api_random_item(self, query: Dict[str, List[str]]) -> None:
        section_key = one(query, "sectionKey", "").strip()
        if not re.fullmatch(r"\d+", section_key):
            self.send_json({"error": "invalid_section"}, status=400)
            return
        genre = one(query, "genre", "").strip()
        if genre and not re.fullmatch(r"\d+", genre):
            self.send_json({"error": "invalid_genre"}, status=400)
            return
        unwatched_value = one(query, "unwatched", "").strip().lower()
        if unwatched_value not in {"", "0", "1", "false", "true"}:
            self.send_json({"error": "invalid_unwatched"}, status=400)
            return
        unwatched = unwatched_value in {"1", "true"}
        sections = PLEX.xml("/library/sections")
        section = next(
            (item for item in sections.findall("Directory") if item.get("key") == section_key),
            None,
        )
        if section is None:
            self.send_json({"error": "library_not_found"}, status=404)
            return

        endpoint = f"/library/sections/{urllib.parse.quote(section_key)}/all"
        params: Dict[str, Any] = {
            "includeCollections": "1",
            "X-Plex-Container-Start": 0,
            "X-Plex-Container-Size": 1,
        }
        if genre:
            params["genre"] = genre
        if unwatched:
            params["unwatched"] = "1"
        first = PLEX.xml(endpoint, params=params)
        total = to_int(first.get("totalSize")) or to_int(first.get("size")) or 0
        if total <= 0:
            self.send_json(
                {
                    "library": section_key,
                    "totalSize": 0,
                    "genre": genre or None,
                    "unwatched": unwatched,
                    "item": None,
                }
            )
            return

        offset = secrets.randbelow(total)
        root = first
        if offset:
            params["X-Plex-Container-Start"] = offset
            root = PLEX.xml(endpoint, params=params)
        items = items_from_container(root)
        self.send_json(
            {
                "library": section_key,
                "totalSize": total,
                "offset": offset,
                "genre": genre or None,
                "unwatched": unwatched,
                "item": items[0] if items else None,
            }
        )

    def api_library_scan(self, method: str) -> None:
        if method != "POST":
            self.send_json({"error": "method_not_allowed"}, status=405)
            return
        payload = self.read_json()
        section_key = str(payload.get("sectionKey") or "").strip()
        if not re.fullmatch(r"\d+", section_key):
            self.send_json({"error": "invalid_section"}, status=400)
            return
        sections = PLEX.xml("/library/sections")
        section = next(
            (item for item in sections.findall("Directory") if item.get("key") == section_key),
            None,
        )
        if section is None:
            self.send_json({"error": "library_not_found"}, status=404)
            return
        endpoint = f"/library/sections/{urllib.parse.quote(section_key)}/refresh"
        with PLEX.open(endpoint) as response:
            plex_status = response.getcode()
        self.send_json(
            {
                "ok": True,
                "scanStarted": True,
                "sectionKey": section_key,
                "libraryTitle": section.get("title"),
                "plexStatus": plex_status,
            }
        )

    def api_media_delete(self, method: str, query: Dict[str, List[str]]) -> None:
        if method not in {"GET", "HEAD", "POST"}:
            self.send_json({"error": "method_not_allowed"}, status=405)
            return
        payload: Dict[str, Any] = self.read_json() if method == "POST" else {}
        rating_key = (
            str(payload.get("ratingKey") or "").strip()
            if method == "POST"
            else one(query, "ratingKey", "").strip()
        )
        try:
            if method != "POST":
                plan = build_media_delete_plan(rating_key)
                self.send_json(public_media_delete_plan(plan))
                return
            if payload.get("confirmation") != "DELETE":
                raise MediaDeletionError(
                    "confirmation_required",
                    "Type DELETE exactly to confirm permanent deletion.",
                )
            token_data = verify_media_delete_token(str(payload.get("confirmationToken") or ""))
            if rating_key != str(token_data.get("ratingKey") or ""):
                raise MediaDeletionError(
                    "confirmation_invalid",
                    "The confirmation does not match this Plex item.",
                    409,
                )
            with MEDIA_DELETE_LOCK:
                plan = build_media_delete_plan(rating_key)
                if plan["_digest"] != token_data.get("digest"):
                    raise MediaDeletionError(
                        "deletion_plan_changed",
                        "The files changed after the preview. Review the deletion again.",
                        409,
                    )
                if not plan["canDelete"]:
                    raise MediaDeletionError(
                        "media_delete_blocked",
                        plan.get("blockReason") or "Disk deletion is currently blocked.",
                        409,
                    )
                self.send_json(execute_media_delete(plan))
        except MediaDeletionError as exc:
            self.send_json({"error": exc.code, "message": str(exc)}, status=exc.status)

    def api_watch_state(self, method: str) -> None:
        if method != "POST":
            self.send_json({"error": "method_not_allowed"}, status=405)
            return
        payload = self.read_json()
        rating_key = str(payload.get("ratingKey") or "").strip()
        watched = payload.get("watched")
        if not rating_key:
            self.send_json({"error": "missing_rating_key"}, status=400)
            return
        if not isinstance(watched, bool):
            self.send_json({"error": "invalid_watched_state"}, status=400)
            return
        item = metadata_item_for_rating_key(rating_key)
        if item is None:
            self.send_json({"error": "metadata_not_found"}, status=404)
            return
        if item.get("type") not in {"movie", "episode"}:
            self.send_json({"error": "unsupported_media_type"}, status=400)
            return

        if watched:
            mark_plex_watched(rating_key)
        else:
            mark_plex_unwatched(rating_key)

        refreshed = metadata_item_for_rating_key(rating_key) or item
        refreshed["viewCount"] = max(1, refreshed.get("viewCount") or 0) if watched else 0
        if not watched:
            refreshed["viewOffset"] = 0
        self.send_json({"ok": True, "watched": watched, "item": refreshed})

    def api_my_list(self, method: str, query: Dict[str, List[str]]) -> None:
        if method == "POST":
            payload = self.read_json()
            rating_key = str(payload.get("ratingKey") or "").strip()
            saved = payload.get("saved")
            if not re.fullmatch(r"\d+", rating_key):
                self.send_json({"error": "invalid_rating_key"}, status=400)
                return
            if not isinstance(saved, bool):
                self.send_json({"error": "invalid_saved_state"}, status=400)
                return
            item = metadata_item_for_rating_key(rating_key) if saved else None
            if saved and item is None:
                self.send_json({"error": "metadata_not_found"}, status=404)
                return
            if saved and item.get("type") not in {"movie", "show", "episode"}:
                self.send_json({"error": "unsupported_media_type"}, status=400)
                return
            keys = update_my_list(rating_key, saved)
            if item is not None:
                item["inMyList"] = saved
            self.send_json(
                {
                    "ok": True,
                    "ratingKey": rating_key,
                    "saved": saved,
                    "ratingKeys": keys,
                    "item": item,
                }
            )
            return
        if method not in {"GET", "HEAD"}:
            self.send_json({"error": "method_not_allowed"}, status=405)
            return
        keys = my_list_keys()
        if one(query, "keysOnly", "").strip().lower() in {"1", "true", "yes", "on"}:
            self.send_json({"ratingKeys": keys, "totalSize": len(keys)})
            return
        section_key = one(query, "sectionKey", "").strip()
        if section_key and not re.fullmatch(r"\d+", section_key):
            self.send_json({"error": "invalid_section"}, status=400)
            return
        start = max(0, to_int(one(query, "start", "0")) or 0)
        limit = min(300, max(1, to_int(one(query, "limit", "48")) or 48))
        keys, items = my_list_items(section_key)
        total = len(items)
        self.send_json(
            {
                "ratingKeys": keys,
                "sectionKey": section_key or None,
                "start": start,
                "limit": limit,
                "size": len(items[start : start + limit]),
                "totalSize": total,
                "items": items[start : start + limit],
            }
        )

    def collection_membership(self, rating_key: str) -> Dict[str, Any]:
        metadata_root = PLEX.xml(
            f"/library/metadata/{urllib.parse.quote(rating_key)}",
            params={"includeCollections": "1"},
        )
        movie_elem = next(iter(metadata_root), None)
        if movie_elem is None:
            raise LookupError("metadata_not_found")
        if movie_elem.get("type") != "movie":
            raise ValueError("unsupported_media_type")
        section_key = movie_elem.get("librarySectionID") or ""
        if not re.fullmatch(r"\d+", section_key):
            raise ValueError("invalid_library_section")

        collections_root = PLEX.xml(
            f"/library/sections/{section_key}/collections",
            params={
                "sort": "titleSort",
                "X-Plex-Container-Start": 0,
                "X-Plex-Container-Size": 500,
            },
        )
        member_titles = {
            child.get("tag")
            for child in movie_elem.findall("Collection")
            if child.get("tag")
        }
        collections = []
        for elem in list(collections_root):
            if elem.get("type") != "collection" or elem.get("subtype") not in {None, "movie"}:
                continue
            title = elem.get("title") or "Untitled collection"
            smart = elem.get("smart") in {"1", "true", "True"}
            collections.append(
                {
                    "ratingKey": elem.get("ratingKey"),
                    "title": title,
                    "smart": smart,
                    "editable": not smart,
                    "member": title in member_titles,
                    "childCount": to_int(elem.get("childCount")) or 0,
                }
            )
        collections.sort(key=lambda collection: collection["title"].casefold())
        item = item_from_xml(movie_elem)
        return {
            "ratingKey": rating_key,
            "librarySectionID": section_key,
            "item": item,
            "collections": collections,
            "memberCount": sum(1 for collection in collections if collection["member"]),
        }

    def api_collection_membership(self, method: str, query: Dict[str, List[str]]) -> None:
        if method == "GET":
            rating_key = one(query, "ratingKey", "").strip()
        elif method == "POST":
            payload = self.read_json()
            rating_key = str(payload.get("ratingKey", "")).strip()
        else:
            self.send_json({"error": "method_not_allowed"}, status=405)
            return
        if not re.fullmatch(r"\d+", rating_key):
            self.send_json({"error": "invalid_rating_key"}, status=400)
            return

        try:
            membership = self.collection_membership(rating_key)
        except LookupError as exc:
            self.send_json({"error": str(exc)}, status=404)
            return
        except ValueError as exc:
            self.send_json({"error": str(exc)}, status=400)
            return
        if method == "GET":
            self.send_json(membership)
            return

        collection_key = str(payload.get("collectionRatingKey", "")).strip()
        member = payload.get("member")
        if not re.fullmatch(r"\d+", collection_key):
            self.send_json({"error": "invalid_collection_rating_key"}, status=400)
            return
        if not isinstance(member, bool):
            self.send_json({"error": "invalid_member_state"}, status=400)
            return
        collection = next(
            (item for item in membership["collections"] if item["ratingKey"] == collection_key),
            None,
        )
        if collection is None:
            self.send_json({"error": "collection_not_found"}, status=404)
            return
        if not collection["editable"]:
            self.send_json({"error": "smart_collection_read_only"}, status=409)
            return

        if collection["member"] != member:
            collection_path = f"/library/collections/{collection_key}/items"
            if member:
                server_root = PLEX.xml("/")
                machine_id = server_root.get("machineIdentifier") or ""
                if not machine_id:
                    self.send_json({"error": "plex_machine_identifier_unavailable"}, status=502)
                    return
                uri = (
                    f"server://{machine_id}/com.plexapp.plugins.library"
                    f"/library/metadata/{rating_key}"
                )
                response = PLEX.open(collection_path, params={"uri": uri}, method="PUT")
                response.close()
            else:
                response = PLEX.open(f"{collection_path}/{rating_key}", method="DELETE")
                response.close()
            membership = self.collection_membership(rating_key)

        self.send_json({"ok": True, **membership})

    def api_collection_management(self, method: str) -> None:
        if method != "POST":
            self.send_json({"error": "method_not_allowed"}, status=405)
            return
        payload = self.read_json()
        action = str(payload.get("action", "")).strip().lower()
        if action not in {"create", "rename", "delete"}:
            self.send_json({"error": "invalid_collection_action"}, status=400)
            return
        rating_key = str(payload.get("ratingKey", "")).strip()
        section_key = str(payload.get("sectionKey", "")).strip()
        collection_key = str(payload.get("collectionRatingKey", "")).strip()
        if action == "delete" and not rating_key and section_key:
            if not re.fullmatch(r"\d+", section_key):
                self.send_json({"error": "invalid_section"}, status=400)
                return
            if not re.fullmatch(r"\d+", collection_key):
                self.send_json({"error": "invalid_collection_rating_key"}, status=400)
                return
            root = PLEX.xml(
                f"/library/sections/{section_key}/collections",
                params={"X-Plex-Container-Start": 0, "X-Plex-Container-Size": 500},
            )
            collection_elem = next(
                (elem for elem in root if elem.get("ratingKey") == collection_key),
                None,
            )
            if collection_elem is None:
                self.send_json({"error": "collection_not_found"}, status=404)
                return
            if str(collection_elem.get("smart") or "").lower() in {"1", "true"}:
                self.send_json({"error": "smart_collection_read_only"}, status=409)
                return
            response = PLEX.open(f"/library/collections/{collection_key}", method="DELETE")
            response.close()
            self.send_json(
                {
                    "ok": True,
                    "action": "delete",
                    "sectionKey": section_key,
                    "collectionRatingKey": collection_key,
                    "title": collection_elem.get("title") or "Collection",
                }
            )
            return
        if not re.fullmatch(r"\d+", rating_key):
            self.send_json({"error": "invalid_rating_key"}, status=400)
            return
        try:
            membership = self.collection_membership(rating_key)
        except LookupError as exc:
            self.send_json({"error": str(exc)}, status=404)
            return
        except ValueError as exc:
            self.send_json({"error": str(exc)}, status=400)
            return

        title = payload.get("title")
        if action in {"create", "rename"}:
            if not isinstance(title, str):
                self.send_json({"error": "invalid_collection_title"}, status=400)
                return
            title = title.strip()
            if not title or len(title) > 120 or any(ord(char) < 32 for char in title):
                self.send_json({"error": "invalid_collection_title"}, status=400)
                return

        collection = None
        if action in {"rename", "delete"}:
            if not re.fullmatch(r"\d+", collection_key):
                self.send_json({"error": "invalid_collection_rating_key"}, status=400)
                return
            collection = next(
                (item for item in membership["collections"] if item["ratingKey"] == collection_key),
                None,
            )
            if collection is None:
                self.send_json({"error": "collection_not_found"}, status=404)
                return
            if not collection["editable"]:
                self.send_json({"error": "smart_collection_read_only"}, status=409)
                return

        if action in {"create", "rename"}:
            duplicate = next(
                (
                    item
                    for item in membership["collections"]
                    if item["title"].casefold() == title.casefold()
                    and item["ratingKey"] != collection_key
                ),
                None,
            )
            if duplicate is not None:
                self.send_json({"error": "collection_title_already_exists"}, status=409)
                return

        section_key = membership["librarySectionID"]
        if action == "create":
            server_root = PLEX.xml("/")
            machine_id = server_root.get("machineIdentifier") or ""
            if not machine_id:
                self.send_json({"error": "plex_machine_identifier_unavailable"}, status=502)
                return
            uri = (
                f"server://{machine_id}/com.plexapp.plugins.library"
                f"/library/metadata/{rating_key}"
            )
            response = PLEX.open(
                "/library/collections",
                params={
                    "uri": uri,
                    "type": 1,
                    "title": title,
                    "smart": 0,
                    "sectionId": section_key,
                },
                method="POST",
            )
            response.close()
        elif action == "rename" and title != collection["title"]:
            response = PLEX.open(
                f"/library/sections/{section_key}/all",
                params={
                    "id": collection_key,
                    "type": 18,
                    "title.value": title,
                    "title.locked": 1,
                },
                method="PUT",
            )
            response.close()
        elif action == "delete":
            response = PLEX.open(f"/library/collections/{collection_key}", method="DELETE")
            response.close()

        refreshed = self.collection_membership(rating_key)
        self.send_json({"ok": True, "action": action, **refreshed})

    def api_library(self, path: str, query: Dict[str, List[str]]) -> None:
        section_key = path[len("/api/library/") :].strip("/").split("/", 1)[0]
        if not re.fullmatch(r"\d+", section_key):
            self.send_json({"error": "invalid_section"}, status=400)
            return
        try:
            payload = library_page_for_section(section_key, query)
        except ValueError as exc:
            self.send_json({"error": str(exc)}, status=400)
            return
        self.send_json(payload, cache_control=BROWSE_CACHE_CONTROL)

    def api_metadata(
        self,
        rating_key: str,
        query: Optional[Dict[str, List[str]]] = None,
    ) -> None:
        def load() -> Dict[str, Any]:
            root = PLEX.xml(
                f"/library/metadata/{urllib.parse.quote(rating_key)}",
                params={"includeGuids": "1", "includeCollections": "1"},
            )
            items = items_from_container(root, detailed=True)
            return {"item": items[0] if items else None}

        refresh = one(query or {}, "refresh", "").strip().lower() in {"1", "true", "yes"}
        payload = load() if refresh else API_CACHE.get_or_load(
            f"metadata:{id(PLEX)}:{rating_key}",
            10.0,
            load,
        )
        self.send_json(payload, cache_control=BROWSE_CACHE_CONTROL)

    def api_media_match(self, method: str, query: Dict[str, List[str]]) -> None:
        if method not in {"GET", "POST"}:
            self.send_json({"error": "method_not_allowed"}, status=405)
            return
        payload = self.read_json() if method == "POST" else {}
        rating_key = str(
            payload.get("ratingKey") if method == "POST" else one(query, "ratingKey", "")
        ).strip()
        if not re.fullmatch(r"\d+", rating_key):
            self.send_json(
                {"error": "invalid_rating_key", "message": "Select a valid Plex item."},
                status=400,
            )
            return
        item = metadata_item_for_rating_key(rating_key)
        if item is None:
            self.send_json({"error": "not_found", "message": "Plex item was not found."}, status=404)
            return
        if item.get("type") not in {"movie", "show"}:
            self.send_json(
                {
                    "error": "unsupported_media_type",
                    "message": "Fix Match is available for movies and TV shows.",
                },
                status=400,
            )
            return

        if method == "GET":
            title = one(query, "title", str(item.get("title") or "")).strip()
            if not title or len(title) > 200 or any(ord(char) < 32 for char in title):
                self.send_json(
                    {
                        "error": "invalid_title",
                        "message": "Enter a title or external ID up to 200 characters.",
                    },
                    status=400,
                )
                return
            try:
                year = normalize_match_year(one(query, "year", str(item.get("year") or "")))
                language = normalize_match_language(one(query, "language", ""))
            except ValueError as exc:
                message = (
                    "Enter a valid four-digit year."
                    if str(exc) == "invalid_year"
                    else "Select a valid metadata language."
                )
                self.send_json({"error": str(exc), "message": message}, status=400)
                return
            self.send_json(search_plex_matches(item, title, year, language))
            return

        guid = str(payload.get("guid") or "").strip()
        name = str(payload.get("name") or "").strip()
        guid_match = MATCH_GUID_PATTERN.fullmatch(guid)
        if not guid_match or guid_match.group(1) != item.get("type"):
            self.send_json(
                {"error": "invalid_match", "message": "Select a valid Plex match result."},
                status=400,
            )
            return
        if not name or len(name) > 300 or any(ord(char) < 32 for char in name):
            self.send_json(
                {"error": "invalid_match_name", "message": "The selected match has no valid title."},
                status=400,
            )
            return
        try:
            year = normalize_match_year(payload.get("year"))
        except ValueError:
            self.send_json(
                {"error": "invalid_year", "message": "The selected match has an invalid year."},
                status=400,
            )
            return

        params: Dict[str, Any] = {"guid": guid, "name": name}
        if year is not None:
            params["year"] = year
        response = PLEX.open(
            f"/library/metadata/{urllib.parse.quote(rating_key)}/match",
            params=params,
            method="PUT",
        )
        response.close()
        API_CACHE.clear()

        refreshed: Optional[Dict[str, Any]] = None
        deadline = time.monotonic() + (3.0 if item.get("guid") != guid else 0.0)
        while True:
            refreshed = metadata_item_for_rating_key(rating_key)
            if refreshed is None or refreshed.get("guid") == guid or time.monotonic() >= deadline:
                break
            time.sleep(0.25)
        pending = refreshed is None or refreshed.get("guid") != guid
        self.send_json(
            {
                "ok": True,
                "ratingKey": rating_key,
                "guid": guid,
                "pending": pending,
                "item": refreshed or item,
            }
        )

    def api_children(self, rating_key: str) -> None:
        def load() -> Dict[str, Any]:
            root = PLEX.xml(f"/library/metadata/{urllib.parse.quote(rating_key)}/children")
            return {
                "parentTitle": root.get("parentTitle") or root.get("grandparentTitle"),
                "parentRatingKey": root.get("parentRatingKey"),
                "items": items_from_container(root),
            }

        self.send_json(
            API_CACHE.get_or_load(f"children:{id(PLEX)}:{rating_key}", 5.0, load),
            cache_control=BROWSE_CACHE_CONTROL,
        )

    def api_episode_neighbors(self, query: Dict[str, List[str]]) -> None:
        rating_key = one(query, "ratingKey", "").strip()
        if not re.fullmatch(r"\d+", rating_key):
            self.send_json({"error": "invalid_rating_key"}, status=400)
            return
        try:
            previous_item, next_item, position, total = episode_neighbors(rating_key)
        except ValueError as exc:
            self.send_json({"error": str(exc)}, status=400)
            return
        except LookupError as exc:
            self.send_json({"error": str(exc)}, status=404)
            return
        self.send_json(
            {
                "ratingKey": rating_key,
                "position": position,
                "totalSize": total,
                "previous": previous_item,
                "next": next_item,
            }
        )

    def api_search(self, query: Dict[str, List[str]]) -> None:
        search_query = one(query, "query", "").strip()
        if len(search_query) < 2:
            self.send_json({"items": []})
            return
        def load() -> Dict[str, Any]:
            root = PLEX.xml("/search", params={"query": search_query, "includeCollections": "1"})
            items = items_from_container(root, recursive=True)
            return {"query": search_query, "items": items[:150]}

        key = f"search:{id(PLEX)}:{search_query.casefold()}"
        self.send_json(
            API_CACHE.get_or_load(key, 5.0, load),
            cache_control=BROWSE_CACHE_CONTROL,
        )

    def handle_image(self, method: str, query: Dict[str, List[str]]) -> None:
        if method not in {"GET", "HEAD"}:
            self.send_json({"error": "method_not_allowed"}, status=405)
            return
        self.require_auth()
        raw_path = one(query, "path", "")
        plex_path, image_params = plex_image_upstream_request(raw_path)
        if not plex_path:
            self.send_json({"error": "bad_image_path"}, status=400)
            return
        with PLEX.open(
            plex_path,
            params=image_params,
            timeout=Settings.request_timeout,
            method=method,
        ) as response:
            self.send_response(response.status)
            self.send_header("Content-Type", response.headers.get("Content-Type", "image/jpeg"))
            self.send_header("Cache-Control", "private, max-age=2592000, immutable")
            self.send_header("X-Content-Type-Options", "nosniff")
            content_length = response.headers.get("Content-Length")
            if content_length:
                self.send_header("Content-Length", content_length)
            else:
                self.send_header("Connection", "close")
                self.close_connection = True
            self.end_headers()
            if method != "HEAD":
                try:
                    copy_stream(response, self.wfile, 128 * 1024)
                except (BrokenPipeError, ConnectionResetError):
                    return

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
        stream_format = one(query, "format", "").strip().lower()
        if stream_format == "hls":
            self.handle_hls_stream(method, plex_path, query)
            return
        if method == "HEAD":
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Cache-Control", "private, max-age=0")
            self.end_headers()
            return

        quality = one(query, "quality", "").strip().lower()
        remote_quality = quality in {"remote", "low", "480p"}
        video_mode = one(query, "video", "").strip().lower()
        transcode_video = video_mode in {"h264", "transcode"}
        command = compatible_stream_command(plex_path, remote_quality, transcode_video)
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
                (
                    "remote-480p-h264-aac"
                    if remote_quality
                    else "video-transcode-h264-audio-transcode-aac"
                    if transcode_video
                    else "audio-transcode-aac"
                ),
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

    def handle_hls_stream(
        self,
        method: str,
        part_key: str,
        query: Dict[str, List[str]],
    ) -> None:
        if method == "HEAD":
            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.apple.mpegurl")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        quality = one(query, "quality", "").strip().lower()
        remote_quality = quality in {"remote", "low", "480p"}
        video_mode = one(query, "video", "").strip().lower()
        transcode_video = video_mode in {"h264", "transcode"}
        try:
            playback_id, _, raw_manifest = ensure_hls_stream(
                part_key,
                remote_quality,
                transcode_video,
            )
            body = hls_manifest_text(playback_id, raw_manifest).encode("utf-8")
        except TimeoutError as exc:
            self.send_json({"error": "hls_startup_timeout", "message": str(exc)}, status=504)
            return
        except RuntimeError as exc:
            self.send_json({"error": "hls_transcode_failed", "message": str(exc)}, status=502)
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/vnd.apple.mpegurl")
        self.send_header("Cache-Control", "no-store")
        self.send_header(
            "X-Playback-Mode",
            (
                "hls-480p-h264-aac"
                if remote_quality
                else "hls-video-transcode-h264-aac"
                if transcode_video
                else "hls-audio-transcode-aac"
            ),
        )
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def handle_hls_segment(self, method: str, query: Dict[str, List[str]]) -> None:
        if method not in {"GET", "HEAD"}:
            self.send_json({"error": "method_not_allowed"}, status=405)
            return
        playback_id = one(query, "id", "").strip().lower()
        name = one(query, "name", "").strip()
        if not re.fullmatch(r"[a-f0-9]{24}", playback_id) or not HLS_SEGMENT_PATTERN.fullmatch(name):
            self.send_json({"error": "bad_hls_segment"}, status=400)
            return
        session_dir = hls_session_dir(playback_id)
        segment_path = session_dir / name
        if not segment_path.is_file() or segment_path.is_symlink():
            self.send_json({"error": "hls_segment_not_found"}, status=404)
            return
        touch_hls_session(session_dir)
        size = segment_path.stat().st_size
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
        self.send_header("Content-Type", "video/mp2t")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "private, max-age=14400, immutable")
        self.send_header("Content-Length", str(length))
        if selected_range:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if method == "HEAD":
            return
        try:
            with segment_path.open("rb") as handle:
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

    def api_playback_progress(self, method: str) -> None:
        if method != "POST":
            self.send_json({"error": "method_not_allowed"}, status=405)
            return
        payload = self.read_json()
        rating_key = str(payload.get("ratingKey") or "").strip()
        time_ms = max(0, to_int(str(payload.get("timeMs") or "0")) or 0)
        duration_ms = max(0, to_int(str(payload.get("durationMs") or "0")) or 0)
        state = str(payload.get("state") or "stopped").strip().lower()
        if not rating_key:
            self.send_json({"error": "missing_rating_key"}, status=400)
            return
        if metadata_item_element(rating_key) is None:
            self.send_json({"error": "not_found"}, status=404)
            return

        watched = state == "ended" or watched_threshold_reached(time_ms, duration_ms)
        progress_saved = False
        if watched:
            mark_plex_watched(rating_key)
        elif time_ms >= 60000:
            update_plex_progress(rating_key, time_ms)
            progress_saved = True
        self.send_json(
            {
                "ok": True,
                "watched": watched,
                "progressSaved": progress_saved,
                "timeMs": time_ms,
                "durationMs": duration_ms,
                "state": state,
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

    def handle_original_download(self, method: str, query: Dict[str, List[str]]) -> None:
        if method not in {"GET", "HEAD"}:
            self.send_json({"error": "method_not_allowed"}, status=405)
            return
        self.require_auth()
        rating_key = one(query, "ratingKey", "").strip()
        if not rating_key:
            self.send_json({"error": "missing_rating_key"}, status=400)
            return
        elem = metadata_item_element(rating_key)
        if elem is None:
            self.send_json({"error": "metadata_not_found"}, status=404)
            return
        part = first_part_element(elem)
        media_path = Path(part.get("file", "")) if part is not None and part.get("file") else None
        if media_path is None or not media_path.is_absolute() or not media_path.is_file():
            self.send_json({"error": "media_file_unavailable"}, status=404)
            return

        subtitles = subtitles_for_part(rating_key, part)
        zip_name = safe_download_filename(f"{media_path.stem} + subtitles.zip", "media-with-subtitles.zip")
        self.send_response(200)
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Disposition", content_disposition_attachment(zip_name))
        self.send_header("Cache-Control", "private, max-age=0")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        if method == "HEAD":
            return
        try:
            with zipfile.ZipFile(self.wfile, mode="w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
                archive.write(media_path, arcname=safe_download_filename(media_path.name, "video"))
                for name, data in subtitle_download_entries(rating_key, media_path, subtitles):
                    info = zipfile.ZipInfo(name)
                    info.compress_type = zipfile.ZIP_DEFLATED
                    archive.writestr(info, data)
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


class ResponsiveThreadingHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False
    request_queue_size = 128
    allow_reuse_address = True


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
    httpd = ResponsiveThreadingHTTPServer((Settings.host, Settings.port), AppHandler)
    print(f"Plex Open Web listening on http://{Settings.host}:{Settings.port}", flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
