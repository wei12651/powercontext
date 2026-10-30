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

"""Source-dependent availability for Memory and Topic Memory."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy import Boolean, Column, Integer, MetaData, Table, Text, insert, select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.artifacts import ArtifactRef, MemoryCitation
from powercontext.builtin.artifacts.memory import InvalidMemoryCitationError, MemoryEntryNotFoundError
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.errors import RepositoryNotFoundError
from powercontext.builtin.persistence.tables import identity_string
from powercontext.errors import ArtifactNotFoundError, SourceNotFoundError
from powercontext.sources import SourceRef

if TYPE_CHECKING:
    from powercontext.builtin.runtime.application import BuiltinRuntime

NodeFamily = Literal["source", "memory", "topic"]
DependencyPolicy = Literal["all", "any"]
ResolvePurpose = Literal["generation", "audit"]

SOURCE: NodeFamily = "source"
MEMORY: NodeFamily = "memory"
TOPIC: NodeFamily = "topic"


class SourceLifecycleError(ValueError):
    """A lifecycle registration, event, or resolution violates its contract."""


class SourceLifecycleEvent(BaseModel):
    """One caller-supplied lifecycle transition for a registered Source."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    node_id: str
    revision: int
    sequence: int
    available: bool
    reason: str

    @field_validator("node_id", "reason")
    @classmethod
    def require_text(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("must be non-empty text")  # noqa: TRY003
        return value

    @field_validator("revision", "sequence", mode="before")
    @classmethod
    def require_positive_int(cls, value: object) -> object:
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError("must be a positive integer")  # noqa: TRY003
        return value


_LIFECYCLE_METADATA = MetaData()

LIFECYCLE_NODES = Table(
    "pc_source_lifecycle_nodes",
    _LIFECYCLE_METADATA,
    Column("scope_id", identity_string(255), primary_key=True),
    Column("node_id", identity_string(255), primary_key=True),
    Column("family", identity_string(16), nullable=False),
    Column("reference", Text, nullable=False),
    Column("parents", Text, nullable=False),
    Column("policy", identity_string(8), nullable=False),
    Column("available", Boolean, nullable=False),
    Column("source_revision", Integer, nullable=True),
    Column("source_sequence", Integer, nullable=True),
    Column("last_reason", Text, nullable=True),
)

LIFECYCLE_TABLES = (LIFECYCLE_NODES,)


@dataclass(frozen=True)
class _Node:
    node_id: str
    family: NodeFamily
    reference: Mapping[str, Any]
    parents: tuple[str, ...]
    policy: DependencyPolicy
    available: bool
    source_revision: int | None = None
    source_sequence: int | None = None
    last_reason: str | None = None


class SourceLifecycleStore:
    """Persist the per-scope dependency graph and its last applied transition."""

    def __init__(self, database: AsyncDatabase) -> None:
        self._database = database

    async def load(self, scope_id: str, /) -> dict[str, _Node]:
        async with self._database.transaction() as connection:
            return await self._load(connection, scope_id)

    async def insert(self, scope_id: str, node: _Node, /) -> None:
        async with self._database.transaction() as connection:
            await connection.execute(insert(LIFECYCLE_NODES).values(**_row_values(scope_id, node)))

    async def save(self, scope_id: str, nodes: Iterable[_Node], /) -> None:
        async with self._database.transaction() as connection:
            for node in nodes:
                await connection.execute(
                    update(LIFECYCLE_NODES)
                    .where(
                        LIFECYCLE_NODES.c.scope_id == scope_id,
                        LIFECYCLE_NODES.c.node_id == node.node_id,
                    )
                    .values(
                        available=node.available,
                        source_revision=node.source_revision,
                        source_sequence=node.source_sequence,
                        last_reason=node.last_reason,
                    )
                )

    async def _load(self, connection: AsyncConnection, scope_id: str) -> dict[str, _Node]:
        result = await connection.execute(select(LIFECYCLE_NODES).where(LIFECYCLE_NODES.c.scope_id == scope_id))
        return {str(row.node_id): _node_from_row(row) for row in result.mappings()}

    async def hidden_topics(self, scope_id: str, artifact_ids: Iterable[str], /) -> frozenset[str]:
        candidates = set(artifact_ids)
        if not candidates:
            return frozenset()
        visible: set[str] = set()
        hidden: set[str] = set()
        for node in (await self.load(scope_id)).values():
            if node.family != TOPIC:
                continue
            artifact_id = ArtifactRef.model_validate(node.reference).artifact_id
            if artifact_id not in candidates:
                continue
            (visible if node.available else hidden).add(artifact_id)
        return frozenset(hidden - visible)


def _row_values(scope_id: str, node: _Node) -> dict[str, Any]:
    return {
        "scope_id": scope_id,
        "node_id": node.node_id,
        "family": node.family,
        "reference": json.dumps(dict(node.reference), sort_keys=True, separators=(",", ":")),
        "parents": json.dumps(list(node.parents), separators=(",", ":")),
        "policy": node.policy,
        "available": node.available,
        "source_revision": node.source_revision,
        "source_sequence": node.source_sequence,
        "last_reason": node.last_reason,
    }


def _node_from_row(row: Any) -> _Node:
    return _Node(
        node_id=str(row.node_id),
        family=str(row.family),  # type: ignore[arg-type]
        reference=json.loads(row.reference),
        parents=tuple(json.loads(row.parents)),
        policy=str(row.policy),  # type: ignore[arg-type]
        available=bool(row.available),
        source_revision=None if row.source_revision is None else int(row.source_revision),
        source_sequence=None if row.source_sequence is None else int(row.source_sequence),
        last_reason=None if row.last_reason is None else str(row.last_reason),
    )


class SourceLifecycleApplication:
    """Open lifecycle services for one Runtime without owning their persistence."""

    def __init__(self, runtime: BuiltinRuntime, store: SourceLifecycleStore | None) -> None:
        self._runtime = runtime
        self._store = store

    def for_scope(self, scope_id: str, /) -> SourceLifecycleService:
        return SourceLifecycleService(self._runtime, self._store, scope_id)

    async def hidden_topic_ids(self, scope_id: str, artifact_ids: Iterable[str], /) -> frozenset[str]:
        if self._store is None:
            return frozenset()
        return await self._store.hidden_topics(scope_id, artifact_ids)


class SourceLifecycleService:
    """Register evidence dependencies and propagate Source availability through them."""

    def __init__(self, runtime: BuiltinRuntime, store: SourceLifecycleStore | None, scope_id: str) -> None:
        from powercontext.builtin.sources import validate_scope_id

        self._runtime = runtime
        self._store = store
        self.scope_id = validate_scope_id(scope_id)

    async def register_source(self, node_id: str, source_ref: SourceRef, /, *, revision: int) -> None:
        self._require_store()
        _require_node_id(node_id)
        _require_positive_integer(revision, "revision")
        if not isinstance(source_ref, SourceRef):
            raise SourceLifecycleError("source-reference")
        await self._require_source(source_ref)
        reference = source_ref.model_dump(mode="json")
        nodes = await self._store.load(self.scope_id)
        existing = nodes.get(node_id)
        candidate = _Node(
            node_id=node_id,
            family=SOURCE,
            reference=reference,
            parents=(),
            policy="all",
            available=True,
            source_revision=revision,
            source_sequence=0,
        )
        if existing is not None:
            if _binding(existing) == _binding(candidate) and existing.source_revision == revision:
                return
            raise SourceLifecycleError("registration-conflict")
        await self._store.insert(self.scope_id, candidate)

    async def register_memory(
        self,
        node_id: str,
        citation: MemoryCitation,
        /,
        *,
        parents: Iterable[str],
        policy: DependencyPolicy,
    ) -> None:
        self._require_store()
        _require_node_id(node_id)
        if not isinstance(citation, MemoryCitation):
            raise SourceLifecycleError("memory-reference")
        await self._require_memory(citation)
        await self._register_derived(
            node_id,
            MEMORY,
            citation.model_dump(mode="json"),
            tuple(parents),
            policy,
        )

    async def register_topic(
        self,
        node_id: str,
        artifact_ref: ArtifactRef,
        /,
        *,
        parents: Iterable[str],
        policy: DependencyPolicy,
    ) -> None:
        self._require_store()
        _require_node_id(node_id)
        if not isinstance(artifact_ref, ArtifactRef):
            raise SourceLifecycleError("topic-reference")
        await self._require_topic(artifact_ref)
        await self._register_derived(
            node_id,
            TOPIC,
            artifact_ref.model_dump(mode="json"),
            tuple(parents),
            policy,
        )

    async def apply(self, event: SourceLifecycleEvent, /) -> list[str]:
        self._require_store()
        if not isinstance(event, SourceLifecycleEvent):
            raise SourceLifecycleError("event")
        nodes = await self._store.load(self.scope_id)
        source = nodes.get(event.node_id)
        if source is None or source.family != SOURCE:
            raise SourceLifecycleError("unknown-source")
        if source.source_revision != event.revision:
            raise SourceLifecycleError("revision-mismatch")
        sequence = source.source_sequence or 0
        if event.sequence < sequence:
            raise SourceLifecycleError("stale-sequence")
        replay = (
            event.sequence == sequence
            and source.available == event.available
            and source.last_reason == event.reason
        )
        if event.sequence == sequence:
            if replay:
                return sorted(node_id for node_id, node in nodes.items() if node.available)
            raise SourceLifecycleError("sequence-conflict")

        transitioned = replace(
            source,
            available=event.available,
            source_sequence=event.sequence,
            last_reason=event.reason,
        )
        pending = {**nodes, event.node_id: transitioned}
        states = _propagate(pending, {event.node_id: event.available})
        updated = tuple(replace(node, available=states[node.node_id]) for node in pending.values())
        targets = {
            node.node_id: (MemoryCitation.model_validate(node.reference), states[node.node_id])
            for node in updated
            if node.family == MEMORY
        }
        await self._apply_memory_states(targets, event.reason)
        await self._store.save(self.scope_id, updated)
        return sorted(node_id for node_id, available in states.items() if available)

    async def resolve(self, node_id: str, /, *, purpose: ResolvePurpose) -> SourceRef | MemoryCitation | ArtifactRef:
        self._require_store()
        if purpose not in ("generation", "audit"):
            raise SourceLifecycleError("purpose")
        nodes = await self._store.load(self.scope_id)
        node = nodes.get(node_id)
        if node is None:
            raise SourceLifecycleError("unknown-node")
        if purpose == "generation" and not node.available:
            raise SourceLifecycleError("unavailable")
        if node.family == SOURCE:
            return SourceRef.model_validate(node.reference)
        if node.family == MEMORY:
            return MemoryCitation.model_validate(node.reference)
        return ArtifactRef.model_validate(node.reference)

    def _require_store(self) -> SourceLifecycleStore:
        if self._store is None:
            raise SourceLifecycleError("lifecycle-store")
        return self._store

    async def _register_derived(
        self,
        node_id: str,
        family: NodeFamily,
        reference: Mapping[str, Any],
        parents: tuple[str, ...],
        policy: DependencyPolicy,
    ) -> None:
        if policy not in ("all", "any"):
            raise SourceLifecycleError("policy")
        if not parents:
            raise SourceLifecycleError("parents")
        if len(set(parents)) != len(parents):
            raise SourceLifecycleError("duplicate-parent")
        if node_id in parents:
            raise SourceLifecycleError("cycle")
        nodes = await self._store.load(self.scope_id)
        for parent in parents:
            if parent not in nodes:
                raise SourceLifecycleError("missing-parent")
        existing = nodes.get(node_id)
        candidate = _Node(
            node_id=node_id,
            family=family,
            reference=reference,
            parents=parents,
            policy=policy,
            available=False,
        )
        if existing is not None:
            if _binding(existing) == _binding(candidate):
                return
            raise SourceLifecycleError("registration-conflict")
        states = _propagate(nodes, {})
        available = _supported(states, candidate)
        candidate = replace(candidate, available=available)
        if family == MEMORY:
            await self._apply_memory_states(
                {node_id: (MemoryCitation.model_validate(reference), available)},
                "source lifecycle",
            )
        await self._store.insert(self.scope_id, candidate)

    async def _apply_memory_states(
        self,
        targets: Mapping[str, tuple[MemoryCitation, bool]],
        reason: str,
    ) -> None:
        for citation, available in targets.values():
            await self._set_memory_state(citation, available, reason)

    async def _set_memory_state(self, citation: MemoryCitation, available: bool, reason: str) -> None:
        async with self._runtime._context(self.scope_id) as context:
            service = context.artifacts.memory
            head = await service.head(citation.memory_ref.artifact_id)
            manifest = {item.entry_id: item for item in head.content.manifest.entries}
            item = manifest.get(citation.entry_id)
            if item is None:
                raise SourceLifecycleError("memory-entry")
            current = {entry.entry_id: entry for entry in await service.entries(head)}
            version = current.get(citation.entry_id)
            if version is None:
                raise SourceLifecycleError("memory-entry")
            if available and item.state != "active":
                await service.reactivate(head, entries=(version,), reason=reason)
            elif not available and item.state == "active":
                await service.forget(head, entries=(version,), reason=reason)

    async def _require_source(self, source_ref: SourceRef) -> None:
        try:
            await self._runtime.records.for_scope(self.scope_id).get_source(
                source_ref.source_type,
                source_ref.source_id,
            )
        except (RepositoryNotFoundError, SourceNotFoundError) as error:
            raise SourceLifecycleError("source-not-found") from error

    async def _require_memory(self, citation: MemoryCitation) -> None:
        try:
            async with self._runtime._context(self.scope_id) as context:
                await context.artifacts.memory.validate_citation(citation)
        except (InvalidMemoryCitationError, MemoryEntryNotFoundError, RepositoryNotFoundError, ArtifactNotFoundError) as error:
            raise SourceLifecycleError("memory-not-found") from error

    async def _require_topic(self, artifact_ref: ArtifactRef) -> None:
        try:
            await self._runtime.records.for_scope(self.scope_id).get_artifact_revision(
                artifact_ref.family,
                artifact_ref.artifact_id,
                artifact_ref.revision,
            )
        except (RepositoryNotFoundError, ArtifactNotFoundError) as error:
            raise SourceLifecycleError("topic-not-found") from error


def _binding(node: _Node) -> tuple[NodeFamily, Mapping[str, Any], tuple[str, ...], DependencyPolicy]:
    return (node.family, node.reference, node.parents, node.policy)


def _propagate(nodes: Mapping[str, _Node], source_states: Mapping[str, bool]) -> dict[str, bool]:
    states: dict[str, bool] = {}

    def state(node_id: str, visiting: frozenset[str] = frozenset()) -> bool:
        if node_id in states:
            return states[node_id]
        if node_id in visiting:
            raise SourceLifecycleError("cycle")
        node = nodes[node_id]
        if node.family == SOURCE:
            value = source_states.get(node_id, node.available)
        else:
            flags = [state(parent, visiting | {node_id}) for parent in node.parents]
            value = all(flags) if node.policy == "all" else any(flags)
        states[node_id] = value
        return value

    for node_id in nodes:
        state(node_id)
    return states


def _supported(states: Mapping[str, bool], node: _Node) -> bool:
    flags = [states[parent] for parent in node.parents]
    return all(flags) if node.policy == "all" else any(flags)


def _require_node_id(node_id: str) -> None:
    if not isinstance(node_id, str) or not node_id.strip():
        raise SourceLifecycleError("node-id")


def _require_positive_integer(value: object, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise SourceLifecycleError(field_name)
