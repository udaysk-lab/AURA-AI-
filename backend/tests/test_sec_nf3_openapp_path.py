"""NF3: open_app with a file or folder path is a path target too, so floors and path rules apply."""

from __future__ import annotations

import os

import pytest

from app.device import apps
from app.permissions import gate, match, rules
from app.permissions.catalogue import CATALOGUE
from tests.sec_world import build


@pytest.fixture
def w(tmp_path, monkeypatch):
    world = build(tmp_path, monkeypatch, mode="skip")
    fin = tmp_path / "home" / "work" / "finance"
    fin.mkdir()
    (fin / "salary.txt").write_text("salary")
    (tmp_path / "home" / "work" / "notes.txt").write_text("hi")
    world.finance = str(fin)
    world.opened = []
    monkeypatch.setattr(apps, "_open_target", lambda target, label: world.opened.append(target) or {"message": label})
    yield world
    world.db.close()
    world.engine.dispose()


def decide(w, name):
    return gate.decide(w.db, w.user, "open_app", {"name": name}, gate.build_context(w.db, w.user, "chat"))


def rule(w, effect):
    rules.add(w.db, w.user, effect=effect, system="device-control", tool=None, target_kind="path",
              pattern=w.finance + "/**", actor="user")
    w.db.commit()


def test_a_path_is_extracted_as_a_path_target():
    t = match.extract(CATALOGUE["open_app"], {"name": "~/work/finance/salary.txt"})
    assert [x.kind for x in t] == ["path"] and t[0].value.endswith("salary.txt")
    assert [x.kind for x in match.extract(CATALOGUE["open_app"], {"name": "Spotify"})] == ["app"]
    assert [x.kind for x in match.extract(CATALOGUE["open_app"], {"name": "Visual Studio Code"})] == ["app"]


@pytest.mark.parametrize("name", ["~/work/finance/salary.txt", "~/work/finance", "$HOME/work/finance/salary.txt",
                                  "~/work/../work/finance/salary.txt"])
def test_a_block_rule_refuses_the_file(w, name):
    rule(w, "block")
    d = decide(w, name)
    assert d.kind == "deny" and d.reason_code == "RULE_BLOCK", (name, d.kind, d.reason_code)
    assert not w.opened


def test_an_ask_rule_asks(w):
    rule(w, "ask")
    d = decide(w, "~/work/finance/salary.txt")
    assert d.kind == "ask" and d.reason_code == "RULE_ASK"


def test_a_relative_name_that_exists_where_aura_runs_is_a_path(w):
    d = decide(w, ".env")  # the server's working directory holds AURA's own .env
    assert d.kind == "deny" and d.reason_code == "FLOOR_SELF_PATH", (d.kind, d.reason_code)


@pytest.mark.parametrize("name", ["~/.aura/aura.env", "~/.aura", "~/code/aura/backend/.env"])
def test_aura_s_own_files_cannot_be_opened(w, name):
    d = decide(w, name)
    assert d.kind == "deny" and d.reason_code == "FLOOR_SELF_PATH", (name, d.kind, d.reason_code)


def test_a_file_outside_the_connected_folders_needs_the_folder_card(tmp_path, monkeypatch):
    w = build(tmp_path, monkeypatch, mode="skip", connect_home=False)
    (tmp_path / "home" / "other").mkdir()
    (tmp_path / "home" / "other" / "x.txt").write_text("x")
    d = decide(w, "~/other/x.txt")
    assert d.kind == "ask" and d.card == "folder_access", (d.kind, d.reason_code)
    w.db.close()
    w.engine.dispose()


def test_an_ordinary_document_and_an_app_name_still_open_in_skip(w):
    assert decide(w, "~/work/notes.txt").kind == "allow"
    assert decide(w, "Spotify").kind == "allow"


def test_a_rule_that_blocks_an_app_still_blocks_the_name(w):
    rules.add(w.db, w.user, effect="block", system="device-control", tool="open_app", target_kind="app",
              pattern="spotify", actor="user")
    w.db.commit()
    d = decide(w, "Spotify")
    assert d.kind == "deny" and d.reason_code == "RULE_BLOCK"
