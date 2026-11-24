# Packages provided by the Lambda Python runtime; excluded from the zip.
LAMBDA_EXCLUDE := boto3 botocore s3transfer jmespath python-dateutil six urllib3 pyarrow

.PHONY: ml-test backtest dbt-deps slo-report demo-r2 acceptance check lambda-zip tf-validate dbt e2e app-test demo-data demo images-build images-push transform-smoke curate-smoke ingest-smoke images-smoke deploy seed

DEMO_DIR := .local/demo
DEMO_DATA_URL := file://$(abspath $(DEMO_DIR))/public/boston

# Install the pinned dbt packages (Elementary, dbt_utils) when dbt/headway/dbt_packages is missing.
dbt-deps:
	uv run python scripts/dbt_deps.py

check: dbt-deps
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy src dbt/headway_transform
	uv run pytest
	$(MAKE) ml-test

# Forecast evaluation (ml/ has its own environment; `uv run --project ml` syncs it from ml/uv.lock,
# so a clean clone needs nothing else).
ml-test:
	cd ml && uv run mypy headway_ml tests
	cd ml && uv run pytest -q

# Backtest the forecast baselines on a DuckDB built with HEADWAY_ENABLE_R3=true (default: the
# demo-r2 build). Results: .local/backtest/backtest.duckdb (agg_forecast_backtest), MLflow
# .local/mlflow. Pass BACKTEST_ARGS="--db ... --out ...".
BACKTEST_ARGS ?=
backtest:
	HEADWAY_ENABLE_R3=true PYTHONPATH=ml uv run --project ml python -m headway_ml.backtest $(BACKTEST_ARGS)

lambda-zip:
	rm -rf build/lambda build/ingest.zip build/requirements.txt
	mkdir -p build/lambda
	uv export --frozen --no-dev --no-hashes --no-emit-project \
		$(foreach p,$(LAMBDA_EXCLUDE),--no-emit-package $(p)) > build/requirements.txt
	uv pip install --quiet --target build/lambda --python-platform aarch64-manylinux2014 \
		--python-version 3.12 --only-binary :all: -r build/requirements.txt
	cp -R src/headway build/lambda/headway
	cp -R config build/lambda/config
	uv run python scripts/make_zip.py build/lambda build/ingest.zip
	@ls -l build/ingest.zip

tf-validate:
	terraform fmt -check -recursive infra
	cd infra/bootstrap && terraform init -backend=false -input=false >/dev/null && terraform validate
	cd infra/main && terraform init -backend=false -input=false >/dev/null && terraform validate

# Build the dbt project against .local/replay (populate it first with
# `uv run headway replay --source samples/boston --target .local/replay --city boston`).
dbt: dbt-deps
	mkdir -p .local
	uv run --project dbt dbt build --project-dir dbt/headway --profiles-dir dbt/headway --target local
	uv run --project dbt python scripts/check_dbt_docs.py

# Replay samples/boston into a temp dir, build dbt against it and compare key tables.
e2e: dbt-deps
	uv run --project dbt python scripts/e2e.py

# Release 1 acceptance report (PASS/FAIL/MANUAL per A1-A12) from the repo, the public gold and
# read-only AWS; also refreshes the latency and cost rows of docs/MEASUREMENTS.md.
acceptance:
	uv run python scripts/acceptance.py

# Streamlit app tests (own dependency group; not part of `make check`).
app-test: dbt-deps
	uv run --group app pytest app/tests -q

# Replay the sample, build dbt, export gold and write the live snapshot into .local/demo.
demo-data:
	uv run python scripts/build_demo.py $(DEMO_DIR)

# Build the demo data and launch the app on it.
demo: demo-data
	HEADWAY_DATA_URL=$(DEMO_DATA_URL) uv run --group app streamlit run app/Home.py

# Write docs/SLO_REPORT.md from the published SLO tables over HTTPS (HEADWAY_DATA_URL, else the
# public bucket); no credentials.
slo-report:
	uv run python scripts/slo_report.py

# Release 2 demo on the real 27 h spike capture (.local/spike_root): builds every model with
# HEADWAY_ENABLE_R2=true, exports gold to .local/demo-r2 and launches the app on it.
demo-r2:
	uv run python scripts/demo_r2.py

# Container images for the curate/static and transform Lambdas (linux/arm64).
IMAGE_TAG ?= $(shell git rev-parse --short HEAD)
AWS_REGION ?= us-east-1
IMAGES := curate transform

images-build:
	@set -e; for name in $(IMAGES); do \
		docker buildx build --platform linux/arm64 --provenance=false --sbom=false --load -f docker/$$name.Dockerfile \
			-t headway-$$name:$(IMAGE_TAG) -t headway-$$name:local .; \
	done
	@for name in $(IMAGES); do \
		docker image inspect headway-$$name:local --format "headway-$$name:$(IMAGE_TAG) {{.Size}}" \
			| awk '{printf "%s  %.0f MB\n", $$1, $$2 / 1000000}'; \
	done

# Lambda-conditions smoke tests (docker + the images from `make images-build`): Runtime Interface
# Emulator, read-only root, writable /tmp only, unprivileged user, HOME unset, local fixtures.
transform-smoke:
	scripts/smoke_image.sh transform

curate-smoke:
	scripts/smoke_image.sh curate

# Ingest is a zip function: this runs the built package on the Lambda Python image at 128 MB.
ingest-smoke: lambda-zip
	scripts/smoke_image.sh ingest

images-smoke: curate-smoke transform-smoke ingest-smoke

# Logs in to ECR, tags with the git short sha and pushes. The ECR repositories must already exist
# (see docs/DEPLOY.md). Prints the image URIs to put in terraform.tfvars.
images-push: images-build
	@set -eo pipefail; \
	account=$$(aws sts get-caller-identity --query Account --output text); \
	registry=$$account.dkr.ecr.$(AWS_REGION).amazonaws.com; \
	aws ecr get-login-password --region $(AWS_REGION) \
		| docker login --username AWS --password-stdin $$registry; \
	for name in $(IMAGES); do \
		docker tag headway-$$name:$(IMAGE_TAG) $$registry/headway-$$name:$(IMAGE_TAG); \
		docker push $$registry/headway-$$name:$(IMAGE_TAG); \
	done; \
	echo; \
	echo "curate_image_uri    = \"$$registry/headway-curate:$(IMAGE_TAG)\""; \
	echo "transform_image_uri = \"$$registry/headway-transform:$(IMAGE_TAG)\""

# Push the images, point terraform.tfvars at the new git short sha, plan and apply. Aborts before
# the apply unless the plan destroys nothing. Needs AWS credentials and the ECR repositories.
TFVARS := infra/main/terraform.tfvars

deploy: images-push
	@set -eo pipefail; \
	test -f $(TFVARS) || { echo "$(TFVARS) not found" >&2; exit 1; }; \
	sed -i.bak -E \
		-e 's|^(curate_image_uri[[:space:]]*=[[:space:]]*"[^"]*headway-curate:)[^"]*"|\1$(IMAGE_TAG)"|' \
		-e 's|^(transform_image_uri[[:space:]]*=[[:space:]]*"[^"]*headway-transform:)[^"]*"|\1$(IMAGE_TAG)"|' \
		$(TFVARS); \
	rm -f $(TFVARS).bak; \
	grep -q 'headway-curate:$(IMAGE_TAG)"' $(TFVARS) && grep -q 'headway-transform:$(IMAGE_TAG)"' $(TFVARS) \
		|| { echo "could not set both image tags to $(IMAGE_TAG) in $(TFVARS)" >&2; exit 1; }; \
	cd infra/main; \
	terraform plan -no-color -input=false -out=tfplan | tee tfplan.txt; \
	if grep -q '^No changes' tfplan.txt; then echo "nothing to apply"; exit 0; fi; \
	grep -Eq '[0-9]+ to destroy' tfplan.txt && grep -Eq ' 0 to destroy' tfplan.txt \
		|| { echo "aborting: the plan destroys resources (expected 0 to destroy)" >&2; exit 1; }; \
	terraform apply -input=false tfplan

# Seed the pipeline in order: static GTFS, manifests backfill, curate, transform. Prints each result
# and stops at the first function error.
seed:
	@set -eo pipefail; \
	invoke() { \
		name=$$1; payload=$$2; timeout=$$3; out=$$(mktemp); \
		echo "== $$name $$payload"; \
		err=$$(aws lambda invoke --region $(AWS_REGION) --function-name $$name --cli-read-timeout $$timeout \
			--cli-binary-format raw-in-base64-out --payload "$$payload" \
			--query FunctionError --output text $$out); \
		cat $$out; echo; rm -f $$out; \
		[ "$$err" = "None" ] || { echo "$$name failed: $$err" >&2; exit 1; }; \
	}; \
	invoke headway-static '{}' 330; \
	invoke headway-curate '{"task":"backfill_manifests"}' 330; \
	invoke headway-curate '{}' 330; \
	invoke headway-transform '{}' 930
