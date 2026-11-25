"""Lambda entry point: the FastAPI app behind an HTTP API (payload format 2.0) through Mangum."""

from mangum import Mangum

from headway_api.app import create_app

lambda_handler = Mangum(create_app(), lifespan="off")
