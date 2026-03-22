"""High-level ``WeChatBot`` facade.

``WeChatBot`` is the primary entry point for programmatic usage of the
wechat-bot-cli library.  It wires together the API client, message sender,
listener, CDN uploader, and context store into a single cohesive object.

Usage::

    bot = WeChatBot(token="...", account_id="...")
    await bot.start()

    # Send a text message
    await bot.send_text("user_123", "Hello!")

    # Listen for incoming messages
    async for msg in bot.listen():
        print(msg.from_user_id, msg.item_list)

    await bot.stop()
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import AsyncGenerator, Optional

from wechat_bot_cli.core.client import WeChatAPIClient
from wechat_bot_cli.core.constants import CDN_BASE_URL, DEFAULT_BASE_URL, DEFAULT_CONFIG_DIR_NAME
from wechat_bot_cli.media.uploader import CDNUploader
from wechat_bot_cli.messaging.context_store import ContextStore
from wechat_bot_cli.messaging.listener import MessageListener
from wechat_bot_cli.messaging.sender import MessageSender, SendResult
from wechat_bot_cli.core.models import WeChatMessage

logger = logging.getLogger(__name__)


class WeChatBot:
    """All-in-one WeChat bot with send, listen, and upload capabilities.

    Parameters
    ----------
    token:
        Bot authentication token from QR login.
    account_id:
        The iLink bot account identifier.
    base_url:
        API base URL.  Defaults to :data:`DEFAULT_BASE_URL`.
    cdn_base_url:
        CDN base URL for media uploads.  Defaults to :data:`CDN_BASE_URL`.
    config_dir:
        Directory for persisting context tokens, sync buffers, etc.
        Defaults to ``~/.wechat-bot-cli``.
    """

    def __init__(
        self,
        *,
        token: str,
        account_id: str = "",
        base_url: str = DEFAULT_BASE_URL,
        cdn_base_url: str = CDN_BASE_URL,
        config_dir: Optional[Path] = None,
    ) -> None:
        self._token = token
        self._account_id = account_id
        self._base_url = base_url
        self._cdn_base_url = cdn_base_url
        self._config_dir = config_dir or Path.home() / DEFAULT_CONFIG_DIR_NAME

        # Core components (initialised in start()).
        self._client: Optional[WeChatAPIClient] = None
        self._context_store: Optional[ContextStore] = None
        self._sender: Optional[MessageSender] = None
        self._listener: Optional[MessageListener] = None
        self._uploader: Optional[CDNUploader] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Initialise all sub-components.

        Must be called before sending or listening.
        """
        self._client = WeChatAPIClient(
            base_url=self._base_url,
            token=self._token,
        )
        self._context_store = ContextStore(persist_dir=self._config_dir)
        self._sender = MessageSender(
            self._client,
            self._context_store,
            account_id=self._account_id,
        )
        self._listener = MessageListener(
            self._client,
            context_store=self._context_store,
            config_dir=self._config_dir,
            account_id=self._account_id,
        )
        self._uploader = CDNUploader(
            self._client,
            cdn_base_url=self._cdn_base_url,
        )
        logger.info("WeChatBot started: account=%s", self._account_id)

    async def stop(self) -> None:
        """Shut down the bot gracefully, releasing all resources."""
        if self._listener and self._listener.is_running:
            self._listener.stop()
        if self._client:
            await self._client.close()
            self._client = None
        logger.info("WeChatBot stopped")

    async def __aenter__(self) -> "WeChatBot":
        await self.start()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.stop()

    # ------------------------------------------------------------------
    # Convenience accessors
    # ------------------------------------------------------------------

    @property
    def client(self) -> WeChatAPIClient:
        """The underlying API client."""
        if self._client is None:
            raise RuntimeError("Bot not started; call start() first")
        return self._client

    @property
    def sender(self) -> MessageSender:
        """The message sender."""
        if self._sender is None:
            raise RuntimeError("Bot not started; call start() first")
        return self._sender

    @property
    def listener(self) -> MessageListener:
        """The message listener."""
        if self._listener is None:
            raise RuntimeError("Bot not started; call start() first")
        return self._listener

    @property
    def uploader(self) -> CDNUploader:
        """The CDN uploader."""
        if self._uploader is None:
            raise RuntimeError("Bot not started; call start() first")
        return self._uploader

    @property
    def context_store(self) -> ContextStore:
        """The context token store."""
        if self._context_store is None:
            raise RuntimeError("Bot not started; call start() first")
        return self._context_store

    # ------------------------------------------------------------------
    # Messaging shortcuts
    # ------------------------------------------------------------------

    async def send_text(self, to: str, text: str, **kwargs) -> "SendResult":
        """Send a plain-text message."""
        return await self.sender.send_text(to, text, **kwargs)

    async def send_image(
        self, to: str, upload_info: dict, **kwargs
    ) -> "SendResult":
        """Send an image that has already been uploaded to the CDN."""
        return await self.sender.send_image(to, upload_info, **kwargs)

    async def send_video(
        self, to: str, upload_info: dict, **kwargs
    ) -> "SendResult":
        """Send a video that has already been uploaded to the CDN."""
        return await self.sender.send_video(to, upload_info, **kwargs)

    async def send_file(
        self, to: str, upload_info: dict, **kwargs
    ) -> "SendResult":
        """Send a file attachment that has already been uploaded to the CDN."""
        return await self.sender.send_file(to, upload_info, **kwargs)

    async def listen(self) -> AsyncGenerator[WeChatMessage, None]:
        """Continuously yield inbound messages."""
        async for msg in self.listener.listen():
            yield msg
