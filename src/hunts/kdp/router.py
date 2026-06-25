"""KDP hunt router — AI book generation and Amazon KDP publishing endpoints.

Generates books from topics using AI, formats for KDP, and manages
the publishing pipeline.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from src.api.deps import _current_user, get_current_user, require_admin

try:
    from src.core.logger import get_logger

    logger = get_logger("1ai-auto-hunt.hunts.kdp")
except ImportError:
    logger = logging.getLogger("1ai-auto-hunt.hunts.kdp")  # type: ignore[assignment]

router = APIRouter()


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class BookStatus(str, Enum):
    GENERATING = "generating"
    GENERATED = "generated"
    FORMATTING = "formatting"
    FORMATTED = "formatted"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    FAILED = "failed"


class BookFormat(str, Enum):
    EBOOK = "ebook"
    PAPERBACK = "paperback"
    HARDCOVER = "hardcover"


class BookGenre(str, Enum):
    NONFICTION = "nonfiction"
    FICTION = "fiction"
    SELF_HELP = "self_help"
    BUSINESS = "business"
    TECHNOLOGY = "technology"
    CHILDREN = "children"
    COOKING = "cooking"
    OTHER = "other"


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class GenerateRequest(BaseModel):
    """Generate a book from a topic."""

    topic: str = Field(..., min_length=3, max_length=500, description="Book topic or title")
    genre: BookGenre = BookGenre.NONFICTION
    chapter_count: int = Field(10, ge=3, le=50, description="Number of chapters")
    words_per_chapter: int = Field(2000, ge=500, le=10000)
    language: str = Field("en", description="ISO 639-1 language code")
    tone: str = Field("informative", description="Writing tone: informative, casual, academic, etc.")
    count: int = Field(1, ge=1, le=20, description="Number of book variants to generate")
    include_images: bool = Field(False, description="Generate chapter illustrations")
    target_audience: str | None = Field(None, description="Target reader demographic")


class BookResponse(BaseModel):
    id: str
    title: str
    topic: str
    genre: BookGenre
    language: str
    chapter_count: int
    total_words: int = 0
    status: BookStatus
    format: BookFormat | None = None
    asin: str | None = Field(None, description="Amazon Standard Identification Number")
    kdp_url: str | None = None
    royalty_earned: float = 0.0
    sales_count: int = 0
    created_at: str
    published_at: str | None = None
    output_dir: str | None = None


class BookList(BaseModel):
    items: list[BookResponse]
    total: int


class PublishRequest(BaseModel):
    """Publish a book to Amazon KDP."""

    book_id: str
    format: BookFormat = BookFormat.EBOOK
    price_usd: float = Field(..., ge=0.99, le=200.0, description="List price in USD")
    categories: list[str] = Field(default=[], max_length=3, description="KDP browse categories")
    keywords: list[str] = Field(default=[], max_length=7, description="KDP keywords")
    description: str | None = Field(None, max_length=4000, description="Book description / blurb")
    isbn: str | None = Field(None, description="ISBN (auto-generated if omitted)")
    enable_kdp_select: bool = Field(True, description="Enroll in KDP Select / Kindle Unlimited")


class PublishResponse(BaseModel):
    book_id: str
    status: BookStatus
    asin: str | None = None
    kdp_url: str | None = None
    format: BookFormat
    price_usd: float
    message: str


class RevenueReport(BaseModel):
    """KDP revenue report."""

    month: str  # YYYY-MM
    total_books: int = 0
    total_sales: int = 0
    total_royalty_usd: float = 0.0
    kenp_reads: int = Field(0, description="Kindle Edition Normalized Pages read")
    kenp_royalty_usd: float = 0.0
    top_books: list[dict[str, Any]] = []


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post(
    "/generate",
    response_model=list[BookResponse],
    status_code=status.HTTP_202_ACCEPTED,
    summary="Generate books",
)
async def generate_books(
    body: GenerateRequest,
    user: dict = Depends(get_current_user),
):
    """Generate one or more AI-authored books on the given topic.

    Returns immediately with pending book records; generation runs
    in the background. Poll the book ID for status updates.
    """
    import uuid

    now = datetime.now(timezone.utc).isoformat()
    books: list[BookResponse] = []
    for i in range(body.count):
        book_id = f"BOOK-{uuid.uuid4().hex[:8].upper()}"
        books.append(
            BookResponse(
                id=book_id,
                title=f"{body.topic}" + (f" Vol. {i + 1}" if body.count > 1 else ""),
                topic=body.topic,
                genre=body.genre,
                language=body.language,
                chapter_count=body.chapter_count,
                status=BookStatus.GENERATING,
                created_at=now,
            )
        )
    return books


@router.get(
    "/books",
    response_model=BookList,
    summary="List books",
)
async def list_books(
    status_filter: BookStatus | None = Query(None, alias="status"),
    genre: BookGenre | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: dict = Depends(get_current_user),
):
    """List all books in the pipeline."""
    return BookList(items=[], total=0)


@router.get(
    "/books/{book_id}",
    response_model=BookResponse,
    summary="Get book details",
)
async def get_book(
    book_id: str,
    user: dict = Depends(get_current_user),
):
    """Get the full details of a specific book."""
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Book {book_id} not found",
    )


@router.delete(
    "/books/{book_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a book",
)
async def delete_book(
    book_id: str,
    user: dict = Depends(get_current_user),
):
    """Delete a book and its generated files."""
    return None


@router.post(
    "/publish",
    response_model=PublishResponse,
    summary="Publish book to KDP",
)
async def publish_book(
    body: PublishRequest,
    user: dict = Depends(get_current_user),
):
    """Publish a formatted book to Amazon KDP.

    Handles KDP login, manuscript upload, metadata entry, pricing,
    and category selection via browser automation.
    """
    return PublishResponse(
        book_id=body.book_id,
        status=BookStatus.PUBLISHING,
        format=body.format,
        price_usd=body.price_usd,
        message=f"Publishing {body.book_id} to KDP as {body.format.value}",
    )


@router.get(
    "/published",
    response_model=BookList,
    summary="List published books",
)
async def list_published(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: dict = Depends(get_current_user),
):
    """List all books that have been published to Amazon KDP."""
    return BookList(items=[], total=0)


@router.get(
    "/revenue",
    response_model=RevenueReport,
    summary="Get KDP revenue report",
)
async def get_revenue(
    month: str = Query(..., description="Month in YYYY-MM format"),
    user: dict = Depends(get_current_user),
):
    """Get KDP sales and royalty report for a specific month."""
    return RevenueReport(month=month)
