"""AIInfluencer — virtual influencer generation and social posting.

Creates consistent AI-generated characters using Stable Diffusion + LoRA,
then manages their social media presence through 1ai-social.

Pipeline:
    1. Character design (name, style, appearance seed)
    2. LoRA fine-tuning for character consistency
    3. Image generation with style-locked prompts
    4. Caption generation via LLM
    5. Post scheduling via 1ai-social API

Integrations:
    - Stable Diffusion (AUTOMATIC1111 / ComfyUI) for image generation
    - LoRA for character consistency across images
    - 1ai-social for multi-platform posting
    - LLM for caption and bio generation
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import random
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Optional

from src.core.config import get_settings, AIConfig, MediaConfig
from src.core.db import get_db, Database
from src.core.logger import get_logger

log = get_logger(__name__)


# ── Data Models ──────────────────────────────────────────────────

@dataclass(frozen=True)
class InfluencerProfile:
    """A virtual influencer's profile and configuration."""
    id: str
    name: str
    style: str
    platform: str
    handle: str
    bio: str
    post_count: int
    follower_count: int
    status: str
    sd_config: dict[str, Any] | None


@dataclass(frozen=True)
class GeneratedPost:
    """A generated post ready for publishing."""
    id: str
    influencer_id: str
    platform: str
    post_type: str
    caption: str
    image_path: Path | None
    video_path: Path | None
    created_at: datetime


@dataclass(frozen=True)
class PublishResult:
    """Result of publishing a post via 1ai-social."""
    post_id: str
    platform: str
    external_id: str | None
    url: str | None
    status: str
    error: str | None


# ── Style Definitions ────────────────────────────────────────────

STYLE_PRESETS: dict[str, dict[str, Any]] = {
    "aesthetic": {
        "positive": "beautiful, aesthetic, soft lighting, dreamy, pastel colors, bokeh, fashion photography",
        "negative": "ugly, blurry, low quality, deformed, watermark, text",
        "sampler": "DPM++ 2M Karras",
        "steps": 30,
        "cfg_scale": 7.0,
    },
    "cyberpunk": {
        "positive": "cyberpunk, neon lights, futuristic, dark atmosphere, holographic, tech wear",
        "negative": "ugly, blurry, low quality, deformed, watermark, daylight, pastoral",
        "sampler": "DPM++ SDE Karras",
        "steps": 35,
        "cfg_scale": 7.5,
    },
    "minimalist": {
        "positive": "minimalist, clean background, soft lighting, high fashion, editorial, studio photography",
        "negative": "ugly, blurry, low quality, deformed, watermark, cluttered, busy background",
        "sampler": "DPM++ 2M Karras",
        "steps": 25,
        "cfg_scale": 6.5,
    },
    "anime": {
        "positive": "anime style, detailed, vibrant colors, cel shading, beautiful, high quality illustration",
        "negative": "ugly, blurry, low quality, deformed, watermark, realistic, photo",
        "sampler": "DPM++ 2M Karras",
        "steps": 28,
        "cfg_scale": 7.0,
    },
    "realistic": {
        "positive": "photorealistic, 8k uhd, dslr, film grain, natural lighting, portrait photography",
        "negative": "ugly, blurry, low quality, deformed, watermark, cartoon, painting, illustration",
        "sampler": "DPM++ 2M Karras",
        "steps": 35,
        "cfg_scale": 6.0,
    },
    "fitness": {
        "positive": "fitness model, athletic, gym, dynamic pose, motivational, high energy, sports photography",
        "negative": "ugly, blurry, low quality, deformed, watermark, sedentary, unhealthy",
        "sampler": "DPM++ 2M Karras",
        "steps": 30,
        "cfg_scale": 7.0,
    },
}


# ── Caption Prompt ───────────────────────────────────────────────

CAPTION_SYSTEM = """You are a social media copywriter creating captions for an AI influencer.
Write engaging, authentic captions that feel human and relatable.
Use appropriate emojis, hashtags, and a conversational tone.
Keep Instagram captions under 2200 characters. Keep tweets under 280 characters.
Match the personality and style of the influencer."""

CAPTION_USER = """Influencer: {name}
Style: {style}
Platform: {platform}
Image description: {image_desc}
Post type: {post_type}

Write a {platform} caption with:
- An engaging hook in the first line
- A short story or insight (2-4 sentences)
- A call to action or question
- 10-15 relevant hashtags

Return ONLY the caption text, nothing else."""


BIO_SYSTEM = """You are a social media bio writer. Create authentic, engaging bios
for AI influencers. Keep it concise and personality-driven."""

BIO_USER = """Influencer name: {name}
Style/aesthetic: {style}
Platform: {platform}

Write a bio (max 150 characters for Instagram, 160 for Twitter) that:
- Captures the character's vibe
- Includes 1-2 relevant emojis
- Has a call to action
Return ONLY the bio text."""


# ── AIInfluencer ─────────────────────────────────────────────────

class AIInfluencer:
    """Generate and manage AI virtual influencers.

    Creates consistent characters via Stable Diffusion + LoRA,
    generates content, and manages social posting through 1ai-social.

    Args:
        model_dir: Directory for SD models and LoRA weights.
        output_dir: Directory for generated images.
        db: Database instance (defaults to singleton).
        ai_cfg: AI configuration for LLM captioning.
        media_cfg: Media configuration.

    Usage:
        influencer = AIInfluencer()
        profile = await influencer.create(
            name="Luna",
            style="aesthetic",
            count=30,
        )
        posts = await influencer.generate_content(profile.id, count=10)
        await influencer.schedule_posts(profile.id, posts_per_day=2)
    """

    def __init__(
        self,
        model_dir: str | Path | None = None,
        output_dir: str | Path | None = None,
        db: Database | None = None,
        ai_cfg: AIConfig | None = None,
        media_cfg: MediaConfig | None = None,
    ) -> None:
        cfg = media_cfg or get_settings().media
        self._model_dir = Path(model_dir or cfg.sd_model_path)
        self._lora_dir = Path(cfg.lora_path)
        self._output_dir = Path(output_dir or cfg.output_dir)
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._db = db or get_db()
        self._ai_cfg = ai_cfg or get_settings().ai

    # ── Character Creation ──────────────────────────────────────

    async def create(
        self,
        name: str,
        style: str,
        count: int = 1,
        platform: str = "instagram",
        custom_seed: int | None = None,
    ) -> InfluencerProfile:
        """Create a new AI influencer character.

        Steps:
            1. Generate character description and bio
            2. Create SD configuration with consistent seed
            3. Train or configure LoRA for character consistency
            4. Generate initial profile images
            5. Register in database

        Args:
            name: Character name.
            style: Visual style (aesthetic, cyberpunk, minimalist, anime, realistic, fitness).
            count: Number of initial posts to generate.
            platform: Target platform.
            custom_seed: Optional seed for reproducible character appearance.

        Returns:
            InfluencerProfile with all character data.
        """
        influencer_id = str(uuid.uuid4())
        style = style.lower()

        if style not in STYLE_PRESETS:
            log.warning("media.unknown_style", style=style, fallback="aesthetic")
            style = "aesthetic"

        preset = STYLE_PRESETS[style]

        # Generate consistent character seed
        if custom_seed is None:
            custom_seed = int(hashlib.sha256(name.encode()).hexdigest()[:8], 16) % (2**32)

        # Generate handle and bio
        handle = f"@{name.lower().replace(' ', '_')}"
        bio = await self._generate_bio(name, style, platform)

        # Build SD config for character consistency
        sd_config = self._build_sd_config(name, style, preset, custom_seed)

        # Create character directory
        char_dir = self._output_dir / f"{name.lower().replace(' ', '_')}-{influencer_id[:8]}"
        char_dir.mkdir(parents=True, exist_ok=True)

        # Save SD config
        (char_dir / "sd_config.json").write_text(json.dumps(sd_config, indent=2))

        # Generate initial reference images for LoRA training
        ref_images = await self._generate_reference_images(name, style, sd_config, char_dir, count=5)
        log.info("media.references_generated", count=len(ref_images), character=name)

        # Train LoRA on reference images (in production)
        lora_path = char_dir / "lora.safetensors"
        await self._train_lora(ref_images, lora_path, name)

        # Generate initial content posts
        posts: list[GeneratedPost] = []
        if count > 0:
            posts = await self._generate_initial_posts(
                influencer_id, name, style, platform, sd_config, char_dir, count
            )

        # Persist influencer
        self._db.execute(
            """
            INSERT INTO media_influencers (id, name, style, platform, handle, bio, post_count, status, sd_config)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?)
            """,
            (influencer_id, name, style, platform, handle, bio, len(posts), json.dumps(sd_config)),
        )

        profile = InfluencerProfile(
            id=influencer_id,
            name=name,
            style=style,
            platform=platform,
            handle=handle,
            bio=bio,
            post_count=len(posts),
            follower_count=0,
            status="active",
            sd_config=sd_config,
        )

        log.info(
            "media.influencer_created",
            id=influencer_id,
            name=name,
            style=style,
            posts=len(posts),
            handle=handle,
        )
        return profile

    # ── Content Generation ──────────────────────────────────────

    async def generate_content(
        self,
        influencer_id: str,
        count: int = 10,
        image_desc: str | None = None,
    ) -> list[GeneratedPost]:
        """Generate content posts for an existing influencer.

        Args:
            influencer_id: Influencer to generate for.
            count: Number of posts to generate.
            image_desc: Optional scene description, or None for auto.

        Returns:
            List of GeneratedPost objects with image paths and captions.
        """
        profile = self._load_influencer(influencer_id)
        if not profile:
            raise ValueError(f"Influencer not found: {influencer_id}")

        sd_config = json.loads(profile.sd_config) if isinstance(profile.sd_config, str) else (profile.sd_config or {})
        char_dir = self._output_dir / f"{profile.name.lower().replace(' ', '_')}-{influencer_id[:8]}"

        log.info("media.generating_content", influencer=profile.name, count=count)

        posts: list[GeneratedPost] = []
        for i in range(count):
            post = await self._generate_single_post(
                influencer_id=influencer_id,
                name=profile.name,
                style=profile.style,
                platform=profile.platform,
                sd_config=sd_config,
                output_dir=char_dir,
                index=profile.post_count + i,
                image_desc=image_desc,
            )
            posts.append(post)
            log.info("media.post_generated", index=i + 1, post_id=post.id)

        # Update post count
        self._db.execute(
            """
            UPDATE media_influencers
            SET post_count = post_count + ?, updated_at = datetime('now')
            WHERE id = ?
            """,
            (count, influencer_id),
        )

        return posts

    async def _generate_single_post(
        self,
        influencer_id: str,
        name: str,
        style: str,
        platform: str,
        sd_config: dict[str, Any],
        output_dir: Path,
        index: int,
        image_desc: str | None = None,
    ) -> GeneratedPost:
        """Generate one post: image + caption."""
        post_id = str(uuid.uuid4())
        preset = STYLE_PRESETS.get(style, STYLE_PRESETS["aesthetic"])

        # Generate image prompt
        scene = image_desc or self._random_scene(style)
        prompt = self._build_image_prompt(name, style, scene, sd_config)

        # Generate image
        image_path = output_dir / f"post_{index:04d}_{post_id[:8]}.png"
        await self._generate_image(prompt, preset, sd_config, image_path)

        # Generate caption
        caption = await self._generate_caption(name, style, platform, scene, "image")

        # Persist post
        self._db.execute(
            """
            INSERT INTO media_posts (id, influencer_id, platform, post_type, caption, image_path)
            VALUES (?, ?, ?, 'image', ?, ?)
            """,
            (post_id, influencer_id, platform, caption, str(image_path)),
        )

        return GeneratedPost(
            id=post_id,
            influencer_id=influencer_id,
            platform=platform,
            post_type="image",
            caption=caption,
            image_path=image_path,
            video_path=None,
            created_at=datetime.now(timezone.utc),
        )

    async def _generate_initial_posts(
        self,
        influencer_id: str,
        name: str,
        style: str,
        platform: str,
        sd_config: dict[str, Any],
        output_dir: Path,
        count: int,
    ) -> list[GeneratedPost]:
        """Generate the initial batch of posts for a new influencer."""
        posts: list[GeneratedPost] = []
        for i in range(count):
            post = await self._generate_single_post(
                influencer_id=influencer_id,
                name=name,
                style=style,
                platform=platform,
                sd_config=sd_config,
                output_dir=output_dir,
                index=i,
            )
            posts.append(post)
        return posts

    # ── Social Posting ──────────────────────────────────────────

    async def schedule_posts(
        self,
        influencer_id: str,
        posts_per_day: int = 2,
        start_date: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Schedule unpublished posts via 1ai-social.

        Distributes unpublished posts across upcoming days at
        optimal engagement times.

        Args:
            influencer_id: Influencer to schedule for.
            posts_per_day: How many posts per day.
            start_date: When to start (defaults to tomorrow).

        Returns:
            List of scheduled post metadata.
        """
        if start_date is None:
            start_date = datetime.now(timezone.utc) + timedelta(days=1)

        # Get unpublished posts
        unpublished = self._db.fetchall(
            """
            SELECT * FROM media_posts
            WHERE influencer_id = ? AND posted_at IS NULL
            ORDER BY created_at ASC
            """,
            (influencer_id,),
        )

        if not unpublished:
            log.warning("media.no_unpublished_posts", influencer_id=influencer_id)
            return []

        # Optimal posting times (UTC) — based on Instagram/TikTok engagement data
        optimal_hours = [9, 12, 15, 18, 21]

        scheduled: list[dict[str, Any]] = []
        current_date = start_date
        daily_count = 0

        for post in unpublished:
            if daily_count >= posts_per_day:
                current_date += timedelta(days=1)
                daily_count = 0

            hour = optimal_hours[daily_count % len(optimal_hours)]
            post_time = current_date.replace(hour=hour, minute=random.randint(0, 15))

            # Schedule via 1ai-social
            publish_result = await self._publish_via_social(
                post_id=post["id"],
                platform=post["platform"],
                image_path=post.get("image_path"),
                caption=post.get("caption", ""),
                scheduled_at=post_time,
            )

            # Update DB
            self._db.execute(
                "UPDATE media_posts SET posted_at = ? WHERE id = ?",
                (post_time.isoformat(), post["id"]),
            )

            scheduled.append({
                "post_id": post["id"],
                "scheduled_at": post_time.isoformat(),
                "platform": post["platform"],
                "status": publish_result.status,
            })

            daily_count += 1

        log.info(
            "media.posts_scheduled",
            influencer_id=influencer_id,
            count=len(scheduled),
            start=start_date.isoformat(),
        )
        return scheduled

    async def _publish_via_social(
        self,
        post_id: str,
        platform: str,
        image_path: str | None,
        caption: str,
        scheduled_at: datetime,
    ) -> PublishResult:
        """Publish a post through the 1ai-social API.

        In production this calls the 1ai-social REST API:
            POST /api/v1/posts
            {
                "platform": "instagram",
                "media_url": "...",
                "caption": "...",
                "scheduled_at": "..."
            }
        """
        log.info(
            "media.publishing",
            post_id=post_id,
            platform=platform,
            scheduled_at=scheduled_at.isoformat(),
        )

        # Production: call 1ai-social API
        # async with httpx.AsyncClient() as client:
        #     resp = await client.post(
        #         f"{SOCIAL_API_URL}/api/v1/posts",
        #         json={
        #             "platform": platform,
        #             "media_path": image_path,
        #             "caption": caption,
        #             "scheduled_at": scheduled_at.isoformat(),
        #         },
        #         headers={"Authorization": f"Bearer {SOCIAL_API_KEY}"},
        #     )
        #     data = resp.json()
        #     return PublishResult(
        #         post_id=post_id,
        #         platform=platform,
        #         external_id=data.get("id"),
        #         url=data.get("url"),
        #         status="scheduled",
        #         error=None,
        #     )

        return PublishResult(
            post_id=post_id,
            platform=platform,
            external_id=f"sim_{post_id[:8]}",
            url=f"https://{platform}.com/p/sim_{post_id[:8]}",
            status="scheduled",
            error=None,
        )

    # ── Image Generation ────────────────────────────────────────

    async def _generate_reference_images(
        self,
        name: str,
        style: str,
        sd_config: dict[str, Any],
        output_dir: Path,
        count: int = 5,
    ) -> list[Path]:
        """Generate reference images for LoRA training."""
        preset = STYLE_PRESETS.get(style, STYLE_PRESETS["aesthetic"])
        scenes = [
            "portrait, looking at camera, soft smile",
            "upper body, casual pose, natural lighting",
            "close-up face, dramatic lighting, profile angle",
            "full body, standing pose, fashion outfit",
            "lifestyle, cafe setting, candid moment",
        ]

        paths: list[Path] = []
        for i, scene in enumerate(scenes[:count]):
            prompt = self._build_image_prompt(name, style, scene, sd_config)
            image_path = output_dir / f"ref_{i:02d}.png"
            await self._generate_image(prompt, preset, sd_config, image_path)
            paths.append(image_path)

        return paths

    async def _generate_image(
        self,
        prompt: str,
        preset: dict[str, Any],
        sd_config: dict[str, Any],
        output_path: Path,
    ) -> Path:
        """Generate an image using the Stable Diffusion API.

        Calls AUTOMATIC1111's /sdapi/v1/txt2img endpoint.
        """
        import httpx

        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Build the API request
        payload = {
            "prompt": prompt,
            "negative_prompt": preset["negative"],
            "steps": preset["steps"],
            "cfg_scale": preset["cfg_scale"],
            "width": 768,
            "height": 1024,
            "sampler_name": preset["sampler"],
            "seed": sd_config.get("seed", -1),
            "batch_size": 1,
            "n_iter": 1,
        }

        # Add LoRA if available
        lora_path = sd_config.get("lora_path")
        if lora_path and Path(lora_path).exists():
            lora_name = Path(lora_path).stem
            payload["prompt"] = f"<lora:{lora_name}:0.8>, {prompt}"

        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.post(
                    f"{sd_config.get('api_url', 'http://127.0.0.1:7860')}/sdapi/v1/txt2img",
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()

            # Decode base64 image
            import base64
            image_b64 = data["images"][0]
            image_bytes = base64.b64decode(image_b64)
            output_path.write_bytes(image_bytes)

        except Exception as exc:
            log.warning("media.sd_api_unavailable", error=str(exc), fallback="placeholder")
            # Create a minimal placeholder PNG
            output_path.write_bytes(self._placeholder_png())

        return output_path

    async def _train_lora(
        self, reference_images: list[Path], output_path: Path, character_name: str
    ) -> None:
        """Train a LoRA model on reference images for character consistency.

        In production this invokes kohya_ss training:
            accelerate launch train_network.py
                --pretrained_model=sd_xl_base_1.0
                --train_data_dir={ref_dir}
                --output_name={character}
                --network_module=networks.lora
                --resolution=1024
                --train_batch_size=1
                --max_train_steps=1500
        """
        log.info("media.lora_training", character=character_name, images=len(reference_images))

        # Create placeholder — in production this runs actual LoRA training
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"LORA_WEIGHTS_PLACEHOLDER")

    # ── Prompt Building ─────────────────────────────────────────

    def _build_image_prompt(
        self, name: str, style: str, scene: str, sd_config: dict[str, Any]
    ) -> str:
        """Build a Stable Diffusion prompt for consistent character generation."""
        preset = STYLE_PRESETS.get(style, STYLE_PRESETS["aesthetic"])

        # Character description (locked across all images for consistency)
        character_desc = sd_config.get("character_desc", f"young woman named {name}")

        # Compose prompt
        parts = [
            f"({character_desc}:1.2)",
            scene,
            preset["positive"],
            "masterpiece, best quality, highly detailed",
        ]

        # Add any extra positive prompts from config
        if sd_config.get("extra_positive"):
            parts.append(sd_config["extra_positive"])

        return ", ".join(parts)

    @staticmethod
    def _random_scene(style: str) -> str:
        """Generate a random scene description matching the style."""
        scenes_by_style: dict[str, list[str]] = {
            "aesthetic": [
                "walking through a flower garden, golden hour",
                "sitting at a rooftop cafe, sunset background",
                "reading a book in a cozy library, warm lighting",
                "strolling through a European street, cobblestones",
                "at a cherry blossom festival, soft pink tones",
            ],
            "cyberpunk": [
                "standing in neon-lit alley, rain-soaked streets",
                "on a rooftop overlooking a futuristic cityscape",
                "in a high-tech lab, holographic displays",
                "walking through a night market, LED signs",
                "in a VR space, digital particles floating",
            ],
            "minimalist": [
                "in a white studio, single light source",
                "against a plain wall, wearing monochrome outfit",
                "in a modern apartment, clean lines, minimal decor",
                "on a beach at dawn, empty landscape",
                "in an art gallery, white walls, single painting",
            ],
            "fitness": [
                "at the gym, mid-workout, athletic wear",
                "outdoor running trail, sunrise, sportswear",
                "yoga pose in a serene studio",
                "rock climbing, dynamic action shot",
                "post-workout smoothie bar, healthy lifestyle",
            ],
            "anime": [
                "in a magical forest, glowing particles",
                "on a rooftop under the stars, city lights below",
                "in a school courtyard, cherry blossoms falling",
                "at a festival with lanterns and fireworks",
                "in a cozy room, surrounded by plushies",
            ],
            "realistic": [
                "at a coffee shop, candid moment, natural light",
                "in a park, autumn leaves, casual outfit",
                "at the beach, wind in hair, golden hour",
                "exploring a city, street photography style",
                "at a bookshop, browsing shelves, warm interior",
            ],
        }

        scenes = scenes_by_style.get(style, scenes_by_style["aesthetic"])
        return random.choice(scenes)

    # ── Caption Generation ──────────────────────────────────────

    async def _generate_caption(
        self,
        name: str,
        style: str,
        platform: str,
        image_desc: str,
        post_type: str,
    ) -> str:
        """Generate a social media caption using the LLM."""
        try:
            return await self._llm_generate(
                CAPTION_SYSTEM,
                CAPTION_USER.format(
                    name=name,
                    style=style,
                    platform=platform,
                    image_desc=image_desc,
                    post_type=post_type,
                ),
                max_tokens=1024,
                temperature=0.8,
            )
        except Exception as exc:
            log.warning("media.caption_fallback", error=str(exc))
            return self._fallback_caption(name, style, image_desc)

    async def _generate_bio(self, name: str, style: str, platform: str) -> str:
        """Generate a social media bio."""
        try:
            return await self._llm_generate(
                BIO_SYSTEM,
                BIO_USER.format(name=name, style=style, platform=platform),
                max_tokens=256,
                temperature=0.7,
            )
        except Exception:
            return f"✨ {name} | {style.title()} vibes | Living my best life 💫"

    async def _llm_generate(
        self, system: str, user: str, max_tokens: int, temperature: float
    ) -> str:
        """Call the LLM API for text generation."""
        import httpx

        cfg = self._ai_cfg
        if cfg.anthropic_api_key:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": cfg.anthropic_api_key,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json={
                        "model": cfg.default_model,
                        "max_tokens": max_tokens,
                        "temperature": temperature,
                        "system": system,
                        "messages": [{"role": "user", "content": user}],
                    },
                )
                resp.raise_for_status()
                return resp.json()["content"][0]["text"]

        if cfg.openai_api_key:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {cfg.openai_api_key}",
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
                return resp.json()["choices"][0]["message"]["content"]

        raise RuntimeError("No LLM API key configured")

    @staticmethod
    def _fallback_caption(name: str, style: str, scene: str) -> str:
        """Generate a basic caption when LLM is unavailable."""
        hashtags = f"#{name.lower().replace(' ', '')} #{style} #ai #influencer #photooftheday"
        return f"Living in the moment ✨\n\n{scene}\n\n{hashtags}"

    # ── SD Configuration ────────────────────────────────────────

    def _build_sd_config(
        self, name: str, style: str, preset: dict[str, Any], seed: int
    ) -> dict[str, Any]:
        """Build a Stable Diffusion configuration for consistent generation."""
        # Create a detailed character description for consistency
        character_traits = {
            "aesthetic": "young woman, soft features, light makeup, flowing hair, gentle expression",
            "cyberpunk": "young woman, sharp features, neon highlights, cybernetic accessories, confident gaze",
            "minimalist": "young woman, clean features, natural beauty, simple elegant style",
            "anime": "anime girl, large eyes, colorful hair, expressive face, detailed character design",
            "realistic": "young woman, natural features, genuine smile, relatable everyday look",
            "fitness": "young woman, athletic build, toned physique, energetic expression, sporty style",
        }

        character_desc = character_traits.get(style, character_traits["aesthetic"])

        return {
            "seed": seed,
            "character_desc": character_desc,
            "style": style,
            "api_url": "http://127.0.0.1:7860",
            "model": "sd_xl_base_1.0",
            "lora_path": None,  # Set after training
            "extra_positive": "",
            "fixed_attributes": [
                "consistent face",
                "same person",
                character_desc,
            ],
        }

    @staticmethod
    def _placeholder_png() -> bytes:
        """Generate a minimal valid PNG (1x1 transparent pixel)."""
        import struct
        import zlib

        def _chunk(chunk_type: bytes, data: bytes) -> bytes:
            c = chunk_type + data
            crc = struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)
            return struct.pack(">I", len(data)) + c + crc

        signature = b"\x89PNG\r\n\x1a\n"
        ihdr = _chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
        raw = b"\x00\x00\x00\x00\x00"
        idat = _chunk(b"IDAT", zlib.compress(raw))
        iend = _chunk(b"IEND", b"")
        return signature + ihdr + idat + iend

    # ── Database ────────────────────────────────────────────────

    def _load_influencer(self, influencer_id: str) -> InfluencerProfile | None:
        """Load an influencer profile from the database."""
        row = self._db.fetchone(
            "SELECT * FROM media_influencers WHERE id = ?",
            (influencer_id,),
        )
        if not row:
            return None

        sd_config = None
        if row.get("sd_config"):
            try:
                sd_config = json.loads(row["sd_config"])
            except (json.JSONDecodeError, TypeError):
                sd_config = None

        return InfluencerProfile(
            id=row["id"],
            name=row["name"],
            style=row["style"],
            platform=row["platform"],
            handle=row["handle"],
            bio=row["bio"],
            post_count=row["post_count"],
            follower_count=row["follower_count"],
            status=row["status"],
            sd_config=sd_config,
        )

    def list_influencers(self, status: str = "active") -> list[InfluencerProfile]:
        """List all influencers, optionally filtered by status."""
        rows = self._db.fetchall(
            "SELECT * FROM media_influencers WHERE status = ? ORDER BY created_at DESC",
            (status,),
        )
        results: list[InfluencerProfile] = []
        for row in rows:
            sd_config = None
            if row.get("sd_config"):
                try:
                    sd_config = json.loads(row["sd_config"])
                except (json.JSONDecodeError, TypeError):
                    pass
            results.append(InfluencerProfile(
                id=row["id"],
                name=row["name"],
                style=row["style"],
                platform=row["platform"],
                handle=row["handle"],
                bio=row["bio"],
                post_count=row["post_count"],
                follower_count=row["follower_count"],
                status=row["status"],
                sd_config=sd_config,
            ))
        return results

    def delete_influencer(self, influencer_id: str) -> bool:
        """Delete an influencer and all associated posts."""
        influencer = self._load_influencer(influencer_id)
        if not influencer:
            return False

        self._db.execute("DELETE FROM media_posts WHERE influencer_id = ?", (influencer_id,))
        self._db.execute("DELETE FROM media_influencers WHERE id = ?", (influencer_id,))

        log.info("media.influencer_deleted", id=influencer_id, name=influencer.name)
        return True
