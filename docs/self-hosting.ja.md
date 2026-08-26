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

初回起動時に合成デモキャラクターと世界観が作成されます。ランタイムデータは Docker の名前付きボリュームに保存されます。

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

重要なボリュームは、データ、アップロード、ログ、シークレットです。ホストのバックアップツールまたはレビュー済みの一時コンテナを使用してアーカイブし、バックアップは暗号化してリポジトリ外に保管してください。

## リモートアクセス

標準構成を公開インターネットに直接公開しないでください。次のいずれかを推奨します。

1. プライベート VPN またはプライベートメッシュネットワーク
2. リクエストサイズとタイムアウトを制限する認証済み TLS リバースプロキシ

この保護境界を準備してから、次の値を変更してください。

```dotenv
LORECHAT_BIND_ADDRESS=0.0.0.0
```

## デモデータのリセット

`docker compose down` はデータを保持します。名前付きボリュームを削除すると、データベース、アップロード、ログ、ローカルに保存したプロバイダーキーが完全に削除されます。削除が意図したものであり、バックアップがある場合を除き、ボリューム削除コマンドを実行しないでください。
