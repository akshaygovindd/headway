# Transform Lambda (linux/arm64): dbt + DuckDB in their own environment (dbt pins protobuf<7).
FROM public.ecr.aws/lambda/python:3.12 AS build
COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /usr/local/bin/uv
WORKDIR /build
COPY dbt/pyproject.toml dbt/uv.lock ./
RUN uv export --frozen --only-group dbt --no-hashes --no-emit-project > requirements.txt \
    && uv pip install --python /var/lang/bin/python3.12 --target /deps --only-binary :all: \
        --no-cache -r requirements.txt
# Extensions are installed at build time and copied to /tmp at cold start (the motherduck
# extension downloads its implementation into the extension dir at load time).
RUN PYTHONPATH=/deps /var/lang/bin/python3.12 -c "\
import duckdb; \
con = duckdb.connect(config={'extension_directory': '/duckdb_extensions', 'home_directory': '/tmp'}); \
con.execute('install httpfs'); con.execute('install aws'); con.execute('install motherduck')"

FROM public.ecr.aws/lambda/python:3.12
COPY --from=build /deps ${LAMBDA_TASK_ROOT}
COPY --from=build /duckdb_extensions ${LAMBDA_TASK_ROOT}/duckdb_extensions
COPY dbt/headway ${LAMBDA_TASK_ROOT}/dbt_project
COPY dbt/headway_transform ${LAMBDA_TASK_ROOT}/headway_transform
ENV HOME=/tmp \
    DBT_PROJECT_DIR=${LAMBDA_TASK_ROOT}/dbt_project \
    HEADWAY_DUCKDB_EXTENSIONS_SRC=${LAMBDA_TASK_ROOT}/duckdb_extensions \
    HEADWAY_DUCKDB_EXTENSIONS=/tmp/duckdb_extensions \
    HEADWAY_DUCKDB_PATH=/tmp/headway.duckdb \
    DBT_SEND_ANONYMOUS_USAGE_STATS=False
CMD ["headway_transform.handler.lambda_handler"]
