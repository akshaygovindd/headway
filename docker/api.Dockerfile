# Public metric API (linux/arm64): FastAPI behind Mangum, reads the public gold over HTTPS.
# The httpfs extension is baked at build time and only loaded at run time (read-only root, HOME
# unset): the function never downloads anything. No credentials, no AWS SDK use.
FROM public.ecr.aws/lambda/python:3.12 AS build
COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /usr/local/bin/uv
WORKDIR /build
COPY pyproject.toml uv.lock ./
RUN uv export --frozen --only-group api --no-hashes --no-emit-project > requirements.txt \
    && uv pip install --python /var/lang/bin/python3.12 --target /deps --only-binary :all: \
        --no-cache -r requirements.txt
# The duckdb version is the pin of pyproject.toml (ADR-012); extensions are baked for linux_arm64.
RUN PYTHONPATH=/deps /var/lang/bin/python3.12 -c "\
import duckdb; \
assert duckdb.__version__ == '1.5.6', duckdb.__version__; \
con = duckdb.connect(config={'extension_directory': '/duckdb_extensions', 'home_directory': '/tmp'}); \
con.execute('install httpfs')"

FROM public.ecr.aws/lambda/python:3.12
COPY --from=build /deps ${LAMBDA_TASK_ROOT}
COPY --from=build /duckdb_extensions ${LAMBDA_TASK_ROOT}/duckdb_extensions
# The API and the query code it reuses from the analyst tools (pure Python over DuckDB).
COPY src/headway_api ${LAMBDA_TASK_ROOT}/headway_api
COPY src/headway_mcp ${LAMBDA_TASK_ROOT}/headway_mcp
ENV HEADWAY_DUCKDB_EXTENSIONS_DIR=${LAMBDA_TASK_ROOT}/duckdb_extensions \
    HEADWAY_DUCKDB_HOME=/tmp \
    HOME=/tmp
CMD ["headway_api.handler.lambda_handler"]
