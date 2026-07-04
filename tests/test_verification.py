"""土台の最優先目標「コアを改修せずローカルルールを積める」の検証（issue #17）。

- engine/ が rules/ を一切 import しない（依存方向は rules→engine のみ）＝ engine 非改修で
  ルールが載る静的証明。
- rule-authoring.md の手順どおり、新しいローカルルールを rules 相当の dict として足すと、
  engine を1行も触らずに挙動が変わることの動的検証。
- 是正後の rule-authoring.md の応答待ち例（wild_effect / on_choose_color）が実 API で
  動くことの回帰担保。
"""

from __future__ import annotations

import ast
from pathlib import Path

import lUNO.engine as engine_pkg
from lUNO.engine.actions import ChooseColorAction, PlayAction
from lUNO.engine.cards import CardInstance, CardType, Color
from lUNO.engine.engine import apply_action
from lUNO.engine.hooks import (
    CAN_PLAY,
    ON_AFTER_PLAY,
    ON_CHOOSE_COLOR,
    Ctx,
    build_registry,
)
from lUNO.engine.state import GameState
from lUNO.rules import ENABLED_RULES, registry, standard

P = ("p1", "p2")


def card(symbol, color, cid):
    return CardInstance(CardType(symbol=symbol, color=color, label=symbol), id=cid)


# --- 完了条件: engine/ の差分ゼロでルールが有効化される（静的証明） ----------


def test_engine_does_not_import_rules():
    """engine/*.py は rules を import しない（依存方向 rules→engine）。

    エンジンがルールを知らない＝ルール追加でエンジンを改修する必要が構造的に無い。
    """
    engine_dir = Path(engine_pkg.__file__).parent
    offenders: list[tuple[str, str]] = []
    for py in sorted(engine_dir.rglob("*.py")):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            mods: list[str] = []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                mods = [base] + [f"{base}.{a.name}".strip(".") for a in node.names]
            for m in mods:
                if "rules" in m.split("."):
                    offenders.append((py.name, m))
    assert offenders == [], f"engine が rules を import している: {offenders}"


def test_enabled_rules_build_without_engine_involvement():
    """有効化リストは rules 側だけで実行器に組める（standard 先頭・複数ルール積載）。"""
    reg = registry()
    assert ENABLED_RULES[0] is standard.RULES
    assert len(ENABLED_RULES) >= 2  # standard + ローカルルールが積まれている
    # 実行器として機能する（can_play が呼べる）
    st = GameState.new_game(P, seed=1)
    ctx = Ctx.from_state(st, card=card("7", Color.RED, 0), owner="p1")
    assert isinstance(reg.can_play(ctx), bool)


# --- 完了条件: 追加ルールが engine 非改修で挙動を変える（動的検証） ----------


def _ctx(played, top, hand=None):
    st = GameState.new_game(P, seed=1)
    hand = hand if hand is not None else (played,)
    return Ctx(state=st, current_player="p1", card=played, hand=hand, top_of_pile=top)


def test_new_local_rule_changes_can_play_without_touching_engine():
    """rule-authoring.md の「許可を足す」手順で新ルールを dict として足すと挙動が変わる。"""

    # 新ローカルルール: 数字の 7 はいつでも出せる（OR 的に許可を追加）
    def sevens_always(current, ctx):
        if current:
            return True
        return ctx.card.symbol == "7"

    played = card("7", Color.BLUE, 1)
    top = card("3", Color.RED, 2)  # 標準では色も記号も不一致 → 不可

    base = build_registry([standard.RULES])
    extended = build_registry([standard.RULES, {CAN_PLAY: sevens_always}])

    assert base.can_play(_ctx(played, top)) is False  # 標準のみ: 却下
    assert extended.can_play(_ctx(played, top)) is True  # ルール追加で許可（engine 非改修）


def test_documented_wild_pattern_works_with_real_api():
    """是正後の rule-authoring.md の wild_effect / on_choose_color 例が実 API で動く。"""

    def wild_effect(state, ctx):
        if not ctx.card.is_wild:
            return state
        return state.with_awaiting({ctx.current_player: ("choose_color",)})

    def on_choose_color(state, ctx):
        return state.with_forced_color(ctx.action.color)

    reg = build_registry(
        [
            {CAN_PLAY: lambda current, ctx: True},
            {ON_AFTER_PLAY: wild_effect},
            {ON_CHOOSE_COLOR: on_choose_color},
        ]
    )
    wild = card("wild", None, 1)
    st = GameState(
        hands={"p1": (wild, card("5", Color.RED, 2)), "p2": (card("9", Color.GREEN, 3),)},
        draw_pile=(),
        discard_pile=(card("3", Color.BLUE, 4),),
        current_player="p1",
        rng_state=GameState.new_game(P, seed=0).rng_state,
        awaiting={"p1": ("play", "draw")},
    )

    paused = apply_action(reg, st, PlayAction("p1", card_ids=(1,)))
    assert paused.awaiting == {"p1": ("choose_color",)}  # 停止
    assert paused.current_player == "p1"

    done = apply_action(reg, paused, ChooseColorAction("p1", Color.RED))
    assert done.forced_color == Color.RED  # 継続フックが確定
    assert done.current_player == "p2"  # awaiting 解消でエンジンが手番送り
    assert done.awaiting == {"p2": ("play", "draw")}


# --- 完了条件: 追加ルール（既存の draw2_stack）の挙動が標準と異なる ---------


def test_draw2_stack_rule_is_active_in_enabled_set():
    """有効化リストに積まれた draw2_stack が、標準のみと異なる受理集合を生む。

    標準のみ: draw2 を出すと相手は「引く」だけ（スタック不可）。draw2_stack 有効時は
    相手の受理集合に play が加わりスタックできる（rules 内だけの差分で実現）。
    """
    from lUNO.rules import draw2_stack

    st = GameState(
        # 各自 draw2 の他に1枚持つ（draw2 を出しても上がらない＝終局で awaiting/pending が
        # クリアされないようにする）
        hands={
            "p1": (card("draw2", Color.RED, 1), card("5", Color.RED, 5)),
            "p2": (card("draw2", Color.BLUE, 2), card("9", Color.GREEN, 6)),
        },
        draw_pile=(card("0", Color.RED, 3),),
        discard_pile=(card("7", Color.RED, 4),),
        current_player="p1",
        rng_state=GameState.new_game(P, seed=0).rng_state,
        awaiting={"p1": ("play", "draw")},
    )

    base = build_registry([standard.RULES])
    stacked = build_registry([standard.RULES, draw2_stack.RULES])

    after_base = apply_action(base, st, PlayAction("p1", card_ids=(1,)))
    after_stack = apply_action(stacked, st, PlayAction("p1", card_ids=(1,)))

    # 標準のみ: 相手は draw のみ（スタック不可）
    assert after_base.awaiting.get("p2") == ("draw",)
    assert after_base.pending_draw == 2
    # スタック有効: 相手の受理集合に play が加わる（Draw2 で返せる）
    assert "play" in after_stack.awaiting.get("p2", ())
    assert after_stack.pending_draw == 2

    # 完了条件2: 実際に Draw2 を返すと累積が 2 → 4 に積み上がる（rules 内だけで実現）
    after_return = apply_action(stacked, after_stack, PlayAction("p2", card_ids=(2,)))
    assert after_return.pending_draw == 4
    assert "play" in after_return.awaiting.get("p1", ())  # 返し合いが連鎖できる
