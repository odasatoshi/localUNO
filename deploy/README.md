# デプロイ: nginx のサブパス `/luno` で公開する

LAN 内の端末から `http://<このホスト>/luno/` で local-UNO にアクセスできるようにする手順。
本体（uvicorn）を `127.0.0.1:8000` に常駐させ、既存 nginx で `/luno/` をリバースプロキシする。

> フロント（`src/lUNO/web`）は全 URL を**ベース相対**で組むため、nginx 側で `/luno/` を
> 除去して root 配信のアプリへ渡すだけで、アセット・WebSocket（`/ws`）・カード画像（`/cards`）が
> すべて正しく解決される。root 直配信（`http://<ホスト>:8000/`）も従来どおり動く。

## 1. 本体を常駐させる（systemd）

`uv` をシステムに導入し（`command -v uv` で確認。無ければ
[公式手順](https://docs.astral.sh/uv/getting-started/installation/) で導入）、
`deploy/luno.service` の `ExecStart` の `uv` 絶対パスを実環境に合わせてから配置する。

```bash
sudo cp deploy/luno.service /etc/systemd/system/luno.service
# 必要なら User / WorkingDirectory / ExecStart の uv パスを編集
sudo systemctl daemon-reload
sudo systemctl enable --now luno.service
systemctl status luno.service          # active (running) を確認
curl -sS http://127.0.0.1:8000/ | head  # index.html が返ることを確認
```

## 2. nginx に `/luno/` を足す

`deploy/nginx-luno.conf` の 2 つの `location`（`= /luno` リダイレクトと `/luno/` プロキシ）を、
port 80 を待ち受けている既存 server ブロック（このマシンでは
`/etc/nginx/sites-enabled/jupyter.conf`）の中に追記する。`include` で取り込んでもよい：

```nginx
# jupyter.conf の server { ... } 内、他の location と並べて:
include /home/oda/localUNO/deploy/nginx-luno.conf;
```

反映：

```bash
sudo nginx -t          # 構文チェック（syntax is ok / test is successful）
sudo systemctl reload nginx
```

## 3. 動作確認

```bash
curl -sS -I http://localhost/luno            # 301 → /luno/
curl -sS     http://localhost/luno/ | head   # index.html
curl -sS -I  http://localhost/luno/app.js    # 200 (application/javascript)
```

ブラウザで `http://<このホストのLAN IP>/luno/` を開き、
「接続済み」表示・カード画像の表示・2 端末での即時反映を確認する。

## 補足

- 本体を直接 LAN へ晒したい場合は従来どおり `uv run luno`（既定 `0.0.0.0:8000`）でよい。
  nginx 経由に一本化するなら `--host 127.0.0.1`（本 unit の既定）で 8000 を外部非公開にする。
- `/luno/` の末尾スラは重要。`= /luno` の 301 で必ず付与されるため、リンク共有は
  どちらでも到達できる。
