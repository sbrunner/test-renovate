GITHUB_REPOSITORY ?= camptocamp/c2cciutils
export DOCKER_BUILDKIT = 1

.PHONY: help
help: ## Display this help message
	@echo "Usage: make <target>"
	@echo
	@echo "Available targets:"
	@grep --extended-regexp --no-filename '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "	%-20s%s\n", $$1, $$2}'

.PHONY: build
build: ## Build the Docker images
	docker build --tag=$(GITHUB_REPOSITORY) .

.PHONY: build-checker
build-checker: ## Build the checker Docker images
	docker build --target=checker --tag=$(GITHUB_REPOSITORY)-checker .

.PHONY: checks
checks: prospector ## Do the checks

.PHONY: prospector
prospector: build-checker ## Run Prospector
	docker run --volume=${PWD}:/app $(GITHUB_REPOSITORY)-checker prospector --ignore-paths=example-project/ --output=pylint --die-on-tool-error

.PHONY: jsonschema
jsonschema: ## Generate files depends on the JSON schema
	jsonschema-gentypes
