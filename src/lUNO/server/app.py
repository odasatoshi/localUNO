"""FastAPI アプリ: web/ 静的配信 + WebSocket エンドポイント（spec.md §6/§9）。

構成:
- ``static/cards/`` を ``/cards`` に静的マウント（generator が出力するカード画像 PNG, §7）。
- ``web/`` を ``/`` に静的マウント（``index.html`` / JS / CSS）。
- WebSocket ``/ws`` で Action を受信 → :class:`~lUNO.server.session.Session` に適用 →
  各接続へ視界フィルタ済み PlayerView をフル送信（差分は送らない, §6）。

サーバ権威（原則1）: クライアントは Action を送るだけ。状態はサーバが保持し PlayerView
のみを配る。多重接続は後勝ちで旧接続を閉じる（§8）。

メッセージ規約:
- server→client: ``{"type":"welcome","token","player_id","view":{...}}`` 接続時／
  ``{"type":"state","view":{...}}`` 状態更新時／``{"type":"error","message":...}``。
- client→server: Action の JSON（``{"type":"play","player":"p1","card_id":N}`` 等）。
  再接続は ``/ws?token=<token>`` のクエリでトークンを渡す。
"""

from __future__ import annotations

import secrets
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

from ..engine.actions import ActionError
from ..engine.engine import EngineError
from .session import Session, SessionError, SessionFull

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
# カード画像の配信元。generator（cards_render）の出力先 static/cards と一致させる（§7）。
CARDS_DIR = Path(__file__).resolve().parent.parent / "static" / "cards"


async def _broadcast(session: Session) -> None:
    """現行接続の各クライアントへ、その視界の PlayerView をフル送信する（§6）。"""
    for conn, view in session.broadcast_targets():
        try:
            await conn.send_json({"type": "state", "view": view.to_dict()})
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
                }
            )
            while True:
                raw = await websocket.receive_text()
                try:
                    session.apply(result.token, raw)
                except (SessionError, ActionError, EngineError) as exc:
                    await websocket.send_json({"type": "error", "message": str(exc)})
                    continue
                await _broadcast(session)
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
