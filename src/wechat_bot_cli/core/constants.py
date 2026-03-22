"""Project-wide constants for wechat-bot-cli."""

from __future__ import annotations

# ---------------------------------------------------------------------------
# API defaults
# ---------------------------------------------------------------------------

#: Default iLink API base URL.
DEFAULT_BASE_URL: str = "https://ilinkai.weixin.qq.com"

#: Default CDN base URL for media uploads/downloads.
CDN_BASE_URL: str = "https://novac2c.cdn.weixin.qq.com/c2c"

# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------

#: Maximum number of QR-code refresh attempts before aborting a login.
MAX_QR_REFRESH_COUNT: int = 5

# ---------------------------------------------------------------------------
# Token / credential storage
# ---------------------------------------------------------------------------

#: Name of the top-level configuration directory under ``$HOME``.
DEFAULT_CONFIG_DIR_NAME: str = ".wechat-bot-cli"

#: Subdirectory (under the config dir) that stores per-account JSON files.
ACCOUNTS_SUBDIR: str = "accounts"

# ---------------------------------------------------------------------------
# Media upload
# ---------------------------------------------------------------------------

#: Maximum file size (bytes) accepted for CDN upload (~100 MiB).
MEDIA_MAX_BYTES: int = 100 * 1024 * 1024

#: Number of retry attempts for a CDN upload before raising an error.
CDN_UPLOAD_MAX_RETRIES: int = 3

# ---------------------------------------------------------------------------
# Message listener
# ---------------------------------------------------------------------------

#: Filename used to persist the ``get_updates_buf`` sync cursor.
SYNC_BUF_FILENAME: str = "sync_buf.json"

#: Base delay (seconds) for exponential back-off on transient poll errors.
BACKOFF_DELAY: float = 2.0

#: Base retry delay (seconds) for unexpected errors.
RETRY_DELAY: float = 3.0

#: After this many consecutive failures the listener gives up.
MAX_CONSECUTIVE_FAILURES: int = 10
