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


@pytest.fixture(autouse=True)
def _private_aura_home(tmp_path_factory, monkeypatch):
    """Keep AURA's home out of the real one: the permission log's signing key, its head file and
    the generated secrets are written there, and a test run must not leave any in ~/.aura."""
    config = sys.modules.get("app.config")
    if config is not None:
        monkeypatch.setattr(config.settings, "aura_home", str(tmp_path_factory.mktemp("aura-home")), raising=False)
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


@pytest.fixture(autouse=True)
def _test_peers_are_ordinary_programs(request, monkeypatch):
    """The ASGI test client's peer ("127.0.0.1", 50000) is not a real socket, so the process lookup cannot
    find an owner and answers "unknown", which permission-changing routes refuse (F3). Tests that are not
    about that lookup see an ordinary program instead; a test marked `real_peer` gets the real answer."""
    if request.node.get_closest_marker("real_peer"):
        yield
        return
    deps = sys.modules.get("app.deps")
    if deps is None:
        yield
        return
    real = deps._classify

    def classify(req):
        kind = real(req)
        return "other" if kind == "unknown" else kind

    monkeypatch.setattr(deps, "classify_peer", classify)
    yield


def pytest_collection_modifyitems(config, items):
    """Run one slice of the suite when AURA_TEST_SHARD is "i/n" (CI on slow runners).

    The suite is cut into units, dealt out by size (largest first, each to the slice with the fewest tests so far),
    so the slices come out close to even. A unit is a whole test module, so module-scoped fixtures behave as usual;
    a module larger than a quarter of a slice that uses no module- or class-scoped fixture is cut into pieces of a
    quarter of a slice, because one such module would otherwise decide how long a slice takes. Every slice collects
    the same tests and so computes the same split.
    """
    import collections
    import math
    import os

    spec = os.environ.get("AURA_TEST_SHARD", "").strip()
    if not spec or spec == "1/1":
        return
    index, _, total = spec.partition("/")
    index, total = int(index), int(total)
    if not 1 <= index <= total:
        raise pytest.UsageError(f"AURA_TEST_SHARD={spec!r} is not i/n with 1 <= i <= n")

    by_module: dict[str, list] = collections.defaultdict(list)
    for item in items:
        by_module[item.nodeid.split("::", 1)[0].replace("\\", "/")].append(item)

    def shared_state(module_items) -> bool:
        for item in module_items:
            info = getattr(item, "_fixtureinfo", None)
            for defs in (info.name2fixturedefs.values() if info is not None else ()):
                if any(getattr(d, "scope", "function") in ("module", "class", "package") for d in defs):
                    return True
        return False

    piece = max(1, math.ceil(len(items) / total / 4))
    units: list[tuple[str, int, list]] = []
    for name, module_items in by_module.items():
        if len(module_items) > piece and not shared_state(module_items):
            for k in range(0, len(module_items), piece):
                units.append((name, k, module_items[k:k + piece]))
        else:
            units.append((name, 0, module_items))
    load = [0] * total
    keep_ids: set[int] = set()
    for name, k, unit in sorted(units, key=lambda u: (-len(u[2]), u[0], u[1])):
        target = min(range(total), key=lambda i: (load[i], i))
        load[target] += len(unit)
        if target == index - 1:
            keep_ids.update(id(item) for item in unit)
    keep = [item for item in items if id(item) in keep_ids]
    drop = [item for item in items if id(item) not in keep_ids]
    if drop:
        config.hook.pytest_deselected(items=drop)
        items[:] = keep


@pytest.fixture
def wall_on(monkeypatch):
    """Force computer protection on, so commands follow the mode and the rules (the later-package path).
    Nothing in the product turns it on yet; tests of the mode and rule machinery use a command to reach it."""
    from app.permissions import wall

    monkeypatch.setattr(wall, "active", lambda: True)
