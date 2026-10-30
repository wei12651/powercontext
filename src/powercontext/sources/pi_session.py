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

"""Pi v3 session branch capture with resumable connector checkpoints."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
from urllib.parse import quote

from pydantic import BaseModel, JsonValue

from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources import (
    TEXT_EVIDENCE_PROJECTION_KEY,
    AdapterSourceDefinition,
    ConnectorRunCompletion,
    ConnectorRunSession,
    ConnectorRunStatus,
    ConnectorSubmissionStatus,
    TextEvidence,
)
from powercontext.sources.models import Source, SourceMaterialization

PI_EVENT_SOURCE_NAME = "pi-event"
PI_SESSION_CONNECTOR_NAME = "pi-session-history"
PI_SESSION_VERSION = 3
_CHECKPOINT_VERSION = 1


class PiSessionInput(BaseModel):
    """One full Pi v3 JSONL archive and an optional branch leaf."""

    content: bytes
    leaf_id: str | None = None


class PiEventInput(BaseModel):
    """One visible event from the selected branch, retaining its raw record."""

    session_id: str
    event_id: str
    parent_id: str | None = None
    event_type: str
    role: str
    content: str
    record: dict[str, JsonValue]


class PiEventSource(Source):
    """A captured Pi session event identified by session and event identity."""

    session_id: str
    event_id: str
    parent_id: str | None = None
    event_type: str
    role: str
    content: str
    record: dict[str, JsonValue]


def pi_event_source_id(session_id: str, event_id: str, /) -> str:
    """Derive a bounded identity from the session and event identifiers."""

    identity = f"pi:{_quote(session_id)}:{_quote(event_id)}"
    if len(identity) <= MAX_SOURCE_ID_LENGTH:
        return identity
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return f"pi:{digest}"


def parse_pi_session(value: PiSessionInput, /) -> tuple[PiEventInput, ...]:
    """Parse the whole archive, then return the selected branch from root to leaf."""

    records = _load_records(value.content)
    header = records[0]
    session_id = _session_id(header)

    order: list[str] = []
    parents: dict[str, str | None] = {}
    by_id: dict[str, dict[str, JsonValue]] = {}
    for record in records[1:]:
        event_id = record.get("id")
        if not isinstance(event_id, str) or not event_id.strip():
            raise ValueError("every Pi record must declare a non-empty string id")  # noqa: TRY003
        if event_id in by_id:
            raise ValueError(f"duplicate Pi record id {event_id!r}")  # noqa: TRY003
        parent_id = record.get("parentId")
        if parent_id is not None and not isinstance(parent_id, str):
            raise ValueError("parentId must be a string or null")  # noqa: TRY003
        if parent_id is not None and parent_id not in by_id:
            raise ValueError(f"parent {parent_id!r} must appear before its child")  # noqa: TRY003
        by_id[event_id] = record
        parents[event_id] = parent_id
        order.append(event_id)

    if not order:
        raise ValueError("Pi session archive must contain at least one event")  # noqa: TRY003
    leaf_id = value.leaf_id if value.leaf_id is not None else order[-1]
    if leaf_id not in by_id:
        raise ValueError(f"leaf {leaf_id!r} is not present in the session archive")  # noqa: TRY003

    chain: list[str] = []
    cursor: str | None = leaf_id
    while cursor is not None:
        chain.append(cursor)
        cursor = parents[cursor]
    chain.reverse()

    events: list[PiEventInput] = []
    for event_id in chain:
        record = by_id[event_id]
        rendered = _render_event(record)
        if rendered is None:
            continue
        role, content = rendered
        events.append(
            PiEventInput(
                session_id=session_id,
                event_id=event_id,
                parent_id=parents[event_id],
                event_type=str(record["type"]),
                role=role,
                content=f"{role}: {content}",
                record=record,
            )
        )
    return tuple(events)


class PiEventSourceAdapter:
    """Resolve and read one captured Pi event Source."""

    input_class = PiEventInput
    name = PI_EVENT_SOURCE_NAME
    source_class = PiEventSource

    async def resolve(self, value: PiEventInput, /) -> PiEventSource:
        return PiEventSource(
            name=pi_event_source_id(value.session_id, value.event_id),
            materialization=SourceMaterialization.CAPTURED,
            session_id=value.session_id,
            event_id=value.event_id,
            parent_id=value.parent_id,
            event_type=value.event_type,
            role=value.role,
            content=value.content,
            record=value.record,
        )

    async def read(self, source: PiEventSource, /) -> PiEventInput:
        return PiEventInput(
            session_id=source.session_id,
            event_id=source.event_id,
            parent_id=source.parent_id,
            event_type=source.event_type,
            role=source.role,
            content=source.content,
            record=source.record,
        )


class PiEventTextEvidenceProjection:
    """Expose the fully rendered event text as standard evidence."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = PiEventSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: PiEventSource, /) -> TextEvidence:
        return TextEvidence(
            source_type=PI_EVENT_SOURCE_NAME,
            source_id=source.name,
            content=source.content,
            metadata={
                "session_id": source.session_id,
                "event_id": source.event_id,
                "parent_id": source.parent_id,
                "event_type": source.event_type,
                "role": source.role,
            },
        )


PI_EVENT_SOURCE_ADAPTER = PiEventSourceAdapter()
PI_EVENT_SOURCE_DEFINITION = AdapterSourceDefinition(
    PI_EVENT_SOURCE_ADAPTER,
    projections=(PiEventTextEvidenceProjection(),),
)


class PiSessionConnector:
    """Import unseen events of the selected branch and persist its progress."""

    name = PI_SESSION_CONNECTOR_NAME
    version = "1"
    source_definitions = frozenset({PI_EVENT_SOURCE_NAME})

    def __init__(self, path: str | Path, leaf_id: str | None = None) -> None:
        self._path = Path(path)
        self._leaf_id = leaf_id

    async def run(self, session: ConnectorRunSession, /) -> ConnectorRunCompletion:
        events = parse_pi_session(
            PiSessionInput(content=self._path.read_bytes(), leaf_id=self._leaf_id)
        )
        imported = _decode_checkpoint(session.checkpoint)
        for event in events:
            digest = _record_digest(event.record)
            previous = imported.get(event.event_id)
            if previous is not None:
                if previous != digest:
                    raise ValueError(f"imported event {event.event_id!r} was rewritten")  # noqa: TRY003
                continue
            outcome = await session.submit(event.event_id, PI_EVENT_SOURCE_NAME, event)
            if outcome.status is not ConnectorSubmissionStatus.ACCEPTED:
                return ConnectorRunCompletion(
                    status=ConnectorRunStatus.INCOMPLETE, checkpoint=session.checkpoint
                )
            imported[event.event_id] = digest
        return ConnectorRunCompletion(
            status=ConnectorRunStatus.COMPLETE,
            checkpoint={"version": _CHECKPOINT_VERSION, "events": imported},
        )


def _decode_checkpoint(checkpoint: JsonValue | None) -> dict[str, str]:
    if checkpoint is None:
        return {}
    if not isinstance(checkpoint, dict):
        raise ValueError("connector checkpoint must be an object")  # noqa: TRY003
    events = checkpoint.get("events")
    if not isinstance(events, dict):
        raise ValueError("connector checkpoint must record imported events")  # noqa: TRY003
    decoded: dict[str, str] = {}
    for event_id, digest in events.items():
        if not isinstance(event_id, str) or not isinstance(digest, str):
            raise ValueError("connector checkpoint entries must be strings")  # noqa: TRY003
        decoded[event_id] = digest
    return decoded


def _record_digest(record: dict[str, JsonValue]) -> str:
    encoded = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _load_records(content: bytes) -> list[dict[str, JsonValue]]:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("Pi session archive must be strict UTF-8") from error  # noqa: TRY003
    if text.startswith("\ufeff"):
        text = text[1:]
    records: list[dict[str, JsonValue]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError("Pi session archive must be valid JSONL") from error  # noqa: TRY003
        if not isinstance(record, dict):
            raise ValueError("every Pi record must be a JSON object")  # noqa: TRY003
        records.append(record)
    if not records:
        raise ValueError("Pi session archive must not be empty")  # noqa: TRY003
    return records


def _session_id(header: dict[str, JsonValue]) -> str:
    if header.get("type") != "session":
        raise ValueError("the first Pi record must declare type=session")  # noqa: TRY003
    version = header.get("version")
    if type(version) is not int or version != PI_SESSION_VERSION:
        raise ValueError("Pi session records must declare version 3")  # noqa: TRY003
    session_id = header.get("id")
    if not isinstance(session_id, str) or not session_id.strip():
        raise ValueError("Pi session header must declare a non-empty id")  # noqa: TRY003
    return session_id


def _render_event(record: dict[str, JsonValue]) -> tuple[str, str] | None:
    event_type = record.get("type")
    if event_type == "message":
        message = record.get("message")
        if not isinstance(message, dict):
            return None
        role = message.get("role")
        if not isinstance(role, str) or not role:
            return None
        body = _message_body(role, message)
        return (role, body) if body.strip() else None
    if event_type in {"compaction", "branch_summary"}:
        summary = record.get("summary")
        if not isinstance(summary, str) or not summary.strip():
            return None
        return event_type, summary
    if event_type == "custom_message":
        body = _text_body(record.get("content"))
        return ("custom", body) if body.strip() else None
    return None


def _message_body(role: str, message: dict[str, Any]) -> str:
    if role == "toolResult":
        name = _scalar(message.get("toolName"))
        call_id = _scalar(message.get("toolCallId"))
        return f"[tool_result {name} {call_id}] {_text_body(message.get('content'))}"
    if role == "bashExecution":
        return f"$ {_scalar(message.get('command'))}\n{_scalar(message.get('output'))}"
    return _text_body(message.get("content"))


def _text_body(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "text" and isinstance(block.get("text"), str):
            parts.append(block["text"])
        elif kind == "toolCall":
            name = _scalar(block.get("name"))
            call_id = _scalar(block.get("id"))
            arguments = json.dumps(
                block.get("arguments"),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            parts.append(f"[tool_call {name} {call_id}] {arguments}")
    return "\n".join(parts)


def _scalar(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _quote(component: str) -> str:
    return quote(component, safe="")


__all__ = [
    "PI_EVENT_SOURCE_ADAPTER",
    "PI_EVENT_SOURCE_DEFINITION",
    "PI_EVENT_SOURCE_NAME",
    "PI_SESSION_CONNECTOR_NAME",
    "PiEventInput",
    "PiEventSource",
    "PiEventSourceAdapter",
    "PiEventTextEvidenceProjection",
    "PiSessionConnector",
    "PiSessionInput",
    "parse_pi_session",
    "pi_event_source_id",
]
