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

### (A) 値リデューサ型 — その場で1つの値を算出する（永続化しない）

```
handler(現在値, ctx) -> 新しい値
```

- 対象: `can_play`(bool) / `score`(得点) など、**一時的な判定・計算**のみ。
- `現在値` は**固定シード**（`can_play` は `False`、`score` は `0`）から始まり、毎回畳み込み直す。結果はその場で消費し、state フィールドへは書き戻さない。
- `ctx` は読み取り専用の評価文脈（`action` / `card` / `hand` / `top_of_pile` / `current_player` / `state`）。
- **永続フィールド（`pending_draw` / `forced_color` / `direction` / `current_player`）はここでは扱わない**。累積など「前の状態を引き継ぐ」ものは (B) で書く。

### (B) state トランスフォーマ型 — GameState 全体を変換する（永続フィールドの唯一の書き手）

```
handler(state, ctx) -> state
```

- 対象: `on_before_play` / `on_after_play` / `on_draw` / `on_turn_end` / `on_choose_color` など、効果適用・手番送り・フェーズ遷移。
- 前のハンドラが返した state を次が受け取る。永続フィールドの書き換えはすべてここで行う。

**例: 累積の書き方**（現在の state 値を読み、加算して書き戻す）

```python
# 「前の状態を引き継ぐ」累積は、現在値を読んで書き戻す（重ねるほど積み上がる）
def accumulate_draw2(state, ctx):
    if ctx.card.symbol != "draw2":
        return state
    return state.with_pending_draw(state.pending_draw + 2)
```

> 実装との対応（誤解防止）: 標準の Draw2 の累積（`+2`）は `rules/standard.py` の
> `apply_effect` が担う。ハウスルールの `rules/draw2_stack.py` は `pending_draw` を
> **触らず**、Draw2 を出された受け手の受理集合に `play` を足して「Draw2 で返せる」よう
> にするだけ（累積自体は standard 側で積み上がる）。上のコードは累積パターンの説明用。

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
2. 対応する Action が来たら、継続フックが残りを適用する。**手番送りはエンジンが担う**（応答待ちが解消され `awaiting` が空になると、エンジンが既定で相手へ手番を送る）。ルール側は「停止したい間だけ `awaiting` を立てる／自ターンを保持したいときは自分向けの `awaiting` を立てる」で制御する。

`awaiting` は `{player_id -> (許可アクション名, ...)}` のマップ。差し替えは `state.with_awaiting(...)` を使う（引数はマップ）。

```python
# ワイルドの色選択（標準）
def wild_effect(state, ctx):
    if not ctx.card.is_wild:
        return state
    # 効果を途中で止め、本人の色選択を待つ（自分向けに choose_color だけを受理集合へ）
    return state.with_awaiting({ctx.current_player: ("choose_color",)})

def on_choose_color(state, ctx):
    # 強制色を確定するだけ。awaiting が空に戻るのでエンジンが相手へ手番を送る。
    return state.with_forced_color(ctx.action.color)
```

> エンジンの手番送りの意味論（`engine/engine.py`）:
> - 効果適用後に `awaiting` が空なら、エンジンが二人対戦の既定手番送り（相手へ、受理集合を `("play", "draw")`）を行う。
> - `awaiting` が非空なら停止する（色選択待ちや、2人でのスキップ＝自分向けに `("play","draw")` を立て自ターンを保持、など）。
> - 終局は `state.with_winner(pid)` で表す（`winner` が立つとエンジンは手番送りしない）。
> - `pass_turn()` のようなヘルパは無い。手番は「`awaiting` をどう立てるか」で表現する。

スタックやジャンプインも、同じ「`awaiting` を立てて停止 → Action で継続」のライフサイクルに載せる。

## 追加時のチェックリスト

1. 効果はカード固有か、横断的か？ → CardType 追加か、フック登録かを選ぶ。
2. 新カードを足したなら、CardType にメタデータを持たせたか？（画像は自動生成される）
3. 判定ロジックをカードに埋め込んでいないか？（判定は `can_play`）
4. そのフックは**値リデューサ型**（一時的な判定・計算）か**state トランスフォーマ型**（永続フィールドの書き換え）か？シグネチャは合っているか？
5. 永続フィールド（`pending_draw`/`forced_color`/`direction`/`current_player`）を書くなら state トランスフォーマで行っているか（値リデューサで扱っていないか）？累積は現在の state 値を読んで書き戻しているか？ can_play の制限ルールは許可ルールより**後ろ**に置いたか？
6. 応答待ちが必要なら、`awaiting` を立てて停止し、継続フックで再開しているか？
7. 手番外アクションなら、受理可能アクション集合を正しく広げたか？
8. `engine/` を改修せず `rules/` 内で完結したか？
9. ルールの挙動を担保する pytest を書いたか？（`engine/` は RNG 注入で決定的なので、固定シード＋Action 列で再現テストが書ける）
