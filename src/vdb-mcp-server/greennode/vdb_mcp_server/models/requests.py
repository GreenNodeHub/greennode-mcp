"""Typed request DTOs for vDB write operations.

Every DTO sets ``extra="forbid"``: the API answers any validation failure with
the single word ``in_valid`` and no field name, so a typo that reaches the wire
is close to undiagnosable. Rejecting unknown fields locally turns that into a
precise error.

The same reasoning drives the validators here. `PASSWORD_PATTERN` in
particular: the platform's password rule is not in the OpenAPI document at all,
and a violation surfaces as `400 Bad request: in_valid` — indistinguishable
from a wrong flavour, a wrong subnet or an empty body.
"""

from __future__ import annotations

import re
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from typing import Literal


PASSWORD_ALLOWED = "a-zA-Z0-9$^_<>"
"""The only characters the platform accepts in a database password.

Note what is missing: ``! # @ %`` and every other punctuation mark, plus
spaces. The rule is not in the OpenAPI document, and a violation surfaces as
``400 Bad request: in_valid`` — the same message as a wrong flavour, a wrong
subnet or an empty body. Validating locally is the only way to tell a caller
what is actually wrong.
"""

RELATIONAL_PASSWORD_LENGTH = (8, 32)
"""Length range for relational engines (MySQL, MariaDB, PostgreSQL)."""

MEMORY_PASSWORD_LENGTH = (16, 128)
"""Length range for MemoryStore (Redis) -- four times the relational minimum.

A password that is valid for a relational instance is very often too short
here, so the two families cannot share one validator.
"""


def _password_pattern(min_length: int, max_length: int) -> re.Pattern[str]:
    """Build the password rule for a length range.

    Both ends of the string are constrained: it must start with a letter and
    end with a letter or digit, so the free middle is two characters shorter
    than the overall length.
    """
    return re.compile(
        rf"^[a-zA-Z][{PASSWORD_ALLOWED}]{{{min_length - 2},{max_length - 2}}}[a-zA-Z0-9]$"
    )


RELATIONAL_PASSWORD_PATTERN = _password_pattern(*RELATIONAL_PASSWORD_LENGTH)
MEMORY_PASSWORD_PATTERN = _password_pattern(*MEMORY_PASSWORD_LENGTH)


def _password_message(min_length: int, max_length: int) -> str:
    return (
        f"password must be {min_length}-{max_length} characters using only letters, digits "
        "and $ ^ _ < >, start with a letter, and end with a letter or digit. The platform "
        "rejects any other character (including ! # @ % and spaces) with an unhelpful "
        "'in_valid' error."
    )


RELATIONAL_PASSWORD_MESSAGE = _password_message(*RELATIONAL_PASSWORD_LENGTH)
MEMORY_PASSWORD_MESSAGE = _password_message(*MEMORY_PASSWORD_LENGTH)

# Kept as the relational spelling for backwards compatibility of the export.
PASSWORD_RULE_MESSAGE = RELATIONAL_PASSWORD_MESSAGE


def validate_relational_password(value: str) -> str:
    """Check *value* against the relational password rule (8-32), or raise."""
    if not RELATIONAL_PASSWORD_PATTERN.fullmatch(value or ""):
        raise ValueError(RELATIONAL_PASSWORD_MESSAGE)
    return value


def validate_memory_password(value: str) -> str:
    """Check *value* against the MemoryStore password rule (16-128), or raise."""
    if not MEMORY_PASSWORD_PATTERN.fullmatch(value or ""):
        raise ValueError(MEMORY_PASSWORD_MESSAGE)
    return value


validate_password = validate_relational_password
"""Alias kept so existing relational callers read naturally."""


CANONICAL_DATASTORE_TYPES = {
    "mysql": "MySQL",
    "postgresql": "PostgreSQL",
    "mariadb": "MariaDB",
    "kafka": "Kafka",
    "redis": "Redis",
}
"""Canonical spelling for each engine, keyed by its lowercase form.

The catalogue reports `type` in lowercase (`mysql`) while the console and the
Portal send the capitalised form (`MySQL`), and a listing simply **echoes**
whichever spelling created the instance -- so the same engine shows up both
ways in one response. Requests are normalised to the capitalised form here so
this server never adds to that inconsistency, whatever a caller types.

Safe to do because the endpoints that consume `type` match it
case-insensitively; only the values stored and read back differ.
"""


def canonical_datastore_type(value: str) -> str:
    """Return the platform's canonical spelling of an engine name.

    An unrecognised engine is passed through untouched rather than rejected:
    the platform declares no enum anywhere, so a new engine must not be blocked
    by this map being out of date.
    """
    if not value:
        return value
    return CANONICAL_DATASTORE_TYPES.get(value.strip().lower(), value.strip())


BACKUP_RETENTION_MIN_DAYS = 2
BACKUP_RETENTION_MAX_DAYS = 14
BACKUP_RETENTION_DESCRIPTION = (
    "Required when backupAuto is true. How many DAYS an automatic backup is retained, "
    "2-14. Not a window length in hours -- the field name suggests a duration but the "
    "platform reads it as a retention period, and a value outside 2-14 comes back as a "
    "bare 'in_valid'."
)
"""Retention rule for ``backupDuration``, identical in all seven schemas that carry it.

Worth stating once because the name misleads: the value sits next to
``backupTime`` ("02:00"), which makes it read as the length of the backup
window. The spec's own description says otherwise -- days of retention -- and
the lower bound is 2, so the obvious ``1`` is rejected.
"""


class _Dto(BaseModel):
    """Base for every vDB request DTO."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class RelationalDatabaseDto(_Dto):
    """One database to create inside a new relational instance."""

    name: str = Field(..., description="Database name")
    characterSet: str | None = Field(None, description="Character set, e.g. 'utf8mb4'")
    collate: str | None = Field(None, description="Collation, e.g. 'utf8mb4_general_ci'")


class RelationalUserDto(_Dto):
    """The admin user created with a new relational instance."""

    name: str = Field(..., description="Admin username")
    password: str = Field(..., description=f"Admin password. {PASSWORD_RULE_MESSAGE}")
    databases: list[RelationalDatabaseDto] = Field(
        default_factory=list, description="Databases this user owns"
    )

    @field_validator("password")
    @classmethod
    def _password_rule(cls, value: str) -> str:
        return validate_relational_password(value)


class CreateRelationalInstanceDto(_Dto):
    """Body of `POST /v1/payment/database-instances` (relational).

    `volumeType` and `packageId` are both zone-scoped, so all three of
    `locateZoneId`, `volumeType` and `packageId` must come from the *same*
    zone or the order is rejected with `in_valid`.
    """

    name: str = Field(..., description="Instance name")
    datastoreType: str = Field(
        ...,
        description=(
            "Engine type from list_relational_datastores. Normalised to the platform's "
            "canonical spelling (MySQL, MariaDB, PostgreSQL) whatever case is given."
        ),
    )
    datastoreVersion: str = Field(
        ..., description="Engine version from list_relational_datastores"
    )
    packageId: str = Field(
        ..., description="Flavour id (as a string) from list_relational_flavors for this zone"
    )
    volumeType: str = Field(
        ..., description="Volume type from list_relational_volume_types for this zone"
    )
    volumeSize: int = Field(..., ge=20, description="Storage size in GB; the API minimum is 20")
    netIds: list[str] = Field(
        ..., min_length=1, description="Subnet IDs from list_relational_subnets"
    )
    locateZoneId: str = Field(..., description="Availability zone from list_relational_zones")
    user: RelationalUserDto | None = Field(None, description="Admin user to create")
    databases: list[RelationalDatabaseDto] = Field(
        default_factory=list, description="Databases to create"
    )
    configId: str | None = Field(None, description="Configuration group ID to attach")
    publicAccess: bool = Field(False, description="Expose the instance publicly")
    backupAuto: bool = Field(False, description="Enable automatic backups")
    backupDuration: int | None = Field(None, ge=2, le=14, description=BACKUP_RETENTION_DESCRIPTION)
    backupTime: str | None = Field(None, description="Automatic backup window start, e.g. '00:00'")
    poc: bool = Field(False, description="Proof-of-concept (trial) instance")

    @field_validator("datastoreType")
    @classmethod
    def _canonical_engine(cls, value: str) -> str:
        return canonical_datastore_type(value)


class ResizeRelationalInstanceDto(_Dto):
    """New flavour for `POST /v1/database-instances/{id}/resize-instance`."""

    packageId: str = Field(
        ..., description="Target flavour id from list_relational_flavors for the instance's zone"
    )
    poc: bool = Field(False, description="Proof-of-concept (trial) pricing")


class ResizeRelationalStorageDto(_Dto):
    """New storage for `POST /v1/database-instances/{id}/resize-storage`.

    Storage can only grow. There is no relational equivalent in the memory
    family at all.
    """

    volumeSize: int = Field(..., ge=20, description="New storage size in GB; must be larger")
    volumeType: str | None = Field(
        None, description="New volume type; must be one offered in the instance's zone"
    )
    poc: bool = Field(False, description="Proof-of-concept (trial) pricing")


class CreateRelationalReplicaDto(_Dto):
    """Body of `POST /v1/database-instances/{id}/create-replicas`."""

    name: str = Field(..., description="Replica name")
    packageId: str = Field(..., description="Flavour id for the replica")
    volumeType: str = Field(..., description="Volume type for the replica")
    volumeSize: int = Field(..., ge=20, description="Storage size in GB")
    datastoreType: str | None = Field(None, description="Engine type; defaults to the source's")
    datastoreVersion: str | None = Field(
        None, description="Engine version; defaults to the source's"
    )
    netIds: list[str] = Field(default_factory=list, description="Subnet IDs for the replica")
    locateZoneId: str | None = Field(None, description="Availability zone for the replica")
    configId: str | None = Field(None, description="Configuration group ID to attach")
    publicAccess: bool = Field(False, description="Expose the replica publicly")
    backupAuto: bool = Field(False, description="Enable automatic backups on the replica")
    backupDuration: int | None = Field(None, ge=2, le=14, description=BACKUP_RETENTION_DESCRIPTION)
    backupTime: str | None = Field(None, description="Backup window start")
    poc: bool = Field(False, description="Proof-of-concept (trial) pricing")

    @field_validator("datastoreType")
    @classmethod
    def _canonical_engine(cls, value: str | None) -> str | None:
        return None if value is None else canonical_datastore_type(value)


class UpdateRelationalSettingDto(_Dto):
    """Body of `PUT /v1/database-instances/{id}/update/setting`.

    Every field is optional, but the API takes the body as the whole desired
    state rather than a patch, so send the values that should remain as well as
    the ones being changed.
    """

    password: str | None = Field(None, description=f"New admin password. {PASSWORD_RULE_MESSAGE}")
    publicAccess: bool | None = Field(None, description="Expose the instance publicly")
    backupAuto: bool | None = Field(None, description="Enable automatic backups")
    backupDuration: int | None = Field(None, ge=2, le=14, description=BACKUP_RETENTION_DESCRIPTION)
    backupTime: str | None = Field(None, description="Backup window start, e.g. '00:00'")

    @field_validator("password")
    @classmethod
    def _password_rule(cls, value: str | None) -> str | None:
        return None if value is None else validate_relational_password(value)


class UpdateSecurityRuleDto(_Dto):
    """One rule in the array sent to `PUT /v1/database-instances/{id}/secrules`.

    The endpoint takes a JSON **array** of these and replaces the whole rule
    set, so omitting an existing rule deletes it.
    """

    id: str | None = Field(None, description="Existing rule ID; omit to add a new rule")
    portRangeMin: int = Field(..., ge=0, le=65535, description="Lowest port to allow")
    portRangeMax: int = Field(..., ge=0, le=65535, description="Highest port to allow")
    remoteIpPrefix: str = Field(..., description="CIDR allowed to connect, e.g. '10.0.0.0/8'")


class DeleteRelationalInstanceDto(_Dto):
    """Deletion options for `POST /v1/database-instances/{id}/delete`."""

    createFinalBackup: bool = Field(
        False, description="Take one last backup before the instance is destroyed"
    )
    deleteAllBackup: bool = Field(
        False,
        description=(
            "Also destroy every existing backup of this instance. Irreversible, and it removes "
            "the only way to recover the data."
        ),
    )


BACKUP_TYPE_FULL = "FULL"
BACKUP_TYPE_INCREMENTAL = "INCREMENTAL"

DEFAULT_BACKUP_DESCRIPTION = "Manual created"
"""What the Portal writes as the description of a hand-made backup.

`description` is marked optional in the spec, and the API accepts a request
without it with HTTP 200 and a `backupId` -- then **fails the backup
asynchronously** with "An error occurred when communicating with system", after
which it appears in no listing and the id resolves to nothing. Only
`list_relational_instance_histories` records that it happened.

The Portal never lets a user leave it blank: it generates this string for a
manual backup. So the field is defaulted rather than left out, and never sent
as null.
"""


class CreateBackupDto(_Dto):
    """Body of `POST /v1/backups/create` -- **both** families.

    Upstream this is one schema, `CreateBackupRequest`, referenced by the
    relational and the memory path alike, so one DTO serves both rather than
    two drifting apart over a single wire contract. Only the *restore* body
    differs between the families.

    `backupType` is one of the few closed value sets in the whole API. It has
    no `enum` -- nothing here does -- but the spec's own description names both
    values ("Allowed values: FULL, INCREMENTAL"), so a `Literal` is safe here
    where it would not be for a status or a flavour.
    """

    dbInstanceId: str = Field(..., description="Instance to back up, e.g. 'db-1234abcd-...'")
    name: str = Field(..., description="Backup name")
    backupType: Literal["FULL", "INCREMENTAL"] = Field(
        BACKUP_TYPE_FULL,
        description=(
            "FULL copies everything; INCREMENTAL stores only what changed since parentId "
            "and cannot be restored without its chain of parents."
        ),
    )
    parentId: str | None = Field(
        None,
        description=(
            "Backup this one builds on. Required when backupType is INCREMENTAL, and must "
            "be a backup of the same instance."
        ),
    )
    description: str = Field(
        DEFAULT_BACKUP_DESCRIPTION,
        min_length=1,
        description=(
            "Backup description. Optional in the spec but required in practice -- omitting "
            "it makes the backup fail asynchronously. Defaults to the string the Portal "
            "generates for a hand-made backup."
        ),
    )

    @model_validator(mode="after")
    def _incremental_needs_a_parent(self) -> "CreateBackupDto":
        """An INCREMENTAL backup without a parent is rejected as a bare `in_valid`."""
        if self.backupType == BACKUP_TYPE_INCREMENTAL and not self.parentId:
            raise ValueError(
                "parentId is required when backupType is INCREMENTAL -- an incremental "
                "backup stores only the changes since another backup. Use "
                "list_relational_instance_backups to find one, or ask for a FULL backup."
            )
        if self.backupType == BACKUP_TYPE_FULL and self.parentId:
            raise ValueError(
                "parentId only applies to an INCREMENTAL backup; a FULL backup has no parent."
            )
        return self


class RestoreRelationalBackupDto(_Dto):
    """The `config` object inside `POST /v1/backups/{id}/restore` (relational).

    This describes the **new instance** the restore creates -- it does not
    overwrite the instance the backup came from. Everything a create needs has
    to be supplied again, and `packageId`, `volumeType` and `locateZoneId` are
    zone-scoped exactly as they are on a create.

    `backupId` is deliberately absent: the API wants it both in the path and in
    this config, and the handler fills it in from the path argument so the two
    cannot disagree.
    """

    name: str = Field(..., description="Name for the NEW instance the restore creates")
    packageId: str = Field(
        ..., description="Flavour id from list_relational_flavors for the target zone"
    )
    volumeType: str = Field(
        ..., description="Volume type from list_relational_volume_types for the target zone"
    )
    volumeSize: int = Field(
        ...,
        ge=20,
        description=(
            "Storage size in GB for the new instance; must be at least the source "
            "instance's size, reported as storage_size_gb by get_relational_backup"
        ),
    )
    datastoreType: str = Field(..., description="Engine, normally the backup's own datastore_type")
    datastoreVersion: str = Field(..., description="Engine version, normally the backup's own")
    netIds: list[str] = Field(
        ..., min_length=1, description="Subnet IDs from list_relational_subnets"
    )
    locateZoneId: str = Field(..., description="Availability zone from list_relational_zones")
    configId: str | None = Field(None, description="Configuration group ID to attach")
    publicAccess: bool = Field(False, description="Expose the new instance publicly")
    backupAuto: bool = Field(False, description="Enable automatic backups on the new instance")
    backupDuration: int | None = Field(None, ge=2, le=14, description=BACKUP_RETENTION_DESCRIPTION)
    backupTime: str | None = Field(None, description="Automatic backup window start, e.g. '02:00'")
    poc: bool = Field(False, description="Proof-of-concept (trial) pricing")

    @field_validator("datastoreType")
    @classmethod
    def _canonical_engine(cls, value: str) -> str:
        return canonical_datastore_type(value)


class DeleteBackupDto(_Dto):
    """One entry in the JSON **array** a backup deletion takes -- both families.

    One upstream schema (`DeleteBackupRequest`) behind two different endpoints:
    `DELETE /vdb-relational/v1/backups/{backupId}/delete`, which also repeats
    the id in the path and so deletes exactly one, and
    `POST /vdb-memory/v1/backups/delete`, which has no path id and therefore
    deletes **as many entries as the array holds**.

    Both endpoints are among the six that take an array rather than an object.
    The handlers build these from their own arguments, so callers never
    construct one directly -- it exists so the array's element shape is typed
    and `extra="forbid"` applies to it like every other request body.
    """

    backupId: str = Field(..., description="Backup to delete")


DEPLOY_TYPE_SINGLE_NODE = "single_node"
DEPLOY_TYPE_CLUSTER = "cluster"

DEPLOY_TYPE_IS_REQUIRED = """`deployType` must be sent when creating a configuration group.

The spec marks it optional, says it is "currently only available for
PostgreSQL", and names ``single_node`` as its default value. All three readings
lead to omitting it, and omitting it returns ``400 bad_request`` -- verified
live for MySQL, with and without a description. Supplying it succeeds.

This is the second field in this package whose documented default is not
applied server-side; the backup ``description`` is the other. Treat "optional
with a default" in this spec as a claim to test, not a fact.
"""


class CreateRelationalConfigurationDto(_Dto):
    """Body of `POST /v1/configurations/create` (relational).

    A group is bound to one engine and version for life: the create fixes
    `datastoreType`/`datastoreVersion` and no update can change them. Attaching
    it to an instance running a different engine is not possible, so get the
    pair right here.
    """

    name: str = Field(..., min_length=1, description="Configuration group name")
    datastoreType: str = Field(
        ...,
        description=(
            "Engine the group applies to. The spec names MySQL, PostgreSQL and MariaDB; "
            "normalised to the platform's canonical spelling whatever case is given."
        ),
    )
    datastoreVersion: str = Field(
        ..., min_length=1, description="Engine version from list_relational_datastores"
    )
    description: str | None = Field(None, description="Configuration group description")
    deployType: Literal["single_node", "cluster"] = Field(
        DEPLOY_TYPE_SINGLE_NODE,
        description=(
            "Required in practice, though the spec calls it optional with a default. A "
            "'cluster' group can only be used by a PostgreSQL Cluster, never by a standalone "
            "instance; use single_node for MySQL and MariaDB."
        ),
    )

    @field_validator("datastoreType")
    @classmethod
    def _canonical_engine(cls, value: str) -> str:
        return canonical_datastore_type(value)


class UpdateConfigurationDto(_Dto):
    """The `values` half of `PUT /v1/configurations/update` -- **both** families.

    Upstream this is one schema, `UpdateConfigGroupRequest`, referenced by the
    relational and the memory path alike. Only the *create* body differs
    between the families, and only by one field.

    `id` is deliberately absent: the handler fills it from its own `config_id`
    argument so the path-style argument and the body cannot disagree, the same
    way the restore DTO omits `backupId`.

    Keys must be parameter names from `list_relational_configuration_params`
    for **this group's** engine and version, and values must respect the
    `minimum`/`maximum` or `allowed_values` reported there.
    """

    values: dict[str, str | int | float | bool] = Field(
        ...,
        min_length=1,
        description=(
            "Parameter name to value. Whether this replaces the group's whole override set "
            "or merges into it is not documented; send the complete set of overrides the "
            "group should end up with."
        ),
    )


class DeleteConfigurationDto(_Dto):
    """One entry in the JSON **array** sent to `DELETE /v1/configurations/delete`.

    Built by the handler from its `config_ids` argument; it exists so the
    array's element shape is typed like every other request body.
    """

    id: str = Field(..., description="Configuration group to delete")


class CreateBackupStorageDto(_Dto):
    """Body of `POST /v1/payment/backup-storages` (relational).

    Buying backup storage is an order flow: it raises a billable order for a
    recurring quota, not a one-off purchase.
    """

    backupPackageId: str = Field(
        ...,
        min_length=1,
        description=(
            "Package id from list_relational_backup_storage_packages. The catalogue is "
            "repeated per engine group but the repetitions are identical, so the id is "
            "unambiguous -- take it from the deduplicated `packages` list."
        ),
    )


class ResizeBackupStorageDto(_Dto):
    """The `config` half of `POST /v1/backup-storages/actions/resize` (relational).

    The storage id travels as the handler's own argument, so it is absent here
    for the same reason `RestoreRelationalBackupDto` omits `backupId`.
    """

    backupPackageId: str = Field(
        ...,
        min_length=1,
        description=(
            "Target package id from list_relational_backup_storage_packages. Must grant at "
            "least the quota already in use."
        ),
    )


# ---------------------------------------------------------------------------
# MemoryStore (Redis) write DTOs
# ---------------------------------------------------------------------------
#
# These are NOT the relational DTOs with a different name. Read from the raw
# schemas (`CreateMemDbInstanceRequest`, `UpdateMemDbSettingRequest`), the
# memory create body has **no volume fields, no databases and no user** -- the
# flavour decides the disk and Redis has no per-database concept -- and it
# carries a password pair (`redisPasswordEnabled` / `redisPassword`) the
# relational body does not have.


REDIS_PASSWORD_REQUIRED_MESSAGE = (
    "redisPassword is required when redisPasswordEnabled is true. The platform rejects the "
    "combination as a bare 'in_valid', naming no field."
)

PUBLIC_ACCESS_NEEDS_PASSWORD_MESSAGE = (
    "publicAccess requires redisPasswordEnabled to be true -- the spec states it "
    "('Required ENABLED if publicAccess is true') and the platform enforces it with a bare "
    "'in_valid'. An unauthenticated Redis reachable from the internet is not a state to "
    "reach for by accident."
)


class CreateMemoryInstanceDto(_Dto):
    """Body of `POST /v1/payment/database-instances` (memory).

    Note what is absent compared with the relational create: no `volumeType`,
    no `volumeSize`, no `databases`, no `user`. The flavour fixes the disk, and
    Redis authenticates with a single master password rather than a user.

    `packageId` and `locateZoneId` must still agree: flavour ids differ per
    zone, so a `packageId` read without a `zoneId` describes HCM03-1A only.
    """

    name: str = Field(..., description="Instance name")
    datastoreType: str = Field(
        ...,
        description=(
            "Engine type from list_memory_datastores -- 'Redis'. Normalised to the "
            "platform's canonical spelling whatever case is given."
        ),
    )
    datastoreVersion: str = Field(
        ..., description="Redis version from list_memory_datastores, e.g. '7.2'"
    )
    packageId: str = Field(
        ...,
        description=(
            "Flavour id (as a string) from list_memory_flavors for this zone. It decides "
            "RAM, vCPU and disk together -- there is nothing else to size."
        ),
    )
    netIds: list[str] = Field(..., min_length=1, description="Subnet IDs from list_memory_subnets")
    locateZoneId: str = Field(
        ...,
        description=(
            "Availability zone. The memory family has no zones endpoint of its own -- use "
            "list_relational_zones, which enumerates the account's zones."
        ),
    )
    redisPasswordEnabled: bool = Field(
        True,
        description=(
            "Enable the Redis master password. Defaults to enabled: it is mandatory when "
            "publicAccess is on, and a Redis without one accepts any client that can reach it."
        ),
    )
    redisPassword: str | None = Field(
        None, description=f"Redis master password. {MEMORY_PASSWORD_MESSAGE}"
    )
    configId: str | None = Field(None, description="Configuration group ID to attach")
    publicAccess: bool = Field(False, description="Assign a floating IP to the instance")
    backupAuto: bool = Field(False, description="Enable automatic backups")
    backupDuration: int | None = Field(None, ge=2, le=14, description=BACKUP_RETENTION_DESCRIPTION)
    backupTime: str | None = Field(None, description="Automatic backup window start, e.g. '05:30'")
    poc: bool = Field(False, description="Proof-of-concept (trial) instance")

    @field_validator("datastoreType")
    @classmethod
    def _canonical_engine(cls, value: str) -> str:
        return canonical_datastore_type(value)

    @field_validator("redisPassword")
    @classmethod
    def _password_rule(cls, value: str | None) -> str | None:
        return None if value is None else validate_memory_password(value)

    @model_validator(mode="after")
    def _password_consistency(self) -> "CreateMemoryInstanceDto":
        if self.redisPasswordEnabled and not self.redisPassword:
            raise ValueError(REDIS_PASSWORD_REQUIRED_MESSAGE)
        if self.publicAccess and not self.redisPasswordEnabled:
            raise ValueError(PUBLIC_ACCESS_NEEDS_PASSWORD_MESSAGE)
        return self


class ResizeMemoryInstanceDto(_Dto):
    """New flavour for `POST /v1/database-instances/{id}/resize-instance` (memory).

    Resizing the flavour is the **only** sizing operation in this family: there
    is no `resize-storage`, because the disk comes with the flavour.
    """

    packageId: str = Field(
        ..., description="Target flavour id from list_memory_flavors for the instance's zone"
    )
    poc: bool = Field(False, description="Proof-of-concept (trial) pricing")


class CreateMemoryReplicaDto(_Dto):
    """Body of `POST /v1/database-instances/{id}/create-replicas` (memory).

    Unlike the create body this one has **no password fields**: a replica
    inherits the source's authentication.
    """

    name: str = Field(..., description="Replica name")
    packageId: str = Field(..., description="Flavour id for the replica, from its zone")
    datastoreType: str | None = Field(None, description="Engine type; defaults to the source's")
    datastoreVersion: str | None = Field(
        None, description="Engine version; defaults to the source's"
    )
    netIds: list[str] = Field(default_factory=list, description="Subnet IDs for the replica")
    locateZoneId: str | None = Field(None, description="Availability zone for the replica")
    configId: str | None = Field(None, description="Configuration group ID to attach")
    publicAccess: bool = Field(False, description="Assign a floating IP to the replica")
    backupAuto: bool = Field(False, description="Enable automatic backups on the replica")
    backupDuration: int | None = Field(None, ge=2, le=14, description=BACKUP_RETENTION_DESCRIPTION)
    backupTime: str | None = Field(None, description="Backup window start")
    poc: bool = Field(False, description="Proof-of-concept (trial) pricing")

    @field_validator("datastoreType")
    @classmethod
    def _canonical_engine(cls, value: str | None) -> str | None:
        return None if value is None else canonical_datastore_type(value)


class UpdateMemorySettingDto(_Dto):
    """Body of `PUT /v1/database-instances/{id}/update-setting` (memory).

    Two things make this unlike its relational twin.

    **`editRedisPassword` is a flag the caller has to set.** The spec says:
    "Set to 'true' if you make any change of 'redisPasswordEnabled' or
    'redisPassword'". So the password half of the body is gated behind it, and
    sending a new password without the flag risks a silent no-op -- the same
    failure mode as a wrong `action` value. It is validated here rather than
    inferred: deriving it would mean guessing whether re-sending an unchanged
    `redisPasswordEnabled` counts as a change, and guessing wrong could reset
    a password nobody asked to reset.

    **The body is the desired state, not a patch**, as in the relational
    family: send the values that should stay, not only the ones changing.
    """

    editRedisPassword: bool = Field(
        False,
        description=(
            "Set true when changing redisPasswordEnabled or redisPassword. The platform "
            "ignores the password half of this body without it."
        ),
    )
    redisPasswordEnabled: bool | None = Field(
        None, description="Enable or disable the Redis master password"
    )
    redisPassword: str | None = Field(
        None, description=f"New Redis master password. {MEMORY_PASSWORD_MESSAGE}"
    )
    publicAccess: bool | None = Field(None, description="Assign or release a floating IP")
    backupAuto: bool | None = Field(None, description="Enable automatic backups")
    backupDuration: int | None = Field(None, ge=2, le=14, description=BACKUP_RETENTION_DESCRIPTION)
    backupTime: str | None = Field(None, description="Backup window start, e.g. '05:30'")

    @field_validator("redisPassword")
    @classmethod
    def _password_rule(cls, value: str | None) -> str | None:
        return None if value is None else validate_memory_password(value)

    @model_validator(mode="after")
    def _password_consistency(self) -> "UpdateMemorySettingDto":
        touches_password = self.redisPassword is not None or self.redisPasswordEnabled is not None
        if touches_password and not self.editRedisPassword:
            raise ValueError(
                "editRedisPassword must be true to change redisPasswordEnabled or "
                "redisPassword. Without it the platform accepts the request and leaves the "
                "password untouched, reporting nothing."
            )
        if self.redisPasswordEnabled and not self.redisPassword:
            raise ValueError(REDIS_PASSWORD_REQUIRED_MESSAGE)
        if self.publicAccess and self.redisPasswordEnabled is False:
            raise ValueError(PUBLIC_ACCESS_NEEDS_PASSWORD_MESSAGE)
        return self


class DeleteMemoryInstanceDto(_Dto):
    """Deletion options for `POST /v1/database-instances/{id}/delete` (memory).

    Upstream this is the same `DeleteDbInstanceConfig` the relational family
    uses; it is spelled out per family so each tool's schema names the family
    it belongs to.
    """

    createFinalBackup: bool = Field(
        False, description="Take one last backup before the instance is destroyed"
    )
    deleteAllBackup: bool = Field(
        False,
        description=(
            "Also destroy every existing backup of this instance. Irreversible, and it removes "
            "the only way to recover the data."
        ),
    )


class RestoreMemoryBackupDto(_Dto):
    """The `config` object inside `POST /v1/backups/{id}/restore` (memory).

    Restore is the **one** body that differs between the two families.
    Upstream, `RestoreMemBackupConfig` and `RestoreBackupConfig` differ in
    exactly two fields each way: this one has the Redis password pair and no
    `volumeType`/`volumeSize`, because a Redis flavour brings its own disk.

    Like its relational twin this describes the **new instance** the restore
    creates -- it does not overwrite the instance the backup came from -- and
    `backupId` is deliberately absent: the API wants it both in the path and in
    this config, and the handler fills it in from the path argument so the two
    cannot disagree.
    """

    name: str = Field(..., description="Name for the NEW instance the restore creates")
    packageId: str = Field(
        ...,
        description=(
            "Flavour id from list_memory_flavors for the target zone. It sets RAM, vCPU and "
            "disk together -- there is nothing else to size, and no volume fields here."
        ),
    )
    datastoreType: str = Field(..., description="Engine, normally the backup's own datastore_type")
    datastoreVersion: str = Field(..., description="Engine version, normally the backup's own")
    netIds: list[str] = Field(..., min_length=1, description="Subnet IDs from list_memory_subnets")
    locateZoneId: str = Field(
        ...,
        description=(
            "Availability zone. The memory family has no zones endpoint of its own -- use "
            "list_relational_zones, which enumerates the account's zones."
        ),
    )
    redisPasswordEnabled: bool = Field(
        True,
        description=(
            "Enable the Redis master password on the new instance. Defaults to enabled: it "
            "is mandatory when publicAccess is on, and a Redis without one accepts any "
            "client that can reach it."
        ),
    )
    redisPassword: str | None = Field(
        None,
        description=(
            f"Master password for the NEW instance -- not the source's. {MEMORY_PASSWORD_MESSAGE}"
        ),
    )
    configId: str | None = Field(None, description="Configuration group ID to attach")
    publicAccess: bool = Field(False, description="Assign a floating IP to the new instance")
    backupAuto: bool = Field(False, description="Enable automatic backups on the new instance")
    backupDuration: int | None = Field(None, ge=2, le=14, description=BACKUP_RETENTION_DESCRIPTION)
    backupTime: str | None = Field(None, description="Automatic backup window start, e.g. '05:30'")
    poc: bool = Field(False, description="Proof-of-concept (trial) pricing")

    @field_validator("datastoreType")
    @classmethod
    def _canonical_engine(cls, value: str) -> str:
        return canonical_datastore_type(value)

    @field_validator("redisPassword")
    @classmethod
    def _password_rule(cls, value: str | None) -> str | None:
        return None if value is None else validate_memory_password(value)

    @model_validator(mode="after")
    def _password_consistency(self) -> "RestoreMemoryBackupDto":
        if self.redisPasswordEnabled and not self.redisPassword:
            raise ValueError(REDIS_PASSWORD_REQUIRED_MESSAGE)
        if self.publicAccess and not self.redisPasswordEnabled:
            raise ValueError(PUBLIC_ACCESS_NEEDS_PASSWORD_MESSAGE)
        return self


class CreateMemoryConfigurationDto(_Dto):
    """Body of `POST /v1/configurations/create` (memory).

    🐛 **`deployType` is required and is NOT IN THE SCHEMA AT ALL.**
    `CreateMemConfigGroupRequest` declares four fields -- name, description,
    datastoreType, datastoreVersion -- and a body containing exactly those
    four is rejected with `400 bad_request`. Adding `deployType:
    "single_node"`, a field the schema never mentions, makes it succeed.
    Verified by A/B live on 2026-09-10, with and without `description`.

    This is worse than the relational create, where the field at least exists
    and is merely mis-documented as optional (see
    `CreateRelationalConfigurationDto`). Here the schema cannot produce a
    working request at all -- which is why a DTO built faithfully from it had
    to be corrected after the first live call.

    **`datastoreType` is case-sensitive on this endpoint**, unlike the flavour
    and parameter catalogues: `"redis"` is rejected as `bad_request` where
    `"Redis"` succeeds. The validator normalises it, so a caller cannot get
    this wrong through the tool.

    `description`, by contrast, really is optional here -- unlike the backup
    `description`, which the spec also calls optional and which fails
    asynchronously when omitted.

    A group is bound to one engine and version for life: the create fixes
    `datastoreType`/`datastoreVersion` and no update can change them. Attaching
    it to an instance running a different version is not possible, so get the
    pair right here.
    """

    name: str = Field(..., min_length=1, description="Configuration group name")
    datastoreVersion: str = Field(
        ..., min_length=1, description="Redis version from list_memory_datastores, e.g. '7.2'"
    )
    datastoreType: str = Field(
        "Redis",
        description=(
            "Engine the group applies to. The spec names Redis as the only allowed value, "
            "which is why it is defaulted; normalised to the platform's canonical spelling "
            "because this endpoint rejects a lowercase 'redis'."
        ),
    )
    deployType: Literal["single_node", "cluster"] = Field(
        DEPLOY_TYPE_SINGLE_NODE,
        description=(
            "Required, even though this request's schema does not declare the field: "
            "omitting it returns 400 bad_request. Only 'single_node' has been verified for "
            "Redis -- leave the default alone unless the platform grows a clustered Redis."
        ),
    )
    description: str | None = Field(None, description="Configuration group description")

    @field_validator("datastoreType")
    @classmethod
    def _canonical_engine(cls, value: str) -> str:
        return canonical_datastore_type(value)


# ----------------------------------------------------------------------
# PostgreSQL Cluster
# ----------------------------------------------------------------------

POSTGRESQL_NODES_MIN = 2
POSTGRESQL_NODES_MAX = 10
"""Node count bounds, stated only in the field's ``description`` in the spec.

A cluster is at least two nodes by definition -- one node is a relational
`db-` instance, ordered through a different endpoint entirely.
"""

RESIZE_VOLUME_SIZE = "VOLUME-SIZE"
RESIZE_VOLUME_TYPE = "VOLUME-TYPE"
RESIZE_NUMBER_OF_NODES = "NUMBER-OF-NODES"
"""The three values `type` accepts on a cluster resize.

Upper case, hyphenated, and **not** derivable from anything else in the API:
the relational family's resize takes no `type` at all, and every other
enumerated value in vDB is lower case with underscores (`single_node`,
`detach_replica`). They appear only in the field's ``description`` -- there is
no ``enum`` in the schema -- so a generated client loses them.
"""


class PostgresqlDatabaseDto(_Dto):
    """One database to create inside a new cluster.

    Unlike the relational family's version this takes **only** a name: the
    cluster create's `DatabaseRequest` declares no character set and no
    collation.
    """

    name: str = Field(..., min_length=1, description="Database name")


class PostgresqlUserDto(_Dto):
    """The admin user created with a new cluster."""

    name: str = Field(..., min_length=1, description="Master username")
    password: str = Field(..., description=f"Master password. {RELATIONAL_PASSWORD_MESSAGE}")

    @field_validator("password")
    @classmethod
    def _password_rule(cls, value: str) -> str:
        # Checked against the relational rule (8-32). This family is
        # PostgreSQL and its spec example is a 12-character relational-style
        # password, but the rule itself is undocumented and has not been
        # measured here -- so a rejection from the API is still possible where
        # this passes.
        return validate_relational_password(value)


class CreatePostgresqlClusterDto(_Dto):
    """Body of `POST /v1/cluster` (PostgreSQL Cluster).

    Zone consistency applies exactly as in the relational family:
    `packageId`, `volumeTypeId` and `locateZoneId` must all come from the same
    zone. Both catalogues are zone-filtered, and a bare call returns HCM03-1A
    only -- measured: `/cluster/flavors` returns 28 rows for each zone, with
    **different ids** in each.

    Three fields name resources this API does not own: `backupLocationId`,
    `backupPolicyId` and `backupPointId` are vBackup ids, listed by
    `list_postgresql_backup_locations` and `list_postgresql_backup_policies`.
    `backupPointId` is what turns a create into a restore.
    """

    name: str = Field(..., min_length=1, description="Cluster name")
    locateZoneId: str = Field(..., description="Availability zone from list_relational_zones")
    packageId: str = Field(
        ..., description="Flavour id ('pgp-...') from list_postgresql_flavors for this zone"
    )
    volumeTypeId: str = Field(
        ...,
        description=(
            "Volume type id ('pgst-...') from list_postgresql_volume_types for this zone. "
            "Note this is the id, where the relational create takes the volume type NAME."
        ),
    )
    volumeSize: int = Field(
        ..., ge=20, description="Storage size in GB; the catalogue's minimum is 20"
    )
    numberOfNodes: int = Field(
        ...,
        ge=POSTGRESQL_NODES_MIN,
        le=POSTGRESQL_NODES_MAX,
        description="Nodes in the cluster; 2-10. One node is a relational instance, not a cluster.",
    )
    datastoreVersion: str = Field(
        ..., min_length=1, description="PostgreSQL version from list_postgresql_datastores"
    )
    netIds: list[str] = Field(
        ..., min_length=1, description="Subnet IDs ('sub-...') from list_relational_subnets"
    )
    user: PostgresqlUserDto | None = Field(None, description="Master user to create")
    databases: list[PostgresqlDatabaseDto] = Field(
        default_factory=list,
        description="Databases to create. The spec states only one is supported at creation.",
    )
    configId: str | None = Field(
        None,
        description=(
            "Configuration group ID to attach. Must be a group with deployType 'cluster' -- "
            "its id is prefixed 'pg-cfg-'."
        ),
    )
    publicAccess: bool = Field(False, description="Expose the cluster publicly")
    backupLocationId: str | None = Field(
        None, description="vBackup destination from list_postgresql_backup_locations"
    )
    backupPolicyId: str | None = Field(
        None, description="vBackup policy from list_postgresql_backup_policies"
    )
    backupPointId: str | None = Field(
        None,
        description=(
            "Restore point from list_postgresql_restore_points. Set it to build the new "
            "cluster FROM a backup; leave it empty for an empty cluster."
        ),
    )
    isPoc: bool = Field(False, description="Pay with PoC credit (Auto Payment only)")


class ResizePostgresqlClusterDto(_Dto):
    """Body of `PUT /v1/cluster/{clusterId}/resize`.

    One axis per call. `type` selects which, and the field belonging to the
    other two axes must be left out -- the validator enforces that here rather
    than letting the API decide, because a body carrying the wrong pair is the
    kind of mistake this API answers with a bare rejection.
    """

    type: Literal["VOLUME-SIZE", "VOLUME-TYPE", "NUMBER-OF-NODES"] = Field(
        ..., description="Which axis to resize. Upper case and hyphenated, exactly as shown."
    )
    volumeSize: int | None = Field(
        None, ge=20, description="New storage size in GB. Use with type VOLUME-SIZE."
    )
    volumeTypeId: int | str | None = Field(
        None, description="New volume type id ('pgst-...'). Use with type VOLUME-TYPE."
    )
    numberOfNodes: int | None = Field(
        None,
        ge=POSTGRESQL_NODES_MIN,
        le=POSTGRESQL_NODES_MAX,
        description="New node count, 2-10. Use with type NUMBER-OF-NODES.",
    )
    isPoc: bool = Field(False, description="Pay with PoC credit (Auto Payment only)")

    @model_validator(mode="after")
    def _one_axis(self) -> "ResizePostgresqlClusterDto":
        required = {
            RESIZE_VOLUME_SIZE: "volumeSize",
            RESIZE_VOLUME_TYPE: "volumeTypeId",
            RESIZE_NUMBER_OF_NODES: "numberOfNodes",
        }
        wanted = required[self.type]
        if getattr(self, wanted) is None:
            raise ValueError(f"type '{self.type}' requires {wanted}")
        extra = [
            name
            for axis, name in required.items()
            if axis != self.type and getattr(self, name) is not None
        ]
        if extra:
            raise ValueError(
                f"type '{self.type}' resizes one axis; remove {', '.join(sorted(extra))}"
            )
        return self


class UpdatePostgresqlClusterSettingDto(_Dto):
    """Body of `PUT /v1/cluster/{clusterId}/settings`.

    Both fields are optional in the schema, which would make an empty body
    legal; it is rejected here instead, since a write that changes nothing is
    never what the caller meant.

    Note this endpoint carries no `resType` and no action envelope -- unlike
    every relational lifecycle call. It is a plain body.
    """

    password: str | None = Field(
        None, description=f"New master password. {RELATIONAL_PASSWORD_MESSAGE}"
    )
    publicAccess: bool | None = Field(None, description="Whether to allow public access")

    @field_validator("password")
    @classmethod
    def _password_rule(cls, value: str | None) -> str | None:
        return None if value is None else validate_relational_password(value)

    @model_validator(mode="after")
    def _not_empty(self) -> "UpdatePostgresqlClusterSettingDto":
        if self.password is None and self.publicAccess is None:
            raise ValueError("set password, publicAccess, or both -- an empty update does nothing")
        return self


# ----------------------------------------------------------------------
# Kafka
# ----------------------------------------------------------------------

KAFKA_BROKERS_MIN = 3
KAFKA_BROKERS_MAX = 10
"""Broker bounds, published live by ``GET /vdb-kafka/database/configs``.

Note the floor is **3**, not the 2 a PostgreSQL Cluster allows -- Kafka needs a
quorum. The spec's ``description`` says the same, and the live catalogue is the
authority; `get_kafka_limits` returns it so an agent can check rather than
trust these constants.
"""

KAFKA_STORAGE_MIN_GB = 20
KAFKA_STORAGE_MAX_GB = 5000

KAFKA_TOPIC_PARTITIONS_MIN = 1
KAFKA_TOPIC_PARTITIONS_MAX = 2048
KAFKA_TOPIC_RETENTION_SECONDS_MIN = 3600
KAFKA_TOPIC_RETENTION_SECONDS_MAX = 7776000
KAFKA_TOPIC_RETENTION_BYTES_MAX = 1099511627776
KAFKA_RETENTION_BYTES_UNLIMITED = -1
"""What `retentionBytes` takes to mean "no size limit". Not zero, not omitted."""

KAFKA_SECURITY_RULE_PORTS = (9092, 9094, 9096, 9194, 9196)
"""The only ports a Kafka security-group rule may open.

Published live in the configs catalogue as a JSON array inside a string. Any
other port is rejected, so the DTO closes the set rather than letting a caller
discover it through an error.
"""

KAFKA_CLUSTER_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9-]{3,48}[a-zA-Z0-9]$")
KAFKA_TOPIC_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9\-_.]{3,247}[a-zA-Z0-9]$")
KAFKA_CONFIG_GROUP_NAME_PATTERN = re.compile(r"^[a-zA-Z][a-zA-Z0-9\-_ ]{3,48}[a-zA-Z0-9]$")
KAFKA_COMMON_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9\-_]{3,48}[a-zA-Z0-9]$")
"""Name rules, taken verbatim from the platform's own published regexes.

Kafka is the one family that publishes these. They are anchored here because
the API applies them unanchored-looking but in full, and each implies a length
range the caller would otherwise have to infer: 5-50 characters for a cluster,
5-249 for a topic, 5-50 for a configuration group and for a user.
"""


def _check_name(value: str, pattern: re.Pattern[str], what: str, rule: str) -> str:
    if not pattern.fullmatch(value or ""):
        raise ValueError(f"{what} must match {pattern.pattern} ({rule})")
    return value


class CreateKafkaClusterDto(_Dto):
    """Body of `POST /vdb-kafka/clusters`.

    Two id fields name things the catalogue reports under **three** names
    each, and the obvious one is the wrong one:

    * `serverFlavorId` takes a flavour's `flavorId` (``flav-…``), not its
      integer `id`.
    * `kafkaStorageType` takes a volume type's `kafkaUuid` (``vtype-…``), not
      its integer `id` and not its `type` name.

    `configGroupVersionId` takes a **version** (``cgroupver-…``), never a group
    (``cgroup-…``): a cluster is attached to one version of a group.

    🐛 **``tags`` must be present or the whole request fails with a bare
    ``500``.** Kafka was designed to let users tag a cluster with a
    ``Map<String, String>``; the feature was dropped, but the backend still
    calls ``getTags().size()`` while validating, so a body without the field
    dereferences null and the gateway answers an empty Spring error page --
    no field named, no message. The Portal sends ``tags: {}`` on every create,
    which is why it works there and a faithful reading of the schema does not.

    That is why the field defaults to an empty map rather than to ``None``:
    ``exclude_none`` would drop a ``None`` and reintroduce the crash.
    """

    name: str = Field(..., description="Cluster name; 5-50 characters, letters, digits and '-'")
    tags: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Cluster tags. Kafka no longer supports tagging, so this is always an empty map "
            "-- but it MUST be sent: see the class docstring."
        ),
    )
    kafkaVersion: str = Field(
        ..., min_length=1, description="Kafka version from get_kafka_limits, e.g. '3.7.0'"
    )
    serverFlavorId: str = Field(
        ..., description="Broker flavour 'flav-…' id from list_kafka_flavors"
    )
    kafkaBrokerCount: int = Field(
        ...,
        ge=KAFKA_BROKERS_MIN,
        le=KAFKA_BROKERS_MAX,
        description="Brokers; 3-10. Kafka needs a quorum, so 3 is the floor, not 1 or 2.",
    )
    kafkaStorageType: str = Field(
        ..., description="Storage type 'vtype-…' id from list_kafka_volume_types"
    )
    kafkaStorageSize: int = Field(
        ...,
        ge=KAFKA_STORAGE_MIN_GB,
        le=KAFKA_STORAGE_MAX_GB,
        description="Storage per broker in GB; 20-5000. Billed per broker.",
    )
    vserverProjectId: str = Field(
        ...,
        description=(
            "vServer project id. The handler fills this from the configured profile, so a "
            "caller normally leaves it out."
        ),
    )
    networkId: str = Field(..., description="Network 'net-…' id")
    subnetId: str = Field(..., description="Subnet 'sub-…' id inside that network")
    mtlsAuthen: bool = Field(False, description="Enable mTLS authentication")
    saslAuthen: bool = Field(False, description="Enable SASL authentication")
    configGroupVersionId: str | None = Field(
        None, description="Configuration group VERSION 'cgroupver-…' id to apply"
    )
    encryptionVolume: bool = Field(False, description="Encrypt the brokers' volumes")

    @field_validator("name")
    @classmethod
    def _name_rule(cls, value: str) -> str:
        return _check_name(
            value, KAFKA_CLUSTER_NAME_PATTERN, "cluster name", "5-50 chars, letters, digits, '-'"
        )


class CreateKafkaTopicDto(_Dto):
    """Body of `POST /vdb-kafka/clusters/{clusterId}/topics`."""

    name: str = Field(..., description="Topic name; 5-249 characters, letters, digits, '-_.'")
    partitions: int | None = Field(
        None,
        ge=KAFKA_TOPIC_PARTITIONS_MIN,
        le=KAFKA_TOPIC_PARTITIONS_MAX,
        description="Partition count, 1-2048. Omit to take the version's default.",
    )
    replicas: int | None = Field(
        None,
        ge=1,
        description=(
            "Replication factor. At least 1 and **never more than the cluster's broker "
            "count** -- that ceiling is not published in get_kafka_limits, so read "
            "broker_count from get_kafka_cluster."
        ),
    )
    retentionSeconds: int | None = Field(
        None,
        ge=KAFKA_TOPIC_RETENTION_SECONDS_MIN,
        le=KAFKA_TOPIC_RETENTION_SECONDS_MAX,
        description="Retention time in seconds; 3600 (1 hour) to 7776000 (90 days).",
    )
    retentionBytes: int | None = Field(
        None,
        description=(
            "Retention size in bytes, 1 to 1099511627776, or exactly -1 for unlimited. "
            "Omit to let Kafka apply its default."
        ),
    )

    @field_validator("name")
    @classmethod
    def _name_rule(cls, value: str) -> str:
        return _check_name(
            value, KAFKA_TOPIC_NAME_PATTERN, "topic name", "5-249 chars, letters, digits, '-_.'"
        )

    @field_validator("retentionBytes")
    @classmethod
    def _retention_bytes_rule(cls, value: int | None) -> int | None:
        if value is None or value == KAFKA_RETENTION_BYTES_UNLIMITED:
            return value
        if not 1 <= value <= KAFKA_TOPIC_RETENTION_BYTES_MAX:
            raise ValueError(
                f"retentionBytes must be between 1 and {KAFKA_TOPIC_RETENTION_BYTES_MAX}, "
                f"or exactly {KAFKA_RETENTION_BYTES_UNLIMITED} for unlimited"
            )
        return value


class UpdateKafkaTopicDto(_Dto):
    """Body of `PUT /vdb-kafka/clusters/{clusterId}/topics/{topicId}`.

    **This is a complete replacement, not a patch**, which is why `partitions`
    and `replicas` are required rather than optional as the schema has them.
    Measured 2026-09-14, each omission produces a different misleading error:

    * without `replicas` -> ``400 Can't update replicas for topic`` -- as if
      the caller had asked to change something they never mentioned;
    * without `partitions` -> ``400 Partition count needs to be between 1 and
      2048`` -- a range complaint about a value that was not sent.

    Neither names "you left out a field", so both read as a rejection of what
    *was* sent. Requiring them here turns that into a schema error that says
    what to do: read the topic with `get_kafka_topic` and send its current
    values back for anything you do not mean to change.

    The name cannot be changed. Partitions can only grow -- Kafka itself does
    not support reducing a topic's partition count.
    """

    partitions: int = Field(
        ...,
        ge=KAFKA_TOPIC_PARTITIONS_MIN,
        le=KAFKA_TOPIC_PARTITIONS_MAX,
        description=(
            "Partition count, 1-2048. Required even when unchanged -- send the topic's "
            "current value. Kafka cannot reduce partitions."
        ),
    )
    replicas: int = Field(
        ...,
        ge=1,
        description=(
            "Replication factor, never above the cluster's broker count. Required even when "
            "unchanged -- send the topic's current value."
        ),
    )
    retentionSeconds: int | None = Field(
        None,
        ge=KAFKA_TOPIC_RETENTION_SECONDS_MIN,
        le=KAFKA_TOPIC_RETENTION_SECONDS_MAX,
        description="New retention time in seconds; 3600 to 7776000",
    )
    retentionBytes: int | None = Field(
        None, description="New retention size in bytes, or -1 for unlimited"
    )

    @field_validator("retentionBytes")
    @classmethod
    def _retention_bytes_rule(cls, value: int | None) -> int | None:
        return CreateKafkaTopicDto._retention_bytes_rule(value)


class _KafkaPermissionsDto(_Dto):
    """The permission block shared by creating and updating a user.

    Four permission kinds, each a list of topic names **or** an `*All` flag.
    The platform ignores the list when the flag is set, so sending both is not
    a refinement -- it is a grant of everything with a misleading list beside
    it. The validator refuses that combination rather than letting it through
    to look correct in a later read.
    """

    produceTopicNames: list[str] = Field(
        default_factory=list, description="Topics this user may produce to"
    )
    produceAll: bool = Field(False, description="Produce to every topic; ignores the list")
    consumeTopicNames: list[str] = Field(
        default_factory=list, description="Topics this user may consume from"
    )
    consumeAll: bool = Field(False, description="Consume from every topic; ignores the list")
    produceConsumeTopicNames: list[str] = Field(
        default_factory=list, description="Topics this user may both produce to and consume from"
    )
    produceConsumeAll: bool = Field(
        False, description="Produce and consume on every topic; ignores the list"
    )
    adminTopicNames: list[str] = Field(
        default_factory=list, description="Topics this user administers"
    )
    adminAll: bool = Field(False, description="Administer every topic; ignores the list")
    mtlsAuthen: bool = Field(False, description="Issue mTLS credentials for this user")
    saslAuthen: bool = Field(False, description="Issue SASL credentials for this user")

    @model_validator(mode="after")
    def _flag_and_list_conflict(self) -> "_KafkaPermissionsDto":
        for names, flag in (
            ("produceTopicNames", "produceAll"),
            ("consumeTopicNames", "consumeAll"),
            ("produceConsumeTopicNames", "produceConsumeAll"),
            ("adminTopicNames", "adminAll"),
        ):
            if getattr(self, flag) and getattr(self, names):
                raise ValueError(
                    f"{flag} is true, so {names} would be ignored by the platform -- send one "
                    "or the other, so the stored permission matches what was asked for"
                )
        return self


class CreateKafkaUserDto(_KafkaPermissionsDto):
    """Body of `POST /vdb-kafka/clusters/{clusterId}/users`.

    A user's authentication must match the cluster's: enabling `saslAuthen`
    here for a cluster that has SASL turned off leaves the user unable to
    connect.
    """

    name: str = Field(..., description="User name; 5-50 characters, letters, digits, '-_'")

    @field_validator("name")
    @classmethod
    def _name_rule(cls, value: str) -> str:
        return _check_name(
            value, KAFKA_COMMON_NAME_PATTERN, "user name", "5-50 chars, letters, digits, '-_'"
        )


class UpdateKafkaUserDto(_KafkaPermissionsDto):
    """Body of `PUT /vdb-kafka/clusters/{clusterId}/users/{userId}`.

    Permissions are **replaced**, not merged: a field left at its default is
    sent as empty or false, so read the user first and send back everything
    that should survive.
    """


class KafkaConfigPropertyDto(_Dto):
    """One broker setting inside a configuration group version."""

    key: str = Field(
        ..., min_length=1, description="Setting name, e.g. 'auto.create.topics.enable'"
    )
    value: str = Field(..., description="Setting value, always as a string")


class CreateKafkaConfigGroupDto(_Dto):
    """Body of `POST /vdb-kafka/config-groups`.

    Creating a group creates its **version 1** from `properties`; there is no
    way to edit a version afterwards. Changing a setting means
    `create_kafka_config_group_version`, then re-attaching the cluster to the
    new version.
    """

    name: str = Field(
        ...,
        description=(
            "Group name; 5-50 characters, starting with a letter. Spaces are allowed here "
            "and nowhere else in this family."
        ),
    )
    description: str | None = Field(None, description="Group description")
    properties: list[KafkaConfigPropertyDto] = Field(
        default_factory=list, description="Broker settings for version 1 of the group"
    )

    @field_validator("name")
    @classmethod
    def _name_rule(cls, value: str) -> str:
        return _check_name(
            value,
            KAFKA_CONFIG_GROUP_NAME_PATTERN,
            "config group name",
            "5-50 chars, starts with a letter, letters/digits/'-_' and spaces",
        )


class CreateKafkaConfigGroupVersionDto(_Dto):
    """Body of `POST /vdb-kafka/config-groups/{configGroupId}/versions`.

    A version is a complete set, not a patch: send every setting the version
    should carry, because nothing is inherited from the version before it.
    """

    properties: list[KafkaConfigPropertyDto] = Field(
        ..., min_length=1, description="The complete set of broker settings for this version"
    )


class CreateKafkaSecurityRuleDto(_Dto):
    """Body of `POST /vdb-kafka/clusters/{clusterId}/security-group-rules`.

    Unlike the other families, rules are added and removed **one at a time**;
    there is no replace-the-whole-set endpoint here, so adding a rule never
    silently drops another.
    """

    remoteIp: str = Field(
        ..., min_length=1, description="CIDR allowed to connect, e.g. '10.0.0.0/8'"
    )
    port: Literal[9092, 9094, 9096, 9194, 9196] = Field(
        ...,
        description=(
            "Port to open. The platform publishes these five as the only allowed values; "
            "confirm against get_kafka_limits if a cluster rejects one."
        ),
    )
