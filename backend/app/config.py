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
    # Claude Sonnet 5.5: the best balance of speed and intelligence. For the
    # background model, claude-haiku-5-5 is the cheap choice.
    anthropic_model: str = "claude-sonnet-5-5"
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

    # --- Local device control ---------------------------------------------
    # The switch that turns AURA from a hosted assistant into one that operates
    # the computer it runs on: shell, files, apps, screen/keyboard/mouse, system.
    #
    # OFF by default, and it MUST stay off on any shared or public deployment —
    # the whole point is that it gives the agent the current user's hands on
    # this machine. The local launchers set LOCAL_MODE=true, which turns it on
    # along with the single-user owner login and the in-process worker. On a
    # server (Vercel, etc.) none of this is set, so behaviour is unchanged and
    # the device tools never even reach the model.
    local_mode: bool = False

    # Explicit override. Blank means "follow local_mode". Set DEVICE_CONTROL=off
    # to keep a local install chat-only, or =on to enable it without the other
    # local-mode conveniences (own risk).
    device_control: Literal["", "on", "off"] = ""

    # Directories the file tools may read and write. Blank means the current
    # user's home directory. Everything resolves to an absolute real path and is
    # checked against these roots, so the agent cannot read or write outside
    # them however the path is written (.., symlinks, ~).
    device_allowed_roots: str = ""

    # Directories that are always refused even inside an allowed root — keys
    # and credentials. Startup items and AURA's own folder are protected on
    # top of this list whatever it says (ALWAYS_PROTECTED, below), so an old
    # .env that sets this can't quietly drop them.
    device_protected_paths: str = (
        "~/.ssh,~/.aws,~/.gnupg,~/.config/gcloud,~/.kube,~/.docker/config.json"
    )

    # How shell command approval works, on top of the autonomy tier:
    #   ask        every command is previewed and queued for approval unless a
    #              standing grant covers it (the safe default)
    #   allowlist  commands matching device_exec_allowlist run without asking;
    #              everything else is queued
    #   full       commands run without asking (still subject to the denylist)
    # The autonomy tier still applies: at "full" trust the tier already waves
    # shell through, and this narrows that back down when you want it.
    device_exec_mode: Literal["ask", "allowlist", "full"] = "ask"

    # Command prefixes allowed to run without approval under device_exec_mode
    # = allowlist, e.g. "git status,git diff,ls,cat,python --version". Matched
    # against the command with its arguments, prefix-wise.
    device_exec_allowlist: str = ""

    # Commands refused outright, even after approval and even at full trust —
    # the last stop against a catastrophic instruction (yours or a web page's).
    # Empty by default so "operate the whole machine" means what it says; fill
    # it on a machine you want to fence off. Matched as case-insensitive
    # substrings of the command.
    device_command_denylist: str = ""

    # Cap on how long one foreground command may run before it is killed.
    device_exec_timeout_seconds: int = 300

    # Ceiling on bytes read from a single file / returned from a command, so a
    # huge file or a chatty command can't blow up the model's context.
    device_max_read_bytes: int = 200_000

    # Let the agent see and drive the screen (screenshots, mouse, keyboard).
    # Needs a desktop session and the optional packages in
    # requirements-device.txt; degrades to "unavailable" without them.
    device_screen_control: bool = True

    # Run the background worker (heartbeat, schedules, automations, compaction)
    # inside the API process on a thread, so a single launch does everything.
    # Blank means "follow local_mode". A real server should run worker.py (or a
    # platform cron) separately instead and set this to off.
    run_embedded_worker: Literal["", "on", "off"] = ""

    # Paths protected on top of device_protected_paths. The desktop app puts
    # its own data folder and its install folder here, so the file tools can
    # never rewrite AURA's settings (to widen its own limits) or its code.
    device_extra_protected_paths: str = ""

    # --- Desktop app / local install --------------------------------------
    # Where a local install keeps its own files: the database, the settings
    # file, logs and background-command output. Blank means ~/.aura, which is
    # already on the protected list above.
    aura_home: str = ""

    # The exported web app this process serves at its own address. Only the
    # desktop app sets it; the hosted site has Vercel serve the frontend.
    aura_web_dir: str = ""

    # The settings file that the in-app "AI model" and "Remote access" screens
    # write to on a local install. Blank means the project's .env when running
    # from source. Never writable from a hosted deployment.
    aura_config_file: str = ""

    # A secret only the person who launched AURA on this machine can know: the
    # desktop app keeps it in their own settings file and puts it in the link
    # it opens. When set, the owner sign-in requires it, so another account on
    # a shared computer — which can reach 127.0.0.1 too — can't sign in as you.
    aura_local_login_key: str = ""

    # Host names a local install answers to. A web page can't make your
    # browser talk to 127.0.0.1 under its own name (DNS rebinding) when the
    # API refuses every Host header that isn't this machine's.
    local_allowed_hosts: str = "localhost,127.0.0.1,::1"

    # --- Remote access (local installs only) ------------------------------
    # Lets a paired phone or laptop use this computer's AURA over your own
    # network or a tunnel like Tailscale. Off by default; the desktop app only
    # listens beyond 127.0.0.1 when this is on, and every request from another
    # device must carry a token from pairing, which you start on this computer.
    remote_access: bool = False
    # Extra host names this computer is reached by, shown on the pairing
    # screen (e.g. my-pc.tailnet-name.ts.net). Its network addresses are found
    # automatically.
    remote_hostnames: str = ""
    # How long a paired device stays signed in before it must pair again.
    remote_session_days: int = 30

    # --- Platform cron ------------------------------------------------------
    # Shared secret for /api/cron/tick, which runs the background worker's
    # loops on platforms with no long-running process (Vercel). Vercel sends it
    # automatically as "Authorization: Bearer <CRON_SECRET>" once it is set.
    # Without it the endpoint only keeps the database awake.
    cron_secret: str = ""
    # A cron call is a request with a time limit (300 s on Vercel with fluid
    # compute). The tick stops starting new work after this long and leaves
    # the rest, still due, for the next call.
    cron_budget_seconds: int = 240

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
        Production still uses the explicit allowlist only — and so does the
        desktop app, which serves its own web app from its own address and
        needs no other origin at all.
        """
        if self.environment != "local" or self.aura_web_dir:
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

    # --- Local device control ---------------------------------------------

    @property
    def device_control_enabled(self) -> bool:
        """The one gate the whole device subsystem checks.

        DEVICE_CONTROL wins if set; otherwise it follows LOCAL_MODE. Off on any
        deployment that sets neither, which is every server deployment.
        """
        if self.device_control == "on":
            return True
        if self.device_control == "off":
            return False
        return self.local_mode

    @property
    def embedded_worker_enabled(self) -> bool:
        if self.run_embedded_worker == "on":
            return True
        if self.run_embedded_worker == "off":
            return False
        return self.local_mode

    @property
    def device_allowed_root_list(self) -> list[str]:
        import os.path

        raw = [r.strip() for r in self.device_allowed_roots.split(",") if r.strip()]
        if not raw:
            raw = ["~"]
        # Real paths, because that's what every path checked against them is:
        # on macOS /tmp and /var are links into /private, and a root written
        # the short way would otherwise refuse everything under itself.
        return [os.path.realpath(os.path.expanduser(r)) for r in raw]

    @property
    def device_protected_path_list(self) -> list[str]:
        import os.path

        raw = ",".join(
            [self.device_protected_paths, self.device_extra_protected_paths, *ALWAYS_PROTECTED]
        )
        out: list[str] = []
        for p in raw.split(","):
            if p.strip():
                expanded = os.path.expanduser(p.strip())
                # Both spellings: the real path is what resolved paths are
                # compared with, and the plain one is what a person reads.
                for path in (os.path.realpath(expanded), os.path.abspath(expanded)):
                    if path not in out:
                        out.append(path)
        return out

    @property
    def aura_home_path(self) -> str:
        import os.path

        return os.path.abspath(os.path.expanduser(self.aura_home.strip() or "~/.aura"))

    @property
    def local_allowed_host_set(self) -> set[str]:
        return {h.strip().lower() for h in self.local_allowed_hosts.split(",") if h.strip()}

    @property
    def remote_hostname_list(self) -> list[str]:
        return [h.strip() for h in self.remote_hostnames.split(",") if h.strip()]

    @property
    def remote_access_enabled(self) -> bool:
        """Remote access only means something on a local install."""
        return self.local_mode and self.remote_access

    @property
    def device_exec_allow_list(self) -> list[str]:
        return [c.strip() for c in self.device_exec_allowlist.split(",") if c.strip()]

    @property
    def device_command_deny_list(self) -> list[str]:
        return [c.strip().lower() for c in self.device_command_denylist.split(",") if c.strip()]

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


# Never touched by the file tools, whatever the settings say. Everything that
# runs by itself — at login, in every new shell, on every git command — plus
# AURA's own folder: a file tool that could write one of these could run any
# program later, or widen its own limits, without the shell's approval card
# ever appearing.
ALWAYS_PROTECTED = (
    "~/.aura",
    # git settings can name commands it runs (core.fsmonitor, ! aliases)
    "~/.gitconfig",
    "~/.config/git",
    # Windows: the Startup folder and PowerShell profiles (which live under
    # OneDrive when it has taken over the Documents folder)
    "~/AppData/Roaming/Microsoft/Windows/Start Menu/Programs/Startup",
    "~/Documents/WindowsPowerShell",
    "~/Documents/PowerShell",
    "~/OneDrive/Documents/WindowsPowerShell",
    "~/OneDrive/Documents/PowerShell",
    # macOS and Linux: login agents, autostart entries, shell start-up files
    "~/Library/LaunchAgents",
    "~/.config/autostart",
    "~/.config/systemd",
    "~/.bashrc",
    "~/.bash_profile",
    "~/.bash_login",
    "~/.profile",
    "~/.zshrc",
    "~/.zprofile",
    "~/.zshenv",
    "~/.zlogin",
    "~/.config/fish",
)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
