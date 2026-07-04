// local-UNO フロントエンド（バニラ JS・ビルド無し, spec §9）。
//
// 役割は薄く保つ: サーバから届く PlayerView を丸ごと描き直し、クリック等の入力を
// Action の JSON として送るだけ。プレイ可否・効果・手番などの判定は一切持たない
// （サーバ権威, 原則1）。出せない札を送ってもサーバが error を返すだけ。
//
// - WebSocket で /ws に接続。再接続トークンは localStorage に保存し、次回接続時にクエリで渡す。
// - カード画像は /cards/<image_key>.png を <img> で参照（§7）。

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
};

// --- 通信 ------------------------------------------------------------------

function wsUrl() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const token = localStorage.getItem(TOKEN_KEY);
  const q = token ? "?token=" + encodeURIComponent(token) : "";
  return proto + "://" + location.host + "/ws" + q;
}

function connect() {
  const ws = new WebSocket(wsUrl());
  state.ws = ws;
  ws.onopen = () => {
    state.retry = 0;
    setStatus("接続済み");
  };
  ws.onclose = () => {
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
    render(msg.view);
  } else if (msg.type === "state") {
    render(msg.view);
  } else if (msg.type === "error") {
    setStatus("エラー: " + msg.message);
    // 満席（3人目以降）はサーバが close する。無限リトライを止める。
    if (msg.message && msg.message.indexOf("満席") !== -1) state.stop = true;
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
  img.src = "/cards/" + card.image_key + ".png";
  img.alt = card.label;
  img.title = card.label;
  return img;
}

function render(view) {
  state.view = view;
  const me = state.me;
  const opponent = me === "p1" ? "p2" : "p1";

  document.getElementById("me").textContent = me || "";

  // 相手（枚数のみ）
  const oppCount = (view.hand_counts && view.hand_counts[opponent]) || 0;
  document.getElementById("opponent-count").textContent = oppCount + " 枚";

  // 場
  const discard = document.getElementById("discard-top");
  discard.replaceChildren();
  if (view.top_of_pile) discard.appendChild(cardImg(view.top_of_pile));
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
    const el = cardImg(card);
    // タップ＝選択トグル。まとめて出すのは「出す」ボタン（複数枚出し, #35/#62）。
    el.addEventListener("click", () => toggleSelect(card.id, el));
    hand.appendChild(el);
  });

  // 受理集合に応じて操作可否と色選択の表示を切り替える（判定ではなく UI のみ）
  const allowed = (view.awaiting && view.awaiting[me]) || [];
  document.getElementById("draw-btn").disabled = !allowed.includes("draw");
  document.getElementById("play-btn").disabled = !allowed.includes("play");
  toggleClass(document.getElementById("color-picker"), "hidden", !allowed.includes("choose_color"));

  // 手番・勝敗の表示
  const banner = document.getElementById("banner");
  if (view.winner) {
    banner.textContent = view.winner === me ? "あなたの勝ち！" : "あなたの負け…";
    toggleClass(banner, "hidden", false);
    setStatus("終局");
  } else {
    toggleClass(banner, "hidden", true);
    setStatus(view.current_player === me ? "あなたの番" : "相手の番");
  }
}

// --- 選択（複数枚出し）: 送信順を保持するトグル ----------------------------

// タップされたカードを選択リストに足す/外す。タップ順＝送信順（先頭=リード,
// 末尾=トップ）。一度外して再度タップすると末尾に付き直す。可否判定はサーバ。
function toggleSelect(cardId, el) {
  const idx = state.selected.indexOf(cardId);
  if (idx === -1) {
    state.selected.push(cardId);
    el.classList.add("selected");
  } else {
    state.selected.splice(idx, 1);
    el.classList.remove("selected");
  }
}

// 選択したカードを選択順に card_ids へ入れて出す。空なら何もしない。
function playSelected() {
  if (state.selected.length === 0) return;
  send({ type: "play", player: state.me, card_ids: state.selected.slice() });
}

// --- 入力ハンドラ（送信のみ） ---------------------------------------------

function wireControls() {
  document.getElementById("play-btn").addEventListener("click", playSelected);
  document.getElementById("draw-btn").addEventListener("click", () => {
    send({ type: "draw", player: state.me });
  });
  document.getElementById("reset-btn").addEventListener("click", () => {
    send({ type: "reset", player: state.me });
  });
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
  wireControls();
  connect();
});
