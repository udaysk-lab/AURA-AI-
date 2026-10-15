"""Settings for AURA on your own computer.

A downloaded AURA has no server administrator to edit a .env file for it, so
the person at the computer sets the model key here, and switches remote access
on or off. Local installs only, from this computer only — never from a paired
phone, and never on a hosted deployment, where every route here is a 404.

A key is written to AURA's own settings file and applied to the running app at
once. It is never sent back: the API returns whether one is set and a hint.
"""

from __future__ import annotations

import logging
import re
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.config import settings
from app.deps import DbSession, LocalOwner
from app.llm import get_provider
from app.services import local_config
from app.services import usage as usage_service

log = logging.getLogger("aura.local")
router = APIRouter(prefix="/api/local", tags=["local"])

_MODEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@-]{0,99}$")


class ProviderOut(BaseModel):
    provider: str                 # what AURA is using right now
    choice: str                   # auto | anthropic | openai | mock
    anthropic_key_set: bool
    anthropic_key_hint: str
    anthropic_model: str
    openai_key_set: bool
    openai_key_hint: str
    openai_model: str
    openai_base_url: str
    background_model: str
    config_file: str
    writable: bool


class ProviderIn(BaseModel):
    """Only the fields present are changed. An empty key removes it."""

    choice: Literal["auto", "anthropic", "openai", "mock"] | None = None
    anthropic_api_key: str | None = Field(default=None, max_length=400)
    anthropic_model: str | None = Field(default=None, max_length=100)
    openai_api_key: str | None = Field(default=None, max_length=400)
    openai_model: str | None = Field(default=None, max_length=100)
    openai_base_url: str | None = Field(default=None, max_length=300)
    background_model: str | None = Field(default=None, max_length=100)


class ProviderTestOut(BaseModel):
    ok: bool
    provider: str
    model: str
    detail: str


class RemoteSettingsOut(BaseModel):
    enabled: bool                 # on in the running app
    saved: bool                   # what the settings file says (applies on restart)
    hostnames: str
    restart_needed: bool
    writable: bool


class RemoteSettingsIn(BaseModel):
    enabled: bool
    hostnames: str = Field(default="", max_length=500)


def _provider_out() -> ProviderOut:
    return ProviderOut(
        provider=get_provider().name,
        choice=settings.llm_provider,
        anthropic_key_set=bool(settings.anthropic_api_key),
        anthropic_key_hint=local_config.hint(settings.anthropic_api_key),
        anthropic_model=settings.anthropic_model,
        openai_key_set=bool(settings.openai_api_key),
        openai_key_hint=local_config.hint(settings.openai_api_key),
        openai_model=settings.openai_model,
        openai_base_url=settings.openai_base_url,
        background_model=settings.background_model,
        config_file=str(local_config.config_path()),
        writable=local_config.is_writable(),
    )


def _clean_key(value: str) -> str:
    key = value.strip()
    if any(ch.isspace() for ch in key):
        raise HTTPException(status_code=422, detail="A key can't contain spaces or line breaks.")
    return key


def _clean_model(value: str, field: str) -> str:
    model = value.strip()
    if model and not _MODEL.match(model):
        raise HTTPException(status_code=422, detail=f"{field} doesn't look like a model name.")
    return model


@router.get("/provider", response_model=ProviderOut)
def provider_settings(owner: LocalOwner) -> ProviderOut:
    return _provider_out()


@router.put("/provider", response_model=ProviderOut)
def update_provider(payload: ProviderIn, owner: LocalOwner) -> ProviderOut:
    if not local_config.is_writable():
        raise HTTPException(
            status_code=409,
            detail=f"AURA can't write its settings file ({local_config.config_path()}).",
        )

    # Validate everything before writing anything.
    updates: dict[str, str | None] = {}
    applied: dict[str, str] = {}
    if payload.choice is not None:
        updates["LLM_PROVIDER"] = payload.choice
        applied["llm_provider"] = payload.choice
    if payload.anthropic_api_key is not None:
        key = _clean_key(payload.anthropic_api_key)
        updates["ANTHROPIC_API_KEY"] = key or None
        applied["anthropic_api_key"] = key
    if payload.anthropic_model is not None:
        model = _clean_model(payload.anthropic_model, "The Claude model")
        updates["ANTHROPIC_MODEL"] = model or None
        applied["anthropic_model"] = model or "claude-sonnet-5-5"
    if payload.openai_api_key is not None:
        key = _clean_key(payload.openai_api_key)
        updates["OPENAI_API_KEY"] = key or None
        applied["openai_api_key"] = key
    if payload.openai_model is not None:
        model = _clean_model(payload.openai_model, "The OpenAI-compatible model")
        updates["OPENAI_MODEL"] = model or None
        applied["openai_model"] = model or "gpt-4o"
    if payload.openai_base_url is not None:
        url = payload.openai_base_url.strip().rstrip("/")
        if url and not re.match(r"^https?://[^\s/]+", url):
            raise HTTPException(status_code=422, detail="The endpoint must start with http:// or https://")
        updates["OPENAI_BASE_URL"] = url or None
        applied["openai_base_url"] = url
    if payload.background_model is not None:
        model = _clean_model(payload.background_model, "The background model")
        updates["BACKGROUND_MODEL"] = model or None
        applied["background_model"] = model

    if updates:
        local_config.write_values(updates)
        for field, value in applied.items():
            setattr(settings, field, value)
        provider = get_provider(force_reload=True)
        log.info("Model settings changed on this computer; provider is now %s", provider.name)
    return _provider_out()


@router.post("/provider/test", response_model=ProviderTestOut)
def test_provider(owner: LocalOwner, db: DbSession) -> ProviderTestOut:
    """Make one tiny call, so "it works" is something you saw rather than hoped."""
    provider = get_provider()
    model = getattr(provider, "model", "") or ""
    if provider.name == "mock":
        return ProviderTestOut(
            ok=False, provider="mock", model="",
            detail="No model is connected yet — AURA is using its offline demo mode.",
        )
    try:
        usage_service.check(db, owner.id, trigger="chat")
        response = provider.complete(
            [{"role": "user", "content": "Reply with exactly one word: ready"}]
        )
        usage_service.record(db, owner.id, response.usage, trigger="chat")
    except usage_service.SpendCapReached:
        return ProviderTestOut(
            ok=False, provider=provider.name, model=model,
            detail="Today's spend cap is used up, so AURA didn't make the call.",
        )
    except Exception as exc:  # the SDKs raise their own types; show what they said
        text = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
        return ProviderTestOut(ok=False, provider=provider.name, model=model, detail=text[:400])
    reply = (response.text or "").strip()
    return ProviderTestOut(
        ok=True, provider=provider.name, model=model,
        detail=f"Connected. The model replied: “{reply[:60]}”" if reply else "Connected.",
    )


@router.get("/remote", response_model=RemoteSettingsOut)
def remote_settings(owner: LocalOwner) -> RemoteSettingsOut:
    saved = local_config.read_values()
    saved_on = saved.get("REMOTE_ACCESS", "").strip().lower() in ("1", "true", "yes", "on")
    return RemoteSettingsOut(
        enabled=settings.remote_access_enabled,
        saved=saved_on,
        hostnames=saved.get("REMOTE_HOSTNAMES", settings.remote_hostnames),
        restart_needed=saved_on != settings.remote_access_enabled,
        writable=local_config.is_writable(),
    )


@router.put("/remote", response_model=RemoteSettingsOut)
def update_remote(payload: RemoteSettingsIn, owner: LocalOwner) -> RemoteSettingsOut:
    """Saved for the next start: listening on the network is decided at launch.

    Turning it off also takes effect at once — paired devices stop working
    immediately — because refusing is always safe to do early.
    """
    if not local_config.is_writable():
        raise HTTPException(
            status_code=409,
            detail=f"AURA can't write its settings file ({local_config.config_path()}).",
        )
    names = ",".join(
        h.strip() for h in payload.hostnames.split(",")
        if h.strip() and re.match(r"^[A-Za-z0-9.-]{1,253}$", h.strip())
    )
    local_config.write_values(
        {"REMOTE_ACCESS": "true" if payload.enabled else "false", "REMOTE_HOSTNAMES": names or None}
    )
    settings.remote_hostnames = names
    if not payload.enabled:
        settings.remote_access = False
    return remote_settings(owner)
