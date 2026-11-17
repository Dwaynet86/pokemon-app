"""Image handling for photos used to identify a card, and the shared artwork cache.

A photo is only ever a temporary aid. Every upload is re-encoded to WebP (strips metadata, defends
against malformed files) and parked in uploads/pending/ while the user confirms which card it is.
It is deleted when the card is confirmed or the photo discarded (and any leftovers after an hour).
Nothing about the photo is kept in the database; the collection shows the published artwork.
"""
import io
import ipaddress
import re
import socket
import time
from urllib.parse import urljoin, urlparse
from pathlib import Path
from uuid import uuid4

import requests
from flask import current_app
from itsdangerous import BadSignature, URLSafeTimedSerializer
from PIL import Image, ImageOps, UnidentifiedImageError

try:  # iPhone photos are HEIC; optional but recommended
    from pillow_heif import register_heif_opener
    register_heif_opener()
except ImportError:  # pragma: no cover
    pass

NAME_RE = re.compile(r"^[0-9a-f]{32}\.webp$")


class ImageError(ValueError):
    """Raised with a user-facing message when an upload is unusable."""


def _dirs() -> tuple[Path, Path]:
    root: Path = current_app.config["UPLOAD_DIR"]
    pending, catalog = root / "pending", root / "catalog"
    for d in (pending, catalog):
        d.mkdir(parents=True, exist_ok=True)
    return pending, catalog


def catalog_dir() -> Path:
    return _dirs()[1]


def valid_name(name: str | None) -> bool:
    return bool(name) and bool(NAME_RE.match(name))


def _open(file_storage) -> Image.Image:
    ext = Path(file_storage.filename or "").suffix.lower()
    if ext not in current_app.config["ALLOWED_IMAGE_EXTENSIONS"]:
        allowed = ", ".join(sorted(current_app.config["ALLOWED_IMAGE_EXTENSIONS"]))
        raise ImageError(f"Unsupported file type. Use one of: {allowed}.")
    try:
        img = Image.open(file_storage.stream)
        img.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise ImageError("That file couldn't be read as an image.") from None
    img = ImageOps.exif_transpose(img)   # respect phone-camera rotation
    return img if img.mode in ("RGB", "RGBA") else img.convert("RGB")


def _save_pending(img: Image.Image) -> str:
    img.thumbnail((current_app.config["IMAGE_MAX_SIZE"],) * 2)
    _cleanup_pending()
    name = f"{uuid4().hex}.webp"
    img.save(_dirs()[0] / name, "WEBP", quality=85)
    return name


def store_pending(file_storage) -> str:
    """Validate an upload and park it, as it is, until the user confirms the card. Returns the file name."""
    return _save_pending(_open(file_storage))


def pending_path(name: str) -> Path | None:
    path = _dirs()[0] / name if valid_name(name) else None
    return path if path and path.is_file() else None


ART_WIDTHS = {"low": 245, "high": 600}          # same sizes TCGdex publishes
MAX_ART_BYTES = 10 * 1024 * 1024


def art_webp(img: Image.Image) -> dict[str, bytes]:
    """Both artwork sizes as WebP, from any image (never enlarged)."""
    img = ImageOps.exif_transpose(img)
    img = img if img.mode in ("RGB", "RGBA") else img.convert("RGB")
    out = {}
    for size, width in ART_WIDTHS.items():
        copy = img.copy()
        copy.thumbnail((width, width * 4))
        buf = io.BytesIO()
        copy.save(buf, "WEBP", quality=88)
        out[size] = buf.getvalue()
    return out


def art_from_upload(file_storage) -> dict[str, bytes]:
    return art_webp(_open(file_storage))


def art_from_bytes(data: bytes) -> dict[str, bytes]:
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise ImageError("That didn't look like an image.") from None
    return art_webp(img)


def _public_host(host: str) -> bool:
    """Only fetch from public internet addresses (never this server or the local network)."""
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    return bool(infos) and all(ipaddress.ip_address(i[4][0]).is_global for i in infos)


def download_image(url: str, timeout: int = 15) -> bytes:
    """Fetch an image from a web address (up to 3 redirects, public hosts only, size-capped)."""
    for _ in range(4):
        parts = urlparse(url)
        if parts.scheme not in ("http", "https") or not parts.hostname or not _public_host(parts.hostname):
            raise ImageError("That address can't be used. Use a public http(s) link to an image.")
        try:
            resp = requests.get(url, timeout=timeout, stream=True, allow_redirects=False,
                                headers={"User-Agent": "pokemon-card-collection/1.0"})
        except requests.RequestException:
            raise ImageError("Couldn't download that image.") from None
        with resp:
            if resp.is_redirect and resp.headers.get("Location"):
                url = urljoin(url, resp.headers["Location"])
                continue
            if resp.status_code != 200:
                raise ImageError(f"That address returned an error ({resp.status_code}).")
            data = resp.raw.read(MAX_ART_BYTES + 1, decode_content=True)
            if len(data) > MAX_ART_BYTES:
                raise ImageError("That image is too large.")
            return data
    raise ImageError("Too many redirects.")


def catalog_usage() -> int:
    """Bytes used by the local artwork cache (shared by every collection)."""
    return sum(p.stat().st_size for p in catalog_dir().iterdir() if p.is_file())


def discard_pending(name: str) -> None:
    path = pending_path(name)
    if path:
        path.unlink(missing_ok=True)


def _cleanup_pending() -> None:
    """Drop abandoned pending photos (older than an hour)."""
    cutoff = time.time() - 3600
    for path in _dirs()[0].glob("*.webp"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            pass


# ---- Pending-photo tokens: bind a pending file to the user who uploaded it ---------

def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt="pending-photo")


def make_token(user_id: int, name: str) -> str:
    return _serializer().dumps({"u": user_id, "f": name})


def read_token(token: str | None, user_id: int) -> str | None:
    """The pending file name inside a token, or None if it's invalid, expired or someone else's."""
    if not token:
        return None
    try:
        data = _serializer().loads(token, max_age=current_app.config["PENDING_PHOTO_TTL"])
    except BadSignature:
        return None
    return data["f"] if data.get("u") == user_id and valid_name(data.get("f")) else None
