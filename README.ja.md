# Lorechat

[한국어](README.md) | [English](README.en.md) | [日本語](README.ja.md)

Lorechatは、キャラクターや世界観を作成して会話できる**セルフホスト型・モバイルファーストのキャラクターチャットランタイム**です。このモノレポにはReact WebクライアントとFastAPIバックエンドが含まれ、単一のDocker Compose構成として提供されます。

> **ライセンスについて**
> Lorechatは、OSI承認のオープンソースではなく、**ソースアベイラブルソフトウェア**です。個人利用を含む許可された非商用目的には、[PolyForm Noncommercial License 1.0.0](docs/license.ja.md)が適用されます。ライセンサーによる別途の書面許可がない限り、商用利用はできません。

## 主な機能

- キャラクターカードと世界観の作成・管理
- 1対1および複数キャラクターのチャットルーム
- 行動、台詞、思考、感情で構成されるマルチバブル応答
- 会話コンテキスト、シーン状態、関係性、キャラクターメモリの保持
- 実行時のプロバイダー／モデル設定と決定論的なモックモード
- SQLiteによる永続化、ファイルアップロード、オプションのTTS
- 同一オリジンで動作するReact + Nginx WebコンテナとFastAPI API
- Docker初回起動時の合成デモキャラクター・世界観の自動作成

## クイックスタート

### 必要なもの

- Docker Engine
- Docker Compose v2（`docker compose`）

### 起動

```bash
git clone <repository-url> lorechat
cd lorechat
cp .env.example .env
docker compose config
docker compose up --build -d
docker compose ps
```

ブラウザで <http://127.0.0.1:8080> を開きます。

初期値は`LLM_MOCK=true`です。APIキーなしで、モックによるチャットと圧縮のフローを確認できます。TTSなど、別途有効にした外部機能は独立して外部リクエストを送信する場合があります。

### ヘルスチェック

```bash
curl -fsS http://127.0.0.1:8080/healthz
curl -fsS http://127.0.0.1:8080/api/ready
```

### 停止と再起動

データを残したまま停止:

```bash
docker compose down
```

再起動:

```bash
docker compose up -d
```

## SQLiteとデータの永続化

別のデータベースコンテナは不要です。APIコンテナは`/data/lorechat.db`のSQLiteファイルを使用し、そのファイルはDockerの名前付きボリュームに保存されます。

| Composeボリュームキー | 内容 |
|---|---|
| `lorechat_data` | SQLite DBとデモシードの状態 |
| `lorechat_uploads` | アップロードファイル |
| `lorechat_logs` | アプリケーションログ |
| `lorechat_secrets` | UIから保存したプロバイダー認証情報 |

実際のDockerボリューム名には、Composeプロジェクト名のプレフィックスが付きます。標準の`COMPOSE_PROJECT_NAME=lorechat`では、DBボリュームは通常`lorechat_lorechat_data`として作成されます。

通常の`docker compose down`、コンテナの再作成、イメージの更新ではデータは削除されません。

> **注意:** `docker compose down -v`を実行すると、SQLite DB、アップロード、ログ、ローカルに保存したプロバイダーキーを含む名前付きボリュームが完全に削除されます。意図的に初期化する場合、またはバックアップがある場合にのみ実行してください。

初回起動では、2人の合成キャラクターと初期世界観が作成されます。新しいデータボリュームでデモシードを無効にする場合は、`.env`に次を設定します。

```dotenv
LORECHAT_SEED_DEMO=false
```

## 実際のモデルを接続する

1. まず`LLM_MOCK=true`でデプロイを確認します。
2. Web UIの**設定 → モデル設定**（現在のUIでは`설정 → 모델 설정`）を開きます。
3. 使用するプロバイダーアカウントとAPIキーを登録します。
4. モデル一覧を同期するか、互換モデルを手動で追加します。
5. 実行時のデフォルトモデルを選択します。
6. `.env`でモックモードを無効にします。

```dotenv
LLM_MOCK=false
```

7. APIコンテナを再作成します。

```bash
docker compose up -d --force-recreate api
```

UI で保存したプロバイダーキーは `/secrets/provider_secrets.json` に平文の JSON 値として保存され、データベースからは内容を示さない ID で参照されます。このファイルは POSIX ファイルシステムではモード `0600` で作成されますが、Lorechat 自体は暗号化しません。Git にコミットされたり、Docker イメージに組み込まれたりすることはありません。脅威モデル上必要な場合は、ホスト側のディスク暗号化とアクセス制御を使用してください。

Docker内の`127.0.0.1`はAPIコンテナ自身を指します。Dockerホスト上のOpenAI互換サーバーに接続する場合は、`.env.example`の例にある`host.docker.internal`を使用します。

詳しくは[プロバイダー設定](docs/providers.ja.md)と[環境設定](docs/configuration.ja.md)を参照してください。

## 主な環境変数

`.env.example`を`.env`へコピーして使用します。実際のキーを含む`.env`はコミットしないでください。

| 変数 | 初期値 | 説明 |
|---|---|---|
| `LORECHAT_BIND_ADDRESS` | `127.0.0.1` | Webエンドポイントをバインドするホストアドレス |
| `LORECHAT_PORT` | `8080` | Web/APIの公開ポート |
| `LLM_MOCK` | `true` | チャット・圧縮生成で決定論的なモック応答を使用 |
| `LORECHAT_SEED_DEMO` | `true` | 新しいデータボリュームに合成デモデータを作成 |
| `CORS_ORIGINS` | `http://localhost:8080,http://127.0.0.1:8080` | APIを直接開発する際に許可するオリジン |
| `MEMORY_PROVIDER` | `local` | メモリプロバイダーの選択 |
| `MEM0_ENABLED` | `false` | 外部メモリの基本機能フラグ。この値だけでは読み書きは有効にならない |

Composeは、コンテナ内の`DATABASE_URL`、`UPLOAD_ROOT`、`LOG_DIR`、`PROVIDER_SECRET_ROOT`を、提供されるボリューム構成に合わせて固定します。提供されるComposeファイルは`MEM0_READ_ENABLED`と`MEM0_WRITE_ENABLED`を渡さないため、外部メモリの読み書きは無効です。カスタムデプロイでは、プロバイダー依存関係と読み書きのフラグを明示的に設定する必要があります。

## ネットワークとセキュリティ

標準のデプロイは`127.0.0.1`にのみバインドします。現在のLorechatは、信頼できる単一ユーザーのセルフホスト環境を対象としており、公開インターネット向けの完全な認証境界は提供していません。

外部からアクセスする場合は、先に次のいずれかを構成してください。

- プライベートVPN
- 認証を備えたTLSリバースプロキシ

保護境界なしで`LORECHAT_BIND_ADDRESS=0.0.0.0`へ変更しないでください。プロバイダーキー、SQLite DB、会話、プロンプト、アップロード、ログは機密データとして扱ってください。

詳しくは[セキュリティポリシー](SECURITY.ja.md)を参照してください。

## リポジトリ構成

```text
apps/web/        React + Vite Webクライアント
apps/api/        FastAPI + SQLModel API
apps/api/seeds/  公開用の合成デモデータ
deploy/          Nginx リバースプロキシ設定
docs/            アーキテクチャ・設定・セルフホストガイド
docker-compose.yml
```

ブラウザはNginxの単一エンドポイントに接続し、`/api/*`へのリクエストは内部FastAPIサービスへ転送されます。APIポート `8123`はComposeネットワーク内にのみ公開されます。

## ローカル開発と検証

### Web

```bash
cd apps/web
npm ci
npm run typecheck
npm run test
npm run build
npm audit --omit=dev
```

### API

Python 3.12と[uv](https://docs.astral.sh/uv/)の利用を推奨します。

```bash
cd apps/api
uv venv .venv --python 3.12
source .venv/bin/activate
uv pip sync requirements-dev.txt
python -m pytest -q
```

### Docker

```bash
docker compose config --quiet
docker compose build --pull
docker compose up -d --wait
```

## ドキュメント

- [アーキテクチャ](docs/architecture.ja.md)
- [環境設定](docs/configuration.ja.md)
- [プロバイダー設定](docs/providers.ja.md)
- [セルフホスティング](docs/self-hosting.ja.md)
- [セキュリティポリシー](SECURITY.ja.md)
- [コントリビューションポリシー](CONTRIBUTING.ja.md)

## プライバシーと外部サービス

- 標準ではランタイムデータをローカルDockerボリュームに保存します。
- `LLM_MOCK=true`では、チャットと圧縮生成から外部LLMを呼び出しません。TTSなど、別途有効にした機能は独立して外部リクエストを送信する場合があります。
- 外部プロバイダーを設定した場合、リクエストに必要なプロンプトと会話コンテキストがそのプロバイダーへ送信されます。
- 提供されるComposeデプロイでは、外部メモリの読み書きは無効です。カスタムデプロイで有効にすると、選択されたメモリ情報がホスト外へ送信される場合があります。
- 同梱するデモデータは合成データであり、実際の会話、ペルソナ、運用データは含みません。

## ライセンス

Copyright 2026 orderthan31.

[PolyForm Noncommercial License 1.0.0](docs/license.ja.md)に基づいて提供されます。このリポジトリは商用利用権を付与しません。商用利用、有料サービス、再販売、商用ホスティング、または業務利用には、ライセンサーによる別途の書面許可が必要です。

この制限により、LorechatはOSI定義のオープンソースではなく、**ソースアベイラブルプロジェクト**です。

## コントリビューション

セキュリティ報告とバグ報告を歓迎します。コントリビューター契約または再ライセンス契約が公開されるまで、コードのコントリビューションは受け付けません。詳しくは[コントリビューションポリシー](CONTRIBUTING.ja.md)を参照してください。
