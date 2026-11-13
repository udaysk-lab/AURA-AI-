"""F12: the self-API floor reads hosts the way the fetch floors do and knows the port AURA is on."""

from __future__ import annotations

import os
import platform

import pytest

from app.device import net
from app.permissions import floors, match
from app.permissions.catalogue import CATALOGUE

POSIX, PWSH, CMD = "posix", "pwsh", "cmd"

HOSTS = [
    "localhost", "localhost.", "LOCALHOST", "Localhost.", "foo.localhost", "127.0.0.1", "127.1", "127.0.1",
    "2130706433", "0x7f000001", "0x7f.0.0.1", "017700000001", "0177.0.0.1", "0", "0.0.0.0", "[::1]",
    "[0:0:0:0:0:0:0:1]", "[::ffff:127.0.0.1]", "[::ffff:7f00:1]", "[::ffff:7f00:0001]", "[0::ffff:127.0.0.1]",
    "[::]", "[::1%25lo]", "127.0.0.2", "127.255.255.254", "myhost", "MyHost.",
]


@pytest.fixture(autouse=True)
def _where(tmp_path, monkeypatch):
    h = tmp_path / "home"
    (h / ".aura").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setenv("USERPROFILE", str(h))
    work = tmp_path / "repo" / "backend"
    work.mkdir(parents=True)
    monkeypatch.chdir(work)
    from app.config import settings

    monkeypatch.setattr(settings, "aura_home", "", raising=False)
    monkeypatch.setattr(settings, "aura_config_file", "", raising=False)
    monkeypatch.setattr(settings, "database_url", "sqlite:///./aura.db", raising=False)
    monkeypatch.setattr(settings, "device_extra_protected_paths", "", raising=False)
    monkeypatch.setattr(settings, "device_command_denylist", "", raising=False)
    monkeypatch.setattr(platform, "node", lambda: "myhost")
    monkeypatch.delenv("AURA_PORT", raising=False)
    monkeypatch.setattr(net, "_BOUND_PORTS", set(), raising=False)
    monkeypatch.setattr("sys.argv", ["pytest"])
    yield


def floor_of(command, dialect=POSIX):
    policy = CATALOGUE["run_command"]
    targets = match.extract(policy, {"command": command}, dialect=dialect)
    return floors.check(None, None, policy, targets)


@pytest.mark.parametrize("host", HOSTS)
@pytest.mark.parametrize("template,dialect", [
    ("curl http://{h}:8765/api/settings", POSIX),
    ("curl -s 'http://{h}/api/auth/local'", POSIX),
    ("wget -qO- http://{h}:8765/", POSIX),
    ("python3 -c \"import urllib.request as u; u.urlopen('http://{h}:8765/api/x')\"", POSIX),
    ("Invoke-WebRequest http://{h}:8765/api/settings", PWSH),
    ("irm 'http://{h}/api/me'", PWSH),
    ("curl.exe http://{h}:8765/api/settings", CMD),
])
def test_every_spelling_of_this_computer_is_refused(host, template, dialect):
    command = template.format(h=host)
    assert floor_of(command, dialect).code == "FLOOR_SELF_API", command


@pytest.mark.parametrize("command,dialect", [
    ("nc 2130706433 8765", POSIX), ("nc 0x7f000001 8765", POSIX), ("ncat 127.1 8765", POSIX),
    ("telnet localhost. 8765", POSIX), ("curl --resolve x:8765:127.1 http://x:8765/", POSIX),
])
def test_other_client_forms(command, dialect):
    assert floor_of(command, dialect).code == "FLOOR_SELF_API", command


def test_the_port_aura_is_really_on_counts(monkeypatch):
    # not 8765: AURA_PORT says so
    monkeypatch.setenv("AURA_PORT", "9123")
    assert floor_of("curl http://myhost:9123/api/x").code == "FLOOR_SELF_API"
    assert 9123 in net.bound_ports()
    # a from-source server started as `uvicorn app.main:app --port 7001`
    monkeypatch.delenv("AURA_PORT")
    monkeypatch.setattr("sys.argv", ["uvicorn", "app.main:app", "--port", "7001"])
    assert floor_of("curl http://myhost:7001/api/x").code == "FLOOR_SELF_API"
    monkeypatch.setattr("sys.argv", ["uvicorn", "app.main:app", "--port=7002"])
    assert floor_of("curl http://myhost:7002/api/x").code == "FLOOR_SELF_API"
    # the port a request really arrived on
    monkeypatch.setattr("sys.argv", ["pytest"])
    net.note_bound_port(("127.0.0.1", 5544))
    assert floor_of("curl http://myhost:5544/api/x").code == "FLOOR_SELF_API"
    # a private address (this computer's own LAN address, when AURA listens on the network) on AURA's port
    assert floor_of("curl http://192.168.1.50:5544/api/x").code == "FLOOR_SELF_API"
    assert floor_of("curl http://192.168.1.50:5545/api/x").code == ""      # another port: another service
    assert floor_of("curl http://example.com:5544/api/x").code == ""       # a public host is not this computer


def test_ordinary_commands_are_not_self_api():
    for command in ("curl https://example.com/api/items", "curl http://192.168.1.50/status", "git fetch origin",
                    "ping localhost", "ls -la", "pip install requests", "curl http://example.com:8765/",
                    "curl https://1.1.1.1/", "echo 127.0.0.1 > hosts.txt", "git clone https://github.com/a/b"):
        assert not floor_of(command).deny, command


def test_hosts_are_read_by_the_same_code_as_the_fetch_floors():
    """net.is_this_computer is what both floors use: whatever the fetch floor refuses as 'this computer'
    by its spelling, the shell floor refuses too."""
    for host in HOSTS:
        bare = host.strip("[]")
        assert net.is_this_computer(bare), host
    for host in ("example.com", "93.184.216.34", "192.168.1.5", "10.0.0.1", "[2606:4700::1111]", "1.1.1.1", ""):
        assert not net.is_this_computer(host.strip("[]")), host
    for url in ("http://2130706433/", "http://[::ffff:7f00:1]/", "http://localhost./", "http://0x7f000001/",
                "http://127.1/", "http://[::]/", "http://0/"):
        with pytest.raises(net.FetchDenied):
            net.check_url(url)
        assert floor_of(f"curl {url}").code == "FLOOR_SELF_API", url
    assert os.name  # silence unused import on some platforms
