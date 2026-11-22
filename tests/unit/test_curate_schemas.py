from datetime import UTC, date, datetime

import pyarrow as pa

from headway.curate.schemas import SCHEMAS, to_table


def test_all_expected_tables_present() -> None:
    assert set(SCHEMAS) == {
        "trip_updates",
        "vehicle_positions",
        "alerts",
        "routes",
        "stops",
        "trips",
        "stop_times",
        "shapes",
        "calendar",
        "calendar_dates",
        "feed_info",
    }


def test_realtime_tables_carry_lineage_columns() -> None:
    for name in ("trip_updates", "vehicle_positions", "alerts"):
        names = set(SCHEMAS[name].names)
        assert {"capture_id", "run_id", "content_hash", "fetched_at", "feed_header_ts"} <= names


def test_static_tables_carry_feed_version() -> None:
    for name in ("routes", "stops", "trips", "stop_times", "shapes", "calendar", "feed_info"):
        assert "feed_version" in SCHEMAS[name].names


def test_to_table_types() -> None:
    ts = datetime(2026, 11, 22, 14, tzinfo=UTC)
    table = to_table(
        "calendar_dates",
        [
            {
                "feed_version": "v1",
                "service_id": "s",
                "date": date(2026, 11, 22),
                "exception_type": 1,
            }
        ],
    )
    assert table.schema.field("date").type == pa.date32()
    assert SCHEMAS["alerts"].field("fetched_at").type == pa.timestamp("us", tz="UTC")
    assert ts.tzinfo is not None
