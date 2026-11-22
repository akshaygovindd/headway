# Packages provided by the Lambda Python runtime; excluded from the zip.
LAMBDA_EXCLUDE := boto3 botocore s3transfer jmespath python-dateutil six urllib3

.PHONY: check lambda-zip tf-validate

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
