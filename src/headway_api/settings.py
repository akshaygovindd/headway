"""Settings from the environment. No credentials: the API reads public HTTPS objects only."""

import os
from dataclasses import dataclass

from headway_mcp import config

DEFAULT_CORS = "https://headway.example.vercel.app"  # placeholder: set API_CORS_ORIGINS
DEFAULT_CACHE_DIR = "/tmp/headway_api_cache"  # noqa: S108 (the only writable path on Lambda)


@dataclass(frozen=True)
class Settings:
    data_url: str
    cors_origins: tuple[str, ...] = (DEFAULT_CORS,)
    cache_max_age_s: int = 300  # gold is refreshed about every 15 minutes
    max_request_bytes: int = 2048  # body and URL length: every endpoint is a bare GET
    max_rows: int = config.MAX_ROWS
    cache_dir: str | None = None  # None: read over HTTPS on every request
    recheck_s: float = 60.0  # at most one manifest check per this many seconds

    @classmethod
    def from_env(cls) -> "Settings":
        env = os.environ
        url = config.base_url()
        origins = tuple(o.strip() for o in env.get("API_CORS_ORIGINS", DEFAULT_CORS).split(",")
                        if o.strip() and o.strip() != "*")  # fmt: skip
        cache_dir = env.get("API_CACHE_DIR", DEFAULT_CACHE_DIR) or None
        if url.startswith("file://"):
            cache_dir = None  # already local
        return cls(
            data_url=url,
            cors_origins=origins,
            cache_max_age_s=int(env.get("API_CACHE_MAX_AGE", "300")),
            max_request_bytes=int(env.get("API_MAX_REQUEST_BYTES", "2048")),
            max_rows=min(int(env.get("API_MAX_ROWS", str(config.MAX_ROWS))), config.MAX_ROWS),
            cache_dir=cache_dir,
            recheck_s=float(env.get("API_RECHECK_S", "60")),
        )
