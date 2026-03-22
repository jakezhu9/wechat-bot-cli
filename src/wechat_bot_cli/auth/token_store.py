"""Persistent credential and account storage.

Stores bot account credentials as individual JSON files under the user's
config directory (``~/.wechat-bot-cli/accounts/``).  An index file at
``~/.wechat-bot-cli/accounts.json`` keeps a lightweight registry of every
known account for quick enumeration.

Thread safety
-------------
File writes are protected by ``filelock`` when available.  If the package
is not installed, a no-op context manager is used instead, which is safe
for single-process CLI usage but not for concurrent writers.

Typical layout::

    ~/.wechat-bot-cli/
    ├── accounts.json              # index: [{id, name?}]
    └── accounts/
        ├── abc123-im-wechat.json  # per-account credentials
        └── xyz789-im-wechat.json
"""

from __future__ import annotations

import json
import logging
import re
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from wechat_bot_cli.core.constants import (
    ACCOUNTS_SUBDIR,
    CDN_BASE_URL,
    DEFAULT_BASE_URL,
    DEFAULT_CONFIG_DIR_NAME,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Filelock helpers
# ---------------------------------------------------------------------------

try:
    from filelock import FileLock as _FileLock  # type: ignore[import-untyped]

    _HAS_FILELOCK = True
except ImportError:  # pragma: no cover
    _HAS_FILELOCK = False


@contextmanager
def _file_lock(lock_path: Path, timeout: float = 5.0) -> Iterator[None]:
    """Acquire an advisory file lock if *filelock* is installed.

    Falls back to a no-op context manager when the library is absent, which
    is acceptable for single-process CLI usage.

    Parameters
    ----------
    lock_path:
        Path to the lock file (typically ``<target>.lock``).
    timeout:
        Maximum seconds to wait for the lock.
    """
    if _HAS_FILELOCK:
        lock = _FileLock(str(lock_path), timeout=timeout)
        with lock:
            yield
    else:
        yield


# ---------------------------------------------------------------------------
# Account-ID helpers
# ---------------------------------------------------------------------------

# Characters that are not safe in filenames.
_UNSAFE_CHARS = re.compile(r"[^a-zA-Z0-9_-]")


def normalize_account_id(raw_id: str) -> str:
    """Convert a raw account ID into a filesystem-safe form.

    The WeChat iLink API returns IDs such as ``abc123@im.wechat``.  This
    function replaces every non-alphanumeric character (except ``_`` and
    ``-``) with ``-`` so the result can be used directly as a filename
    stem.

    Examples
    --------
    >>> normalize_account_id("abc123@im.wechat")
    'abc123-im-wechat'
    >>> normalize_account_id("simple-id")
    'simple-id'
    """
    return _UNSAFE_CHARS.sub("-", raw_id)


def denormalize_account_id(normalized: str) -> Optional[str]:
    """Attempt to reverse :func:`normalize_account_id` for known suffixes.

    Only the two common iLink patterns are supported (``@im.wechat`` and
    ``@im.bot``).  Returns ``None`` when the suffix is not recognised.

    Examples
    --------
    >>> denormalize_account_id("abc123-im-wechat")
    'abc123@im.wechat'
    >>> denormalize_account_id("abc123-im-bot")
    'abc123@im.bot'
    >>> denormalize_account_id("unknown-suffix") is None
    True
    """
    if normalized.endswith("-im-wechat"):
        return f"{normalized[:-10]}@im.wechat"
    if normalized.endswith("-im-bot"):
        return f"{normalized[:-7]}@im.bot"
    return None


# ---------------------------------------------------------------------------
# TokenStore
# ---------------------------------------------------------------------------


class TokenStore:
    """Manages persistent account credentials on disk.

    Parameters
    ----------
    config_dir:
        Root configuration directory.  Defaults to ``~/.wechat-bot-cli``.
    """

    def __init__(self, config_dir: Optional[Path] = None) -> None:
        if config_dir is not None:
            self._root = Path(config_dir)
        else:
            self._root = Path.home() / DEFAULT_CONFIG_DIR_NAME

        self._accounts_dir = self._root / ACCOUNTS_SUBDIR
        self._index_path = self._root / "accounts.json"

    # -- public properties ---------------------------------------------------

    @property
    def config_dir(self) -> Path:
        """Root configuration directory (e.g. ``~/.wechat-bot-cli``)."""
        return self._root

    @property
    def accounts_dir(self) -> Path:
        """Directory containing per-account JSON files."""
        return self._accounts_dir

    # -- account file helpers ------------------------------------------------

    def _account_path(self, account_id: str) -> Path:
        """Return the JSON file path for *account_id* (already normalised)."""
        return self._accounts_dir / f"{account_id}.json"

    def _ensure_dirs(self) -> None:
        """Create the accounts directory (and parents) if missing."""
        self._accounts_dir.mkdir(parents=True, exist_ok=True)

    # -- index management ----------------------------------------------------

    def _read_index(self) -> List[Dict[str, Any]]:
        """Load the account index from disk.

        Returns a list of dicts, each having at least an ``"id"`` key.
        If the file does not exist or is corrupt, an empty list is returned.
        """
        if not self._index_path.exists():
            return []
        try:
            with _file_lock(self._index_path.with_suffix(".lock")):
                data = json.loads(self._index_path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return [
                    entry
                    for entry in data
                    if isinstance(entry, dict) and isinstance(entry.get("id"), str)
                ]
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to read account index %s: %s", self._index_path, exc)
        return []

    def _write_index(self, entries: List[Dict[str, Any]]) -> None:
        """Atomically write the account index to disk."""
        self._ensure_dirs()
        with _file_lock(self._index_path.with_suffix(".lock")):
            self._index_path.write_text(
                json.dumps(entries, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )

    def _upsert_index(self, account_id: str, name: Optional[str] = None) -> None:
        """Insert or update an entry in the account index."""
        entries = self._read_index()
        for entry in entries:
            if entry.get("id") == account_id:
                if name is not None:
                    entry["name"] = name
                self._write_index(entries)
                return
        new_entry: Dict[str, Any] = {"id": account_id}
        if name is not None:
            new_entry["name"] = name
        entries.append(new_entry)
        self._write_index(entries)

    def _remove_from_index(self, account_id: str) -> None:
        """Remove *account_id* from the index (no-op if absent)."""
        entries = self._read_index()
        filtered = [e for e in entries if e.get("id") != account_id]
        if len(filtered) != len(entries):
            self._write_index(filtered)

    # -- CRUD ----------------------------------------------------------------

    def save_account(
        self,
        account_id: str,
        *,
        token: str,
        base_url: Optional[str] = None,
        cdn_base_url: Optional[str] = None,
        user_id: Optional[str] = None,
        name: Optional[str] = None,
    ) -> Path:
        """Persist account credentials to disk.

        Parameters
        ----------
        account_id:
            Raw account ID as returned by the API (e.g. ``abc@im.wechat``).
            Will be normalised for the filename.
        token:
            Bot authentication token.
        base_url:
            API base URL; defaults to ``DEFAULT_BASE_URL``.
        cdn_base_url:
            CDN base URL; defaults to ``CDN_BASE_URL``.
        user_id:
            The WeChat user ID of the account owner (from QR scan).
        name:
            Optional human-readable name for the account.

        Returns
        -------
        Path
            The path of the saved JSON file.
        """
        safe_id = normalize_account_id(account_id)

        data: Dict[str, Any] = {
            "token": token,
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "base_url": base_url or DEFAULT_BASE_URL,
            "cdn_base_url": cdn_base_url or CDN_BASE_URL,
        }
        if user_id:
            data["user_id"] = user_id

        self._ensure_dirs()
        file_path = self._account_path(safe_id)

        with _file_lock(file_path.with_suffix(".lock")):
            file_path.write_text(
                json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            # Restrict permissions so only the owner can read the token.
            try:
                file_path.chmod(0o600)
            except OSError:
                pass  # best-effort on platforms that don't support chmod

        self._upsert_index(safe_id, name=name)
        logger.info("Saved account %s -> %s", safe_id, file_path)
        return file_path

    def load_account(self, account_id: str) -> Optional[Dict[str, Any]]:
        """Load credentials for *account_id*.

        The method first tries the normalised form of *account_id*.  If not
        found and *account_id* was already normalised, it attempts the
        reverse-denormalised filename as a compatibility fallback.

        Parameters
        ----------
        account_id:
            Account ID (raw or normalised).

        Returns
        -------
        dict or None
            The stored credential dict, or ``None`` if not found.
        """
        safe_id = normalize_account_id(account_id)
        primary = self._account_path(safe_id)

        # Primary lookup.
        data = self._try_read_account(primary)
        if data is not None:
            return data

        # Compatibility: try denormalised filename (old installs).
        raw_id = denormalize_account_id(safe_id)
        if raw_id is not None:
            compat = self._account_path(raw_id)
            data = self._try_read_account(compat)
            if data is not None:
                return data

        return None

    def list_accounts(self) -> List[Dict[str, Any]]:
        """Return a list of all registered accounts.

        Each element is a dict with at least ``"id"`` and optionally
        ``"name"``.  The credential data itself is **not** loaded; call
        :meth:`load_account` for that.

        Returns
        -------
        list[dict]
            Registered accounts from the index file.
        """
        return self._read_index()

    def remove_account(self, account_id: str) -> bool:
        """Delete an account's credential file and index entry.

        Parameters
        ----------
        account_id:
            Account ID (raw or normalised).

        Returns
        -------
        bool
            ``True`` if the account file was found and deleted, ``False``
            otherwise.
        """
        safe_id = normalize_account_id(account_id)
        file_path = self._account_path(safe_id)
        removed = False

        with _file_lock(file_path.with_suffix(".lock")):
            try:
                file_path.unlink()
                removed = True
                logger.info("Removed account file %s", file_path)
            except FileNotFoundError:
                logger.debug("Account file not found: %s", file_path)

        self._remove_from_index(safe_id)

        # Also clean up the lock file.
        lock_path = file_path.with_suffix(".lock")
        try:
            lock_path.unlink(missing_ok=True)
        except OSError:
            pass

        return removed

    def get_default_account(self) -> Optional[Dict[str, Any]]:
        """Return the most recently saved account, or ``None``.

        This is a convenience for CLI commands that operate on "the current
        account" when the user does not specify an ID explicitly.

        The method loads every account and picks the one with the latest
        ``saved_at`` timestamp.

        Returns
        -------
        dict or None
            A dict with ``"id"`` and the full credential data merged in,
            or ``None`` if no accounts are stored.
        """
        entries = self._read_index()
        if not entries:
            return None

        best: Optional[Dict[str, Any]] = None
        best_time: str = ""

        for entry in entries:
            aid = entry["id"]
            data = self.load_account(aid)
            if data is None:
                continue
            merged = {**entry, **data}
            saved = merged.get("saved_at", "")
            if isinstance(saved, str) and saved > best_time:
                best_time = saved
                best = merged

        return best

    # -- internal helpers ----------------------------------------------------

    @staticmethod
    def _try_read_account(path: Path) -> Optional[Dict[str, Any]]:
        """Read and parse an account JSON file, returning ``None`` on failure."""
        if not path.is_file():
            return None
        try:
            with _file_lock(path.with_suffix(".lock")):
                data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to read account file %s: %s", path, exc)
        return None
