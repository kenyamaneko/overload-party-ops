-- IAM database authentication (Cloud SQL Auth Proxy with --auto-iam-authn)
-- Grant permissions to IAM service account users per-schema.
-- The user name follows the format: <sa-name>@<project-id>.iam
--
-- Schema layout:
--   - 各サービスは自スキーマのみに RW 権限を持つ
--   - サービス間の参照は REST API 経由。DB 越境参照は行わない
--   - matchmaking は DB を使わない (Redis + Pub/Sub のみ)
--   - gateway は自身の gateway スキーマ (game_players) に RW 権限を持ち、
--     加えて newsfeed.news_articles を read-only proxy として参照する
--     (read のみで、書き込みは newsfeed が担う)
--   - game_config は Cloud Firestore Native。RDB 側に shared スキーマは持たない
--
-- Ownership:
--   - SSoT は overload-party-ops リポ (db-migrate/sql/grant_iam.sql)。
--
-- This file is NOT managed by psqldef (which doesn't support DO $$ blocks).
-- Run separately after schema migration (entrypoint.sh handles the ordering).

-- shared スキーマは管理対象外だが、既存クラスタに残っている可能性があるため明示的に削除する。
-- psqldef は target_schema 外のオブジェクトに触らないので、本ファイルでクリーンアップする。
DROP SCHEMA IF EXISTS shared CASCADE;

DO $$
BEGIN
  -- ---------------------------------------------------------------------------
  -- account service account (dev): account スキーマ RW
  -- ---------------------------------------------------------------------------
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'overload-party-account@overload-party-dev.iam') THEN
    GRANT USAGE ON SCHEMA account TO "overload-party-account@overload-party-dev.iam";
    GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA account TO "overload-party-account@overload-party-dev.iam";
    ALTER DEFAULT PRIVILEGES IN SCHEMA account GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO "overload-party-account@overload-party-dev.iam";
  END IF;

  -- ---------------------------------------------------------------------------
  -- card service account (dev): card スキーマ RW
  -- ---------------------------------------------------------------------------
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'overload-party-card@overload-party-dev.iam') THEN
    GRANT USAGE ON SCHEMA card TO "overload-party-card@overload-party-dev.iam";
    GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA card TO "overload-party-card@overload-party-dev.iam";
    ALTER DEFAULT PRIVILEGES IN SCHEMA card GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO "overload-party-card@overload-party-dev.iam";
  END IF;

  -- ---------------------------------------------------------------------------
  -- shop service account (dev): shop スキーマ RW
  -- ---------------------------------------------------------------------------
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'overload-party-shop@overload-party-dev.iam') THEN
    GRANT USAGE ON SCHEMA shop TO "overload-party-shop@overload-party-dev.iam";
    GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA shop TO "overload-party-shop@overload-party-dev.iam";
    ALTER DEFAULT PRIVILEGES IN SCHEMA shop GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO "overload-party-shop@overload-party-dev.iam";
  END IF;

  -- ---------------------------------------------------------------------------
  -- scenario service account (dev): scenario スキーマ RW
  -- ---------------------------------------------------------------------------
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'overload-party-scenario@overload-party-dev.iam') THEN
    GRANT USAGE ON SCHEMA scenario TO "overload-party-scenario@overload-party-dev.iam";
    GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA scenario TO "overload-party-scenario@overload-party-dev.iam";
    ALTER DEFAULT PRIVILEGES IN SCHEMA scenario GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO "overload-party-scenario@overload-party-dev.iam";
  END IF;

  -- ---------------------------------------------------------------------------
  -- battle service account (dev): battle スキーマ RW
  -- ---------------------------------------------------------------------------
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'overload-party-battle@overload-party-dev.iam') THEN
    GRANT USAGE ON SCHEMA battle TO "overload-party-battle@overload-party-dev.iam";
    GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA battle TO "overload-party-battle@overload-party-dev.iam";
    ALTER DEFAULT PRIVILEGES IN SCHEMA battle GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO "overload-party-battle@overload-party-dev.iam";
  END IF;

  -- ---------------------------------------------------------------------------
  -- newsfeed service account (dev): newsfeed スキーマ RW
  -- ---------------------------------------------------------------------------
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'overload-party-newsfeed@overload-party-dev.iam') THEN
    GRANT USAGE ON SCHEMA newsfeed TO "overload-party-newsfeed@overload-party-dev.iam";
    GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA newsfeed TO "overload-party-newsfeed@overload-party-dev.iam";
    ALTER DEFAULT PRIVILEGES IN SCHEMA newsfeed GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO "overload-party-newsfeed@overload-party-dev.iam";
  END IF;

  -- ---------------------------------------------------------------------------
  -- gateway service account (dev): gateway スキーマ RW + newsfeed.news_articles 読取
  --   gateway が所有するのは gateway.game_players (ADR-014 game_players
  --   schema move 後)。newsfeed.news_articles は read-only proxy として
  --   テーブル単位で SELECT 権限だけを持つ (write は newsfeed が担う)。
  --   スキーマ単位ではなくテーブル単位の grant にすることで、将来 newsfeed
  --   が別テーブル (e.g. news_sources) を追加しても最小権限を維持できる。
  -- ---------------------------------------------------------------------------
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'overload-party-gateway@overload-party-dev.iam') THEN
    GRANT USAGE ON SCHEMA gateway TO "overload-party-gateway@overload-party-dev.iam";
    GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA gateway TO "overload-party-gateway@overload-party-dev.iam";
    GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA gateway TO "overload-party-gateway@overload-party-dev.iam";
    ALTER DEFAULT PRIVILEGES IN SCHEMA gateway GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO "overload-party-gateway@overload-party-dev.iam";
    ALTER DEFAULT PRIVILEGES IN SCHEMA gateway GRANT USAGE, SELECT ON SEQUENCES TO "overload-party-gateway@overload-party-dev.iam";

    GRANT USAGE ON SCHEMA newsfeed TO "overload-party-gateway@overload-party-dev.iam";
    GRANT SELECT ON TABLE newsfeed.news_articles TO "overload-party-gateway@overload-party-dev.iam";
  END IF;

  -- ---------------------------------------------------------------------------
  -- stg environment mirrors (same schemas, different IAM role suffix)
  -- ---------------------------------------------------------------------------
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'overload-party-account@overload-party-stg.iam') THEN
    GRANT USAGE ON SCHEMA account TO "overload-party-account@overload-party-stg.iam";
    GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA account TO "overload-party-account@overload-party-stg.iam";
    ALTER DEFAULT PRIVILEGES IN SCHEMA account GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO "overload-party-account@overload-party-stg.iam";
  END IF;

  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'overload-party-card@overload-party-stg.iam') THEN
    GRANT USAGE ON SCHEMA card TO "overload-party-card@overload-party-stg.iam";
    GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA card TO "overload-party-card@overload-party-stg.iam";
    ALTER DEFAULT PRIVILEGES IN SCHEMA card GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO "overload-party-card@overload-party-stg.iam";
  END IF;

  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'overload-party-shop@overload-party-stg.iam') THEN
    GRANT USAGE ON SCHEMA shop TO "overload-party-shop@overload-party-stg.iam";
    GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA shop TO "overload-party-shop@overload-party-stg.iam";
    ALTER DEFAULT PRIVILEGES IN SCHEMA shop GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO "overload-party-shop@overload-party-stg.iam";
  END IF;

  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'overload-party-scenario@overload-party-stg.iam') THEN
    GRANT USAGE ON SCHEMA scenario TO "overload-party-scenario@overload-party-stg.iam";
    GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA scenario TO "overload-party-scenario@overload-party-stg.iam";
    ALTER DEFAULT PRIVILEGES IN SCHEMA scenario GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO "overload-party-scenario@overload-party-stg.iam";
  END IF;

  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'overload-party-battle@overload-party-stg.iam') THEN
    GRANT USAGE ON SCHEMA battle TO "overload-party-battle@overload-party-stg.iam";
    GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA battle TO "overload-party-battle@overload-party-stg.iam";
    ALTER DEFAULT PRIVILEGES IN SCHEMA battle GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO "overload-party-battle@overload-party-stg.iam";
  END IF;

  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'overload-party-newsfeed@overload-party-stg.iam') THEN
    GRANT USAGE ON SCHEMA newsfeed TO "overload-party-newsfeed@overload-party-stg.iam";
    GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA newsfeed TO "overload-party-newsfeed@overload-party-stg.iam";
    ALTER DEFAULT PRIVILEGES IN SCHEMA newsfeed GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO "overload-party-newsfeed@overload-party-stg.iam";
  END IF;

  -- gateway (stg): dev と同じく gateway スキーマ RW + newsfeed.news_articles SELECT。
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'overload-party-gateway@overload-party-stg.iam') THEN
    GRANT USAGE ON SCHEMA gateway TO "overload-party-gateway@overload-party-stg.iam";
    GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA gateway TO "overload-party-gateway@overload-party-stg.iam";
    GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA gateway TO "overload-party-gateway@overload-party-stg.iam";
    ALTER DEFAULT PRIVILEGES IN SCHEMA gateway GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO "overload-party-gateway@overload-party-stg.iam";
    ALTER DEFAULT PRIVILEGES IN SCHEMA gateway GRANT USAGE, SELECT ON SEQUENCES TO "overload-party-gateway@overload-party-stg.iam";

    GRANT USAGE ON SCHEMA newsfeed TO "overload-party-gateway@overload-party-stg.iam";
    GRANT SELECT ON TABLE newsfeed.news_articles TO "overload-party-gateway@overload-party-stg.iam";
  END IF;

  -- ---------------------------------------------------------------------------
  -- prod environment mirrors (same schemas, different IAM role suffix)
  -- ---------------------------------------------------------------------------
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'overload-party-account@overload-party-prod.iam') THEN
    GRANT USAGE ON SCHEMA account TO "overload-party-account@overload-party-prod.iam";
    GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA account TO "overload-party-account@overload-party-prod.iam";
    ALTER DEFAULT PRIVILEGES IN SCHEMA account GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO "overload-party-account@overload-party-prod.iam";
  END IF;

  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'overload-party-card@overload-party-prod.iam') THEN
    GRANT USAGE ON SCHEMA card TO "overload-party-card@overload-party-prod.iam";
    GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA card TO "overload-party-card@overload-party-prod.iam";
    ALTER DEFAULT PRIVILEGES IN SCHEMA card GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO "overload-party-card@overload-party-prod.iam";
  END IF;

  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'overload-party-shop@overload-party-prod.iam') THEN
    GRANT USAGE ON SCHEMA shop TO "overload-party-shop@overload-party-prod.iam";
    GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA shop TO "overload-party-shop@overload-party-prod.iam";
    ALTER DEFAULT PRIVILEGES IN SCHEMA shop GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO "overload-party-shop@overload-party-prod.iam";
  END IF;

  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'overload-party-scenario@overload-party-prod.iam') THEN
    GRANT USAGE ON SCHEMA scenario TO "overload-party-scenario@overload-party-prod.iam";
    GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA scenario TO "overload-party-scenario@overload-party-prod.iam";
    ALTER DEFAULT PRIVILEGES IN SCHEMA scenario GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO "overload-party-scenario@overload-party-prod.iam";
  END IF;

  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'overload-party-battle@overload-party-prod.iam') THEN
    GRANT USAGE ON SCHEMA battle TO "overload-party-battle@overload-party-prod.iam";
    GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA battle TO "overload-party-battle@overload-party-prod.iam";
    ALTER DEFAULT PRIVILEGES IN SCHEMA battle GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO "overload-party-battle@overload-party-prod.iam";
  END IF;

  -- newsfeed は prod では未稼働 (dev のみ)。prod で有効化したら GSA が
  -- 自動的にこの grant 対象に含まれる (IF EXISTS ガードで冪等)。
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'overload-party-newsfeed@overload-party-prod.iam') THEN
    GRANT USAGE ON SCHEMA newsfeed TO "overload-party-newsfeed@overload-party-prod.iam";
    GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA newsfeed TO "overload-party-newsfeed@overload-party-prod.iam";
    ALTER DEFAULT PRIVILEGES IN SCHEMA newsfeed GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO "overload-party-newsfeed@overload-party-prod.iam";
  END IF;

  -- gateway (prod): dev と同じく gateway スキーマ RW + newsfeed.news_articles SELECT。
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'overload-party-gateway@overload-party-prod.iam') THEN
    GRANT USAGE ON SCHEMA gateway TO "overload-party-gateway@overload-party-prod.iam";
    GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA gateway TO "overload-party-gateway@overload-party-prod.iam";
    GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA gateway TO "overload-party-gateway@overload-party-prod.iam";
    ALTER DEFAULT PRIVILEGES IN SCHEMA gateway GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO "overload-party-gateway@overload-party-prod.iam";
    ALTER DEFAULT PRIVILEGES IN SCHEMA gateway GRANT USAGE, SELECT ON SEQUENCES TO "overload-party-gateway@overload-party-prod.iam";

    -- newsfeed schema + news_articles table 未作成のガード:
    -- prod では newsfeed が未稼働のため, schema/table の存在を動的に確認する。
    IF EXISTS (SELECT 1 FROM information_schema.schemata WHERE schema_name = 'newsfeed') THEN
      GRANT USAGE ON SCHEMA newsfeed TO "overload-party-gateway@overload-party-prod.iam";
      IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'newsfeed' AND table_name = 'news_articles') THEN
        GRANT SELECT ON TABLE newsfeed.news_articles TO "overload-party-gateway@overload-party-prod.iam";
      END IF;
    END IF;
  END IF;

  -- ---------------------------------------------------------------------------
  -- matchmaking service account: DB 使用なし (Redis + Pub/Sub のみ)
  -- ---------------------------------------------------------------------------
END
$$;
