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

"""Coalesce concurrent embedding work behind a bounded vector cache."""

from __future__ import annotations

import asyncio
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from typing import Any

from powercontext.builtin.inference.models import EmbeddingResult, EmbeddingVector, InferenceUsage
from powercontext.builtin.inference.protocols import EmbeddingModel


@dataclass
class _Pending:
    text: str
    future: asyncio.Future[EmbeddingVector]
    batch: _Batch | None = None


@dataclass
class _Batch:
    identity: int
    texts: tuple[str, ...]
    usage: InferenceUsage = field(default_factory=lambda: InferenceUsage(requests=0))


def _consume_exception(future: asyncio.Future[Any]) -> None:
    if not future.cancelled():
        future.exception()


class CoalescingEmbeddingModel:
    """Share in-flight texts across callers and reuse successful vectors."""

    def __init__(
        self,
        model: EmbeddingModel,
        *,
        max_batch_size: int = 64,
        coalesce_ms: float = 5.0,
        cache_size: int = 1024,
    ) -> None:
        if isinstance(max_batch_size, bool) or not isinstance(max_batch_size, int) or max_batch_size < 1:
            raise ValueError("max_batch_size must be a positive integer")  # noqa: TRY003
        if isinstance(cache_size, bool) or not isinstance(cache_size, int) or cache_size < 0:
            raise ValueError("cache_size must be a non-negative integer")  # noqa: TRY003
        if isinstance(coalesce_ms, bool) or not isinstance(coalesce_ms, (int, float)) or coalesce_ms < 0:
            raise ValueError("coalesce_ms must be a non-negative number")  # noqa: TRY003
        self._model = model
        self._max_batch_size = max_batch_size
        self._coalesce_seconds = float(coalesce_ms) / 1000.0
        self._cache_size = cache_size
        self._cache: OrderedDict[str, EmbeddingVector] = OrderedDict()
        self._pending: dict[str, _Pending] = {}
        self._queue: deque[str] = deque()
        self._dispatcher: asyncio.Task[None] | None = None
        self._batch_identity = 0
        self.profile = model.profile

    async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        if not texts:
            return EmbeddingResult(vectors=(), usage=InferenceUsage(requests=0))

        loop = asyncio.get_running_loop()
        vectors: dict[str, EmbeddingVector] = {}
        waited: dict[str, _Pending] = {}
        for text in texts:
            if text in vectors or text in waited:
                continue
            if self._cache_size > 0 and text in self._cache:
                self._cache.move_to_end(text)
                vectors[text] = self._cache[text]
                continue
            pending = self._pending.get(text)
            if pending is None:
                future: asyncio.Future[EmbeddingVector] = loop.create_future()
                future.add_done_callback(_consume_exception)
                pending = _Pending(text=text, future=future)
                self._pending[text] = pending
                self._queue.append(text)
                self._start_dispatcher()
            waited[text] = pending

        for text, pending in waited.items():
            vectors[text] = await asyncio.shield(pending.future)

        batches: dict[int, _Batch] = {}
        for pending in waited.values():
            if pending.batch is not None:
                batches[pending.batch.identity] = pending.batch
        usage = _aggregate(tuple(batch.usage for batch in batches.values()))
        return EmbeddingResult(vectors=tuple(vectors[text] for text in texts), usage=usage)

    def _start_dispatcher(self) -> None:
        if self._dispatcher is None:
            self._dispatcher = asyncio.create_task(self._dispatch())

    async def _dispatch(self) -> None:  # noqa: C901 - one bounded dispatch state machine
        try:
            if self._coalesce_seconds > 0.0:
                await asyncio.sleep(self._coalesce_seconds)
            while self._queue:
                batch_texts: list[str] = []
                while self._queue and len(batch_texts) < self._max_batch_size:
                    text = self._queue.popleft()
                    pending = self._pending.get(text)
                    if pending is None or pending.future.done():
                        continue
                    batch_texts.append(text)
                if not batch_texts:
                    continue
                self._batch_identity += 1
                batch = _Batch(identity=self._batch_identity, texts=tuple(batch_texts))
                for text in batch_texts:
                    pending = self._pending.get(text)
                    if pending is not None:
                        pending.batch = batch
                try:
                    result = await self._model.embed(batch.texts)
                except asyncio.CancelledError:
                    raise
                except BaseException as error:
                    self._fail(batch_texts, error)
                    continue
                if len(result.vectors) != len(batch_texts):
                    self._fail(batch_texts, ValueError("embedding vector count does not match batch size"))
                    continue
                batch.usage = result.usage
                for text, vector in zip(batch_texts, result.vectors, strict=True):
                    if self._cache_size > 0:
                        self._remember(text, vector)
                    pending = self._pending.pop(text, None)
                    if pending is not None and not pending.future.done():
                        pending.future.set_result(vector)
        finally:
            self._dispatcher = None

    def _remember(self, text: str, vector: EmbeddingVector) -> None:
        self._cache[text] = vector
        self._cache.move_to_end(text)
        while len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)

    def _fail(self, texts: list[str], error: BaseException) -> None:
        for text in texts:
            pending = self._pending.pop(text, None)
            if pending is not None and not pending.future.done():
                pending.future.set_exception(error)


def _aggregate(usages: tuple[InferenceUsage, ...]) -> InferenceUsage:
    if not usages:
        return InferenceUsage(requests=0)
    input_tokens = _sum_tokens(usage.input_tokens for usage in usages)
    output_tokens = _sum_tokens(usage.output_tokens for usage in usages)
    return InferenceUsage(
        requests=sum(usage.requests for usage in usages),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


def _sum_tokens(values: Any) -> int | None:
    total: int | None = None
    for value in values:
        if value is None:
            continue
        total = value if total is None else total + value
    return total
