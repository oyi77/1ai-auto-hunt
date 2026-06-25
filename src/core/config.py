"""Centralised configuration via Pydantic BaseSettings.

Every integration URL/key lives here so the rest of the codebase never reads
os.environ directly.  Settings are loaded once (cached) and shared as a
frozen singleton through ``get_settings()``.

Resolution order (standard pydantic-settings):
  1. Constructor kwargs
  2. Environment variables
  3. .env file (project root)
  4. Defaults declared on the class
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All tunables for 1ai-auto-hunt and its integrations.

    Field names map 1:1 to env-var names (case-insensitive).
    Secrets are wrapped in ``SecretStr`` so they never leak into logs.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="HUNT_",
        case_sensitive=False,
        frozen=True,
    )

    # ── Database ──────────────────────────────────────────────────────────
    database_url: str = Field(
        default="sqlite+aiosqlite:///./hunt.db",
        description="SQLAlchemy connection string",
    )

    # ── Phone farm (remote Android devices) ───────────────────────────────
    phonefarm_url: str = Field(
        default="http://localhost:8889",
        description="Base URL of the phone-farm control server",
    )
    phonefarm_api_key: SecretStr = Field(
        default=SecretStr(""),
        description="API key for phone-farm server",
    )

    # ── Social API (engagement / boost) ───────────────────────────────────
    social_api_url: str = Field(
        default="http://localhost:8200",
        description="Base URL for the social-engagement micro-service",
    )

    # ── Proxy (1proxy residential rotating) ───────────────────────────────
    proxy_api_url: str = Field(
        default="http://localhost:8000",
        description="1proxy API base URL",
    )
    proxy_api_key: SecretStr = Field(
        default=SecretStr(""),
        description="1proxy API key",
    )
    proxy_default_protocol: str = Field(
        default="http",
        description="Default proxy protocol (http, socks5)",
    )

    # ── Stalwart mail server ──────────────────────────────────────────────
    stalwart_host: str = Field(
        default="localhost",
        description="Stalwart SMTP/IMAP host",
    )
    stalwart_port: int = Field(
        default=587,
        description="Stalwart SMTP port",
    )
    stalwart_user: str = Field(default="", description="SMTP username")
    stalwart_password: SecretStr = Field(
        default=SecretStr(""),
        description="SMTP password",
    )

    # ── Captcha solving (2captcha) ────────────────────────────────────────
    captcha_api_key: SecretStr = Field(
        default=SecretStr(""),
        description="2captcha API key",
    )
    captcha_timeout: int = Field(
        default=180,
        ge=30,
        le=600,
        description="Max seconds to wait for a captcha solution",
    )

    # ── SMS verification (sms-activate.org) ───────────────────────────────
    sms_activate_key: SecretStr = Field(
        default=SecretStr(""),
        description="sms-activate.org API key",
    )
    sms_default_country: int = Field(
        default=0,
        description="Default country code for sms-activate (0 = Russia)",
    )

    # ── Redis (task queue / rate limiting) ────────────────────────────────
    redis_url: str = Field(
        default="redis://localhost:6379/0",
        description="Redis connection URL",
    )

    # ── WAHA (WhatsApp HTTP API) ───────────────────────────────────────
    waha_url: str = Field(
        default="http://localhost:3010",
        description="WAHA WhatsApp API base URL",
    )
    waha_session: str = Field(
        default="default",
        description="Default WAHA session name",
    )

    # ── Affiliate (ClickServer) ────────────────────────────────────────
    affiliate_url: str = Field(
        default="http://localhost:3001",
        description="1ai-affiliate ClickServer base URL",
    )

    # ── Application ──────────────────────────────────────────────────────
    log_level: str = Field(
        default="INFO",
        description="Root log level (DEBUG, INFO, WARNING, ERROR)",
    )
    log_format: str = Field(
        default="json",
        description="Log format: 'json' or 'console'",
    )
    environment: str = Field(
        default="development",
        description="Runtime environment (development, staging, production)",
    )
    max_concurrent_tasks: int = Field(
        default=10,
        ge=1,
        le=100,
        description="Global concurrency cap for hunt workers",
    )
    request_timeout: float = Field(
        default=30.0,
        ge=1.0,
        description="Default HTTP client timeout in seconds",
    )

    # ── Derived helpers ──────────────────────────────────────────────────

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    # ── Sub-config accessors (lazy, no env vars needed) ───────────
    @property
    def stream(self) -> "StreamConfig":
        return StreamConfig()

    @property
    def ai(self) -> "AIConfig":
        return AIConfig(
            openai_api_key="",
            anthropic_api_key="",
        )

    @property
    def kdp(self) -> "KDPConfig":
        return KDPConfig()

    @property
    def media(self) -> "MediaConfig":
        return MediaConfig()

    # ── Sub-config accessors (lazy, no env vars needed) ───────────
    @property
    def stream(self) -> "StreamConfig":
        return StreamConfig()

    @property
    def ai(self) -> "AIConfig":
        return AIConfig(
            openai_api_key="",
            anthropic_api_key="",
        )

    @property
    def kdp(self) -> "KDPConfig":
        return KDPConfig()

    @property
    def media(self) -> "MediaConfig":
        return MediaConfig()

    # ── Sub-config accessors (lazy, no env vars needed) ───────────
    @property
    def stream(self) -> "StreamConfig":
        return StreamConfig()

    @property
    def ai(self) -> "AIConfig":
        return AIConfig(
            openai_api_key="",
            anthropic_api_key="",
        )

    @property
    def kdp(self) -> "KDPConfig":
        return KDPConfig()

    @property
    def media(self) -> "MediaConfig":
        return MediaConfig()

    @property
    def proxy_api_key_plain(self) -> str:
        """Unwrapped secret for clients that need a bare string."""
        return self.proxy_api_key.get_secret_value()

    @property
    def captcha_api_key_plain(self) -> str:
        return self.captcha_api_key.get_secret_value()

    @property
    def sms_activate_key_plain(self) -> str:
        return self.sms_activate_key.get_secret_value()

    @property
    def phonefarm_api_key_plain(self) -> str:
        return self.phonefarm_api_key.get_secret_value()

    @property
    def stalwart_password_plain(self) -> str:
        return self.stalwart_password.get_secret_value()

    @model_validator(mode="after")
    def _warn_missing_secrets(self) -> "Settings":
        """Emit a warning (not an error) when secrets are empty.

        Lets the app start in dev mode without all integrations configured.
        """
        import warnings

        secret_fields = {
            "HUNT_PHONEFARM_API_KEY": self.phonefarm_api_key,
            "HUNT_PROXY_API_KEY": self.proxy_api_key,
            "HUNT_CAPTCHA_API_KEY": self.captcha_api_key,
            "HUNT_SMS_ACTIVATE_KEY": self.sms_activate_key,
            "HUNT_STALWART_PASSWORD": self.stalwart_password,
        }
        empty = [name for name, val in secret_fields.items() if not val.get_secret_value()]
        if empty and self.is_production:
            raise ValueError(
                f"Production requires all secrets set. Missing: {', '.join(empty)}"
            )
        if empty:
            warnings.warn(
                f"Secrets not configured (dev mode OK): {', '.join(empty)}",
                stacklevel=2,
            )
        return self




# ── Hunt-specific sub-configs ─────────────────────────────────────────
from dataclasses import dataclass


@dataclass
class StreamConfig:
    """Configuration for streaming farm operations."""
    max_streams_per_account: int = 200
    min_track_duration_seconds: int = 31
    jitter_min_seconds: float = 2.0
    jitter_max_seconds: float = 8.0
    daily_reset_hour_utc: int = 0


@dataclass
class AIConfig:
    """Configuration for LLM-based content generation."""
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    default_model: str = "gpt-4o-mini"
    max_tokens_per_chapter: int = 2000
    temperature: float = 0.7


@dataclass
class KDPConfig:
    """Configuration for KDP book publishing."""
    kdp_email: str = ""
    kdp_password: str = ""
    default_chapter_count: int = 10
    cover_width: int = 1600
    cover_height: int = 2560
    trim_size: str = "6x9"


@dataclass
class MediaConfig:
    """Configuration for voice cloning and AI influencer."""
    rvc_model_path: str = "./models/rvc"
    output_dir: str = "./media_output"
    default_style: str = "aesthetic"
    sd_model: str = "stabilityai/stable-diffusion-xl-base-1.0"
    lora_rank: int = 8




# ── Hunt-specific sub-configs ─────────────────────────────────────────
from dataclasses import dataclass


@dataclass
class StreamConfig:
    """Configuration for streaming farm operations."""
    max_streams_per_account: int = 200
    min_track_duration_seconds: int = 31
    jitter_min_seconds: float = 2.0
    jitter_max_seconds: float = 8.0
    daily_reset_hour_utc: int = 0


@dataclass
class AIConfig:
    """Configuration for LLM-based content generation."""
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    default_model: str = "gpt-4o-mini"
    max_tokens_per_chapter: int = 2000
    temperature: float = 0.7


@dataclass
class KDPConfig:
    """Configuration for KDP book publishing."""
    kdp_email: str = ""
    kdp_password: str = ""
    default_chapter_count: int = 10
    cover_width: int = 1600
    cover_height: int = 2560
    trim_size: str = "6x9"


@dataclass
class MediaConfig:
    """Configuration for voice cloning and AI influencer."""
    rvc_model_path: str = "./models/rvc"
    output_dir: str = "./media_output"
    default_style: str = "aesthetic"
    sd_model: str = "stabilityai/stable-diffusion-xl-base-1.0"
    lora_rank: int = 8




# ── Hunt-specific sub-configs ─────────────────────────────────────────
from dataclasses import dataclass


@dataclass
class StreamConfig:
    """Configuration for streaming farm operations."""
    max_streams_per_account: int = 200
    min_track_duration_seconds: int = 31
    jitter_min_seconds: float = 2.0
    jitter_max_seconds: float = 8.0
    daily_reset_hour_utc: int = 0


@dataclass
class AIConfig:
    """Configuration for LLM-based content generation."""
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    default_model: str = "gpt-4o-mini"
    max_tokens_per_chapter: int = 2000
    temperature: float = 0.7


@dataclass
class KDPConfig:
    """Configuration for KDP book publishing."""
    kdp_email: str = ""
    kdp_password: str = ""
    default_chapter_count: int = 10
    cover_width: int = 1600
    cover_height: int = 2560
    trim_size: str = "6x9"


@dataclass
class MediaConfig:
    """Configuration for voice cloning and AI influencer."""
    rvc_model_path: str = "./models/rvc"
    output_dir: str = "./media_output"
    default_style: str = "aesthetic"
    sd_model: str = "stabilityai/stable-diffusion-xl-base-1.0"
    lora_rank: int = 8


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the frozen singleton ``Settings`` instance.

    Cached so repeated calls never re-read .env or re-validate.
    """
    return Settings()
