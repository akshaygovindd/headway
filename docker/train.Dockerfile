# Forecast training Lambda (linux/arm64): LightGBM and MLflow in their own environment (ml/uv.lock).
# No dbt and no DuckDB extensions: it reads the closed-day Parquet partitions the transform keeps in
# the raw bucket (state/forecast_examples), so its runtime does not depend on the dbt build.
FROM public.ecr.aws/lambda/python:3.12 AS build
COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /usr/local/bin/uv
# LightGBM needs the OpenMP runtime, which the Lambda base image does not ship.
RUN (microdnf install -y libgomp || dnf install -y libgomp) && ls /usr/lib64/libgomp.so.1
WORKDIR /build
COPY ml/pyproject.toml ml/uv.lock ./
RUN uv export --frozen --only-group ml --no-hashes --no-emit-project > requirements.txt \
    && uv pip install --python /var/lang/bin/python3.12 --target /deps --only-binary :all: \
        --no-cache -r requirements.txt \
    && PYTHONPATH=/deps /var/lang/bin/python3.12 -c "import lightgbm, mlflow, duckdb; \
assert duckdb.__version__ == '1.5.6', duckdb.__version__"

FROM public.ecr.aws/lambda/python:3.12
COPY --from=build /usr/lib64/libgomp.so.1 /usr/lib64/libgomp.so.1
COPY --from=build /deps ${LAMBDA_TASK_ROOT}
COPY ml/headway_ml ${LAMBDA_TASK_ROOT}/headway_ml
ARG GIT_SHA=unknown
ENV HOME=/tmp \
    HEADWAY_GIT_SHA=${GIT_SHA} \
    MLFLOW_DISABLE_AGENT_HINT=1
CMD ["headway_ml.train_handler.lambda_handler"]
