"""Coercion helpers shared by every vBackup model.

Three API traits would otherwise be re-handled in every ``from_api``:

- **Numbers arrive as floats.** The gateway sends ``hour: 12.0``,
  ``retention: 1.0`` for fields that are conceptually integers. A model that
  accepts only ``int`` rejects a perfectly valid policy.
- **Sizes are bytes.** Every size the API reports is a byte count, so a caller
  reading one as GiB is off by a factor of 2^30.
- **Snapshot fields are JSON strings, not objects.** A history record embeds
  ``policySnapshot`` / ``destinationSnapshot`` as an escaped JSON *string*.
  Reading them as dicts yields nothing; parsing them without a guard raises on
  the records where they are absent.
"""

from __future__ import annotations

import json
from typing import Any


BYTES_PER_GIB = 1024**3


def as_int(value: Any, default: int = 0) -> int:
    """Coerce an API number to int, tolerating the floats vBackup returns."""
    if isinstance(value, bool) or value is None:
        return default
    if isinstance(value, (int, float)):
        return int(value)
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def as_gib(value: Any) -> float:
    """Convert a byte count to GiB, rounded to two decimals."""
    return round(as_int(value) / BYTES_PER_GIB, 2)


def as_text(value: Any) -> str:
    """Normalise an optional API string to a plain string."""
    return value if isinstance(value, str) else ""


def as_dict(value: Any) -> dict:
    """Return a dict from a field the API may send as a dict or a JSON string.

    ``policySnapshot`` and ``destinationSnapshot`` arrive as escaped JSON
    strings, and a destination's ``config`` can be either, depending on the
    endpoint. Anything unparseable becomes an empty dict rather than raising —
    a malformed snapshot must not sink the whole history read.
    """
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def resource_id(data: dict, *keys: str) -> str:
    """Return the first non-empty id among *keys*, stringified.

    vBackup spells a point's id differently per endpoint: the generic
    collections use ``id``, while the vServer projection of a volume point uses
    ``backupVolumePointId``. A model that reads only one of them comes back
    with an empty id and fails later, at the call that consumes it.
    """
    for key in keys or ("id",):
        value = data.get(key)
        if value not in (None, ""):
            return str(value)
    return ""
