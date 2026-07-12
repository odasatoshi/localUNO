"""FastAPI アプリ: web/ 静的配信 + WebSocket エンドポイント（spec.md §6/§9）。

構成:
- ``static/cards/`` を ``/cards`` に静的マウント（generator が出力するカード画像 PNG, §7）。
- ``web/`` を ``/`` に静的マウント（``index.html`` / JS / CSS）。
- WebSocket ``/ws`` で Action を受信 → :class:`~lUNO.server.session.Session` に適用 →
  各接続へ視界フィルタ済み PlayerView をフル送信（差分は送らない, §6）。

サーバ権威（原則1）: クライアントは Action を送るだけ。状態はサーバが保持し PlayerView
のみを配る。多重接続は後勝ちで旧接続を閉じる（§8）。

メッセージ規約:
- server→client: ``{"type":"welcome","token","player_id","view":{...},"rules":[...]}`` 接続時
  （``rules`` は有効ローカルルールのメタ配列, #84）／``{"type":"state","view":{...}}`` 状態
  更新時／``{"type":"error","message":...}``。
- client→server: Action の JSON（``{"type":"play","player":"p1","card_ids":[N]}`` 等。
  複数枚出し対応で ``card_ids`` はリスト, #35）。ローカルルール設定は
  ``{"type":"new_game","player":"p1","enabled_rule_ids":[...]}``（構成を差し替えて新規
  ゲーム, #85。直後の state ブロードキャストは ``rules`` を同梱）。再接続は ``/ws?token=<token>``。
"""

from __future__ import annotations

import secrets
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

from ..engine.actions import ActionError, NewGameAction, ResetPlayersAction, parse
from ..engine.engine import EngineError
from .session import Session, SessionError, SessionFull

# 参加者リセット（#115）で退席させる旧接続へ送る通知。旧接続の close はこのハンドラ
# （別接続のタスク）からは配信が不安定なので**サーバからは close せず**、クライアントが
# この通知を受けて自分で切断・再接続停止し「参加するにはリロード」を表示する。これで
# 旧ブラウザが空席を自動再接続で奪わない。送信は現行接続への send と同経路で確実。
EVICTED_MESSAGE = "参加者がリセットされました。参加するにはリロードしてください。"

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
# カード画像の配信元。generator（cards_render）の出力先 static/cards と一致させる（§7）。
CARDS_DIR = Path(__file__).resolve().parent.parent / "static" / "cards"


async def _broadcast(
    session: Session, include_rules: bool = False, exclude: object | None = None
) -> None:
    """現行接続の各クライアントへ、その視界の PlayerView をフル送信する（§6）。

    ``include_rules`` が True のときは有効ルールのメタも同梱する。ルール構成が変わる
    new_game の後だけ True にし、通常の手番更新では送らない（フロントの設定パネルの
    途中操作を毎手番でリセットしないため, #85）。

    ``exclude`` を渡すとその接続には送らない（新規着席の通知で、welcome を受け取った
    ばかりの本人を二重更新しないため, #115）。各メッセージには待機状態
    （``waiting_for_opponent``）を同梱し、相手参加でゲートが解除されたことを既存
    クライアントへ伝える。
    """
    waiting = session.waiting_for_opponent()
    for conn, view in session.broadcast_targets():
        if conn is exclude:
            continue
        msg: dict = {"type": "state", "view": view.to_dict(), "waiting_for_opponent": waiting}
        if include_rules:
            msg["rules"] = session.rules_meta()
        try:
            await conn.send_json(msg)
        except Exception:  # noqa: BLE001 送信失敗（切断途中など）は握りつぶす
            pass


def create_app(
    session: Session | None = None,
    web_dir: Path | None = None,
    cards_dir: Path | None = None,
) -> FastAPI:
    """FastAPI アプリを生成する。テストは ``session``（決定的 seed）等を注入できる。"""
    # seed は任意幅の乱数（本番はゲームごとに 1 つ。決定性は不要なので secrets を使う）
    session = session if session is not None else Session(seed=secrets.randbits(63))
    web_dir = web_dir if web_dir is not None else WEB_DIR
    cards_dir = cards_dir if cards_dir is not None else CARDS_DIR
    # マウント対象が無いと StaticFiles が失敗するため、空でも存在を保証する
    web_dir.mkdir(parents=True, exist_ok=True)
    cards_dir.mkdir(parents=True, exist_ok=True)

    app = FastAPI()
    app.state.session = session

    @app.websocket("/ws")
    async def ws_endpoint(websocket: WebSocket) -> None:
        await websocket.accept()
        token = websocket.query_params.get("token")
        try:
            result = session.connect(token=token, conn=websocket)
        except SessionFull:
            await websocket.send_json({"type": "error", "message": "満席です（3人目以降は拒否）"})
            await websocket.close()
            return

        # 多重接続の後勝ち: 旧接続を閉じる（§8）
        if result.replaced is not None and result.replaced is not websocket:
            try:
                await result.replaced.close()
            except Exception:  # noqa: BLE001
                pass

        # 想定外例外でもタスクを抜ける前に必ずスロットを掃除する（ゾンビ枠でのソフト
        # ロック防止）。ゲーム系例外は受信ループ内で捕捉しクライアントへ返す。
        try:
            await websocket.send_json(
                {
                    "type": "welcome",
                    "token": result.token,
                    "player_id": result.player_id,
                    "view": result.view.to_dict(),
                    # 有効ローカルルールのメタ（確認パネル用, #84）。静的情報なので
                    # 接続時に一度だけ配る。判定はサーバ権威、フロントは表示のみ。
                    "rules": session.rules_meta(),
                    # 対戦相手の接続待ちか（待機ゲート #115）。片席のみなら True。
                    "waiting_for_opponent": session.waiting_for_opponent(),
                }
            )
            # 新規着席（初回/参加者リセット後の入場）は、既存クライアントへ状態を送って
            # 待機ゲートを解除させる（本人 welcome とは二重更新にならないよう除外）。
            # 再接続（リロード復帰）は顔ぶれ不変なので送らない（#115）。
            if not result.reconnected:
                await _broadcast(session, exclude=websocket)
            while True:
                raw = await websocket.receive_text()
                try:
                    act = parse(raw)
                    if act.type == ResetPlayersAction.type:
                        # 参加者リセット（#115）: 相手席を解放し、旧接続へ evicted 通知を送る。
                        # サーバからは close しない（別接続タスクからの close は配信が不安定）。
                        # クライアントが通知を受けて自分で切断・再接続停止する。
                        res = session.reset_players(result.token, act.player)
                        for old in res.evicted:
                            try:
                                await old.send_json({"type": "evicted", "message": EVICTED_MESSAGE})
                            except Exception:  # noqa: BLE001 切断途中などは握りつぶす
                                pass
                    else:
                        session.apply(result.token, act)
                except (SessionError, ActionError, EngineError) as exc:
                    await websocket.send_json({"type": "error", "message": str(exc)})
                    continue
                # ルール構成が変わる new_game のときだけ更新後のメタを同梱する（#85）。
                await _broadcast(session, include_rules=act.type == NewGameAction.type)
        except WebSocketDisconnect:
            pass
        except Exception:  # noqa: BLE001 想定外でもスロット解放のため握る（可視化は将来 logging）
            pass
        finally:
            session.disconnect(result.token, conn=websocket)

    # 静的配信は WS ルート定義後にマウント。カード画像 → web の順（"/" は貪欲なので最後）。
    app.mount("/cards", StaticFiles(directory=cards_dir), name="cards")
    app.mount("/", StaticFiles(directory=web_dir, html=True), name="web")
    return app


# uvicorn 実行用のモジュールレベル ASGI アプリ（1ゲーム）。
app = create_app()


def run(host: str = "0.0.0.0", port: int = 8000) -> None:
    """LAN 内向けにサーバを起動する（host=0.0.0.0）。CLI(#16) から呼ぶ想定。"""
    import uvicorn

    uvicorn.run(app, host=host, port=port)


__all__ = ["create_app", "app", "run", "WEB_DIR", "CARDS_DIR"]
