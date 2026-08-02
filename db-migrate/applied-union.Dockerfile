# 環境に適用した schema union を記録として保持するイメージ。
# 次回のマイグレーションが破壊的変更チェックの比較元として取り出す。
#
# ビルドコンテキストは schema_union.sql を含むディレクトリ (CI では db-migrate/sql)。
FROM scratch
COPY schema_union.sql /schema_union.sql
