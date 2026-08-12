# セットアップ

## ローカル開発

Cloud Run 系サービスは Makefile でビルド・デプロイする。

```bash
make build-db-migrate               # db-migrate イメージのビルド
make deploy-db-migrate              # ビルド + AR push + Cloud Run Job 更新
make help                           # コマンド一覧
```
