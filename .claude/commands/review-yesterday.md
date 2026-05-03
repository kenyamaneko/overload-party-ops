---
description: 17 リポの前日 00:00 JST 以降の差分を Subagent で並列レビューし、~/reviews/{前日日付}/ に書き出して指摘があれば各リポに Issue 起票する
allowed-tools: Bash, Agent, Read, Write
---

# /review-yesterday

前日 00:00 JST 以降の各リポジトリの差分を、リポ全体を読みながら並列でレビューする。
Cloud Run Job 上の旧 nightly-review (差分のみを Claude API に投げる方式) の置き換え。

## 全体方針

- 親エージェント (このコマンド) は **オーケストレーション専任**。レビュー本体は実行しない
- 17 リポを `general-purpose` Subagent に **すべて並列で** 投げる (1 メッセージ内で複数 Agent 呼び出し)
- Subagent は各自で `gh repo clone` してリポ全体を Read/Grep/Glob で参照し、観点に沿ってレビューする
- 結果は `~/reviews/{前日日付}/{repo}.md` に書き出す。指摘ありなら GitHub Issue も起票する
- 全 Subagent 完了後、親が `~/reviews/{前日日付}/index.md` を集約生成してチャットに返す

## 手順

### 1. 日付計算

JST で「前日」と「実行日」を計算する。前日日付がレビュー対象範囲の起点、実行日が Issue タイトルに使う。

```bash
TODAY=$(TZ=Asia/Tokyo date +%Y-%m-%d)
YESTERDAY=$(TZ=Asia/Tokyo date -v-1d +%Y-%m-%d)  # macOS BSD date
mkdir -p ~/reviews/$YESTERDAY
```

### 2. リポジトリ一覧

下記 17 リポを並列で Subagent に投げる。`(repo, branch)` の組:

- (overload-party-common, main)
- (overload-party-client, main)
- (overload-party-battle, main)
- (overload-party-gateway, main)
- (overload-party-account, main)
- (overload-party-card, main)
- (overload-party-matchmaking, main)
- (overload-party-shop, develop)
- (overload-party-scenario, main)
- (overload-party-support, main)
- (overload-party-infra, main)
- (overload-party-k8s, main)
- (overload-party-newsfeed, main)
- (overload-party-news, main)
- (overload-party-analytics, main)
- (overload-party-ops, main)
- (overload-party-assets, main)

### 3. Subagent への指示テンプレート

各 Subagent には `general-purpose` を使い、次のプロンプトを渡す (テンプレート中の `{...}` は親が埋める):

---

```
あなたは {repo} (ブランチ: {branch}) の自動コードレビュアです。
前日 {YESTERDAY} 00:00 JST 以降の差分をレビューしてください。

## 出力ファイル
~/reviews/{YESTERDAY}/{repo}.md

## 手順

### Step 1: 差分の有無確認

```bash
COMMITS=$(gh api "repos/kenyamaneko/{repo}/commits?sha={branch}&since={YESTERDAY}T00:00:00%2B09:00" --jq 'length')
echo "$COMMITS"
```

`$COMMITS` が 0 なら、レビュー不要。`~/reviews/{YESTERDAY}/{repo}.md` に下記だけ書いて Step 6 にスキップ:

```
No changes since {YESTERDAY} 00:00 JST.
```

そして親への返答は "No changes" とする。

### Step 2: 差分とリポ全体の取得

```bash
WORKDIR=/tmp/review-{repo}-{YESTERDAY}
[ -d "$WORKDIR" ] || gh repo clone kenyamaneko/{repo} "$WORKDIR" -- --branch {branch}
git -C "$WORKDIR" diff "{branch}~$COMMITS...{branch}" > "$WORKDIR/.review-diff.patch"
git -C "$WORKDIR" log --since="{YESTERDAY}T00:00:00+09:00" --pretty=format:'%h %s (%an)' > "$WORKDIR/.review-commits.txt"
```

### Step 3: コンテキスト構築

- `$WORKDIR/.review-diff.patch` を Read で読み、変更ファイル一覧と patch を把握する
- 変更ファイルそれぞれを Read で全文読む (差分だけでなく全文)
- 必要に応じて `$WORKDIR` 配下を Read / Grep / Glob で参照し、呼び出し元・関連設定・テスト・既存設計を確認する。propagation や scope の見落としを防ぐためにこのステップは省略しないこと

### Step 4: レビュー観点

下記すべての観点で評価する。1 つでも該当する指摘があれば Step 5 で Markdown に書き出す。すべて問題なければ "LGTM" とだけ書いて Step 5 をスキップして Step 6 へ。

- 設計
  - 設計通りに実装されているか (設計ドキュメント・既存設計と実装の整合性)
  - 拡張性と保守性が高い設計であること
  - 同じような処理を複数箇所に書いていないか
  - 場当たり的なワークアラウンドで設計を汚していないか
- バグ・セキュリティ
  - バグのリスクがないか
  - セキュリティリスクがないか
- コード品質
  - 使用していないコードがないか
  - 未実装の TODO がないか
- 構成・ドキュメント
  - ディレクトリ構成が整理されているか
  - ドキュメントとコードの乖離がないか
- エラーハンドリング
  - エラーハンドリングが適切か (エラーを握りつぶしていないか)
- テスト
  - テストコードは仕様に沿っているか
  - テストコードのデータパターンが十分か
  - テストを通すために設計を汚していないか
  - 実装をなぞるだけのテストになっていないか (仕様ベースになっているか)
- コメント
  - 実装をなぞるだけのコメントがないか (Doc コメントは除く)
  - 実装意図がわかりにくい箇所に意図を表すコメントがあるか
- 責務分離
  - ファイル・クラス・関数の責務が明確に分離されているか
  - リポジトリ間の責務が明確に分離されているか
- 共通化
  - 通信用の文字列 (エンドポイント、イベント名、ヘッダ名、トピック名など) はリテラル直書きではなく共通パッケージの定数を使っているか

### Step 5: 結果ファイルの書き出し

`~/reviews/{YESTERDAY}/{repo}.md` に Markdown で書く。フォーマット:

```
# {repo} 自動レビュー ({YESTERDAY} 以降)

## 対象コミット
- {hash} {subject} ({author})
- ...

## 指摘

### {観点カテゴリ名}
- **ファイル**: `path/to/file.go:123-145`
- **指摘**: ...
- **改善案**: ...

### ...
```

指摘がない場合の本文は `LGTM` の一行のみとする (LGTM 判定のため後続処理が文字列マッチする)。

### Step 6: Issue 起票 (指摘ありの場合のみ)

LGTM または "No changes" の場合は Issue を作らない。指摘がある場合のみ実行する。

```bash
TITLE_PREFIX="[自動レビュー {TODAY}] 差分"
EXISTING=$(gh issue list --repo kenyamaneko/{repo} --search "in:title \"$TITLE_PREFIX\"" --state open --json number --jq 'length')
if [ "$EXISTING" = "0" ]; then
  gh label create auto-review --repo kenyamaneko/{repo} --color 0e8a16 --description "Nightly auto-review" 2>/dev/null || true
  ISSUE_URL=$(gh issue create \
    --repo kenyamaneko/{repo} \
    --title "$TITLE_PREFIX {repo}" \
    --label auto-review \
    --body-file ~/reviews/{YESTERDAY}/{repo}.md)
  echo "$ISSUE_URL"
else
  echo "skipped (existing issue)"
fi
```

### Step 7: 親への返答

次の JSON 形式で 1 行返す。それ以外の冗長な文章は不要。

- 差分なし: `{"repo": "{repo}", "status": "no_changes"}`
- LGTM: `{"repo": "{repo}", "status": "lgtm"}`
- 指摘あり (Issue 起票成功): `{"repo": "{repo}", "status": "issues", "issue_url": "<URL>", "review_path": "~/reviews/{YESTERDAY}/{repo}.md"}`
- 指摘あり (Issue 既存スキップ): `{"repo": "{repo}", "status": "issues", "issue_url": null, "skipped_existing": true, "review_path": "~/reviews/{YESTERDAY}/{repo}.md"}`
- 失敗: `{"repo": "{repo}", "status": "error", "error": "<短い説明>"}`

エラーは silent に握りつぶさず、必ず "error" ステータスで親に返すこと。
```

---

### 4. 並列ディスパッチ

17 リポすべての Subagent を **1 メッセージ内で並列に呼び出す** こと。逐次実行すると数十分かかるので必ず並列化する。

### 5. 集約

全 Subagent の返答 (JSON 1 行) を集計し、`~/reviews/{YESTERDAY}/index.md` を生成する。フォーマット:

```markdown
# 自動レビュー {TODAY} (対象範囲: {YESTERDAY} 以降)

## 指摘あり ({件数})
- [{repo}](./{repo}.md) — Issue: {URL}
- ...

## 既存 Issue ありスキップ ({件数})
- [{repo}](./{repo}.md)
- ...

## LGTM ({件数})
- {repo}
- ...

## 差分なし ({件数})
- {repo}
- ...

## 失敗 ({件数})
- {repo} — {エラー詳細}
- ...
```

### 6. ユーザーへの返答

チャットには下記を返す:

1. 1 行サマリ: `指摘あり: N / LGTM: N / 差分なし: N / 失敗: N`
2. 指摘ありリポと Issue URL の箇条書き
3. 失敗リポとエラー概要 (あれば)
4. index.md の絶対パス (`~/reviews/{YESTERDAY}/index.md`)

冗長な進捗ログや内部状態は出力しない。

## 失敗時の方針

- 個別 Subagent が失敗しても他の Subagent は継続させる (1 リポの失敗で全体停止しない)
- 失敗リポは index.md と返答に明示する。silent に欠落させない
- 親自身が失敗した場合 (日付計算・ディレクトリ作成等) は即座にユーザーへ報告する
