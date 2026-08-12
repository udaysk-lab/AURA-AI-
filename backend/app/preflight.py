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


def _check_database() -> Check:
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
    return Check("database", "Database", "ok", "Postgres.")


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
]


def run() -> Report:
    return Report(
        environment=settings.environment,
        checks=[check() for check in CHECKS],
    )


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
