"""Turns raw USGS events (GeoJSON features) into validated records, rejecting ones we can't trust.

Sensible defaults for messy data: missing optional text becomes "", missing numbers stay None,
over-long free text is truncated. Anything we can't identify, place or order (no id, bad
coordinates, bad timestamps, unknown status) is rejected with a reason rather than guessed.
The one deliberate exception is a merge stub (see MergeStub), which is recognised, not rejected.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, ValidationError

from quakes.models import Event


def _none_to_empty(value: Any) -> Any:
    return "" if value is None else value


def _epoch_ms_to_datetime(value: Any) -> Any:
    # USGS timestamps are epoch milliseconds. Converted explicitly rather than relying on
    # pydantic's number heuristics, which guess between seconds and milliseconds.
    if isinstance(value, int | float):
        return datetime.fromtimestamp(value / 1000, tz=UTC)
    return value


def _truncated(max_length: int):
    def truncate(value: Any) -> Any:
        value = _none_to_empty(value)
        return value[:max_length] if isinstance(value, str) else value

    return BeforeValidator(truncate)


EpochMs = Annotated[datetime, BeforeValidator(_epoch_ms_to_datetime)]


class ParsedEvent(BaseModel):
    """A raw USGS event flattened to the shape of `quakes.Event`."""

    # NaN is a valid float, and Python's JSON parser accepts it, so `float` alone won't reject it.
    # Python says NaN > 9 is False, but Postgres treats NaN as greater than every number, so a
    # stored NaN magnitude would match the API's SQL filter `magnitude >= 9` and sort as the
    # biggest quake. e.g. {"mag": NaN} would be returned by ?min_magnitude=9.
    model_config = ConfigDict(allow_inf_nan=False)

    id: str = Field(min_length=1, max_length=64)
    time: EpochMs
    source_updated: EpochMs
    status: Event.Status

    magnitude: float | None = None
    magnitude_type: Annotated[str, _truncated(16)] = ""
    longitude: float = Field(ge=-180, le=180)
    latitude: float = Field(ge=-90, le=90)
    depth_km: float | None = None

    place: Annotated[str, _truncated(255)] = ""
    title: Annotated[str, _truncated(255)] = ""
    url: Annotated[str, _truncated(500)] = ""

    event_type: Annotated[str, _truncated(64)] = ""
    network: Annotated[str, _truncated(16)] = ""
    significance: int | None = None
    tsunami: bool = False
    felt: int | None = None
    alert: Annotated[str, _truncated(16)] = ""


class MergeStub(BaseModel):
    """An id USGS has merged into another event.

    When networks' reports of the same quake are associated, the non-preferred id is still
    returned, but as an empty stub: time, status and magnitude null, coordinates [0, 0], and
    `ids` listing both ids. It tells us that any row we hold under this id is now a duplicate.
    """

    id: str = Field(min_length=1, max_length=64)
    source_updated: EpochMs
    merged_with: list[str] = Field(min_length=1)


@dataclass(frozen=True)
class Rejection:
    event_id: str | None
    reason: str


@dataclass(frozen=True)
class ParsedPage:
    events: list[ParsedEvent]
    merges: list[MergeStub]
    rejected: list[Rejection]


def parse_raw_event(raw_event: Any) -> ParsedEvent | MergeStub:
    """Validate one raw event. Raises ValueError (pydantic's ValidationError is one) if unusable."""
    if not isinstance(raw_event, dict):
        raise ValueError("raw event is not an object")
    props = raw_event.get("properties")
    if not isinstance(props, dict):
        raise ValueError("missing properties")

    if stub := _as_merge_stub(raw_event.get("id"), props):
        return stub

    geometry = raw_event.get("geometry")
    coords = geometry.get("coordinates") if isinstance(geometry, dict) else None
    if not isinstance(coords, list) or len(coords) < 2:
        raise ValueError("missing coordinates")

    status = props.get("status")
    return ParsedEvent.model_validate(
        {
            "id": raw_event.get("id"),
            "time": props.get("time"),
            "source_updated": props.get("updated"),
            "status": status.lower() if isinstance(status, str) else status,
            "magnitude": props.get("mag"),
            "magnitude_type": props.get("magType"),
            "longitude": coords[0],
            "latitude": coords[1],
            "depth_km": coords[2] if len(coords) > 2 else None,
            "place": props.get("place"),
            "title": props.get("title"),
            "url": props.get("url"),
            "event_type": props.get("type"),
            "network": props.get("net"),
            "significance": props.get("sig"),
            "tsunami": bool(props.get("tsunami")),
            "felt": props.get("felt"),
            "alert": props.get("alert"),
        }
    )


def _as_merge_stub(event_id: Any, props: dict) -> MergeStub | None:
    """A merge stub has no origin time, and its `ids` names at least one other event."""
    if props.get("time") is not None:
        return None
    # `ids` is comma-wrapped, e.g. ",av94482093,aka2026vsvfmy," for stub aka2026vsvfmy:
    # split it, drop the empty ends and our own id, leaving the event(s) it merged into.
    others = [i for i in str(props.get("ids") or "").split(",") if i and i != event_id]
    if not others:
        return None
    return MergeStub(id=event_id, source_updated=props.get("updated"), merged_with=others)


def parse_page(raw_events: list[Any]) -> ParsedPage:
    """Parse a page, never letting one bad record fail the rest."""
    page = ParsedPage(events=[], merges=[], rejected=[])
    for raw_event in raw_events:
        try:
            parsed = parse_raw_event(raw_event)
        except (ValidationError, ValueError) as exc:
            event_id = raw_event.get("id") if isinstance(raw_event, dict) else None
            page.rejected.append(Rejection(event_id=event_id, reason=_summarise(exc)))
            continue
        if isinstance(parsed, MergeStub):
            page.merges.append(parsed)
        else:
            page.events.append(parsed)
    return page


def _summarise(exc: Exception) -> str:
    """One line per rejection, e.g. "status: Input should be ...; latitude: Input should be ..."."""
    if not isinstance(exc, ValidationError):
        return str(exc)
    problems = []
    for error in exc.errors():
        field = ".".join(str(part) for part in error["loc"])
        problems.append(f"{field}: {error['msg']}")
    return "; ".join(problems)
