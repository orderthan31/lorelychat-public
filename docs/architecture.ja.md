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

## 起動処理

1. API entrypoint がデータボリュームごとに合成デモデータを一度だけ作成します。
2. FastAPI が SQLite スキーマを初期化し、マイグレーションを適用します。
3. 既定のシステムプロンプトとチャットコマンドが作成されます。
4. Mock mode ではバックグラウンドのモデルディスパッチャーは起動しません。
5. `/ready` が正常状態を返した後に Web サービスが起動します。

## 信頼境界

- ブラウザーからの入力とアップロードファイルは、信頼できないデータとして扱います。
- 実際のプロバイダーを選ぶと、プロバイダーへのリクエストは外部のデータ境界を越えます。
- プロバイダーの機密情報を保存するボリュームは、ソースコードではなく機密性の高いローカルデータです。
- Nginx はルーティング層であり、認証システムではありません。
- 公開インターネットに公開するには、別途、認証済みの TLS 境界が必要です。
