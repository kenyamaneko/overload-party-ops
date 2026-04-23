# Nightly Review コンテナ。Cloud Run Jobs から起動され、各リポジトリの前日差分を
# Claude Code CLI でレビューして GitHub Issue / Slack 通知を作る。
#
# ランタイム要件:
#   - Python 3.12 (review.py 本体 / pyyaml)
#   - Node.js 22     (npm から @anthropic-ai/claude-code を入れるため)
#   - gh CLI         (GitHub API / Issue 作成 / ラベル操作)
#   - claude CLI     (npm グローバル経由、review.py が subprocess 経由で呼ぶ)
FROM python:3.12-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_NO_CACHE_DIR=1 \
    NPM_CONFIG_UPDATE_NOTIFIER=false

# apt の鍵追加に必要な最低限のユーティリティを先に入れる。
# curl/gnupg は NodeSource / GitHub CLI apt リポの鍵取り込みで使うため必須。
# ca-certificates が無いと HTTPS apt リポから fetch できない。
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        gnupg \
    && rm -rf /var/lib/apt/lists/*

# Node.js 22 を NodeSource 経由でインストール。
# apt のデフォルト Node は古く @anthropic-ai/claude-code の要件を満たさないため、
# NodeSource の 22.x チャネルを明示的に使う。
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# gh CLI を GitHub 公式 apt リポからインストール。
# tarball ではなく apt 経由にすることで、base image の更新と鍵ローテーションを
# apt の仕組みに寄せて運用を単純化する。
RUN mkdir -p -m 755 /etc/apt/keyrings \
    && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
        | tee /etc/apt/keyrings/githubcli-archive-keyring.gpg > /dev/null \
    && chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
        > /etc/apt/sources.list.d/github-cli.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends gh \
    && rm -rf /var/lib/apt/lists/*

# Python 依存。review.py が import する pyyaml のみ。
RUN pip install --no-cache-dir pyyaml==6.*

# Claude Code CLI (review.py の subprocess から `claude` として呼ばれる)
RUN npm install -g @anthropic-ai/claude-code

WORKDIR /app

# review.py と同階層の設定 / ヘルパのみをコピーする。
# test_review.py / __pycache__ / .pytest_cache / .DS_Store / README.md は
# .dockerignore で除外するため、ここでワイルドカード COPY しても入らない。
COPY review.py gh_client.py repos.yaml review_criteria.yaml /app/

ENTRYPOINT ["python3", "/app/review.py"]
