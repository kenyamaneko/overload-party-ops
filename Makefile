REGISTRY   := asia-northeast1-docker.pkg.dev
AR_PROJECT := keyandnotes-platform
AR_REPO    := overload-party
REGION     := asia-northeast1
PROJECT    ?= overload-party-dev

JOBS := nightly-review cost-monitor drift-monitor db-migrate

IMAGE_BASE = $(REGISTRY)/$(AR_PROJECT)/$(AR_REPO)

# nightly-review は Cloud Run Job が 2 つある
CLOUD_RUN_JOBS_nightly-review := nightly-review-diff nightly-review-full
CLOUD_RUN_JOBS_cost-monitor   := cost-monitor
CLOUD_RUN_JOBS_drift-monitor  := drift-monitor
CLOUD_RUN_JOBS_db-migrate     := db-migrate

.PHONY: help build-all push-all

help: ## Show this help
	@grep -E '^[a-zA-Z_%-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

build-%: ## Build Docker image (e.g. make build-cost-monitor)
	docker build -t $(IMAGE_BASE)/$*:latest $*/

push-%: build-% ## Build and push to Artifact Registry (e.g. make push-cost-monitor)
	docker push $(IMAGE_BASE)/$*:latest

deploy-%: push-% ## Build, push, and update Cloud Run Job (e.g. make deploy-cost-monitor)
	@for job in $(CLOUD_RUN_JOBS_$*); do \
	  echo "Updating $${job}..."; \
	  gcloud run jobs update $${job} \
	    --region $(REGION) \
	    --project $(PROJECT) \
	    --image $(IMAGE_BASE)/$*:latest \
	    --quiet; \
	done

build-all: $(addprefix build-,$(JOBS)) ## Build all job images

push-all: $(addprefix push-,$(JOBS)) ## Build and push all job images
