"""In-memory + persistent context token cache.

The WeChat iLink API issues a ``context_token`` with every inbound message
received via ``getUpdates``.  This token **must** be echoed back verbatim in
every outbound ``sendMessage`` call so the server can associate the reply with
the correct conversation session.

``ContextStore`` keeps a fast in-memory dict keyed by ``(account_id, user_id)``
and optionally persists the mapping to disk so tokens survive process restarts.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Optional

from wechat_bot_cli.core.constants import DEFAULT_CONFIG_DIR_NAME

logger = logging.getLogger(__name__)

# Default filename used when persistence is enabled.
_CONTEXT_TOKENS_FILENAME = "context_tokens.json"


class ContextStore:
    """Thread-safe, in-memory context token cache with optional JSON persistence.

    Parameters
    ----------
    persist_dir:
        Directory where ``context_tokens.json`` will be read/written.
        Pass ``None`` to disable persistence (pure in-memory mode).
        When omitted the default ``~/.wechat-bot-cli`` directory is used.
    auto_save:
        When ``True`` (the default), every :meth:`set` call will also write the
        updated mapping to disk.  Set to ``False`` if you prefer to call
        :meth:`save` explicitly (e.g. in a batch after processing many
        messages).
    """

    def __init__(
        self,
        persist_dir: Path | str | None = ...,
        *,
        auto_save: bool = True,
    ) -> None:
        # Sentinel ``...`` means "use default"; explicit ``None`` disables persistence.
        if persist_dir is ...:
            persist_dir = Path.home() / DEFAULT_CONFIG_DIR_NAME
        self._persist_path: Path | None = (
            Path(persist_dir) / _CONTEXT_TOKENS_FILENAME
            if persist_dir is not None
            else None
        )
        self._auto_save = auto_save
        self._lock = threading.Lock()
        self._tokens: dict[str, str] = {}

        # Eagerly load persisted data if available.
        if self._persist_path is not None:
            self.load()

    # ------------------------------------------------------------------
    # Key helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _key(account_id: str, user_id: str) -> str:
        """Build a composite cache key from account and user identifiers."""
        return f"{account_id}:{user_id}"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set(self, account_id: str, user_id: str, token: str) -> None:
        """Store *token* for the given account/user pair.

        Parameters
        ----------
        account_id:
            Bot account identifier (e.g. the normalised iLink account id).
        user_id:
            The ``from_user_id`` of the remote user.
        token:
            The ``context_token`` value received in the inbound message.
        """
        key = self._key(account_id, user_id)
        with self._lock:
            self._tokens[key] = token
        logger.debug("ContextStore.set: key=%s", key)
        if self._auto_save:
            self.save()

    def get(self, account_id: str, user_id: str) -> Optional[str]:
        """Return the cached token or ``None`` if not found.

        Parameters
        ----------
        account_id:
            Bot account identifier.
        user_id:
            Remote user identifier.
        """
        key = self._key(account_id, user_id)
        with self._lock:
            token = self._tokens.get(key)
        logger.debug(
            "ContextStore.get: key=%s found=%s store_size=%d",
            key,
            token is not None,
            len(self._tokens),
        )
        return token

    def delete(self, account_id: str, user_id: str) -> bool:
        """Remove a cached token.  Returns ``True`` if an entry was removed."""
        key = self._key(account_id, user_id)
        with self._lock:
            removed = self._tokens.pop(key, None) is not None
        if removed and self._auto_save:
            self.save()
        return removed

    def clear(self) -> None:
        """Drop all cached tokens (and persist the empty state if enabled)."""
        with self._lock:
            self._tokens.clear()
        if self._auto_save:
            self.save()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self) -> None:
        """Write the current token mapping to disk.

        No-op when persistence is disabled (``persist_dir=None``).
        Errors are logged but never raised -- persistence is best-effort.
        """
        if self._persist_path is None:
            return
        try:
            self._persist_path.parent.mkdir(parents=True, exist_ok=True)
            with self._lock:
                snapshot = dict(self._tokens)
            # Atomic-ish write: write to a temp file then rename.
            tmp_path = self._persist_path.with_suffix(".tmp")
            tmp_path.write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
            tmp_path.replace(self._persist_path)
            logger.debug(
                "ContextStore.save: wrote %d entries to %s",
                len(snapshot),
                self._persist_path,
            )
        except OSError:
            logger.warning(
                "ContextStore.save: failed to persist tokens to %s",
                self._persist_path,
                exc_info=True,
            )

    def load(self) -> None:
        """Load tokens from disk into memory, merging with any existing entries.

        No-op when persistence is disabled or the file does not exist.
        Errors are logged but never raised.
        """
        if self._persist_path is None:
            return
        if not self._persist_path.is_file():
            logger.debug(
                "ContextStore.load: no persisted file at %s", self._persist_path
            )
            return
        try:
            raw = self._persist_path.read_text(encoding="utf-8")
            data: dict[str, str] = json.loads(raw)
            if not isinstance(data, dict):
                logger.warning(
                    "ContextStore.load: expected dict, got %s -- ignoring",
                    type(data).__name__,
                )
                return
            with self._lock:
                # Merge: persisted values fill in gaps, but in-memory values
                # (which are newer) take precedence.
                for k, v in data.items():
                    self._tokens.setdefault(k, v)
            logger.debug(
                "ContextStore.load: loaded %d entries from %s (total now %d)",
                len(data),
                self._persist_path,
                len(self._tokens),
            )
        except (OSError, json.JSONDecodeError):
            logger.warning(
                "ContextStore.load: failed to read %s",
                self._persist_path,
                exc_info=True,
            )

    # ------------------------------------------------------------------
    # Dunder helpers
    # ------------------------------------------------------------------

    def __len__(self) -> int:
        with self._lock:
            return len(self._tokens)

    def __repr__(self) -> str:
        return (
            f"<ContextStore entries={len(self)} "
            f"persist={'on' if self._persist_path else 'off'}>"
        )
