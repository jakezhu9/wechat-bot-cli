"""Custom exception hierarchy for wechat-bot-cli."""

from __future__ import annotations


class WeChatCLIError(Exception):
    """Base exception for all wechat-bot-cli errors."""


# ---------------------------------------------------------------------------
# API / transport errors
# ---------------------------------------------------------------------------


class WeChatAPIError(WeChatCLIError):
    """An error returned by the iLink bot API or an HTTP transport failure."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class SessionExpiredError(WeChatAPIError):
    """Raised when the server indicates that the bot session has expired."""


# ---------------------------------------------------------------------------
# Login errors
# ---------------------------------------------------------------------------


class LoginTimeoutError(WeChatCLIError):
    """The QR-code login flow did not complete within the allowed time."""


class QRCodeExpiredError(WeChatCLIError):
    """The QR code expired too many times without being scanned."""


# ---------------------------------------------------------------------------
# Media errors
# ---------------------------------------------------------------------------


class MediaTooLargeError(WeChatCLIError):
    """The file exceeds the maximum allowed upload size."""


class UploadError(WeChatCLIError):
    """CDN upload failed after all retry attempts."""
