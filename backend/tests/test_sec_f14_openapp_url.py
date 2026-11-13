"""F14: a URL given to open_app is a URL target: floors, domain rules and the reviewer's pre-blocks apply."""

from __future__ import annotations

import pytest

from app.device import apps, net
from app.permissions import gate, match, reviewer, rules as rules_store
from app.permissions.catalogue import CATALOGUE
from tests.sec_world import build

PUBLIC = "93.184.216.34"


@pytest.fixture(autouse=True)
def _dns(monkeypatch):
    monkeypatch.setattr(net, "resolve", lambda host, port: ["127.0.0.1"] if host == "2130706433" else [PUBLIC])


@pytest.fixture
def opened(monkeypatch):
    seen = []
    monkeypatch.setattr(apps, "_open_target", lambda target, label: seen.append(target) or {"message": label})
    return seen


def decide(w, url):
    return gate.decide(w.db, w.user, "open_app", {"name": url}, gate.build_context(w.db, w.user, "chat"))


def test_a_url_is_extracted_as_a_url_target():
    t = match.extract(CATALOGUE["open_app"], {"name": "HTTPS://Example.com/x"})
    assert [(x.kind, x.value, x.scheme) for x in t] == [("url", "example.com", "https")]
    assert [x.kind for x in match.extract(CATALOGUE["open_app"], {"name": "Spotify"})] == ["app"]


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:8765/api/me", "http://localhost/", "http://[::1]/", "http://169.254.169.254/latest/meta-data/",
    "HTTP://127.0.0.1/", "https://printer.local/", "http://2130706433/",
])
def test_the_network_floors_apply(tmp_path, monkeypatch, url):
    w = build(tmp_path, monkeypatch, mode="skip")
    d = decide(w, url)
    assert d.kind == "deny" and d.reason_code.startswith("FLOOR"), (url, d.kind, d.reason_code)


def test_domain_rules_apply(tmp_path, monkeypatch):
    w = build(tmp_path, monkeypatch, mode="ask")
    assert decide(w, "https://example.com/a").kind == "ask"
    rules_store.add(w.db, w.user, effect="allow", system="device-control", tool="open_app",
                    target_kind="domain", pattern="example.com")
    rules_store.add(w.db, w.user, effect="block", system="*", target_kind="domain", pattern="evil.example")
    w.db.commit()
    assert decide(w, "https://www.example.com/a").kind == "allow"
    assert decide(w, "https://other.example/a").kind == "ask"
    d = decide(w, "https://evil.example/a")
    assert d.kind == "deny" and d.reason_code == "RULE_BLOCK"
    # an app-name allow rule does not cover a URL
    rules_store.add(w.db, w.user, effect="allow", system="device-control", tool="open_app",
                    target_kind="app", pattern="firefox")
    w.db.commit()
    assert decide(w, "https://other.example/a").kind == "ask"


def test_skip_mode_still_runs_the_floors_and_block_rules(tmp_path, monkeypatch):
    w = build(tmp_path, monkeypatch, mode="skip")
    rules_store.add(w.db, w.user, effect="block", system="*", target_kind="domain", pattern="evil.example")
    w.db.commit()
    assert decide(w, "https://evil.example/").kind == "deny"
    assert decide(w, "https://example.com/").kind == "allow"


def test_the_reviewer_pre_blocks_a_payload_url(tmp_path, monkeypatch):
    w = build(tmp_path, monkeypatch, mode="auto")
    url = "https://example.com/c?d=" + "A1b2" * 20
    targets = match.extract(CATALOGUE["open_app"], {"name": url})
    out = reviewer.pre_block(w.db, w.user, CATALOGUE["open_app"], {"name": url}, targets, gate.build_context(w.db, w.user, "chat"))
    assert out and out[0] == reviewer.PRE_EXFIL_URL


@pytest.mark.parametrize("name", ["ms-msdt:/id PCWDiagnostic", "file:///etc/passwd", "javascript:alert(1)", "smb://host/x"])
def test_other_schemes_are_not_app_names(monkeypatch, name):
    monkeypatch.setattr(apps, "require_enabled", lambda: None)
    assert "error" in apps.open_app(name)


def test_the_handler_opens_a_url_case_insensitively(monkeypatch, opened):
    monkeypatch.setattr(apps, "require_enabled", lambda: None)
    apps.open_app("HTTPS://example.com/")
    assert opened == ["HTTPS://example.com/"]
