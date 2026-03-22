"""High-level message sending facade.

``MessageSender`` wraps the low-level :class:`~wechat_bot_cli.client.WeChatAPIClient`
with convenience methods for each media type and automatic ``context_token``
resolution through a :class:`~wechat_bot_cli.messaging.context_store.ContextStore`.

Usage::

    sender = MessageSender(client, context_store)
    await sender.send_text("user_123", "Hello!")
    await sender.send_image("user_123", upload_info)
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any, Sequence

from wechat_bot_cli.core.client import WeChatAPIClient
from wechat_bot_cli.messaging.context_store import ContextStore
from wechat_bot_cli.core.models import (
    CDNMedia,
    FileItem,
    ImageItem,
    MessageItem,
    MessageItemType,
    TextItem,
    VideoItem,
)

logger = logging.getLogger(__name__)

# Prefix used in generated client IDs so they are recognisable in logs.
_CLIENT_ID_PREFIX = "wechat-bot-cli"


@dataclass
class SendResult:
    """Lightweight result returned after a successful send."""

    client_id: str
    """The unique client-generated message id used for the request."""


class MessageSender:
    """Builds and sends messages through the WeChat iLink bot API.

    Parameters
    ----------
    client:
        An authenticated :class:`WeChatAPIClient` instance.
    context_store:
        A :class:`ContextStore` used to resolve ``context_token`` when the
        caller does not supply one explicitly.
    account_id:
        The bot account identifier.  Used as the key when looking up cached
        context tokens.  Defaults to ``""`` which is fine for single-account
        deployments.
    """

    def __init__(
        self,
        client: WeChatAPIClient,
        context_store: ContextStore,
        *,
        account_id: str = "",
    ) -> None:
        self._client = client
        self._context_store = context_store
        self._account_id = account_id

    # ------------------------------------------------------------------
    # Public send methods
    # ------------------------------------------------------------------

    async def send_text(
        self,
        to: str,
        text: str,
        *,
        context_token: str | None = None,
    ) -> SendResult:
        """Send a plain-text message.

        Parameters
        ----------
        to:
            Recipient ``user_id``.
        text:
            Message body.
        context_token:
            Optional explicit token.  When ``None``, the store is consulted.

        Returns
        -------
        SendResult
            Contains the ``client_id`` that was assigned to the message.

        Raises
        ------
        ValueError
            If *text* is empty.
        WeChatAPIError
            On transport / server errors.
        """
        if not text:
            raise ValueError("send_text: text must not be empty")

        item = MessageItem(
            type=MessageItemType.TEXT,
            text_item=TextItem(text=text),
        )
        return await self._send_items(to, [item], context_token=context_token)

    async def send_image(
        self,
        to: str,
        upload_info: dict[str, Any],
        *,
        context_token: str | None = None,
    ) -> SendResult:
        """Send an image that has already been uploaded to the CDN.

        Parameters
        ----------
        to:
            Recipient ``user_id``.
        upload_info:
            Dictionary returned by the CDN upload step.  Expected keys:

            * ``download_encrypted_query_param`` -- CDN download param
            * ``aes_key`` -- base64-encoded AES key
            * ``file_size_ciphertext`` -- encrypted file size (used as ``mid_size``)
            * ``thumb_download_encrypted_query_param`` (optional) -- thumb CDN param
            * ``thumb_aes_key`` (optional) -- thumb AES key (base64)
        context_token:
            Optional explicit token.
        """
        media = CDNMedia(
            encrypt_query_param=upload_info["download_encrypted_query_param"],
            aes_key=upload_info["aes_key"],
            encrypt_type=1,
        )

        thumb_media: CDNMedia | None = None
        if "thumb_download_encrypted_query_param" in upload_info:
            thumb_media = CDNMedia(
                encrypt_query_param=upload_info["thumb_download_encrypted_query_param"],
                aes_key=upload_info.get("thumb_aes_key", ""),
                encrypt_type=1,
            )

        image_item = ImageItem(
            media=media,
            thumb_media=thumb_media,
            mid_size=upload_info.get("file_size_ciphertext"),
        )
        item = MessageItem(type=MessageItemType.IMAGE, image_item=image_item)
        return await self._send_items(to, [item], context_token=context_token)

    async def send_video(
        self,
        to: str,
        upload_info: dict[str, Any],
        *,
        context_token: str | None = None,
    ) -> SendResult:
        """Send a video that has already been uploaded to the CDN.

        Parameters
        ----------
        to:
            Recipient ``user_id``.
        upload_info:
            Dictionary with CDN upload result.  Expected keys:

            * ``download_encrypted_query_param``
            * ``aes_key`` (base64)
            * ``file_size_ciphertext`` -- used as ``video_size``
            * ``thumb_download_encrypted_query_param`` (optional)
            * ``thumb_aes_key`` (optional)
        context_token:
            Optional explicit token.
        """
        media = CDNMedia(
            encrypt_query_param=upload_info["download_encrypted_query_param"],
            aes_key=upload_info["aes_key"],
            encrypt_type=1,
        )

        thumb_media: CDNMedia | None = None
        if "thumb_download_encrypted_query_param" in upload_info:
            thumb_media = CDNMedia(
                encrypt_query_param=upload_info["thumb_download_encrypted_query_param"],
                aes_key=upload_info.get("thumb_aes_key", ""),
                encrypt_type=1,
            )

        video_item = VideoItem(
            media=media,
            video_size=upload_info.get("file_size_ciphertext"),
            thumb_media=thumb_media,
        )
        item = MessageItem(type=MessageItemType.VIDEO, video_item=video_item)
        return await self._send_items(to, [item], context_token=context_token)

    async def send_file(
        self,
        to: str,
        upload_info: dict[str, Any],
        *,
        file_name: str | None = None,
        context_token: str | None = None,
    ) -> SendResult:
        """Send a file attachment that has already been uploaded to the CDN.

        Parameters
        ----------
        to:
            Recipient ``user_id``.
        upload_info:
            Dictionary with CDN upload result.  Expected keys:

            * ``download_encrypted_query_param``
            * ``aes_key`` (base64)
            * ``file_name`` (optional, fallback if *file_name* arg omitted)
            * ``file_size`` -- plaintext size in bytes
        file_name:
            Override for the filename shown to the recipient.  Falls back to
            ``upload_info["file_name"]``.
        context_token:
            Optional explicit token.
        """
        media = CDNMedia(
            encrypt_query_param=upload_info["download_encrypted_query_param"],
            aes_key=upload_info["aes_key"],
            encrypt_type=1,
        )
        resolved_name = file_name or upload_info.get("file_name", "file")
        file_item = FileItem(
            media=media,
            file_name=resolved_name,
            len=str(upload_info.get("file_size", 0)),
        )
        item = MessageItem(type=MessageItemType.FILE, file_item=file_item)
        return await self._send_items(to, [item], context_token=context_token)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_context_token(
        self,
        to: str,
        explicit_token: str | None,
    ) -> str | None:
        """Return the context token to use for an outbound message.

        Priority:
        1. Explicitly provided token (caller override).
        2. Token cached in the :class:`ContextStore` for ``(account_id, to)``.
        3. ``None`` -- the caller did not provide one and none is cached.
        """
        if explicit_token is not None:
            return explicit_token
        cached = self._context_store.get(self._account_id, to)
        if cached is not None:
            logger.debug(
                "Resolved context_token from store for account=%s user=%s",
                self._account_id,
                to,
            )
        return cached

    @staticmethod
    def _generate_client_id() -> str:
        """Generate a unique, collision-resistant client message id.

        Format: ``wechat-bot-cli-<uuid4>``
        """
        return f"{_CLIENT_ID_PREFIX}-{uuid.uuid4().hex}"

    async def _send_items(
        self,
        to: str,
        item_list: Sequence[MessageItem],
        *,
        context_token: str | None = None,
    ) -> SendResult:
        """Low-level helper: build and dispatch a ``sendMessage`` request.

        Each call generates a fresh ``client_id`` and resolves the context
        token, then delegates to :pymethod:`WeChatAPIClient.send_message`.
        """
        resolved_token = self._resolve_context_token(to, context_token)
        if resolved_token is None:
            logger.warning(
                "send_items: no context_token available for to=%s -- "
                "the server may reject or mis-route this message",
                to,
            )

        client_id = self._generate_client_id()
        logger.debug(
            "send_items: to=%s client_id=%s items=%d context_token=%s",
            to,
            client_id,
            len(item_list),
            "present" if resolved_token else "missing",
        )

        try:
            await self._client.send_message(
                to=to,
                item_list=list(item_list),
                context_token=resolved_token,
                client_id=client_id,
            )
        except Exception:
            logger.error(
                "send_items: failed to=%s client_id=%s",
                to,
                client_id,
                exc_info=True,
            )
            raise

        logger.debug("send_items: success to=%s client_id=%s", to, client_id)
        return SendResult(client_id=client_id)
