"""More places that run things by themselves, or decide which program opens a file, are protected."""

from __future__ import annotations

import os

import pytest

from app.config import ALWAYS_PROTECTED, settings
from app.device import files
from tests.sec_world import build, run

ADDED = (
    "~/.local/share/flatpak/exports", "~/.xsession", "~/.config/plasma-workspace", "~/.config/autostart-scripts",
    "~/.kde/Autostart", "~/.kde4/Autostart", "~/.config/i3", "~/.i3", "~/.config/sway", "~/.config/hypr",
    "~/.config/openbox", "~/.config/lxsession", "~/.config/awesome", "~/.config/bspwm", "~/.config/sxhkd",
    "~/.xbindkeysrc",
)


@pytest.fixture
def w(tmp_path, monkeypatch):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    world = build(tmp_path, monkeypatch, mode="skip", connect_home=True)
    yield world
    world.db.close()
    world.engine.dispose()


def test_the_added_places_are_on_the_list(w):
    for entry in ADDED:
        assert entry in ALWAYS_PROTECTED
        assert os.path.abspath(os.path.expanduser(entry)) in settings.device_protected_path_list, entry


def test_the_xdg_forms_follow_the_variables(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xc"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xd"))
    listed = settings.device_protected_path_list
    for rel in ("i3", "sway", "hypr", "openbox", "lxsession", "awesome", "bspwm", "sxhkd", "plasma-workspace",
                "autostart-scripts"):
        assert os.path.abspath(str(tmp_path / "xc" / rel)) in listed, rel
    assert os.path.abspath(str(tmp_path / "xd" / "flatpak" / "exports")) in listed


@pytest.mark.parametrize("path", [
    "~/.config/gnome-mimeapps.list",
    "~/.config/kde-mimeapps.list",
    "~/.local/share/flatpak/exports/share/applications/x.desktop",
    "~/.xsession",
    "~/.config/plasma-workspace/env/x.sh",
    "~/.config/autostart-scripts/x.sh",
    "~/.kde/Autostart/x.sh",
    "~/.kde4/Autostart/x.sh",
    "~/.config/i3/config",
    "~/.i3/config",
    "~/.config/sway/config",
    "~/.config/hypr/hyprland.conf",
    "~/.config/openbox/autostart",
    "~/.config/lxsession/LXDE/autostart",
    "~/.config/awesome/rc.lua",
    "~/.config/bspwm/bspwmrc",
    "~/.config/sxhkd/sxhkdrc",
    "~/.xbindkeysrc",
    # the name rule: anywhere at all
    "~/work/mimeapps.list",
    "~/work/deep/er/foo-mimeapps.list",
    "~/.local/share/applications/mimeapps.list",
    "~/work/MIMEAPPS.LIST",
])
def test_these_are_refused_for_writing_and_for_reading(w, path):
    d, _ = run(w, "write_file", {"path": path, "content": "x"})
    assert (d.kind, d.reason_code) == ("deny", "FLOOR_PROTECTED_PATH"), path
    d, _ = run(w, "read_file", {"path": path})
    assert (d.kind, d.reason_code) == ("deny", "FLOOR_PROTECTED_PATH"), path
    assert not os.path.exists(os.path.expanduser(path))


def test_the_file_layer_applies_the_name_rule_too(w):
    from app.permissions import access

    folder = access.FolderInfo("1", w.work, "k", True, "user")
    with access.use(access.Access(folders=(folder,))):
        with pytest.raises(files.PathNotAllowed):
            files.write_text(os.path.join(w.work, "kde-mimeapps.list"), "x")
        with pytest.raises(files.PathNotAllowed):
            files.read_text(os.path.join(w.work, "mimeapps.list"))


@pytest.mark.parametrize("name", ["mimeapps.list", "gnome-mimeapps.list", "x-mimeapps.list", "MimeApps.List"])
def test_the_name_rule_matches(name):
    assert files.protected_name(os.path.join("/any", "place", name))


@pytest.mark.parametrize("name", ["mimeapps.list.bak", "notmimeapps.list", "mimeapps.txt", "my-mimeapps", "i3.txt"])
def test_the_name_rule_does_not_over_match(name):
    assert not files.protected_name(os.path.join("/any", "place", name))


def test_an_ordinary_file_in_the_connected_folder_still_works(w):
    d, out = run(w, "write_file", {"path": os.path.join(w.work, "notes.txt"), "content": "x"})
    assert d.kind == "allow" and "error" not in out
