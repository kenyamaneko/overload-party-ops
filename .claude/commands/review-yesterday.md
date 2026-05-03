---
description: 前日 00:00 JST 以降の差分を Subagent で並列レビューし、~/workspace/key_and_notes/overload-party/review/{前日日付}/ に書き出して指摘があれば各リポに Issue 起票する。引数でリポを絞り込み可能
allowed-tools: Bash, Agent, Read, Write
argument-hint: "[repo ...]"
---

# /review-yesterday

前日 00:00 JST 以降の各リポジトリの差分を、リポ全体を読みながら並列でレビューする。
詳細な背景・仕様は @auto-review/README.md を参照。

## 引数

`$ARGUMENTS` にスペース区切りでレビュー対象リポ名を渡せる。`overload-party-` プレフィックスは省略可能。

- 引数なし: @auto-review/repos.yaml の全リポを対象とする (本番運用)
- 引数あり: 渡されたリポ名のみを対象とする (スモークテスト用途)

例:

```
/review-yesterday                          # 全リポ
/review-yesterday gateway                  # overload-party-gateway のみ
/review-yesterday gateway battle           # 2 リポ並列
/review-yesterday overload-party-gateway   # フルネームでも可
```

## 全体方針

- このコマンド (親) は **オーケストレーション専任**。レビュー本体は Subagent が実行する
- 対象リポと観点は @auto-review/repos.yaml と @auto-review/review_criteria.yaml を SSoT とする
- これらを Read してから、対象リポ数ぶんの `general-purpose` Subagent を **すべて並列で** 投げる (1 メッセージ内で複数 Agent 呼び出し)
- Subagent は各自で `gh repo clone` してリポ全体を Read/Grep/Glob で参照し、観点に沿ってレビューする
- 結果は `~/workspace/key_and_notes/overload-party/review/{前日日付}/{repo}.md` に書き出す。指摘ありなら GitHub Issue も起票する
- 全 Subagent 完了後、親が `index.md` を集約生成してチャットに返す

## 重要度の定義

各指摘には下記いずれかの重要度を必ず付ける。silent に省略しないこと。

| 重要度 | 適用基準 |
|---|---|
| `critical` | 本番影響リスクあり (バグ・セキュリティ脆弱性・データ破損・認証認可の穴) |
| `high` | 設計違反・主要機能の不整合・dead code・エラー握りつぶし・テスト不足で再発リスクあり |
| `medium` | 構成乱れ・docs と実装の乖離・命名の一貫性欠如・責務分離の改善余地 |
| `low` | 軽微なコメント・スタイル・将来的な改善提案 |

判断に迷う場合は **高い方** を選ぶ (見落としを防ぐため)。

## 手順

### 1. 設定ファイルの読み込みと対象リポ決定

- @auto-review/repos.yaml を Read してレビュー対象 `(name, branch)` の全リストを取得
- @auto-review/review_criteria.yaml を Read して観点を取得し、Subagent プロンプトに差し込む Markdown を組み立てる
  - フォーマット: 各カテゴリを `## {name}` セクション、items を箇条書きにする
  - YAML 構造に異常 (categories キー欠落 / items が空) があれば、フォールバックせずユーザーに報告して終了する
- `$ARGUMENTS` を空白で split して対象リポ名リストを得る
  - 空なら repos.yaml の全リポを対象とする
  - 非空なら下記の正規化を適用してから repos.yaml と突合する:
    - 引数が `overload-party-` で始まらない場合、`overload-party-` を前置する (例: `gateway` → `overload-party-gateway`)
    - 既に `overload-party-` で始まる場合はそのまま使う
  - 正規化後の名前が repos.yaml に存在しない場合は、フォールバックせず該当不明リポ名 (正規化後) と利用可能なリポ一覧をユーザーに報告して終了する (typo の silent skip 防止)

### 2. 日付計算と出力ディレクトリ作成

JST で「前日」と「実行日」を計算する。前日日付がレビュー対象範囲の起点、実行日が Issue タイトルに使う。

```bash
TODAY=$(TZ=Asia/Tokyo date +%Y-%m-%d)
YESTERDAY=$(TZ=Asia/Tokyo date -v-1d +%Y-%m-%d)  # macOS BSD date
OUTPUT_DIR=~/workspace/key_and_notes/overload-party/review/$YESTERDAY
mkdir -p "$OUTPUT_DIR"
```

以降のテンプレート中の `{OUTPUT_DIR}` は上記 `~/workspace/key_and_notes/overload-party/review/{YESTERDAY}` を指す。

### 3. Subagent への指示テンプレート

対象リポの各エントリ `(name, branch)` ごとに `general-purpose` Subagent を起動する。テンプレート中の `{...}` は親が埋める。`{REVIEW_CRITERIA_MD}` は Step 1 で組み立てた Markdown。

---

```
あなたは {repo} (ブランチ: {branch}) の自動コードレビュアです。
前日 {YESTERDAY} 00:00 JST 以降の差分をレビューしてください。

## 出力ファイル
{OUTPUT_DIR}/{repo}.md

## 手順

### Step 1: 差分の有無確認

```bash
COMMITS=$(gh api "repos/kenyamaneko/{repo}/commits?sha={branch}&since={YESTERDAY}T00:00:00%2B09:00" --jq 'length')
echo "$COMMITS"
```

`$COMMITS` が 0 なら、レビュー不要。`{OUTPUT_DIR}/{repo}.md` に下記だけ書いて Step 6 にスキップ:

```
No changes since {YESTERDAY} 00:00 JST.
```

そして親への返答は "no_changes" ステータスとする。

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

{REVIEW_CRITERIA_MD}

### Step 5: 結果ファイルの書き出し

`{OUTPUT_DIR}/{repo}.md` に Markdown で書く。各指摘には重要度 `critical` / `high` / `medium` / `low` のいずれかを必ず付ける (定義は親プロンプトの「重要度の定義」セクションに従う)。判断に迷う場合は高い方を選ぶ。

フォーマット:

```
# {repo} 自動レビュー ({YESTERDAY} 以降)

## 対象コミット
- {hash} {subject} ({author})
- ...

## 重要度別件数
- critical: N
- high: N
- medium: N
- low: N

## 指摘

### {観点カテゴリ名}
- **重要度**: critical
- **ファイル**: `path/to/file.go:123-145`
- **指摘**: ...
- **改善案**: ...

### {観点カテゴリ名}
- **重要度**: high
- ...
```

指摘がない場合の本文は `LGTM` の一行のみとする (LGTM 判定のため後続処理が文字列マッチする)。

### Step 6: Issue 起票 (指摘ありの場合のみ)

LGTM または "no_changes" の場合は Issue を作らない。指摘がある場合のみ実行する。

```bash
TITLE_PREFIX="[自動レビュー {TODAY}] 差分"
EXISTING=$(gh issue list --repo kenyamaneko/{repo} --search "in:title \"$TITLE_PREFIX\"" --state open --json number --jq 'length')
if [ "$EXISTING" = "0" ]; then
  gh label create auto-review --repo kenyamaneko/{repo} --color 0e8a16 --description "Nightly auto-review" 2>/dev/null || true
  ISSUE_URL=$(gh issue create \
    --repo kenyamaneko/{repo} \
    --title "$TITLE_PREFIX {repo}" \
    --label auto-review \
    --body-file {OUTPUT_DIR}/{repo}.md)
  echo "$ISSUE_URL"
else
  echo "skipped (existing issue)"
fi
```

### Step 7: 親への返答

次の JSON 形式で 1 行返す。それ以外の冗長な文章は不要。`severity_counts` は Step 5 で集計した重要度別件数を入れる。

- 差分なし: `{"repo": "{repo}", "status": "no_changes"}`
- LGTM: `{"repo": "{repo}", "status": "lgtm"}`
- 指摘あり (Issue 起票成功): `{"repo": "{repo}", "status": "issues", "severity_counts": {"critical": N, "high": N, "medium": N, "low": N}, "issue_url": "<URL>", "review_path": "{OUTPUT_DIR}/{repo}.md"}`
- 指摘あり (Issue 既存スキップ): `{"repo": "{repo}", "status": "issues", "severity_counts": {"critical": N, "high": N, "medium": N, "low": N}, "issue_url": null, "skipped_existing": true, "review_path": "{OUTPUT_DIR}/{repo}.md"}`
- 失敗: `{"repo": "{repo}", "status": "error", "error": "<短い説明>"}`

エラーは silent に握りつぶさず、必ず "error" ステータスで親に返すこと。
```

---

### 4. 並列ディスパッチ

Step 1 で確定した対象リポすべての Subagent を **1 メッセージ内で並列に呼び出す** こと。逐次実行すると数十分かかるので必ず並列化する。

### 5. 集約

全 Subagent の返答 (JSON 1 行) を集計し、`{OUTPUT_DIR}/index.md` を生成する。指摘ありリポは **重要度の高い順** (critical → high → medium → low) でソートする (リポ内の最高重要度をソートキーにする)。

フォーマット:

```markdown
# 自動レビュー {TODAY} (対象範囲: {YESTERDAY} 以降)

## 全体サマリ
- critical: N 件 (X リポ)
- high: N 件 (X リポ)
- medium: N 件 (X リポ)
- low: N 件 (X リポ)

## 指摘あり ({件数})
- [{repo}](./{repo}.md) — critical:N high:N medium:N low:N — Issue: {URL}
- ...

## 既存 Issue ありスキップ ({件数})
- [{repo}](./{repo}.md) — critical:N high:N medium:N low:N
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
2. 重要度別の合計件数: `critical: N / high: N / medium: N / low: N`
3. 指摘ありリポと Issue URL の箇条書き (重要度の高い順)
4. 失敗リポとエラー概要 (あれば)
5. index.md の絶対パス (`{OUTPUT_DIR}/index.md`)

冗長な進捗ログや内部状態は出力しない。

## 失敗時の方針

- 個別 Subagent が失敗しても他の Subagent は継続させる (1 リポの失敗で全体停止しない)
- 失敗リポは index.md と返答に明示する。silent に欠落させない
- 親自身が失敗した場合 (設定読み込み・日付計算・ディレクトリ作成等) は即座にユーザーへ報告する
