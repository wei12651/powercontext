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

"""Batched HTTP scoring adapter for native Memory reranking."""

from __future__ import annotations

import math
from typing import Any

import httpx

from powercontext.builtin.artifacts.memory.models import MemoryHit
from powercontext.builtin.artifacts.memory.reranking import MemoryRerankDecision, _validate_bounds
from powercontext.builtin.inference import InferenceUsage

DEFAULT_HTTP_RERANK_POLICY_ID = "http-cross-encoder-v1"


class HTTPMemoryReranker:
    """Score coarse Memory candidates through one offline HTTP scoring service."""

    def __init__(
        self,
        endpoint: str,
        *,
        client: httpx.AsyncClient,
        batch_size: int = 32,
        policy_id: str = DEFAULT_HTTP_RERANK_POLICY_ID,
    ) -> None:
        if batch_size < 1:
            raise ValueError("batch_size must be a positive integer")  # noqa: TRY003
        self._endpoint = endpoint
        self._client = client
        self._batch_size = batch_size
        self.policy_id = policy_id

    async def rerank(
        self,
        query: str,
        candidates: tuple[MemoryHit, ...],
        limit: int,
        /,
    ) -> MemoryRerankDecision:
        """Score every candidate once, then select from the complete pool."""

        _validate_bounds(len(candidates), limit)
        scores: dict[int, float] = {}
        requests = 0
        for start in range(0, len(candidates), self._batch_size):
            batch = range(start + 1, min(start + self._batch_size, len(candidates)) + 1)
            payload = {
                "query": query,
                "candidates": [
                    {"rank": rank, "text": candidates[rank - 1].text}
                    for rank in batch
                ],
            }
            response = await self._client.post(self._endpoint, json=payload)
            response.raise_for_status()
            requests += 1
            scores.update(_parse_scores(response, expected_ranks=frozenset(batch)))

        ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
        return MemoryRerankDecision(
            selected_ranks=tuple(rank for rank, _score in ranked[:limit]),
            usage=InferenceUsage(requests=requests),
        )


def _parse_scores(response: httpx.Response, *, expected_ranks: frozenset[int]) -> dict[int, float]:
    try:
        body = response.json()
    except ValueError as error:
        raise ValueError("reranker response must be valid JSON") from error  # noqa: TRY003
    if not isinstance(body, dict) or set(body) != {"scores"}:
        raise ValueError("reranker response must contain exactly the scores field")  # noqa: TRY003
    scores = body["scores"]
    if not isinstance(scores, list):
        raise ValueError("reranker scores must be a list")  # noqa: TRY003

    parsed: dict[int, float] = {}
    for item in scores:
        rank, score = _parse_score(item)
        if rank not in expected_ranks:
            raise ValueError("reranker returned a rank outside the requested batch")  # noqa: TRY003
        if rank in parsed:
            raise ValueError("reranker returned a duplicate rank")  # noqa: TRY003
        parsed[rank] = score
    if set(parsed) != set(expected_ranks):
        raise ValueError("reranker response must cover every requested rank")  # noqa: TRY003
    return parsed


def _parse_score(item: Any) -> tuple[int, float]:
    if not isinstance(item, dict) or set(item) != {"rank", "score"}:
        raise ValueError("each reranker score must contain exactly rank and score")  # noqa: TRY003
    rank = item["rank"]
    score = item["score"]
    if type(rank) is not int:
        raise ValueError("reranker rank must be an integer")  # noqa: TRY003
    if isinstance(score, bool) or not isinstance(score, int | float):
        raise ValueError("reranker score must be a number")  # noqa: TRY003
    value = float(score)
    if not math.isfinite(value):
        raise ValueError("reranker score must be finite")  # noqa: TRY003
    return rank, value


__all__ = ["DEFAULT_HTTP_RERANK_POLICY_ID", "HTTPMemoryReranker"]
