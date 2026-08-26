"""Notification handler for the vMonitor MCP server (notification gateway).

A notification is a delivery channel (Email, SMS, Slack, Webhook, Telegram,
Teams) that alarms send to when they trigger. These tools list the supported
channel types and the user's channels, and (with --allow-write) run the
OTP-verified create/update/delete flow.

## OTP flow (Email / SMS / Slack / Telegram)
1. ``create_notification_otp`` sends an OTP to the address (real email/SMS).
2. ``validate_notification_otp`` checks the OTP and returns a ``code``.
3. ``create_notification`` / ``update_notification`` with ``otp_code=code``.

Webhook channels need no OTP (create/update directly).
"""

from __future__ import annotations

import urllib.parse
from greennode.vmonitor_mcp_server.client import VmonitorNotificationClient
from greennode.vmonitor_mcp_server.config import VmonitorConfig
from greennode.vmonitor_mcp_server.models import (
    CreateNotificationDto,
    CreateNotificationOtpDto,
    NotificationListData,
    NotificationOtpResult,
    NotificationTypeListData,
    UpdateNotificationDto,
    ValidateNotificationOtpDto,
)
from greennode.vmonitor_mcp_server.tool_annotations import DESTRUCTIVE, READ, WRITE
from greennode.vmonitor_mcp_server.validators import validate_id
from pydantic import Field
from typing import Any


def _seg(value: str, name: str) -> str:
    """URL-encode an identifier (e.g. an email address) for a path segment."""
    if not value or "/" in value or "\\" in value:
        raise ValueError(
            f"Invalid {name}: '{value}'. Must be non-empty and contain no path separators."
        )
    return urllib.parse.quote(value, safe="")


class NotificationHandler:
    """Register and serve vMonitor notification-gateway MCP tools."""

    def __init__(
        self,
        mcp,
        config: VmonitorConfig,
        client: VmonitorNotificationClient,
        allow_write: bool = False,
    ):
        self.mcp = mcp
        self.config = config
        self.client = client
        self.allow_write = allow_write

        self.mcp.tool(name="list_notification_types", annotations=READ)(
            self.list_notification_types
        )
        self.mcp.tool(name="list_notifications", annotations=READ)(self.list_notifications)
        self.mcp.tool(name="get_notification_otp_info", annotations=READ)(
            self.get_notification_otp_info
        )

        if self.allow_write:
            self.mcp.tool(name="create_notification_otp", annotations=WRITE)(
                self.create_notification_otp
            )
            self.mcp.tool(name="validate_notification_otp", annotations=READ)(
                self.validate_notification_otp
            )
            self.mcp.tool(name="create_notification", annotations=WRITE)(self.create_notification)
            self.mcp.tool(name="update_notification", annotations=WRITE)(self.update_notification)
            self.mcp.tool(name="delete_notification", annotations=DESTRUCTIVE)(
                self.delete_notification
            )

    async def list_notification_types(
        self,
        page: int | None = Field(None, ge=1, description="1-based page number (omit for all)"),
        size: int | None = Field(None, ge=1, description="Page size (omit for all)"),
    ) -> NotificationTypeListData:
        """List the notification channel types the gateway supports."""
        if page is None:
            data = await self.client.get("/v1/type/list")
        else:
            params: dict[str, Any] = {"page": page}
            if size is not None:
                params["size"] = size
            data = await self.client.get("/v1/type/list", params=params)
        return NotificationTypeListData.from_api(data)

    async def list_notifications(
        self,
        searchtext: str = Field("", description="Free-text search over name/address"),
        field: str = Field("", description="Field to search within (empty = all)"),
        type: str = Field("", description="Filter by channel type name (e.g. Email, SMS)"),
        page: int = Field(1, ge=1, description="1-based page number"),
        size: int = Field(10, ge=1, description="Page size"),
    ) -> NotificationListData:
        """List the user's notification channels with search/type filters.

        To route an alarm to a channel (create_metric_alarm / create_log_alarm
        inAlarm/ok/undetermined), use that channel's **`metric_mapping_id`**, NOT
        its `id` — the alarm API 500s on a plain channel id.

        Note: the gateway returns HTTP 500 if `searchtext` is set while `field` is
        empty, so when a search text is given without a field this defaults the
        field to `name` (the common case).
        """
        effective_field = field or ("name" if searchtext else "")
        params = {
            "searchtext": searchtext,
            "field": effective_field,
            "type": type,
            "page": page,
            "size": size,
        }
        data = await self.client.get("/v1/notification/list/typeSearch", params=params)
        return NotificationListData.from_api(data)

    async def get_notification_otp_info(
        self,
        address: str = Field(..., description="Address to look up a pending OTP for"),
    ) -> NotificationOtpResult:
        """Get info about a pending OTP for an address (reference, expiry)."""
        data = await self.client.get(f"/v1/notification/otps/{_seg(address, 'address')}")
        return NotificationOtpResult.from_api(data)

    async def create_notification_otp(
        self,
        body: CreateNotificationOtpDto = Field(
            ...,
            description="CreateNotificationOtpDto body: type, address, optional header.",
        ),
    ) -> NotificationOtpResult:
        """Send an OTP to an address to verify a channel before creating it.

        A real message (email/SMS/...) is dispatched to the address. The returned
        ``ref`` is required by validate_notification_otp.

        ## Requirements
        - Server must run with --allow-write
        - Not needed for Webhook channels.
        """
        data = await self.client.post(
            "/v1/notification/otps", json=body.model_dump(exclude_none=True)
        )
        return NotificationOtpResult.from_api(data)

    async def validate_notification_otp(
        self,
        body: ValidateNotificationOtpDto = Field(
            ...,
            description="ValidateNotificationOtpDto body: otp, address, ref, optional header.",
        ),
    ) -> NotificationOtpResult:
        """Validate an OTP; returns the ``code`` to pass into create/update."""
        data = await self.client.post(
            "/v1/notification/otps/validate", json=body.model_dump(exclude_none=True)
        )
        return NotificationOtpResult.from_api(data)

    async def create_notification(
        self,
        body: CreateNotificationDto = Field(
            ...,
            description=(
                "CreateNotificationDto body: name, address, type, optional header, "
                "optional otpCode (validated OTP code for verified channels)."
            ),
        ),
    ) -> NotificationOtpResult:
        """Create a notification channel.

        ## Requirements
        - Server must run with --allow-write
        - For Email/SMS/Slack/Telegram: verify first via create_notification_otp +
          validate_notification_otp, then pass the returned code as otpCode.
        - Webhook needs no otpCode.
        """
        data = await self.client.post("/v1/notification", json=body.model_dump(exclude_none=True))
        return NotificationOtpResult.from_api(data)

    async def update_notification(
        self,
        body: UpdateNotificationDto = Field(
            ...,
            description="UpdateNotificationDto body: id, name, address, type, header, otpCode.",
        ),
    ) -> NotificationOtpResult:
        """Update an existing notification channel.

        ## Requirements
        - Server must run with --allow-write
        - Re-verify with a fresh OTP (otpCode) if you change the address.
        """
        data = await self.client.put("/v1/notification", json=body.model_dump(exclude_none=True))
        return NotificationOtpResult.from_api(data)

    async def delete_notification(
        self,
        notification_id: str = Field(..., description="Notification ID to delete. IRREVERSIBLE."),
    ) -> str:
        """Delete a notification channel. IRREVERSIBLE.

        ## Requirements
        - Server must run with --allow-write
        - Confirm the id with list_notifications first.
        - Alarms still referencing the channel will stop notifying through it.
        """
        validate_id(notification_id, "notification_id")
        await self.client.delete(f"/v1/notification/{notification_id}")
        return f"Notification {notification_id} deleted."
