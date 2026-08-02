# DB Migrate

psqldef ベースの multi-source スキーママイグレーションシステム。各サービスリポに分散した DDL を union して Cloud SQL に適用する。

## スキーマソース

DB スキーマはサービスごとに分割されている。DDL の所在:

| Schema | Owner repo | Path in repo |
|--------|------------|--------------|
| `account` | overload-party-account | `db/schema.sql` |
| `battle` | overload-party-battle | `db/schema.sql` |
| `card` | overload-party-card | `db/schema.sql` |
| `shop` | overload-party-shop | `db/schema.sql` |
| `scenario` | overload-party-scenario | `db/schema.sql` |
| `gateway` | overload-party-gateway | `db/schema.sql` |
| `news` | overload-party-news | `db/schema.sql` |
| `support` | overload-party-support | `db/schema.sql` |

matchmaking は DB を持たない (Redis + Pub/Sub のみ)。ゲーム動的設定値 (`game_config`) は Cloud Firestore で別管理。

## 仕組み

1. `schemas.lock.yaml` に列挙された全サービスリポを pinned ref で sparse-checkout (`fetch-schemas.py`)
2. 取得した DDL を依存順で union し `sql/schema_union.sql` に書き出す (app-level FK 依存の都合で `gateway` は `battle` の後)
3. psqldef + `sqldef.yml` の `target_schema` で各サービススキーマを宣言的に diff → ALTER 適用
4. `grant_iam.sql` を psql で実行して IAM user 権限を付与 (per-schema RW)
5. `seeds` に列挙されたマスタデータ投入 SQL を union し (`sql/seed_union.sql`)、psql で適用

seed は upsert で書かれており、マイグレーションのたびに流すとマスタデータが lock の内容に揃う。カードやプロダクトの定義を各サービスリポで更新すれば、次のマイグレーションで環境に反映される。

psqldef は宣言的スキーマ管理ツールで、現在の DB 状態と union の差分を自動で計算・適用する。union は常に「望ましい全体像」なので、サービスを追加したら `schemas.lock.yaml` にエントリを足し、`sqldef.yml` の `target_schema` にスキーマ名を追加すれば次のマイグレーションで新スキーマが作られる。

> psqldef の upstream バグ (dropped column で NULL スキャン) に対するパッチを Dockerfile 内で適用している。

## ファイル一覧

| ファイル | 役割 |
|---------|------|
| `schemas.lock.yaml` | 各サービスリポの DDL 参照 (repo / path / ref) と seed の適用順 |
| `fetch-schemas.py` | lock file から schema / seed の union をビルドする CI 側スクリプト |
| `grant_iam.sql` | IAM ロール権限付与 (per-schema, idempotent) |
| `sqldef.yml` | psqldef config (管理対象スキーマをサービス所有スキーマに限定) |
| `schema_check.py` | 破壊的変更 (DROP TABLE / DROP COLUMN) 検出 |
| `union_format.py` | union のサービス区分見出しの書式 (`fetch-schemas.py` が書き `schema_check.py` が読む) |
| `entrypoint.sh` | psqldef + psql 実行ラッパー (Cloud Run Job 内で走る) |
| `Dockerfile` | psqldef を upstream patch + Alpine postgres client で同梱 |
| `build-push.sh` | イメージビルド & Artifact Registry push |
| `update-job-image.sh` | Cloud Run Job のイメージ差し替え |
| `execute-job.sh` | Cloud Run Job 実行 |
| `ensure-sql-running.sh` | Cloud SQL インスタンス起動待ち (夜間停止運用との互換) |
| `sql/` | gitignore。CI 実行時に生成される `schema_union.sql` / `seed_union.sql` + copy された `grant_iam.sql` |

## スキーマ変更フロー

### サービスリポ側 (開発者)

1. 各サービスリポの `db/schema.sql` を編集
2. PR → main merge
3. main push が自動で ops repo に `repository_dispatch(db-migrate)` を送出 (該当 workflow が各サービスリポ側に必要)
4. ops 側の db-migrate workflow が dev 環境に自動適用

### ops 側 (ref を pin したい場合)

1. `db-migrate/schemas.lock.yaml` を編集して特定 ref に pin (trouble shoot / bisect / hotfix 等)
2. ops repo に PR / main merge
3. `gh workflow run db-migrate.yaml -f environment=dev` (or `stg`) で手動実行

### stg / prod への昇格

1. dev で適用を確認後、`gh workflow run db-migrate.yaml -f environment=stg` を手動実行
2. lock file の ref は環境間で共通 (dev で検証済みのものを stg でそのまま使う)

## スキーマ安全チェック

`schema_check.py` が「前コミットの lock file で作った union」 vs 「現コミットの lock file で作った union」を比較し、破壊的変更 (DROP TABLE / DROP COLUMN) を検出する。

破壊的変更が検出されるとワークフローは失敗する。意図的な変更の場合は dry_run でプレビューした上で手動実行する。

テーブルは所有サービスで修飾した名前 (`shop.outbox_events`) で対応付ける。`outbox_events` / `processed_events` / `products` は複数のサービスが同名で持つため、修飾しないと片方の定義がもう片方を隠し、隠れた側の削除を検出できない。所有サービスは union のサービス区分見出しから決まるので、`schemas.lock.yaml` の `name` を変えると旧名のテーブル削除と新名のテーブル追加として報告される。見出しの無い SQL を渡した場合や、1 つのサービスが同名のテーブルを二重に定義している場合は、対応付けができないためワークフローは失敗する。

DDL 中の `CREATE TABLE` の数と解析できたテーブルの数が食い違う場合も、ワークフローは失敗する。解析できなかったテーブルは削除・カラム削除を検出できないため、検査の穴を残したまま適用へ進ませない。新しい DDL 構文を使うときは `schema_check.py` のパーサを併せて拡張する。

## トリガー

### repository_dispatch (自動)

service repo の main push 時に `db-migrate` イベントが発火し、**dev 環境のみ**自動実行される。各 service repo 側の workflow が送信元。

### workflow_dispatch (手動)

GitHub Actions UI から手動実行。

| パラメータ | 説明 | デフォルト |
|-----------|------|-----------|
| `environment` | 対象環境 (`dev` / `stg`) | `dev` |
| `dry_run` | dry-run モード (イメージ更新のみ、ジョブ実行なし) | `false` |

## Dry-run モード

`dry_run=true` で実行すると Docker イメージのビルド・プッシュと Cloud Run Job のイメージ更新までは行うが、**ジョブ実行はスキップ**される。

## 必要なシークレット / 変数

ops リポジトリの Settings > Secrets and variables > Actions:

| 種別 | 名前 | 用途 |
|---|---|---|
| Variable | `CROSS_REPO_DEPS_APP_ID` | service repo の schema を fetch する Cross-Repo Deps App の App ID |
| Secret | `CROSS_REPO_DEPS_APP_PRIVATE_KEY` | 同 App の private key (PEM)。短命 token を発行し `DB_MIGRATE_TOKEN` 環境変数として注入 |

環境ごと（Settings > Environments > `dev` / `stg`）:

| 種別 | 名前 | 用途 |
|---|---|---|
| Variable | `WIF_PROVIDER` | Workload Identity Federation プロバイダ |
| Variable | `DB_MIGRATOR_SERVICE_ACCOUNT` | db-migrate 実行用サービスアカウント |
| Variable | `CLOUDSQL_INSTANCE_NAME` | Cloud SQL インスタンス名 |

## ローカルでの dry-run

```bash
# lock file から union をビルド (GitHub PAT が必要)
export DB_MIGRATE_TOKEN=ghp_xxx
python3 db-migrate/fetch-schemas.py \
  --lock db-migrate/schemas.lock.yaml \
  --out db-migrate/sql/schema_union.sql \
  --seed-out db-migrate/sql/seed_union.sql \
  --grant-src db-migrate/grant_iam.sql

# ローカル postgres に流す
docker run -d --rm --name testdb -p 5433:5432 \
  -e POSTGRES_PASSWORD=testpass postgres:16-alpine
psqldef --dry-run --config db-migrate/sqldef.yml \
  --host=127.0.0.1 --port=5433 --user=postgres --password=testpass postgres \
  < db-migrate/sql/schema_union.sql
```
