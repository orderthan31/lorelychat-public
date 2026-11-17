# アーキテクチャ

[한국어](architecture.ko.md) | [English](architecture.md) | [日本語](architecture.ja.md)

## ランタイム構成

```text
Browser
  |
  v
web (Nginx, :8080)
  |-- /, /assets/*  -> React static files
  `-- /api/*        -> api:8123/*
                         |
                         |-- /data/lorechat.db
                         |-- /uploads
                         |-- /logs
                         `-- /secrets/provider_secrets.json
```

ブラウザーは UI と API に同一オリジンでアクセスします。Nginx は FastAPI へ転送する前に `/api` プレフィックスを取り除きます。API ポート `8123` は Compose ネットワーク内でのみ公開されます。

## コンポーネント

- `apps/web`: React、Vite、TanStack Query/Router、Zustand、Tailwind
- `apps/api`: FastAPI、SQLModel/SQLAlchemy、SQLite、構造化生成パイプライン
- `deploy/nginx.conf`: SPA ルーティング、API プロキシ、アップロード容量制限、基本セキュリティヘッダー
- Docker named volume: DB、アップロード、ログ、プロバイダーの機密情報を分離して保存

## ソースの責任境界

- Web の `app/` は provider・layout・機能 controller を組み立てます。`router/` が TanStack ナビゲーションと既存 URL のマッピングを所有し、history の所有者を一つにします。
- `views/` は画面固有の状態と操作、`components/` は再利用 UI を所有します。`agent/` は承認・復旧の独立した機能であり、通常の CRUD commit と混ぜません。
- Web の `api/` は HTTP 契約と query key を所有します。サーバー一覧は Query cache、編集中の draft と進行中の生成・復旧状態は別の責任です。共有の純粋な既定値・正規化は View に依存しません。
- API の `main.py` は lifecycle と public worker runner を組み立てます。`api/` は HTTP admission・エラー/応答変換・lifecycle SSE、`services/` は生成・後処理の実行と永続化 orchestration を所有します。
- `engine/` は prompt・budget・transport・compression の実行部品、`db/` は ORM/session と共通 SQL、ドメイン service は一覧の検索・可視性・順序を所有します。`schemas/` は外部契約です。
- Backend の `agent/` は計画・所有者承認・CAS・receipt、`mcp/` は同じ操作の adapter、`ops/` は制限された運用 I/O・lock・backup・復旧を担当します。通常の catalog 方針は ops に置きません。
- provider I/O と最終化 transaction を分離します。見かけの重複を理由に request journal・unknown 復旧・lease/CAS fence・nullable/default suppression の意味を統合しません。

## 起動処理

1. Demo seed が有効で `/data/.demo-seeded` が存在しない場合、コンテナ entrypoint は `scripts.seed_demo` を実行します。スクリプトが先に schema を準備し、同名がない demo resource を挿入した後、entrypoint が volume marker を作成します。
2. FastAPI lifespan はアプリと Agent の schema、Agent service・任意の MCP gateway を準備し、制限付きの保留 image cleanup を再開します。
3. 既定の chat command を seed し、実行可能な durable job を復旧します。既存の部屋・世界観の関連や作成済み内容を仮の世界観で置き換えません。
4. 実モードでは public service runner により生成・後処理 dispatcher を起動します。Mock では実 dispatcher を無効にし、別の監督付き mock 専用 queue task を起動します。background task が全くないという意味ではありません。
5. Lifecycle 準備後に `/ready` が正常となり、Compose web は API readiness を待ちます。終了時は admission を停止し、所有する処理を drain してから runtime 所有権を解放します。

## 信頼境界

- ブラウザーからの入力とアップロードファイルは、信頼できないデータとして扱います。
- 実際のプロバイダーを選ぶと、プロバイダーへのリクエストは外部のデータ境界を越えます。
- プロバイダーの機密情報を保存するボリュームは、ソースコードではなく機密性の高いローカルデータです。
- Nginx はルーティング層であり、認証システムではありません。
- 公開インターネットに公開するには、別途、認証済みの TLS 境界が必要です。
