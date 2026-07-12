"""カード出しアニメ（Fever 限定, #119）の静的資産マーカー試験。

Fever モードと同様に純フロント機能なのでブラウザ挙動そのものは単体テストできない。実際に
配信される web 資産（app.js / style.css）に、機能の骨格となる識別子（差分検出・種別判定・
登場アニメ・全画面バースト・盤面シェイク）が含まれることを回帰ガードとして担保する。配信
経路は本番と同じ StaticFiles を通す。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from lUNO.server.app import WEB_DIR, create_app
from lUNO.server.session import Session


def make_client(tmp_path) -> TestClient:
    """本番の web ディレクトリ（WEB_DIR）をそのまま配信するクライアント。"""
    session = Session(seed=1)
    cards = tmp_path / "cards"
    return TestClient(create_app(session=session, web_dir=WEB_DIR, cards_dir=cards))


def test_app_js_detects_play_and_fires_effects(tmp_path):
    """app.js がすて札トップの変化を差分検出し、Fever 限定でカード出し演出を発火する。"""
    client = make_client(tmp_path)
    js = client.get("/app.js").text
    assert "lastTopId" in js  # すて札トップの差分検出キー
    assert "fever-played" in js  # 登場アニメ用クラスの付与
    assert "screenBurst" in js  # Draw2/Draw4 の全画面バースト
    assert '"draw2"' in js and '"draw4"' in js  # card.symbol による種別判定
    assert "feverOn()" in js  # Fever ON 時のみ発火
    assert "isAppend" in js  # 追記のみを play とみなし reset/new_game の開始札を弾く


def test_app_js_guards_play_fx_context(tmp_path):
    """演出は state 受信時のみ・reduced-motion では抑制する配線がある。"""
    client = make_client(tmp_path)
    js = client.get("/app.js").text
    # welcome は false（復元では出さない）・state は true（手番更新で発火）
    assert "render(msg.view, false)" in js
    assert "render(msg.view, true)" in js
    # screenBurst 冒頭の reduced-motion ガード（confetti と同じ流儀）
    assert "prefers-reduced-motion" in js


def test_style_css_defines_play_fx(tmp_path):
    """style.css が着地アニメ・全画面バースト・シェイクを定義している。"""
    client = make_client(tmp_path)
    css = client.get("/style.css").text
    # 出した人で方向分け（自分＝下から / 相手＝上から）の別キーフレーム（#121）
    assert "@keyframes fever-land-self" in css
    assert "@keyframes fever-land-opp" in css
    assert "from-self" in css and "from-opp" in css  # 付け分けクラスのスタイル
    assert ".fx-burst" in css  # 全画面バーストのレイヤー
    assert "@keyframes fx-fly" in css  # 破片が四方八方へ飛ぶ
    assert "@keyframes fx-shake" in css  # Draw4 の盤面シェイク
    assert 'data-fever="on"' in css  # 着地アニメは Fever スコープ


def test_app_js_picks_play_direction_by_hand_count(tmp_path):
    """出した人（枚数が減ったプレイヤー）で登場方向クラスを付け分ける（#121）。"""
    client = make_client(tmp_path)
    js = client.get("/app.js").text
    assert "lastHandCounts" in js  # 前 render の手札枚数スナップショット
    assert "from-self" in js and "from-opp" in js  # 自分/相手の方向クラス
