// local-UNO フロントエンド（バニラ JS・ビルド無し, spec §9）。
//
// 役割は薄く保つ: サーバから届く PlayerView を丸ごと描き直し、クリック等の入力を
// Action の JSON として送るだけ。プレイ可否・効果・手番などの判定は一切持たない
// （サーバ権威, 原則1）。出せない札を送ってもサーバが error を返すだけ。
//
// - WebSocket で ws に接続。再接続トークンは localStorage に保存し、次回接続時にクエリで渡す。
// - カード画像は cards/<image_key>.png を <img> で参照（§7）。
//
// URL はすべて**ベース相対**（先頭スラなし）で組み立て、ドキュメントの base（現在ページの
// ディレクトリ）に解決させる。これにより root 直配信（http://host:8000/）でもリバース
// プロキシのサブパス配信（http://host/luno/）でも同じ JS が動く。先頭スラ付きの
// 絶対パスだとサブパス配下でサイト root に飛び 404 になる。

"use strict";

const TOKEN_KEY = "luno_token";

const state = {
  ws: null,
  me: null, // 自分の player_id（welcome で確定）
  view: null, // 最後に受け取った PlayerView
  retry: 0, // 再接続のバックオフ用
  stop: false, // 満席等で再接続を止めるフラグ
  // 複数枚出しの選択（タップ順＝送信順を保持する card_id の配列）。
  // 先頭＝リード（場に合法な必要あり）、末尾＝出した後の新しい捨て山トップ。
  // サーバから新しい state を受けて再描画するたびにクリアする（stale 防止）。
  selected: [],
  // 直近の awaiting に play が含まれるか（「出す」ボタンの有効条件の一つ）。
  canPlay: false,
  // 有効ローカルルールのメタ（welcome で一度届く。確認パネルの表示用, #84）。
  rules: null,
  // 対戦相手の接続待ちか（待機ゲート #115）。true の間は操作をブロックする。
  waiting: false,
  // 参加者リセットで退席させられたか（#115）。true なら自動再接続せずリロードを促す。
  evicted: false,
};

// --- 通信 ------------------------------------------------------------------

function wsUrl() {
  const token = localStorage.getItem(TOKEN_KEY);
  const q = token ? "?token=" + encodeURIComponent(token) : "";
  // "ws" をドキュメント base に相対解決する（root なら /ws、/luno/ 配下なら /luno/ws）。
  // その後 http(s) → ws(s) にスキームだけ差し替える。
  const u = new URL("ws" + q, document.baseURI);
  u.protocol = location.protocol === "https:" ? "wss:" : "ws:";
  return u.href;
}

function connect() {
  const ws = new WebSocket(wsUrl());
  state.ws = ws;
  ws.onopen = () => {
    state.retry = 0;
    setStatus("接続済み");
  };
  ws.onclose = () => {
    if (state.evicted) {
      // 参加者リセットで退席させられた側は自動再接続しない（席を奪わないため, #115）。
      setStatus("退席しました");
      return;
    }
    if (state.stop) {
      setStatus("接続できません（満席の可能性）");
      return;
    }
    // 指数バックオフ（最大 10 秒）で自動再接続（トークンで手札復帰）
    const delay = Math.min(1000 * 2 ** state.retry, 10000);
    state.retry += 1;
    setStatus("切断。再接続します…");
    setTimeout(connect, delay);
  };
  ws.onmessage = (ev) => {
    let msg;
    try {
      msg = JSON.parse(ev.data);
    } catch (_e) {
      return; // 不正データは無視（サーバは常に JSON を送る）
    }
    handleMessage(msg);
  };
}

function handleMessage(msg) {
  if (msg.type === "welcome") {
    localStorage.setItem(TOKEN_KEY, msg.token); // 再接続トークンを保存
    state.me = msg.player_id;
    if (msg.rules) {
      state.rules = msg.rules; // 有効ルールのメタ（確認パネル用, #84）
      renderRules(msg.rules);
    }
    render(msg.view);
    // 対戦相手の接続待ちなら待機オーバーレイを出す（待機ゲート, #115）。
    state.waiting = Boolean(msg.waiting_for_opponent);
    updateGate();
  } else if (msg.type === "state") {
    // new_game 後の state はルールメタを同梱する。届いたときだけ設定パネルを更新
    // （通常の手番更新では送られないので、途中のチェック操作を消さない, #85）。
    if (msg.rules) {
      state.rules = msg.rules;
      renderRules(msg.rules);
    }
    render(msg.view);
    // 直近アクションの出来事（UNO!/指摘/強制ドロー）をカットインで見せる（#97）。
    // welcome（再接続）では出さない＝古い出来事の再演を避ける。
    if (msg.view && msg.view.last_event) showCutIn(msg.view.last_event, state.me);
    // 相手の参加/退席で待機状態が変わる（#115）。state ごとに反映しゲートを開閉する。
    state.waiting = Boolean(msg.waiting_for_opponent);
    updateGate();
  } else if (msg.type === "evicted") {
    // 参加者リセットで退席（#115）。この直後サーバが close するので、再接続停止フラグを
    // 立てて（onclose で自動再接続しない）「参加するにはリロード」を表示する。先着の
    // 別ブラウザに席を渡すため、ここで席を取りにいかない。
    state.evicted = true;
    state.stop = true;
    updateGate();
  } else if (msg.type === "error") {
    setStatus("エラー: " + msg.message);
    // 満席（3人目以降）はサーバが close する。無限リトライを止める。
    if (msg.message && msg.message.indexOf("満席") !== -1) state.stop = true;
  }
}

// 待機/退席オーバーレイ（#115）を状態から開閉する。退席（evicted）が最優先で、
// 次に対戦相手の接続待ち（waiting）。どちらでもなければ隠して盤面を操作可能にする。
function updateGate() {
  const gate = document.getElementById("gate");
  const msg = document.getElementById("gate-msg");
  const reload = document.getElementById("gate-reload");
  if (!gate || !msg || !reload) return;
  if (state.evicted) {
    // 退席時はセッション終了なので全面を覆う（ヘッダー/設定も含めブロック）。
    msg.textContent = "参加者がリセットされました";
    toggleClass(reload, "hidden", false); // リロードで再参加する導線
    toggleClass(gate, "cover-all", true);
    toggleClass(gate, "hidden", false);
  } else if (state.waiting) {
    // 待機中は盤面のみブロック。ヘッダー/ルール設定は操作可能に残す（reset/new_game 免除）。
    msg.textContent = "対戦相手の接続を待っています…";
    toggleClass(reload, "hidden", true);
    toggleClass(gate, "cover-all", false);
    toggleClass(gate, "hidden", false);
  } else {
    toggleClass(gate, "cover-all", false);
    toggleClass(gate, "hidden", true);
  }
}

// Action は JSON を送るだけ（ロジックはサーバ側）。
function send(action) {
  if (state.ws && state.ws.readyState === WebSocket.OPEN) {
    state.ws.send(JSON.stringify(action));
  }
}

// --- 描画（PlayerView → 画面）: 純粋に view から DOM を作り直す ------------

function cardImg(card) {
  const img = document.createElement("img");
  img.className = "card";
  // ベース相対（先頭スラなし）。img.src は自動で document base に解決される
  // （root→/cards/…、/luno/ 配下→/luno/cards/…）。
  img.src = "cards/" + card.image_key + ".png";
  img.alt = card.label;
  img.title = card.label;
  return img;
}

// ローカルルール設定パネルを描画（#84 確認 ＋ #85 設定 ＋ #93 順序編集）。welcome または
// new_game 後の state で受けたメタ配列を**現在の評価順**で並べ、各ルールを ON/OFF チェック
// ボックス＋上下移動ボタンにする。required（standard）は常時 ON・移動不可・先頭固定。
// 前後依存（after）を破る移動はボタンを無効化する（最終判定はサーバ権威, 原則1）。
// チェック・並びは「新規ゲーム」まではローカル。ここは表示と選択の送信のみ。
function renderRules(rules) {
  const list = document.getElementById("rules-list");
  if (!list) return;
  const items = rules || [];
  list.replaceChildren();
  items.forEach((r, i) => {
    const li = document.createElement("li");
    li.className = "rule-item";
    // 上下移動（依存を破る移動・required・端は無効化。判定はサーバでも再検証）
    const move = document.createElement("span");
    move.className = "rule-move";
    move.appendChild(moveButton("▲", "上へ", canMoveUp(items, i), () => moveRule(i, -1)));
    move.appendChild(moveButton("▼", "下へ", canMoveDown(items, i), () => moveRule(i, 1)));
    const label = document.createElement("label");
    label.className = "rule-label";
    const box = document.createElement("input");
    box.type = "checkbox";
    box.className = "rule-check";
    box.dataset.ruleId = r.id;
    box.checked = Boolean(r.enabled);
    box.disabled = Boolean(r.required); // standard は外せない
    // チェックはメタに同期し再描画（移動の間もチェックを保持し、可否判定の母集合＝
    // 送信対象＝有効ルールを一致させる）。判定はサーバ権威、ここは選択の記録のみ。
    box.addEventListener("change", () => {
      r.enabled = box.checked;
      renderRules(state.rules);
    });
    const name = document.createElement("span");
    name.className = "rule-name";
    const sec = r.section ? r.section + " " : "";
    name.textContent = sec + r.name;
    label.appendChild(box);
    label.appendChild(name);
    const head = document.createElement("div");
    head.className = "rule-head";
    head.appendChild(move);
    head.appendChild(label);
    const desc = document.createElement("span");
    desc.className = "rule-desc";
    desc.textContent = r.description;
    li.appendChild(head);
    li.appendChild(desc);
    list.appendChild(li);
  });
}

function moveButton(glyph, title, enabled, onClick) {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "move-btn";
  btn.textContent = glyph;
  btn.title = title;
  btn.disabled = !enabled;
  if (enabled) btn.addEventListener("click", onClick);
  return btn;
}

// i を上へ動かせるか。先頭/required は不可。**無効（未チェック）な隣は送信されず順序
// 制約を課さない**ため壁にしない（サーバ order_violations と母集合を一致させる）。隣が
// 有効で、かつ i の依存先（after）なら、上げると「依存先より前」になり違反するので不可。
function canMoveUp(items, i) {
  if (i <= 0 || items[i].required || items[i - 1].required) return false;
  if (!items[i - 1].enabled) return true; // 無効な隣は越えても送信順に影響しない
  return !(items[i].after || []).includes(items[i - 1].id);
}

// i を下へ動かせるか。末尾/required は不可。無効な隣は壁にしない。隣（i+1）が有効で i を
// 依存先に持つ（after に i）なら、下げると i+1 が i より前になり違反するので不可。
function canMoveDown(items, i) {
  if (i >= items.length - 1 || items[i].required || items[i + 1].required) return false;
  if (!items[i + 1].enabled) return true; // 無効な隣は越えても送信順に影響しない
  return !(items[i + 1].after || []).includes(items[i].id);
}

// 表示中の順序（state.rules）で隣と入れ替え、再描画する（サーバ往復なしのローカル操作）。
// state.rules を意図的に in-place で並べ替える（他に消費者は無く renderRules が同参照を
// 読み直す。送信は DOM 走査で現在の並び順を拾う）。
function moveRule(i, delta) {
  const items = state.rules;
  const j = i + delta;
  if (!items || j < 0 || j >= items.length) return;
  const tmp = items[i];
  items[i] = items[j];
  items[j] = tmp;
  renderRules(items);
}

// チェック済みのルール id を**現在の並び順**で集め、その構成で新規ゲームを開始する（#85/#93）。
// required（disabled かつ checked）も含めて送るが、standard はサーバ側で常に先頭・有効。
function startNewGame() {
  const ids = [];
  document.querySelectorAll("#rules-list .rule-check").forEach((box) => {
    if (box.checked) ids.push(box.dataset.ruleId);
  });
  send({ type: "new_game", player: state.me, enabled_rule_ids: ids });
}

function render(view) {
  state.view = view;
  const me = state.me;
  const opponent = me === "p1" ? "p2" : "p1";

  document.getElementById("me").textContent = me || "";

  // 相手（枚数のみ）
  const oppCount = (view.hand_counts && view.hand_counts[opponent]) || 0;
  document.getElementById("opponent-count").textContent = oppCount + " 枚";

  // 場: すて札は直近数枚を出した順（古い→新しい）に重ねて描く。相手の複数枚出しでも
  // 何をどの順で出したか分かるようにする（#111）。最新（末尾）が前面。
  const discard = document.getElementById("discard-top");
  discard.replaceChildren();
  const pile =
    view.recent_discards && view.recent_discards.length
      ? view.recent_discards
      : view.top_of_pile
        ? [view.top_of_pile] // 後方互換: recent_discards が無ければトップ1枚
        : [];
  for (const card of pile) {
    const el = cardImg(card);
    el.classList.add("discard-card");
    discard.appendChild(el);
  }
  document.getElementById("draw-count").textContent = view.draw_count + " 枚";
  const forced = document.getElementById("forced-color");
  forced.textContent = view.forced_color || "-";
  forced.dataset.color = view.forced_color || "";

  // 新しいサーバ状態を受けたので、前回の選択は破棄（stale 防止, サーバ権威）。
  state.selected = [];

  // 自分の手札（タップで選択トグル。可否判定はサーバ）
  const hand = document.getElementById("hand");
  hand.replaceChildren();
  (view.your_hand || []).forEach((card) => {
    // カードは wrap で包み、選択順バッジ（先頭/末尾/連番）を重ねる（#63）。
    const wrap = document.createElement("div");
    wrap.className = "card-wrap";
    wrap.dataset.cardId = String(card.id);
    const el = cardImg(card);
    // タップ＝選択トグル。まとめて出すのは「出す」ボタン（複数枚出し, #35/#62）。
    el.addEventListener("click", () => toggleSelect(card.id));
    const badge = document.createElement("span");
    badge.className = "order-badge hidden";
    wrap.appendChild(el);
    wrap.appendChild(badge);
    hand.appendChild(wrap);
  });

  // 受理集合に応じて操作可否と色選択の表示を切り替える（判定ではなく UI のみ）
  const allowed = (view.awaiting && view.awaiting[me]) || [];
  document.getElementById("draw-btn").disabled = !allowed.includes("draw");
  state.canPlay = allowed.includes("play");
  // パスはドロー後フェーズ（awaiting に pass）でのみ活性。引いた札を出さず手番を送る。
  document.getElementById("pass-btn").disabled = !allowed.includes("pass");
  toggleClass(document.getElementById("color-picker"), "hidden", !allowed.includes("choose_color"));
  // 選択（クリア済み）を反映して「出す」ボタンとバッジを初期化。
  refreshSelectionUI();

  // UNO 宣言/指摘は awaiting に載らない常時受理アクション。判定・ペナルティは
  // サーバ（サーバ権威）。UI は対局中は常時表示し、終局時のみ隠す。
  const over = Boolean(view.winner) || Boolean(view.is_draw);
  // 「UNO!」は対局中いつでも押せる（house-rules §6 の誤宣言＝2枚ドロー, #79/#80）。
  // 手札1枚・未宣言なら正当宣言、1枚・宣言済みは no-op、2枚以上は誤宣言でペナルティ
  // （すべてサーバが判定）。
  toggleClass(document.getElementById("uno-btn"), "hidden", over);
  // 「UNO言ってない!」（指摘）も対局中いつでも可能にする（house-rules §6 の駆け引き）。
  // 相手が該当しないのに突けば誤爆で自分が2枚ドロー、正しく突けば相手が2枚。成否と
  // ペナルティはサーバが判定（サーバ権威）。UI は終局時のみ隠す。
  toggleClass(document.getElementById("challenge-btn"), "hidden", over);

  // 手番をひと目で分かるよう body に印を付ける（style.css が強調表示に使う）。
  document.body.dataset.turn = over ? "over" : view.current_player === me ? "you" : "other";

  // 手番・勝敗の表示。終局時はバナー＋再戦ボタンを出す（メッセージは banner-msg に
  // 入れる。banner 自体を textContent で書くと再戦ボタンが消えるため触らない）。
  const banner = document.getElementById("banner");
  const bannerMsg = document.getElementById("banner-msg");
  if (view.winner) {
    bannerMsg.textContent = view.winner === me ? "あなたの勝ち！" : "あなたの負け…";
    toggleClass(banner, "hidden", false);
    setStatus("終局");
  } else if (view.is_draw) {
    bannerMsg.textContent = "山切れ — 引き分け";
    toggleClass(banner, "hidden", false);
    setStatus("終局");
  } else {
    bannerMsg.textContent = ""; // 再戦後に前回終局の文言を残さない（非表示だが掃除）
    toggleClass(banner, "hidden", true);
    setStatus(view.current_player === me ? "あなたの番" : "相手の番");
  }
}

// --- 選択（複数枚出し）: 送信順を保持するトグル ----------------------------

// タップされたカードを選択リストに足す/外す。タップ順＝送信順（先頭=リード,
// 末尾=トップ）。一度外して再度タップすると末尾に付き直す。可否判定はサーバ。
function toggleSelect(cardId) {
  const idx = state.selected.indexOf(cardId);
  if (idx === -1) {
    state.selected.push(cardId);
  } else {
    state.selected.splice(idx, 1);
  }
  refreshSelectionUI();
}

// 選択順における各位置のラベル。先頭＝場に合わせるリード、末尾＝出した後に
// 新しい捨て山トップになる札。中間は連番で並びを示す。
function roleLabel(pos, total) {
  if (total === 1) return "先頭=トップ";
  if (pos === 0) return "先頭";
  if (pos === total - 1) return "トップ";
  return String(pos + 1);
}

// 選択状態（ハイライト・順序バッジ・「出す」ボタン）を現在の state.selected から
// 描き直す。full render はしない（サーバ状態でのみ選択はクリアされる）。
function refreshSelectionUI() {
  const total = state.selected.length;
  document.querySelectorAll("#hand .card-wrap").forEach((wrap) => {
    const id = Number(wrap.dataset.cardId);
    const pos = state.selected.indexOf(id);
    const img = wrap.querySelector(".card");
    const badge = wrap.querySelector(".order-badge");
    if (pos === -1) {
      img.classList.remove("selected");
      badge.textContent = "";
      toggleClass(badge, "hidden", true);
    } else {
      img.classList.add("selected");
      badge.textContent = roleLabel(pos, total);
      toggleClass(badge, "hidden", false);
    }
  });
  updatePlayButton();
}

// 「出す」ボタンの表示（選択枚数）と有効/無効（自分の番かつ 1 枚以上選択）。
function updatePlayButton() {
  const n = state.selected.length;
  const btn = document.getElementById("play-btn");
  btn.textContent = n > 0 ? `出す（${n}枚）` : "出す";
  btn.disabled = !state.canPlay || n === 0;
}

// 選択したカードを選択順に card_ids へ入れて出す。空なら何もしない。
function playSelected() {
  if (state.selected.length === 0) return;
  send({ type: "play", player: state.me, card_ids: state.selected.slice() });
}

// --- カットイン演出（直近アクションの出来事を一瞬大きく見せる, #97） ---------

// last_event（サーバが載せる出来事）を、見る人（me）視点の文言に変換する。
// tone は色味: cheer=宣言 / good=自分に有利 / bad=自分が損 / neutral。
function cutinContent(ev, me) {
  const who = (p) => (p === me ? "あなた" : "あいて");
  const onMe = (p) => p === me;
  switch (ev.kind) {
    case "uno":
      return { title: "UNO!", sub: who(ev.by) + "が宣言", tone: "cheer" };
    case "uno_misfire":
      return { title: "誤宣言…", sub: who(ev.target) + " +" + ev.amount + "枚", tone: onMe(ev.target) ? "bad" : "good" };
    case "challenge_success":
      return { title: "UNO言ってない!", sub: "指摘成功！ " + who(ev.target) + " +" + ev.amount + "枚", tone: onMe(ev.target) ? "bad" : "good" };
    case "challenge_misfire":
      return { title: "UNO言ってない!", sub: "お手つき… " + who(ev.target) + " +" + ev.amount + "枚", tone: onMe(ev.target) ? "bad" : "good" };
    case "forced_draw":
      return { title: "+" + ev.amount, sub: who(ev.target) + "がドロー", tone: onMe(ev.target) ? "bad" : "neutral" };
    case "win_streak":
      // 連勝カットインは勝った本人にだけ出す（相手側は演出しない）。
      return onMe(ev.by) ? { title: ev.amount + "連勝!", sub: "勝ち続け中！", tone: "cheer" } : null;
    default:
      return null;
  }
}

let cutinTimer = null;
function showCutIn(ev, me) {
  const el = document.getElementById("cutin");
  if (!el || !ev) return;
  // 待機/退席オーバーレイ表示中はカットインを出さない（ゲートより前面に出さない, #115）。
  if (state.waiting || state.evicted) return;
  const c = cutinContent(ev, me);
  if (!c) return;
  // 文言は固定文＋数値＋あなた/あいて のみ（外部入力なし）。
  el.innerHTML =
    '<div class="cutin-card"><div class="cutin-title"></div><div class="cutin-sub"></div></div>';
  el.querySelector(".cutin-title").textContent = c.title;
  el.querySelector(".cutin-sub").textContent = c.sub;
  el.className = "cutin hidden tone-" + c.tone;
  void el.offsetWidth; // reflow でアニメーションを毎回リスタート
  el.classList.remove("hidden");
  clearTimeout(cutinTimer);
  cutinTimer = setTimeout(() => el.classList.add("hidden"), 1200);
}

// --- テーマ（ライト/ダーク） -----------------------------------------------

// 既定は OS 設定に追従（CSS の prefers-color-scheme）。ボタンで data-theme を
// 上書きし localStorage に保存する。ゲーム状態には無関係な純粋な表示設定。
const THEME_KEY = "luno_theme";
function applyTheme(pref) {
  const root = document.documentElement;
  if (pref === "light" || pref === "dark") root.setAttribute("data-theme", pref);
  else root.removeAttribute("data-theme");
  const btn = document.getElementById("theme-btn");
  if (btn) {
    const dark = root.getAttribute("data-theme") === "dark"
      || (!root.hasAttribute("data-theme") && matchMedia("(prefers-color-scheme: dark)").matches);
    btn.textContent = dark ? "☀️" : "🌙";
  }
}

// --- 入力ハンドラ（送信のみ） ---------------------------------------------

function wireControls() {
  document.getElementById("play-btn").addEventListener("click", playSelected);
  document.getElementById("draw-btn").addEventListener("click", () => {
    send({ type: "draw", player: state.me });
  });
  document.getElementById("pass-btn").addEventListener("click", () => {
    send({ type: "pass", player: state.me });
  });
  document.getElementById("uno-btn").addEventListener("click", () => {
    send({ type: "declare_uno", player: state.me });
  });
  document.getElementById("challenge-btn").addEventListener("click", () => {
    send({ type: "challenge_uno", player: state.me });
  });
  document.getElementById("reset-btn").addEventListener("click", () => {
    send({ type: "reset", player: state.me });
  });
  // 参加者リセット（#115）: 相手を退席させる破壊的操作なので確認を挟む。OK なら
  // reset_players を送る。サーバが相手席を解放し、こちらは在席のまま待機表示になる。
  document.getElementById("reset-players-btn").addEventListener("click", () => {
    if (window.confirm("対戦相手を退席させて参加者をリセットします。よろしいですか？")) {
      send({ type: "reset_players", player: state.me });
    }
  });
  // 退席オーバーレイのリロードボタン: 明示リロードで空席に再入場する（先着, #115）。
  document.getElementById("gate-reload").addEventListener("click", () => {
    location.reload();
  });
  // 終局バナー内の再戦ボタン。現在のルール構成のまま再配札する（reset, §8）。
  document.getElementById("rematch-btn").addEventListener("click", () => {
    send({ type: "reset", player: state.me });
  });
  document.getElementById("new-game-btn").addEventListener("click", startNewGame);
  const themeBtn = document.getElementById("theme-btn");
  if (themeBtn) {
    themeBtn.addEventListener("click", () => {
      const root = document.documentElement;
      const cur = root.getAttribute("data-theme");
      const dark = cur ? cur === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
      const next = dark ? "light" : "dark";
      localStorage.setItem(THEME_KEY, next);
      applyTheme(next);
    });
  }
  document.querySelectorAll(".color-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      send({ type: "choose_color", player: state.me, color: btn.dataset.color });
    });
  });
}

// --- ユーティリティ --------------------------------------------------------

function setStatus(text) {
  document.getElementById("status").textContent = text;
}

function toggleClass(el, cls, on) {
  if (on) el.classList.add(cls);
  else el.classList.remove(cls);
}

window.addEventListener("DOMContentLoaded", () => {
  applyTheme(localStorage.getItem(THEME_KEY));
  // OS のライト/ダーク変更に追従（data-theme 未設定＝自動追従のときアイコンも更新）。
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
    if (!document.documentElement.hasAttribute("data-theme")) applyTheme(null);
  });
  wireControls();
  connect();
});
