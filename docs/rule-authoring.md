# ローカルルール追加ガイド

local-UNO の中核目標は「ローカルルールをどんどん追加していく」こと。本書は、コアを改修せずに `rules/` 内でルールを足すための考え方と手順を示す。全体設計は [`spec.md`](./spec.md) を参照。

## 基本方針

- ルール追加は原則 **`rules/` 内で完結**させる。`engine/` は改修しない。
- ルールは「カード固有の効果」と「ゲーム全体の挙動・判定」の2種類に分けて表現する。
- 有効ルールは**順序付きリスト**で持ち、各フックを**先頭から順に**評価する（後勝ち）。
- 有効化リストは**起動時に固定**。順序は**記述順**で決まる。

## ルールの2つの差し込み口

### 1. カード固有の効果

新しい効果カードは、新しい **CardType** を定義して追加する。CardType は色・記号・表示ラベル・画像メタデータ・効果への参照を持つ。

- 画像は CardType のメタデータから自動生成される（未生成分のみ差分生成）。
- カードの一致判定ロジックは CardType に埋め込まない。判定は `can_play` フックで行う。

### 2. ゲーム全体の挙動・判定（フック）

以下のような横断的ルールはエンジンのフックにハンドラを登録して表現する。

- プレイ可能判定（`can_play`）
- プレイ前後・ドロー・ターン終了などのタイミング（効果適用）
- グローバル状態（累積ドロー枚数・強制色・方向）への作用
- 勝敗・得点、山切れ時の挙動
- 手番外アクション（ジャンプイン等）や応答待ちフェーズ（スタック等）：受理可能アクション集合を広げる

## フックの2分類と書き方

フックには型が2つある（spec.md §3.2）。ハンドラを登録する前に、どちらの型かを見極める。

### (A) 値リデューサ型 — ある1つの値を畳み込む

```
handler(現在値, ctx) -> 新しい値
```

- 対象: `can_play`(bool) / `pending_draw`(累積ドロー枚数, int) / `forced_color` / `direction` / `score` など。
- `現在値` は前のハンドラまでで確定した値（フックごとの初期値から始まる）。
- `ctx` は読み取り専用の評価文脈（対象 Action、評価対象カード、GameState 参照など）。

**例: ドロー2スタック**（累積は前値に加算）

```python
def draw2_stack(current, ctx):
    # current は前ルールまでで確定した累積ドロー枚数
    return current + 2
```

### (B) state トランスフォーマ型 — GameState 全体を変換する

```
handler(state, ctx) -> state
```

- 対象: `on_before_play` / `on_after_play` / `on_draw` / `on_turn_end` など、効果適用・手番送り・フェーズ遷移。
- 前のハンドラが返した state を次が受け取る。

## can_play の合成意味論（OR / AND）

`can_play` は bool 値リデューサで、**初期値は `False`**（何も許可しない）。許可を足すルールと制限を課すルールが混在するので、記述順で意味を作る。

**許可を足すルール**（OR 的に許可を追加）は、条件を満たせば `True` を返す。

```python
# 標準の一致判定（先頭ルール）
def standard_can_play(current, ctx):
    if current:                      # 既に誰かが許可済みならそのまま
        return True
    card, top = ctx.card, ctx.top_of_pile
    if card.is_wild:
        return True
    return card.color == top.color or card.symbol == top.symbol
```

```python
# ジャンプイン（手番外でも場と完全一致なら許可を追加）
def jump_in(current, ctx):
    if current:
        return True
    return same_card_type(ctx.card, ctx.top_of_pile)   # 色・数字・記号すべて一致
```

**制限を課すルール**（前がどうであれ却下）は、条件に反するとき `False` を返す。制限したい許可ルールより**後ろ**に置く。

```python
# 「ドロー4は他に出せる札が無いときのみ」
def draw4_only_when_no_alternative(current, ctx):
    if not current:                  # そもそも許可されていないなら関与しない
        return False
    if ctx.card.symbol != "draw4":
        return current               # ドロー4以外は素通し
    return not has_other_playable(ctx.hand, ctx.top_of_pile)
```

```python
# 「ドロー4にドロー2は乗せられない」（スタック時の相互制約）
def no_draw2_on_draw4(current, ctx):
    if not current:
        return False
    if ctx.card.symbol == "draw2" and ctx.top_of_pile.symbol == "draw4":
        return False                 # 却下
    return current
```

記述順の要点: `rules = [standard, draw2_stack, jump_in, no_draw2_on_draw4]` のように、**制限ルールを後ろ**に置くことで、前段の許可を確実に上書きできる。

## 応答待ち（色選択・スタック）の作り方

効果を1パスで完結できず、プレイヤー入力を待つ場合は `awaiting` を使う（spec.md §3.6）。

1. state トランスフォーマ型フックが、効果適用の途中で `awaiting`（受理可能アクション）を設定して state を返し、そこで停止する。
2. 対応する Action が来たら、継続フックが残りを適用し、`awaiting` を通常の手番に戻す。

```python
# ワイルドの色選択（標準）
def wild_effect(state, ctx):
    if not ctx.card.is_wild:
        return state
    # 効果を途中で止め、本人の色選択を待つ
    return state.with_awaiting(player=ctx.current_player,
                               allowed_actions=["choose_color"])

def on_choose_color(state, ctx):
    state = state.with_forced_color(ctx.action.color)
    return state.pass_turn()          # 手番を相手へ戻す
```

スタックやジャンプインも、同じ「`awaiting` を立てて停止 → Action で継続」のライフサイクルに載せる。

## 追加時のチェックリスト

1. 効果はカード固有か、横断的か？ → CardType 追加か、フック登録かを選ぶ。
2. 新カードを足したなら、CardType にメタデータを持たせたか？（画像は自動生成される）
3. 判定ロジックをカードに埋め込んでいないか？（判定は `can_play`）
4. そのフックは**値リデューサ型**か**state トランスフォーマ型**か？シグネチャは合っているか？
5. 累積が必要なら前値（`現在値`）を使っているか？ 制限ルールなら許可ルールより**後ろ**に置いたか？
6. 応答待ちが必要なら、`awaiting` を立てて停止し、継続フックで再開しているか？
7. 手番外アクションなら、受理可能アクション集合を正しく広げたか？
8. `engine/` を改修せず `rules/` 内で完結したか？
9. ルールの挙動を担保する pytest を書いたか？（`engine/` は RNG 注入で決定的なので、固定シード＋Action 列で再現テストが書ける）
