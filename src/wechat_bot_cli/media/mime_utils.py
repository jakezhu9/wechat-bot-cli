"""MIME type and media-type detection utilities for WeChat CDN uploads.

Provides bidirectional mapping between file extensions and MIME types, and
logic to infer the correct :class:`UploadMediaType` enum from a filename so
the uploader can select the right CDN pipeline (image / video / file).
"""

from __future__ import annotations

import os
from typing import Dict

from wechat_bot_cli.core.models import UploadMediaType

# ---------------------------------------------------------------------------
# Extension <-> MIME mappings
# ---------------------------------------------------------------------------

#: Mapping from lowercase file extension (with leading dot) to MIME type.
EXTENSION_TO_MIME: Dict[str, str] = {
    # Images
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".tiff": "image/tiff",
    ".tif": "image/tiff",
    # Video
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".avi": "video/x-msvideo",
    ".mkv": "video/x-matroska",
    ".webm": "video/webm",
    ".flv": "video/x-flv",
    ".wmv": "video/x-ms-wmv",
    ".3gp": "video/3gpp",
    # Audio
    ".mp3": "audio/mpeg",
    ".ogg": "audio/ogg",
    ".wav": "audio/wav",
    ".flac": "audio/flac",
    ".aac": "audio/aac",
    ".wma": "audio/x-ms-wma",
    ".silk": "audio/silk",
    ".amr": "audio/amr",
    # Documents
    ".pdf": "application/pdf",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".ppt": "application/vnd.ms-powerpoint",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".csv": "text/csv",
    ".txt": "text/plain",
    ".rtf": "application/rtf",
    ".json": "application/json",
    ".xml": "application/xml",
    ".html": "text/html",
    ".htm": "text/html",
    ".md": "text/markdown",
    # Archives
    ".zip": "application/zip",
    ".rar": "application/vnd.rar",
    ".7z": "application/x-7z-compressed",
    ".tar": "application/x-tar",
    ".gz": "application/gzip",
    ".bz2": "application/x-bzip2",
    # Other
    ".apk": "application/vnd.android.package-archive",
    ".exe": "application/x-msdownload",
    ".dmg": "application/x-apple-diskimage",
}

#: Reverse mapping from MIME type to a canonical extension (with leading dot).
#: When multiple extensions share the same MIME type (e.g. .jpg / .jpeg),
#: only one canonical extension is stored.
MIME_TO_EXTENSION: Dict[str, str] = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/bmp": ".bmp",
    "image/svg+xml": ".svg",
    "image/x-icon": ".ico",
    "image/tiff": ".tiff",
    "video/mp4": ".mp4",
    "video/quicktime": ".mov",
    "video/x-msvideo": ".avi",
    "video/x-matroska": ".mkv",
    "video/webm": ".webm",
    "video/x-flv": ".flv",
    "video/x-ms-wmv": ".wmv",
    "video/3gpp": ".3gp",
    "audio/mpeg": ".mp3",
    "audio/ogg": ".ogg",
    "audio/wav": ".wav",
    "audio/flac": ".flac",
    "audio/aac": ".aac",
    "audio/x-ms-wma": ".wma",
    "audio/silk": ".silk",
    "audio/amr": ".amr",
    "application/pdf": ".pdf",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.ms-powerpoint": ".ppt",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "text/csv": ".csv",
    "text/plain": ".txt",
    "application/rtf": ".rtf",
    "application/json": ".json",
    "application/xml": ".xml",
    "text/html": ".html",
    "text/markdown": ".md",
    "application/zip": ".zip",
    "application/vnd.rar": ".rar",
    "application/x-7z-compressed": ".7z",
    "application/x-tar": ".tar",
    "application/gzip": ".gz",
    "application/x-bzip2": ".bz2",
    "application/vnd.android.package-archive": ".apk",
    "application/x-msdownload": ".exe",
    "application/x-apple-diskimage": ".dmg",
    "application/octet-stream": ".bin",
}

# Pre-computed sets for fast media-type classification.
_IMAGE_MIMES: frozenset[str] = frozenset(
    mime for mime in MIME_TO_EXTENSION if mime.startswith("image/")
)
_VIDEO_MIMES: frozenset[str] = frozenset(
    mime for mime in MIME_TO_EXTENSION if mime.startswith("video/")
)


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def guess_mime_type(filename: str) -> str:
    """Guess the MIME type of *filename* from its extension.

    Parameters
    ----------
    filename:
        A file name or path. Only the extension is used for the lookup.

    Returns
    -------
    str
        The MIME type string, or ``"application/octet-stream"`` if the
        extension is unrecognised.
    """
    _, ext = os.path.splitext(filename)
    return EXTENSION_TO_MIME.get(ext.lower(), "application/octet-stream")


def guess_media_type(filename: str) -> UploadMediaType:
    """Map *filename* to the WeChat :class:`UploadMediaType` enum value.

    The classification is intentionally simple:

    * ``image/*``  -> :attr:`UploadMediaType.IMAGE`
    * ``video/*``  -> :attr:`UploadMediaType.VIDEO`
    * everything else -> :attr:`UploadMediaType.FILE`

    Parameters
    ----------
    filename:
        A file name or path.

    Returns
    -------
    UploadMediaType
    """
    mime = guess_mime_type(filename)
    if mime in _IMAGE_MIMES:
        return UploadMediaType.IMAGE
    if mime in _VIDEO_MIMES:
        return UploadMediaType.VIDEO
    return UploadMediaType.FILE


def guess_extension(mime_type: str) -> str:
    """Return the canonical file extension for *mime_type*.

    Parameters
    ----------
    mime_type:
        A MIME type string, optionally with parameters
        (e.g. ``"text/plain; charset=utf-8"``).  Only the media type
        portion is used.

    Returns
    -------
    str
        The extension including the leading dot (e.g. ``".jpg"``), or
        ``".bin"`` if the MIME type is not recognised.
    """
    # Strip optional parameters (e.g. "; charset=utf-8").
    media_type = mime_type.split(";", 1)[0].strip().lower()
    return MIME_TO_EXTENSION.get(media_type, ".bin")
