"""BookGenerator — LLM-powered non-fiction book generation.

Generates complete books chapter-by-chapter using Claude or GPT.
Each chapter is produced as Markdown with proper headings, structure,
and a target word count. The generator handles:

    - Outline creation from a topic + chapter count
    - Per-chapter content generation with style consistency
    - Intro/conclusion generation
    - Markdown assembly ready for Pandoc compilation

Supports both Anthropic Claude and OpenAI GPT backends.
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from src.core.config import get_settings, AIConfig, KDPConfig
from src.core.db import get_db, Database
from src.core.logger import get_logger

log = get_logger(__name__)


# ── Data Models ──────────────────────────────────────────────────

@dataclass(frozen=True)
class ChapterSpec:
    """Specification for a single chapter."""
    number: int
    title: str
    summary: str
    target_words: int


@dataclass(frozen=True)
class ChapterContent:
    """Generated content for a chapter."""
    id: str
    book_id: str
    chapter_num: int
    title: str
    content_md: str
    word_count: int


@dataclass(frozen=True)
class BookSpec:
    """Full book specification before generation."""
    id: str
    topic: str
    title: str
    subtitle: str
    author_name: str
    chapters: list[ChapterSpec]
    language: str
    target_words_per_chapter: int


@dataclass(frozen=True)
class GeneratedBook:
    """Result of a complete book generation."""
    id: str
    topic: str
    title: str
    subtitle: str
    author_name: str
    chapters: list[ChapterContent]
    total_words: int
    output_dir: Path


# ── Prompt Templates ─────────────────────────────────────────────

OUTLINE_SYSTEM = """You are a non-fiction book outline specialist. Given a topic,
create a detailed chapter outline for a practical, informative book.
Each chapter should have a clear title and a 2–3 sentence summary of what it covers.
Return ONLY valid JSON."""

OUTLINE_USER = """Topic: {topic}
Number of chapters: {chapter_count}
Target audience: General readers interested in {topic}

Create the outline as a JSON array of objects with keys:
- "number": chapter number (starting from 1)
- "title": chapter title
- "summary": 2–3 sentence summary of the chapter content
"""

CHAPTER_SYSTEM = """You are a non-fiction book author. Write engaging, informative
content that is practical and well-structured. Use clear headings, bullet points,
and examples where appropriate. Maintain a consistent, professional but approachable
tone throughout. NEVER use filler phrases or padding — every sentence should add value.
Write in Markdown format with ## for section headings within the chapter."""

CHAPTER_USER = """Book: "{title}" by {author}
Chapter {number}: "{chapter_title}"
Chapter summary: {summary}

Target length: approximately {target_words} words.

Write the complete chapter content in Markdown. Start with the chapter title as
a level-1 heading (# Chapter {number}: {chapter_title}), then use ## for sections.
Include practical examples, actionable advice, and clear explanations.
"""

INTRO_SYSTEM = """You are a non-fiction book author writing an introduction.
The introduction should hook the reader, establish the book's value proposition,
and provide a brief roadmap of what's coming. Write in Markdown."""

INTRO_USER = """Book: "{title}" by {author}
Subtitle: {subtitle}

Chapters:
{chapter_list}

Write a compelling introduction (~800 words) that:
1. Opens with a hook relevant to the topic
2. Explains who this book is for
3. Outlines what the reader will learn
4. Sets the tone for the rest of the book

Use Markdown formatting."""

CONCLUSION_SYSTEM = """You are a non-fiction book author writing a conclusion.
Summarize key takeaways, inspire action, and leave the reader with a clear
next step. Write in Markdown."""

CONCLUSION_USER = """Book: "{title}" by {author}

Chapter topics covered:
{chapter_list}

Write a conclusion (~600 words) that:
1. Recaps the most important insights
2. Provides a "what to do next" action plan
3. Ends with an inspiring call to action

Use Markdown formatting."""


# ── LLM Client Abstraction ──────────────────────────────────────

class LLMClient:
    """Unified interface for Claude and GPT APIs.

    Falls back gracefully between providers based on available keys.
    """

    def __init__(self, cfg: AIConfig | None = None) -> None:
        self._cfg = cfg or get_settings().ai

    @property
    def _use_anthropic(self) -> bool:
        return bool(self._cfg.anthropic_api_key)

    @property
    def _use_openai(self) -> bool:
        return bool(self._cfg.openai_api_key)

    async def generate(
        self,
        system: str,
        user: str,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> str:
        """Generate text from a system + user prompt.

        Tries Anthropic first, falls back to OpenAI.
        """
        if self._use_anthropic:
            return await self._call_anthropic(system, user, max_tokens, temperature)
        if self._use_openai:
            return await self._call_openai(system, user, max_tokens, temperature)
        raise RuntimeError("No LLM API key configured. Set ANTHROPIC_API_KEY or OPENAI_API_KEY.")

    async def generate_json(
        self,
        system: str,
        user: str,
        max_tokens: int = 4096,
        temperature: float = 0.3,
    ) -> Any:
        """Generate and parse JSON output."""
        raw = await self.generate(system, user, max_tokens, temperature)
        # Extract JSON from potential markdown code fences
        json_match = re.search(r"```(?:json)?\s*\n?(.*?)```", raw, re.DOTALL)
        text = json_match.group(1).strip() if json_match else raw.strip()
        return json.loads(text)

    async def _call_anthropic(
        self, system: str, user: str, max_tokens: int, temperature: float
    ) -> str:
        """Call the Anthropic Messages API."""
        import httpx

        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": self._cfg.anthropic_api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": self._cfg.default_model,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                    "system": system,
                    "messages": [{"role": "user", "content": user}],
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return data["content"][0]["text"]

    async def _call_openai(
        self, system: str, user: str, max_tokens: int, temperature: float
    ) -> str:
        """Call the OpenAI Chat Completions API."""
        import httpx

        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {self._cfg.openai_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "gpt-4o",
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]


# ── BookGenerator ────────────────────────────────────────────────

class BookGenerator:
    """Generate non-fiction books from a topic using LLM.

    The generation pipeline:
        1. Generate outline (chapter titles + summaries)
        2. Generate introduction
        3. Generate each chapter
        4. Generate conclusion
        5. Assemble into markdown files in output directory
        6. Persist metadata to database

    Args:
        llm: LLM client (defaults to auto-configured).
        db: Database instance (defaults to singleton).
        kdp_cfg: KDP configuration (defaults to settings).

    Usage:
        gen = BookGenerator()
        book = await gen.generate(topic="AI for beginners", chapters=10)
        print(book.output_dir)  # Path to markdown files
    """

    def __init__(
        self,
        llm: LLMClient | None = None,
        db: Database | None = None,
        kdp_cfg: KDPConfig | None = None,
    ) -> None:
        self._llm = llm or LLMClient()
        self._db = db or get_db()
        self._cfg = kdp_cfg or get_settings().kdp

    async def generate(
        self,
        topic: str,
        chapters: int = 10,
        title: str | None = None,
        author: str = "AI Author",
        language: str = "en",
        words_per_chapter: int = 2000,
    ) -> GeneratedBook:
        """Generate a complete book on the given topic.

        Args:
            topic: Book topic / subject area.
            chapters: Number of chapters (capped at max_chapters).
            title: Book title (auto-generated from topic if None).
            author: Author name for the book.
            language: ISO language code.
            words_per_chapter: Target word count per chapter.

        Returns:
            GeneratedBook with all chapter content and file paths.
        """
        book_id = str(uuid.uuid4())
        chapters = min(chapters, self._cfg.max_chapters)

        log.info("kdp.generate_start", topic=topic, chapters=chapters, book_id=book_id)

        # Step 1: Generate outline
        spec = await self._generate_outline(book_id, topic, chapters, title, author, language, words_per_chapter)
        log.info("kdp.outline_generated", title=spec.title, chapters=len(spec.chapters))

        # Step 2: Generate introduction
        intro_md = await self._generate_intro(spec)

        # Step 3: Generate each chapter (sequentially for style consistency)
        chapter_contents: list[ChapterContent] = []
        for ch_spec in spec.chapters:
            ch = await self._generate_chapter(spec, ch_spec)
            chapter_contents.append(ch)
            log.info("kdp.chapter_done", chapter=ch.chapter_num, words=ch.word_count)

        # Step 4: Generate conclusion
        conclusion_md = await self._generate_conclusion(spec)

        # Step 5: Assemble output
        output_dir = self._assemble_book(spec, intro_md, chapter_contents, conclusion_md)

        # Step 6: Persist to database
        total_words = sum(ch.word_count for ch in chapter_contents)
        self._persist_book(spec, chapter_contents, output_dir, total_words)

        result = GeneratedBook(
            id=book_id,
            topic=topic,
            title=spec.title,
            subtitle=spec.subtitle,
            author_name=author,
            chapters=chapter_contents,
            total_words=total_words,
            output_dir=output_dir,
        )

        log.info(
            "kdp.generate_complete",
            title=spec.title,
            total_words=total_words,
            chapters=len(chapter_contents),
            output_dir=str(output_dir),
        )
        return result

    # ── Outline ─────────────────────────────────────────────────

    async def _generate_outline(
        self,
        book_id: str,
        topic: str,
        chapter_count: int,
        title: str | None,
        author: str,
        language: str,
        words_per_chapter: int,
    ) -> BookSpec:
        """Use LLM to create a chapter outline from the topic."""
        user_prompt = OUTLINE_USER.format(
            topic=topic,
            chapter_count=chapter_count,
        )
        outline_data = await self._llm.generate_json(OUTLINE_SYSTEM, user_prompt)

        chapters = [
            ChapterSpec(
                number=ch["number"],
                title=ch["title"],
                summary=ch["summary"],
                target_words=words_per_chapter,
            )
            for ch in outline_data
        ]

        # Auto-generate title if not provided
        if title is None:
            title = await self._generate_title(topic, chapters)

        subtitle = f"A Practical Guide to {topic}"

        return BookSpec(
            id=book_id,
            topic=topic,
            title=title,
            subtitle=subtitle,
            author_name=author,
            chapters=chapters,
            language=language,
            target_words_per_chapter=words_per_chapter,
        )

    async def _generate_title(self, topic: str, chapters: list[ChapterSpec]) -> str:
        """Generate a catchy book title from the topic and outline."""
        chapter_list = "\n".join(f"- {ch.title}" for ch in chapters)
        raw = await self._llm.generate(
            system="You are a book title specialist. Create a catchy, marketable non-fiction title. Return ONLY the title, nothing else.",
            user=f"Topic: {topic}\n\nChapters:\n{chapter_list}\n\nCreate a single compelling book title (max 60 characters).",
            max_tokens=100,
            temperature=0.8,
        )
        # Clean up — remove quotes and extra whitespace
        title = raw.strip().strip('"').strip("'")
        return title[:60] if len(title) > 60 else title

    # ── Chapter Generation ──────────────────────────────────────

    async def _generate_chapter(self, spec: BookSpec, ch: ChapterSpec) -> ChapterContent:
        """Generate a single chapter using the LLM."""
        user_prompt = CHAPTER_USER.format(
            title=spec.title,
            author=spec.author_name,
            number=ch.number,
            chapter_title=ch.title,
            summary=ch.summary,
            target_words=ch.target_words,
        )

        content_md = await self._llm.generate(
            CHAPTER_SYSTEM, user_prompt, max_tokens=8192, temperature=0.7
        )
        word_count = len(content_md.split())

        chapter_id = str(uuid.uuid4())
        return ChapterContent(
            id=chapter_id,
            book_id=spec.id,
            chapter_num=ch.number,
            title=ch.title,
            content_md=content_md,
            word_count=word_count,
        )

    async def _generate_intro(self, spec: BookSpec) -> str:
        """Generate the book introduction."""
        chapter_list = "\n".join(
            f"Chapter {ch.number}: {ch.title} — {ch.summary}" for ch in spec.chapters
        )
        return await self._llm.generate(
            INTRO_SYSTEM,
            INTRO_USER.format(
                title=spec.title,
                author=spec.author_name,
                subtitle=spec.subtitle,
                chapter_list=chapter_list,
            ),
            max_tokens=4096,
            temperature=0.7,
        )

    async def _generate_conclusion(self, spec: BookSpec) -> str:
        """Generate the book conclusion."""
        chapter_list = "\n".join(
            f"Chapter {ch.number}: {ch.title}" for ch in spec.chapters
        )
        return await self._llm.generate(
            CONCLUSION_SYSTEM,
            CONCLUSION_USER.format(
                title=spec.title,
                author=spec.author_name,
                chapter_list=chapter_list,
            ),
            max_tokens=3072,
            temperature=0.7,
        )

    # ── Assembly ────────────────────────────────────────────────

    def _assemble_book(
        self,
        spec: BookSpec,
        intro_md: str,
        chapters: list[ChapterContent],
        conclusion_md: str,
    ) -> Path:
        """Assemble all markdown files into the output directory.

        Creates:
            book_dir/
                metadata.yaml    — Pandoc YAML front matter
                00-introduction.md
                01-chapter-title.md
                ...
                NN-conclusion.md
                full_book.md     — concatenated version
        """
        # Sanitize title for filesystem
        safe_title = re.sub(r"[^\w\s-]", "", spec.title).strip().replace(" ", "-").lower()
        book_dir = Path(self._cfg.output_dir) / f"{safe_title}-{spec.id[:8]}"
        book_dir.mkdir(parents=True, exist_ok=True)

        # YAML front matter for Pandoc
        metadata = f"""---
title: "{spec.title}"
subtitle: "{spec.subtitle}"
author: "{spec.author_name}"
lang: {spec.language}
date: "{datetime.now(timezone.utc).strftime('%Y-%m-%d')}"
toc: true
toc-depth: 2
geometry: margin=1in
fontsize: 11pt
documentclass: report
---
"""
        (book_dir / "metadata.yaml").write_text(metadata, encoding="utf-8")

        # Introduction
        (book_dir / "00-introduction.md").write_text(intro_md, encoding="utf-8")

        # Chapters
        for ch in chapters:
            filename = f"{ch.chapter_num:02d}-{self._slugify(ch.title)}.md"
            (book_dir / filename).write_text(ch.content_md, encoding="utf-8")

        # Conclusion
        (book_dir / f"{len(chapters)+1:02d}-conclusion.md").write_text(conclusion_md, encoding="utf-8")

        # Full concatenated version
        full_parts = [metadata, "\n\n", intro_md, "\n\n"]
        for ch in chapters:
            full_parts.append(ch.content_md)
            full_parts.append("\n\n")
        full_parts.append(conclusion_md)
        (book_dir / "full_book.md").write_text("".join(full_parts), encoding="utf-8")

        log.info("kdp.assembled", book_dir=str(book_dir), files=len(chapters) + 3)
        return book_dir

    # ── Database ────────────────────────────────────────────────

    def _persist_book(
        self,
        spec: BookSpec,
        chapters: list[ChapterContent],
        book_dir: Path,
        total_words: int,
    ) -> None:
        """Save book and chapter metadata to the database."""
        self._db.execute(
            """
            INSERT INTO kdp_books (id, topic, title, subtitle, author_name, chapter_count, word_count, language, status, book_dir)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'generated', ?)
            """,
            (spec.id, spec.topic, spec.title, spec.subtitle, spec.author_name, len(chapters), total_words, spec.language, str(book_dir)),
        )

        for ch in chapters:
            self._db.execute(
                """
                INSERT INTO kdp_chapters (id, book_id, chapter_num, title, content_md, word_count)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (ch.id, ch.book_id, ch.chapter_num, ch.title, ch.content_md, ch.word_count),
            )

        log.info("kdp.persisted", book_id=spec.id, chapters=len(chapters))

    # ── Utilities ───────────────────────────────────────────────

    @staticmethod
    def _slugify(text: str) -> str:
        """Convert text to filesystem-safe slug."""
        slug = re.sub(r"[^\w\s-]", "", text.lower())
        return re.sub(r"[-\s]+", "-", slug).strip("-")
