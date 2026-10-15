"""Preflight checks.

The failure mode this exists to prevent: someone deploys AURA, it starts
cleanly, and then quietly behaves like a keyword router because a key was
missing — or worse, runs in production with the development secret.

Every check returns a status, a reason and the exact fix. Nothing here says
"misconfigured"; it says which variable to set and where.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from app.config import settings

Level = Literal["ok", "warn", "fail"]

DEV_SECRET = "dev-only-insecure-secret-change-me"


@dataclass
class Check:
    key: str
    label: str
    level: Level
    detail: str
    fix: str = ""
    docs: str = ""

    @property
    def blocking(self) -> bool:
        return self.level == "fail"


@dataclass
class Report:
    environment: str
    checks: list[Check] = field(default_factory=list)

    @property
    def failures(self) -> list[Check]:
        return [c for c in self.checks if c.level == "fail"]

    @property
    def warnings(self) -> list[Check]:
        return [c for c in self.checks if c.level == "warn"]

    @property
    def ready(self) -> bool:
        return not self.failures

    def as_dict(self) -> dict:
        return {
            "environment": self.environment,
            "ready": self.ready,
            "failures": len(self.failures),
            "warnings": len(self.warnings),
            "checks": [
                {
                    "key": c.key,
                    "label": c.label,
                    "level": c.level,
                    "detail": c.detail,
                    "fix": c.fix,
                    "docs": c.docs,
                }
                for c in self.checks
            ],
        }


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def _check_model() -> Check:
    provider = settings.resolved_provider()
    background = (
        f" Background work uses {settings.background_model}."
        if settings.background_model
        else ""
    )

    if provider == "anthropic":
        return Check(
            "model", "Model provider", "ok",
            f"Claude via Anthropic ({settings.anthropic_model}).{background}",
        )
    if provider == "openai":
        if settings.is_local_model:
            return Check(
                "model", "Model provider", "ok",
                f"Local model ({settings.openai_model}) at {settings.openai_base_url}. "
                f"No per-token cost.{background}",
            )
        if settings.openai_base_url:
            return Check(
                "model", "Model provider", "ok",
                f"{settings.openai_model} via {settings.openai_base_url}.{background}",
            )
        return Check(
            "model", "Model provider", "ok",
            f"OpenAI ({settings.openai_model}).{background}",
        )

    level: Level = "fail" if settings.environment == "production" else "warn"
    return Check(
        "model", "Model provider", level,
        "No model configured — running the offline keyword router. Every screen "
        "works, but there is no real reasoning.",
        fix=(
            "Cheapest: install Ollama, run `ollama pull qwen2.5:7b`, then set "
            "OPENAI_BASE_URL=http://localhost:11434/v1 and OPENAI_MODEL=qwen2.5:7b. "
            "Free, runs on your machine.\n"
            "Best quality: set ANTHROPIC_API_KEY. Restart the backend either way."
        ),
        docs="https://ollama.com  ·  https://console.anthropic.com",
    )


def _check_secret_key() -> Check:
    if settings.secret_key == DEV_SECRET:
        level: Level = "fail" if settings.environment != "local" else "warn"
        return Check(
            "secret_key", "Session signing key", level,
            "Using the built-in development secret. Anyone who has read the "
            "source can forge a login token.",
            fix='Set SECRET_KEY to a random value: python -c "import secrets; print(secrets.token_urlsafe(48))"',
        )
    if len(settings.secret_key) < 32:
        return Check(
            "secret_key", "Session signing key", "warn",
            f"Only {len(settings.secret_key)} characters — short enough to brute force.",
            fix="Use at least 32 characters.",
        )
    return Check("secret_key", "Session signing key", "ok", "Set and long enough.")


def _check_encryption_key() -> Check:
    if not settings.token_encryption_key:
        level: Level = "warn" if settings.environment == "local" else "fail"
        return Check(
            "encryption_key", "Credential encryption", level,
            "Derived from SECRET_KEY. Workable, but rotating SECRET_KEY would "
            "make every stored OAuth token and vault secret unreadable.",
            fix='Set TOKEN_ENCRYPTION_KEY: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"',
        )
    return Check("encryption_key", "Credential encryption", "ok", "Dedicated key set.")


def _check_demo_login() -> Check:
    if settings.allow_demo_login and settings.environment != "local":
        return Check(
            "demo_login", "Demo login", "fail",
            "Password-free login is enabled outside local development. Anyone "
            "who knows an email address can sign in as that user.",
            fix="Set ALLOW_DEMO_LOGIN=false.",
        )
    if settings.allow_demo_login:
        return Check(
            "demo_login", "Demo login", "ok",
            "Enabled for local development. Turn it off before deploying.",
        )
    return Check("demo_login", "Demo login", "ok", "Disabled.")


def probe_database(timeout_seconds: int = 4) -> Check | None:
    """Open one real connection, quickly. None when it worked.

    A separate throwaway engine, so a short connect timeout applies here
    without changing how the app's own connections behave.
    """
    from sqlalchemy import create_engine, text
    from sqlalchemy.pool import NullPool

    connect_args: dict = {}
    if settings.is_sqlite:
        connect_args["timeout"] = timeout_seconds
    else:
        connect_args["connect_timeout"] = timeout_seconds
        connect_args["prepare_threshold"] = None
    probe = create_engine(settings.database_url, poolclass=NullPool, connect_args=connect_args)
    try:
        with probe.connect() as conn:
            conn.execute(text("SELECT 1"))
        return None
    except Exception as exc:
        first = (str(getattr(exc, "orig", None) or exc).strip().splitlines() or [type(exc).__name__])[0]
        return Check(
            "database", "Database", "warn",
            f"Couldn't connect: {first[:200]}",
            fix=(
                "Check DATABASE_URL and that the database is running. A Supabase "
                "project that was paused needs Restore project in its dashboard."
            ),
        )
    finally:
        probe.dispose()


def _check_database(probe: bool = False) -> Check:
    if probe:
        failed = probe_database()
        if failed is not None:
            return failed
    if settings.is_sqlite:
        level: Level = "fail" if settings.environment == "production" else "ok"
        detail = (
            "SQLite. Fine for one person on one machine; it will not survive "
            "concurrent writes from multiple workers."
            if level == "fail"
            else "SQLite — no setup required."
        )
        return Check(
            "database", "Database", level, detail,
            fix="Set DATABASE_URL to a Postgres URL for production.",
        )
    return Check(
        "database", "Database", "ok",
        "Postgres, reachable." if probe else "Postgres.",
    )


def _check_cors() -> Check:
    origins = settings.cors_origin_list
    if "*" in origins:
        return Check(
            "cors", "CORS", "fail",
            "Wildcard origin with credentials enabled — any site could call the "
            "API using a signed-in user's browser.",
            fix="Set CORS_ORIGINS to your exact frontend URL.",
        )
    if settings.environment != "local" and any(o.startswith("http://") for o in origins):
        return Check(
            "cors", "CORS", "warn",
            "A plain-HTTP origin is allowed outside local development.",
            fix="Use https:// origins in CORS_ORIGINS.",
        )
    return Check("cors", "CORS", "ok", f"{len(origins)} origin(s) allowed.")


def _check_embeddings() -> Check:
    if settings.embeddings_available:
        return Check("embeddings", "Semantic search", "ok", "OpenAI embeddings available.")
    detail = (
        "Memory and document search fall back to word overlap — it works, but it "
        "won't match meaning across different wording."
    )
    if settings.openai_base_url:
        return Check(
            "embeddings", "Semantic search", "warn",
            f"Custom endpoint in use, which usually can't serve OpenAI embeddings. {detail}",
            fix="Optional: leave as is, or add a real OPENAI_API_KEY alongside it.",
        )
    return Check(
        "embeddings", "Semantic search", "warn",
        f"No embedding provider. {detail}",
        fix=(
            "Optional. OPENAI_API_KEY buys embeddings for roughly $0.02 per million "
            "tokens — cheap even on a tight budget."
        ),
    )


def _check_google() -> Check:
    if settings.google_oauth_configured:
        return Check("google", "Gmail + Calendar", "ok", "OAuth client configured.")
    return Check(
        "google", "Gmail + Calendar", "warn",
        "Not configured. Email and calendar screens run on the sample dataset.",
        fix="Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET, redirect "
            f"{settings.backend_url}/api/auth/google/callback",
    )


def _check_email_sending() -> Check:
    if settings.google_oauth_configured or settings.smtp_configured:
        route = "Gmail" if settings.google_oauth_configured else "SMTP"
        return Check("email_out", "Sending email", "ok", f"Via {route}.")
    return Check(
        "email_out", "Sending email", "warn",
        "No send route. Drafts are produced but nothing can actually be sent — "
        "the tool reports that honestly rather than pretending.",
        fix="Connect Google, or set SMTP_HOST / SMTP_FROM / SMTP_USERNAME / SMTP_PASSWORD.",
    )


def _check_search() -> Check:
    if settings.search_provider and settings.search_api_key:
        return Check("search", "Web research", "ok", f"Via {settings.search_provider}.")
    return Check(
        "search", "Web research", "warn",
        "No search provider. Research falls back to the user's own inbox, "
        "contacts and documents, and says so.",
        fix="Set SEARCH_PROVIDER (brave|serper|tavily) and SEARCH_API_KEY.",
    )


def _check_spend_cap() -> Check:
    if not settings.spend_cap_enabled:
        return Check(
            "spend", "Spend cap", "warn",
            "Disabled. A runaway agent loop or a leaked channel token can spend "
            "without limit.",
            fix="Set SPEND_CAP_ENABLED=true.",
        )
    return Check(
        "spend", "Spend cap", "ok",
        f"${settings.daily_spend_cap_usd:.2f} per user per day.",
    )


def _check_rate_limit() -> Check:
    if not settings.rate_limit_enabled:
        return Check(
            "rate_limit", "Rate limiting", "warn" if settings.environment == "local" else "fail",
            "Disabled. Inbound channel webhooks are unauthenticated by session "
            "and can be hammered.",
            fix="Set RATE_LIMIT_ENABLED=true.",
        )
    backend = "Redis" if settings.redis_url else "in-memory (per-process)"
    level: Level = (
        "warn" if not settings.redis_url and settings.environment == "production" else "ok"
    )
    return Check(
        "rate_limit", "Rate limiting", level,
        f"Enabled, {backend}."
        + (
            " With multiple workers each keeps its own counters, so the real "
            "limit is N times higher than configured."
            if level == "warn"
            else ""
        ),
        fix="Set REDIS_URL so limits are shared across workers." if level == "warn" else "",
    )


def _check_debug() -> Check:
    if settings.debug and settings.environment != "local":
        return Check(
            "debug", "Debug mode", "fail",
            "Debug is on outside local development — internal error messages "
            "are returned to clients.",
            fix="Set DEBUG=false.",
        )
    return Check("debug", "Debug mode", "ok", "Appropriate for this environment.")


def _check_device_control() -> Check:
    """Is device control on, and is this a machine it should be on for?

    The dangerous configuration is device control on a deployment that isn't a
    single person's own computer: it would hand the model a shell on a shared
    host. That's a blocking failure, not a warning.
    """
    if not settings.device_control_enabled:
        return Check(
            "device", "Device control", "ok",
            "Off — AURA works on its own data only.",
            fix="Set LOCAL_MODE=true to let AURA operate the computer it runs on.",
        )

    if settings.environment == "production":
        return Check(
            "device", "Device control", "fail",
            "Device control is ON in a production environment. That gives the model a "
            "shell, your files and your screen on a shared host.",
            fix="Set DEVICE_CONTROL=off (and LOCAL_MODE=false) on anything but your own computer.",
        )

    from app.device.runtime import capabilities

    caps = capabilities()
    roots = ", ".join(settings.device_allowed_root_list)
    missing = [name for name, present in caps["packages"].items() if not present]

    if settings.environment != "local":
        return Check(
            "device", "Device control", "warn",
            f"On, in a '{settings.environment}' environment. Intended for a local install.",
            fix="Only leave this on if this really is the user's own machine.",
        )

    detail = f"On. {caps['os']} {caps['arch']}, shell {caps['shell']['kind']}, folders: {roots}."
    if missing:
        return Check(
            "device", "Device control", "warn",
            detail + f" Optional packages missing: {', '.join(missing)}.",
            fix="pip install -r requirements-device.txt  (adds process stats, screen control, clipboard)",
        )
    return Check("device", "Device control", "ok", detail)


def _check_device_exposure() -> Check:
    """Device control plus a non-loopback bind is the shape that gets people hurt."""
    if not settings.device_control_enabled:
        return Check("device_exposure", "Device exposure", "ok", "Not applicable.")

    origins = settings.cors_origin_list
    remote = [
        o for o in origins
        if not any(host in o for host in ("localhost", "127.0.0.1", "[::1]"))
    ]
    if remote:
        return Check(
            "device_exposure", "Device exposure", "warn",
            f"Device control is on and these non-local origins may call the API: {', '.join(remote)}.",
            fix=(
                "Keep a device-controlling instance on 127.0.0.1 and set "
                "CORS_ORIGINS=http://localhost:3000 only."
            ),
        )
    return Check(
        "device_exposure", "Device exposure", "ok",
        "Local origins only — the device surface isn't reachable from the network.",
    )


CHECKS = [
    _check_model,
    _check_secret_key,
    _check_encryption_key,
    _check_demo_login,
    _check_database,
    _check_cors,
    _check_debug,
    _check_rate_limit,
    _check_spend_cap,
    _check_embeddings,
    _check_google,
    _check_email_sending,
    _check_search,
    _check_device_control,
    _check_device_exposure,
]


def run(probe_database: bool = False) -> Report:
    """`probe_database` opens a real connection for the database check.

    Off at startup, where init_db has just connected anyway and every extra
    handshake lengthens a cold start; on for the Setup screen.
    """
    checks = []
    for check in CHECKS:
        if check is _check_database:
            checks.append(_check_database(probe=probe_database))
        else:
            checks.append(check())
    return Report(environment=settings.environment, checks=checks)


def log_report(logger) -> Report:
    """Print the report at startup. Loud about failures, quiet about successes."""
    report = run()

    icons = {"ok": "✓", "warn": "!", "fail": "✗"}
    logger.info("── AURA preflight (%s) ──", report.environment)
    for check in report.checks:
        line = "%s %-22s %s"
        args = (icons[check.level], check.label, check.detail)
        if check.level == "fail":
            logger.error(line, *args)
        elif check.level == "warn":
            logger.warning(line, *args)
        else:
            logger.info(line, *args)
        if check.fix and check.level != "ok":
            logger.warning("  → %s", check.fix)

    if report.failures:
        logger.error(
            "%d blocking issue(s). See /api/health/preflight or the Setup screen.",
            len(report.failures),
        )
    else:
        logger.info(
            "Ready. %d warning(s).", len(report.warnings)
        )
    return report
