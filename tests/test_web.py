"""web/ フロントの試験（issue #15）。

バニラ JS は JS ランタイムを持たない（spec §9: npm 不使用）ため、ここでは Python 側で
「app が web/ を実配信する」ことと「app.js が必須の結線を持ち、ゲームロジックを持たない」
ことを構造検証する。2ブラウザでの即時反映・リロード復帰は #16 E2E / 手動で担保する。
"""

from __future__ import annotations

import itertools
import json

import pytest
from fastapi.testclient import TestClient

from lUNO.server.app import WEB_DIR, create_app
from lUNO.server.session import Session

APP_JS = (WEB_DIR / "app.js").read_text(encoding="utf-8")
INDEX = (WEB_DIR / "index.html").read_text(encoding="utf-8")


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


def deterministic_client(seed: int = 1) -> TestClient:
    counter = itertools.count(1)
    session = Session(seed=seed, token_factory=lambda: f"tok{next(counter)}")
    return TestClient(create_app(session=session))


# --- 実配信 -----------------------------------------------------------------


def test_serves_index_app_js_style(client: TestClient):
    assert client.get("/").status_code == 200
    assert client.get("/app.js").status_code == 200
    assert client.get("/style.css").status_code == 200


def test_index_references_assets():
    assert "/app.js" in INDEX
    assert "/style.css" in INDEX


# --- 必須の結線（薄いフロントの契約） --------------------------------------


def test_app_js_connects_websocket_to_ws():
    assert "WebSocket" in APP_JS
    assert "/ws" in APP_JS


def test_app_js_persists_reconnect_token_in_localstorage():
    assert "localStorage" in APP_JS
    assert "setItem" in APP_JS
    assert "getItem" in APP_JS


def test_app_js_renders_playerview_and_uses_card_images():
    assert "function render" in APP_JS
    assert "/cards/" in APP_JS  # カード画像は static PNG を参照（§7）
    assert "image_key" in APP_JS
    assert "your_hand" in APP_JS


def test_app_js_sends_actions_as_json():
    assert "JSON.stringify" in APP_JS
    for action_type in ("play", "draw", "choose_color", "reset"):
        assert action_type in APP_JS


# --- ロジックをフロントに持たない（送信＋描画のみ） -------------------------


def test_app_js_has_no_engine_logic():
    """プレイ可否・シャッフル・累積等のルール/エンジン実装をフロントに持たない。"""
    forbidden = ["can_play", "shuffle", "108", "pending_draw +", "new_game"]
    for token in forbidden:
        assert token not in APP_JS, f"フロントにロジックが混入: {token!r}"


def test_app_js_play_uses_card_ids_list():
    """play は card_ids（リスト）で送る（サーバの PlayAction 仕様と一致, #35）。"""
    assert "card_ids" in APP_JS
    assert "card_id:" not in APP_JS  # 旧単数フィールドを送っていない


# --- WS 往復（フロントが送る実ペイロードがサーバと整合するか） ---------------


def _play_payload_from_app_js(view_card_id: int) -> dict:
    """app.js が play クリックで送る形をそのまま再現（プロトコル整合の担保）。"""
    return {"type": "play", "player": "p1", "card_ids": [view_card_id]}


def test_ws_frontend_play_payload_is_accepted_by_server():
    """フロントの play ペイロード形がサーバに受理され、状態がブロードキャストされること。

    Blocker 回帰防止: card_id（旧単数）だと ActionError になる。card_ids で通る。
    """
    client = deterministic_client()
    with client.websocket_connect("/ws") as ws1:
        welcome = ws1.receive_json()
        assert welcome["type"] == "welcome"
        with client.websocket_connect("/ws") as ws2:
            ws2.receive_json()
            # welcome.view.your_hand から出せる札を探し、標準 can_play に通る1枚を出す
            top = welcome["view"]["top_of_pile"]
            playable = _find_playable(welcome["view"]["your_hand"], top)
            if playable is None:
                pytest.skip("この seed では初手に出せる札が無い")
            ws1.send_text(json.dumps(_play_payload_from_app_js(playable["id"])))
            msg = ws1.receive_json()
            assert msg["type"] == "state"  # error でなく state が返る


def test_ws_old_card_id_payload_would_error():
    """旧 card_id 形はサーバに弾かれる（Blocker の存在を明示的に固定）。"""
    client = deterministic_client()
    with client.websocket_connect("/ws") as ws1:
        w = ws1.receive_json()
        cid = w["view"]["your_hand"][0]["id"]
        ws1.send_text(json.dumps({"type": "play", "player": "p1", "card_id": cid}))
        assert ws1.receive_json()["type"] == "error"


def test_ws_reconnect_restores_hand_via_token():
    """完成条件2: ?token= 再接続で手札が復元される（実挙動）。"""
    client = deterministic_client()
    with client.websocket_connect("/ws") as ws:
        w = ws.receive_json()
        token = w["token"]
        ws.send_text(json.dumps({"type": "draw", "player": "p1"}))
        ws.receive_json()
    with client.websocket_connect(f"/ws?token={token}") as ws2:
        w2 = ws2.receive_json()
        assert w2["player_id"] == "p1"
        assert len(w2["view"]["your_hand"]) == 8


def _find_playable(hand, top):
    """標準 can_play 相当（テスト補助）。色/記号一致 or ワイルド。"""
    for c in hand:
        if c["color"] is None:  # wild
            return c
        if top is not None and (c["color"] == top["color"] or c["symbol"] == top["symbol"]):
            return c
    return None
