"""KDPPublisher — compile markdown to PDF, generate cover, upload to KDP.

Full publishing pipeline:
    1. Markdown → PDF via Pandoc (print-ready, 6×9 trim)
    2. Book cover generation via DALL-E (1600×2560, spine + back)
    3. KDP upload via Selenium (login, fill form, upload files)

Requires:
    - pandoc installed and on PATH
    - OpenAI API key for cover generation
    - Chrome/Chromium + chromedriver for KDP upload
"""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
import uuid
import base64
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
class CompileResult:
    """Result of PDF compilation."""
    pdf_path: Path
    page_count: int
    file_size_bytes: int
    compile_time_sec: float


@dataclass(frozen=True)
class CoverResult:
    """Result of cover generation."""
    cover_path: Path
    width: int
    height: int
    prompt_used: str


@dataclass(frozen=True)
class PublishResult:
    """Result of a KDP publish attempt."""
    book_id: str
    asin: str | None
    status: str  # published | pending | failed
    url: str | None
    error: str | None
    published_at: datetime


# ── Cover Prompt Template ────────────────────────────────────────

COVER_PROMPT_TEMPLATE = """Professional book cover design for a non-fiction book.

Title: "{title}"
Subtitle: "{subtitle}"
Genre: Non-fiction / {topic}

Design requirements:
- Clean, modern design with strong visual hierarchy
- Title prominently displayed at top, large and bold
- Subtitle below title, smaller font
- Central visual element relevant to the topic: {visual_element}
- Color palette: {colors}
- Professional typography, no decorative borders
- Print-ready quality at 1600x2560 pixels
- Leave space at bottom for author name: "{author}"

Style: Contemporary non-fiction bestseller (think Atomic Habits, Sapiens).
No clip art, no cheesy stock photos. Minimalist and striking."""


# ── KDPPublisher ─────────────────────────────────────────────────

class KDPPublisher:
    """Compile books and publish to Amazon KDP.

    Pipeline:
        compile()  → markdown to print-ready PDF
        cover()    → DALL-E book cover generation
        publish()  → full pipeline: compile + cover + KDP upload

    Args:
        db: Database instance (defaults to singleton).
        kdp_cfg: KDP configuration (defaults to settings).
        ai_cfg: AI configuration for cover generation.

    Usage:
        pub = KDPPublisher()
        result = await pub.publish(book_dir=Path("output/books/my-book-abc123"))
        print(result.asin)
    """

    def __init__(
        self,
        db: Database | None = None,
        kdp_cfg: KDPConfig | None = None,
        ai_cfg: AIConfig | None = None,
    ) -> None:
        self._db = db or get_db()
        self._cfg = kdp_cfg or get_settings().kdp
        self._ai_cfg = ai_cfg or get_settings().ai

    # ── Full Pipeline ───────────────────────────────────────────

    async def publish(self, book_dir: Path) -> PublishResult:
        """Run the full publish pipeline: compile → cover → upload.

        Args:
            book_dir: Directory containing the book's markdown files.

        Returns:
            PublishResult with status, ASIN, and URL.
        """
        book_dir = Path(book_dir)
        if not book_dir.exists():
            raise FileNotFoundError(f"Book directory not found: {book_dir}")

        # Load book metadata from DB
        book = self._load_book_by_dir(book_dir)
        book_id = book["id"] if book else str(uuid.uuid4())

        log.info("kdp.publish_start", book_dir=str(book_dir), book_id=book_id)

        try:
            # Step 1: Compile markdown to PDF
            compile_result = self.compile(book_dir)
            log.info("kdp.compiled", pdf=str(compile_result.pdf_path), pages=compile_result.page_count)

            # Step 2: Generate cover
            if book:
                cover_result = await self.cover(
                    title=book["title"],
                    subtitle=book.get("subtitle", ""),
                    topic=book.get("topic", ""),
                    author=book.get("author_name", "AI Author"),
                    output_dir=book_dir,
                )
            else:
                cover_result = await self.cover(
                    title=book_dir.name,
                    subtitle="",
                    topic="non-fiction",
                    author="AI Author",
                    output_dir=book_dir,
                )
            log.info("kdp.cover_generated", cover=str(cover_result.cover_path))

            # Step 3: Upload to KDP
            publish_result = await self._upload_to_kdp(
                book_id=book_id,
                book_dir=book_dir,
                pdf_path=compile_result.pdf_path,
                cover_path=cover_result.cover_path,
                metadata=book,
            )

            # Update database
            self._db.execute(
                """
                UPDATE kdp_books
                SET status = ?, asin = ?, pdf_path = ?, cover_path = ?, published_at = ?, updated_at = datetime('now')
                WHERE id = ?
                """,
                (publish_result.status, publish_result.asin, str(compile_result.pdf_path), str(cover_result.cover_path), publish_result.published_at.isoformat(), book_id),
            )

            log.info("kdp.publish_complete", asin=publish_result.asin, status=publish_result.status)
            return publish_result

        except Exception as exc:
            log.error("kdp.publish_failed", book_id=book_id, error=str(exc))
            self._db.execute(
                "UPDATE kdp_books SET status = 'failed', updated_at = datetime('now') WHERE id = ?",
                (book_id,),
            )
            return PublishResult(
                book_id=book_id,
                asin=None,
                status="failed",
                url=None,
                error=str(exc),
                published_at=datetime.now(timezone.utc),
            )

    # ── PDF Compilation ─────────────────────────────────────────

    def compile(self, book_dir: Path) -> CompileResult:
        """Compile book markdown files into a print-ready PDF.

        Uses Pandoc with LaTeX backend for professional typesetting.
        Output is 6×9 inch trim size suitable for KDP paperback.

        Args:
            book_dir: Directory with metadata.yaml and chapter .md files.

        Returns:
            CompileResult with PDF path and stats.

        Raises:
            FileNotFoundError: If book_dir or metadata.yaml missing.
            RuntimeError: If Pandoc fails.
        """
        import time

        book_dir = Path(book_dir)
        metadata_path = book_dir / "metadata.yaml"
        if not metadata_path.exists():
            raise FileNotFoundError(f"metadata.yaml not found in {book_dir}")

        start = time.monotonic()

        # Collect chapter files in order
        chapter_files = sorted(book_dir.glob("[0-9]*.md"))
        if not chapter_files:
            raise FileNotFoundError(f"No chapter files found in {book_dir}")

        pdf_path = book_dir / f"{book_dir.name}.pdf"

        # Build Pandoc command for KDP-ready PDF
        cmd = [
            self._cfg.pandoc_path,
            str(metadata_path),
            *[str(f) for f in chapter_files],
            "-o", str(pdf_path),
            "--pdf-engine=xelatex",
            "-V", "geometry:paperwidth=6in",
            "-V", "geometry:paperheight=9in",
            "-V", "geometry:margin-top=0.75in",
            "-V", "geometry:margin-bottom=0.75in",
            "-V", "geometry:margin-left=0.75in",
            "-V", "geometry:margin-right=0.75in",
            "-V", "mainfont=DejaVu Serif",
            "-V", "sansfont=DejaVu Sans",
            "-V", "monofont=DejaVu Sans Mono",
            "--toc",
            "--toc-depth=2",
            "-V", "numbersections",
            "-V", "header-includes=\\usepackage{fancyhdr}",
            "-V", "header-includes=\\pagestyle{fancy}",
            "-V", "header-includes=\\fancyhf{}",
            "-V", "header-includes=\\fancyfoot[C]{\\thepage}",
        ]

        log.info("kdp.compile_start", pdf=str(pdf_path), chapters=len(chapter_files))

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
        )

        elapsed = time.monotonic() - start

        if result.returncode != 0:
            log.error("kdp.compile_failed", stderr=result.stderr[:500])
            raise RuntimeError(f"Pandoc failed (exit {result.returncode}): {result.stderr[:500]}")

        if not pdf_path.exists():
            raise RuntimeError("Pandoc completed but PDF not created")

        # Get page count and file size
        file_size = pdf_path.stat().st_size
        page_count = self._count_pdf_pages(pdf_path)

        return CompileResult(
            pdf_path=pdf_path,
            page_count=page_count,
            file_size_bytes=file_size,
            compile_time_sec=round(elapsed, 2),
        )

    def _count_pdf_pages(self, pdf_path: Path) -> int:
        """Count pages in a PDF file."""
        try:
            # Try pdftk first (fast, reliable)
            result = subprocess.run(
                ["pdftk", str(pdf_path), "dump_data"],
                capture_output=True, text=True, timeout=30,
            )
            for line in result.stdout.split("\n"):
                if line.startswith("NumberOfPages:"):
                    return int(line.split(":")[1].strip())
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        # Fallback: count with grep on PDF structure
        try:
            content = pdf_path.read_bytes()
            # Count /Type /Page entries (excluding /Pages)
            import re
            return len(re.findall(rb"/Type\s*/Page(?!s)", content))
        except Exception:
            return 0

    # ── Cover Generation ────────────────────────────────────────

    async def cover(
        self,
        title: str,
        subtitle: str,
        topic: str,
        author: str,
        output_dir: Path,
    ) -> CoverResult:
        """Generate a book cover using DALL-E.

        Args:
            title: Book title.
            subtitle: Book subtitle.
            topic: Book topic for visual direction.
            author: Author name to display.
            output_dir: Where to save the cover image.

        Returns:
            CoverResult with file path and metadata.
        """
        import httpx

        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        # Build the prompt
        visual_element = self._topic_to_visual(topic)
        colors = self._topic_to_colors(topic)

        prompt = COVER_PROMPT_TEMPLATE.format(
            title=title,
            subtitle=subtitle,
            topic=topic,
            author=author,
            visual_element=visual_element,
            colors=colors,
        )

        log.info("kdp.cover_generating", title=title)

        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                "https://api.openai.com/v1/images/generations",
                headers={
                    "Authorization": f"Bearer {self._ai_cfg.openai_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self._ai_cfg.image_model,
                    "prompt": prompt,
                    "n": 1,
                    "size": "1024x1792",  # Closest to 1600×2560 DALL-E supports
                    "quality": "hd",
                    "response_format": "b64_json",
                },
            )
            resp.raise_for_status()
            data = resp.json()

        # Decode and save
        image_b64 = data["data"][0]["b64_json"]
        image_bytes = base64.b64decode(image_b64)
        cover_path = output_dir / "cover.png"
        cover_path.write_bytes(image_bytes)

        return CoverResult(
            cover_path=cover_path,
            width=1024,
            height=1792,
            prompt_used=prompt[:200],
        )

    @staticmethod
    def _topic_to_visual(topic: str) -> str:
        """Map a topic to a visual element description for cover generation."""
        topic_lower = topic.lower()
        visuals = {
            "ai": "abstract neural network pattern with glowing nodes and connections",
            "machine learning": "gradient descent visualization with flowing data streams",
            "programming": "elegant code structure flowing into geometric shapes",
            "business": "upward-trending graph merging with cityscape silhouette",
            "finance": "abstract currency symbols dissolving into growth patterns",
            "health": "molecular structure transforming into natural elements",
            "psychology": "human silhouette with thought patterns radiating outward",
            "productivity": "interlocking gears becoming clockwork precision",
            "marketing": "megaphone sending ripples across a digital landscape",
            "crypto": "blockchain lattice with geometric crystals",
        }
        for key, visual in visuals.items():
            if key in topic_lower:
                return visual
        return "abstract geometric pattern representing knowledge and growth"

    @staticmethod
    def _topic_to_colors(topic: str) -> str:
        """Map a topic to a color palette for cover generation."""
        topic_lower = topic.lower()
        palettes = {
            "ai": "deep blue and electric cyan with white accents",
            "tech": "dark navy and bright teal with silver",
            "business": "charcoal gray and gold with deep red accent",
            "finance": "dark green and gold on cream",
            "health": "calming teal and white with soft green",
            "psychology": "deep purple and warm amber",
            "marketing": "bold red and white with dark gray",
        }
        for key, palette in palettes.items():
            if key in topic_lower:
                return palette
        return "sophisticated dark tones with one bold accent color"

    # ── KDP Upload ──────────────────────────────────────────────

    async def _upload_to_kdp(
        self,
        book_id: str,
        book_dir: Path,
        pdf_path: Path,
        cover_path: Path,
        metadata: dict[str, Any] | None,
    ) -> PublishResult:
        """Upload the book to KDP via Selenium automation.

        This automates the KDP web interface:
            1. Login to KDP
            2. Create new paperback
            3. Fill book details (title, author, description, keywords)
            4. Upload interior PDF
            5. Upload cover image
            6. Set pricing and distribution
            7. Submit for review
        """
        log.info("kdp.upload_start", book_id=book_id)

        try:
            from selenium import webdriver
            from selenium.webdriver.chrome.options import Options
            from selenium.webdriver.common.by import By
            from selenium.webdriver.support.ui import WebDriverWait
            from selenium.webdriver.support import expected_conditions as EC

            # Configure headless Chrome
            chrome_opts = Options()
            chrome_opts.add_argument("--headless=new")
            chrome_opts.add_argument("--no-sandbox")
            chrome_opts.add_argument("--disable-dev-shm-usage")
            chrome_opts.add_argument("--window-size=1920,1080")

            driver = webdriver.Chrome(options=chrome_opts)
            wait = WebDriverWait(driver, 30)

            try:
                # Navigate to KDP
                driver.get("https://kdp.amazon.com/en_US/title-setup/paperback/new")
                wait.until(EC.presence_of_element_located((By.ID, "title")))

                # Fill book details
                title = metadata.get("title", "") if metadata else book_dir.name
                subtitle = metadata.get("subtitle", "") if metadata else ""
                author = metadata.get("author_name", "AI Author") if metadata else "AI Author"

                self._fill_field(driver, "title", title)
                if subtitle:
                    self._fill_field(driver, "subtitle", subtitle)
                self._fill_field(driver, "author", author)

                # Set language
                lang_select = driver.find_element(By.ID, "language")
                lang_select.send_keys("English")

                # Set description from first chapter or intro
                description = self._generate_description(metadata, book_dir)
                self._fill_field(driver, "description", description)

                # Set keywords (up to 7)
                keywords = self._extract_keywords(metadata)
                for i, kw in enumerate(keywords[:7]):
                    field_id = f"keyword-{i}"
                    self._fill_field(driver, field_id, kw)

                # Upload interior PDF
                interior_input = driver.find_element(By.CSS_SELECTOR, "input[type='file'][name='interior']")
                interior_input.send_keys(str(pdf_path.resolve()))

                # Upload cover
                cover_input = driver.find_element(By.CSS_SELECTOR, "input[type='file'][name='cover']")
                cover_input.send_keys(str(cover_path.resolve()))

                # Wait for uploads to complete
                wait.until(EC.invisibility_of_element_located((By.CSS_SELECTOR, ".upload-progress")))

                # Set pricing
                self._set_pricing(driver, metadata)

                # Submit
                submit_btn = driver.find_element(By.CSS_SELECTOR, "button[data-testid='submit-button']")
                submit_btn.click()

                # Wait for confirmation
                wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".success-message")))

                # Try to get ASIN
                asin = None
                try:
                    asin_elem = driver.find_element(By.CSS_SELECTOR, "[data-testid='asin']")
                    asin = asin_elem.text.strip()
                except Exception:
                    pass

                url = f"https://www.amazon.com/dp/{asin}" if asin else None

                return PublishResult(
                    book_id=book_id,
                    asin=asin,
                    status="published",
                    url=url,
                    error=None,
                    published_at=datetime.now(timezone.utc),
                )

            finally:
                driver.quit()

        except ImportError:
            log.warning("kdp.selenium_not_installed", hint="pip install selenium")
            return PublishResult(
                book_id=book_id,
                asin=None,
                status="failed",
                url=None,
                error="selenium not installed — install with: pip install selenium",
                published_at=datetime.now(timezone.utc),
            )
        except Exception as exc:
            log.error("kdp.upload_failed", book_id=book_id, error=str(exc))
            return PublishResult(
                book_id=book_id,
                asin=None,
                status="failed",
                url=None,
                error=str(exc),
                published_at=datetime.now(timezone.utc),
            )

    def _fill_field(self, driver: Any, field_id: str, value: str) -> None:
        """Fill a text field, clearing it first."""
        from selenium.webdriver.common.by import By
        elem = driver.find_element(By.ID, field_id)
        elem.clear()
        elem.send_keys(value)

    def _set_pricing(self, driver: Any, metadata: dict[str, Any] | None) -> None:
        """Set book pricing for US marketplace."""
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import Select

        # Set list price for US
        price_input = driver.find_element(By.ID, "list-price-us")
        page_count = 150  # Default estimate
        # KDP pricing: $2.99 minimum for 70% royalty
        price = max(2.99, page_count * 0.012 + 2.0)  # Rough formula
        price_input.clear()
        price_input.send_keys(f"{price:.2f}")

    def _generate_description(self, metadata: dict[str, Any] | None, book_dir: Path) -> str:
        """Generate a book description from metadata."""
        if not metadata:
            return f"A comprehensive guide exploring {book_dir.name}."

        topic = metadata.get("topic", "")
        title = metadata.get("title", "")

        # Try to use the first chapter as description source
        intro_file = book_dir / "00-introduction.md"
        if intro_file.exists():
            content = intro_file.read_text(encoding="utf-8")
            # Extract first 2 paragraphs
            paragraphs = [p.strip() for p in content.split("\n\n") if p.strip() and not p.strip().startswith("#")]
            if paragraphs:
                desc = "\n\n".join(paragraphs[:2])
                # KDP limit: 4000 chars
                return desc[:4000]

        return f"A practical guide to {topic}. {title} covers everything you need to know."

    def _extract_keywords(self, metadata: dict[str, Any] | None) -> list[str]:
        """Extract SEO keywords from book metadata."""
        if not metadata:
            return []

        topic = metadata.get("topic", "")
        title = metadata.get("title", "")

        # Basic keyword extraction from title + topic
        words = set()
        for text in [topic, title]:
            for word in re.findall(r"\b[a-z]{3,}\b", text.lower()):
                words.add(word)

        # Add common non-fiction qualifiers
        base_keywords = list(words)[:4]
        qualifiers = ["guide", "handbook", "practical", "beginners", "2026", "complete"]
        all_kw = base_keywords + qualifiers[:7 - len(base_keywords)]

        return all_kw[:7]

    # ── Database ────────────────────────────────────────────────

    def _load_book_by_dir(self, book_dir: Path) -> dict[str, Any] | None:
        """Look up a book record by its output directory."""
        return self._db.fetchone(
            "SELECT * FROM kdp_books WHERE book_dir = ?",
            (str(book_dir),),
        )

    # ── Utilities ───────────────────────────────────────────────

    def list_books(self, status: str | None = None) -> list[dict[str, Any]]:
        """List all books, optionally filtered by status."""
        if status:
            return self._db.fetchall(
                "SELECT * FROM kdp_books WHERE status = ? ORDER BY created_at DESC",
                (status,),
            )
        return self._db.fetchall("SELECT * FROM kdp_books ORDER BY created_at DESC")

    def get_book(self, book_id: str) -> dict[str, Any] | None:
        """Get a single book by ID."""
        return self._db.fetchone("SELECT * FROM kdp_books WHERE id = ?", (book_id,))
