"""E2E スモーク（issue #16 完了条件3: 標準UNO 一戦が端から端まで完了）。

実サーバ（uvicorn）は起動せず、FastAPI TestClient の WebSocket で2接続を張り、貪欲
クライアント（出せる札があれば出し、無ければ引く／色選択は赤）で自己対戦させ、上がり
（winner）まで到達することを engine+rules+session+app の結合で担保する。
"""

from __future__ import annotations

import itertools
import json

from fastapi.testclient import TestClient

from lUNO.server.app import create_app
from lUNO.server.session import Session


def _make_client(seed: int) -> TestClient:
    counter = itertools.count(1)
    session = Session(seed=seed, token_factory=lambda: f"tok{next(counter)}")
    return TestClient(create_app(session=session))


def _play_full_game(seed: int, max_steps: int = 4000) -> dict:
    """2接続で貪欲自己対戦し、勝者が出たときの最終 view を返す。"""
    client = _make_client(seed)
    with client.websocket_connect("/ws") as ws1, client.websocket_connect("/ws") as ws2:
        socks = {"p1": ws1, "p2": ws2}
        views = {"p1": ws1.receive_json()["view"], "p2": ws2.receive_json()["view"]}

        def broadcast_into(views: dict) -> None:
            # 成功 Action は両接続へ state がブロードキャストされる。想定外
            # （error 等）なら早期失敗させる（偽の無限ブロックを避ける）。
            for pid, ws in socks.items():
                m = ws.receive_json()
                assert m["type"] == "state", f"想定外メッセージ: {m}"
                views[pid] = m["view"]

        for _ in range(max_steps):
            cur = views["p1"]["current_player"]
            if views["p1"]["winner"] is not None:
                return views["p1"]
            sock = socks[cur]
            view = views[cur]
            allowed = view["awaiting"].get(cur, [])

            if "choose_color" in allowed:
                sock.send_text(json.dumps({"type": "choose_color", "player": cur, "color": "red"}))
                broadcast_into(views)
                continue

            # 出せる札を1枚ずつ試す（可否はサーバが判定。error は自分にだけ返る）
            played = False
            for card in view["your_hand"]:
                play = {"type": "play", "player": cur, "card_ids": [card["id"]]}
                sock.send_text(json.dumps(play))
                msg = sock.receive_json()
                if msg["type"] == "state":
                    views[cur] = msg["view"]
                    other = "p2" if cur == "p1" else "p1"
                    views[other] = socks[other].receive_json()["view"]
                    played = True
                    break
                # error: 次の札を試す（この失敗は自分にだけ届く）
            if not played:
                sock.send_text(json.dumps({"type": "draw", "player": cur}))
                broadcast_into(views)

    raise AssertionError(f"{max_steps} 手で決着しなかった（seed={seed}）")


def test_e2e_standard_game_completes():
    final = _play_full_game(seed=20260705)
    assert final["winner"] in ("p1", "p2")
    # 勝者は手札 0（相手の視界からも枚数で確認できる）
    assert final["hand_counts"][final["winner"]] == 0


def test_e2e_deterministic_same_seed_same_winner():
    a = _play_full_game(seed=7)
    b = _play_full_game(seed=7)
    assert a["winner"] == b["winner"]
