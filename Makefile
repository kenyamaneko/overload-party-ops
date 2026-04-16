REGISTRY   := asia-northeast1-docker.pkg.dev
AR_PROJECT := keyandnotes-platform
AR_REPO    := overload-party
REGION     := asia-northeast1
PROJECT    ?= overload-party-ops

IMAGE_BASE = $(REGISTRY)/$(AR_PROJECT)/$(AR_REPO)

.PHONY: help build-db-migrate push-db-migrate deploy-db-migrate \
        build-slack-commands push-slack-commands deploy-service-slack-commands \
        build-all push-all

help: ## Show this help
	@grep -E '^[a-zA-Z_%-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-30s\033[0m %s\n", $$1, $$2}'

# --- db-migrate (Cloud Run Job) ---

build-db-migrate: ## Build db-migrate image
	docker build -t $(IMAGE_BASE)/db-migrate:latest db-migrate/

push-db-migrate: build-db-migrate ## Build and push db-migrate image
	docker push $(IMAGE_BASE)/db-migrate:latest

deploy-db-migrate: push-db-migrate ## Build, push, and update db-migrate Cloud Run Job
	gcloud run jobs update db-migrate \
	  --region $(REGION) \
	  --project $(PROJECT) \
	  --image $(IMAGE_BASE)/db-migrate:latest \
	  --quiet

# --- slack-commands (Cloud Run Service) ---

build-slack-commands: ## Build slack-commands image
	docker build -t $(IMAGE_BASE)/slack-commands:latest slack-commands/

push-slack-commands: build-slack-commands ## Build and push slack-commands image
	docker push $(IMAGE_BASE)/slack-commands:latest

deploy-service-slack-commands: push-slack-commands ## Build, push, and update slack-commands Cloud Run Service
	gcloud run services update slack-commands \
	  --region $(REGION) \
	  --project $(PROJECT) \
	  --image $(IMAGE_BASE)/slack-commands:latest \
	  --quiet

# --- aggregate ---

build-all: build-db-migrate build-slack-commands ## Build all images

push-all: push-db-migrate push-slack-commands ## Build and push all images
