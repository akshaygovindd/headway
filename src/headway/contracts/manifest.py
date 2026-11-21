"""Capture manifest contract."""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, model_validator

from headway.contracts.types import FeedName, UtcDatetime


class ManifestEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    capture_id: str
    feed: FeedName
    city_id: str
    status: Literal["ok", "error"]
    http_status: int | None = None
    scheduled_at: UtcDatetime
    fetched_at: UtcDatetime
    feed_header_ts: UtcDatetime | None = None
    entity_count: int | None = None
    bytes: int | None = None
    content_hash: str | None = None
    s3_key: str | None = None
    error: str | None = None
    header_parse_error: str | None = None

    @model_validator(mode="after")
    def _ok_requires_object(self) -> Self:
        if self.status == "ok" and not (self.s3_key and self.content_hash):
            raise ValueError("status ok requires s3_key and content_hash")
        return self
