# Curate and static GTFS Lambdas (linux/arm64). One image, two commands:
#   headway.curate.handler.curate_handler   (default)
#   headway.curate.handler.static_handler   (image_config.command on the static function)
FROM public.ecr.aws/lambda/python:3.12 AS build
COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /usr/local/bin/uv
WORKDIR /build
COPY pyproject.toml uv.lock ./
# boto3 and friends ship with the Lambda base image.
RUN uv export --frozen --no-dev --no-hashes --no-emit-project \
        --no-emit-package boto3 --no-emit-package botocore --no-emit-package s3transfer \
        --no-emit-package jmespath --no-emit-package python-dateutil --no-emit-package six \
        --no-emit-package urllib3 > requirements.txt \
    && uv pip install --python /var/lang/bin/python3.12 --target /deps --only-binary :all: \
        --no-cache -r requirements.txt

FROM public.ecr.aws/lambda/python:3.12
COPY --from=build /deps ${LAMBDA_TASK_ROOT}
COPY src/headway ${LAMBDA_TASK_ROOT}/headway
COPY config ${LAMBDA_TASK_ROOT}/config
ENV HEADWAY_CONFIG_DIR=${LAMBDA_TASK_ROOT}/config/cities
CMD ["headway.curate.handler.curate_handler"]
