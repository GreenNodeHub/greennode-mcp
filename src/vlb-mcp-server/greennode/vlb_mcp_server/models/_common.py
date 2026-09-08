"""Shared building blocks for the vLB response models."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


def _resource_id(data: dict) -> str:
    """Return the id callers can pass back to another tool.

    Every vLB response DTO carries the real id in ``uuid``; ``id`` is a
    fallback for the untyped create responses and for the vServer objects the
    discovery tools project. Preferring ``uuid`` and stringifying whatever is
    left keeps one model valid for both shapes.
    """
    for key in ("uuid", "id"):
        value = data.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


def _zone_id(data: dict) -> str:
    """Return a resource's availability-zone id.

    vServer nests the zone as an object whose ``uuid`` is the readable zone id
    (``HCM03-1C``); vLB and a few vServer endpoints use a flat ``zoneId``.
    """
    zone = data.get("zone")
    if isinstance(zone, dict):
        return zone.get("uuid") or zone.get("name") or ""
    return data.get("zoneId") or ""


def csv_to_list(value: object) -> list[str]:
    """Split vLB's comma-separated CIDR strings into a list.

    ``allowedCidrs`` and ``blockedCidrs`` are typed ``string`` in both
    directions, with the separator undocumented. Comma is what the console
    sends; whitespace around entries is tolerated and empty entries dropped so
    an empty string reads as "no list" rather than ``[""]``.
    """
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if not isinstance(value, str):
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def list_to_csv(value: object) -> str:
    """Join a CIDR list back into the single string the API expects."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return ",".join(str(v).strip() for v in value if str(v).strip())
    return ""


class TagDto(BaseModel):
    """A key/value tag attached at resource-creation time."""

    model_config = ConfigDict(extra="forbid")

    key: str = Field(..., description="Tag key")
    value: str = Field("", description="Tag value")
