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


class AsPeer:
    """Make every request arrive from `host`, as a real server would report it.

    The ASGI test client reports its peer as "testclient" — neither this
    computer nor an address on the network. That's fine for a hosted
    deployment, but a local install decides who may do what by exactly that,
    so its tests need to say where a request comes from.
    """

    def __init__(self, app, host: str = "127.0.0.1"):
        self.app = app
        self.host = host

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            scope = {**scope, "client": (self.host, 50000)}
        await self.app(scope, receive, send)


@pytest.fixture(scope="session")
def as_peer():
    """`as_peer(app, "192.168.1.20")` → an app whose requests come from there."""
    return AsPeer


@pytest.fixture
def db_rollback():
    """Run one test inside a transaction that is rolled back afterwards.

    Every session the app opens during the test — request handlers, the chat
    stream's own session, services — joins one outer transaction, and their
    commits become savepoints inside it. Whatever the test wrote is gone when
    it ends, so tests stop depending on the order they run in.

    Opt-in rather than automatic: the older modules manage their own rows,
    and the module-scoped clients they share start the app (and its schema)
    outside any one test. Requests in a test run one at a time, which is what
    lets them share the connection.
    """
    from app.db import SessionLocal, engine

    connection = engine.connect()
    sqlite = engine.dialect.name == "sqlite"
    if sqlite:
        # pysqlite manages transactions itself and gets savepoints wrong: a
        # "savepoint" release then commits for real. Taking that job away from
        # it, and beginning by hand, is SQLAlchemy's documented workaround.
        connection.connection.dbapi_connection.isolation_level = None
    outer = connection.begin()
    if sqlite:
        connection.exec_driver_sql("BEGIN")
    SessionLocal.configure(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield
    finally:
        SessionLocal.configure(bind=engine)
        outer.rollback()
        if sqlite:
            # Don't hand a connection with pysqlite's transaction handling
            # switched off back to the pool.
            connection.invalidate()
        connection.close()
