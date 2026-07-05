"""ルールセット（プラグイン）とルールカタログ（メタ＋有効化）。

標準UNO をリファレンス実装として置き、ローカルルールを**記述順**で積む（spec §3.3）。
順序は「後ろほど上書きが強く効く」（制限ルールは制限したい許可ルールより後ろへ）。

各ルールは :class:`RuleSpec` として id・人間可読な名前・章（docs/house-rules.md §）・
説明・フック実装（``RULES``）・``required``（standard は切替不可）・``default``（初期
有効か）を持つ。:data:`RULE_CATALOG` がこのメタ付きの**順序付きカタログ**で、設定画面
（#84/#85）はこのカタログを配信・選択に使う。

:func:`registry` は有効化するルール id の集合を受け、カタログ順にレジストリを組む。
引数を省略すると全 ``default=True`` を有効にする（＝従来の挙動・後方互換）。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from ..engine.hooks import HookRegistry, Rule, build_registry
from . import (
    draw2_stack,
    draw_after_play,
    jump_in,
    multi_play,
    reverse_off,
    stalemate,
    standard,
    uno_call,
    win_unrestricted,
)
from .standard import setup_game


# eq=False: 参照同一性で扱う（RULE_CATALOG はシングルトン）。frozen=True は属性の
# 再束縛を防ぐだけで、rules（dict）は unhashable かつ中身は可変＝浅い不変性である点に注意。
@dataclass(frozen=True, eq=False)
class RuleSpec:
    """1ルールのメタ情報とフック実装。カタログ順が合成の適用順（後ろほど上書きが強い）。

    - ``id``: 安定した識別子（設定の受け渡しキー。モジュール名に一致）。
    - ``name``: 人間可読な名前（設定画面の見出し）。
    - ``section``: docs/house-rules.md の章参照（例 ``§1``。無い場合は空文字）。
    - ``description``: 一行の説明（設定画面の補足）。
    - ``rules``: 各モジュールの ``RULES`` dict（フック→ハンドラ）。
    - ``required``: 常時必須で切替不可か（``standard`` の土台）。
    - ``default``: 未指定時（``registry()`` 引数なし）に有効にするか。
    """

    id: str
    name: str
    section: str
    description: str
    rules: Rule
    required: bool = False
    default: bool = True


# 順序付きルールカタログ（記述順＝合成の適用順）。先頭は必ず standard（required）。
# 以降はハウスルール（docs/house-rules.md）を記述順に積む（後ろほど上書きが強い）。
RULE_CATALOG: list[RuleSpec] = [
    RuleSpec(
        id="standard",
        name="標準 UNO",
        section="",
        description="標準 UNO の土台（プレイ可否・効果・色指定・得点）。常時有効。",
        rules=standard.RULES,
        required=True,
    ),
    RuleSpec(
        id="reverse_off",
        name="リバース＝無効",
        section="§1",
        description="リバースを効果なしの通常カードとして扱う（手番は通常どおり相手へ）。",
        rules=reverse_off.RULES,
    ),
    RuleSpec(
        id="win_unrestricted",
        name="上がり制限なし",
        section="§5",
        description="最後の1枚に Wild / Wild Draw4 を含む任意のカードで上がれる。",
        rules=win_unrestricted.RULES,
    ),
    RuleSpec(
        id="draw2_stack",
        name="Draw2 スタック",
        section="§3",
        description="Draw2 を出された側は Draw2 を重ねて返せる（枚数分累積）。",
        rules=draw2_stack.RULES,
    ),
    RuleSpec(
        id="multi_play",
        name="複数枚出し",
        section="§2",
        description="同じ数字または同じ記号のカードを複数枚まとめて出せる。",
        rules=multi_play.RULES,
    ),
    RuleSpec(
        id="uno_call",
        name="UNO 宣言＋指摘",
        section="§6",
        description="手札1枚で「UNO!」宣言が必須。宣言忘れの指摘・誤宣言はペナルティ。",
        rules=uno_call.RULES,
    ),
    RuleSpec(
        id="jump_in",
        name="ジャンプイン",
        section="",
        description="手番外でも場のトップと完全一致するカードなら割り込んで出せる。",
        rules=jump_in.RULES,
    ),
    RuleSpec(
        id="draw_after_play",
        name="ドロー後プレイ／自主ドロー",
        section="§7",
        description="手番中に山から1枚引ける。引いた札が合法ならそのまま出せる。",
        rules=draw_after_play.RULES,
    ),
    RuleSpec(
        id="stalemate",
        name="山切れ引き分け",
        section="§8",
        description="山切れで両者とも出せず進行不能なら、勝敗をつけず引き分けで終局。",
        rules=stalemate.RULES,
    ),
]

# 後方互換: 従来の「有効化リスト（記述順の RULES 列）」。default=True のもののみ。
# 既存の参照（build_registry(ENABLED_RULES) 等）をそのまま生かす。
ENABLED_RULES = [spec.rules for spec in RULE_CATALOG if spec.default]


def registry(enabled_ids: Iterable[str] | None = None) -> HookRegistry:
    """フック実行器を組み立てる（カタログ順を保存）。

    ``enabled_ids`` を省略すると全 ``default=True`` を有効にする（従来の挙動）。
    指定すると、カタログ順に ``required`` または id が集合に含まれるルールだけを積む
    （``standard`` 等の ``required`` は集合に無くても必ず含む・未知の id は無視）。
    """
    if enabled_ids is None:
        rules = ENABLED_RULES
    else:
        wanted = set(enabled_ids)
        rules = [s.rules for s in RULE_CATALOG if s.required or s.id in wanted]
    return build_registry(rules)


__all__ = [
    "RuleSpec",
    "RULE_CATALOG",
    "ENABLED_RULES",
    "registry",
    "setup_game",
    "standard",
    "reverse_off",
    "win_unrestricted",
    "draw2_stack",
    "multi_play",
    "uno_call",
    "jump_in",
    "draw_after_play",
    "stalemate",
]
