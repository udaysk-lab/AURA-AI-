"""Secret scrubbing for everything task memory and preference memory store (spec 20 section 9.2)."""

from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_aura.db")
os.environ.setdefault("LLM_PROVIDER", "mock")

import pytest  # noqa: E402

from app.services import redaction, vault  # noqa: E402
from tests.perm_helpers import make_session, make_user  # noqa: E402

SECRETS = [
    "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA\nabc\n-----END RSA PRIVATE KEY-----",
    "sk-abcdefghijklmnop1234",
    "sk-ant-api03-abcdefghijklmnop",
    "AKIAABCDEFGHIJKLMNOP",
    "ghp_" + "a1B2c3D4e5" * 4,
    "xoxb-1234567890-abcdefghij",
    "AIza" + "A" * 35,
    "eyJhbGciOiJIUzI1.eyJzdWIiOiIxMjM0.SflKxwRJSMeKKF2QT4",
    "Bearer abcdefghijklmnop1234",
]


@pytest.mark.parametrize("secret", SECRETS)
def test_scrub_patterns(secret):
    out = redaction.scrub(None, "u", f"before {secret} after")
    assert secret not in out and out == "before [redacted] after"


@pytest.mark.parametrize("text, expected", [
    ("password: hunter2 and more", "password: [redacted] and more"),
    ("PASSWD=abc123", "PASSWD=[redacted]"),
    ("my api_key = zzz", "my api_key = [redacted]"),
    ("api-key:foo", "api-key:[redacted]"),
    ("token = {{vault:github}}", "token = {{vault:github}}"),  # a vault placeholder is not a secret
    ("the secret sauce", "the secret sauce"),  # no separator: not a pair
])
def test_key_value_pairs_keep_the_key_name(text, expected):
    assert redaction.scrub(None, "u", text) == expected


def test_long_blobs_only_near_a_key_word():
    hexy = "a" * 40
    b64 = "Ab1+/" * 9
    assert redaction.scrub(None, "u", f"the key is {hexy}") == "the key is [redacted]"
    assert redaction.scrub(None, "u", f"{b64}== is the token") == "[redacted] is the token"
    plain = f"commit {hexy} fixed the build"
    assert redaction.scrub(None, "u", plain) == plain, "a hash with no key word near it is kept"
    assert redaction.scrub(None, "u", "x" * 10) == "x" * 10


def test_scrub_is_idempotent_and_handles_empty():
    once = redaction.scrub(None, "u", "password: hunter2 sk-abcdefghijklmnop1234")
    assert redaction.scrub(None, "u", once) == once
    assert redaction.scrub(None, "u", "") == ""


def test_scrub_json_sensitive_keys():
    out = redaction.scrub_json(None, "u", {
        "to": "a@b.test",
        "password": "hunter2",
        "access_token": "abc",
        "apiKey": "k",
        "Authorization": "Bearer abcdefghijklmnop1234",
        "cookie": "sid=1",
        "pin": "1234",
        "otp": "999999",
        "tokens_used": 12,
        "mapping": "kept",
        "empty_secret": "",
        "nested": {"list": [{"client_secret": "s"}, "password=zzz"]},
        "body": "x" * 500,
    })
    assert out["to"] == "a@b.test" and out["tokens_used"] == 12 and out["mapping"] == "kept"
    for key in ("password", "access_token", "apiKey", "Authorization", "cookie", "pin", "otp"):
        assert out[key] == "[redacted]", key
    assert out["empty_secret"] == ""
    assert out["nested"]["list"][0] == {"client_secret": "[redacted]"}
    assert out["nested"]["list"][1] == "password=[redacted]"
    assert len(out["body"]) == 300 and out["body"].endswith("…")


def test_scrub_json_limits_and_placeholders():
    out = redaction.scrub_json(None, "u", {"password": "{{vault:mail}}", "items": list(range(80))}, max_str=20)
    assert out["password"] == "{{vault:mail}}", "a lone placeholder under a secret key is kept"
    assert len(out["items"]) == 50
    deep: dict = {}
    cur = deep
    for _ in range(12):
        cur["x"] = {}
        cur = cur["x"]
    assert "[redacted]" in str(redaction.scrub_json(None, "u", deep))
    assert redaction.scrub_json(None, "u", (1, "a", None, True)) == [1, "a", None, True]


def test_vault_values_replaced(tmp_path):
    engine, db = make_session(tmp_path)
    user = make_user(db)
    vault.put(db, user.id, "gh_login", "correct-horse-battery")
    out = redaction.scrub(db, user.id, "log in with correct-horse-battery now")
    assert out == "log in with {{vault:gh_login}} now"
    js = redaction.scrub_json(db, user.id, {"text": "pw correct-horse-battery", "n": 1})
    assert js == {"text": "pw {{vault:gh_login}}", "n": 1}
    other = make_user(db, email="other@example.com")
    assert redaction.scrub(db, other.id, "correct-horse-battery") == "correct-horse-battery", "another user's vault is not used"
    db.close()
    engine.dispose()
