"""サーバ権威の1ゲームを管理するセッション（spec.md §5/§6/§8）。

**transport 非依存**に保つ（FastAPI/WebSocket 結線は #14 app.py の責務）。本モジュールは:

- GameState をメモリに1つだけ保持し、Action 適用は engine 経由で行う（サーバ権威, 原則1）。
- 先着2接続をプレイヤー ``p1`` / ``p2`` に割当て、3人目以降は拒否する（§8）。
- 再接続トークンをサーバが発行し、同トークンでの再接続で PlayerView を復元する（§8）。
- 多重接続は後勝ちで置換する（旧接続ハンドルを呼び出し側へ返し、閉じさせる）。
- リセット（再戦）は同じ2トークンのまま盤面を作り直す（§8）。
- 各プレイヤー向けの視界フィルタ済み PlayerView を配信用に返す（§5）。

接続ハンドル（``conn``）は WebSocket 等の不透明オブジェクトとして扱い、その送信・切断は
app.py が担う。ここではどの接続が有効かの対応だけを持つ。

前提: :class:`Session` は排他制御を持たない。Action 適用の直列化（単一イベントループ）を
前提とする（FastAPI の単一ワーカー/asyncio 上で使う）。
"""

from __future__ import annotations

import secrets
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from ..engine.actions import Action, ResetAction, parse
from ..engine.engine import apply_action
from ..engine.state import GameState, PlayerView, player_view
from ..rules import catalog_meta, default_enabled_ids
from ..rules import registry as default_registry
from ..rules import setup_game as default_setup

PLAYER_IDS = ("p1", "p2")


class SessionError(Exception):
    """セッション操作の失敗（未知トークン・プレイヤー不一致など）。"""


class SessionFull(SessionError):
    """3人目以降の接続拒否（§8）。"""


@dataclass
class Slot:
    """1プレイヤー分の枠。token とプレイヤー ID、現在の接続ハンドルを持つ。"""

    player_id: str
    token: str
    conn: object | None = None


@dataclass
class ConnectResult:
    """接続/再接続の結果。``replaced`` は後勝ち置換で切断すべき旧接続ハンドル。"""

    token: str
    player_id: str
    view: PlayerView
    replaced: object | None = None
    reconnected: bool = False


class Session:
    """1ゲーム分のサーバ権威状態と接続を束ねる（transport 非依存）。"""

    def __init__(
        self,
        seed: int,
        *,
        token_factory: Callable[[], str] | None = None,
        registry=None,
        setup: Callable[..., GameState] | None = None,
        enabled_ids: Iterable[str] | None = None,
    ) -> None:
        # 有効ルール id 集合（設定・確認画面用のメタ）。未指定は全 default。
        # 注意: registry を明示注入しつつ enabled_ids も渡すと、実行器（注入物）と
        # メタ（enabled_ids 由来の rules_meta）が乖離し得る。通常はどちらか一方のみ
        # 指定する（registry 注入はテスト用の挙動固定、enabled_ids は設定 #85 の本経路）。
        self._enabled_ids: frozenset[str] = (
            default_enabled_ids() if enabled_ids is None else frozenset(enabled_ids)
        )
        # 実行器: registry を明示注入すればそれを、なければ enabled_ids から組む
        # （id 指定が無ければ従来どおり全 default）。
        if registry is not None:
            self._registry = registry
        elif enabled_ids is not None:
            self._registry = default_registry(self._enabled_ids)
        else:
            self._registry = default_registry()
        self._setup = setup if setup is not None else default_setup
        self._state: GameState = self._setup(PLAYER_IDS, seed)
        self._token_factory = token_factory or (lambda: secrets.token_hex(16))
        self._slots: dict[str, Slot] = {}  # token -> Slot
        self._by_player: dict[str, Slot] = {}  # player_id -> Slot

    # --- 参照 -------------------------------------------------------------

    @property
    def state(self) -> GameState:
        return self._state

    @property
    def enabled_ids(self) -> frozenset[str]:
        """現在有効なルール id 集合（確認・設定画面用）。"""
        return self._enabled_ids

    def rules_meta(self) -> list[dict[str, object]]:
        """カタログ全ルールのメタ＋現在の有効フラグ（welcome での配信用）。"""
        return catalog_meta(self._enabled_ids)

    def view(self, player_id: str) -> PlayerView:
        return player_view(self._state, player_id)

    def views(self) -> dict[str, PlayerView]:
        """割当済みの各プレイヤー向け PlayerView。

        切断中（``conn is None``）のスロットも含む（送信可否は呼び出し側判断）。実際の
        配信は :meth:`broadcast_targets` を使うと現行接続のみに絞れる。
        """
        return {pid: player_view(self._state, pid) for pid in self._by_player}

    def broadcast_targets(self) -> list[tuple[object, PlayerView]]:
        """現行接続がある各プレイヤーの ``(conn, PlayerView)``。

        app.py の送信の**単一真実源**。conn マップを app 側に二重管理させないためのアクセサ。
        後勝ち置換（:meth:`connect` の ``replaced``）・:meth:`disconnect` の結果が自動反映される。
        """
        return [
            (slot.conn, player_view(self._state, slot.player_id))
            for slot in self._by_player.values()
            if slot.conn is not None
        ]

    def player_of(self, token: str) -> str | None:
        slot = self._slots.get(token)
        return slot.player_id if slot else None

    # --- 接続 -------------------------------------------------------------

    def connect(self, token: str | None = None, conn: object | None = None) -> ConnectResult:
        """新規接続（token=None または未知）か再接続（既知 token）を処理する。

        - 既知 token: 再接続。旧接続ハンドルを ``replaced`` で返す（後勝ち, §8）。
        - 未知/None: 空き枠へ割当て、新トークンを発行。枠が無ければ :class:`SessionFull`。
        """
        if token is not None and token in self._slots:
            slot = self._slots[token]
            previous = slot.conn
            slot.conn = conn
            return ConnectResult(
                token=token,
                player_id=slot.player_id,
                view=player_view(self._state, slot.player_id),
                replaced=previous,
                reconnected=True,
            )

        free = [pid for pid in PLAYER_IDS if pid not in self._by_player]
        if not free:
            raise SessionFull("3人目以降は接続拒否（§8）")

        player_id = free[0]  # 空いている枠に割当（release 後の再割当も正しく動く）
        new_token = self._token_factory()
        slot = Slot(player_id=player_id, token=new_token, conn=conn)
        self._slots[new_token] = slot
        self._by_player[player_id] = slot
        return ConnectResult(
            token=new_token,
            player_id=player_id,
            view=player_view(self._state, player_id),
        )

    def disconnect(self, token: str, conn: object | None = None) -> None:
        """接続を外す。``conn`` 指定時は現行接続と一致する場合のみ外す（後勝ち後の誤切断防止）。"""
        slot = self._slots.get(token)
        if slot is not None and (conn is None or slot.conn is conn):
            slot.conn = None

    def release(self, token: str) -> None:
        """スロットを解放してトークンを無効化する（明示離脱, §8）。"""
        slot = self._slots.pop(token, None)
        if slot is not None:
            self._by_player.pop(slot.player_id, None)

    # --- Action 適用 ------------------------------------------------------

    def apply(self, token: str, action: Action | dict | str) -> GameState:
        """トークンの持ち主として Action を適用し、新しい GameState を保持・返す。

        トークンとプレイヤーの不一致は拒否する（クライアントは自分の枠としてのみ操作可能）。
        リセットは rules のセットアップを同トークンのまま再実行する（§8）。

        送出し得る例外（app.py はこれらを捕捉する）:
        - :class:`SessionError` — 未知トークン／トークンとプレイヤーの不一致。
        - ``ActionError`` — 不正な JSON/フィールド（parse 由来）。
        - ``IllegalAction`` / ``EngineError`` — 手番外・出せない札など（engine 由来）。

        注: reset は base では両プレイヤーがいつでも実行できる（§8 は制約を課さない）。
        終局後限定などの制限が要るなら後続で受理集合／ルール側に足す。
        """
        slot = self._slots.get(token)
        if slot is None:
            raise SessionError(f"未知のトークン: {token!r}")
        act = action if isinstance(action, Action) else parse(action)
        if act.player != slot.player_id:
            raise SessionError(f"トークンとプレイヤー不一致: {slot.player_id!r} != {act.player!r}")

        if act.type == ResetAction.type:
            self._reset()
        else:
            self._state = apply_action(self._registry, self._state, act)
        return self._state

    def _reset(self) -> None:
        """同じ2トークンのまま盤面を作り直す（再戦, §8）。新 seed は RNG から決定的に引く。"""
        seed, _ = self._state.with_rng(lambda rng: rng.getrandbits(32))
        self._state = self._setup(PLAYER_IDS, seed)


__all__ = [
    "PLAYER_IDS",
    "Session",
    "Slot",
    "ConnectResult",
    "SessionError",
    "SessionFull",
]
