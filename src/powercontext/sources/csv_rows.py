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

"""Row-level CSV Sources with exact field fidelity and primary-key identity."""

from __future__ import annotations

import csv
import io
import json
from typing import Annotated
from urllib.parse import quote

from pydantic import BaseModel, Field

from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources import (
    TEXT_EVIDENCE_PROJECTION_KEY,
    AdapterSourceDefinition,
    TextEvidence,
)
from powercontext.sources.models import Source, SourceMaterialization

CSV_ROW_SOURCE_NAME = "csv-row"
NonEmptyText = Annotated[str, Field(min_length=1)]


class CSVDocumentInput(BaseModel):
    """One offline CSV byte payload plus the document and primary-key identity."""

    document_id: NonEmptyText
    primary_key: NonEmptyText
    content: bytes


class CSVRowInput(BaseModel):
    """One parsed CSV row retaining header order and raw string values."""

    document_id: str
    primary_key: str
    primary_key_value: str
    columns: tuple[str, ...]
    values: tuple[str, ...]


class CSVRowSource(Source):
    """A captured CSV row identified by its document and primary-key value."""

    source_id: str
    document_id: str
    primary_key: str
    primary_key_value: str
    columns: tuple[str, ...]
    values: tuple[str, ...]


def parse_csv_document(value: CSVDocumentInput, /) -> tuple[CSVRowInput, ...]:
    """Parse and validate the whole document before returning any row."""

    document_id = _require_trimmed(value.document_id, "document_id")
    primary_key = value.primary_key
    if not primary_key or not primary_key.strip():
        raise ValueError("primary_key must be a non-empty string")  # noqa: TRY003
    try:
        text = value.content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("CSV content must be strict UTF-8") from error  # noqa: TRY003
    if text.startswith("\ufeff"):
        text = text[1:]

    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    try:
        header = _first_record(reader)
        _validate_header(header)
        if primary_key not in header:
            raise ValueError("primary_key must exactly match one header field")  # noqa: TRY003
        primary_index = header.index(primary_key)

        columns = tuple(header)
        width = len(columns)
        rows: list[CSVRowInput] = []
        seen: set[str] = set()
        for record in reader:
            if not record:
                continue
            if len(record) != width:
                raise ValueError("data record width must match the header width")  # noqa: TRY003
            primary_key_value = record[primary_index]
            if not primary_key_value.strip():
                raise ValueError("primary key values must not be blank")  # noqa: TRY003
            if primary_key_value in seen:
                raise ValueError("primary key values must be unique within a document")  # noqa: TRY003
            seen.add(primary_key_value)
            csv_row_source_id(document_id, primary_key_value)
            rows.append(
                CSVRowInput(
                    document_id=document_id,
                    primary_key=primary_key,
                    primary_key_value=primary_key_value,
                    columns=columns,
                    values=tuple(record),
                )
            )
    except csv.Error as error:
        raise ValueError(f"malformed CSV document: {error}") from error  # noqa: TRY003
    return tuple(rows)


def csv_row_source_id(document_id: str, primary_key_value: str, /) -> str:
    """Encode the row identity so components cannot collide across delimiters."""

    document_id = _require_trimmed(document_id, "document_id")
    if not isinstance(primary_key_value, str) or not primary_key_value.strip():
        raise ValueError("primary key value must be a non-empty string")  # noqa: TRY003
    identity = f"csv:{quote(document_id, safe='')}:{quote(primary_key_value, safe='')}"
    if len(identity) > MAX_SOURCE_ID_LENGTH:
        raise ValueError(f"csv row identity must not exceed {MAX_SOURCE_ID_LENGTH} characters")  # noqa: TRY003
    return identity


class CSVRowSourceAdapter:
    """Resolve and read the row-level CSV Source."""

    input_class = CSVRowInput
    name = CSV_ROW_SOURCE_NAME
    source_class = CSVRowSource

    async def resolve(self, value: CSVRowInput, /) -> CSVRowSource:
        return CSVRowSource(
            name=csv_row_source_id(value.document_id, value.primary_key_value),
            source_id=csv_row_source_id(value.document_id, value.primary_key_value),
            materialization=SourceMaterialization.CAPTURED,
            document_id=value.document_id,
            primary_key=value.primary_key,
            primary_key_value=value.primary_key_value,
            columns=value.columns,
            values=value.values,
        )

    async def read(self, source: CSVRowSource, /) -> CSVRowInput:
        return CSVRowInput(
            document_id=source.document_id,
            primary_key=source.primary_key,
            primary_key_value=source.primary_key_value,
            columns=source.columns,
            values=source.values,
        )


class CSVRowTextEvidenceProjection:
    """Expose the full row as one compact JSON object in header order."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = CSVRowSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: CSVRowSource, /) -> TextEvidence:
        row = {column: value for column, value in zip(source.columns, source.values, strict=True)}
        return TextEvidence(
            source_type=CSV_ROW_SOURCE_NAME,
            source_id=source.name,
            content=json.dumps(row, ensure_ascii=False, separators=(",", ":")),
            metadata={
                "document_id": source.document_id,
                "primary_key": source.primary_key,
                "primary_key_value": source.primary_key_value,
                "columns": list(source.columns),
                "row": row,
            },
        )


CSV_ROW_SOURCE_ADAPTER = CSVRowSourceAdapter()
CSV_ROW_SOURCE_DEFINITION = AdapterSourceDefinition(
    CSV_ROW_SOURCE_ADAPTER,
    projections=(CSVRowTextEvidenceProjection(),),
)


def _require_trimmed(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")  # noqa: TRY003
    if value != value.strip():
        raise ValueError(f"{field} must not contain leading or trailing whitespace")  # noqa: TRY003
    return value


def _first_record(reader: csv.reader) -> list[str]:
    try:
        return next(reader)
    except StopIteration:
        raise ValueError("CSV document must not be empty") from None


def _validate_header(header: list[str]) -> None:
    seen: set[str] = set()
    for name in header:
        if not name.strip():
            raise ValueError("header fields must be non-empty and not blank")  # noqa: TRY003
        if name in seen:
            raise ValueError("header fields must be unique")  # noqa: TRY003
        seen.add(name)


__all__ = [
    "CSV_ROW_SOURCE_ADAPTER",
    "CSV_ROW_SOURCE_DEFINITION",
    "CSV_ROW_SOURCE_NAME",
    "CSVDocumentInput",
    "CSVRowInput",
    "CSVRowSource",
    "CSVRowSourceAdapter",
    "CSVRowTextEvidenceProjection",
    "csv_row_source_id",
    "parse_csv_document",
]
