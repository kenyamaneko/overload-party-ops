# Review Yesterday

各リポジトリの前日 00:00 JST 以降の差分を Claude Code のスラッシュコマンド `/review-yesterday` で並列レビューするシステム。

## 旧設計との違い

| 項目 | 旧 (Cloud Run Job) | 新 (スラッシュコマンド) |
|---|---|---|
| 実行基盤 | Cloud Run Jobs (夜間 cron) | ローカル Claude Code (手動トリガ) |
| API コスト | Anthropic API 直叩き ($30-40/月) | Max プランで実質無料 |
| 入力範囲 | 差分テキストのみ | 差分 + リポジトリ全体 (clone して Read/Grep/Glob) |
| 並列化 | リポジトリ逐次 | 17 リポを Subagent で並列 |
| 起票先 | 各リポの GitHub Issue (`auto-review` ラベル) | 同左 + ローカル `~/reviews/{前日日付}/` に Markdown |

## 使い方

Claude Code を任意のディレクトリで起動し、`/review-yesterday` を実行する。

実行時の動作:

1. 当該日の `~/reviews/{前日日付}/` ディレクトリを作成
2. [repos.yaml](repos.yaml) の 17 リポすべてに対して `general-purpose` Subagent を **並列**でディスパッチ
3. 各 Subagent が以下を担当:
   - `gh repo clone` でローカルにチェックアウト
   - 前日 00:00 JST 以降の commit / 差分を `gh api` で取得
   - リポ全体を Read/Grep/Glob で参照しながら [review_criteria.yaml](review_criteria.yaml) の観点で評価
   - 結果を `~/reviews/{前日日付}/{repo}.md` に書き出し
   - 指摘ありなら各リポに GitHub Issue を起票 (`auto-review` ラベル、同タイトル既存ならスキップ)
4. 親エージェントが `~/reviews/{前日日付}/index.md` を集約生成
5. チャットにサマリと Issue URL 一覧を返す

## 設定ファイル

### [repos.yaml](repos.yaml)

レビュー対象リポジトリと対象ブランチの SSoT。リポジトリを増減させたい場合はこのファイルだけ編集する。

```yaml
- name: overload-party-common
  branch: main
- name: overload-party-shop
  branch: develop
```

### [review_criteria.yaml](review_criteria.yaml)

Subagent に渡すレビュー観点の SSoT。カテゴリと items を YAML で管理し、スラッシュコマンドが読み込んで Subagent プロンプトに展開する。

## スラッシュコマンドの実体

`.claude/commands/review-yesterday.md` がオーケストレーション本体。Markdown 本文がそのまま Claude Code のプロンプトとして注入され、本ディレクトリの YAML を Read してから Subagent をディスパッチする。

スラッシュコマンドは Claude Code の規約上 `.claude/commands/` 配下にしか配置できないため、本ディレクトリと分離されている。
