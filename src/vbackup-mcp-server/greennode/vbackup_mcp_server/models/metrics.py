"""vMonitor time series for vBackup.

Everything vBackup's own endpoints report is a snapshot of now. These models
carry the other half — how a number got where it is — and they exist because
the statistics API answers in a shape that is easy to misread:

- **Values arrive as strings**, so arithmetic on them silently concatenates.
- **Timestamps come back in epoch SECONDS** although the request sends epoch
  milliseconds.
- **The region label is spelled differently**: series say ``HCM`` where the
  rest of this package says ``HCM-3``.

Each is normalised once here rather than in whichever handler happens to read
the payload.
"""

from __future__ import annotations

from greennode.vbackup_mcp_server.models._common import as_dict, as_text
from pydantic import BaseModel, Field
from typing import Any


REGION_LABELS = {"HCM": "HCM-3", "HAN": "HAN"}
"""vMonitor's region spelling to the one every other tool in this package uses."""


def _as_float(value: Any) -> float | None:
    """Coerce a metric value to a float; the API sends them as strings."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class MetricPoint(BaseModel):
    """One sample in a series."""

    timestamp: str = Field("", description="Sample time, ISO-8601 UTC")
    epoch_seconds: float = Field(0, description="Sample time as the API reports it")
    value: float | None = Field(None, description="Sample value, null when the API sent no number")


class MetricSeries(BaseModel):
    """One metric for one region (and, for a location, one storage type)."""

    metric: str = Field(
        ..., description="Metric name as vMonitor knows it, e.g. `vbk.total_usage`"
    )
    region: str = Field(
        "",
        description=(
            "Region the series covers, normalised to this server's spelling ('HCM-3', 'HAN')."
        ),
    )
    storage_type: str = Field(
        "", description="Storage backend of the location, when the series carries one"
    )
    unit: str = Field("", description="What the numbers mean; empty when the metric is a count")
    points: int = Field(0, description="Number of samples returned")
    latest: float | None = Field(None, description="Most recent value in the window")
    minimum: float | None = Field(None, description="Lowest value in the window")
    maximum: float | None = Field(None, description="Highest value in the window")
    samples: list[MetricPoint] = Field(
        default_factory=list, description="The samples, oldest first"
    )

    @classmethod
    def from_api(cls, metric: str, unit: str, data: dict) -> MetricSeries:
        """Build a MetricSeries from one entry of the statistics array."""
        from datetime import datetime, timezone

        dims = as_dict(data.get("dimensions"))
        raw = data.get("statistics")
        samples: list[MetricPoint] = []
        for entry in raw if isinstance(raw, list) else []:
            if not isinstance(entry, list) or len(entry) < 2:
                continue
            epoch = _as_float(entry[0]) or 0.0
            samples.append(
                MetricPoint(
                    timestamp=datetime.fromtimestamp(epoch, timezone.utc).isoformat(),
                    epoch_seconds=epoch,
                    value=_as_float(entry[1]),
                )
            )
        values = [s.value for s in samples if s.value is not None]
        raw_region = as_text(dims.get("region"))
        return cls(
            metric=metric,
            region=REGION_LABELS.get(raw_region, raw_region),
            storage_type=as_text(dims.get("type")),
            unit=unit,
            points=len(samples),
            latest=values[-1] if values else None,
            minimum=min(values) if values else None,
            maximum=max(values) if values else None,
            samples=samples,
        )


class MetricsWindow(BaseModel):
    """The time range and resolution a metrics answer covers."""

    start: str = Field("", description="Window start, ISO-8601 UTC")
    end: str = Field("", description="Window end, ISO-8601 UTC")
    period_seconds: int = Field(
        60,
        description=(
            "The `period` sent to vMonitor, in seconds. It is a FLOOR on bucket size, "
            "not the bucket size: vBackup metrics are stored hourly, so anything up to "
            "3600 comes back at one point per hour and only larger values aggregate "
            "further. Read `bucket_seconds` for what the answer actually used."
        ),
    )
    bucket_seconds: int = Field(
        0,
        description=(
            "Spacing the API actually returned between samples, 0 when fewer than two "
            "samples came back. This is the real resolution of the answer."
        ),
    )


class BackupMetricsData(BaseModel):
    """Structured output of get_backup_metrics."""

    window: MetricsWindow = Field(..., description="Range and resolution covered")
    series: list[MetricSeries] = Field(
        default_factory=list, description="One entry per metric per region"
    )
    empty_metrics: list[str] = Field(
        default_factory=list,
        description=(
            "Metrics that returned no series at all. vMonitor answers an unknown "
            "metric name with an empty 200, so this is where a typo would show up — "
            "but on these fixed names it means no data was recorded in the window."
        ),
    )


class DestinationMetricsData(BaseModel):
    """Structured output of get_backup_destination_metrics."""

    window: MetricsWindow = Field(..., description="Range and resolution covered")
    destination_id: str = Field("", description="Destination the series belong to")
    destination_name: str = Field("", description="Destination name, when it could be resolved")
    series: list[MetricSeries] = Field(
        default_factory=list, description="One entry per metric for this destination"
    )
    empty_metrics: list[str] = Field(
        default_factory=list,
        description=(
            "Metrics that returned nothing. An id that does not exist returns an "
            "empty 200 for ALL THREE, so three empties together point at a wrong id "
            "rather than at a quiet destination."
        ),
    )


class MultiDestinationMetricsData(BaseModel):
    """Structured output of get_backup_destination_metrics with no destination named."""

    window: MetricsWindow = Field(..., description="Range and resolution covered")
    total: int = Field(0, description="Destinations covered")
    destinations: list[DestinationMetricsData] = Field(
        default_factory=list, description="Per-destination metrics, one entry per location"
    )
