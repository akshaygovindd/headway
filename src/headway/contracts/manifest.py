"""Capture manifest contract."""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

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
    # Position of the capture within its scheduled minute: 0 is the all-feeds poll, 1-3 are the
    # vehicle-only polls at +15/+30/+45 s. Manifests written before 15 s sampling have none (0).
    poll_index: int = Field(default=0, ge=0, le=3)
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
