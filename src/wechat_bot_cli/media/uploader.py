"""CDN upload pipeline for WeChat iLink bot media.

Implements the full upload flow:

1. Read local file, enforce size limit.
2. Auto-detect :class:`UploadMediaType` from file extension.
3. Generate a random AES-128 key and unique *filekey*.
4. Compute plaintext MD5, raw size, and AES-ECB padded ciphertext size.
5. Optionally generate and encrypt a thumbnail for images (requires Pillow).
6. Obtain a signed upload URL from the iLink API (``get_upload_url``).
7. AES-128-ECB encrypt the file data.
8. POST the ciphertext to the CDN.
9. Return an :class:`UploadResult` with all data needed to reference the
   uploaded media in a ``SendMessage`` request.
"""

from __future__ import annotations

import hashlib
import io
import logging
import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Union
from urllib.parse import quote

from wechat_bot_cli.core.constants import CDN_BASE_URL, CDN_UPLOAD_MAX_RETRIES, MEDIA_MAX_BYTES
from wechat_bot_cli.core.crypto import aes_ecb_padded_size, encrypt_aes_ecb
from wechat_bot_cli.core.exceptions import MediaTooLargeError, UploadError
from wechat_bot_cli.media.mime_utils import guess_media_type
from wechat_bot_cli.core.models import UploadMediaType

if TYPE_CHECKING:
    from wechat_bot_cli.core.client import WeChatAPIClient

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Thumbnail helper (soft dependency on Pillow)
# ---------------------------------------------------------------------------

_THUMB_MAX_SIZE: int = 120  # pixels for the longest side
_THUMB_JPEG_QUALITY: int = 60

_PIL_AVAILABLE: bool = False
try:
    from PIL import Image  # type: ignore[import-untyped]

    _PIL_AVAILABLE = True
except ImportError:  # pragma: no cover
    pass


def _make_thumbnail(plaintext: bytes) -> bytes | None:
    """Create a JPEG thumbnail from raw image bytes.

    Returns ``None`` when Pillow is unavailable or the image cannot be
    decoded.
    """
    if not _PIL_AVAILABLE:
        return None
    try:
        img = Image.open(io.BytesIO(plaintext))
        img.thumbnail((_THUMB_MAX_SIZE, _THUMB_MAX_SIZE))
        buf = io.BytesIO()
        # Convert palette/RGBA to RGB for JPEG output.
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        img.save(buf, format="JPEG", quality=_THUMB_JPEG_QUALITY)
        return buf.getvalue()
    except Exception:  # noqa: BLE001
        logger.debug("thumbnail generation failed; proceeding without thumbnail", exc_info=True)
        return None


# ---------------------------------------------------------------------------
# UploadResult
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class UploadResult:
    """Outcome of a successful CDN upload.

    All fields needed to populate ``ImageItem``, ``VideoItem``, or
    ``FileItem`` in a ``SendMessage`` request are included.

    Attributes
    ----------
    filekey:
        Unique identifier for the uploaded blob on the CDN.
    download_encrypted_query_param:
        Opaque string returned by the CDN in the ``x-encrypted-param``
        response header.  Used as ``CDNMedia.encrypt_query_param`` when
        constructing a download URL.
    aes_key_hex:
        The 16-byte AES-128-ECB key encoded as a 32-character hex string.
        For ``CDNMedia.aes_key`` convert with
        ``base64.b64encode(bytes.fromhex(aes_key_hex))``.
    file_size:
        Original plaintext file size in bytes.
    file_size_ciphertext:
        Size of the encrypted blob (AES-128-ECB with PKCS#7 padding).
    thumb_download_encrypted_query_param:
        Download query param for the thumbnail, or ``None`` if no
        thumbnail was uploaded.
    """

    filekey: str
    download_encrypted_query_param: str
    aes_key_hex: str
    file_size: int
    file_size_ciphertext: int
    thumb_download_encrypted_query_param: str | None = field(default=None)


# ---------------------------------------------------------------------------
# CDNUploader
# ---------------------------------------------------------------------------


class CDNUploader:
    """Upload local files to the WeChat CDN with AES-128-ECB encryption.

    Parameters
    ----------
    client:
        An authenticated :class:`WeChatAPIClient` instance used to obtain
        signed upload URLs from the iLink API.
    cdn_base_url:
        Base URL of the CDN gateway.  Defaults to :data:`CDN_BASE_URL`.

    Example
    -------
    ::

        uploader = CDNUploader(client)
        result = await uploader.upload_file(
            "/tmp/photo.jpg",
            to_user_id="wxid_abc123",
        )
        print(result.download_encrypted_query_param)
    """

    def __init__(
        self,
        client: WeChatAPIClient,
        cdn_base_url: str = CDN_BASE_URL,
    ) -> None:
        self._client = client
        self._cdn_base_url = (cdn_base_url or CDN_BASE_URL).rstrip("/")

    # -- public API ---------------------------------------------------------

    async def upload_file(
        self,
        file_path: Union[str, Path],
        to_user_id: str,
        media_type: UploadMediaType | None = None,
    ) -> UploadResult:
        """Upload a local file to the WeChat CDN.

        Parameters
        ----------
        file_path:
            Path to the file on the local filesystem.
        to_user_id:
            The WeChat user ID of the message recipient.  Required by the
            ``get_upload_url`` API.
        media_type:
            Override the auto-detected media type.  When ``None`` the type
            is inferred from the file extension via
            :func:`~wechat_bot_cli.media.mime_utils.guess_media_type`.

        Returns
        -------
        UploadResult
            Metadata needed to reference the uploaded media.

        Raises
        ------
        FileNotFoundError
            If *file_path* does not exist.
        MediaTooLargeError
            If the file exceeds :data:`MEDIA_MAX_BYTES`.
        UploadError
            If the iLink API or CDN upload fails.
        """
        path = Path(file_path)
        if not path.is_file():
            raise FileNotFoundError(f"File not found: {path}")

        plaintext = path.read_bytes()
        raw_size = len(plaintext)
        if raw_size > MEDIA_MAX_BYTES:
            raise MediaTooLargeError(
                f"File size {raw_size} bytes exceeds maximum "
                f"of {MEDIA_MAX_BYTES} bytes"
            )

        if media_type is None:
            media_type = guess_media_type(path.name)

        # Cryptographic material.
        aes_key = os.urandom(16)
        aes_key_hex = aes_key.hex()
        filekey = uuid.uuid4().hex  # 32 hex chars

        # Plaintext digest and padded ciphertext size.
        raw_md5 = hashlib.md5(plaintext).hexdigest()  # noqa: S324
        ciphertext_size = aes_ecb_padded_size(raw_size)

        logger.debug(
            "upload_file: path=%s raw_size=%d ciphertext_size=%d md5=%s "
            "filekey=%s media_type=%s",
            path,
            raw_size,
            ciphertext_size,
            raw_md5,
            filekey,
            media_type,
        )

        # ----- Optional thumbnail (images only) ----------------------------
        thumb_plaintext: bytes | None = None
        thumb_raw_size: int | None = None
        thumb_raw_md5: str | None = None
        thumb_ciphertext_size: int | None = None
        no_need_thumb = True

        if media_type == UploadMediaType.IMAGE:
            thumb_plaintext = _make_thumbnail(plaintext)
            if thumb_plaintext is not None:
                thumb_raw_size = len(thumb_plaintext)
                thumb_raw_md5 = hashlib.md5(thumb_plaintext).hexdigest()  # noqa: S324
                thumb_ciphertext_size = aes_ecb_padded_size(thumb_raw_size)
                no_need_thumb = False
                logger.debug(
                    "upload_file: thumbnail generated, thumb_raw_size=%d "
                    "thumb_ciphertext_size=%d",
                    thumb_raw_size,
                    thumb_ciphertext_size,
                )

        # ----- Step 1: get_upload_url --------------------------------------
        upload_url_params: dict = {
            "filekey": filekey,
            "media_type": media_type,
            "to_user_id": to_user_id,
            "rawsize": raw_size,
            "rawfilemd5": raw_md5,
            "filesize": ciphertext_size,
            "no_need_thumb": no_need_thumb,
            "aeskey": aes_key_hex,
        }
        if not no_need_thumb:
            upload_url_params.update(
                {
                    "thumb_rawsize": thumb_raw_size,
                    "thumb_rawfilemd5": thumb_raw_md5,
                    "thumb_filesize": thumb_ciphertext_size,
                }
            )

        resp = await self._client.get_upload_url(upload_url_params)

        upload_param: str | None = resp.get("upload_param")
        if not upload_param:
            raise UploadError(
                "get_upload_url returned no upload_param; "
                f"response={resp!r}"
            )

        # ----- Step 2: encrypt and upload the main file --------------------
        ciphertext = encrypt_aes_ecb(plaintext, aes_key)
        download_param = await self._cdn_upload(
            ciphertext=ciphertext,
            upload_param=upload_param,
            filekey=filekey,
            label=f"upload_file[{path.name}]",
        )

        # ----- Step 3: optionally upload the thumbnail ---------------------
        thumb_download_param: str | None = None
        if thumb_plaintext is not None and not no_need_thumb:
            thumb_upload_param: str | None = resp.get("thumb_upload_param")
            if thumb_upload_param:
                thumb_ciphertext = encrypt_aes_ecb(thumb_plaintext, aes_key)
                thumb_download_param = await self._cdn_upload(
                    ciphertext=thumb_ciphertext,
                    upload_param=thumb_upload_param,
                    filekey=filekey,
                    label=f"upload_file[{path.name}][thumb]",
                )
            else:
                logger.debug(
                    "upload_file: server did not return thumb_upload_param; "
                    "skipping thumbnail upload"
                )

        return UploadResult(
            filekey=filekey,
            download_encrypted_query_param=download_param,
            aes_key_hex=aes_key_hex,
            file_size=raw_size,
            file_size_ciphertext=ciphertext_size,
            thumb_download_encrypted_query_param=thumb_download_param,
        )

    # -- CDN transport ------------------------------------------------------

    def _build_cdn_upload_url(self, upload_param: str, filekey: str) -> str:
        """Construct the full CDN upload URL.

        Format::

            {cdn_base_url}/upload?encrypted_query_param={upload_param}&filekey={filekey}
        """
        return (
            f"{self._cdn_base_url}/upload"
            f"?encrypted_query_param={quote(upload_param, safe='')}"
            f"&filekey={quote(filekey, safe='')}"
        )

    async def _cdn_upload(
        self,
        *,
        ciphertext: bytes,
        upload_param: str,
        filekey: str,
        label: str,
    ) -> str:
        """POST *ciphertext* to the CDN and return the download param.

        Retries up to :data:`CDN_UPLOAD_MAX_RETRIES` on server errors.
        Client errors (4xx) abort immediately.

        Parameters
        ----------
        ciphertext:
            AES-128-ECB encrypted payload.
        upload_param:
            Signed upload parameter from ``get_upload_url``.
        filekey:
            Unique blob identifier.
        label:
            Human-readable label for log messages.

        Returns
        -------
        str
            The ``x-encrypted-param`` header value from the CDN response,
            used as the download encrypted query param.

        Raises
        ------
        UploadError
            If all retry attempts are exhausted or a client error occurs.
        """
        cdn_url = self._build_cdn_upload_url(upload_param, filekey)
        logger.debug(
            "%s: CDN POST ciphertext_size=%d url=%s...",
            label,
            len(ciphertext),
            cdn_url[:120],
        )

        last_error: Exception | None = None

        for attempt in range(1, CDN_UPLOAD_MAX_RETRIES + 1):
            try:
                download_param = await self._client.cdn_upload_raw(
                    upload_url=cdn_url,
                    ciphertext=ciphertext,
                )
                if not download_param:
                    raise UploadError(  # noqa: TRY301
                        f"{label}: CDN response missing x-encrypted-param "
                        f"header (attempt {attempt})"
                    )
                logger.debug("%s: CDN upload success (attempt %d)", label, attempt)
                return download_param

            except UploadError:
                raise
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if attempt < CDN_UPLOAD_MAX_RETRIES:
                    logger.warning(
                        "%s: CDN upload attempt %d/%d failed: %s; retrying",
                        label,
                        attempt,
                        CDN_UPLOAD_MAX_RETRIES,
                        exc,
                    )
                else:
                    logger.error(
                        "%s: CDN upload failed after %d attempts: %s",
                        label,
                        CDN_UPLOAD_MAX_RETRIES,
                        exc,
                    )

        raise UploadError(
            f"{label}: CDN upload failed after {CDN_UPLOAD_MAX_RETRIES} "
            f"attempts: {last_error}"
        ) from last_error
