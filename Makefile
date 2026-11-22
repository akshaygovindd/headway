# Packages provided by the Lambda Python runtime; excluded from the zip.
LAMBDA_EXCLUDE := boto3 botocore s3transfer jmespath python-dateutil six urllib3 pyarrow

.PHONY: check lambda-zip tf-validate dbt e2e app-test demo-data demo

DEMO_DIR := .local/demo
DEMO_DATA_URL := file://$(abspath $(DEMO_DIR))/public/boston

check:
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy src
	uv run pytest

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
dbt:
	mkdir -p .local
	uv run --project dbt dbt build --project-dir dbt/headway --profiles-dir dbt/headway --target local
	uv run --project dbt python scripts/check_dbt_docs.py

# Replay samples/boston into a temp dir, build dbt against it and compare key tables.
e2e:
	uv run --project dbt python scripts/e2e.py

# Streamlit app tests (own dependency group; not part of `make check`).
app-test:
	uv run --group app pytest app/tests -q

# Replay the sample, build dbt, export gold and write the live snapshot into .local/demo.
demo-data:
	uv run python scripts/build_demo.py $(DEMO_DIR)

# Build the demo data and launch the app on it.
demo: demo-data
	HEADWAY_DATA_URL=$(DEMO_DATA_URL) uv run --group app streamlit run app/Home.py
