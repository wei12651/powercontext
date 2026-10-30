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

"""Physical-page PDF Sources with document revision identity and page retrieval."""

from __future__ import annotations

import hashlib
import io
import math
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from itertools import pairwise
from typing import TYPE_CHECKING, Any
from urllib.parse import quote, unquote

from pydantic import BaseModel
from pypdf import PdfReader

from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources import TEXT_EVIDENCE_PROJECTION_KEY, AdapterSourceDefinition, TextEvidence
from powercontext.sources.models import Source, SourceMaterialization

if TYPE_CHECKING:
    from powercontext.builtin.artifacts.memory.models import MemoryCitation

PDF_PAGE_SOURCE_NAME = "pdf-page"

_TERM = re.compile(r"[a-z]+|\d+")

_STOPWORDS = frozenset({
    "a", "about", "above", "after", "again", "all", "also", "am", "an", "and", "any", "are",
    "as", "at", "be", "been", "before", "being", "below", "between", "both", "but", "by",
    "can", "could", "did", "do", "does", "doing", "done", "down", "during", "each", "few",
    "for", "from", "further", "had", "has", "have", "having", "he", "her", "here", "hers",
    "him", "his", "how", "i", "if", "in", "into", "is", "it", "its", "itself", "just", "me",
    "more", "most", "my", "no", "nor", "not", "of", "off", "on", "once", "only", "or",
    "other", "our", "ours", "out", "over", "own", "same", "she", "should", "so", "some",
    "such", "than", "that", "the", "their", "theirs", "them", "then", "there", "these",
    "they", "this", "those", "through", "to", "too", "under", "until", "up", "very", "was",
    "we", "were", "what", "when", "where", "which", "while", "who", "whom", "why", "will",
    "with", "would", "you", "your", "yours",
})

_SCAFFOLDING = frozenset({
    "answer", "assume", "assuming", "calculate", "calculation", "company", "concisely",
    "define", "defined", "details", "explicitly", "give", "given", "materially", "mention",
    "mentioned", "outline", "outlined", "perspective", "please", "provide", "provided",
    "quantity", "question", "rely", "relying", "respond", "response", "shown", "state",
    "stated", "supplied", "usable", "use", "using",
})

# Finance abbreviations whose expanded wording is what filings actually print.
_QUESTION_REWRITES = (
    ("p&l", " income statement "),
    ("p & l", " income statement "),
    ("d&a", " depreciation and amortization "),
    ("d & a", " depreciation and amortization "),
)

# Structural words that appear on nearly every page and rarely identify the answer page.
_WEAK_TERMS = frozenset({
    "amount", "amounts", "based", "certain", "consolidated", "financial", "fiscal", "include",
    "included", "including", "period", "related", "respective", "statement", "statements",
    "total", "year", "years",
})

# Small, provider-neutral finance vocabulary. Weights are deliberately below exact terms.
_SYNONYMS: dict[str, tuple[str, ...]] = {
    "battles": (
        "litigation", "proceedings", "lawsuits", "claims", "investigations", "regulatory",
    ),
    "capex": ("capital", "expenditures", "expenditure", "property", "plant", "equipment"),
    "charges": ("costs", "expenses", "impairment", "restructuring"),
    "costs": ("cost", "expenses", "charges"),
    "debt": ("borrowings", "notes", "obligations"),
    "dividend": ("dividends", "payout"),
    "ebit": ("operating", "income", "profit"),
    "ebitda": ("operating", "income", "profit", "depreciation", "amortization"),
    "employees": ("headcount", "personnel"),
    "expenses": ("costs", "charges"),
    "geographic": (
        "geographies", "geographical", "countries", "territories", "regions", "markets",
        "worldwide", "global",
    ),
    "geographical": (
        "geographic", "geographies", "countries", "territories", "regions", "markets",
        "worldwide", "global",
    ),
    "geographies": (
        "geographic", "geographical", "countries", "territories", "regions", "markets",
        "worldwide", "global", "segment", "segments", "divisions",
    ),
    "geography": (
        "geographic", "geographical", "countries", "territories", "regions", "markets",
        "worldwide", "global",
    ),
    "headcount": ("employees", "personnel"),
    "impairment": ("impairments", "writeoff", "writedown"),
    "important": ("material", "significant"),
    "income": ("profit", "earnings", "revenue"),
    "legal": (
        "litigation", "proceedings", "lawsuits", "claims", "investigations", "regulatory",
    ),
    "litigation": ("legal", "proceedings", "lawsuits", "claims", "regulatory"),
    "margin": ("margins", "profitability"),
    "ongoing": ("pending", "continuing", "existing"),
    "operate": ("operating", "operations", "operates"),
    "operates": ("operating", "operations", "operate"),
    "operating": ("operations", "operate", "operates"),
    "profit": ("income", "earnings"),
    "repurchase": ("buyback", "repurchases"),
    "revenue": ("sales", "revenues", "income"),
    "sales": ("revenue", "revenues"),
    "shares": ("share", "stock"),
    "stock": ("shares", "equity"),
    "buyback": ("repurchase", "repurchases"),
}


class PDFDocumentInput(BaseModel):
    """One original PDF byte payload plus the caller's document identity."""

    document_id: str
    content: bytes


class PDFPageInput(BaseModel):
    """One physical PDF page with document revision identity."""

    document_id: str
    document_sha256: str
    page_number: int
    text: str


class PDFPageSource(Source):
    """A captured physical page identified by document revision and page number."""

    source_id: str
    document_id: str
    document_sha256: str
    page_number: int
    text: str


def parse_pdf_document(value: PDFDocumentInput, /) -> tuple[PDFPageInput, ...]:
    """Parse every physical page or fail without returning any partial page."""

    document_id = value.document_id
    if not document_id.strip():
        raise ValueError("document_id must be a non-empty string")  # noqa: TRY003
    content = bytes(value.content)
    document_sha256 = hashlib.sha256(content).hexdigest()
    try:
        reader = PdfReader(io.BytesIO(content))
        if reader.is_encrypted and not reader.decrypt(""):
            raise ValueError("PDF requires a user password")  # noqa: TRY003
        pages: list[PDFPageInput] = []
        for index, page in enumerate(reader.pages, start=1):
            text = page.extract_text() or ""
            pages.append(
                PDFPageInput(
                    document_id=document_id,
                    document_sha256=document_sha256,
                    page_number=index,
                    text=unicodedata.normalize("NFC", text).strip(),
                )
            )
    except ValueError:
        raise
    except Exception as error:
        raise ValueError("PDF document could not be parsed completely") from error  # noqa: TRY003
    return tuple(pages)


def pdf_page_source_id(document_id: str, document_sha256: str, page_number: int, /) -> str:
    """Encode document revision and physical page so identities cannot collide."""

    if not isinstance(document_id, str) or not document_id.strip():
        raise ValueError("document_id must be a non-empty string")  # noqa: TRY003
    if (
        not isinstance(document_sha256, str)
        or len(document_sha256) != 64
        or any(character not in "0123456789abcdef" for character in document_sha256)
    ):
        raise ValueError("document_sha256 must be a lowercase hexadecimal SHA-256 digest")  # noqa: TRY003
    if not isinstance(page_number, int) or isinstance(page_number, bool) or page_number < 1:
        raise ValueError("page_number must be a positive integer")  # noqa: TRY003
    identity = f"pdf:{quote(document_id, safe='')}:{document_sha256}:{page_number}"
    if len(identity) <= MAX_SOURCE_ID_LENGTH:
        return identity
    digest = hashlib.sha256(f"{document_id}\0{document_sha256}\0{page_number}".encode()).hexdigest()
    return f"pdf:hashed:{digest}"


class PDFPageSourceAdapter:
    """Resolve and read page-level PDF Sources."""

    input_class = PDFPageInput
    name = PDF_PAGE_SOURCE_NAME
    source_class = PDFPageSource

    async def resolve(self, value: PDFPageInput, /) -> PDFPageSource:
        return PDFPageSource(
            name=pdf_page_source_id(value.document_id, value.document_sha256, value.page_number),
            source_id=pdf_page_source_id(value.document_id, value.document_sha256, value.page_number),
            materialization=SourceMaterialization.CAPTURED,
            document_id=value.document_id,
            document_sha256=value.document_sha256,
            page_number=value.page_number,
            text=value.text,
        )

    async def read(self, source: PDFPageSource, /) -> PDFPageInput:
        return PDFPageInput(
            document_id=source.document_id,
            document_sha256=source.document_sha256,
            page_number=source.page_number,
            text=source.text,
        )


class PDFPageTextEvidenceProjection:
    """Expose the document identity and full page text as one evidence body."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = PDFPageSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: PDFPageSource, /) -> TextEvidence:
        return TextEvidence(
            source_type=PDF_PAGE_SOURCE_NAME,
            source_id=source.name,
            content=f"{source.document_id}\n{source.text}",
            metadata={
                "document_id": source.document_id,
                "document_sha256": source.document_sha256,
                "page_number": source.page_number,
            },
        )


PDF_PAGE_SOURCE_ADAPTER = PDFPageSourceAdapter()
PDF_PAGE_SOURCE_DEFINITION = AdapterSourceDefinition(
    PDF_PAGE_SOURCE_ADAPTER,
    projections=(PDFPageTextEvidenceProjection(),),
)


async def search_pdf_pages(
    memory: Any,
    question: str,
    limit: int = 10,
) -> tuple[MemoryCitation, ...]:
    """Rank stored page evidence for one natural-language question."""

    if not isinstance(question, str) or not question.strip():
        raise ValueError("question must be a non-empty string")  # noqa: TRY003
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise ValueError("limit must be a positive integer")  # noqa: TRY003

    pages = await _stored_pages(memory, question, limit)
    if not pages:
        return ()
    query = _Question(question)
    corpus = [_PageEvidence(page.text) for page in pages]
    document_frequency = _document_frequency(corpus, query.weights)
    document_count = len(corpus)
    idf = {
        term: math.log(1.0 + (document_count - document_frequency[term] + 0.5) / (document_frequency[term] + 0.5))
        for term in query.weights
    }
    average_length = sum(item.length for item in corpus) / document_count or 1.0
    scored = [
        (query.score(item, idf, document_frequency, average_length), index)
        for index, item in enumerate(corpus)
    ]
    scored.sort(key=lambda pair: (-pair[0], pair[1]))

    selected: list[MemoryCitation] = []
    seen: set[tuple[Any, ...]] = set()
    for _score, index in scored:
        page = pages[index]
        if page.identity in seen:
            continue
        seen.add(page.identity)
        selected.append(page.citation)
        if len(selected) == limit:
            break
    return tuple(selected)


@dataclass(frozen=True)
class _StoredPage:
    document_id: str
    page_number: int
    text: str
    citation: MemoryCitation

    @property
    def identity(self) -> tuple[Any, ...]:
        if self.page_number > 0:
            return (self.document_id, self.page_number)
        return (self.citation.memory_ref.artifact_id, self.citation.entry_id)


async def _stored_pages(memory: Any, question: str, limit: int) -> list[_StoredPage]:
    lister = getattr(memory, "list", None)
    if lister is not None:
        result = await lister()
        pages = [page for record in result.entries if (page := _page_from_record(record)) is not None]
        if pages:
            return pages
    return await _searched_pages(memory, question, limit)


def _page_from_record(record: Any) -> _StoredPage | None:
    entry = record.entry
    coordinates = _source_coordinates(entry.sources)
    if coordinates is None:
        return None
    document_id, page_number = coordinates
    prefix = unicodedata.normalize("NFC", document_id)
    text = entry.text
    if text == prefix:
        return None
    if not text.startswith(prefix + "\n"):
        return None
    return _StoredPage(
        document_id=document_id,
        page_number=page_number,
        text=text[len(prefix) + 1 :],
        citation=record.citation,
    )


def _source_coordinates(sources: tuple[Any, ...]) -> tuple[str, int] | None:
    for source in sources:
        if source.source_type != PDF_PAGE_SOURCE_NAME:
            continue
        parts = source.source_id.split(":")
        if len(parts) != 4 or parts[0] != "pdf":
            continue
        try:
            page_number = int(parts[3])
        except ValueError:
            continue
        if page_number < 1:
            continue
        return unquote(parts[1]), page_number
    return None


async def _searched_pages(memory: Any, question: str, limit: int) -> list[_StoredPage]:
    from powercontext.builtin.artifacts.memory.models import MemoryCitation
    from powercontext.builtin.runtime import SearchMemoryRequest

    result = await memory.search(
        SearchMemoryRequest(query=question, mode="fts", limit=max(limit * 10, 100))
    )
    pages: list[_StoredPage] = []
    for hit in result.hits:
        document_id, _, text = hit.text.partition("\n")
        if not text:
            continue
        pages.append(
            _StoredPage(
                document_id=document_id,
                page_number=0,
                text=text,
                citation=MemoryCitation(
                    memory_ref=hit.memory_ref,
                    entry_id=hit.entry_id,
                    entry_version_id=hit.entry_version_id,
                ),
            )
        )
    return pages


class _Question:
    """Question-side lexical evidence used to rerank stored pages offline."""

    def __init__(self, value: str) -> None:
        value = value.casefold()
        for abbreviation, expansion in _QUESTION_REWRITES:
            value = value.replace(abbreviation, expansion)
        tokens = _TERM.findall(unicodedata.normalize("NFC", value))
        content = [token for token in tokens if token not in _STOPWORDS and token not in _SCAFFOLDING]
        if not content:
            content = tokens
        self.content = tuple(dict.fromkeys(content))
        weights: dict[str, float] = {}
        for token in content:
            weights[token] = min(weights.get(token, 0.0) + 1.0, 2.0)
        for token in self.content:
            if token in _WEAK_TERMS:
                weights[token] *= 0.5
            for variant in _variants(token):
                weights[variant] = max(weights.get(variant, 0.0), 0.6 * weights[token])
            for synonym in _SYNONYMS.get(token, ()):
                weights[synonym] = max(weights.get(synonym, 0.0), 0.45 * weights[token])
        self.weights = weights
        self.bigrams = tuple(pairwise(self.content))
        self.trigrams = tuple(zip(self.content, self.content[1:], self.content[2:]))
        self.concept_pairs = tuple(
            (_expansions(left), _expansions(right)) for left, right in self.bigrams
        )

    def score(
        self,
        page: _PageEvidence,
        idf: dict[str, float],
        document_frequency: Counter[str],
        average_length: float,
    ) -> float:
        length_ratio = page.length / average_length
        score = 0.0
        for term, weight in self.weights.items():
            frequency = page.terms.get(term, 0)
            if not frequency:
                continue
            saturation = frequency * 2.2 / (frequency + 1.2 * (0.25 + 0.75 * length_ratio))
            score += weight * idf[term] * saturation
        for phrase in self.bigrams:
            if phrase in page.bigrams:
                score += 1.1 * (idf[phrase[0]] + idf[phrase[1]]) / 2.0
        for phrase in self.trigrams:
            if phrase in page.trigrams:
                score += 1.6 * sum(idf[term] for term in phrase) / 3.0
        for left, right in self.concept_pairs:
            if any(term in page.terms for term in left) and any(term in page.terms for term in right):
                score += 0.35 * (
                    max((idf[term] for term in left if term in page.terms), default=0.0)
                    + max((idf[term] for term in right if term in page.terms), default=0.0)
                ) / 2.0
            adjacency = max(
                (
                    (idf[first] + idf[second]) / 2.0
                    for first in left
                    for second in right
                    if (first, second) in page.bigrams
                ),
                default=0.0,
            )
            score += adjacency
        if page.heading_terms:
            score += 0.12 * sum(
                min(idf[term], 5.0) for term in self.weights if term in page.heading_terms
            )
        if page.title_terms:
            score += 0.6 * sum(
                min(idf[term], 5.0) for term in self.weights if term in page.title_terms
            )
        if self.content:
            denominator = sum(
                idf[term] * self.weights.get(term, 1.0)
                for term in self.content
                if document_frequency[term]
            )
            if denominator:
                covered = sum(
                    idf[term] * self.weights.get(term, 1.0)
                    for term in self.content
                    if document_frequency[term]
                    and any(candidate in page.terms for candidate in _expansions(term))
                )
                score *= 0.4 + 0.6 * (covered / denominator)
        if page.statement_page and self.weights.get("statement", 0.0) > 0.0:
            score += 5.0
        return score


class _PageEvidence:
    __slots__ = (
        "bigrams",
        "heading_terms",
        "length",
        "statement_page",
        "terms",
        "title_terms",
        "trigrams",
    )

    def __init__(self, text: str) -> None:
        normalized = unicodedata.normalize("NFC", text).casefold()
        tokens = _TERM.findall(normalized)
        self.length = max(len(tokens), 1)
        self.terms = Counter(tokens)
        self.bigrams = frozenset(pairwise(tokens))
        self.trigrams = frozenset(zip(tokens, tokens[1:], tokens[2:]))
        headings: set[str] = set()
        title: set[str] = set()
        title_lines = 0
        statement_page = False
        header_lines = 0
        for line in normalized.splitlines():
            stripped = line.strip()
            if not stripped or stripped == "table of contents":
                continue
            if len(stripped) <= 90:
                headings.update(_TERM.findall(stripped))
            if title_lines < 3 and len(stripped) <= 140:
                title.update(_TERM.findall(stripped))
                title_lines += 1
            if header_lines < 6:
                header_lines += 1
                if "consolidated" in stripped and (
                    "statement of" in stripped
                    or "statements of" in stripped
                    or "balance sheet" in stripped
                ):
                    statement_page = True
        self.heading_terms = frozenset(headings)
        self.title_terms = frozenset(title)
        self.statement_page = statement_page


def _document_frequency(pages: list[_PageEvidence], weights: dict[str, float]) -> Counter[str]:
    frequency: Counter[str] = Counter()
    for page in pages:
        for term in weights.keys() & page.terms.keys():
            frequency[term] += 1
    return frequency


def _expansions(term: str) -> frozenset[str]:
    return frozenset((term, *_variants(term), *_SYNONYMS.get(term, ())))


def _variants(term: str) -> tuple[str, ...]:
    variants: set[str] = set()
    if term.endswith("ies") and len(term) > 4:
        variants.add(f"{term[:-3]}y")
    if term.endswith("ical") and len(term) > 6:
        variants.add(f"{term[:-4]}y")
        variants.add(term[:-6])
    if term.endswith("ally") and len(term) > 6:
        variants.add(term[:-4])
        variants.add(term[:-2])
    if term.endswith("ly") and len(term) > 5:
        variants.add(term[:-2])
    if term.endswith("ing") and len(term) > 5:
        variants.add(term[:-3])
        variants.add(f"{term[:-3]}e")
    if term.endswith("ed") and len(term) > 4:
        variants.add(term[:-2])
        variants.add(term[:-1])
    if term.endswith("es") and len(term) > 4:
        variants.add(term[:-2])
    if term.endswith("s") and len(term) > 3:
        variants.add(term[:-1])
    if term.endswith("al") and len(term) > 5:
        variants.add(term[:-2])
    variants.discard(term)
    return tuple(variants)


__all__ = [
    "PDF_PAGE_SOURCE_ADAPTER",
    "PDF_PAGE_SOURCE_DEFINITION",
    "PDF_PAGE_SOURCE_NAME",
    "PDFDocumentInput",
    "PDFPageInput",
    "PDFPageSource",
    "PDFPageSourceAdapter",
    "PDFPageTextEvidenceProjection",
    "parse_pdf_document",
    "pdf_page_source_id",
    "search_pdf_pages",
]
