"""KDP Factory — AI book generation and Amazon publishing pipeline.

Generates non-fiction books chapter-by-chapter via LLM, compiles to
print-ready PDF with Pandoc, creates covers with DALL-E, and automates
the KDP upload via Selenium.

Key classes:
    BookGenerator — LLM-powered chapter content generation
    KDPPublisher  — markdown → PDF → cover → KDP upload
"""

from __future__ import annotations

from src.hunts.kdp.generator import BookGenerator
from src.hunts.kdp.publisher import KDPPublisher

__all__ = ["BookGenerator", "KDPPublisher"]
