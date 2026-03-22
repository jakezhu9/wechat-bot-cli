"""Long-poll message listener for the WeChat iLink bot API.

``MessageListener`` continuously polls ``getUpdates`` and yields incoming
:class:`~wechat_bot_cli.models.WeChatMessage` objects as an async generator.  It
manages:

* **Sync buffer persistence** -- the opaque ``get_updates_buf`` is saved to disk
  after every successful poll so the server knows which messages have already
  been delivered.
* **Backoff / retry** -- consecutive failures trigger exponential back-off;
  a :class:`~wechat_bot_cli.exceptions.SessionExpiredError` triggers a longer
  cooldown before retrying.
* **Context token caching** -- each inbound message's ``context_token`` is
  automatically stored in the provided :class:`ContextStore` so that outbound
  replies can reference the correct conversation.
* **Graceful exit** -- call :meth:`stop` to signal the poll loop to finish
  cleanly after the current iteration.

Usage::

    listener = MessageListener(client, context_store=ctx_store)

    async for msg in listener.listen():
        print(msg.from_user_id, msg.item_list)

    # Or single-shot:
    messages = await listener.listen_once()
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import AsyncGenerator

from wechat_bot_cli.core.client import WeChatAPIClient
from wechat_bot_cli.core.constants import (
    BACKOFF_DELAY,
    DEFAULT_CONFIG_DIR_NAME,
    MAX_CONSECUTIVE_FAILURES,
    RETRY_DELAY,
    SYNC_BUF_FILENAME,
)
from wechat_bot_cli.core.exceptions import SessionExpiredError, WeChatAPIError
from wechat_bot_cli.messaging.context_store import ContextStore
from wechat_bot_cli.core.models import WeChatMessage

logger = logging.getLogger(__name__)

# How long to wait (seconds) after a SessionExpiredError before retrying.
_SESSION_EXPIRED_COOLDOWN: float = 30.0


class MessageListener:
    """Long-poll listener that yields inbound WeChat messages.

    Parameters
    ----------
    client:
        An authenticated :class:`WeChatAPIClient` instance.
    context_store:
        Optional :class:`ContextStore` for caching context tokens extracted
        from incoming messages.  When ``None``, context tokens are still
        logged but not cached.
    config_dir:
        Directory for persisting the sync buffer.  Defaults to
        ``~/.wechat-bot-cli``.  Pass an explicit ``Path`` to override, or
        ``None`` to disable persistence entirely (buffer kept in memory only).
    account_id:
        Bot account identifier used as the key prefix when caching context
        tokens.  Defaults to ``""``.
    """

    def __init__(
        self,
        client: WeChatAPIClient,
        *,
        context_store: ContextStore | None = None,
        config_dir: Path | str | None = ...,
        account_id: str = "",
    ) -> None:
        self._client = client
        self._context_store = context_store
        self._account_id = account_id

        # Resolve config directory.
        if config_dir is ...:
            config_dir = Path.home() / DEFAULT_CONFIG_DIR_NAME
        self._config_dir: Path | None = Path(config_dir) if config_dir is not None else None

        # In-memory sync buffer (base64 string exchanged with the server).
        self._get_updates_buf: str = ""

        # Flag indicating whether the poll loop is active.
        self._running: bool = False

        # Load persisted sync buffer eagerly.
        self._load_sync_buf()

    # ------------------------------------------------------------------
    # Sync buffer persistence
    # ------------------------------------------------------------------

    @property
    def _sync_buf_path(self) -> Path | None:
        if self._config_dir is None:
            return None
        return self._config_dir / SYNC_BUF_FILENAME

    def _load_sync_buf(self) -> None:
        """Load ``get_updates_buf`` from disk into memory."""
        path = self._sync_buf_path
        if path is None or not path.is_file():
            logger.debug("No persisted sync buffer found")
            return
        try:
            raw = path.read_text(encoding="utf-8")
            data = json.loads(raw)
            buf = data.get("get_updates_buf", "")
            if isinstance(buf, str) and buf:
                self._get_updates_buf = buf
                logger.debug(
                    "Loaded sync buffer from %s (%d chars)", path, len(buf)
                )
        except (OSError, json.JSONDecodeError, KeyError):
            logger.warning(
                "Failed to load sync buffer from %s -- starting fresh",
                path,
                exc_info=True,
            )

    def _save_sync_buf(self) -> None:
        """Persist the current ``get_updates_buf`` to disk."""
        path = self._sync_buf_path
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps({"get_updates_buf": self._get_updates_buf}),
                encoding="utf-8",
            )
            tmp.replace(path)
            logger.debug("Persisted sync buffer to %s", path)
        except OSError:
            logger.warning(
                "Failed to persist sync buffer to %s", path, exc_info=True
            )

    # ------------------------------------------------------------------
    # Context token extraction
    # ------------------------------------------------------------------

    def _cache_context_tokens(self, messages: list[WeChatMessage]) -> None:
        """Extract and cache ``context_token`` from each inbound message."""
        if self._context_store is None:
            return
        for msg in messages:
            token = getattr(msg, "context_token", None)
            user_id = getattr(msg, "from_user_id", None)
            if token and user_id:
                self._context_store.set(self._account_id, user_id, token)

    # ------------------------------------------------------------------
    # Single poll
    # ------------------------------------------------------------------

    async def listen_once(self) -> list[WeChatMessage]:
        """Execute a single ``getUpdates`` poll and return the messages.

        This is a low-level building block.  It updates the sync buffer and
        caches context tokens, but does **not** retry on failure -- exceptions
        propagate directly.

        Returns
        -------
        list[WeChatMessage]
            Messages returned by the server (may be empty if the long-poll
            timed out with no new data).
        """
        resp = await self._client.get_updates(
            get_updates_buf=self._get_updates_buf
        )

        # Update the sync buffer with the server's latest value.
        new_buf = getattr(resp, "get_updates_buf", None)
        if new_buf is not None and isinstance(new_buf, str):
            self._get_updates_buf = new_buf
            self._save_sync_buf()

        messages: list[WeChatMessage] = getattr(resp, "msgs", None) or []
        self._cache_context_tokens(messages)

        logger.debug(
            "listen_once: received %d message(s), buf_len=%d",
            len(messages),
            len(self._get_updates_buf),
        )
        return messages

    # ------------------------------------------------------------------
    # Continuous long-poll loop
    # ------------------------------------------------------------------

    async def listen(self) -> AsyncGenerator[WeChatMessage, None]:
        """Continuously long-poll for messages, yielding each one.

        The generator runs indefinitely until :meth:`stop` is called or the
        task is cancelled.  It handles transient errors with exponential
        back-off and treats :class:`SessionExpiredError` as a recoverable
        condition (with a longer cooldown).

        Yields
        ------
        WeChatMessage
            Each inbound message as it arrives.

        Example
        -------
        ::

            listener = MessageListener(client)
            async for msg in listener.listen():
                handle(msg)
        """
        self._running = True
        consecutive_failures: int = 0

        logger.info("MessageListener: starting long-poll loop")

        try:
            while self._running:
                try:
                    messages = await self.listen_once()
                    consecutive_failures = 0  # Reset on success.

                    for msg in messages:
                        yield msg

                        # Re-check the stop flag between yields so we can
                        # exit promptly even if we received a large batch.
                        if not self._running:
                            logger.info(
                                "MessageListener: stop requested mid-batch"
                            )
                            return

                except SessionExpiredError:
                    logger.warning(
                        "MessageListener: session expired -- cooling down "
                        "%.1fs before retry",
                        _SESSION_EXPIRED_COOLDOWN,
                    )
                    await self._interruptible_sleep(_SESSION_EXPIRED_COOLDOWN)
                    # Reset the sync buffer: after session expiry the server
                    # may no longer honour the old buffer.
                    self._get_updates_buf = ""
                    self._save_sync_buf()
                    consecutive_failures = 0

                except WeChatAPIError as exc:
                    consecutive_failures += 1
                    if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                        logger.error(
                            "MessageListener: %d consecutive failures -- "
                            "giving up. Last error: %s",
                            consecutive_failures,
                            exc,
                        )
                        raise
                    delay = min(
                        BACKOFF_DELAY * (2 ** (consecutive_failures - 1)),
                        60.0,
                    )
                    logger.warning(
                        "MessageListener: poll failed (%d/%d): %s -- "
                        "retrying in %.1fs",
                        consecutive_failures,
                        MAX_CONSECUTIVE_FAILURES,
                        exc,
                        delay,
                    )
                    await self._interruptible_sleep(delay)

                except asyncio.CancelledError:
                    logger.info("MessageListener: cancelled")
                    raise

                except Exception as exc:
                    # Unexpected errors: apply the same back-off logic so we
                    # don't spin in a tight loop.
                    consecutive_failures += 1
                    if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                        logger.error(
                            "MessageListener: %d consecutive unexpected "
                            "failures -- giving up. Last error: %s",
                            consecutive_failures,
                            exc,
                        )
                        raise
                    delay = min(
                        RETRY_DELAY * (2 ** (consecutive_failures - 1)),
                        60.0,
                    )
                    logger.warning(
                        "MessageListener: unexpected error (%d/%d): %s "
                        "-- retrying in %.1fs",
                        consecutive_failures,
                        MAX_CONSECUTIVE_FAILURES,
                        exc,
                        delay,
                    )
                    await self._interruptible_sleep(delay)
        finally:
            self._running = False
            logger.info("MessageListener: long-poll loop exited")

    # ------------------------------------------------------------------
    # Halting the listener
    # ------------------------------------------------------------------

    def stop(self) -> None:
        """Signal the :meth:`listen` loop to stop after the current poll.

        This is safe to call from any thread or coroutine.  The loop will
        finish yielding any messages already received in the current batch
        and then exit.
        """
        logger.info("MessageListener: stop requested")
        self._running = False

    @property
    def is_running(self) -> bool:
        """``True`` while the :meth:`listen` loop is active."""
        return self._running

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _interruptible_sleep(self, seconds: float) -> None:
        """Sleep for *seconds*, but wake early if :meth:`stop` is called.

        Checks the ``_running`` flag every 0.5 s so the listener can halt
        promptly even during long cooldowns.
        """
        elapsed = 0.0
        interval = 0.5
        while elapsed < seconds and self._running:
            await asyncio.sleep(min(interval, seconds - elapsed))
            elapsed += interval

    def __repr__(self) -> str:
        return (
            f"<MessageListener running={self._running} "
            f"buf_len={len(self._get_updates_buf)} "
            f"config_dir={self._config_dir}>"
        )
