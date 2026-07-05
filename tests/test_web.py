"""web/ フロントの試験（issue #15）。

バニラ JS は JS ランタイムを持たない（spec §9: npm 不使用）ため、ここでは Python 側で
「app が web/ を実配信する」ことと「app.js が必須の結線を持ち、ゲームロジックを持たない」
ことを構造検証する。2ブラウザでの即時反映・リロード復帰は #16 E2E / 手動で担保する。
"""

from __future__ import annotations

import itertools
import json
import re

import pytest
from fastapi.testclient import TestClient

from lUNO.server.app import WEB_DIR, create_app
from lUNO.server.session import Session

APP_JS = (WEB_DIR / "app.js").read_text(encoding="utf-8")
INDEX = (WEB_DIR / "index.html").read_text(encoding="utf-8")
STYLE_CSS = (WEB_DIR / "style.css").read_text(encoding="utf-8")


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


def deterministic_client(seed: int = 1) -> TestClient:
    counter = itertools.count(1)
    session = Session(seed=seed, token_factory=lambda: f"tok{next(counter)}")
    return TestClient(create_app(session=session))


# --- 実配信 -----------------------------------------------------------------


def test_serves_index_app_js_style(client: TestClient):
    assert client.get("/").status_code == 200
    assert client.get("/app.js").status_code == 200
    assert client.get("/style.css").status_code == 200


def test_index_references_assets():
    assert "/app.js" in INDEX
    assert "/style.css" in INDEX


def test_all_getelementbyid_targets_exist_in_index():
    """app.js が getElementById で参照する全 ID が index.html に存在する（#89）。

    リデザイン等で index.html の構造を変えても、app.js の結線先 ID が失われれば
    実行時に getElementById(...).xxx が TypeError になる。従来は一部 ID しか検証して
    おらず、多くの ID は削除しても素通りした（テスト有効性の穴）。ここで app.js の
    参照する全 ID を index.html に対して機械的に担保する。
    """
    referenced = set(re.findall(r'getElementById\("([a-zA-Z0-9_-]+)"\)', APP_JS))
    assert referenced, "app.js に getElementById 参照が見つからない（正規表現要確認）"
    missing = sorted(rid for rid in referenced if f'id="{rid}"' not in INDEX)
    assert not missing, f"app.js が参照するが index.html に無い ID: {missing}"


# --- 必須の結線（薄いフロントの契約） --------------------------------------


def test_app_js_connects_websocket_to_ws():
    assert "WebSocket" in APP_JS
    assert "/ws" in APP_JS


def test_app_js_persists_reconnect_token_in_localstorage():
    assert "localStorage" in APP_JS
    assert "setItem" in APP_JS
    assert "getItem" in APP_JS


def test_app_js_renders_playerview_and_uses_card_images():
    assert "function render" in APP_JS
    assert "/cards/" in APP_JS  # カード画像は static PNG を参照（§7）
    assert "image_key" in APP_JS
    assert "your_hand" in APP_JS


def test_your_turn_highlights_own_zone():
    """自分の番（body[data-turn="you"]）のとき自分ゾーン .you を枠強調する（#101）。
    判定は app.js が body.dataset.turn にセット済みで、強調は CSS のみで行う。"""
    # フロントは手番を body の data-turn に反映している（"you" を出す）
    assert "dataset.turn" in APP_JS
    assert '"you"' in APP_JS
    # CSS 側: 自分の番のとき自分ゾーンを強調するセレクタが存在する
    assert 'body[data-turn="you"] .you' in STYLE_CSS
    # reduced-motion では明滅アニメを止める配慮がある
    assert "prefers-reduced-motion" in STYLE_CSS


def test_app_js_sends_actions_as_json():
    assert "JSON.stringify" in APP_JS
    for action_type in ("play", "draw", "pass", "choose_color", "reset", "new_game"):
        assert action_type in APP_JS


# --- ロジックをフロントに持たない（送信＋描画のみ） -------------------------


def test_app_js_has_no_engine_logic():
    """プレイ可否・シャッフル・累積等のルール/エンジン実装をフロントに持たない。"""
    forbidden = ["can_play", "shuffle", "108", "pending_draw +"]
    for token in forbidden:
        assert token not in APP_JS, f"フロントにロジックが混入: {token!r}"


def test_app_js_play_uses_card_ids_list():
    """play は card_ids（リスト）で送る（サーバの PlayAction 仕様と一致, #35）。"""
    assert "card_ids" in APP_JS
    assert "card_id:" not in APP_JS  # 旧単数フィールドを送っていない


def test_index_has_play_button():
    """複数枚出しをまとめて確定する「出す」ボタンがある（#62）。"""
    assert 'id="play-btn"' in INDEX


def test_app_js_supports_multi_select_play():
    """複数枚出し UI: タップで選択トグルし、選択順に card_ids でまとめて出す（#62）。"""
    # タップ順を送信順として保持するトグル実装を実際に要求する
    assert "toggleSelect" in APP_JS
    assert "state.selected.push" in APP_JS  # 末尾追加＝送信順を保持
    assert "state.selected.indexOf" in APP_JS  # 選択済み判定→トグル off
    assert 'classList.add("selected")' in APP_JS  # 選択ハイライトの結線
    # 「出す」ボタンで選択配列をそのまま card_ids に載せて送る
    assert "play-btn" in APP_JS
    assert "playSelected" in APP_JS
    assert "card_ids: state.selected" in APP_JS
    # クリックで即 1 枚送る旧挙動を残していない（選択→出す に一本化）
    assert "card_ids: [card.id]" not in APP_JS


def test_app_js_shows_selection_order_and_count():
    """選択順の可視化（先頭/末尾/連番バッジ）・枚数表示・空選択の無効化（#63）。"""
    # 順序バッジと先頭/末尾ラベル（roleLabel の返り値リテラルに厳密一致させ、
    # コメント文字列での偽陽性を避ける）
    assert "order-badge" in APP_JS
    assert "roleLabel" in APP_JS
    assert '"先頭=トップ"' in APP_JS  # 単数選択（リード兼トップ）
    assert '"先頭"' in APP_JS  # リード（場に合わせる札）
    assert '"トップ"' in APP_JS  # 出した後の新しい捨て札
    # 「出す（N枚）」の枚数表示
    assert "出す（" in APP_JS
    # 「出す」ボタンの有効/無効を一元管理（自分の番 state.canPlay かつ 1 枚以上）
    assert "updatePlayButton" in APP_JS
    assert "state.canPlay" in APP_JS


def test_index_has_pass_button():
    """ドロー後にパスを送る導線（パスボタン）が UI にある（#64）。"""
    assert 'id="pass-btn"' in INDEX


def test_app_js_pass_button_wired_and_gated_by_awaiting():
    """パスボタンが pass を送り（click 結線）、awaiting に応じて活性制御される（#64）。

    サーバは自主ドロー後に awaiting=[play, pass] にして手番を保持する。この pass を
    送る手段が UI に無いと、引いた札を出せない時に手番を進められず詰む（回帰防止）。

    gating（無効化）と wiring（click→送信）を**分離して**検証する。無効化行
    ``pass-btn").disabled = !allowed.includes("pass")`` は文字列 ``"pass"`` /
    ``getElementById("pass-btn")`` / ``includes("pass")`` を単独で満たすため、緩い
    部分一致では click ハンドラが消えても素通りする（本 PR の主眼である結線退行を
    捕捉できない）。そこで各行に特異なパターンで照合する。
    """
    # gating: awaiting に pass が無ければ無効化（draw/play ボタンと同じ受理集合連動）
    assert re.search(
        r'getElementById\("pass-btn"\)\.disabled\s*=\s*!allowed\.includes\("pass"\)', APP_JS
    )
    # wiring: pass-btn の click ハンドラが存在する（無効化行だけでは満たせない）
    assert re.search(r'getElementById\("pass-btn"\)\.addEventListener\(\s*"click"', APP_JS)
    # wiring: その送信ペイロードが pass アクション（オブジェクトリテラル形。
    # includes("pass") とは別物で、click 結線が消えれば失われる）
    assert re.search(r'type:\s*"pass"', APP_JS)


def test_app_js_shows_draw_banner():
    """山切れ引き分け（is_draw）を banner に表示する結線がある（#74）。

    winner だけでなく is_draw 分岐で banner を出す。終局として UNO 系ボタンも隠す。
    """
    assert "is_draw" in APP_JS
    assert "引き分け" in APP_JS


def test_index_has_rematch_button_in_banner():
    """終局バナー内に再戦ボタンがある（#99）。"""
    assert 'id="rematch-btn"' in INDEX
    assert 'id="banner-msg"' in INDEX
    # 再戦ボタンはバナー要素の内側に置く（終局時のみ表示される導線）。banner は main
    # 末尾要素なので、banner 開始タグより後・</main> より前にあることを確認する。
    banner_open = INDEX.index('id="banner"')
    main_close = INDEX.index("</main>")
    assert banner_open < INDEX.index('id="rematch-btn"') < main_close
    assert banner_open < INDEX.index('id="banner-msg"') < main_close


def test_app_js_rematch_wired_to_reset():
    """再戦ボタンの click で reset を送る（現在のルール構成のまま再配札, #99）。"""
    assert re.search(
        r'getElementById\("rematch-btn"\)\.addEventListener\(\s*"click"', APP_JS
    )
    assert re.search(r'type:\s*"reset"', APP_JS)
    # 勝敗メッセージは banner-msg に入れる（banner.textContent だと再戦ボタンが消える）
    assert re.search(r'getElementById\("banner-msg"\)', APP_JS)


def test_index_has_cutin_element():
    """カットイン用の要素が UI にある（#97）。"""
    assert 'id="cutin"' in INDEX


def test_app_js_cutin_wired_to_last_event():
    """state の last_event でカットインを出す結線がある（#97）。

    サーバが載せる出来事（UNO!/指摘/強制ドロー）を state メッセージで受けて showCutIn
    を呼ぶ。welcome では呼ばない（再接続時の再演回避）。文言・色は last_event の kind から。
    """
    assert "last_event" in APP_JS
    assert "showCutIn" in APP_JS
    assert "cutinContent" in APP_JS
    # state 分岐で last_event を条件に showCutIn を呼ぶ結線
    assert re.search(r'"state"[\s\S]{0,400}last_event[\s\S]{0,80}showCutIn', APP_JS)
    # 主要な出来事の kind を扱っている
    for kind in ("uno", "challenge_success", "challenge_misfire", "forced_draw"):
        assert f'"{kind}"' in APP_JS


def test_index_has_uno_button():
    """「UNO!」宣言ボタンが UI にある（#70）。"""
    assert 'id="uno-btn"' in INDEX


def test_app_js_declare_uno_wired_and_shown_during_play():
    """UNO! は対局中いつでも押せ（誤宣言可）、declare_uno を送る（#70, #79, #80）。

    house-rules §6 の誤宣言ペナルティ（手札1枚でない宣言＝本人が2枚ドロー, #79）を
    UI から到達可能にするため、手札枚数によらず対局中は常時表示し、終局 (over) の
    ときだけ隠す（指摘ボタン #76 と対称）。成否・ペナルティはサーバが判定（サーバ権威）。
    """
    # wiring: uno-btn の click ハンドラで declare_uno を送る
    assert re.search(r'getElementById\("uno-btn"\)\.addEventListener\(\s*"click"', APP_JS)
    assert re.search(r'type:\s*"declare_uno"', APP_JS)
    # gating: uno-btn の hidden は終局(over)のみで制御する
    assert re.search(
        r'toggleClass\(\s*[^;]*?uno-btn[^;]*?"hidden"[^;]*?over', APP_JS, re.S
    )
    # 手札枚数(myCount)や宣言済み(uno_declared)には紐付けない（誤宣言を許容＝
    # 1枚のときだけの安全ゲートを廃止）
    assert not re.search(r'toggleClass\(\s*[^;]*?uno-btn[^;]*?myCount', APP_JS, re.S)
    assert not re.search(r'toggleClass\(\s*[^;]*?uno-btn[^;]*?declared', APP_JS, re.S)


def test_index_has_rules_panel():
    """ローカルルール設定パネルの受け皿と新規ゲームボタンが UI にある（#84/#85）。"""
    assert 'id="rules-list"' in INDEX
    assert 'id="new-game-btn"' in INDEX


def test_app_js_renders_rules_from_welcome():
    """welcome の rules を受け取り renderRules で設定パネルへ描画する（#84）。

    rules は welcome（と new_game 後の state）に載るメタ。フロントは表示・選択の送信のみ
    （有効/無効の判定はサーバ権威）。描画の結線を検証する。
    """
    assert re.search(r"msg\.rules", APP_JS)
    assert re.search(r"renderRules\(", APP_JS)
    assert re.search(r'getElementById\("rules-list"\)', APP_JS)
    assert re.search(r"\.enabled", APP_JS)
    assert re.search(r"\.section", APP_JS)


def test_app_js_rules_panel_uses_checkboxes():
    """設定パネルは各ルールをチェックボックスにし、required は disabled にする（#85）。"""
    assert re.search(r'type\s*=\s*"checkbox"', APP_JS) or re.search(
        r'\.type\s*=\s*"checkbox"', APP_JS
    )
    assert re.search(r"\.required", APP_JS)  # required を disabled 判定に使う
    assert re.search(r"ruleId", APP_JS)  # 各チェックに rule id を紐付ける


def test_app_js_new_game_wired_with_selected_rule_ids():
    """新規ゲームボタンでチェック済み id を集め new_game を送る（#85）。"""
    assert re.search(
        r'getElementById\("new-game-btn"\)\.addEventListener\(\s*"click"', APP_JS
    )
    assert re.search(r'type:\s*"new_game"', APP_JS)
    assert re.search(r"enabled_rule_ids", APP_JS)


def test_app_js_updates_panel_on_new_game_state():
    """new_game 後の state に rules が載っていれば設定パネルを更新する（#85）。

    通常の手番更新（rules 無し）では再描画せず、途中のチェック操作を保持する。
    """
    # state 分岐で msg.rules を条件に renderRules を呼ぶ結線がある
    assert re.search(r'"state"[\s\S]{0,200}msg\.rules', APP_JS)


def test_app_js_rules_panel_has_reorder_controls():
    """順序編集: 上下移動ボタンと、after 依存を使った移動可否判定がある（#93）。"""
    assert "moveRule" in APP_JS
    assert re.search(r"canMoveUp", APP_JS) and re.search(r"canMoveDown", APP_JS)
    # 依存（after）を参照して移動可否を判定する（違反移動の無効化）
    assert re.search(r"\.after", APP_JS)
    # required は移動不可
    assert re.search(r"\.required", APP_JS)


def test_app_js_reorder_gates_on_enabled_neighbor():
    """移動可否は隣の enabled を見て判定する（無効な隣は依存の壁にしない, #93）。

    無効ルールは送信されずサーバ order_violations の対象外なので、UI 側も無効な隣を
    越える移動を許す（母集合＝送信対象＝有効ルールを一致させる）。
    """
    assert re.search(r"canMoveUp[\s\S]{0,160}\.enabled", APP_JS)
    assert re.search(r"canMoveDown[\s\S]{0,160}\.enabled", APP_JS)
    # チェック変更をメタに同期して再描画する（移動間もチェックを保持）
    assert re.search(r'addEventListener\(\s*"change"[\s\S]{0,120}\.enabled\s*=', APP_JS)


def test_app_js_new_game_sends_ids_in_display_order():
    """new_game は現在の並び順（DOM 順）でチェック済み id を送る（#93）。

    move で並べ替えた順が enabled_rule_ids に載る。querySelectorAll は DOM 順で走る。
    """
    assert re.search(r'querySelectorAll\("#rules-list[^"]*"\)', APP_JS)
    assert re.search(r"enabled_rule_ids", APP_JS)


def test_index_has_challenge_button():
    """「UNO言ってない!」指摘ボタンが UI にある（#71）。"""
    assert 'id="challenge-btn"' in INDEX


def test_app_js_challenge_uno_wired_and_shown_during_play():
    """指摘は対局中いつでも押せ（誤爆可）、challenge_uno を送る（#71, #76）。

    house-rules §6 の駆け引き（リスクを負って指摘。該当しない相手を突けば誤爆で
    自分が2枚ドロー）を再現するため、相手の枚数によらず対局中は常時表示し、終局
    (over) のときだけ隠す。成否・ペナルティはサーバが判定（サーバ権威）。
    """
    # wiring: challenge-btn の click ハンドラで challenge_uno を送る
    assert re.search(
        r'getElementById\("challenge-btn"\)\.addEventListener\(\s*"click"', APP_JS
    )
    assert re.search(r'type:\s*"challenge_uno"', APP_JS)
    # gating: challenge-btn の hidden は終局(over)のみで制御する
    assert re.search(
        r'toggleClass\(\s*[^;]*?challenge-btn[^;]*?"hidden"[^;]*?over', APP_JS, re.S
    )
    # 相手枚数(oppCount)には紐付けない（誤爆を許容＝該当時のみの安全ゲートを廃止）
    assert not re.search(r'toggleClass\(\s*[^;]*?challenge-btn[^;]*?oppCount', APP_JS, re.S)


# --- WS 往復（フロントが送る実ペイロードがサーバと整合するか） ---------------


def _play_payload_from_app_js(view_card_id: int) -> dict:
    """app.js が play クリックで送る形をそのまま再現（プロトコル整合の担保）。"""
    return {"type": "play", "player": "p1", "card_ids": [view_card_id]}


def test_ws_frontend_play_payload_is_accepted_by_server():
    """フロントの play ペイロード形がサーバに受理され、状態がブロードキャストされること。

    Blocker 回帰防止: card_id（旧単数）だと ActionError になる。card_ids で通る。
    """
    client = deterministic_client()
    with client.websocket_connect("/ws") as ws1:
        welcome = ws1.receive_json()
        assert welcome["type"] == "welcome"
        with client.websocket_connect("/ws") as ws2:
            ws2.receive_json()
            # welcome.view.your_hand から出せる札を探し、標準 can_play に通る1枚を出す
            top = welcome["view"]["top_of_pile"]
            playable = _find_playable(welcome["view"]["your_hand"], top)
            if playable is None:
                pytest.skip("この seed では初手に出せる札が無い")
            ws1.send_text(json.dumps(_play_payload_from_app_js(playable["id"])))
            msg = ws1.receive_json()
            assert msg["type"] == "state"  # error でなく state が返る
            # 完成条件1: 相手画面へ即反映（p1 の1枚出しが p2 の視界に載る）
            s2 = ws2.receive_json()
            assert s2["type"] == "state"
            assert s2["view"]["hand_counts"]["p1"] == 6  # 7 → 6 に反映


def test_ws_old_card_id_payload_would_error():
    """旧 card_id 形はサーバに弾かれる（Blocker の存在を明示的に固定）。"""
    client = deterministic_client()
    with client.websocket_connect("/ws") as ws1:
        w = ws1.receive_json()
        cid = w["view"]["your_hand"][0]["id"]
        ws1.send_text(json.dumps({"type": "play", "player": "p1", "card_id": cid}))
        assert ws1.receive_json()["type"] == "error"


def test_ws_multi_card_play_is_accepted_and_last_becomes_top():
    """複数枚出し（card_ids に複数ID）が WS 経由で受理され、末尾カードがトップになること。

    engine/rules/actions では #35 で対応済みだが、WS(session→app) を通す結合テストが
    欠けていた。ここで「複数 card_ids を送れる」ことに加え、card_ids の順序契約
    （先頭＝リード・末尾＝新トップ, house-rules §2 / multi_play）を固定する。
    """
    # seed を固定して決定的に検証する。群が見つからなければ skip で素通りさせず
    # 失敗させる（deck/ルール退行でカバレッジが全損した場合に表面化させるため）。
    client = deterministic_client(seed=1)
    with client.websocket_connect("/ws") as ws1:
        welcome = ws1.receive_json()
        group = _find_multi_group(
            welcome["view"]["your_hand"], welcome["view"]["top_of_pile"]
        )
        assert group is not None, (
            "seed=1 の初手に複数枚出し可能な群が無い（deck 変更時は seed を見直す）"
        )
        with client.websocket_connect("/ws") as ws2:
            ws2.receive_json()
            ws1.send_text(json.dumps({"type": "play", "player": "p1", "card_ids": group}))
            msg = ws1.receive_json()
            assert msg["type"] == "state"  # error でなく state が返る
            # 順序契約: 末尾に置いたカードが新しい捨て山トップになる
            assert msg["view"]["top_of_pile"]["id"] == group[-1]
            # 相手視界へ即反映: p1 の手札は 7 → 5（2枚出し）
            s2 = ws2.receive_json()
            assert s2["type"] == "state"
            assert s2["view"]["hand_counts"]["p1"] == 7 - len(group)


def test_ws_pass_after_voluntary_draw_advances_turn():
    """自主ドロー後、フロントが送る pass ペイロードで手番が相手へ進む（#64 E2E）。

    パスの導線が無いと、引いた札が場に出せない時に手番を進められず詰む。ここで
    UI が送る ``{type:"pass"}`` がサーバに受理され、手番が相手へ送られることを固定する。
    """
    client = deterministic_client()
    with client.websocket_connect("/ws") as ws1:
        w1 = ws1.receive_json()
        assert w1["view"]["current_player"] == "p1"
        with client.websocket_connect("/ws") as ws2:
            ws2.receive_json()
            # p1 が自主ドロー（1枚）→ 手番保持のまま awaiting に pass が入る
            ws1.send_text(json.dumps({"type": "draw", "player": "p1"}))
            after_draw = ws1.receive_json()
            ws2.receive_json()
            assert after_draw["view"]["current_player"] == "p1"
            assert "pass" in after_draw["view"]["awaiting"]["p1"]
            # UI が送る pass ペイロードで手番が p2 へ進む
            ws1.send_text(json.dumps({"type": "pass", "player": "p1"}))
            passed = ws1.receive_json()
            ws2.receive_json()
            assert passed["type"] == "state"
            assert passed["view"]["current_player"] == "p2"


def test_ws_declare_uno_misfire_penalizes_declarer():
    """初手7枚（=手札1枚でない）で declare_uno を送ると誤宣言になり、送信者が
    2枚引く（#70, #79, #80）。

    declare_uno は awaiting 非依存の常時受理アクション。初手7枚での宣言は誤宣言
    （手札1枚でない）＝本人が2枚ドロー（7→9）。宣言は成立せず uno_declared にも
    載らない。WS パス＋誤宣言ペナルティ配線を固定（#80 で UI から到達可能）。
    """
    client = deterministic_client()
    with client.websocket_connect("/ws") as ws1:
        w1 = ws1.receive_json()
        assert len(w1["view"]["your_hand"]) == 7
        with client.websocket_connect("/ws") as ws2:
            ws2.receive_json()
            ws1.send_text(json.dumps({"type": "declare_uno", "player": "p1"}))
            msg = ws1.receive_json()
            assert msg["type"] == "state"  # error でなく state（常時受理）
            # 誤宣言: 送信者 p1 にペナルティ2枚（7→9）、宣言は不成立
            assert msg["view"]["hand_counts"]["p1"] == 9
            assert "p1" not in msg["view"]["uno_declared"]


def test_ws_challenge_uno_misfire_penalizes_challenger():
    """相手が1枚でない初期に challenge_uno を送ると誤爆し、送信者が2枚引く（#71）。

    challenge_uno は awaiting 非依存の常時受理。初期は相手7枚で指摘は不成立（誤爆）
    のため challenger 本人にペナルティ2枚（7→9）が付く。WS パス＋ペナルティ配線を固定。
    """
    client = deterministic_client()
    with client.websocket_connect("/ws") as ws1:
        w1 = ws1.receive_json()
        assert len(w1["view"]["your_hand"]) == 7
        with client.websocket_connect("/ws") as ws2:
            ws2.receive_json()
            ws1.send_text(json.dumps({"type": "challenge_uno", "player": "p1"}))
            msg = ws1.receive_json()
            assert msg["type"] == "state"  # 受理される
            # 誤爆: 送信者 p1 にペナルティ2枚（7→9）
            assert msg["view"]["hand_counts"]["p1"] == 9


def test_ws_reconnect_restores_hand_via_token():
    """完成条件2: ?token= 再接続で手札が復元される（実挙動）。"""
    client = deterministic_client()
    with client.websocket_connect("/ws") as ws:
        w = ws.receive_json()
        token = w["token"]
        ws.send_text(json.dumps({"type": "draw", "player": "p1"}))
        ws.receive_json()
    with client.websocket_connect(f"/ws?token={token}") as ws2:
        w2 = ws2.receive_json()
        assert w2["player_id"] == "p1"
        assert len(w2["view"]["your_hand"]) == 8


def _find_playable(hand, top):
    """標準 can_play 相当（テスト補助）。色/記号一致 or ワイルド。"""
    for c in hand:
        if c["color"] is None:  # wild
            return c
        if top is not None and (c["color"] == top["color"] or c["symbol"] == top["symbol"]):
            return c
    return None


def _find_multi_group(hand, top):
    """先頭が top に合法（非ワイルド）で、同記号の仲間が1枚以上ある群を探す。

    返り値は出す順の card_id リスト ``[先頭, 仲間]``。無ければ None。
    複数枚出しは「全カードが先頭と同記号」（house-rules §2 / multi_play）。
    ワイルド先頭は choose_color を要するためテストでは避ける。
    """
    for lead in hand:
        if lead["color"] is None:  # wild は避ける
            continue
        if top is None or not (
            lead["color"] == top["color"] or lead["symbol"] == top["symbol"]
        ):
            continue
        mates = [c for c in hand if c["id"] != lead["id"] and c["symbol"] == lead["symbol"]]
        if mates:
            return [lead["id"], mates[0]["id"]]
    return None
