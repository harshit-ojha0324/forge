# Forge — day-to-day commands. `make help` lists them.
.PHONY: help test up down demo evals loadgen agent tf-validate helm-lint

help:
	@grep -E '^[a-z-]+:.*##' Makefile | awk -F':.*## ' '{printf "  %-12s %s\n", $$1, $$2}'

test: ## run gateway unit tests
	cd services/gateway && .venv/bin/python -m pytest -q

up: ## start the local stack (gateway, mocks, redis, prometheus, grafana, jaeger)
	docker compose -f deploy/local/docker-compose.yml up -d --build

down: ## stop the local stack
	docker compose -f deploy/local/docker-compose.yml down

demo: ## the failover demo: load + mid-run primary kill + recovery
	./scripts/demo-failover.sh

EVAL_PY ?= services/gateway/.venv/bin/python

evals: ## run the eval gate against the local stack (incl. failover drill)
	$(EVAL_PY) evals/run_evals.py --drill

loadgen: ## 60s of load against the local gateway
	python3 loadtest/loadgen.py --duration 60 --concurrency 12

agent: ## run the smart-city agent once against the local gateway
	cd services/agent-demo && python3 agent.py

TF ?= $(CURDIR)/.tools/terraform

tf-validate: ## terraform validate + fmt check (AWS and archived GCP)
	for d in infra/aws infra/gcp; do \
		(cd $$d && $(TF) init -backend=false > /dev/null && $(TF) validate && $(TF) fmt -check -recursive) || exit 1; \
	done

helm-lint: ## lint all helm charts
	helm lint deploy/helm/*
