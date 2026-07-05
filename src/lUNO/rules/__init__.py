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

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

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
    - ``after``: このルールより**前**に来るべき依存先 id 集合（前後制約）。合成は
      「後ろほど上書きが強い」ため、制限ルールは制限対象の許可ルールより後ろに置く
      必要がある等の制約をここに明記する。順序編集（#92/#93）はこの制約を破れない。
    """

    id: str
    name: str
    section: str
    description: str
    rules: Rule
    required: bool = False
    default: bool = True
    after: frozenset[str] = field(default_factory=frozenset)


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
        after=frozenset({"standard"}),  # ON_AFTER_PLAY: standard の awaiting を打ち消す
    ),
    RuleSpec(
        id="win_unrestricted",
        name="上がり制限なし",
        section="§5",
        description="最後の1枚に Wild / Wild Draw4 を含む任意のカードで上がれる。",
        rules=win_unrestricted.RULES,
        after=frozenset({"standard"}),  # CAN_PLAY: no_win_on_wild を後勝ちで撤廃
    ),
    RuleSpec(
        id="draw2_stack",
        name="Draw2 スタック",
        section="§3",
        description="Draw2 を出された側は Draw2 を重ねて返せる（枚数分累積）。",
        rules=draw2_stack.RULES,
        # CAN_PLAY 制限は許可（standard/win_unrestricted）より後ろ必須
        after=frozenset({"standard", "win_unrestricted"}),
    ),
    RuleSpec(
        id="multi_play",
        name="複数枚出し",
        section="§2",
        description="同じ数字または同じ記号のカードを複数枚まとめて出せる。",
        rules=multi_play.RULES,
        after=frozenset({"standard"}),  # ON_AFTER_PLAY: standard の効果を前提に累積
    ),
    RuleSpec(
        id="uno_call",
        name="UNO 宣言＋指摘",
        section="§6",
        description="手札1枚で「UNO!」宣言が必須。宣言忘れの指摘・誤宣言はペナルティ。",
        rules=uno_call.RULES,
        after=frozenset({"standard"}),  # ON_AFTER_PLAY/ON_DRAW: standard の後で整理
    ),
    RuleSpec(
        id="jump_in",
        name="ジャンプイン",
        section="",
        description="手番外でも場のトップと完全一致するカードなら割り込んで出せる。",
        rules=jump_in.RULES,
        # CAN_PLAY/CAN_STACK 制限は許可ルール（standard/win_unrestricted/multi_play）の後ろ必須
        after=frozenset({"standard", "win_unrestricted", "multi_play"}),
    ),
    RuleSpec(
        id="draw_after_play",
        name="ドロー後プレイ／自主ドロー",
        section="§7",
        description="手番中に山から1枚引ける。引いた札が合法ならそのまま出せる。",
        rules=draw_after_play.RULES,
        # CAN_PLAY 制限（引いた札のみリード可）は許可ルールの後ろ必須
        after=frozenset({"standard", "win_unrestricted"}),
    ),
    RuleSpec(
        id="stalemate",
        name="山切れ引き分け",
        section="§8",
        description="山切れで両者とも出せず進行不能なら、勝敗をつけず引き分けで終局。",
        rules=stalemate.RULES,
        # ON_TURN_END: jump_in の割り込み枠復活を上書きするため jump_in より後ろ（末尾）必須
        after=frozenset({"standard", "jump_in"}),
    ),
]

# 後方互換: 従来の「有効化リスト（記述順の RULES 列）」。default=True のもののみ。
# 既存の参照（build_registry(ENABLED_RULES) 等）をそのまま生かす。
ENABLED_RULES = [spec.rules for spec in RULE_CATALOG if spec.default]


_CATALOG_BY_ID: dict[str, RuleSpec] = {s.id: s for s in RULE_CATALOG}


def default_enabled_ids() -> frozenset[str]:
    """未指定時（``registry()`` 引数なし）に有効となるルール id 集合（``default=True``）。"""
    return frozenset(s.id for s in RULE_CATALOG if s.default)


def _ordered_specs(order: Sequence[str] | None) -> list[RuleSpec]:
    """メタ表示用のルール並び。``order`` が無ければカタログ順。

    ``order`` 指定時は ``required``（standard）を先頭に、続いて ``order`` の順、最後に
    未掲載（無効ルール等）をカタログ順で並べる。未知 id は無視する。
    """
    if order is None:
        return list(RULE_CATALOG)
    result: list[RuleSpec] = []
    seen: set[str] = set()
    for s in RULE_CATALOG:  # required（standard）は常に先頭
        if s.required:
            result.append(s)
            seen.add(s.id)
    for rid in order:
        s = _CATALOG_BY_ID.get(rid)
        if s is not None and s.id not in seen:
            result.append(s)
            seen.add(s.id)
    for s in RULE_CATALOG:  # 残り（無効・未掲載）はカタログ順で末尾に
        if s.id not in seen:
            result.append(s)
            seen.add(s.id)
    return result


def order_violations(ordered_ids: Sequence[str]) -> list[tuple[str, str]]:
    """順序が ``after`` 前後制約を満たすか検査し、違反の ``(rule, 依存先)`` 組を返す。

    与えられた並びの中に両方存在する依存関係のみを見る（無効化された依存先は無関係）。
    空リストなら妥当。順序編集（#93）と ``Session._new_game`` の検証に使う。
    """
    pos = {rid: i for i, rid in enumerate(ordered_ids)}
    violations: list[tuple[str, str]] = []
    for rid in ordered_ids:
        spec = _CATALOG_BY_ID.get(rid)
        if spec is None:
            continue
        for dep in spec.after:
            # dep も並びに含まれ、かつ dep が rid より後ろなら制約違反
            if dep in pos and pos[dep] > pos[rid]:
                violations.append((rid, dep))
    return violations


def catalog_meta(
    enabled_ids: Iterable[str] | None = None,
    order: Sequence[str] | None = None,
) -> list[dict[str, object]]:
    """設定・確認画面向けにカタログを配信用 dict 列へ変換する。

    ``enabled_ids`` は現在有効な id 集合（``None`` で全 default）。``order`` を渡すと
    その並び（required 先頭・未掲載は末尾）で返す（``None`` はカタログ順）。各要素は有効か
    （``required`` は常に有効）を ``enabled``、前後依存を ``after`` として持つ。判定はサーバ
    権威なのでフロントは表示・送信のみ（``after`` は移動可否の UI 補助に使う, #93）。
    """
    enabled = default_enabled_ids() if enabled_ids is None else set(enabled_ids)
    return [
        {
            "id": s.id,
            "name": s.name,
            "section": s.section,
            "description": s.description,
            "required": s.required,
            "enabled": s.required or s.id in enabled,
            "after": sorted(s.after),
        }
        for s in _ordered_specs(order)
    ]


def registry(enabled_ids: Iterable[str] | None = None) -> HookRegistry:
    """フック実行器を組み立てる。

    - ``None``: 全 ``default=True`` を有効（従来の挙動・後方互換）。
    - **list/tuple（順序付き）**: 与えられた順で積む。``required``（standard）を先頭に
      補完し、未知 id は無視する。順序が結果に効くため呼び出し側は妥当な順序（
      :func:`order_violations` が空）を渡すこと。
    - **set/frozenset ほか**: カタログ順に ``required`` または集合に含まれる id を積む
      （順序の意味を持たない従来の集合指定・後方互換）。
    """
    if enabled_ids is None:
        rules = ENABLED_RULES
    elif isinstance(enabled_ids, (list, tuple)):
        specs = _ordered_specs(enabled_ids)
        wanted = set(enabled_ids)
        rules = [s.rules for s in specs if s.required or s.id in wanted]
    else:
        wanted = set(enabled_ids)
        rules = [s.rules for s in RULE_CATALOG if s.required or s.id in wanted]
    return build_registry(rules)


__all__ = [
    "RuleSpec",
    "RULE_CATALOG",
    "ENABLED_RULES",
    "default_enabled_ids",
    "catalog_meta",
    "order_violations",
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
