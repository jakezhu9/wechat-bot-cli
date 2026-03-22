"""Data models for the WeChat iLink bot API.

Provides lightweight dataclasses and enums used across the CLI for
representing messages, media items, and CDN metadata.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class UploadMediaType(int, enum.Enum):
    """Media type tag used by the CDN upload pipeline.

    Values must be integers matching the iLink API protocol.
    """

    IMAGE = 1
    VIDEO = 2
    FILE = 3
    VOICE = 4


class MessageItemType(int, enum.Enum):
    """Discriminator for :class:`MessageItem` payloads.

    Values are integers matching the iLink API protocol:
    0=NONE, 1=TEXT, 2=IMAGE, 3=VOICE, 4=FILE, 5=VIDEO.
    """

    NONE = 0
    TEXT = 1
    IMAGE = 2
    VOICE = 3
    FILE = 4
    VIDEO = 5


# ---------------------------------------------------------------------------
# CDN media reference
# ---------------------------------------------------------------------------


@dataclass
class CDNMedia:
    """Reference to an encrypted blob on the WeChat CDN."""

    encrypt_query_param: str = ""
    aes_key: str = ""
    encrypt_type: int = 1

    def to_dict(self) -> Dict[str, Any]:
        """Serialise to a JSON-friendly dictionary."""
        return {
            "encrypt_query_param": self.encrypt_query_param,
            "aes_key": self.aes_key,
            "encrypt_type": self.encrypt_type,
        }

    @classmethod
    def from_raw(cls, raw: dict) -> "CDNMedia":
        """Construct a :class:`CDNMedia` from a raw API dictionary."""
        return cls(
            encrypt_query_param=raw.get("encrypt_query_param", ""),
            aes_key=raw.get("aes_key", ""),
            encrypt_type=raw.get("encrypt_type", 1),
        )


# ---------------------------------------------------------------------------
# Message item payloads
# ---------------------------------------------------------------------------


@dataclass
class TextItem:
    """Plain-text message content."""

    text: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Serialise to a JSON-friendly dictionary."""
        return {"text": self.text}


@dataclass
class ImageItem:
    """Image message payload."""

    media: CDNMedia | None = None
    thumb_media: CDNMedia | None = None
    mid_size: int | None = None

    def to_dict(self) -> Dict[str, Any]:
        """Serialise to a JSON-friendly dictionary."""
        d: Dict[str, Any] = {}
        if self.media is not None:
            d["media"] = self.media.to_dict()
        if self.thumb_media is not None:
            d["thumb_media"] = self.thumb_media.to_dict()
        if self.mid_size is not None:
            d["mid_size"] = self.mid_size
        return d


@dataclass
class VideoItem:
    """Video message payload."""

    media: CDNMedia | None = None
    video_size: int | None = None
    thumb_media: CDNMedia | None = None

    def to_dict(self) -> Dict[str, Any]:
        """Serialise to a JSON-friendly dictionary."""
        d: Dict[str, Any] = {}
        if self.media is not None:
            d["media"] = self.media.to_dict()
        if self.video_size is not None:
            d["video_size"] = self.video_size
        if self.thumb_media is not None:
            d["thumb_media"] = self.thumb_media.to_dict()
        return d


@dataclass
class FileItem:
    """File attachment payload."""

    media: CDNMedia | None = None
    file_name: str = ""
    len: str = "0"

    def to_dict(self) -> Dict[str, Any]:
        """Serialise to a JSON-friendly dictionary."""
        d: Dict[str, Any] = {"file_name": self.file_name, "len": self.len}
        if self.media is not None:
            d["media"] = self.media.to_dict()
        return d


@dataclass
class MessageItem:
    """A single content item inside a message."""

    type: MessageItemType = MessageItemType.TEXT
    text_item: TextItem | None = None
    image_item: ImageItem | None = None
    video_item: VideoItem | None = None
    file_item: FileItem | None = None

    def to_dict(self) -> Dict[str, Any]:
        """Serialise to a JSON-friendly dictionary."""
        d: Dict[str, Any] = {
            "type": self.type.value if hasattr(self.type, "value") else self.type,
        }
        if self.text_item is not None:
            d["text_item"] = self.text_item.to_dict()
        if self.image_item is not None:
            d["image_item"] = self.image_item.to_dict()
        if self.video_item is not None:
            d["video_item"] = self.video_item.to_dict()
        if self.file_item is not None:
            d["file_item"] = self.file_item.to_dict()
        return d

    @classmethod
    def from_raw(cls, raw: dict) -> "MessageItem":
        """Parse a raw API dictionary into a :class:`MessageItem`.

        The server uses numeric type codes (1=TEXT, 2=IMAGE, 3=VOICE,
        4=FILE, 5=VIDEO).
        """
        raw_type = raw.get("type")
        if isinstance(raw_type, int):
            try:
                item_type = MessageItemType(raw_type)
            except ValueError:
                item_type = MessageItemType.TEXT
        else:
            item_type = MessageItemType.TEXT

        text_item = None
        image_item = None
        video_item = None
        file_item = None

        if item_type == MessageItemType.TEXT:
            raw_text = raw.get("text_item") or {}
            text_item = TextItem(text=raw_text.get("text", ""))
        elif item_type == MessageItemType.IMAGE:
            raw_img = raw.get("image_item") or {}
            image_item = ImageItem(
                media=CDNMedia.from_raw(raw_img.get("media") or {}),
                thumb_media=CDNMedia.from_raw(raw_img["thumb_media"]) if raw_img.get("thumb_media") else None,
                mid_size=raw_img.get("mid_size"),
            )
        elif item_type == MessageItemType.VIDEO:
            raw_vid = raw.get("video_item") or {}
            video_item = VideoItem(
                media=CDNMedia.from_raw(raw_vid.get("media") or {}),
                video_size=raw_vid.get("video_size"),
                thumb_media=CDNMedia.from_raw(raw_vid["thumb_media"]) if raw_vid.get("thumb_media") else None,
            )
        elif item_type == MessageItemType.FILE:
            raw_file = raw.get("file_item") or {}
            file_item = FileItem(
                media=CDNMedia.from_raw(raw_file.get("media") or {}),
                file_name=raw_file.get("file_name", ""),
                len=str(raw_file.get("len", "0")),
            )

        return cls(
            type=item_type,
            text_item=text_item,
            image_item=image_item,
            video_item=video_item,
            file_item=file_item,
        )


# ---------------------------------------------------------------------------
# Inbound message
# ---------------------------------------------------------------------------


@dataclass
class WeChatMessage:
    """An inbound message received via ``getUpdates``.

    This is a simplified representation of the server payload.  Additional
    fields from the raw JSON are collected in :attr:`raw`.
    """

    from_user_id: str = ""
    to_user_id: str = ""
    context_token: str = ""
    item_list: List[MessageItem] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# getUpdates response
# ---------------------------------------------------------------------------


@dataclass
class GetUpdatesResponse:
    """Parsed response from the ``getUpdates`` API endpoint."""

    get_updates_buf: str = ""
    msgs: List[WeChatMessage] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)
