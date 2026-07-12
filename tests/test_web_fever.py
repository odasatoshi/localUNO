"""Fever モード（やりすぎド派手演出のトグル, #116）の静的資産マーカー試験。

Fever は純フロントエンド機能（サーバ／ゲームロジックは不変）なので、ブラウザ挙動その
ものは単体テストできない。代わりに **実際に配信される** web 資産（index.html / app.js /
style.css）に、機能の骨格となるマーカー（切替ボタン・JS 配線・CSS）が含まれることを担保し、
うっかり削除・リグレッションを検知する。配信経路は本番と同じ StaticFiles を通す。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from lUNO.server.app import WEB_DIR, create_app
from lUNO.server.session import Session


def make_client(tmp_path) -> TestClient:
    """本番の web ディレクトリ（WEB_DIR）をそのまま配信するクライアント。

    カード画像ディレクトリはテスト用に空の tmp を渡す（Fever には無関係）。
    """
    session = Session(seed=1)
    cards = tmp_path / "cards"
    return TestClient(create_app(session=session, web_dir=WEB_DIR, cards_dir=cards))


def test_index_has_fever_toggle_button(tmp_path):
    """topbar に Fever 切替ボタン（#fever-btn）があり、初期 aria-pressed は false。"""
    client = make_client(tmp_path)
    html = client.get("/").text
    assert 'id="fever-btn"' in html
    assert 'aria-pressed="false"' in html  # 初期状態は OFF


def test_app_js_wires_fever_toggle_and_persistence(tmp_path):
    """app.js が Fever をトグル・永続化・復元する配線を持つ。

    リファクタで落ちない程度に、内部呼び出しの正確な字面ではなく「契約となる語彙」の
    存在で確認する（localStorage キー・data-fever・関数・紙吹雪）。
    """
    client = make_client(tmp_path)
    js = client.get("/app.js").text
    assert "luno_fever" in js  # localStorage キー（永続化）
    assert "applyFever" in js  # data-fever と見た目を反映する関数
    assert "data-fever" in js  # documentElement に立てる属性
    assert "confettiBurst" in js  # ON の瞬間の紙吹雪演出
    assert "feverOn" in js  # 保存済み設定の読み出し（復元）


def test_style_css_defines_fever_effects(tmp_path):
    """style.css が data-fever スコープの派手演出を定義している。"""
    client = make_client(tmp_path)
    css = client.get("/style.css").text
    assert '[data-fever="on"]' in css  # スコープセレクタ
    assert "@keyframes fever-bg" in css  # 虹色の動く背景
    assert "@keyframes confetti-fall" in css  # 紙吹雪の落下
    assert "#fever-btn" in css  # ボタンのスタイル
