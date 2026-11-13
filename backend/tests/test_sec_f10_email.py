"""F10: inbound email is capped at Ask first, cannot name a forwarding bridge, and matches the address only."""

from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_aura.db")
os.environ.setdefault("LLM_PROVIDER", "mock")

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

from app import models  # noqa: E402
from app.permissions import gate  # noqa: E402
from app.services import channels  # noqa: E402
from tests.sec_world import build  # noqa: E402


def _channel(w, kind="email", **config):
    ch = models.Channel(user_id=w.user.id, kind=kind, enabled=True, verified=True, token="t" * 20, config=config)
    w.db.add(ch)
    w.db.commit()
    return ch


@pytest.mark.parametrize("mode", ["ask", "skip", "auto"])
def test_email_is_ask_first_in_every_mode(tmp_path, monkeypatch, mode):
    w = build(tmp_path, monkeypatch, mode=mode)
    args = {"path": os.path.join(w.work, "note.txt"), "content": "hi"}
    chat = gate.decide(w.db, w.user, "write_file", args, gate.build_context(w.db, w.user, "chat"))
    mail = gate.decide(w.db, w.user, "write_file", args,
                       gate.build_context(w.db, w.user, "inbound", origin_id="email"))
    assert mail.kind == "ask", (mode, mail.kind, mail.reason_code)
    if mode == "skip":
        assert chat.kind == "allow"  # the control: the owner's own chat is unchanged
        other = gate.decide(w.db, w.user, "write_file", args,
                            gate.build_context(w.db, w.user, "inbound", origin_id="telegram"))
        assert other.kind == "allow"


def test_email_has_no_bridge_option(tmp_path, monkeypatch):
    w = build(tmp_path, monkeypatch)
    ch = _channel(w, bridge_owner_only=True)  # a row stored before the fix
    assert channels.bridge_owner_only_of(ch) is False
    assert channels.is_owner_message(ch, "anyone@evil.example") is False
    with pytest.raises(ValueError):
        channels.set_owner(w.db, ch, "", True)
    tg = _channel(w, "telegram")
    assert channels.set_owner(w.db, tg, "", True) is tg and channels.bridge_owner_only_of(tg) is True


def test_owner_route_refuses_email_bridge(tmp_path, monkeypatch):
    from app.api import hub
    from app.schemas import ChannelOwnerIn

    w = build(tmp_path, monkeypatch)
    _channel(w)
    with pytest.raises(HTTPException) as err:
        hub.set_channel_owner("email", ChannelOwnerIn(owner_sender="", bridge_owner_only=True), w.user, w.db)
    assert err.value.status_code == 400


SPOOFED = [
    "ceo@example.com <attacker@evil.example>",         # display name that looks like the owner
    '"me@example.com" <attacker@evil.example>',
    "me@example.com, attacker@evil.example",           # two addresses
    "attacker@evil.example, me@example.com",
    "me@example.com@evil.example",
    "me@example.com.evil.example",
    "Me <me@example.com> <attacker@evil.example>",
    "me@example.com;attacker@evil.example",
    "me",                                              # no address
    "",
]


@pytest.mark.parametrize("sender", SPOOFED)
def test_sender_check_uses_the_address_only(tmp_path, monkeypatch, sender):
    w = build(tmp_path, monkeypatch)
    ch = _channel(w, owner_sender="me@example.com")
    assert channels.is_owner_message(ch, sender) is False


@pytest.mark.parametrize("sender", ["me@example.com", "Me Myself <ME@Example.com>", '"Me" <me@example.com>'])
def test_the_owner_still_matches(tmp_path, monkeypatch, sender):
    w = build(tmp_path, monkeypatch)
    assert channels.is_owner_message(_channel(w, owner_sender="Me <me@example.com>"), sender) is True
