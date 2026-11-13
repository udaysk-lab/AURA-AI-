"""F13: a fetch that is redirected to another host is judged again against the user's URL rules."""

from __future__ import annotations

import httpx
import pytest

from app.device import net
from app.permissions import rules as rules_store
from tests.sec_world import build, run

PUBLIC = "93.184.216.34"


@pytest.fixture
def web(monkeypatch):
    requested: list[str] = []
    table = {"a.example": [PUBLIC], "b.example": [PUBLIC], "www.a.example": [PUBLIC], "c.example": [PUBLIC]}
    monkeypatch.setattr(net, "resolve", lambda host, port: list(table[host]))
    state = {"location": "https://b.example/landing"}

    def handler(request):
        host = request.headers.get("host", "")
        requested.append(host)
        if host == "a.example":
            return httpx.Response(302, headers={"location": state["location"]})
        return httpx.Response(200, text=f"page on {host}")

    real = net._make_client
    monkeypatch.setattr(net, "_make_client", lambda transport=None: real(httpx.MockTransport(handler)))
    return requested, state


def rule(w, effect, domain):
    rules_store.add(w.db, w.user, effect=effect, system="device-control", tool="fetch_url",
                    target_kind="domain", pattern=domain)
    w.db.commit()


def fetch(w, url="https://a.example/start"):
    d, out = run(w, "fetch_url", {"url": url})
    assert d.kind == "allow", d.reason
    return out


def test_redirect_to_another_host_without_an_allow_is_refused(tmp_path, monkeypatch, web):
    requested, _ = web
    w = build(tmp_path, monkeypatch)
    out = fetch(w)
    assert "error" in out and "b.example" in out["error"], out
    assert requested == ["a.example"], "the second host must never be contacted"


def test_a_rule_for_another_site_does_not_help(tmp_path, monkeypatch, web):
    requested, _ = web
    w = build(tmp_path, monkeypatch)
    rule(w, "allow", "c.example")
    assert "b.example" in fetch(w)["error"] and requested == ["a.example"]


def test_an_allow_rule_for_the_target_follows_the_redirect(tmp_path, monkeypatch, web):
    requested, _ = web
    w = build(tmp_path, monkeypatch)
    rule(w, "allow", "b.example")
    out = fetch(w)
    assert out.get("content") == "page on b.example" and requested == ["a.example", "b.example"]


def test_a_block_rule_wins_over_an_allow(tmp_path, monkeypatch, web):
    requested, _ = web
    w = build(tmp_path, monkeypatch)
    rule(w, "allow", "b.example")
    rule(w, "block", "b.example")
    assert "blocks" in fetch(w)["error"] and requested == ["a.example"]


def test_a_redirect_inside_the_same_host_is_not_a_new_host(tmp_path, monkeypatch, web):
    requested, state = web
    w = build(tmp_path, monkeypatch)
    state["location"] = "https://A.example./other"  # same host, other spelling: still the loop, never a refusal
    out = fetch(w)
    assert "b.example" not in str(out) and "no rule of yours" not in str(out)
    assert len(requested) > 1, "the same-host hop was followed"
