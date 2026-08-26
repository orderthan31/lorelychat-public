# 環境設定

[한국어](configuration.ko.md) | [English](configuration.md) | [日本語](configuration.ja.md)

`.env.example` を `.env` にコピーしてください。Docker Compose はこのファイルを自動で読み込みます。

## 主な設定値

| 変数 | 既定値 | 用途 |
|---|---:|---|
| `LORECHAT_BIND_ADDRESS` | `127.0.0.1` | ホスト上で Web コンテナを公開するアドレス |
| `LORECHAT_PORT` | `8080` | Web/API エンドポイントに使用するホスト側ポート |
| `LLM_MOCK` | `true` | 外部モデルを呼び出さず、一定の応答を使用 |
| `LORECHAT_SEED_DEMO` | `true` | データボリュームごとに合成キャラクターとワールドを一度だけ作成 |
| `CORS_ORIGINS` | `http://localhost:8080,http://127.0.0.1:8080` | API を直接開発するときに許可するオリジン |

Compose ファイルはコンテナ内の `DATABASE_URL`、`UPLOAD_ROOT`、`LOG_DIR`、`PROVIDER_SECRET_ROOT` を固定します。ボリューム構成も含めて再設計する場合を除き、これらのパスは変更しないでください。

## プロバイダー用の環境変数

通常は Web UI のプロバイダーおよびモデル設定を使用することを推奨します。既存環境との互換性のため、次の環境変数も利用できます。

- `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY`
- `CHAT_LLM_PROVIDER`, `CHAT_LLM_BASE_URL`, `CHAT_LLM_MODEL`, `CHAT_LLM_API_KEY`
- `GEMINI_API_KEY`

`.env.example` の空欄は意図されたものです。実際の値を `.env.example` に書いたり、`.env` を Git にコミットしたりしないでください。

## 外部メモリ

外部のセマンティックメモリは既定で無効です。

```dotenv
MEMORY_PROVIDER=local
MEM0_ENABLED=false
```

標準の Compose ファイルは `MEM0_READ_ENABLED` と `MEM0_WRITE_ENABLED` を渡しません。そのため、`MEM0_ENABLED=true` だけでは外部への読み書きは有効になりません。独自のデプロイでは、連携に必要な依存関係と読み書きのフラグを明示的に設定する必要があります。有効にすると、選択したメモリ内容がホストの外へ送信される可能性があります。事前にプロバイダーのプライバシーポリシーと保存期間の設定を確認してください。
