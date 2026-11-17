"""Application configuration, read from environment variables (.env supported)."""
import os
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


class Config:
    # Required: signs sessions, CSRF tokens and pending-photo tokens.
    SECRET_KEY = os.environ.get("SECRET_KEY")
    DATABASE_URL = os.environ.get(
        "DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/pokemon"
    )

    # TCGdex: card data, Pokedex numbers and artwork. No key needed. One language app-wide.
    TCGDEX_BASE_URL = os.environ.get("TCGDEX_BASE_URL", "https://api.tcgdex.net/v2")
    TCGDEX_LANG = os.environ.get("TCGDEX_LANG", "en")
    TCGDEX_TIMEOUT = 20
    SYNC_WORKERS = int(os.environ.get("SYNC_WORKERS", "4"))   # parallel downloads per expansion
    BACKGROUND_JOBS = True                                    # expansion downloads / price lookups

    # PokeWallet: prices only (optional). Free plan: 100 requests/hour, 1000/day.
    PRICE_MAX_AGE_HOURS = int(os.environ.get("PRICE_MAX_AGE_HOURS", "24"))   # prices older than this refresh when a card is added
    PRICE_FALLBACK_MAX = int(os.environ.get("PRICE_FALLBACK_MAX", "15"))     # PokeWallet lookups per "refresh all prices" run
    POKEWALLET_API_KEY = os.environ.get("POKEWALLET_API_KEY", "")
    POKEWALLET_BASE_URL = os.environ.get("POKEWALLET_BASE_URL", "https://api.pokewallet.io")
    POKEWALLET_TIMEOUT = 20

    # Photo identification: the TCG Tracking Open API scans a photo of the card (https://openapi.tcgtracking.com).
    # No API key. Game 3 is Pokemon. The scan result is then matched to a TCGdex card.
    TCGTRACKING_BASE_URL = os.environ.get("TCGTRACKING_BASE_URL", "https://openapi.tcgtracking.com/v1")
    TCGTRACKING_GAME_ID = int(os.environ.get("TCGTRACKING_GAME_ID", "3"))
    TCGTRACKING_TIMEOUT = 30
    SCAN_MIN_SCORE = int(os.environ.get("SCAN_MIN_SCORE", "50"))   # below this match %, retry upside down / uncropped

    # Image uploads
    UPLOAD_DIR = Path(os.environ.get("UPLOAD_DIR", BASE_DIR / "uploads"))
    MAX_UPLOAD_BYTES = 25 * 1024 * 1024    # ordinary uploads (phone photos are large)
    MAX_IMPORT_BYTES = 500 * 1024 * 1024   # backup restore on the Maintenance page
    MAX_CONTENT_LENGTH = MAX_IMPORT_BYTES  # hard cap; MAX_UPLOAD_BYTES is enforced per request
    # Where to look for a card's artwork when TCGdex has none (or it fails). Comma-separated URL templates; {set_id},
    # {local_id} (as TCGdex numbers it) and {number} (no leading zeros) are filled in. Empty turns this off.
    ART_FALLBACK_URLS = [u.strip() for u in os.environ.get(
        "ART_FALLBACK_URLS", "https://images.pokemontcg.io/{set_id}/{number}_hires.png").split(",") if u.strip()]
    ART_RETRY_MINUTES = int(os.environ.get("ART_RETRY_MINUTES", "30"))   # don't re-ask the sources for a missing card sooner
    ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".gif"}
    IMAGE_MAX_SIZE = 1600   # px, longest edge of the temporary photo
    PENDING_PHOTO_TTL = 3600   # seconds a photo can wait on the confirm screen

    # Listing
    CARDS_PER_PAGE = 48

    # Sessions / auth
    SESSION_COOKIE_SAMESITE = "Lax"
    TRUST_PROXY = os.environ.get("TRUST_PROXY", "0") == "1"   # behind a reverse proxy (Nginx, Caddy, Traefik): trust its X-Forwarded-* headers
    SESSION_COOKIE_SECURE = os.environ.get("SESSION_COOKIE_SECURE", "0") == "1"
    REMEMBER_COOKIE_DURATION = timedelta(days=30)
    REMEMBER_COOKIE_SAMESITE = "Lax"
    MIN_PASSWORD_LENGTH = 8

    CURRENCY_SYMBOL = os.environ.get("CURRENCY_SYMBOL", "$")
