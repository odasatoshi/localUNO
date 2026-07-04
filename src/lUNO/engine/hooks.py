"""フックの2分類と実行器（spec.md §3.2/§3.3/§3.4）。

ローカルルールの差し込み口を2種類で表す（役割が異なるので統一しない）。

**(A) 値リデューサ型** ``handler(現在値, ctx) -> 新しい値``
    その場で1つの値を先頭から畳み込んで算出する（GameState には永続化しない）。
    ``can_play``(bool, シード False) / ``score``(int, シード 0) など。毎回シードから
    畳み直し、結果はその場で消費する（state フィールドへ書き戻さない）。

**(B) state トランスフォーマ型** ``handler(state, ctx) -> state``
    GameState 全体を順に変換する。**永続フィールドの書き換えはすべてこちらが担う**。
    ``on_before_play`` / ``on_after_play`` / ``on_draw`` / ``on_turn_end`` /
    ``on_choose_color`` など。

合成方式（§3.3）: 有効ルールは順序付きリスト。各フックはそのリストを**先頭から順に**
評価し、前の出力を次が受け取る。最後に返った結果が採用される（後勝ち）。順序は設定の
**記述順**で決める（priority 数値や依存宣言は用いない）。

can_play の合成意味論（§3.4）: 初期値 ``False``。許可を足すルールは条件を満たせば
``True``（OR 的追加）、制限を課すルールは条件に反すると ``False``（前がどうであれ却下）。
したがって制限ルールは、制限したい許可ルールより**後ろ**に置く。

本モジュールはエンジンの純粋部（原則2）。ネットワーク・描画に依存しない。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .actions import Action
from .cards import CardInstance
from .state import GameState

# --- フック名（標準で使う面。ローカルルールは任意に増やせる） -----------------

# 値リデューサ型
CAN_PLAY = "can_play"
SCORE = "score"
# state トランスフォーマ型
ON_BEFORE_PLAY = "on_before_play"
ON_AFTER_PLAY = "on_after_play"
ON_DRAW = "on_draw"
ON_TURN_END = "on_turn_end"
ON_CHOOSE_COLOR = "on_choose_color"

# シードが固定定数の値リデューサ（§3.2）。ここに無い値リデューサは呼び出し側が seed を渡す。
VALUE_SEEDS: dict[str, Any] = {CAN_PLAY: False, SCORE: 0}


@dataclass(frozen=True)
class Ctx:
    """フックに渡す読み取り専用の評価文脈（spec §3.1）。

    面を増やしたら spec.md へ追記する。``card``/``hand`` は評価対象カードとその持ち主の
    手札（あれば）。値リデューサ/トランスフォーマ双方から参照される。
    """

    state: GameState
    current_player: str
    action: Action | None = None
    card: CardInstance | None = None
    hand: tuple[CardInstance, ...] | None = None
    top_of_pile: CardInstance | None = None

    @classmethod
    def from_state(
        cls,
        state: GameState,
        *,
        action: Action | None = None,
        card: CardInstance | None = None,
        owner: str | None = None,
    ) -> Ctx:
        """GameState から ctx を組み立てる（配線ミス・state との二重管理を防ぐ）。

        ``current_player`` / ``top_of_pile`` は state を権威として自動で埋める。``owner``
        は評価対象カードの持ち主（手番外評価では相手を指定）。既定は手番プレイヤー。
        """
        who = state.current_player if owner is None else owner
        if who not in state.hands:
            raise ValueError(f"未知の owner: {who!r}")
        return cls(
            state=state,
            current_player=state.current_player,
            action=action,
            card=card,
            hand=state.hands.get(who),
            top_of_pile=state.top_of_pile(),
        )


# ハンドラ型
ValueHandler = Callable[[Any, "Ctx"], Any]
StateHandler = Callable[[GameState, "Ctx"], GameState]
Handler = ValueHandler | StateHandler


# --- 実行器（記述順で先頭から畳み込む、後勝ち。§3.3） ------------------------


def reduce_value(handlers: Iterable[ValueHandler], seed: Any, ctx: Ctx) -> Any:
    """値リデューサ型フックを ``seed`` から記述順に畳み込む（§3.2/§3.3）。

    ``value = handler(value, ctx)`` を先頭から順に適用し、最後の値を返す。結果は
    その場で消費し、GameState へ書き戻さない。
    """
    value = seed
    for handler in handlers:
        value = handler(value, ctx)
    return value


def transform_state(handlers: Iterable[StateHandler], state: GameState, ctx: Ctx) -> GameState:
    """state トランスフォーマ型フックを記述順に畳み込む（§3.2/§3.3）。

    ``state = handler(state, ctx)`` を先頭から順に適用し、最後の state を返す。永続
    フィールドの書き換えはこの経路のハンドラのみが行う（§3.2）。
    """
    for handler in handlers:
        state = handler(state, ctx)
        if not isinstance(state, GameState):
            raise TypeError(
                f"state トランスフォーマは GameState を返すこと: "
                f"{handler!r} が {type(state).__name__} を返した（return state 忘れ?）"
            )
    return state


# --- ルール集約（順序付きルール → フック別ハンドラ列） -----------------------

# 1つのルールは {フック名: ハンドラ or ハンドラのリスト} のマップで表す。
Rule = Mapping[str, "Handler | Sequence[Handler]"]

# reduce の seed 省略を検出するセンチネル（正当な None seed と区別する）
_MISSING = object()


class HookRegistry:
    """フック名ごとにハンドラを**登録順**で保持する実行器（§3.3）。

    値リデューサの固定シードも保持する（既定は :data:`VALUE_SEEDS`）。新しい値リデューサ
    hook を足すルールは :meth:`register_seed` か :func:`build_registry` の ``seeds`` で
    シードを宣言する（§3.2「シードは固定定数」を拡張側でも守る導線）。
    """

    def __init__(self, seeds: Mapping[str, Any] | None = None) -> None:
        self._hooks: dict[str, list[Handler]] = {}
        self._seeds: dict[str, Any] = dict(VALUE_SEEDS)
        if seeds:
            self._seeds.update(seeds)

    def add(self, name: str, handler: Handler) -> None:
        self._hooks.setdefault(name, []).append(handler)

    def register_seed(self, name: str, seed: Any) -> None:
        """値リデューサ hook の固定シードを宣言する。"""
        self._seeds[name] = seed

    def handlers(self, name: str) -> tuple[Handler, ...]:
        return tuple(self._hooks.get(name, ()))

    def reduce(self, name: str, ctx: Ctx, seed: Any = _MISSING) -> Any:
        """値リデューサ型フックを畳み込む。

        ``seed`` 省略時は登録済みの固定シードを使う。未登録の値リデューサで省略すると
        沈黙バグ（None 起点畳み込み）を避けるため :class:`ValueError` を送出する。
        """
        if seed is _MISSING:
            if name not in self._seeds:
                raise ValueError(
                    f"値リデューサ {name!r} の seed が未登録です。"
                    "register_seed / build_registry(seeds=...) / seed= のいずれかで宣言してください"
                )
            seed = self._seeds[name]
        return reduce_value(self.handlers(name), seed, ctx)

    def transform(self, name: str, state: GameState, ctx: Ctx) -> GameState:
        """state トランスフォーマ型フックを畳み込む。"""
        return transform_state(self.handlers(name), state, ctx)

    # 標準の値リデューサ用ショートカット（シードは VALUE_SEEDS を単一ソースに参照）
    def can_play(self, ctx: Ctx) -> bool:
        return bool(self.reduce(CAN_PLAY, ctx))

    def score(self, ctx: Ctx) -> int:
        return int(self.reduce(SCORE, ctx))


def build_registry(rules: Iterable[Rule], seeds: Mapping[str, Any] | None = None) -> HookRegistry:
    """順序付きルール列から :class:`HookRegistry` を組み立てる（記述順を保存、§3.3）。

    各ルールの各フックのハンドラを、ルールの並び順・ルール内の並び順で登録する。値が
    単一ハンドラでもリストでも受け付ける。``seeds`` で追加の値リデューサ seed を宣言できる。
    """
    reg = HookRegistry(seeds=seeds)
    for rule in rules:
        for name, handler in rule.items():
            if callable(handler):
                reg.add(name, handler)
            else:
                for one in handler:
                    reg.add(name, one)
    return reg


__all__ = [
    "CAN_PLAY",
    "SCORE",
    "ON_BEFORE_PLAY",
    "ON_AFTER_PLAY",
    "ON_DRAW",
    "ON_TURN_END",
    "ON_CHOOSE_COLOR",
    "VALUE_SEEDS",
    "Ctx",
    "ValueHandler",
    "StateHandler",
    "Handler",
    "Rule",
    "HookRegistry",
    "reduce_value",
    "transform_state",
    "build_registry",
]
