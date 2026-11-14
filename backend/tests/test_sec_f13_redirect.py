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


# --- REG1: an ordinary redirect to the www. twin of the same site -----------------------------------------


@pytest.mark.parametrize("start,location,followed", [
    ("https://a.example/start", "https://www.a.example/landing", True),      # a -> www.a
    ("https://www.a.example/start", "https://a.example/landing", True),      # www.a -> a
    ("http://a.example/start", "https://www.a.example/landing", True),       # upgrade to https as well
    ("https://a.example/start", "https://WWW.A.EXAMPLE./landing", True),     # case and a trailing dot
    ("https://a.example/start", "http://www.a.example/landing", False),      # a downgrade is a new decision
    ("https://a.example/start", "https://www.b.example/landing", False),     # another site
    ("https://a.example/start", "https://sub.a.example/landing", False),     # a subdomain other than www
    ("https://a.example/start", "https://www.www.a.example/landing", False),
    ("https://a.example/start", "https://evil-a.example/landing", False),
])
def test_a_redirect_to_the_www_twin_needs_no_new_rule(tmp_path, monkeypatch, start, location, followed):
    requested: list[str] = []
    table = {h: [PUBLIC] for h in ("a.example", "www.a.example", "b.example", "www.b.example", "sub.a.example",
                                   "www.www.a.example", "evil-a.example")}
    monkeypatch.setattr(net, "resolve", lambda host, port: list(table[host.lower().rstrip(".")]))
    first = start.split("/")[2]

    def handler(request):
        host = request.headers.get("host", "")
        requested.append(host)
        if host == first:
            return httpx.Response(302, headers={"location": location})
        return httpx.Response(200, text=f"page on {host}")

    real = net._make_client
    monkeypatch.setattr(net, "_make_client", lambda transport=None: real(httpx.MockTransport(handler)))
    w = build(tmp_path, monkeypatch)
    out = fetch(w, start)
    if followed:
        assert out.get("content", "").startswith("page on"), out
        assert len(requested) == 2
    else:
        assert "error" in out and len(requested) == 1, out


def test_a_block_rule_for_the_www_twin_still_applies(tmp_path, monkeypatch, web):
    requested, state = web
    state["location"] = "https://www.a.example/landing"
    w = build(tmp_path, monkeypatch)
    rule(w, "block", "www.a.example")
    out = fetch(w)
    assert "error" in out and requested == ["a.example"], out


def test_the_network_floors_still_apply_on_the_www_hop(tmp_path, monkeypatch, web):
    requested, state = web
    state["location"] = "https://www.a.example/landing"
    monkeypatch.setattr(net, "resolve", lambda host, port: ["127.0.0.1"] if host.startswith("www.") else [PUBLIC])
    w = build(tmp_path, monkeypatch)
    out = fetch(w)
    assert "error" in out and requested == ["a.example"], out
