# Transform Lambda (linux/arm64): dbt + DuckDB in their own environment (dbt pins protobuf<7).
FROM public.ecr.aws/lambda/python:3.12 AS build
COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /usr/local/bin/uv
WORKDIR /build
# grapheme and ratelimit (pure Python, pulled in by elementary-data) publish no wheel.
COPY dbt/pyproject.toml dbt/uv.lock ./
RUN uv export --frozen --only-group dbt --no-hashes --no-emit-project > requirements.txt \
    && uv pip install --python /var/lang/bin/python3.12 --target /deps --only-binary :all: \
        --no-binary grapheme --no-binary ratelimit --no-cache -r requirements.txt
# Extensions are installed at build time and copied to /tmp at cold start (the motherduck
# extension downloads its implementation into the extension dir at load time).
# The duckdb version is constrained by MotherDuck support (see ADR-012); the build fails if it drifts
# from the pin in dbt/pyproject.toml, so extensions are always baked for v1.5.6/linux_arm64.
RUN PYTHONPATH=/deps /var/lang/bin/python3.12 -c "\
import duckdb; \
assert duckdb.__version__ == '1.5.6', duckdb.__version__; \
con = duckdb.connect(config={'extension_directory': '/duckdb_extensions', 'home_directory': '/tmp'}); \
con.execute('install httpfs'); con.execute('install aws'); con.execute('install motherduck')"

# The dbt packages are installed here, once, from the pinned packages.yml / package-lock.yml, and so
# is the internal dbt project of the `edr` report CLI: the function never downloads anything.
COPY dbt/headway /project
COPY docker/install_dbt_packages.py /build/
RUN PYTHONPATH=/deps /var/lang/bin/python3.12 /build/install_dbt_packages.py /project

FROM public.ecr.aws/lambda/python:3.12
COPY --from=build /deps ${LAMBDA_TASK_ROOT}
COPY --from=build /duckdb_extensions ${LAMBDA_TASK_ROOT}/duckdb_extensions
COPY --from=build /project ${LAMBDA_TASK_ROOT}/dbt_project
COPY dbt/headway_transform ${LAMBDA_TASK_ROOT}/headway_transform
ENV HOME=/tmp \
    DBT_PROJECT_DIR=${LAMBDA_TASK_ROOT}/dbt_project \
    HEADWAY_DUCKDB_EXTENSIONS_SRC=${LAMBDA_TASK_ROOT}/duckdb_extensions \
    HEADWAY_DUCKDB_EXTENSIONS=/tmp/duckdb_extensions \
    HEADWAY_DUCKDB_PATH=/tmp/headway.duckdb \
    DBT_SEND_ANONYMOUS_USAGE_STATS=False
CMD ["headway_transform.handler.lambda_handler"]
