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
    for action_type in ("play", "draw", "pass", "choose_color", "reset"):
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


def test_index_has_play_button():
    """複数枚出しをまとめて確定する「出す」ボタンがある（#62）。"""
    assert 'id="play-btn"' in INDEX


def test_app_js_supports_multi_select_play():
    """複数枚出し UI: タップで選択トグルし、選択順に card_ids でまとめて出す（#62）。"""
    # タップ順を送信順として保持するトグル実装を実際に要求する
    assert "toggleSelect" in APP_JS
    assert "state.selected.push" in APP_JS  # 末尾追加＝送信順を保持
    assert "state.selected.indexOf" in APP_JS  # 選択済み判定→トグル off
    assert 'classList.add("selected")' in APP_JS  # 選択ハイライトの結線
    # 「出す」ボタンで選択配列をそのまま card_ids に載せて送る
    assert "play-btn" in APP_JS
    assert "playSelected" in APP_JS
    assert "card_ids: state.selected" in APP_JS
    # クリックで即 1 枚送る旧挙動を残していない（選択→出す に一本化）
    assert "card_ids: [card.id]" not in APP_JS


def test_app_js_shows_selection_order_and_count():
    """選択順の可視化（先頭/末尾/連番バッジ）・枚数表示・空選択の無効化（#63）。"""
    # 順序バッジと先頭/末尾ラベル（roleLabel の返り値リテラルに厳密一致させ、
    # コメント文字列での偽陽性を避ける）
    assert "order-badge" in APP_JS
    assert "roleLabel" in APP_JS
    assert '"先頭=トップ"' in APP_JS  # 単数選択（リード兼トップ）
    assert '"先頭"' in APP_JS  # リード（場に合わせる札）
    assert '"トップ"' in APP_JS  # 出した後の新しい捨て札
    # 「出す（N枚）」の枚数表示
    assert "出す（" in APP_JS
    # 「出す」ボタンの有効/無効を一元管理（自分の番 state.canPlay かつ 1 枚以上）
    assert "updatePlayButton" in APP_JS
    assert "state.canPlay" in APP_JS


def test_index_has_pass_button():
    """ドロー後にパスを送る導線（パスボタン）が UI にある（#64）。"""
    assert 'id="pass-btn"' in INDEX


def test_app_js_pass_button_wired_and_gated_by_awaiting():
    """パスボタンが pass を送り、awaiting に応じて活性制御される（#64）。

    サーバは自主ドロー後に awaiting=[play, pass] にして手番を保持する。この pass を
    送る手段が UI に無いと、引いた札を出せない時に手番を進められず詰む（回帰防止）。
    """
    # クリックで pass アクションを送る結線
    assert 'getElementById("pass-btn")' in APP_JS
    assert '"pass"' in APP_JS
    # awaiting に pass が無ければ無効化（draw/play ボタンと同じ受理集合連動）
    assert 'includes("pass")' in APP_JS


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
            # 完成条件1: 相手画面へ即反映（p1 の1枚出しが p2 の視界に載る）
            s2 = ws2.receive_json()
            assert s2["type"] == "state"
            assert s2["view"]["hand_counts"]["p1"] == 6  # 7 → 6 に反映


def test_ws_old_card_id_payload_would_error():
    """旧 card_id 形はサーバに弾かれる（Blocker の存在を明示的に固定）。"""
    client = deterministic_client()
    with client.websocket_connect("/ws") as ws1:
        w = ws1.receive_json()
        cid = w["view"]["your_hand"][0]["id"]
        ws1.send_text(json.dumps({"type": "play", "player": "p1", "card_id": cid}))
        assert ws1.receive_json()["type"] == "error"


def test_ws_multi_card_play_is_accepted_and_last_becomes_top():
    """複数枚出し（card_ids に複数ID）が WS 経由で受理され、末尾カードがトップになること。

    engine/rules/actions では #35 で対応済みだが、WS(session→app) を通す結合テストが
    欠けていた。ここで「複数 card_ids を送れる」ことに加え、card_ids の順序契約
    （先頭＝リード・末尾＝新トップ, house-rules §2 / multi_play）を固定する。
    """
    # seed を固定して決定的に検証する。群が見つからなければ skip で素通りさせず
    # 失敗させる（deck/ルール退行でカバレッジが全損した場合に表面化させるため）。
    client = deterministic_client(seed=1)
    with client.websocket_connect("/ws") as ws1:
        welcome = ws1.receive_json()
        group = _find_multi_group(
            welcome["view"]["your_hand"], welcome["view"]["top_of_pile"]
        )
        assert group is not None, (
            "seed=1 の初手に複数枚出し可能な群が無い（deck 変更時は seed を見直す）"
        )
        with client.websocket_connect("/ws") as ws2:
            ws2.receive_json()
            ws1.send_text(json.dumps({"type": "play", "player": "p1", "card_ids": group}))
            msg = ws1.receive_json()
            assert msg["type"] == "state"  # error でなく state が返る
            # 順序契約: 末尾に置いたカードが新しい捨て山トップになる
            assert msg["view"]["top_of_pile"]["id"] == group[-1]
            # 相手視界へ即反映: p1 の手札は 7 → 5（2枚出し）
            s2 = ws2.receive_json()
            assert s2["type"] == "state"
            assert s2["view"]["hand_counts"]["p1"] == 7 - len(group)


def test_ws_pass_after_voluntary_draw_advances_turn():
    """自主ドロー後、フロントが送る pass ペイロードで手番が相手へ進む（#64 E2E）。

    パスの導線が無いと、引いた札が場に出せない時に手番を進められず詰む。ここで
    UI が送る ``{type:"pass"}`` がサーバに受理され、手番が相手へ送られることを固定する。
    """
    client = deterministic_client()
    with client.websocket_connect("/ws") as ws1:
        w1 = ws1.receive_json()
        assert w1["view"]["current_player"] == "p1"
        with client.websocket_connect("/ws") as ws2:
            ws2.receive_json()
            # p1 が自主ドロー（1枚）→ 手番保持のまま awaiting に pass が入る
            ws1.send_text(json.dumps({"type": "draw", "player": "p1"}))
            after_draw = ws1.receive_json()
            ws2.receive_json()
            assert after_draw["view"]["current_player"] == "p1"
            assert "pass" in after_draw["view"]["awaiting"]["p1"]
            # UI が送る pass ペイロードで手番が p2 へ進む
            ws1.send_text(json.dumps({"type": "pass", "player": "p1"}))
            passed = ws1.receive_json()
            ws2.receive_json()
            assert passed["type"] == "state"
            assert passed["view"]["current_player"] == "p2"


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


def _find_multi_group(hand, top):
    """先頭が top に合法（非ワイルド）で、同記号の仲間が1枚以上ある群を探す。

    返り値は出す順の card_id リスト ``[先頭, 仲間]``。無ければ None。
    複数枚出しは「全カードが先頭と同記号」（house-rules §2 / multi_play）。
    ワイルド先頭は choose_color を要するためテストでは避ける。
    """
    for lead in hand:
        if lead["color"] is None:  # wild は避ける
            continue
        if top is None or not (
            lead["color"] == top["color"] or lead["symbol"] == top["symbol"]
        ):
            continue
        mates = [c for c in hand if c["id"] != lead["id"] and c["symbol"] == lead["symbol"]]
        if mates:
            return [lead["id"], mates[0]["id"]]
    return None
