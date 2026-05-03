# Auto Review

各リポジトリの前日 00:00 JST 以降の差分を Claude Code のスラッシュコマンド `/review-yesterday` で並列レビューする自動コードレビューシステム。GitHub Issue 起票時のラベル名 (`auto-review`) と命名を揃えている。

## 使い方

Claude Code を任意のディレクトリで起動し、`/review-yesterday` を実行する。引数でリポを絞り込めば一部リポのみ対象にできる (`overload-party-` プレフィックスは省略可)。

```
/review-yesterday                  # 全リポ
/review-yesterday gateway          # overload-party-gateway のみ
/review-yesterday gateway battle   # 2 リポ並列
```

実行時の動作:

1. 当該日の `~/workspace/key_and_notes/overload-party/review/{前日日付}/` ディレクトリを作成
2. 対象リポ (引数指定なら指定分、なしなら [repos.yaml](repos.yaml) 全 17 リポ) に対して `general-purpose` Subagent を **並列**でディスパッチ
3. 各 Subagent が以下を担当:
   - `gh repo clone` でローカルにチェックアウト
   - 前日 00:00 JST 以降の commit / 差分を `gh api` で取得
   - リポ全体を Read/Grep/Glob で参照しながら [review_criteria.yaml](review_criteria.yaml) の観点で評価
   - 各指摘に重要度 (`critical` / `high` / `medium` / `low`) を付与
   - 結果を `~/workspace/key_and_notes/overload-party/review/{前日日付}/{repo}.md` に書き出し
   - 指摘ありなら各リポに GitHub Issue を起票 (`auto-review` ラベル、同タイトル既存ならスキップ)
4. 親エージェントが `~/workspace/key_and_notes/overload-party/review/{前日日付}/index.md` を集約生成 (重要度の高い順にソート)
5. チャットにサマリ (重要度別件数を含む) と Issue URL 一覧を返す

## 重要度

| 重要度 | 適用基準 |
|---|---|
| `critical` | 本番影響リスクあり (バグ・セキュリティ脆弱性・データ破損・認証認可の穴) |
| `high` | 設計違反・主要機能の不整合・dead code・エラー握りつぶし・テスト不足で再発リスクあり |
| `medium` | 構成乱れ・docs と実装の乖離・命名の一貫性欠如・責務分離の改善余地 |
| `low` | 軽微なコメント・スタイル・将来的な改善提案 |

判断に迷う場合は **高い方** を選ぶ運用 (見落とし防止)。

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
