"""CDN download and decryption for WeChat iLink bot media.

Downloads AES-128-ECB encrypted blobs from the WeChat CDN and decrypts
them using a key that was established during upload or received in an
inbound message payload.
"""

from __future__ import annotations

import base64
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Union
from urllib.parse import quote

from wechat_bot_cli.core.constants import CDN_BASE_URL
from wechat_bot_cli.core.crypto import decrypt_aes_ecb

if TYPE_CHECKING:
    import httpx

logger = logging.getLogger(__name__)

# Pre-compiled pattern for recognising a hex-encoded 16-byte key.
_HEX_32_RE = re.compile(r"^[0-9a-fA-F]{32}$")


class CDNDownloader:
    """Download and decrypt media files from the WeChat CDN.

    The downloader manages its own :class:`httpx.AsyncClient` for HTTP
    requests.  You may inject a pre-configured client (e.g. with a custom
    timeout or proxy) via *http_client*; otherwise a default client is
    created lazily on first use and closed when :meth:`aclose` is called.

    Parameters
    ----------
    cdn_base_url:
        Base URL of the CDN gateway.  Defaults to :data:`CDN_BASE_URL`.
    http_client:
        Optional pre-configured :class:`httpx.AsyncClient`.  When
        provided the downloader will *not* close it automatically.

    Example
    -------
    ::

        dl = CDNDownloader()
        plaintext = await dl.download_and_decrypt(
            encrypted_query_param="...",
            aes_key="<hex-or-base64>",
            output_path="/tmp/photo.jpg",
        )
    """

    def __init__(
        self,
        cdn_base_url: str = CDN_BASE_URL,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._cdn_base_url = cdn_base_url.rstrip("/")
        self._external_client = http_client is not None
        self._http_client = http_client
        # Lazy-import so the module loads even when httpx is not yet
        # importable at definition time (it is always a hard dependency of
        # the SDK).
        self._httpx = __import__("httpx")

    # -- lifecycle ----------------------------------------------------------

    async def _get_client(self) -> httpx.AsyncClient:
        """Return (and lazily create) the HTTP client."""
        if self._http_client is None:
            self._http_client = self._httpx.AsyncClient(
                timeout=self._httpx.Timeout(60.0, connect=10.0),
                follow_redirects=True,
            )
        return self._http_client

    async def aclose(self) -> None:
        """Close the internally-managed HTTP client, if any.

        If the client was injected externally via the constructor, this
        method is a no-op (the caller retains ownership).
        """
        if self._http_client is not None and not self._external_client:
            await self._http_client.aclose()
            self._http_client = None

    async def __aenter__(self) -> CDNDownloader:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    # -- public API ---------------------------------------------------------

    def build_download_url(self, encrypted_query_param: str) -> str:
        """Construct the full CDN download URL.

        Parameters
        ----------
        encrypted_query_param:
            Opaque string from ``CDNMedia.encrypt_query_param`` or from
            the ``x-encrypted-param`` header returned by the CDN on upload.

        Returns
        -------
        str
            Complete URL suitable for an HTTP GET.
        """
        return (
            f"{self._cdn_base_url}/download"
            f"?encrypted_query_param={quote(encrypted_query_param, safe='')}"
        )

    async def download_and_decrypt(
        self,
        encrypted_query_param: str,
        aes_key: Union[str, bytes],
        output_path: Union[str, Path, None] = None,
    ) -> bytes:
        """Download a media blob from the CDN and decrypt it.

        Parameters
        ----------
        encrypted_query_param:
            The opaque CDN download token (``CDNMedia.encrypt_query_param``).
        aes_key:
            The AES-128-ECB key.  Accepted formats:

            * 16 raw bytes (``bytes``).
            * 32-character hex string (``str``).
            * Base64 of 16 raw bytes (``str``, 24 chars padded).
            * Base64 of a 32-character hex string (``str``, 44 chars
              padded) -- this double-encoding is used by some WeChat
              protocol paths.

            See :meth:`_parse_aes_key` for the full parsing logic.
        output_path:
            When set, the decrypted plaintext is also written to this
            file path.  Parent directories are created automatically.

        Returns
        -------
        bytes
            The decrypted plaintext.

        Raises
        ------
        httpx.HTTPStatusError
            On non-2xx responses from the CDN.
        ValueError
            If *aes_key* cannot be parsed into a valid 16-byte key.
        """
        key_bytes = self._parse_aes_key(aes_key)
        url = self.build_download_url(encrypted_query_param)

        logger.debug("download_and_decrypt: fetching %s...", url[:120])

        client = await self._get_client()
        response = await client.get(url)
        response.raise_for_status()

        ciphertext = response.content
        logger.debug(
            "download_and_decrypt: downloaded %d bytes, decrypting",
            len(ciphertext),
        )

        plaintext = decrypt_aes_ecb(ciphertext, key_bytes)
        logger.debug("download_and_decrypt: decrypted to %d bytes", len(plaintext))

        if output_path is not None:
            out = Path(output_path)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(plaintext)
            logger.debug("download_and_decrypt: saved to %s", out)

        return plaintext

    # -- key parsing --------------------------------------------------------

    @staticmethod
    def _parse_aes_key(key: Union[str, bytes]) -> bytes:
        """Normalise an AES key from various protocol formats into 16 raw bytes.

        The WeChat protocol uses two key-encoding conventions depending on
        the message type:

        1. **Base64 of 16 raw bytes** -- typical for images.  Base64
           decoding yields exactly 16 bytes which are used directly.
        2. **Base64 of a 32-character hex string** -- typical for files,
           voice, and video.  Base64 decoding yields 32 ASCII characters
           that represent the hex encoding of the 16-byte key.

        This method also accepts the pre-decoded forms (raw ``bytes`` of
        length 16, or a 32-char hex ``str``) for convenience.

        Parameters
        ----------
        key:
            The AES key in one of the accepted formats.

        Returns
        -------
        bytes
            A 16-byte AES key.

        Raises
        ------
        ValueError
            If the key cannot be interpreted as a 16-byte AES key.
        """
        # ---- bytes input ---------------------------------------------------
        if isinstance(key, bytes):
            if len(key) == 16:
                return key
            # Could be a hex-encoded key passed as bytes.
            if len(key) == 32:
                try:
                    ascii_str = key.decode("ascii")
                    if _HEX_32_RE.match(ascii_str):
                        return bytes.fromhex(ascii_str)
                except (UnicodeDecodeError, ValueError):
                    pass
            raise ValueError(
                f"bytes key must be 16 raw bytes or 32 hex-ASCII bytes; "
                f"got {len(key)} bytes"
            )

        # ---- str input -----------------------------------------------------
        key_str: str = key.strip()

        # 1) 32-char hex string -> direct decode.
        if _HEX_32_RE.match(key_str):
            return bytes.fromhex(key_str)

        # 2) Try base64 decode.
        try:
            decoded = base64.b64decode(key_str)
        except Exception as exc:
            raise ValueError(
                f"aes_key string is neither 32-char hex nor valid base64: "
                f"{key_str!r}"
            ) from exc

        if len(decoded) == 16:
            # base64(raw 16 bytes)
            return decoded

        if len(decoded) == 32:
            # base64(hex string of 16 bytes)
            try:
                ascii_hex = decoded.decode("ascii")
                if _HEX_32_RE.match(ascii_hex):
                    return bytes.fromhex(ascii_hex)
            except (UnicodeDecodeError, ValueError):
                pass

        raise ValueError(
            f"aes_key must decode to 16 raw bytes or a 32-char hex string; "
            f"base64 decoded to {len(decoded)} bytes from {key_str!r}"
        )
