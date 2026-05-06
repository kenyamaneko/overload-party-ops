# GitHub App Migration Runbook (Ops)

[ADR-033](https://github.com/kenyamaneko/overload-party-common/blob/main/docs/adr/033-cross-repo-auth-github-app-migration.md) の実装 runbook。本リポ (overload-party-ops) のワークフローを **個人 PAT から GitHub App + 短命 token** に移行する手順を順序付きで記載する。

関連 issue: [#18](https://github.com/kenyamaneko/overload-party-ops/issues/18)

## スコープ

本 runbook は **ops リポ内のワークフロー 3 本** を対象とする。

| Workflow | 廃止する PAT | 移行先 App | 必要 permission |
|---|---|---|---|
| `.github/workflows/drift-monitor.yaml` | `INFRA_DRIFT_MONITOR_TOKEN` | `overload-party-ops-automation` | Issues:Write + Contents:Read |
| `.github/workflows/nightly-shutdown.yaml` | `K8S_DISPATCH` / `INFRA_DISPATCH` | `overload-party-ops-automation` | Actions:Write |
| `.github/workflows/db-migrate.yaml` | `DB_MIGRATE_TOKEN` | `overload-party-cross-repo-deps` | Contents:Read |

ops リポ外の対応 (slack-commands Cloud Run の #12 や k8s リポの #7 PLATFORM_DISPATCH) は別 PR/別 runbook で扱う。本 runbook の完了時点では:

- Ops Automation App と Common Read App の 2 つが組織にインストールされている
- ops リポの 3 ワークフローが App token で動いている
- `INFRA_DRIFT_MONITOR` / `K8S_DISPATCH` / `INFRA_DISPATCH` / `DB_MIGRATE` の 4 PAT が revoke 済み

## Phase 0: 前提

GitHub Organization オーナー権限の操作が必要 (App 作成と組織インストール)。本 runbook は `kenyamaneko` 個人アカウントが実行する想定。

## Phase 1: Ops Automation App を作成

### 1.1 App 作成 (GitHub UI)

1. https://github.com/settings/apps/new を開く
2. 以下で作成:

   | 項目 | 値 |
   |---|---|
   | GitHub App name | `overload-party-ops-automation` |
   | Homepage URL | `https://github.com/kenyamaneko/overload-party-ops` |
   | Webhook | **Active のチェックを外す** (受信側で受ける必要なし) |
   | Repository permissions / Actions | **Read and write** |
   | Repository permissions / Contents | **Read-only** |
   | Repository permissions / Issues | **Read and write** |
   | Repository permissions / Metadata | (自動で Read-only) |
   | Where can this GitHub App be installed? | **Only on this account** |

3. Create GitHub App をクリック

### 1.2 App ID の控え

作成完了画面に表示される **App ID** (数値) を控えておく。後で `vars.OPS_AUTOMATION_APP_ID` に登録する。

### 1.3 Private key 発行

同じ App 設定画面下部の **Private keys** セクションで **Generate a private key** をクリック。`.pem` ファイルがダウンロードされる。後で secret に登録するためダウンロードファイルは保管 (登録後にローカルから削除)。

### 1.4 overload-party-* + keyandnotes-platform にインストール

1. 同設定画面の **Install App** タブを開く
2. `@kenyamaneko` の **Install** をクリック
3. **Only select repositories** を選択し、overload-party-* リポ全部 + keyandnotes-platform (PLATFORM_DISPATCH 用) を対象にする。無関係な個人リポ (pokelingual / shikacrush 等) は外す
4. Install をクリック

## Phase 2: Common Read App を作成

`db-migrate.yaml` の Contents:Read 用途は ADR-033 の方針に従い別 App (Common Read App) を使う。本 runbook では ops 完結のために Common Read App も併せて作成する。Phase 1 とほぼ同じ手順。

### 2.1 App 作成

1. https://github.com/settings/apps/new を開く
2. 以下で作成:

   | 項目 | 値 |
   |---|---|
   | GitHub App name | `overload-party-cross-repo-deps` |
   | Webhook | Active off |
   | Repository permissions / Contents | **Read-only** |
   | Repository permissions / Metadata | (自動で Read-only) |
   | Where can this GitHub App be installed? | **Only on this account** |

3. Create

### 2.2 App ID 控え + Private key 発行 + overload-party-* リポへインストール

Phase 1.2〜1.4 と同手順だが、インストール時は **Only select repositories** を選択し overload-party-* リポ全部 (個人の無関係リポは除外) を対象にする。`vars.CROSS_REPO_DEPS_APP_ID` と `secrets.CROSS_REPO_DEPS_APP_PRIVATE_KEY` に対応する値を控える。

## Phase 3: Secret / Variable 登録

ops リポの secret / variable に以下を登録する。

### 3.1 ops リポの設定画面

`https://github.com/kenyamaneko/overload-party-ops/settings/secrets/actions`

#### Repository variables

| Name | Value |
|---|---|
| `OPS_AUTOMATION_APP_ID` | Phase 1.2 で控えた数値 |
| `CROSS_REPO_DEPS_APP_ID` | Phase 2.2 で控えた数値 |

#### Repository secrets

| Name | Value |
|---|---|
| `OPS_AUTOMATION_APP_PRIVATE_KEY` | Phase 1.3 でダウンロードした PEM ファイルの中身 (BEGIN〜END まで全文) |
| `CROSS_REPO_DEPS_APP_PRIVATE_KEY` | Phase 2.2 でダウンロードした PEM ファイルの中身 |

### 3.2 既存の旧 PAT secret は **削除しない** (まだ)

並走期間として旧 PAT を残し、App token に切り替えた workflow が安定 green になってから削除する (Phase 6)。

## Phase 4: Workflow を順次書き換え

ワークフロー単位で PR を分ける。各 PR を main にマージしたら少なくとも 1 回手動 dispatch して green を確認してから次へ進む。

### 4.1 PR-1: drift-monitor.yaml

`.github/workflows/drift-monitor.yaml` の `Run drift check` step 直前に App token 取得 step を追加し、`GITHUB_TOKEN` に App token を流す。

**変更前** ([drift-monitor.yaml:39-47](.github/workflows/drift-monitor.yaml#L39)):

```yaml
      - name: Run drift check
        env:
          SLACK_WEBHOOK_URL: ${{ secrets.SLACK_WEBHOOK_URL }}
          GITHUB_TOKEN: ${{ secrets.INFRA_DRIFT_MONITOR_TOKEN }}
          ...
        run: python3 drift-monitor/check.py
```

**変更後**:

```yaml
      - name: Generate App token
        id: app-token
        uses: actions/create-github-app-token@v1
        with:
          app-id: ${{ vars.OPS_AUTOMATION_APP_ID }}
          private-key: ${{ secrets.OPS_AUTOMATION_APP_PRIVATE_KEY }}
          owner: kenyamaneko

      - name: Run drift check
        env:
          SLACK_WEBHOOK_URL: ${{ secrets.SLACK_WEBHOOK_URL }}
          GITHUB_TOKEN: ${{ steps.app-token.outputs.token }}
          ...
        run: python3 drift-monitor/check.py
```

確認方法: PR マージ後 `gh workflow run drift-monitor.yaml --repo kenyamaneko/overload-party-ops` を実行し、Issue 起票が App アカウント (`overload-party-ops-automation[bot]`) 名義で行われることを確認。

### 4.2 PR-2: nightly-shutdown.yaml

dispatch 用 step が 2 つあるため、両方を App token に切り替える。token は両 step で共通の 1 つでよい (job の頭で取得)。

**変更前** ([nightly-shutdown.yaml:38-58](.github/workflows/nightly-shutdown.yaml#L38)):

```yaml
      - name: Dispatch env-lifecycle down
        uses: benc-uk/workflow-dispatch@v1
        with:
          ...
          token: ${{ secrets.K8S_DISPATCH }}
          ...

      - name: Dispatch Cloud SQL down
        uses: benc-uk/workflow-dispatch@v1
        with:
          ...
          token: ${{ secrets.INFRA_DISPATCH }}
          ...
```

**変更後**:

```yaml
      - name: Generate App token
        id: app-token
        uses: actions/create-github-app-token@v1
        with:
          app-id: ${{ vars.OPS_AUTOMATION_APP_ID }}
          private-key: ${{ secrets.OPS_AUTOMATION_APP_PRIVATE_KEY }}
          owner: kenyamaneko

      - name: Dispatch env-lifecycle down
        uses: benc-uk/workflow-dispatch@v1
        with:
          ...
          token: ${{ steps.app-token.outputs.token }}
          ...

      - name: Dispatch Cloud SQL down
        uses: benc-uk/workflow-dispatch@v1
        with:
          ...
          token: ${{ steps.app-token.outputs.token }}
          ...
```

確認方法: PR マージ後 `gh workflow run nightly-shutdown.yaml -f environment=dev` を実行し、k8s 側 / infra 側で env-lifecycle.yaml / cloudsql-activation.yaml が起動すること、`wait-for-completion` が成功することを確認。

### 4.3 PR-3: db-migrate.yaml

`fetch-schemas.py` と `build-previous-schema-union.sh` に `DB_MIGRATE_TOKEN` env として渡しているので、両方を App token に切り替える。

**変更前** ([db-migrate.yaml:75-88](.github/workflows/db-migrate.yaml#L75)):

```yaml
      - name: Build schema union (current HEAD of schemas.lock.yaml)
        env:
          DB_MIGRATE_TOKEN: ${{ secrets.DB_MIGRATE_TOKEN }}
        run: |
          python3 db-migrate/fetch-schemas.py ...

      - name: Build previous schema union (for safety diff)
        env:
          DB_MIGRATE_TOKEN: ${{ secrets.DB_MIGRATE_TOKEN }}
        run: .github/scripts/db-migrate/build-previous-schema-union.sh
```

**変更後**:

```yaml
      - name: Generate Cross-Repo Deps App token
        id: cross-repo-deps-token
        uses: actions/create-github-app-token@v1
        with:
          app-id: ${{ vars.CROSS_REPO_DEPS_APP_ID }}
          private-key: ${{ secrets.CROSS_REPO_DEPS_APP_PRIVATE_KEY }}
          owner: kenyamaneko

      - name: Build schema union (current HEAD of schemas.lock.yaml)
        env:
          DB_MIGRATE_TOKEN: ${{ steps.cross-repo-deps-token.outputs.token }}
        run: |
          python3 db-migrate/fetch-schemas.py ...

      - name: Build previous schema union (for safety diff)
        env:
          DB_MIGRATE_TOKEN: ${{ steps.cross-repo-deps-token.outputs.token }}
        run: .github/scripts/db-migrate/build-previous-schema-union.sh
```

確認方法: PR マージ後 `gh workflow run db-migrate.yaml -f environment=dev -f dry_run=true` を実行し、各サービスリポからの schema fetch が成功し dry-run 完了するまで確認。

> **注**: App token は **1 時間 expire**。db-migrate のジョブ全体が 1 時間以内に完了する想定なので 1 回の token 取得で全 step を回せる。万一長時間化した場合は step ごとに token を再発行する設計に変更する。

## Phase 5: Green を確認 → 旧 PAT secret を削除

PR-1 / PR-2 / PR-3 すべてのマージ後、各 workflow が **少なくとも 2 回** App token で green になっていることを確認したら、ops リポから旧 PAT secret を削除する。

```
削除対象 (ops repo secrets):
- INFRA_DRIFT_MONITOR_TOKEN
- K8S_DISPATCH
- INFRA_DISPATCH
- DB_MIGRATE_TOKEN

> 旧 PAT 自体 (個人 PAT) は Phase 6 で revoke する。本 phase は repo に貼られた secret 削除のみ。
```

## Phase 6: 個人 PAT を revoke

`https://github.com/settings/tokens` を開き、以下の PAT を revoke する。

- `INFRA_DRIFT_MONITOR`
- `K8S_DISPATCH`
- `INFRA_DISPATCH`
- `DB_MIGRATE`

revoke 後 24 時間 (or 適切な観測期間) は workflow を見て予期せぬ失敗がないことを確認。

## 完了条件

- [ ] Ops Automation App / Common Read App が組織にインストール済み
- [ ] ops リポに `OPS_AUTOMATION_APP_ID` / `OPS_AUTOMATION_APP_PRIVATE_KEY` / `GO_MODULES_APP_ID` / `GO_MODULES_APP_PRIVATE_KEY` 登録済み
- [ ] PR-1, PR-2, PR-3 マージ済みで本番運用が green
- [ ] 旧 PAT secret 4 種を ops リポから削除済み
- [ ] 個人 PAT 4 種を revoke 済み

## 後続作業

本 runbook の完了後に残る作業 (別 issue / 別 runbook):

- **ops#18 残**: slack-commands Cloud Run (`SLACK_COMMANDS`) の App 化 — Cloud Run service 内で installation token を取得する実装変更が必要
- **k8s#16**: ArgoCD Image Updater (`ARGOCD_IMAGE_UPDATE`) の App 化
- **common#34**: 6 サービスリポ (card / shop / account / scenario / gateway / matchmaking) の CI を Common Read App に切り替え
- **common#34 e2e**: `overload-party-e2e/docker/docker-compose.yml` のローカル secret を App token CLI に置換
- **common#35**: `CLAUDE_SYNC` を専用 Claude Sync App に移行
- **k8s/env-lifecycle.yaml**: `PLATFORM_DISPATCH` を Ops Automation App に切り替え (k8s リポ側で別 PR)
- **PAT 棚卸し残**: `COMMON_CI_DISPATCH` の audit log 確認 → 死蔵確定後 revoke
