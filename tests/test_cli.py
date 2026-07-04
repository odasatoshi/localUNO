"""cli.py の試験（issue #16: 画像差分生成 → サーバ起動の結線）。

実サーバ起動は monkeypatch で差し替え、結線（生成→起動、フラグ受け渡し）を検証する。
差分生成の冪等性は実 generator で確認する（2回目は 0 枚）。
"""

from __future__ import annotations

import pytest

from lUNO import cli


def test_version_exits_zero(capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["--version"])
    assert e.value.code == 0
    assert "luno" in capsys.readouterr().out


def test_no_serve_generates_but_does_not_start_server(monkeypatch):
    called = {"gen": False, "serve": False}

    def fake_generate_cards(*args, **kwargs):
        called["gen"] = True
        return []

    def fake_run(*args, **kwargs):
        called["serve"] = True

    monkeypatch.setattr(cli, "generate_cards", fake_generate_cards)
    monkeypatch.setattr(cli, "run_server", fake_run)

    assert cli.main(["--no-serve"]) == 0
    assert called["gen"] is True
    assert called["serve"] is False  # --no-serve ではサーバを起動しない


def test_main_starts_server_with_host_port(monkeypatch):
    captured = {}

    monkeypatch.setattr(cli, "generate_cards", lambda *a, **k: [])
    monkeypatch.setattr(cli, "run_server", lambda host, port: captured.update(host=host, port=port))

    assert cli.main(["--host", "127.0.0.1", "--port", "9999"]) == 0
    assert captured == {"host": "127.0.0.1", "port": 9999}


def test_regenerate_flag_forwarded(monkeypatch):
    captured = {}

    def fake_generate_cards(regenerate=False):
        captured["regen"] = regenerate
        return []

    monkeypatch.setattr(cli, "generate_cards", fake_generate_cards)
    monkeypatch.setattr(cli, "run_server", lambda *a, **k: None)

    cli.main(["--no-serve", "--regenerate"])
    assert captured["regen"] is True


def test_generate_diff_is_idempotent():
    """一度生成すれば、2回目の差分生成は 0 枚（spec §7 差分生成）。"""
    cli.generate()  # 未生成分があれば生成
    assert cli.generate() == 0  # 2回目は差分なし
