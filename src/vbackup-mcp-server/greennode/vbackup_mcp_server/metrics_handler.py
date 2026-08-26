"""vMonitor dashboards for vBackup.

The rest of this package answers "what is true now". This handler answers "and
how did it get there", by posting fixed queries to the vMonitor statistics API
behind the Backup Center console.

The queries are NOT open-ended. vMonitor publishes exactly six vBackup metrics —
three for the product as a whole and three per backup location — so both tools
here ship those payloads baked in and a caller chooses only the time window.
There is deliberately no "run an arbitrary metric query" tool: a free-form name
returns an empty 200 rather than an error, which would turn every typo into a
confident "no data".
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from greennode.vbackup_mcp_server.client import VbackupClient
from greennode.vbackup_mcp_server.config import Region, VbackupConfig
from greennode.vbackup_mcp_server.history_handler import to_epoch_millis
from greennode.vbackup_mcp_server.models import (
    BackupDestinationItem,
    BackupMetricsData,
    DestinationMetricsData,
    MetricSeries,
    MetricsWindow,
    MultiDestinationMetricsData,
)
from greennode.vbackup_mcp_server.paging import as_list, fetch_all_items
from greennode.vbackup_mcp_server.tool_annotations import READ
from greennode.vbackup_mcp_server.validators import validate_id
from pydantic import Field


STATISTICS_PATH = "/api/v1/statistics/default"

DEFAULT_WINDOW_HOURS = 24

DEFAULT_PERIOD_SECONDS = 60

MIN_PERIOD_SECONDS = 60

MAX_PERIOD_SECONDS = 86_400

STORED_RESOLUTION_SECONDS = 3_600
"""Resolution vBackup metrics are stored at.

Any ``period`` from 60 to 3600 answers with one sample per hour; only a period
above an hour aggregates further. Sending 60 therefore does not give
minute-level data — that data does not exist.
"""

MAX_DESTINATIONS_PER_CALL = 20

OVERALL_METRICS: dict[str, str] = {
    "vbk.total_backup_servers": "",
    "vbk.total_servers": "",
    "vbk.total_usage": "GB",
}
"""The three product-wide metrics vMonitor publishes, and what their values mean."""

LOCATION_METRICS: dict[str, str] = {
    "vbk.location.usage": "GB",
    "vbk.location.failed_rate": "",
    "vbk.location.success_rate": "",
}
"""The three per-location metrics. Despite the names, the two `*_rate` metrics
are COUNTS of runs, not percentages — values well above 100 are normal."""


def _bucket_seconds(series: list[MetricSeries]) -> int:
    """Spacing between the first two samples of the first series that has two."""
    for one in series:
        if len(one.samples) > 1:
            return int(one.samples[1].epoch_seconds - one.samples[0].epoch_seconds)
    return 0


def validate_period(period_seconds: int) -> int:
    """Check *period_seconds* against the bounds vMonitor enforces.

    The API rejects anything outside 60-86400 or not divisible by 60 with a
    `400`; catching it here names the constraint before a round trip.
    """
    if not MIN_PERIOD_SECONDS <= period_seconds <= MAX_PERIOD_SECONDS:
        raise ValueError(
            f"period_seconds must be between {MIN_PERIOD_SECONDS} and "
            f"{MAX_PERIOD_SECONDS}; got {period_seconds}"
        )
    if period_seconds % 60:
        raise ValueError(f"period_seconds must be divisible by 60; got {period_seconds}")
    return period_seconds


def _iso(epoch_millis: int) -> str:
    """Render epoch milliseconds as an ISO-8601 UTC string."""
    return datetime.fromtimestamp(epoch_millis / 1000, timezone.utc).isoformat()


def _query(name: str, dimensions: str, start: int, end: int, period: int) -> dict:
    """Build the statistics payload vMonitor expects.

    Every field is fixed except the metric name, the dimensions and the window:
    ``statistics: max`` and ``group_by: region,type`` are what the Backup Center
    console itself sends, and changing them changes what the numbers mean.
    """
    return {
        "type": "SIMPLE",
        "data": {
            "graph": {
                "name": name,
                "dimensions": dimensions,
                "statistics": "max",
                "group_by": "region,type",
                "offset": 0,
                "limit": "",
                "rollup": "",
                "rate": 0,
            },
            "start_time": start,
            "end_time": end,
            "period": period,
            "alarm": False,
        },
    }


class MetricsHandler:
    """Register and serve the vBackup metric dashboards."""

    def __init__(
        self,
        mcp,
        config: VbackupConfig,
        client: VbackupClient,
        allow_write: bool = False,
    ):
        self.mcp = mcp
        self.config = config
        self.client = client
        self.allow_write = allow_write

        self.mcp.tool(name="get_backup_metrics", annotations=READ)(self.get_backup_metrics)
        self.mcp.tool(name="get_backup_destination_metrics", annotations=READ)(
            self.get_backup_destination_metrics
        )

    def _window(
        self, from_date: str | None, to_date: str | None, period_seconds: int
    ) -> tuple[int, int, MetricsWindow]:
        """Resolve the requested window, defaulting to the last 24 hours."""
        end = (
            to_epoch_millis(to_date, "to_date")
            if to_date
            else int(datetime.now(timezone.utc).timestamp() * 1000)
        )
        start = (
            to_epoch_millis(from_date, "from_date")
            if from_date
            else end - DEFAULT_WINDOW_HOURS * 3_600_000
        )
        if start >= end:
            raise ValueError(
                f"from_date must be before to_date (got start={_iso(start)}, end={_iso(end)})"
            )
        return (
            start,
            end,
            MetricsWindow(start=_iso(start), end=_iso(end), period_seconds=period_seconds),
        )

    async def _collect(
        self, metrics: dict[str, str], dimensions: str, start: int, end: int, period: int
    ) -> tuple[list[MetricSeries], list[str], int]:
        """Run every metric of a dashboard concurrently and normalise the answers.

        Also reports the spacing the API actually used, which is rarely the
        period that was asked for.
        """
        payloads = [
            self.client.post_vmonitor(
                STATISTICS_PATH, _query(name, dimensions, start, end, period)
            )
            for name in metrics
        ]
        answers = await asyncio.gather(*payloads)

        series: list[MetricSeries] = []
        empty: list[str] = []
        for (name, unit), raw in zip(metrics.items(), answers):
            entries = [e for e in as_list(raw) if isinstance(e, dict)]
            if not entries:
                empty.append(name)
                continue
            series.extend(MetricSeries.from_api(name, unit, e) for e in entries)
        return series, empty, _bucket_seconds(series)

    async def get_backup_metrics(
        self,
        from_date: str | None = Field(
            None,
            description=(
                "Window start as an ISO-8601 date or datetime. Defaults to 24 hours "
                "before `to_date`."
            ),
        ),
        to_date: str | None = Field(
            None, description="Window end as an ISO-8601 date or datetime. Defaults to now."
        ),
        period_seconds: int = Field(
            DEFAULT_PERIOD_SECONDS,
            ge=MIN_PERIOD_SECONDS,
            le=MAX_PERIOD_SECONDS,
            description=(
                "Requested bucket size in SECONDS. Must be 60-86400 and divisible by "
                "60. It is a floor, not the bucket size: these metrics are stored "
                "hourly, so 60 and 3600 both return one point per hour. Use 21600 or "
                "86400 to aggregate a long window down."
            ),
        ),
        region: Region = Field(
            "HCM-3",
            description=(
                "Ignored for routing: one vMonitor host serves both regions and each "
                "series says which region it came from. Present only for consistency."
            ),
        ),
    ) -> BackupMetricsData:
        """Get the product-wide backup metrics over time — the vBackup dashboard.

        Returns {window{start, end, period_seconds}, series[{metric, region,
        unit, points, latest, minimum, maximum, samples[]}], empty_metrics}.

        Three metrics, each broken down **per region in one call**, so this is
        the whole account in a single answer:

        | Metric | Meaning |
        |---|---|
        | `vbk.total_backup_servers` | Backup servers that exist |
        | `vbk.total_servers` | vServer instances that exist |
        | `vbk.total_usage` | Storage consumed by backups, in **GB** |

        Use it for trends and get_backup_statistics for the exact current
        numbers. The two answer different questions and will not match to the
        digit: this is a `max` per bucket sampled by vMonitor, and `total_usage`
        is decimal **GB** while `vault.used_gb` elsewhere in this server is
        **GiB** — a ~7% difference that is a unit, not a discrepancy. Do not
        present them as a contradiction.

        Read the shape, not just the last value: `total_usage` climbing while
        `total_backup_servers` is flat means existing backups are growing;
        both climbing means new servers were protected.

        A metric listed in `empty_metrics` recorded nothing in the window —
        normal for a quiet account over a short window, so widen the window
        before concluding anything is broken.

        **Resolution is hourly and `period_seconds` cannot make it finer.** Every
        period from 60 to 3600 returns one point per hour; only a larger value
        aggregates. `window.bucket_seconds` reports what the answer actually
        used, so quote that rather than the period that was requested.
        """
        start, end, window = self._window(from_date, to_date, period_seconds)
        series, empty, bucket = await self._collect(
            OVERALL_METRICS, "product:vbackup", start, end, period_seconds
        )
        window.bucket_seconds = bucket
        return BackupMetricsData(window=window, series=series, empty_metrics=empty)

    async def get_backup_destination_metrics(
        self,
        destination_id: str | None = Field(
            None,
            description=(
                "Backup destination to chart (`bk-des-...`). Omit to chart EVERY "
                "destination in the account — convenient, but it issues three requests "
                "per location."
            ),
        ),
        from_date: str | None = Field(
            None, description="Window start as an ISO-8601 date or datetime; default 24h back."
        ),
        to_date: str | None = Field(
            None, description="Window end as an ISO-8601 date or datetime; default now."
        ),
        period_seconds: int = Field(
            DEFAULT_PERIOD_SECONDS,
            ge=MIN_PERIOD_SECONDS,
            le=MAX_PERIOD_SECONDS,
            description=(
                "Requested bucket size in SECONDS, 60-86400 and divisible by 60. A "
                "floor, not the bucket size — these metrics are stored hourly."
            ),
        ),
        region: Region = Field(
            "HCM-3",
            description=(
                "Only used to list destinations when `destination_id` is omitted. The "
                "metrics host itself is not region-scoped."
            ),
        ),
    ) -> DestinationMetricsData | MultiDestinationMetricsData:
        """Get the per-location backup metrics over time — the Backup Location dashboard.

        With `destination_id`: returns {window, destination_id, destination_name,
        series[], empty_metrics}. Without it: returns {window, total,
        destinations[]} covering every location.

        Three metrics per location:

        | Metric | Meaning |
        |---|---|
        | `vbk.location.usage` | Storage this location holds, in **GB** |
        | `vbk.location.success_rate` | Backup runs that SUCCEEDED — a **count** |
        | `vbk.location.failed_rate` | Backup runs that FAILED — a **count** |

        **Despite their names the two `*_rate` metrics are counts, not
        percentages.** Values in the hundreds are normal and mean hundreds of
        runs, not hundreds of percent. To express reliability as a percentage,
        compute it yourself from the two and say that is what you did.

        Pair `usage` with the destination's `max_quota_gb` from
        get_backup_destination: a rising curve against a fixed ceiling is the
        warning that runs are about to start failing, and the slope is what says
        how long there is left.

        **All three metrics empty together almost always means a wrong id.**
        vMonitor answers an unknown `backup_location_id` with an empty `200`, not
        an error, so it is indistinguishable from a silent location — check the
        id against list_backup_destinations before reporting "no activity".

        Series carry a `storage_type` (VAULT or VSTORAGE) that the destination's
        own record also reports; use it to confirm you are looking at the
        location you think you are.
        """
        start, end, window = self._window(from_date, to_date, period_seconds)

        if destination_id:
            validate_id(destination_id, "destination_id")
            series, empty, bucket = await self._collect(
                LOCATION_METRICS,
                f"product:vbackup,backup_location_id:{destination_id}",
                start,
                end,
                period_seconds,
            )
            window.bucket_seconds = bucket
            return DestinationMetricsData(
                window=window,
                destination_id=destination_id,
                destination_name=await self._destination_name(destination_id, region),
                series=series,
                empty_metrics=empty,
            )

        raw = await fetch_all_items(self.client, "/v1/backup-destinations", region=region)
        destinations = [BackupDestinationItem.from_api(d) for d in raw if isinstance(d, dict)][
            :MAX_DESTINATIONS_PER_CALL
        ]

        results = []
        for dest in destinations:
            series, empty, bucket = await self._collect(
                LOCATION_METRICS,
                f"product:vbackup,backup_location_id:{dest.id}",
                start,
                end,
                period_seconds,
            )
            window.bucket_seconds = window.bucket_seconds or bucket
            results.append(
                DestinationMetricsData(
                    window=window,
                    destination_id=dest.id,
                    destination_name=dest.name,
                    series=series,
                    empty_metrics=empty,
                )
            )
        return MultiDestinationMetricsData(window=window, total=len(results), destinations=results)

    async def _destination_name(self, destination_id: str, region: str | None) -> str:
        """Resolve a destination's name for the report, tolerating a failed lookup."""
        try:
            data = await self.client.get(
                f"/v1/backup-destinations/{destination_id}", region=region
            )
        except Exception:
            return ""
        return BackupDestinationItem.from_api(data).name if isinstance(data, dict) else ""
