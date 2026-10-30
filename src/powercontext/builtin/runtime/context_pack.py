# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Chunked context packs with prefix-reusing deltas.

The wire format is the interoperability contract: canonical JSON (sorted keys, ``","`` and
``":"`` separators, UTF-8), Base64 chunk arrays, and SHA-256 digests over the unsigned
payload. Receivers rebuild a target pack from a base pack plus a delta without any sender
state.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from dataclasses import dataclass

PACK_SCHEMA = "powercontext.context-pack.v1"
DELTA_SCHEMA = "powercontext.context-pack-delta.v1"
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_PACK_FIELDS = frozenset(("schema", "items", "digest", "byte_count"))
_DELTA_FIELDS = frozenset(("schema", "base_digest", "target_digest", "prefix_items", "appended"))


class ContextPackError(ValueError):
    """A context pack or delta violated the wire or chunking contract."""


@dataclass(frozen=True)
class ContextPack:
    """An immutable, digest-addressed sequence of UTF-8-safe byte chunks."""

    items: tuple[bytes, ...]
    digest: str

    @classmethod
    def build(cls, items: tuple[bytes, ...]) -> ContextPack:
        chunks = tuple(bytes(item) for item in items)
        return cls(items=chunks, digest=_pack_digest(chunks))

    @classmethod
    def from_content(cls, content: str, *, chunk_bytes: int = 512) -> ContextPack:
        if not isinstance(chunk_bytes, int) or isinstance(chunk_bytes, bool) or chunk_bytes < 1:
            raise ContextPackError("chunk_bytes must be a positive integer")  # noqa: TRY003
        return cls.build(_chunk(content, chunk_bytes))

    def to_bytes(self) -> bytes:
        body = {"schema": PACK_SCHEMA, "items": [_encode(item) for item in self.items], "digest": self.digest}
        byte_count = 0
        while True:
            encoded = _canonical({**body, "byte_count": byte_count})
            if len(encoded) == byte_count:
                return encoded
            byte_count = len(encoded)

    @property
    def byte_count(self) -> int:
        return len(self.to_bytes())

    @classmethod
    def from_bytes(cls, raw: bytes) -> ContextPack:
        payload = _payload(raw, _PACK_FIELDS)
        if payload["schema"] != PACK_SCHEMA:
            raise ContextPackError("unsupported context pack schema")  # noqa: TRY003
        items = _decode_items(payload["items"])
        digest = payload["digest"]
        if not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
            raise ContextPackError("invalid context pack digest")  # noqa: TRY003
        if digest != _pack_digest(items):
            raise ContextPackError("context pack digest mismatch")  # noqa: TRY003
        byte_count = payload["byte_count"]
        if not _is_int(byte_count) or byte_count != len(raw):
            raise ContextPackError("context pack byte_count mismatch")  # noqa: TRY003
        return cls(items=items, digest=digest)


@dataclass(frozen=True)
class ContextPackDelta:
    """A target pack expressed as a retained base prefix plus an appended suffix."""

    base_digest: str
    target_digest: str
    prefix_items: int
    appended: tuple[bytes, ...]

    @classmethod
    def between(cls, base: ContextPack, target: ContextPack) -> ContextPackDelta:
        prefix = 0
        while (
            prefix < len(base.items)
            and prefix < len(target.items)
            and base.items[prefix] == target.items[prefix]
        ):
            prefix += 1
        return cls(
            base_digest=base.digest,
            target_digest=target.digest,
            prefix_items=prefix,
            appended=target.items[prefix:],
        )

    def to_bytes(self) -> bytes:
        return _canonical(
            {
                "schema": DELTA_SCHEMA,
                "base_digest": self.base_digest,
                "target_digest": self.target_digest,
                "prefix_items": self.prefix_items,
                "appended": [_encode(item) for item in self.appended],
            }
        )

    @property
    def byte_count(self) -> int:
        return len(self.to_bytes())

    @classmethod
    def from_bytes(cls, raw: bytes) -> ContextPackDelta:
        payload = _payload(raw, _DELTA_FIELDS)
        if payload["schema"] != DELTA_SCHEMA:
            raise ContextPackError("unsupported context pack delta schema")  # noqa: TRY003
        base_digest = payload["base_digest"]
        target_digest = payload["target_digest"]
        for digest in (base_digest, target_digest):
            if not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
                raise ContextPackError("invalid context pack delta digest")  # noqa: TRY003
        prefix = payload["prefix_items"]
        if not _is_int(prefix) or prefix < 0:
            raise ContextPackError("prefix_items must be a non-negative integer")  # noqa: TRY003
        return cls(
            base_digest=base_digest,
            target_digest=target_digest,
            prefix_items=prefix,
            appended=_decode_items(payload["appended"]),
        )


def apply_context_pack_delta(base: ContextPack, delta: ContextPackDelta) -> ContextPack:
    """Validate one delta against its base and return the rebuilt target pack."""

    if delta.base_digest != base.digest:
        raise ContextPackError("context pack delta base digest mismatch")  # noqa: TRY003
    if not _is_int(delta.prefix_items) or not 0 <= delta.prefix_items <= len(base.items):
        raise ContextPackError("context pack delta prefix out of range")  # noqa: TRY003
    target = ContextPack.build((*base.items[: delta.prefix_items], *delta.appended))
    if target.digest != delta.target_digest:
        raise ContextPackError("context pack delta target digest mismatch")  # noqa: TRY003
    return target


def _pack_digest(items: tuple[bytes, ...]) -> str:
    unsigned = {"schema": PACK_SCHEMA, "items": [_encode(item) for item in items]}
    return "sha256:" + hashlib.sha256(_canonical(unsigned)).hexdigest()


def _chunk(content: str, chunk_bytes: int) -> tuple[bytes, ...]:
    if not content:
        return ()
    chunks: list[bytes] = []
    current: list[str] = []
    current_bytes = 0
    for character in content:
        encoded = character.encode("utf-8")
        if len(encoded) > chunk_bytes:
            raise ContextPackError("content contains a character wider than chunk_bytes")  # noqa: TRY003
        if current_bytes + len(encoded) > chunk_bytes:
            chunks.append("".join(current).encode("utf-8"))
            current = []
            current_bytes = 0
        current.append(character)
        current_bytes += len(encoded)
    if current:
        chunks.append("".join(current).encode("utf-8"))
    return tuple(chunks)


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _encode(item: bytes) -> str:
    return base64.b64encode(item).decode("ascii")


def _decode_items(value: object) -> tuple[bytes, ...]:
    if not isinstance(value, list):
        raise ContextPackError("chunks must be a list")  # noqa: TRY003
    items = []
    for entry in value:
        if not isinstance(entry, str):
            raise ContextPackError("chunks must be Base64 strings")  # noqa: TRY003
        try:
            items.append(base64.b64decode(entry, validate=True))
        except (binascii.Error, ValueError) as error:
            raise ContextPackError("invalid Base64 chunk") from error  # noqa: TRY003
    return tuple(items)


def _payload(raw: bytes, fields: frozenset[str]) -> dict[str, object]:
    if not isinstance(raw, (bytes, bytearray)):
        raise ContextPackError("wire payload must be bytes")  # noqa: TRY003
    try:
        payload = json.loads(bytes(raw).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ContextPackError("wire payload is not valid UTF-8 JSON") from error  # noqa: TRY003
    if not isinstance(payload, dict) or set(payload) != fields:
        raise ContextPackError("wire payload has unexpected fields")  # noqa: TRY003
    return payload


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


__all__ = [
    "ContextPack",
    "ContextPackDelta",
    "ContextPackError",
    "apply_context_pack_delta",
]
