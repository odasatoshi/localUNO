"""ルールメタ層（RuleSpec / RULE_CATALOG）と部分集合レジストリの試験（#83）。

設定画面（#84/#85）の土台。カタログのメタ整合、`registry()` の後方互換、
`registry(enabled_ids)` による部分集合構築（standard 強制・順序保持・未知ID無視）を担保する。

差分の検出には reverse を使う: reverse_off 有効時はリバースが通常カード＝手番が相手へ
渡り（p2）、standard のみだと2人でのリバースは自分の連続手番（p1 のまま）になる。
"""

from __future__ import annotations

import random

from lUNO.engine.actions import PlayAction
from lUNO.engine.cards import WILD, CardInstance, CardType, Color
from lUNO.engine.engine import STANDARD_TURN_ACTIONS, IllegalAction, apply_action
from lUNO.engine.state import GameState
from lUNO.rules import (
    ENABLED_RULES,
    RULE_CATALOG,
    RuleSpec,
    catalog_meta,
    default_enabled_ids,
    registry,
    standard,
)


def card(symbol: str, color: Color | None, cid: int) -> CardInstance:
    return CardInstance(CardType(symbol=symbol, color=color, label=symbol), id=cid)


def _reverse_state() -> GameState:
    """p1 が赤リバースを持ち、トップが赤3。reverse_off の有無で手番送りが変わる局面。"""
    return GameState(
        hands={
            "p1": (card("reverse", Color.RED, 1), card("5", Color.RED, 2)),
            "p2": (card("9", Color.GREEN, 3),),
        },
        draw_pile=(card("0", Color.RED, 4),),
        discard_pile=(card("3", Color.RED, 5),),
        current_player="p1",
        rng_state=random.Random(0).getstate(),
        awaiting={"p1": ("play", "draw")},
    )


def _turn_after_reverse(reg) -> str:
    """_reverse_state で p1 が赤リバースを出した後の手番プレイヤーを返す。"""
    return apply_action(reg, _reverse_state(), PlayAction("p1", card_ids=(1,))).current_player


# --- カタログのメタ整合 ----------------------------------------------------


def test_catalog_first_is_standard_and_required():
    """カタログ先頭は standard で、required（切替不可・土台）。"""
    head = RULE_CATALOG[0]
    assert head.id == "standard"
    assert head.required is True
    assert head.rules is standard.RULES


def test_catalog_ids_are_unique_and_have_meta():
    """id は一意、各 spec は人間可読メタ（name/description）を持つ。"""
    ids = [s.id for s in RULE_CATALOG]
    assert len(ids) == len(set(ids)), f"id 重複: {ids}"
    for s in RULE_CATALOG:
        assert isinstance(s, RuleSpec)
        assert s.id and s.name and s.description, f"メタ不足: {s.id}"


def test_only_standard_is_required():
    """required はカタログ土台の standard だけ（ハウスルールは全て切替可）。"""
    assert [s.id for s in RULE_CATALOG if s.required] == ["standard"]


# --- registry() の後方互換 -------------------------------------------------


def test_enabled_rules_derived_from_defaults():
    """ENABLED_RULES は default=True の spec.rules をカタログ順に並べたもの。"""
    assert ENABLED_RULES == [s.rules for s in RULE_CATALOG if s.default]
    assert ENABLED_RULES[0] is standard.RULES


def test_registry_no_args_matches_all_defaults():
    """registry()（引数なし）は全 default を明示指定したものと同一挙動（後方互換）。"""
    all_default_ids = {s.id for s in RULE_CATALOG if s.default}
    assert _turn_after_reverse(registry()) == _turn_after_reverse(registry(all_default_ids))


# --- registry(enabled_ids) の部分集合構築 ----------------------------------


def test_registry_subset_enables_only_listed_rule():
    """reverse_off だけ指定すると reverse_off が効く（手番が p2 へ）。standard は自動で含む。"""
    assert _turn_after_reverse(registry({"reverse_off"})) == "p2"


def test_registry_empty_set_is_standard_only():
    """空集合＝standard のみ（reverse_off 無効＝2人リバースは連続手番で p1 のまま）。"""
    assert _turn_after_reverse(registry(set())) == "p1"


def test_registry_ignores_unknown_ids():
    """未知の id は無視される（standard のみが組まれ、reverse_off は効かない）。"""
    assert _turn_after_reverse(registry({"does_not_exist"})) == "p1"


# --- 配信メタ（catalog_meta / default_enabled_ids, #84） --------------------


def test_default_enabled_ids_matches_default_specs():
    """default_enabled_ids は default=True の id 集合（standard を含む）。"""
    assert default_enabled_ids() == frozenset(s.id for s in RULE_CATALOG if s.default)
    assert "standard" in default_enabled_ids()


def test_catalog_meta_shape_and_order():
    """catalog_meta は catalog 順の dict 列で、表示に必要なキーを全て持つ。"""
    meta = catalog_meta()
    assert [m["id"] for m in meta] == [s.id for s in RULE_CATALOG]  # 順序保存
    for m in meta:
        assert set(m) == {"id", "name", "section", "description", "required", "enabled"}


def test_catalog_meta_default_all_enabled():
    """引数なし（None）は全 default が enabled=True。"""
    assert all(m["enabled"] for m in catalog_meta())


def test_catalog_meta_reflects_enabled_subset():
    """enabled_ids を渡すと該当のみ enabled、required(standard) は常に enabled。"""
    meta = {m["id"]: m for m in catalog_meta({"reverse_off"})}
    assert meta["reverse_off"]["enabled"] is True
    assert meta["standard"]["enabled"] is True  # required は集合に無くても True
    assert meta["uno_call"]["enabled"] is False  # 集合外は False
    assert meta["standard"]["required"] is True


def _win_on_last_wild(reg) -> bool:
    """p1 が最後の1枚のワイルドを出して上がれるか（win_unrestricted の後勝ちを観測）。"""
    st = GameState(
        hands={"p1": (card(WILD, None, 1),), "p2": (card("9", Color.GREEN, 4),)},
        draw_pile=(),
        discard_pile=(card("7", Color.RED, 3),),
        current_player="p1",
        rng_state=random.Random(0).getstate(),
        awaiting={"p1": STANDARD_TURN_ACTIONS},
    )
    try:
        return apply_action(reg, st, PlayAction("p1", (1,))).winner == "p1"
    except IllegalAction:
        return False  # standard の no_win_on_wild が却下（win_unrestricted 無効）


def test_registry_preserves_catalog_order_for_override():
    """catalog 順で組むため win_unrestricted が standard を後勝ちで撤廃する（順序の実効検証）。

    standard は no_win_on_wild で「最後の1枚のワイルド」を却下するが、catalog 順で
    後段の win_unrestricted がそれを撤廃する。registry は set 入力でも catalog 順に
    積むので、win_unrestricted 有効時のみ上がれる＝順序が結果に効くことを担保する。
    """
    assert _win_on_last_wild(registry({"win_unrestricted"})) is True  # 後勝ちで撤廃
    assert _win_on_last_wild(registry(set())) is False  # standard のみ＝却下
