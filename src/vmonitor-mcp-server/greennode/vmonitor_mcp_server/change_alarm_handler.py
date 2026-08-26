"""Change-detection alarm handler for the vMonitor MCP server.

A change alarm ("change-method") compares a metric against its own past
(``timeshift``) to fire when the value *changes* beyond a threshold, rather than
crossing a fixed level. These tools are its dedicated CRUD + history surface
(distinct from the standard metric/log alarms in ``alarm_handler``).

- ``get_change_alarm`` reads a change alarm's definition for a window.
- ``list_change_alarm_histories`` reads its state-transition history.
- ``create_change_alarm`` / ``update_change_alarm`` / ``delete_change_alarm``
  manage it; ``delete_change_alarm_history`` clears its history.
"""

from __future__ import annotations

from greennode.vmonitor_mcp_server.client import VmonitorClient
from greennode.vmonitor_mcp_server.config import VmonitorConfig
from greennode.vmonitor_mcp_server.models import (
    AlarmDefinitionData,
    AlarmHistoryData,
    AlarmSummary,
    CreateChangeAlarmDto,
    UpdateChangeAlarmDto,
)
from greennode.vmonitor_mcp_server.tool_annotations import DESTRUCTIVE, READ, WRITE
from greennode.vmonitor_mcp_server.validators import validate_id
from pydantic import Field
from typing import Any


class ChangeAlarmHandler:
    """Register and serve vMonitor change-detection alarm MCP tools."""

    def __init__(
        self, mcp, config: VmonitorConfig, client: VmonitorClient, allow_write: bool = False
    ):
        self.mcp = mcp
        self.config = config
        self.client = client
        self.allow_write = allow_write

        self.mcp.tool(name="get_change_alarm", annotations=READ)(self.get_change_alarm)
        self.mcp.tool(name="list_change_alarm_histories", annotations=READ)(
            self.list_change_alarm_histories
        )

        if self.allow_write:
            self.mcp.tool(name="create_change_alarm", annotations=WRITE)(self.create_change_alarm)
            self.mcp.tool(name="update_change_alarm", annotations=WRITE)(self.update_change_alarm)
            self.mcp.tool(name="delete_change_alarm", annotations=DESTRUCTIVE)(
                self.delete_change_alarm
            )
            self.mcp.tool(name="delete_change_alarm_history", annotations=DESTRUCTIVE)(
                self.delete_change_alarm_history
            )

    async def get_change_alarm(
        self,
        alarm_id: str = Field(..., description="Change alarm ID"),
        start_time: str = Field(..., description="Window start (epoch millis or ISO-8601)"),
        end_time: str = Field(..., description="Window end (epoch millis or ISO-8601)"),
    ) -> AlarmDefinitionData:
        """Get a change alarm's definition evaluated over a time window.

        `start_time` and `end_time` are required — the definition is returned in
        the context of that evaluation window.
        """
        validate_id(alarm_id, "alarm_id")
        params = {"start_time": start_time, "end_time": end_time}
        data = await self.client.get(f"/api/v1/alarms/change-method/{alarm_id}", params=params)
        return AlarmDefinitionData.from_api(data)

    async def list_change_alarm_histories(
        self,
        alarm_id: str = Field(..., description="Change alarm ID"),
        start_time: str = Field(..., description="Window start (epoch millis or ISO-8601)"),
        end_time: str = Field(..., description="Window end (epoch millis or ISO-8601)"),
        interval: str | None = Field(None, description="Bucket interval for the history"),
    ) -> AlarmHistoryData:
        """List a change alarm's state-transition history over a time window."""
        validate_id(alarm_id, "alarm_id")
        params: dict[str, Any] = {"start_time": start_time, "end_time": end_time}
        if interval:
            params["interval"] = interval
        data = await self.client.get(
            f"/api/v1/alarms/change-method/{alarm_id}/histories", params=params
        )
        return AlarmHistoryData.from_api(data)

    async def create_change_alarm(
        self,
        body: CreateChangeAlarmDto = Field(
            ...,
            description=(
                "CreateChangeAlarmDto body. Required: name. Supply the metric "
                "selection, the change threshold (thresholdMethod/thresholdValue/"
                "timeshift) and notification actions the API needs."
            ),
        ),
    ) -> AlarmSummary:
        """Create a change-detection alarm on a metric.

        ## Requirements
        - Server must run with --allow-write
        - `name` is required; the metric selection + change threshold define what fires.
        """
        data = await self.client.post(
            "/api/v1/alarms/change-method", json=body.model_dump(exclude_none=True)
        )
        return AlarmSummary.from_api(data)

    async def update_change_alarm(
        self,
        alarm_id: str = Field(..., description="Change alarm ID to update"),
        body: UpdateChangeAlarmDto = Field(
            ...,
            description="UpdateChangeAlarmDto body. All fields optional; id comes from the path.",
        ),
    ) -> str:
        """Update a change alarm's thresholds, timing, notifications or metadata.

        ## Requirements
        - Server must run with --allow-write
        - Only the provided fields change; read it first with get_change_alarm.
        """
        validate_id(alarm_id, "alarm_id")
        payload = body.model_dump(exclude_none=True)
        payload["id"] = alarm_id
        await self.client.put(f"/api/v1/alarms/change-method/{alarm_id}", json=payload)
        return f"Change alarm {alarm_id} updated."

    async def delete_change_alarm(
        self,
        alarm_id: str = Field(..., description="Change alarm ID to delete. IRREVERSIBLE."),
    ) -> str:
        """Delete a change-detection alarm. IRREVERSIBLE.

        ## Requirements
        - Server must run with --allow-write
        - Confirm the id first; deletion cannot be undone.
        """
        validate_id(alarm_id, "alarm_id")
        await self.client.delete(f"/api/v1/alarms/change-method/{alarm_id}")
        return f"Change alarm {alarm_id} deleted."

    async def delete_change_alarm_history(
        self,
        alarm_id: str = Field(
            ..., description="Change alarm ID whose history to clear. IRREVERSIBLE."
        ),
    ) -> str:
        """Clear a change alarm's state-transition history. IRREVERSIBLE.

        Removes the stored history records for the alarm; the alarm itself stays.

        ## Requirements
        - Server must run with --allow-write
        - History records cannot be recovered once cleared.
        """
        validate_id(alarm_id, "alarm_id")
        await self.client.delete(f"/api/v1/alarms/change-method/{alarm_id}/histories")
        return f"Change alarm {alarm_id} history cleared."
