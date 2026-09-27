"""Suite-wide fixtures.

Deliberately imports nothing from `app` at module level: every test module sets
DATABASE_URL and friends with os.environ.setdefault *before* importing the app,
and importing it here first would fix the configuration before they can.
"""

from __future__ import annotations

import sys

import pytest


@pytest.fixture(autouse=True)
def _fresh_rate_limits():
    """Give every test its own rate-limit window.

    The limiter keeps its counters in the process, and every TestClient request
    comes from the same address. Across a whole-suite run the anonymous calls
    (sign-in attempts, OAuth callbacks) added up past the 20-per-minute
    anonymous limit, so whichever test happened to make the 21st call got a 429
    instead of the response it was checking — which is why
    test_callback_reports_a_cancelled_consent only failed when the password
    tests ran first. Tests that exercise the limiter build their own buckets.
    """
    middleware = sys.modules.get("app.middleware")
    if middleware is not None:
        middleware._buckets = None
    yield
