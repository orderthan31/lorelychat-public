# セルフホスティング

[한국어](self-hosting.ko.md) | [English](self-hosting.md) | [日本語](self-hosting.ja.md)

## 初回デプロイ

```bash
cp .env.example .env
docker compose config
docker compose up --build -d
docker compose ps
curl -fsS http://127.0.0.1:8080/api/ready
```

初回起動時に合成デモのキャラクターとワールドが作成されます。ランタイムデータは Docker named volume に保存されます。

## 更新

```bash
git pull --ff-only
docker compose build --pull
docker compose up -d
docker compose ps
```

更新前に永続データをバックアップしてください。データが重要な場合は、新しいイメージをステージング環境で先に確認してください。

## バックアップ

実際の Compose ボリューム名を先に確認します。

```bash
docker volume ls --filter label=com.docker.compose.project=lorechat
```

重要なボリュームには、データ、アップロード、ログ、機密情報が保存されています。ホストのバックアップツール、またはレビュー済みの一時コンテナでアーカイブを作成してください。バックアップは暗号化し、リポジトリの外に保管します。

## リモートアクセス

標準構成を公開インターネットに直接公開しないでください。次のいずれかを推奨します。

1. Private VPN またはプライベートメッシュネットワーク
2. リクエストサイズとタイムアウトを制限する認証済み TLS リバースプロキシ

この保護境界を準備してから、次の値を変更してください。

```dotenv
LORECHAT_BIND_ADDRESS=0.0.0.0
```

## デモデータのリセット

`docker compose down` はデータを保持します。Named volume を削除すると、データベース、アップロード、ログ、ローカルに保存されたプロバイダー key が完全に消去されます。意図的な初期化で、バックアップも完了している場合を除き、ボリューム削除コマンドを実行しないでください。
