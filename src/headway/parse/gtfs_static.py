"""GTFS static feed reader: typed rows from a zip archive or an extracted folder."""

import csv
import io
import zipfile
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import IO, Any, ClassVar, TextIO

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

OPTIONAL_TABLES = frozenset({"calendar", "calendar_dates", "shapes", "feed_info"})


class GtfsParseError(Exception):
    """A GTFS table could not be read or a row did not match its schema."""


def _parse_date(value: Any) -> Any:
    if isinstance(value, str):
        return datetime.strptime(value, "%Y%m%d").date()
    return value


class _Row(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    table: ClassVar[str]
    _date_fields: ClassVar[tuple[str, ...]] = ()

    @classmethod
    def from_csv(cls, raw: dict[str, str]) -> "_Row":
        data: dict[str, Any] = {}
        for name in cls.model_fields:
            value = raw.get(name)
            if value is None:
                continue
            value = value.strip()
            if value == "" and name not in cls._required():
                continue
            data[name] = value
        return cls.model_validate(data)

    @classmethod
    def _required(cls) -> frozenset[str]:
        return frozenset(n for n, f in cls.model_fields.items() if f.is_required())

    @field_validator("*", mode="before")
    @classmethod
    def _dates(cls, value: Any, info: Any) -> Any:
        if info.field_name in cls._date_fields:
            return _parse_date(value)
        return value

    def to_csv(self) -> list[str]:
        return [_format(getattr(self, name)) for name in type(self).model_fields]


def _format(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, date):
        return f"{value:%Y%m%d}"
    return str(value)


class Agency(_Row):
    table: ClassVar[str] = "agency"
    agency_id: str | None = None
    agency_name: str
    agency_url: str
    agency_timezone: str
    agency_lang: str | None = None
    agency_phone: str | None = None
    agency_fare_url: str | None = None


class Route(_Row):
    table: ClassVar[str] = "routes"
    route_id: str
    agency_id: str | None = None
    route_short_name: str | None = None
    route_long_name: str | None = None
    route_desc: str | None = None
    route_type: int
    route_url: str | None = None
    route_color: str | None = None
    route_text_color: str | None = None
    route_sort_order: int | None = None
    line_id: str | None = None


class Stop(_Row):
    table: ClassVar[str] = "stops"
    stop_id: str
    stop_code: str | None = None
    stop_name: str | None = None
    stop_desc: str | None = None
    platform_code: str | None = None
    platform_name: str | None = None
    stop_lat: float | None = None
    stop_lon: float | None = None
    zone_id: str | None = None
    location_type: int | None = None
    parent_station: str | None = None
    wheelchair_boarding: int | None = None
    level_id: str | None = None


class Trip(_Row):
    table: ClassVar[str] = "trips"
    route_id: str
    service_id: str
    trip_id: str
    trip_headsign: str | None = None
    trip_short_name: str | None = None
    direction_id: int | None = None
    block_id: str | None = None
    shape_id: str | None = None
    wheelchair_accessible: int | None = None
    route_pattern_id: str | None = None


class StopTime(_Row):
    """Times stay strings: GTFS hours may exceed 24."""

    table: ClassVar[str] = "stop_times"
    trip_id: str
    arrival_time: str | None = None
    departure_time: str | None = None
    stop_id: str
    stop_sequence: int
    stop_headsign: str | None = None
    pickup_type: int | None = None
    drop_off_type: int | None = None
    timepoint: int | None = None


class ShapePoint(_Row):
    table: ClassVar[str] = "shapes"
    shape_id: str
    shape_pt_lat: float
    shape_pt_lon: float
    shape_pt_sequence: int
    shape_dist_traveled: float | None = None


class Calendar(_Row):
    table: ClassVar[str] = "calendar"
    _date_fields: ClassVar[tuple[str, ...]] = ("start_date", "end_date")
    service_id: str
    monday: bool
    tuesday: bool
    wednesday: bool
    thursday: bool
    friday: bool
    saturday: bool
    sunday: bool
    start_date: date
    end_date: date

    def runs_on(self, day: date) -> bool:
        flags = (
            self.monday,
            self.tuesday,
            self.wednesday,
            self.thursday,
            self.friday,
            self.saturday,
            self.sunday,
        )
        return self.start_date <= day <= self.end_date and flags[day.weekday()]


class CalendarDate(_Row):
    table: ClassVar[str] = "calendar_dates"
    _date_fields: ClassVar[tuple[str, ...]] = ("date",)
    service_id: str
    date: date
    exception_type: int
    holiday_name: str | None = None


class FeedInfo(_Row):
    table: ClassVar[str] = "feed_info"
    _date_fields: ClassVar[tuple[str, ...]] = ("feed_start_date", "feed_end_date")
    feed_publisher_name: str
    feed_publisher_url: str
    feed_lang: str
    feed_start_date: date | None = None
    feed_end_date: date | None = None
    feed_version: str | None = None
    feed_contact_email: str | None = None


class GtfsSource:
    """Read GTFS tables from a zip archive or an extracted directory."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._is_zip = path.is_file()
        if not self._is_zip and not path.is_dir():
            raise FileNotFoundError(path)

    def has_table(self, table: str) -> bool:
        name = f"{table}.txt"
        if self._is_zip:
            with zipfile.ZipFile(self.path) as zf:
                return name in zf.namelist()
        return (self.path / name).is_file()

    def iter_rows[R: _Row](
        self, model: type[R], raw_filter: Callable[[dict[str, str]], bool] | None = None
    ) -> Iterator[R]:
        """Yield typed rows; ``raw_filter`` skips unwanted rows before validation."""
        table = model.table
        if not self.has_table(table):
            if table in OPTIONAL_TABLES:
                return
            raise GtfsParseError(f"missing required table {table}.txt")
        with self._open(table) as handle:
            reader = csv.DictReader(handle)
            for raw in reader:
                if raw_filter is not None and not raw_filter(raw):
                    continue
                try:
                    row = model.from_csv(raw)
                except ValidationError as exc:
                    raise GtfsParseError(
                        f"{table}.txt line {reader.line_num}: {exc.error_count()} invalid field(s)"
                    ) from exc
                assert isinstance(row, model)
                yield row

    @contextmanager
    def open_binary(self, table: str) -> Iterator[IO[bytes]]:
        """Raw byte stream of one table file, for vectorized readers."""
        name = f"{table}.txt"
        if self._is_zip:
            with zipfile.ZipFile(self.path) as zf, zf.open(name) as stream:
                yield stream
        else:
            with (self.path / name).open("rb") as stream:
                yield stream

    def _open(self, table: str) -> "_Handle":
        return _Handle(self, f"{table}.txt")

    def agency(self) -> Iterator[Agency]:
        return self.iter_rows(Agency)

    def routes(self) -> Iterator[Route]:
        return self.iter_rows(Route)

    def stops(self) -> Iterator[Stop]:
        return self.iter_rows(Stop)

    def trips(self) -> Iterator[Trip]:
        return self.iter_rows(Trip)

    def stop_times(self) -> Iterator[StopTime]:
        return self.iter_rows(StopTime)

    def shapes(self) -> Iterator[ShapePoint]:
        return self.iter_rows(ShapePoint)

    def calendar(self) -> Iterator[Calendar]:
        return self.iter_rows(Calendar)

    def calendar_dates(self) -> Iterator[CalendarDate]:
        return self.iter_rows(CalendarDate)

    def feed_info(self) -> Iterator[FeedInfo]:
        return self.iter_rows(FeedInfo)


class _Handle:
    """Context manager yielding a text stream for one table file."""

    def __init__(self, source: GtfsSource, name: str) -> None:
        self._source = source
        self._name = name
        self._zip: zipfile.ZipFile | None = None
        self._stream: TextIO | None = None

    def __enter__(self) -> TextIO:
        if self._source._is_zip:
            self._zip = zipfile.ZipFile(self._source.path)
            self._stream = io.TextIOWrapper(
                self._zip.open(self._name), encoding="utf-8-sig", newline=""
            )
        else:
            self._stream = (self._source.path / self._name).open(encoding="utf-8-sig", newline="")
        return self._stream

    def __exit__(self, *exc: object) -> None:
        if self._stream is not None:
            self._stream.close()
        if self._zip is not None:
            self._zip.close()


def write_table[R: _Row](path: Path, model: type[R], rows: Iterable[R]) -> int:
    """Write rows as a GTFS CSV (model field order, ``\\n`` line endings); return row count."""
    count = 0
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(list(model.model_fields))
        for row in rows:
            writer.writerow(row.to_csv())
            count += 1
    return count
