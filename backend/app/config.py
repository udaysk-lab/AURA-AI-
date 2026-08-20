"""Application configuration.

Every value has a safe default so the app boots with zero configuration.
Adding real credentials upgrades AURA from demo mode to live mode.
"""

import os
from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Set this to point the app at a different database than the one the host
# injected. Vercel's storage integrations own DATABASE_URL — the variable is
# locked in the dashboard, and the only way to remove it is to delete the
# database. This gives you a way out that doesn't involve destroying data:
# attach whatever you like, set DATABASE_URL_OVERRIDE, and the injected value
# is ignored. Leave it unset and nothing changes.
DATABASE_URL_OVERRIDE_ENV = "DATABASE_URL_OVERRIDE"

# Managed Postgres add-ons rarely agree on what to call the connection string.
# Vercel's marketplace integrations (Neon, Supabase) inject POSTGRES_URL and
# friends rather than DATABASE_URL, so a database can be attached and correct
# while the app still falls back to SQLite and fails confusingly.
#
# Order matters: the pooled URL comes first. On serverless every invocation is
# a new process, and opening a direct connection per invocation exhausts the
# server's connection limit under any real traffic.
_DATABASE_URL_ALIASES = (
    "POSTGRES_URL",           # Vercel / Neon, pooled
    "POSTGRES_PRISMA_URL",    # Vercel, pooled, with pgbouncer args
    "DATABASE_URL_UNPOOLED",
    "POSTGRES_URL_NON_POOLING",
)


def _discover_database_url() -> str:
    """Fall back to a host-injected Postgres URL before defaulting to SQLite.

    Only consulted when DATABASE_URL itself is unset — pydantic-settings reads
    that from the environment and it always wins.
    """
    for name in _DATABASE_URL_ALIASES:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    # No infrastructure needed for `uvicorn app.main:app`. Never right for a
    # deployed instance: on serverless the filesystem is read-only outside
    # /tmp, and /tmp does not survive between invocations.
    return "sqlite:///./aura.db"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"), env_file_encoding="utf-8", extra="ignore"
    )

    # --- Core -------------------------------------------------------------
    app_name: str = "AURA AI"
    environment: Literal["local", "staging", "production"] = "local"
    debug: bool = True

    # SQLite default means `uvicorn app.main:app` works with no infra at all.
    # Point at Postgres for anything real. See _discover_database_url: if the
    # host injected POSTGRES_URL instead of DATABASE_URL, that is picked up
    # automatically so attaching a database is all the setup required.
    database_url: str = Field(default_factory=_discover_database_url)

    # Create tables and patch in new columns at startup. Right for local dev,
    # wrong once you have data you care about — set false and use Alembic.
    auto_create_schema: bool = True

    # --- Security ---------------------------------------------------------
    # MUST be overridden in production; main.py refuses to start otherwise.
    secret_key: str = "dev-only-insecure-secret-change-me"
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = 60 * 24 * 7

    # Fernet key used to encrypt OAuth refresh tokens at rest. Generate with:
    #   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    token_encryption_key: str = ""

    frontend_url: str = "http://localhost:3000"
    backend_url: str = "http://localhost:8000"
    cors_origins: str = "http://localhost:3000"

    # --- Model provider ---------------------------------------------------
    # "auto" prefers Anthropic, then OpenAI, then the offline mock.
    #
    # These are SERVER credentials and belong in the environment, not in the
    # database. The vault (services/vault.py) is for a *user's* third-party
    # secrets; putting infrastructure keys there would mean decrypting them on
    # every request and widening the blast radius of a database leak for no gain.
    llm_provider: Literal["auto", "openai", "anthropic", "mock"] = "auto"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-5"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o"

    # Point the OpenAI adapter at any OpenAI-compatible endpoint. This is the
    # cheap path: Ollama and LM Studio run free on your own machine, and Groq,
    # DeepSeek and OpenRouter are a fraction of first-party pricing. They all
    # speak the same wire format, so one field covers the lot.
    #
    #   Ollama      http://localhost:11434/v1   (api key can be anything)
    #   LM Studio   http://localhost:1234/v1
    #   Groq        https://api.groq.com/openai/v1
    #   DeepSeek    https://api.deepseek.com/v1
    #   OpenRouter  https://openrouter.ai/api/v1
    openai_base_url: str = ""

    # Background work — triage, heartbeat, schedules — runs far more often than
    # anyone chats, and needs far less judgement. Pointing it at a small model
    # is the single biggest saving available without changing behaviour.
    # Blank means "use the same model as chat".
    background_model: str = ""

    # Embeddings need OpenAI. Without it, memory and document search fall back to
    # lexical overlap — degraded but working, never broken.
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 1536
    max_agent_steps: int = 8

    # --- Spend guard ------------------------------------------------------
    # A tool-calling loop plus a heartbeat can spend money unattended. This is
    # the backstop: past the cap, the agent refuses rather than continuing.
    daily_spend_cap_usd: float = 5.0
    spend_cap_enabled: bool = True

    # --- Google OAuth -----------------------------------------------------
    google_client_id: str = ""
    google_client_secret: str = ""
    google_scopes: str = (
        "openid "
        "https://www.googleapis.com/auth/userinfo.email "
        "https://www.googleapis.com/auth/userinfo.profile "
        "https://www.googleapis.com/auth/gmail.modify "
        "https://www.googleapis.com/auth/calendar"
    )

    # --- Behaviour --------------------------------------------------------
    # Tools the agent may never fire without explicit user confirmation, on top
    # of whatever the user's autonomy tier already forbids (see agent/autonomy.py).
    destructive_tools: str = "send_email,archive_email,delete_event,delete_task"
    allow_demo_login: bool = True

    # --- Heartbeat --------------------------------------------------------
    heartbeat_enabled: bool = True
    heartbeat_default_interval_minutes: int = 30
    heartbeat_default_quiet_hours: str = "22:00-07:00"
    default_autonomy_level: str = "conservative"

    # --- Web research -----------------------------------------------------
    # brave | serper | tavily. Without a key, research falls back to the
    # user's own data and says so rather than inventing sources.
    search_provider: Literal["", "brave", "serper", "tavily"] = ""
    search_api_key: str = ""

    # --- Documents --------------------------------------------------------
    max_upload_mb: int = 20

    # --- Outbound email ---------------------------------------------------
    # Without Google connected, sending goes through SMTP instead. Leave blank
    # and send_email reports honestly that it couldn't send rather than
    # pretending it did.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_use_tls: bool = True

    # --- Rate limiting ----------------------------------------------------
    rate_limit_enabled: bool = True
    rate_limit_per_minute: int = 120          # authenticated API calls per user
    rate_limit_anon_per_minute: int = 20      # unauthenticated, per IP
    rate_limit_inbound_per_minute: int = 15   # per channel token
    redis_url: str = ""                       # blank = in-memory buckets

    @model_validator(mode="before")
    @classmethod
    def _apply_database_override(cls, values):
        """Let DATABASE_URL_OVERRIDE beat a host-injected DATABASE_URL.

        Runs before field validation, so the URL still goes through
        _normalise_database_url and gets the psycopg 3 dialect like any other.
        """
        override = os.environ.get(DATABASE_URL_OVERRIDE_ENV, "").strip()
        if override and isinstance(values, dict):
            values["database_url"] = override
        return values

    @field_validator("database_url")
    @classmethod
    def _normalise_database_url(cls, value: str) -> str:
        """Force the psycopg 3 dialect on Postgres URLs.

        Managed hosts inject a bare `postgresql://` (Render, Fly) or the legacy
        `postgres://` (Heroku, some Railway templates). SQLAlchemy maps the bare
        form to psycopg2, which isn't installed — the app would die at import
        with `ModuleNotFoundError: psycopg2`. It rejects `postgres://` outright.
        Rewriting here means the URL the host hands us just works, and nothing
        downstream has to care.
        """
        if value.startswith("postgres://"):
            return "postgresql+psycopg://" + value[len("postgres://") :]
        if value.startswith("postgresql://"):
            return "postgresql+psycopg://" + value[len("postgresql://") :]
        return value

    @property
    def smtp_configured(self) -> bool:
        return bool(self.smtp_host and self.smtp_from)

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def cors_origin_regex(self) -> str | None:
        """In local dev, trust localhost on any port.

        Next.js silently moves to :3001 when :3000 is taken, and the resulting
        failure is an opaque "Failed to fetch" with nothing in the UI to explain
        it. Pinning dev CORS to one port buys no security — an attacker who can
        run code on your loopback has already won — and costs an afternoon.
        Production still uses the explicit allowlist only.
        """
        if self.environment != "local":
            return None
        return r"http://(localhost|127\.0\.0\.1)(:\d+)?"

    @property
    def google_scope_list(self) -> list[str]:
        return [s.strip() for s in self.google_scopes.split() if s.strip()]

    @property
    def destructive_tool_set(self) -> set[str]:
        return {t.strip() for t in self.destructive_tools.split(",") if t.strip()}

    @property
    def google_oauth_configured(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret)

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def google_redirect_uri(self) -> str:
        return f"{self.backend_url}/api/auth/google/callback"

    @property
    def embeddings_available(self) -> bool:
        # A local or third-party endpoint usually can't serve OpenAI's embedding
        # models, so don't promise semantic search we can't deliver.
        return bool(self.openai_api_key and not self.openai_base_url)

    @property
    def is_local_model(self) -> bool:
        return bool(self.openai_base_url) and any(
            host in self.openai_base_url for host in ("localhost", "127.0.0.1", "0.0.0.0")
        )

    def resolved_provider(self) -> str:
        if self.llm_provider != "auto":
            return self.llm_provider
        if self.anthropic_api_key:
            return "anthropic"
        # A custom endpoint often needs no real key (Ollama ignores it), so the
        # base URL alone is enough to count as configured.
        if self.openai_api_key or self.openai_base_url:
            return "openai"
        return "mock"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
