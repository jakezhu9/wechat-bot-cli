"""Core API client, models, constants, crypto, and exceptions."""

from wechat_bot_cli.core.client import WeChatAPIClient
from wechat_bot_cli.core.constants import (
    ACCOUNTS_SUBDIR,
    BACKOFF_DELAY,
    CDN_BASE_URL,
    CDN_UPLOAD_MAX_RETRIES,
    DEFAULT_BASE_URL,
    DEFAULT_CONFIG_DIR_NAME,
    MAX_CONSECUTIVE_FAILURES,
    MAX_QR_REFRESH_COUNT,
    MEDIA_MAX_BYTES,
    RETRY_DELAY,
    SYNC_BUF_FILENAME,
)
from wechat_bot_cli.core.crypto import aes_ecb_padded_size, decrypt_aes_ecb, encrypt_aes_ecb
from wechat_bot_cli.core.exceptions import (
    LoginTimeoutError,
    MediaTooLargeError,
    QRCodeExpiredError,
    SessionExpiredError,
    UploadError,
    WeChatAPIError,
    WeChatCLIError,
)
from wechat_bot_cli.core.models import (
    CDNMedia,
    FileItem,
    GetUpdatesResponse,
    ImageItem,
    MessageItem,
    MessageItemType,
    TextItem,
    UploadMediaType,
    VideoItem,
    WeChatMessage,
)

__all__ = [
    # client
    "WeChatAPIClient",
    # constants
    "ACCOUNTS_SUBDIR",
    "BACKOFF_DELAY",
    "CDN_BASE_URL",
    "CDN_UPLOAD_MAX_RETRIES",
    "DEFAULT_BASE_URL",
    "DEFAULT_CONFIG_DIR_NAME",
    "MAX_CONSECUTIVE_FAILURES",
    "MAX_QR_REFRESH_COUNT",
    "MEDIA_MAX_BYTES",
    "RETRY_DELAY",
    "SYNC_BUF_FILENAME",
    # crypto
    "aes_ecb_padded_size",
    "decrypt_aes_ecb",
    "encrypt_aes_ecb",
    # exceptions
    "LoginTimeoutError",
    "MediaTooLargeError",
    "QRCodeExpiredError",
    "SessionExpiredError",
    "UploadError",
    "WeChatAPIError",
    "WeChatCLIError",
    # models
    "CDNMedia",
    "FileItem",
    "GetUpdatesResponse",
    "ImageItem",
    "MessageItem",
    "MessageItemType",
    "TextItem",
    "UploadMediaType",
    "VideoItem",
    "WeChatMessage",
]
