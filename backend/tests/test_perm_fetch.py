"""The fetch floors (spec 10 section 8.3): fetch_url never reaches this computer or its network."""

from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_aura.db")
os.environ.setdefault("LLM_PROVIDER", "mock")

import httpx  # noqa: E402
import pytest  # noqa: E402

from app.device import net  # noqa: E402

PUBLIC = "93.184.216.34"


@pytest.fixture
def dns(monkeypatch):
    """A resolver table instead of real DNS; records every lookup."""
    table: dict[str, list[str]] = {}
    calls: list[str] = []

    def fake(host, port):
        calls.append(host)
        if host not in table:
            raise OSError("no such host")
        answer = table[host]
        return list(answer.pop(0)) if answer and isinstance(answer[0], list) else list(answer)

    monkeypatch.setattr(net, "resolve", fake)
    table["__calls__"] = calls  # type: ignore[assignment]
    return table


def serve(handler):
    return httpx.MockTransport(handler)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8765/api/me",
        "http://127.1/",
        "http://[::1]/",
        "http://0.0.0.0/",
        "http://2130706433/",  # 127.0.0.1 as one number
        "http://localhost/",
        "http://LOCALHOST./",
        "http://foo.localhost/",
        "http://printer.local/",
        "http://db.internal/",
    ],
)
def test_loopback_v4_v6_denied(url, dns):
    dns["2130706433"] = ["127.0.0.1"]
    with pytest.raises(net.FetchDenied) as err:
        net.check_url(url)
    assert err.value.code == "FLOOR_PRIVATE_NETWORK"


@pytest.mark.parametrize(
    "ip",
    ["10.0.0.5", "172.16.3.4", "192.168.1.1", "169.254.169.254", "100.64.1.1", "224.0.0.1", "0.0.0.0", "fe80::1", "fc00::1", "ff02::1"],
)
def test_private_and_link_local_denied(ip, dns):
    dns["example.org"] = [ip]
    with pytest.raises(net.FetchDenied):
        net.check_url("https://example.org/")
    with pytest.raises(net.FetchDenied):
        net.check_url(f"http://[{ip}]/" if ":" in ip else f"http://{ip}/")


@pytest.mark.parametrize("ip", ["::ffff:127.0.0.1", "::ffff:10.1.2.3", "2002:7f00:1::1", "64:ff9b::a00:1", "::ffff:169.254.169.254"])
def test_mapped_ipv6_denied(ip, dns):
    dns["example.org"] = [ip]
    with pytest.raises(net.FetchDenied):
        net.check_url("https://example.org/")


def test_one_private_address_among_public_ones_denies(dns):
    dns["example.org"] = [PUBLIC, "10.0.0.1"]
    with pytest.raises(net.FetchDenied):
        net.check_url("https://example.org/")


def test_localhost_names_denied(dns, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "local_allowed_hosts", "localhost,127.0.0.1,::1,mybox.example")
    monkeypatch.setattr(settings, "remote_hostnames", "pc.tailnet.example")
    dns["mybox.example"] = [PUBLIC]
    dns["pc.tailnet.example"] = [PUBLIC]
    for host in ("mybox.example", "pc.tailnet.example", "x.localhost", "localhost"):
        with pytest.raises(net.FetchDenied):
            net.check_url(f"https://{host}/")
    assert not dns["__calls__"], "a refused name must not even be looked up"


def test_redirect_to_private_denied(dns):
    dns["public.example"] = [PUBLIC]
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data/"})

    with pytest.raises(net.FetchDenied):
        net.safe_get("https://public.example/start", transport=serve(handler))
    assert seen == [f"https://{PUBLIC}/start"], "the private hop must never be requested"


def test_redirect_by_name_to_loopback_denied_and_chain_limited(dns):
    dns["a.example"] = [PUBLIC]
    dns["b.example"] = ["127.0.0.1"]

    def to_b(request):
        return httpx.Response(301, headers={"location": "https://b.example/x"})

    with pytest.raises(net.FetchDenied):
        net.safe_get("https://a.example/", transport=serve(to_b))

    def loop(request):
        return httpx.Response(302, headers={"location": "/again"})

    with pytest.raises(net.FetchDenied) as err:
        net.safe_get("https://a.example/", transport=serve(loop))
    assert "redirect" in err.value.reason.lower()


def test_rebinding_pinned(dns):
    """The name answers public to the check and private afterwards: the connection still
    goes to the checked public address, and the second answer is never used."""
    dns["rebind.example"] = [[PUBLIC], ["127.0.0.1"]]
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, text="hello")

    out = net.safe_get("https://rebind.example/page?q=1", transport=serve(handler))
    assert out == {"url": "https://rebind.example/page?q=1", "status": 200, "content": "hello"}
    assert len(requests) == 1
    req = requests[0]
    assert req.url.host == PUBLIC
    assert req.headers["host"] == "rebind.example"
    assert req.extensions["sni_hostname"] == "rebind.example"
    assert dns["__calls__"] == ["rebind.example"], "resolved once per hop"


def test_redirect_to_a_public_page_is_followed_and_truncated(dns):
    dns["a.example"] = [PUBLIC]
    dns["b.example"] = ["93.184.216.35"]

    def handler(request):
        if request.headers["host"] == "a.example":
            return httpx.Response(302, headers={"location": "https://b.example/big"})
        return httpx.Response(200, text="x" * 30_000)

    out = net.safe_get("http://a.example/", transport=serve(handler))
    assert out["url"] == "https://b.example/big"
    assert out["content"].endswith("…[truncated]…") and len(out["content"]) < 20_100


def test_scheme_and_credentials(dns):
    for url in ("file:///etc/passwd", "ftp://example.org/", "gopher://example.org/", "http://"):
        with pytest.raises(net.FetchDenied):
            net.check_url(url)


def test_the_tool_reports_a_refusal_not_an_exception(dns):
    from app.agent import tools as tool_registry

    out = tool_registry.REGISTRY["fetch_url"].handler(
        tool_registry.ToolContext(db=None, user=None), {"url": "http://127.0.0.1:8765/api/settings"}
    )
    assert "error" in out and "Refused" in out["error"]
