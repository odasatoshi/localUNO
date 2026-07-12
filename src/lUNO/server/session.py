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

from ..engine.actions import Action, NewGameAction, ResetAction, parse
from ..engine.engine import apply_action
from ..engine.state import GameEvent, GameState, PlayerView, player_view
from ..rules import RULE_CATALOG, catalog_meta, default_enabled_ids, order_violations
from ..rules import registry as default_registry
from ..rules import setup_game as default_setup

PLAYER_IDS = ("p1", "p2")

# 待機ゲート（#115）を免除する「席の顔ぶれに依存しない」仕切り直し系 Action。
# これらは1人（片席のみ）でも実行でき、盤面/ルールの作り直しに使う。
_ROSTER_ACTIONS = frozenset({ResetAction.type, NewGameAction.type})


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


@dataclass
class ResetPlayersResult:
    """参加者リセット（#115）の結果。

    ``evicted`` は解放した相手席の**現行接続ハンドル**の一覧（app.py が
    ``evicted`` 通知を送って close し、旧ブラウザをリロード待機へ誘導する）。切断中
    （conn=None）だった席は解放されるが evicted には載らない（閉じる対象が無い）。
    """

    evicted: list[object]


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
        # 初期の有効集合（未指定は全 default）。standard 補完・順序正規化は下で行う。
        requested = default_enabled_ids() if enabled_ids is None else frozenset(enabled_ids)
        # 現在の評価順（required 先頭・カタログ順に正規化）。_new_game と同じ正規化で、
        # enabled_ids は常に順序（standard 含む）から導く＝単一真実源で経路差を無くす。
        self._ordered_ids: tuple[str, ...] = tuple(
            s.id for s in RULE_CATALOG if s.required or s.id in requested
        )
        self._enabled_ids: frozenset[str] = frozenset(self._ordered_ids)
        # 実行器: registry を明示注入すればそれを、なければ enabled_ids から組む
        # （id 指定が無ければ従来どおり全 default）。
        if registry is not None:
            self._registry = registry
        elif enabled_ids is not None:
            self._registry = default_registry(self._enabled_ids)
        else:
            self._registry = default_registry()
        self._setup = setup if setup is not None else default_setup
        # 初回は先攻をランダムに決める（ハウスルール, #107）。以降の再戦は前ゲームの勝者を先攻。
        self._state: GameState = self._start(seed, first_player=None)
        self._token_factory = token_factory or (lambda: secrets.token_hex(16))
        self._slots: dict[str, Slot] = {}  # token -> Slot
        self._by_player: dict[str, Slot] = {}  # player_id -> Slot
        # 連勝カウント（ゲームをまたぐ状態。GameState は毎ゲーム作り直されるので Session に持つ）。
        # 同じプレイヤーが連続で勝った回数を数え、2連勝以上でカットイン（#108）に使う。
        self._streak_holder: str | None = None
        self._streak_count: int = 0

    # --- 参照 -------------------------------------------------------------

    @property
    def state(self) -> GameState:
        return self._state

    @property
    def enabled_ids(self) -> frozenset[str]:
        """現在有効なルール id 集合（確認・設定画面用）。"""
        return self._enabled_ids

    @property
    def ordered_ids(self) -> tuple[str, ...]:
        """現在の評価順（required 先頭。順序編集 #93 で並べ替え）。"""
        return self._ordered_ids

    def rules_meta(self) -> list[dict[str, object]]:
        """カタログ全ルールのメタ＋現在の有効フラグを**現在の評価順**で（welcome/state 配信用）。"""
        return catalog_meta(self._enabled_ids, order=self._ordered_ids)

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

    def waiting_for_opponent(self) -> bool:
        """まだ両席が埋まっていない（対戦相手の接続待ち）か（#115）。

        参加者リセット直後や初回接続直後など、席が1つしか埋まっていない間は True。
        配信メッセージの ``waiting_for_opponent`` フラグと :meth:`apply` の待機ゲートの
        **単一真実源**。切断中でも席（トークン）が残っていれば「埋まっている」扱い
        （リロード復帰のための保持中はゲートしない）。
        """
        return len(self._by_player) < len(PLAYER_IDS)

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

    def reset_players(self, token: str, player: str | None = None) -> ResetPlayersResult:
        """参加者（席）をリセットし、別ブラウザへの入れ替えを可能にする（#115）。

        要求者（``token`` の持ち主）は**自席・トークンを維持したまま在席**し、もう一方の
        席を :meth:`release` で解放してトークンを無効化する。解放した席の**現行接続**は
        ``ResetPlayersResult.evicted`` で返す（app.py が ``evicted`` 通知＋close で旧ブラウザ
        をリロード待機へ誘導し、自動再接続で席を奪わせない）。盤面は同設定で再配札し、
        連勝はリセットする。空いた席は次に接続/リロードした人が埋める（先着）。

        ``player`` を渡した場合はトークンの席と一致すること（app 由来のなりすまし防止。
        なりすましは :meth:`apply` と同じく :class:`SessionError`）。
        """
        slot = self._slots.get(token)
        if slot is None:
            raise SessionError(f"未知のトークン: {token!r}")
        if player is not None and player != slot.player_id:
            raise SessionError(f"トークンとプレイヤー不一致: {slot.player_id!r} != {player!r}")

        keep = slot.player_id
        evicted: list[object] = []
        for pid in PLAYER_IDS:
            if pid == keep:
                continue
            other = self._by_player.get(pid)
            if other is None:
                continue
            if other.conn is not None:
                evicted.append(other.conn)
            self.release(other.token)  # 相手席のみ解放（要求者は据え置き）

        # 顔ぶれが変わる仕切り直し＝新規対局扱い。先攻はランダム、連勝はリセット（#107/#108）。
        # 相手が元々いない（evicted 空）場合も新規対局として作り直す（明示リセットの一貫動作）。
        self._redeal(first_player=None)
        self._streak_holder = None
        self._streak_count = 0
        return ResetPlayersResult(evicted=evicted)

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

        # 待機ゲート（#115）: 両席が揃うまではゲーム操作を進行不可にする。仕切り直し系
        # （reset/new_game）は待機中でも許す（1人で再配札・ルール変更して待てる）。
        if act.type not in _ROSTER_ACTIONS and self.waiting_for_opponent():
            raise SessionError("対戦相手の接続を待っています")

        if act.type == NewGameAction.type:
            self._new_game(act.enabled_rule_ids)
            # ルール構成を変える仕切り直しは別ゲーム扱い＝連勝をリセット（#108）。
            self._streak_holder = None
            self._streak_count = 0
        elif act.type == ResetAction.type:
            # 同設定での再戦は連勝を継続（次の勝利確定時に holder と突き合わせて加算/リセット）。
            self._reset()
        else:
            prev_winner = self._state.winner
            self._state = apply_action(self._registry, self._state, act)
            # winner が None→確定に変化した瞬間だけ数える（勝利経路＝standard/jump_in 等に
            # 依存しない堅い検出）。引き分け（is_draw）は winner が立たないので連勝は据え置き。
            new_winner = self._state.winner
            if new_winner is not None and prev_winner is None:
                self._record_win(new_winner)
        return self._state

    def _record_win(self, winner: str) -> None:
        """勝者確定を連勝カウントへ反映し、2連勝以上なら結果 state に連勝イベントを載せる。

        連勝は Session が保持する（ゲームをまたぐため）。2連勝以上のときだけ
        ``GameEvent("win_streak", by=winner, amount=n)`` を ``with_last_event`` で載せ、
        既存のカットイン配信経路（state ブロードキャスト）に乗せる（#108）。
        """
        if self._streak_holder == winner:
            self._streak_count += 1
        else:
            self._streak_holder = winner
            self._streak_count = 1
        if self._streak_count >= 2:
            self._state = self._state.with_last_event(
                GameEvent("win_streak", by=winner, amount=self._streak_count)
            )

    def _start(self, seed: int, first_player: str | None) -> GameState:
        """rules のセットアップで盤面を作り、先攻を決めて付け替える（#107）。

        ``self._setup`` の signature（注入物含む）に先攻の概念を持ち込まず、
        セットアップ後に :meth:`GameState.with_first_player` で先攻だけを揃える。

        ``first_player=None`` なら**配札後の RNG ストリーム**から先攻をランダムに引く。
        配札を消費した後のストリームから引くため山札・配札と無相関で、かつ同一 seed なら
        再現的（§3.5）。``first_player`` 指定時（再戦の勝者先攻）はその値をそのまま使う。
        """
        state = self._setup(PLAYER_IDS, seed)
        if first_player is None:
            first_player, state = state.with_rng(lambda rng: rng.choice(PLAYER_IDS))
        return state.with_first_player(first_player)

    def _redeal(self, first_player: str | None) -> None:
        """新 seed を現在の RNG ストリームから決定的に引き、盤面を作り直す（#107）。

        再戦(:meth:`_reset`)・ルール変更(:meth:`_new_game`)・参加者リセット
        (:meth:`reset_players`) 共通の「新 seed → :meth:`_start`」を一元化する。
        ``first_player`` は先攻指定（``None`` なら配札後 RNG からランダム）。
        """
        seed, _ = self._state.with_rng(lambda rng: rng.getrandbits(32))
        self._state = self._start(seed, first_player)

    def _reset(self) -> None:
        """同じ2トークンのまま盤面を作り直す（再戦, §8）。新 seed は RNG から決定的に引く。

        ハウスルール(#107): 前ゲームの勝者を先攻にする。引き分け（勝者なし）は先攻を
        ランダムに決め直す（``first_player=None`` でランダム引きに委ねる）。
        """
        winner = self._state.winner
        first = winner if winner in PLAYER_IDS else None
        self._redeal(first)

    def _new_game(self, enabled_rule_ids: Iterable[str]) -> None:
        """選択したルール構成**と順序**で実行器を組み直し、盤面を新規に作る（設定変更, #85/#92）。

        どちらのプレイヤーからも実行できる（`apply` のトークン整合のみ確認）。standard は
        required で常に有効・先頭。未知の id は弾き（構成ミスの黙認防止）、前後依存（after）を
        破る順序も弾く（silently-wrong 防止）。順序を**単一の真実源**として registry・enabled
        集合・メタを必ずここから再構築する（実行器とメタの乖離を防ぐ）。
        """
        requested = list(enabled_rule_ids)
        known = {m["id"] for m in catalog_meta()}
        unknown = sorted(set(requested) - known)
        if unknown:
            raise SessionError(f"未知のルールID: {unknown}")
        # 実効順: required(standard) を先頭に、要求順の有効ルールを続ける（重複除去・順序保持）。
        required_first = [s.id for s in RULE_CATALOG if s.required]
        ordered = tuple(dict.fromkeys(required_first + requested))
        violations = order_violations(ordered)
        if violations:
            raise SessionError(f"順序制約に違反: {violations}")
        self._ordered_ids = ordered
        self._enabled_ids = frozenset(ordered)
        self._registry = default_registry(ordered)  # tuple → 与えた順で積む
        # ルール変更しての新規開始は「勝者先攻」を適用せず、先攻はランダム（#107）。
        self._redeal(first_player=None)


__all__ = [
    "PLAYER_IDS",
    "Session",
    "Slot",
    "ConnectResult",
    "ResetPlayersResult",
    "SessionError",
    "SessionFull",
]
