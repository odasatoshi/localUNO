"""server/app.py の試験（issue #14 の完了条件を担保）。

- WS で Action を送ると PlayerView が返る
- 静的ファイル（index.html・カード画像）が配信される
"""

from __future__ import annotations

import itertools

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from lUNO.server.app import create_app
from lUNO.server.session import Session


def make_client(tmp_path, seed: int = 1) -> TestClient:
    counter = itertools.count(1)
    session = Session(seed=seed, token_factory=lambda: f"tok{next(counter)}")
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("<h1>local-UNO</h1>", encoding="utf-8")
    # カード画像は web とは別の static/cards 相当ディレクトリから /cards で配信される（§7）
    cards = tmp_path / "static" / "cards"
    cards.mkdir(parents=True)
    (cards / "red_5.png").write_bytes(b"\x89PNG\r\n")
    return TestClient(create_app(session=session, web_dir=web, cards_dir=cards))


# --- 静的配信 ---------------------------------------------------------------


def test_serves_index_html(tmp_path):
    client = make_client(tmp_path)
    r = client.get("/")
    assert r.status_code == 200
    assert "local-UNO" in r.text


def test_serves_card_image(tmp_path):
    client = make_client(tmp_path)
    r = client.get("/cards/red_5.png")
    assert r.status_code == 200
    assert r.content.startswith(b"\x89PNG")


def test_real_cards_dir_is_static_cards():
    """既定のカード配信元が generator の出力先 static/cards と一致すること（§7）。"""
    from lUNO.cards_render.generator import default_output_dir
    from lUNO.server.app import CARDS_DIR

    assert CARDS_DIR == default_output_dir()


# --- WebSocket: Action → PlayerView ブロードキャスト ------------------------


def test_ws_welcome_and_action_broadcasts_playerview(tmp_path):
    client = make_client(tmp_path)
    with client.websocket_connect("/ws") as ws1:
        w1 = ws1.receive_json()
        assert w1["type"] == "welcome"
        assert w1["player_id"] == "p1"
        assert len(w1["view"]["your_hand"]) == 7

        with client.websocket_connect("/ws") as ws2:
            w2 = ws2.receive_json()
            assert w2["player_id"] == "p2"

            # p1 がドロー → 両者へ state がブロードキャストされる
            ws1.send_text('{"type":"draw","player":"p1"}')
            s1 = ws1.receive_json()
            s2 = ws2.receive_json()
            assert s1["type"] == "state"
            assert len(s1["view"]["your_hand"]) == 8  # p1 は8枚に
            assert s2["view"]["hand_counts"]["p1"] == 8  # p2 には枚数のみ見える
            # 相手手札の中身は p2 の view に入らない（本人のみ）
            assert s2["view"]["you"] == "p2"


def test_ws_welcome_includes_rules_meta(tmp_path):
    """welcome に有効ローカルルールのメタ配列が載る（確認パネル用, #84）。"""
    client = make_client(tmp_path)
    with client.websocket_connect("/ws") as ws1:
        w1 = ws1.receive_json()
        assert w1["type"] == "welcome"
        rules = w1["rules"]
        assert isinstance(rules, list) and rules
        ids = [r["id"] for r in rules]
        assert ids[0] == "standard"  # カタログ順（先頭は standard）
        std = next(r for r in rules if r["id"] == "standard")
        assert std["required"] is True and std["enabled"] is True
        for r in rules:
            assert set(r) == {"id", "name", "section", "description", "required", "enabled"}


def test_ws_welcome_rules_reflect_enabled_subset(tmp_path):
    """Session に enabled_ids を渡すと welcome の enabled フラグに反映される（#84/#85 前提）。"""
    import itertools

    counter = itertools.count(1)
    session = Session(
        seed=1, token_factory=lambda: f"tok{next(counter)}", enabled_ids={"reverse_off"}
    )
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("<h1>local-UNO</h1>", encoding="utf-8")
    cards = tmp_path / "static" / "cards"
    cards.mkdir(parents=True)
    client = TestClient(create_app(session=session, web_dir=web, cards_dir=cards))
    with client.websocket_connect("/ws") as ws1:
        rules = {r["id"]: r for r in ws1.receive_json()["rules"]}
        assert rules["reverse_off"]["enabled"] is True
        assert rules["standard"]["enabled"] is True  # required は常時
        assert rules["uno_call"]["enabled"] is False  # 集合外は無効


def test_ws_new_game_broadcasts_state_with_updated_rules(tmp_path):
    """new_game で両クライアントへ state が飛び、更新後の rules が同梱される（#85）。"""
    client = make_client(tmp_path)
    with client.websocket_connect("/ws") as ws1:
        ws1.receive_json()  # welcome
        with client.websocket_connect("/ws") as ws2:
            ws2.receive_json()  # welcome
            ws1.send_text(
                '{"type":"new_game","player":"p1","enabled_rule_ids":["reverse_off"]}'
            )
            s1 = ws1.receive_json()
            s2 = ws2.receive_json()
            for s in (s1, s2):
                assert s["type"] == "state"
                assert "rules" in s  # new_game 後は rules を同梱
                meta = {r["id"]: r for r in s["rules"]}
                assert meta["reverse_off"]["enabled"] is True
                assert meta["standard"]["enabled"] is True  # required
                assert meta["uno_call"]["enabled"] is False
            assert len(s1["view"]["your_hand"]) == 7  # 再配札


def test_ws_normal_action_broadcast_omits_rules(tmp_path):
    """通常の手番更新（draw 等）では rules を同梱しない（設定パネルの途中操作を消さない, #85）。"""
    client = make_client(tmp_path)
    with client.websocket_connect("/ws") as ws1:
        ws1.receive_json()
        with client.websocket_connect("/ws") as ws2:
            ws2.receive_json()
            ws1.send_text('{"type":"draw","player":"p1"}')
            s1 = ws1.receive_json()
            assert s1["type"] == "state"
            assert "rules" not in s1


def test_ws_new_game_unknown_rule_id_returns_error(tmp_path):
    """未知のルールID の new_game は error を返す（弾く, #85）。"""
    client = make_client(tmp_path)
    with client.websocket_connect("/ws") as ws1:
        ws1.receive_json()
        with client.websocket_connect("/ws") as ws2:
            ws2.receive_json()
            ws1.send_text(
                '{"type":"new_game","player":"p1","enabled_rule_ids":["bogus"]}'
            )
            m = ws1.receive_json()
            assert m["type"] == "error"


def test_ws_third_connection_rejected(tmp_path):
    client = make_client(tmp_path)
    with client.websocket_connect("/ws") as ws1:
        ws1.receive_json()
        with client.websocket_connect("/ws") as ws2:
            ws2.receive_json()
            with client.websocket_connect("/ws") as ws3:
                m = ws3.receive_json()
                assert m["type"] == "error"


def test_ws_illegal_action_returns_error(tmp_path):
    client = make_client(tmp_path)
    with client.websocket_connect("/ws") as ws1:
        ws1.receive_json()
        with client.websocket_connect("/ws") as ws2:
            ws2.receive_json()
            # p2 は手番でないので draw は拒否される
            ws2.send_text('{"type":"draw","player":"p2"}')
            m = ws2.receive_json()
            assert m["type"] == "error"


def test_ws_reconnect_with_token_restores_view(tmp_path):
    client = make_client(tmp_path)
    with client.websocket_connect("/ws") as ws1:
        w1 = ws1.receive_json()
        token = w1["token"]
        ws1.send_text('{"type":"draw","player":"p1"}')
        ws1.receive_json()  # state（p1 は8枚）
    # ws1 切断後、同トークンで再接続 → 手札が復元される
    with client.websocket_connect(f"/ws?token={token}") as ws1b:
        wb = ws1b.receive_json()
        assert wb["type"] == "welcome"
        assert wb["player_id"] == "p1"
        assert len(wb["view"]["your_hand"]) == 8


def test_ws_last_wins_closes_old_connection(tmp_path):
    """同トークンで再接続すると、サーバが旧接続を閉じる（後勝ち, §8）。"""
    client = make_client(tmp_path)
    with client.websocket_connect("/ws") as ws1:
        w1 = ws1.receive_json()
        token = w1["token"]
        with client.websocket_connect(f"/ws?token={token}") as ws1b:
            wb = ws1b.receive_json()
            assert wb["player_id"] == "p1"
            # 旧接続 ws1 はサーバ側から閉じられる
            with pytest.raises(WebSocketDisconnect):
                ws1.receive_json()


def test_ws_reset_broadcasts_fresh_game(tmp_path):
    client = make_client(tmp_path)
    with client.websocket_connect("/ws") as ws1:
        ws1.receive_json()
        with client.websocket_connect("/ws") as ws2:
            ws2.receive_json()
            ws1.send_text('{"type":"draw","player":"p1"}')
            ws1.receive_json()
            ws2.receive_json()
            # p1 がリセット → 両者に初期化された state がブロードキャストされる
            ws1.send_text('{"type":"reset","player":"p1"}')
            s1 = ws1.receive_json()
            s2 = ws2.receive_json()
            assert len(s1["view"]["your_hand"]) == 7
            assert s2["view"]["hand_counts"]["p1"] == 7


def test_ws_rejects_impersonation(tmp_path):
    """p1 のトークンで p2 として行動しようとすると error。"""
    client = make_client(tmp_path)
    with client.websocket_connect("/ws") as ws1:
        ws1.receive_json()
        ws1.send_text('{"type":"draw","player":"p2"}')  # なりすまし
        m = ws1.receive_json()
        assert m["type"] == "error"


def test_create_app_stores_session(tmp_path):
    session = Session(seed=2)
    app = create_app(session=session, web_dir=tmp_path / "w")
    assert app.state.session is session


@pytest.mark.parametrize("path", ["/does-not-exist.js"])
def test_missing_static_returns_404(tmp_path, path):
    client = make_client(tmp_path)
    assert client.get(path).status_code == 404


def test_uvicorn_resolves_a_websocket_impl():
    """uvicorn(ws=auto) が WS プロトコル実装を解決できること（#59 の回帰防止）。

    素の uvicorn には WS 実装が含まれず、実起動時に ``/ws`` が 404 になる。実行時依存に
    ``websockets`` 等が入っていれば ``ws_protocol_class`` が非 None に解決される。
    既存 WS テストは Starlette の TestClient（インプロセス実装）を使うため、この実起動
    ギャップを検知できない。ここで実装導入を担保する。
    """
    from uvicorn.config import Config

    from lUNO.server.app import app

    config = Config(app, ws="auto")
    config.load()
    assert config.ws_protocol_class is not None


def test_run_invokes_uvicorn_with_app_host_port(monkeypatch):
    """run() が uvicorn.run にモジュール app・host・port を渡すこと（実バインドの結線）。"""
    import lUNO.server.app as appmod

    captured = {}
    monkeypatch.setattr(
        "uvicorn.run",
        lambda app, host, port: captured.update(app=app, host=host, port=port),
    )
    appmod.run(host="1.2.3.4", port=5555)
    assert captured["app"] is appmod.app
    assert captured["host"] == "1.2.3.4"
    assert captured["port"] == 5555
