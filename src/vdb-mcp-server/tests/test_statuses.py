"""Tests for the status vocabularies and their classification.

The vocabularies come from the platform's own source (supplied 2026-09-08), so
the job of these tests is to prove every published value is accounted for and
that the classification does not fall back on the casing of the value.
"""

from __future__ import annotations

import pytest
from greennode.vdb_mcp_server.statuses import (
    BACKUP_STATUSES,
    DB_PORTAL_STATUSES,
    KAFKA_CLUSTER_STATUSES,
    KAFKA_TOPIC_STATUSES,
    KAFKA_USER_STATUSES,
    POSTGRESQL_CLUSTER_STATUSES,
    classify,
    describe,
    is_failure,
    is_transitional,
)


ALL_VOCABULARIES = (
    ("relational/memory instance", DB_PORTAL_STATUSES),
    ("backup", BACKUP_STATUSES),
    ("postgresql cluster", POSTGRESQL_CLUSTER_STATUSES),
    ("kafka cluster", KAFKA_CLUSTER_STATUSES),
)


@pytest.mark.parametrize(("label", "vocabulary"), ALL_VOCABULARIES)
def test_every_published_status_is_classified(label, vocabulary):
    """No published value may fall through to `unknown`.

    `unknown` exists for statuses the platform adds later, not for ones already
    documented -- a documented value landing there means this module is stale.
    """
    unclassified = [status for status in vocabulary if classify(status) == "unknown"]
    assert not unclassified, f"{label}: unclassified {unclassified}"


@pytest.mark.parametrize(("label", "vocabulary"), ALL_VOCABULARIES)
def test_no_duplicate_statuses_within_a_vocabulary(label, vocabulary):
    assert len(vocabulary) == len(set(vocabulary)), label


def test_casing_is_not_the_signal():
    """The tempting shortcut -- uppercase means settled -- is wrong.

    `BUILDING`, `BACKUP`, `REBOOT` and `RESIZE` are uppercase and in progress;
    `SHUTDOWN` is uppercase and settled. Any implementation that reads the case
    instead of the meaning breaks on exactly these.
    """
    for in_progress in ("BUILDING", "BUILD", "BACKUP", "REBOOT", "RESIZE"):
        assert classify(in_progress) == "transitional", in_progress
    assert classify("SHUTDOWN") == "settled"
    assert classify("ACTIVE") == "settled"


def test_uppercase_and_lowercase_pairs_are_distinct_states():
    """`REBOOT` and `rebooting` both exist and are not variants of one value."""
    assert "REBOOT" in DB_PORTAL_STATUSES
    assert "rebooting" in DB_PORTAL_STATUSES
    assert classify("REBOOT") == classify("rebooting") == "transitional"


@pytest.mark.parametrize("status", ["ERROR", "INTERNAL_ERROR", "FAILED", "BILLING_ERROR"])
def test_failure_statuses_are_flagged_as_platform_faults(status):
    """The user asked to be told about these rather than have them retried."""
    assert classify(status) == "failed"
    assert is_failure(status) is True
    assert "PLATFORM failure" in describe(status)
    assert "Do not retry" in describe(status)


@pytest.mark.parametrize("status", ["RESTART_REQUIRED", "BLOCKED", "EXPIRED", "WAIT_BILLING"])
def test_attention_statuses_are_not_failures(status):
    """These are stable states needing a decision, not platform faults."""
    assert classify(status) == "attention"
    assert is_failure(status) is False
    assert is_transitional(status) is False


def test_kafka_in_flight_states_are_transitional():
    for status in (
        "WAITING_WORKSPACE_CREATING",
        "NODES_SCALING_OUT",
        "REBALANCING_IN",
        "TOPIC_PROCESSING",
        "UPDATING_STORAGE_TYPE",
        "RECOVERING",
    ):
        assert classify(status) == "transitional", status


def test_kafka_terminal_states_are_not_transitional():
    assert classify("ACTIVE") == "settled"
    assert classify("DELETED") == "settled"
    assert classify("ERROR") == "failed"
    assert classify("WARNING_CONFIG_GROUP") == "attention"


def test_unknown_status_is_indeterminate_not_a_failure():
    """A status the platform adds tomorrow must not read as an error."""
    for status in ("SOMETHING_NEW", "", None):
        assert classify(status) == "unknown"
        assert is_failure(status) is False
        assert "not evidence of failure" in describe(status)


def test_transitional_guidance_tells_the_caller_to_poll():
    assert "Poll" in describe("BUILDING")
    assert is_transitional("BUILDING") is True


# --------------------------------------------------------------------------
# Kafka has THREE status vocabularies, not one
# --------------------------------------------------------------------------


def test_kafka_topic_and_user_statuses_are_separate_enums():
    """Supplied by the GreenNode Kafka team; neither is the cluster's.

    A topic has WAITING_* states and a user does not, so treating them as one
    set would invent states for one and lose them for the other.
    """
    assert "WAITING_CREATING" in KAFKA_TOPIC_STATUSES
    assert "WAITING_CREATING" not in KAFKA_USER_STATUSES
    assert "WAITING_DELETING" in KAFKA_TOPIC_STATUSES
    assert "WAITING_DELETING" not in KAFKA_USER_STATUSES
    # And neither belongs to the cluster's 48.
    assert "WAITING_CREATING" not in KAFKA_CLUSTER_STATUSES
    assert "CREATING" not in KAFKA_CLUSTER_STATUSES


def test_the_credential_rotation_states_are_recognised():
    """Re-issuing credentials is the only rotation observable as a status in vDB."""
    assert "DELETING_OLD_CREDENTIALS" in KAFKA_USER_STATUSES
    assert "GENERATING_NEW_CREDENTIALS" in KAFKA_USER_STATUSES
    assert classify("DELETING_OLD_CREDENTIALS") == "transitional"
    assert classify("GENERATING_NEW_CREDENTIALS") == "transitional"


def test_every_topic_and_user_status_classifies():
    """None may fall through to 'unknown' -- that is what this vocabulary is for."""
    for status in KAFKA_TOPIC_STATUSES + KAFKA_USER_STATUSES:
        assert classify(status) != "unknown", status


def test_topic_and_user_terminal_states_are_settled_not_transitional():
    for status in ("ACTIVE", "DELETED"):
        assert classify(status) == "settled", status
    assert classify("ERROR") == "failed"
