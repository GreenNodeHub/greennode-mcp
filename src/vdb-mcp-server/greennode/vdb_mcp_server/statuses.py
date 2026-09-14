"""The status vocabulary of each vDB family, and what each status means to act on.

The API declares no ``enum`` anywhere and has no endpoint that lists statuses,
so these tuples come from the platform's own source, supplied by the GreenNode
team on 2026-09-08 and kept verbatim in
``docs/api-docs-local/vdb-status-notes.txt``. Diff against that file when
touching this one. :func:`classify` still answers ``unknown`` for anything
outside them, and callers must treat that as "cannot tell" rather than as a
failure -- the platform can add a state before this file learns about it.

Classification is by **meaning, not by spelling**. It is tempting to read the
casing as a signal, since the transitional relational states are lowercase
(``stopping``) and the settled ones uppercase (``ACTIVE``) -- but ``BUILDING``,
``BACKUP``, ``REBOOT`` and ``RESIZE`` are uppercase and describe work in
progress, so casing gets it wrong exactly where being wrong costs most.

Note also that ``REBOOT``/``rebooting`` and ``SHUTDOWN``/``stopping`` are
*different* states, not casing variants of one: the uppercase pair names the
operation, the lowercase pair the transition into it.
"""

from __future__ import annotations

from typing import Literal


DB_PORTAL_STATUSES: tuple[str, ...] = (
    "BUILDING",
    "BUILD",
    "ACTIVE",
    "ERROR",
    "BACKUP",
    "REBOOT",
    "SHUTDOWN",
    "RESIZE",
    "RESTART_REQUIRED",
    "BLOCKED",
    "FAILED",
    "EXPIRED",
    "INTERNAL_ERROR",
    "starting",
    "stopping",
    "rebooting",
    "deleting",
    "resizing",
    "updating",
    "recovering",
    "promoting",
)
"""Statuses a relational or MemoryStore **instance** can report."""

BACKUP_STATUSES: tuple[str, ...] = (
    "NEW",
    "BUILDING",
    "SAVING",
    "COMPLETED",
    "ERROR",
    "FAILED",
)
"""Statuses a relational or MemoryStore **backup** can report."""

POSTGRESQL_CLUSTER_STATUSES: tuple[str, ...] = (
    "ACTIVE",
    "BUILDING",
    "WAIT_BILLING",
    "ERROR",
    "BILLING_ERROR",
    "DELETING",
    "DELETED",
    "EXPIRED",
    "REBOOTING",
    "BACKUP",
    "RESTART_REQUIRED",
)
"""Statuses a **PostgreSQL Cluster** can report."""

KAFKA_CLUSTER_STATUSES: tuple[str, ...] = (
    "ERROR",
    "WAITING_WORKSPACE_CREATING",
    "WORKSPACE_CREATING",
    "INFRA_CREATING",
    "NODES_CREATING",
    "WAITING_CLUSTER_CREATING",
    "CLUSTER_CREATING",
    "CREATING_BILL",
    "ACTIVE",
    "TOPICS_DELETING",
    "USERS_DELETING",
    "WAITING_CLUSTER_DELETING",
    "CLUSTER_DELETING",
    "WAITING_WORKSPACE_DELETING",
    "WORKSPACE_DELETING",
    "INFRA_DELETING",
    "DELETED",
    "WAITING_UPDATING",
    "UPDATING",
    "WAITING_UPDATING_STORAGE_SIZE",
    "UPDATING_STORAGE_SIZE",
    "WAITING_UPDATING_STORAGE_TYPE",
    "UPDATING_STORAGE_TYPE",
    "WAITING_UPDATING_CONFIG_GROUP",
    "UPDATING_CONFIG_GROUP",
    "WARNING_CONFIG_GROUP",
    "WAITING_SCALING",
    "SCALING_OUT_GET_INFO",
    "INFRA_SCALING_OUT",
    "NODES_SCALING_OUT",
    "INFRA_SCALING_IN",
    "NODES_SCALING_IN",
    "WAITING_CLUSTER_SCALING_OUT",
    "CLUSTER_SCALING_OUT",
    "WAITING_CLUSTER_SCALING_IN",
    "CLUSTER_SCALING_IN",
    "WAITING_REBALANCING_OUT",
    "REBALANCING_OUT",
    "WAITING_REBALANCING_IN",
    "REBALANCING_IN",
    "WAITING_FLOATING_IPS_UPDATING",
    "FLOATING_IPS_UPDATING",
    "SECURITY_GROUP_RULES_UPDATING",
    "TOPIC_PROCESSING",
    "USER_PROCESSING",
    "EXPIRING",
    "EXPIRED",
    "RECOVERING",
)
"""Statuses a **Kafka cluster** can report. Far more granular than the others --
it names each step of provisioning, scaling and teardown separately."""


FAILURE_STATUSES = frozenset(
    {
        "ERROR",
        "INTERNAL_ERROR",
        "FAILED",
        "BILLING_ERROR",
    }
)
"""A resource here failed on the **platform** side, not because of the request.

Report the resource id, its zone and what was asked for so the platform team
can look. Do not retry the same call as though the payload were wrong, and do
not quietly try a different shape.
"""

SETTLED_STATUSES = frozenset(
    {
        "ACTIVE",
        "COMPLETED",
        "DELETED",
        "SHUTDOWN",
    }
)
"""Nothing is in flight. ``SHUTDOWN`` is a stopped instance -- stable, and still billed
for storage, so it is settled rather than needing attention."""

ATTENTION_STATUSES = frozenset(
    {
        "RESTART_REQUIRED",
        "BLOCKED",
        "EXPIRED",
        "EXPIRING",
        "WAIT_BILLING",
        "WARNING_CONFIG_GROUP",
    }
)
"""Stable, but the resource needs a human decision before it is usable again."""

KAFKA_TOPIC_STATUSES: tuple[str, ...] = (
    "ERROR",
    "WAITING_CREATING",
    "CREATING",
    "ACTIVE",
    "WAITING_DELETING",
    "DELETING",
    "DELETED",
    "WAITING_DELETING_ALL",
    "DELETING_ALL",
    "WAITING_UPDATING",
    "UPDATING",
)
"""Statuses of a Kafka **topic** -- its own enum, not the cluster's.

Supplied by the GreenNode Kafka team (``TopicStatus`` in
``../api-docs-local/vdb-status-notes.txt``). Before it existed, a freshly
created topic's ``WAITING_CREATING`` classified as ``unknown``: the 48-value
cluster vocabulary does not contain it.
"""

KAFKA_USER_STATUSES: tuple[str, ...] = (
    "ERROR",
    "CREATING",
    "ACTIVE",
    "DELETING",
    "DELETING_ALL",
    "DELETED",
    "UPDATING",
    "DELETING_OLD_CREDENTIALS",
    "GENERATING_NEW_CREDENTIALS",
)
"""Statuses of a Kafka **user** -- a third enum again, not the topic's.

Supplied by the GreenNode Kafka team (``UserStatus`` in
``../api-docs-local/vdb-status-notes.txt``). Two differences from
:data:`KAFKA_TOPIC_STATUSES` matter and neither is guessable:

* a user has **no** ``WAITING_*`` state at all -- it goes straight to
  ``CREATING``;
* re-issuing credentials has two states of its own,
  ``DELETING_OLD_CREDENTIALS`` and ``GENERATING_NEW_CREDENTIALS``, which is the
  only place in vDB where a credential rotation is observable as a status.

So "Kafka's sub-resources share a vocabulary" is false: there are three.
"""

TRANSITIONAL_STATUSES = frozenset(
    {
        # relational / memory instances
        "BUILDING",
        "BUILD",
        "BACKUP",
        "REBOOT",
        "RESIZE",
        "starting",
        "stopping",
        "rebooting",
        "deleting",
        "resizing",
        "updating",
        "recovering",
        "promoting",
        # backups
        "NEW",
        "SAVING",
        # postgresql cluster
        "DELETING",
        "REBOOTING",
    }
    # Every remaining Kafka status names an operation in flight -- creating,
    # deleting, updating, scaling, rebalancing, waiting to do one of those.
    # Listing them by exclusion keeps this set correct if the enum grows.
    | {
        status
        for status in KAFKA_CLUSTER_STATUSES + KAFKA_TOPIC_STATUSES + KAFKA_USER_STATUSES
        if status
        not in {
            "ERROR",
            "ACTIVE",
            "DELETED",
            "EXPIRED",
            "EXPIRING",
            "WARNING_CONFIG_GROUP",
        }
    }
)
"""Work is in progress; poll again rather than reporting the current state as final."""


StatusKind = Literal["settled", "transitional", "failed", "attention", "unknown"]


def classify(status: str | None) -> StatusKind:
    """Say what kind of state *status* is, so a caller knows what to do next.

    ``unknown`` means the platform reported something none of the published
    vocabularies contain. Treat it as "cannot tell" -- never as a failure.
    Matching is exact: the platform is consistent about the casing of each
    individual value, even though it is not consistent across values.
    """
    if not status:
        return "unknown"
    if status in FAILURE_STATUSES:
        return "failed"
    if status in TRANSITIONAL_STATUSES:
        return "transitional"
    if status in SETTLED_STATUSES:
        return "settled"
    if status in ATTENTION_STATUSES:
        return "attention"
    return "unknown"


GUIDANCE: dict[StatusKind, str] = {
    "settled": "Nothing is in progress; this state is final until something changes it.",
    "transitional": (
        "An operation is still running. Poll the get tool again before reporting a "
        "result, and do not start another operation on this resource yet."
    ),
    "failed": (
        "This is a PLATFORM failure, not a rejected request. Report the resource id, its "
        "zone and what was asked for so the platform team can investigate. Do not retry "
        "the same call as though the payload were at fault."
    ),
    "attention": (
        "Stable but not usable as-is: it needs a decision from the user (a restart, a "
        "renewal, or a billing or configuration fix). Say what is needed rather than "
        "acting on their behalf."
    ),
    "unknown": (
        "The platform reported a status none of the published vocabularies list. Report it "
        "verbatim and treat it as indeterminate -- it is not evidence of failure."
    ),
}
"""What to do about each kind of status, in the words a tool response can carry."""


def describe(status: str | None) -> str:
    """Return the guidance for *status*."""
    return GUIDANCE[classify(status)]


def is_transitional(status: str | None) -> bool:
    """True while an operation on the resource is still running."""
    return classify(status) == "transitional"


def is_failure(status: str | None) -> bool:
    """True when the status indicates a platform-side failure."""
    return classify(status) == "failed"
