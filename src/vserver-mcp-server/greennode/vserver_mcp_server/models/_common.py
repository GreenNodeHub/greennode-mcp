"""Shared building blocks for the vServer response models.

Field-extraction helpers that more than one domain needs, plus the tag
payload every create/update body accepts.
"""

from __future__ import annotations

import json
from pydantic import BaseModel, ConfigDict, Field


def _image_types_from_metadata(raw: object) -> list[str]:
    """Extract ``imageTypeSupport`` from a flavor's ``metaData`` field.

    The API returns metaData as a JSON **string**, not an object, and it is
    absent on some flavors — treat any parse failure as "unknown", never raise.
    """
    if not isinstance(raw, str) or not raw.strip():
        return []
    try:
        parsed = json.loads(raw)
    except ValueError:
        return []
    if not isinstance(parsed, dict):
        return []
    types = parsed.get("imageTypeSupport")
    if not isinstance(types, list):
        return []
    return [str(t).strip() for t in types if str(t).strip()]


def _resource_id(data: dict) -> str:
    """Return the id callers can pass back to another tool.

    List and get responses put the resource id straight into ``id``, but
    create and update responses answer with the platform's internal numeric
    key there and keep the real id in ``uuid``. Preferring ``uuid`` and
    stringifying whatever is left keeps one model valid for both shapes —
    reading ``id`` first makes a successful create look like a validation
    failure and hands back an id no other endpoint accepts.
    """
    for key in ("uuid", "id"):
        value = data.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


def _zone_id(data: dict) -> str:
    """Return a resource's availability-zone id.

    vServer nests the zone as an object whose ``uuid`` is the readable zone id
    (``HCM03-1C``); a few endpoints instead return a flat ``zoneId``.
    """
    zone = data.get("zone")
    if isinstance(zone, dict):
        return zone.get("uuid") or zone.get("name") or ""
    return data.get("zoneId") or ""


class TagDto(BaseModel):
    """A key/value tag attached at resource-creation time."""

    model_config = ConfigDict(extra="forbid")

    key: str = Field(..., description="Tag key")
    value: str = Field("", description="Tag value")
