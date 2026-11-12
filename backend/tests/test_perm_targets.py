"""Recipients, URLs, app names and target extraction (spec 10 sections 5.3-5.5)."""

from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_aura.db")
os.environ.setdefault("LLM_PROVIDER", "mock")

import pytest  # noqa: E402

from app.device import appnames  # noqa: E402
from app.permissions import match  # noqa: E402
from app.permissions.catalogue import CATALOGUE  # noqa: E402
from app.permissions.match import BadArgs, PatternError, RuleSpec  # noqa: E402


def rule(effect, kind, pattern, id_="r"):
    return RuleSpec(id_, effect, kind, pattern)


def targets(tool, args, **kw):
    return match.extract(CATALOGUE[tool], args, **kw)


def test_recipient_exact_and_domain():
    t = targets("send_email", {"to": "Alice <Alice@Example.COM>"})
    assert [x.value for x in t] == ["alice@example.com"]
    assert match.rule_matches_any(rule("ask", "recipient", "alice@example.com"), t)
    assert match.rule_matches_any(rule("ask", "recipient", "@example.com"), t)
    assert not match.rule_matches_any(rule("ask", "recipient", "bob@example.com"), t)
    assert match.normalise_pattern("recipient", "@Example.com", "ask") == "@example.com"


def test_domain_not_subdomain_mail():
    t = targets("send_email", {"to": "x@mail.example.com"})
    assert not match.rule_matches_any(rule("ask", "recipient", "@example.com"), t)
    assert match.allow_cover([rule("allow", "recipient", "@example.com")], t) is None
    assert match.rule_matches_any(rule("ask", "recipient", "@mail.example.com"), t)


def test_all_recipients_needed():
    t = targets("send_email", {"to": "a@x.com, b@y.org"})
    assert len(t) == 2
    only_a = [rule("allow", "recipient", "a@x.com")]
    assert match.allow_cover(only_a, t) is None
    both = only_a + [rule("allow", "recipient", "@y.org", "r2")]
    assert [r.id for r in match.allow_cover(both, t)] == ["r", "r2"]
    # block / ask need only one
    assert match.first_match("block", [rule("block", "recipient", "b@y.org")], t) is not None
    # list form, create_event, empty cases
    assert len(targets("create_event", {"title": "t", "attendees": ["a@x.com", "b@y.org"]})) == 2
    assert targets("create_event", {"title": "t"}) == ()
    with pytest.raises(BadArgs):
        targets("send_email", {"to": ""})
    with pytest.raises(BadArgs):
        targets("send_email", {"to": "not an address"})
    # a tool-wide allow covers everything
    assert match.allow_cover([rule("allow", "", "")], t) is not None


@pytest.mark.parametrize(
    "pattern, url, expected",
    [
        ("example.com", "https://example.com/a", True),
        ("example.com", "http://www.example.com:8080/", True),
        ("example.com", "https://a.b.example.com", True),
        ("example.com", "https://notexample.com", False),
        ("example.com", "https://example.com.evil.org", False),
        ("https://example.com", "http://example.com", False),
        ("https://example.com", "https://sub.example.com", True),
        ("1.2.3.4", "http://1.2.3.4/", True),
        ("2.3.4", "http://9.1.2.3.4/", False),
        ("1.2.3.4", "http://[::1]/", False),
    ],
)
def test_url_subdomains(pattern, url, expected):
    t = targets("fetch_url", {"url": url})
    assert match.rule_matches_any(rule("ask", "domain", pattern), t) is expected


def test_url_idna_and_normalisation():
    t = targets("fetch_url", {"url": "https://Bücher.example./x"})
    assert t[0].value == "xn--bcher-kva.example"
    assert match.normalise_pattern("domain", "bücher.example", "ask") == "xn--bcher-kva.example"
    assert match.rule_matches_any(rule("ask", "domain", "xn--bcher-kva.example"), t)
    assert targets("fetch_url", {"url": "http://example.com.:80"})[0].value == "example.com"
    for bad in ("ftp://example.com", "file:///etc/passwd", "", "http://", "example.com"):
        with pytest.raises(BadArgs):
            targets("fetch_url", {"url": bad})
    with pytest.raises(PatternError):
        match.normalise_pattern("domain", "*.example.com", "ask")


@pytest.mark.parametrize(
    "name, expected",
    [
        ("Google Chrome", "chrome"), ("chrome.exe", "chrome"), ("com.google.Chrome", "chrome"),
        ("google-chrome", "chrome"), ("/Applications/Google Chrome.app", "chrome"),
        ("C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe", "chrome"),
        ("msedge.exe", "edge"), ("Microsoft Edge", "edge"),
        ("Code.exe", "vscode"), ("Visual Studio Code", "vscode"), ("com.microsoft.VSCode", "vscode"),
        ("wt.exe", "windows-terminal"), ("iTerm2", "iterm2"), ("com.apple.Terminal", "terminal"),
        ("org.mozilla.firefox", "firefox"), ("com.apple.dt.Xcode", "xcode"),
        ("firefox.desktop", "firefox"), ("1Password 7", "1password-7"), ("Notepad", "notepad"),
        ("Node.js", "node-js"), ("!!!", ""),
    ],
)
def test_app_aliases_cross_platform(name, expected):
    assert appnames.canonical(name) == expected


def test_app_classes_and_targets():
    assert appnames.is_terminal("Windows Terminal") and appnames.is_ide("Code.exe")
    assert appnames.is_browser("Google Chrome") and not appnames.is_browser("notepad")
    t = targets("open_app", {"name": "Google Chrome.app"})
    assert t[0].value == "chrome"
    assert match.rule_matches_any(rule("block", "app", "chrome"), t)
    assert match.normalise_pattern("app", "Google Chrome", "block") == "chrome"
    assert match.extract(CATALOGUE["take_screenshot"], {}, frontmost="Code.exe")[0].value == "vscode"
    assert match.extract(CATALOGUE["take_screenshot"], {}) == ()
    with pytest.raises(BadArgs):
        targets("open_app", {"name": "???"})


def test_pid_target_is_floor_only():
    t = targets("kill_process", {"pid": 4242}, pid_app=lambda pid: "Spotify.exe")
    assert [(x.kind, x.value, x.matchable) for x in t] == [("pid", "4242", False), ("app", "spotify", True)]
    assert match.rule_matches_any(rule("block", "app", "spotify"), t)
    with pytest.raises(BadArgs):
        targets("kill_process", {"pid": "abc"})
    with pytest.raises(BadArgs):
        targets("kill_process", {"pid": True})


def test_rule_kind_not_among_tool_targets_is_skipped():
    t = targets("send_email", {"to": "a@x.com"})
    assert not match.rule_applies_to(rule("block", "path", "/tmp/**"), t)
    assert match.first_match("block", [rule("block", "path", "/tmp/**")], t) is None
    assert match.allow_cover([rule("allow", "path", "/tmp/**")], t) is None
    assert match.rule_applies_to(rule("ask", "", ""), t)
    assert not match.rule_applies_to(rule("ask", "", "stray"), t)
    assert match.allow_cover([rule("allow", "", "")], ()) is not None  # tool without targets: tool-wide only
    assert match.allow_cover([rule("allow", "recipient", "")], targets("send_email", {"to": "a@x.com"})) is not None
