"""Low-level HTTP client for the WeChat iLink bot API.

``WeChatAPIClient`` wraps :mod:`httpx` and provides typed methods for every
endpoint used by the CLI: QR login, message sending, long-poll updates,
CDN upload URL generation, config retrieval, and typing indicators.

All methods are ``async`` and raise :class:`~wechat_bot_cli.exceptions.WeChatAPIError`
(or a subclass) on transport/server errors.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import struct
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

import httpx

from wechat_bot_cli.core.constants import DEFAULT_BASE_URL
from wechat_bot_cli.core.exceptions import SessionExpiredError, WeChatAPIError
from wechat_bot_cli.core.models import GetUpdatesResponse, MessageItem, WeChatMessage

logger = logging.getLogger(__name__)

# Timeout configuration for the long-poll ``getUpdates`` call.
_LONG_POLL_TIMEOUT = httpx.Timeout(120.0, connect=10.0)
# Default timeout for everything else.
_DEFAULT_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


def _read_channel_version() -> str:
    """Read the package version for the base_info payload."""
    try:
        from wechat_bot_cli import __version__
        return __version__
    except Exception:
        return "unknown"


def _build_base_info() -> Dict[str, str]:
    """Build the ``base_info`` payload included in every API request."""
    return {"channel_version": _read_channel_version()}


def _random_wechat_uin() -> str:
    """Generate a random X-WECHAT-UIN header value (matches the TS implementation)."""
    raw = os.urandom(4)
    uint32 = struct.unpack(">I", raw)[0]
    return base64.b64encode(str(uint32).encode("utf-8")).decode("ascii")


class WeChatAPIClient:
    """Async HTTP client for the iLink bot API.

    Parameters
    ----------
    base_url:
        API base URL.  Defaults to :data:`DEFAULT_BASE_URL`.
    token:
        Bot authentication token (from QR login).  When ``None``, only
        unauthenticated endpoints (QR code) can be called.
    """

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        token: Optional[str] = None,
    ) -> None:
        self._base_url = base_url.rstrip("/") + "/"
        self._token = token
        self._http = httpx.AsyncClient(
            timeout=_DEFAULT_TIMEOUT,
            follow_redirects=True,
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """Close the underlying HTTP client and release resources."""
        await self._http.aclose()

    async def __aenter__(self) -> "WeChatAPIClient":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_headers(self, body: str = "") -> Dict[str, str]:
        """Build request headers matching the iLink API expectations."""
        headers: Dict[str, str] = {
            "Content-Type": "application/json",
            "AuthorizationType": "ilink_bot_token",
            "X-WECHAT-UIN": _random_wechat_uin(),
        }
        if body:
            headers["Content-Length"] = str(len(body.encode("utf-8")))
        if self._token and self._token.strip():
            headers["Authorization"] = f"Bearer {self._token.strip()}"
        return headers

    async def _request(
        self,
        method: str,
        endpoint: str,
        *,
        json_body: Any = None,
        params: Optional[Dict[str, str]] = None,
        timeout: httpx.Timeout | None = None,
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Send an API request and return the parsed JSON body.

        Raises
        ------
        SessionExpiredError
            When the server returns a 401 status.
        WeChatAPIError
            On any other non-2xx status or transport error.
        """
        url = f"{self._base_url}{endpoint}"
        if params:
            url = f"{url}?{urlencode(params)}"

        body_str = json.dumps(json_body) if json_body is not None else ""
        headers = self._build_headers(body_str)
        if extra_headers:
            headers.update(extra_headers)

        try:
            if method.upper() == "GET":
                response = await self._http.get(
                    url,
                    headers=headers,
                    timeout=timeout or _DEFAULT_TIMEOUT,
                )
            else:
                response = await self._http.post(
                    url,
                    content=body_str if body_str else None,
                    headers=headers,
                    timeout=timeout or _DEFAULT_TIMEOUT,
                )
        except httpx.HTTPError as exc:
            raise WeChatAPIError(f"HTTP transport error: {exc}") from exc

        if response.status_code == 401:
            raise SessionExpiredError(
                "Session expired (HTTP 401). Please log in again.",
                status_code=401,
            )

        if response.status_code >= 400:
            raise WeChatAPIError(
                f"API error: HTTP {response.status_code} - "
                f"{response.text[:500]}",
                status_code=response.status_code,
            )

        try:
            return response.json()
        except Exception:
            # Some endpoints return empty 200 responses.
            return {}

    # ------------------------------------------------------------------
    # QR Login endpoints
    # ------------------------------------------------------------------

    async def get_qr_code(self, bot_type: str = "3") -> Dict[str, Any]:
        """Request a new QR code for login.

        Parameters
        ----------
        bot_type:
            The ``bot_type`` parameter.  Defaults to ``"3"``.

        Returns
        -------
        dict
            Contains at least ``qrcode`` and ``qrcode_img_content`` keys.
        """
        return await self._request(
            "GET",
            "ilink/bot/get_bot_qrcode",
            params={"bot_type": bot_type},
        )

    async def get_qr_status(self, qr_code: str) -> Dict[str, Any]:
        """Poll the status of a QR code login.

        Parameters
        ----------
        qr_code:
            The ``qrcode`` value returned by :meth:`get_qr_code`.

        Returns
        -------
        dict
            Contains ``status`` (wait/scaned/confirmed/expired) and on
            confirmation: ``bot_token``, ``ilink_bot_id``, ``ilink_user_id``.
        """
        return await self._request(
            "GET",
            "ilink/bot/get_qrcode_status",
            params={"qrcode": qr_code},
            extra_headers={"iLink-App-ClientVersion": "1"},
        )

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    async def get_config(
        self,
        *,
        ilink_user_id: str = "",
        context_token: str = "",
    ) -> Dict[str, Any]:
        """Fetch the bot configuration from the server."""
        body: Dict[str, Any] = {"base_info": _build_base_info()}
        if ilink_user_id:
            body["ilink_user_id"] = ilink_user_id
        if context_token:
            body["context_token"] = context_token
        return await self._request("POST", "ilink/bot/getconfig", json_body=body)

    # ------------------------------------------------------------------
    # Messaging
    # ------------------------------------------------------------------

    async def send_message(
        self,
        *,
        to: str,
        item_list: List[MessageItem],
        context_token: Optional[str] = None,
        client_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Send a message to a WeChat user."""
        items_payload = [item.to_dict() for item in item_list]

        msg: Dict[str, Any] = {
            "from_user_id": "",
            "to_user_id": to,
            "message_type": 2,   # BOT
            "message_state": 2,  # FINISH
            "item_list": items_payload,
        }
        if context_token:
            msg["context_token"] = context_token
        if client_id:
            msg["client_id"] = client_id

        body: Dict[str, Any] = {
            "msg": msg,
            "base_info": _build_base_info(),
        }

        return await self._request("POST", "ilink/bot/sendmessage", json_body=body)

    async def send_typing(
        self,
        *,
        ilink_user_id: str,
        typing_ticket: str = "",
        status: int = 1,
    ) -> Dict[str, Any]:
        """Send a typing indicator to a user.

        Parameters
        ----------
        ilink_user_id:
            The user to show the typing indicator to.
        typing_ticket:
            Ticket obtained from ``get_config``.
        status:
            1 = typing (default), 2 = cancel typing.
        """
        return await self._request(
            "POST",
            "ilink/bot/sendtyping",
            json_body={
                "ilink_user_id": ilink_user_id,
                "typing_ticket": typing_ticket,
                "status": status,
                "base_info": _build_base_info(),
            },
        )

    # ------------------------------------------------------------------
    # Long-poll updates
    # ------------------------------------------------------------------

    async def get_updates(
        self,
        *,
        get_updates_buf: str = "",
    ) -> GetUpdatesResponse:
        """Long-poll for new inbound messages."""
        body: Dict[str, Any] = {"base_info": _build_base_info()}
        if get_updates_buf:
            body["get_updates_buf"] = get_updates_buf

        raw = await self._request(
            "POST",
            "ilink/bot/getupdates",
            json_body=body,
            timeout=_LONG_POLL_TIMEOUT,
        )

        # Parse messages from the raw response.
        msgs: List[WeChatMessage] = []
        for raw_msg in raw.get("msgs", []):
            raw_items = raw_msg.get("item_list") or []
            parsed_items = [MessageItem.from_raw(ri) for ri in raw_items]

            msg = WeChatMessage(
                from_user_id=raw_msg.get("from_user_id", ""),
                to_user_id=raw_msg.get("to_user_id", ""),
                context_token=raw_msg.get("context_token", ""),
                item_list=parsed_items,
                raw=raw_msg,
            )
            msgs.append(msg)

        return GetUpdatesResponse(
            get_updates_buf=raw.get("get_updates_buf", ""),
            msgs=msgs,
            raw=raw,
        )

    # ------------------------------------------------------------------
    # CDN / Upload
    # ------------------------------------------------------------------

    async def get_upload_url(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Request a signed upload URL for CDN media upload."""
        body = {**params, "base_info": _build_base_info()}
        return await self._request(
            "POST",
            "ilink/bot/getuploadurl",
            json_body=body,
        )

    async def cdn_upload_raw(
        self,
        *,
        upload_url: str,
        ciphertext: bytes,
    ) -> str:
        """POST encrypted bytes to the CDN and return the download param."""
        headers = {
            "Content-Type": "application/octet-stream",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"

        try:
            response = await self._http.post(
                upload_url,
                content=ciphertext,
                headers=headers,
                timeout=httpx.Timeout(120.0, connect=10.0),
            )
        except httpx.HTTPError as exc:
            raise WeChatAPIError(f"CDN upload transport error: {exc}") from exc

        if response.status_code >= 400:
            raise WeChatAPIError(
                f"CDN upload failed: HTTP {response.status_code}",
                status_code=response.status_code,
            )

        return response.headers.get("x-encrypted-param", "")
