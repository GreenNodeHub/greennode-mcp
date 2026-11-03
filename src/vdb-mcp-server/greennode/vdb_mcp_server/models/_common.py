"""Response pieces shared by every vDB family.

What lives here rather than in a family module is the set of models whose
**upstream schema is identical in every family** -- verified against the spec,
not assumed: `SecurityGroupRuleEntity`, `HistoryResponse`,
`ActionDbInstancesResponse` and `OrderResponse` are one schema each, referenced
by both the relational and the memory paths. A projection of them cannot
disagree between families, so duplicating it per family would only create two
places to fix one bug.

Everything that describes an *instance*, a *backup* or a *configuration* stays
in its family module: those genuinely differ, which is the whole reason the
models are split by family at all.
"""

from __future__ import annotations

from greennode.vdb_mcp_server.paging import Page
from greennode.vdb_mcp_server.statuses import StatusKind, classify, describe
from pydantic import BaseModel, Field
from typing import Any


class PageInfo(BaseModel):
    """Paging metadata as vDB reports it.

    ``page`` is 1-based both in the request and in this echo of it. Do not use
    it as a zero-based offset when asking for the next page.
    """

    page: int | None = Field(None, description="Page number returned (1-based)")
    page_size: int | None = Field(None, description="Items per page")
    total_pages: int | None = Field(None, description="Number of pages available")
    total_items: int | None = Field(None, description="Number of items across all pages")
    max_page_size: int | None = Field(None, description="Largest page size the API accepts")

    @classmethod
    def from_page(cls, page: Page) -> "PageInfo":
        """Build a PageInfo from a `paging.Page`."""
        return cls(
            page=page.page_number,
            page_size=page.page_size,
            total_pages=page.total_pages,
            total_items=page.total_elements,
            max_page_size=page.max_size,
        )


class SecurityRule(BaseModel):
    """One security-group rule guarding an instance."""

    id: str = Field("", description="Rule ID")
    direction: str = Field("", description="'ingress' or 'egress'")
    ether_type: str = Field("", description="IPv4 or IPv6")
    protocol: str = Field("", description="Protocol")
    port_range_min: int | None = Field(None, description="Lowest port in range")
    port_range_max: int | None = Field(None, description="Highest port in range")
    remote_ip_prefix: str = Field("", description="Allowed CIDR")
    remote_group_id: str = Field("", description="Allowed remote security group ID")
    remote_group_name: str = Field("", description="Allowed remote security group name")
    status: str = Field("", description="Rule status")
    description: str = Field("", description="Rule description")
    created_at: str = Field("", description="Creation timestamp")

    @classmethod
    def from_api(cls, data: dict) -> "SecurityRule":
        """Build from a raw `SecurityGroupRuleEntity` row."""
        return cls(
            id=data.get("id") or "",
            direction=data.get("direction") or "",
            ether_type=data.get("etherType") or "",
            protocol=data.get("protocol") or "",
            port_range_min=data.get("portRangeMin"),
            port_range_max=data.get("portRangeMax"),
            remote_ip_prefix=data.get("remoteIpPrefix") or "",
            remote_group_id=data.get("remoteGroupId") or "",
            remote_group_name=data.get("remoteGroupName") or "",
            status=data.get("status") or "",
            description=data.get("description") or "",
            created_at=data.get("createdAt") or "",
        )


class SecurityRuleListData(BaseModel):
    """The security rules on one instance."""

    instance_id: str = Field("", description="Instance the rules belong to")
    count: int = Field(0, description="Number of rules")
    items: list[SecurityRule] = Field(default_factory=list, description="Rules")


class HistoryEntry(BaseModel):
    """One entry in an instance's operation history."""

    id: str = Field("", description="History entry ID")
    instance_id: str = Field("", description="Instance the entry belongs to")
    action: str = Field("", description="Action performed")
    status: str = Field("", description="Outcome status")
    description: str = Field("", description="Human-readable description")
    error_message: str = Field("", description="Error detail when the action failed")
    created_time: str = Field("", description="When the action started")
    updated_time: str = Field("", description="When the action last changed state")

    @classmethod
    def from_api(cls, data: dict) -> "HistoryEntry":
        """Build from a raw `HistoryResponse` row."""
        return cls(
            id=str(data.get("id") or ""),
            instance_id=data.get("instanceId") or "",
            action=data.get("action") or "",
            status=data.get("status") or "",
            description=data.get("description") or "",
            error_message=data.get("errorMessage") or "",
            created_time=data.get("createdTime") or "",
            updated_time=data.get("updatedTime") or "",
        )


class HistoryListData(BaseModel):
    """A page of instance history."""

    instance_id: str = Field("", description="Instance the history belongs to")
    count: int = Field(0, description="Entries on this page")
    page: PageInfo | None = Field(None, description="Paging metadata")
    items: list[HistoryEntry] = Field(default_factory=list, description="History entries")


class OrderResult(BaseModel):
    """What an order-flow endpoint answers with.

    Note what is **not** here: live, `orderId` and `resourceId` come back
    `null` and only `orderUrl` is populated, so a create cannot tell you the id
    of the instance it just ordered. Find it by listing instances by name.
    """

    order_url: str = Field("", description="Payment console URL for the order")
    order_id: str = Field("", description="Order ID -- usually empty in practice")
    resource_id: str = Field("", description="Resource ID -- usually empty in practice")

    @classmethod
    def from_api(cls, data: dict) -> "OrderResult":
        """Build from a raw `OrderResponse` row."""
        return cls(
            order_url=data.get("orderUrl") or "",
            order_id=data.get("orderId") or "",
            resource_id=data.get("resourceId") or "",
        )


class OrderData(BaseModel):
    """The result of a billable operation, plus what the caller must do next."""

    orders: list[OrderResult] = Field(default_factory=list, description="Orders raised")
    instance_name: str | None = Field(
        None, description="Name that was ordered, for finding the resource once it exists"
    )
    next_step: str = Field(
        "",
        description="What has to happen before the resource exists",
    )


class SettingUpdateData(BaseModel):
    """The result of an instance settings or configuration-group update.

    Success is the **HTTP status**: `BaseClient` raises on anything outside 2xx,
    so reaching this model means the platform accepted the change. The response
    body is deliberately not modelled -- it echoes an inner `status` plus two id
    fields holding each other's values, and the GreenNode team confirmed only
    the HTTP status is meant to be relied on.
    """

    instance_id: str = Field("", description="Instance the change was applied to")
    applied: bool = Field(
        True, description="True whenever the API accepted the request (HTTP 2xx)"
    )
    next_step: str = Field(
        "",
        description="The change is asynchronous; how to confirm it actually took effect",
    )


class ActionResult(BaseModel):
    """The result of a lifecycle action (start, stop, reboot, delete, detach)."""

    instance_ids: str = Field("", description="Instances the action applied to")
    action: str = Field("", description="Action performed")
    status: str = Field("", description="Reported status")
    success: bool | None = Field(None, description="Whether the API accepted the action")
    error_message: str = Field("", description="Error detail when it did not")
    code: int | None = Field(None, description="Result code")

    @classmethod
    def from_api(cls, data: dict) -> "ActionResult":
        """Build from a raw `ActionDbInstancesResponse` row."""
        return cls(
            instance_ids=str(data.get("databaseInstances") or ""),
            action=data.get("action") or "",
            status=data.get("status") or "",
            success=data.get("success"),
            error_message=data.get("errorMsg") or "",
            code=data.get("code"),
        )


class ActionData(BaseModel):
    """The results of a lifecycle action, with the asynchrony spelled out."""

    instance_id: str = Field("", description="Instance the action was sent for")
    accepted: bool = Field(
        True,
        description=(
            "False when the API answered 200 but reported no per-instance result at all, "
            "which in practice means the action was silently ignored rather than queued."
        ),
    )
    results: list[ActionResult] = Field(default_factory=list, description="Per-instance results")
    warning: str | None = Field(
        None, description="Set when the response cannot be read as success"
    )
    next_step: str = Field(
        "",
        description="Actions are asynchronous; how to confirm the instance actually changed",
    )


class DryRunData(BaseModel):
    """What a billable or destructive call *would* send, without sending it."""

    tool: str = Field("", description="Tool this preview is for")
    method: str = Field("", description="HTTP method that would be used")
    path: str = Field("", description="API path that would be called")
    service: str = Field("", description="vDB family the call would go to")
    user_type: str | None = Field(None, description="Billing flow header, when one applies")
    body: dict | list | None = Field(None, description="Exact request body that would be sent")
    warnings: list[str] = Field(
        default_factory=list, description="Consequences the caller should confirm first"
    )


class DatabaseBackup(BaseModel):
    """One database backup -- a projection of a 34-field row.

    Upstream this is `BackupInfo`, and the relational and the memory paths
    reference the **same** schema, so one projection serves both families.
    Verified live in each: a Redis backup and a MySQL backup differ only in
    the values they carry.

    Most of what is kept describes the instance the backup came from, because
    restoring builds a *new* instance that has to be specified all over again:
    `storage_size_gb`, `storage_type`, `package_id`, the datastore pair.
    Dropped are the fields that only describe billing or sharing (`priceKey`,
    `engineGroup`, `sharedBy`, `sharedActions`) and the internal `backendId`.

    **The listing and the detail endpoint disagree about the same backup.**
    Measured on one row: `GET /backups` returns `storageType`, `storageSize`,
    `ram`, `vcpu`, `packageId`, `username`, `configId`, `netIds` and
    `backup_duration_days` as `null`, while `GET /backups/detail/{id}` fills
    all of them in -- and reports `datastoreType` as `MySQL` where the listing
    says `mysql`. So a list row is an index, not a description: read the detail
    before planning a restore from one.
    """

    id: str = Field("", description="Backup ID (starts with 'bk-')")
    name: str = Field("", description="Backup name")
    description: str = Field("", description="Backup description")
    instance_id: str = Field("", description="Instance this backup was taken from")
    instance_name: str = Field("", description="Name of that instance")
    backup_type: str = Field("", description="FULL or INCREMENTAL")
    origin: str = Field(
        "",
        description=(
            "MANUAL for a backup someone asked for, AUTO_DAILY for one the instance's "
            "automatic schedule took. Upstream this field is called `type`."
        ),
    )
    tier: str = Field("", description="Which allowance this backup counts against, e.g. FREE")
    parent_id: str = Field(
        "", description="Parent backup ID; set on an INCREMENTAL backup, empty on a FULL one"
    )
    parent_name: str = Field("", description="Parent backup name")
    status: str = Field("", description="Backup status, verbatim from the API")
    status_kind: StatusKind = Field(
        "unknown",
        description=(
            "What the status means to act on: settled (COMPLETED), transitional "
            "(NEW/BUILDING/SAVING -- poll again), failed (ERROR/FAILED, a platform fault to "
            "report), or unknown."
        ),
    )
    status_guidance: str = Field(
        "", description="What to do about this status, derived from status_kind"
    )
    size_gb: float | None = Field(None, description="Size the backup actually occupies")
    storage_size_gb: int | None = Field(
        None, description="Volume size of the source instance, in GB"
    )
    storage_type: str = Field(
        "", description="Volume type of the source instance (zone-suffixed outside HCM03-1A)"
    )
    datastore_type: str = Field("", description="Engine of the source instance")
    datastore_version: str = Field("", description="Engine version of the source instance")
    package_id: str = Field("", description="Flavour id of the source instance")
    vcpus: int | None = Field(None, description="vCPU count of the source instance")
    ram_gb: int | None = Field(None, description="RAM of the source instance, in GB")
    network_ids: list[str] = Field(
        default_factory=list,
        description=(
            "NETWORK ids (`net-...`) of the source instance -- despite the upstream field "
            "being called netIds, which is what a create/restore calls its SUBNET list. "
            "These cannot be passed to a restore: get a `sub-...` id from "
            "list_relational_subnets instead."
        ),
    )
    config_group_id: str = Field("", description="Configuration group of the source instance")
    config_group_name: str = Field("", description="Configuration group name")
    username: str = Field("", description="Admin user of the source instance")
    backup_duration_days: int | None = Field(
        None,
        description=(
            "Retention in DAYS, not a window length in hours. New backups are created "
            "within 2-14, but older rows predate that rule and report 1 -- so this is not "
            "bounded on the way out."
        ),
    )
    is_restoring: bool | None = Field(
        None, description="True while a restore from this backup is in flight"
    )
    created: str = Field("", description="Creation timestamp")
    updated: str = Field("", description="Last update timestamp")

    @classmethod
    def from_api(cls, data: dict) -> "DatabaseBackup":
        """Build from a raw `BackupInfo` row."""
        status = data.get("status") or ""
        raw_nets = data.get("netIds")
        return cls(
            id=data.get("id") or "",
            name=data.get("name") or "",
            description=data.get("description") or "",
            instance_id=data.get("dbInstanceId") or "",
            instance_name=data.get("instanceName") or "",
            backup_type=data.get("backupType") or "",
            origin=data.get("type") or "",
            tier=data.get("backupTier") or "",
            parent_id=data.get("parent") or "",
            parent_name=data.get("parentName") or "",
            status=status,
            status_kind=classify(status),
            status_guidance=describe(status),
            size_gb=data.get("size"),
            storage_size_gb=data.get("storageSize"),
            storage_type=data.get("storageType") or "",
            datastore_type=data.get("datastoreType") or "",
            datastore_version=str(data.get("datastoreVersion") or ""),
            package_id=str(data.get("packageId") or ""),
            vcpus=data.get("vcpu"),
            ram_gb=data.get("ram"),
            network_ids=[str(net) for net in (raw_nets or []) if net],
            config_group_id=data.get("configId") or "",
            config_group_name=data.get("configName") or "",
            username=data.get("username") or "",
            backup_duration_days=data.get("backupDuration"),
            is_restoring=data.get("isRestoring"),
            created=data.get("created") or "",
            updated=data.get("updated") or "",
        )


class FreeBackupUsageData(BaseModel):
    """The project's free backup allowance and how much of it is used.

    The API reports two bare numbers and no unit. They are gigabytes, matching
    every other storage figure in vDB, but the field names are echoed here
    unchanged rather than renamed to something the API does not say.
    """

    free_backup_storage_gb: int | None = Field(
        None, description="Free backup storage granted to the project, in GB"
    )
    backup_usage_gb: float | None = Field(
        None, description="Backup storage currently in use, in GB"
    )

    @classmethod
    def from_api(cls, data: dict) -> "FreeBackupUsageData":
        """Build from a raw `FreeBackupStorageInfo` payload."""
        return cls(
            free_backup_storage_gb=data.get("freeBackupStorage"),
            backup_usage_gb=data.get("backupUsage"),
        )


class BackupCreateData(BaseModel):
    """The result of requesting a backup.

    Unlike a create *instance*, this is not an order flow: it answers
    synchronously with the id of the backup it started. The backup itself is
    still built asynchronously, so the id exists before the data does.
    """

    backup_id: str = Field("", description="ID of the backup that was started")
    instance_id: str = Field("", description="Instance it is being taken from")
    success: bool | None = Field(None, description="Whether the API accepted the request")
    error_message: str = Field("", description="Error detail when it did not")
    code: int | None = Field(None, description="Result code")
    next_step: str = Field(
        "", description="The backup is built asynchronously; how to confirm it completed"
    )

    @classmethod
    def from_api(cls, data: dict, next_step: str = "") -> "BackupCreateData":
        """Build from a raw `CreateBackupResponse` payload."""
        return cls(
            backup_id=data.get("backupId") or "",
            instance_id=data.get("dbInstanceId") or "",
            success=data.get("success"),
            error_message=data.get("errorMsg") or "",
            code=data.get("code"),
            next_step=next_step,
        )


class BackupDeleteResult(BaseModel):
    """The per-backup result of a delete request."""

    backup_id: str = Field("", description="Backup the result is for")
    action: str = Field("", description="Action performed")
    status: str = Field("", description="Reported status")
    success: bool | None = Field(None, description="Whether the API accepted the deletion")
    error_message: str = Field("", description="Error detail when it did not")
    code: int | None = Field(None, description="Result code")

    @classmethod
    def from_api(cls, data: dict) -> "BackupDeleteResult":
        """Build from a raw `DeleteBackupResponse` row."""
        return cls(
            backup_id=data.get("backupId") or "",
            action=data.get("action") or "",
            status=data.get("status") or "",
            success=data.get("success"),
            error_message=data.get("errorMsg") or "",
            code=data.get("code"),
        )


class BackupDeleteData(BaseModel):
    """The result of deleting backups, with the asynchrony spelled out.

    `backup_ids` is a list because the two endpoints differ in how many they
    can touch: the relational one repeats a single id in its path and always
    deletes exactly one, while the memory one is a `POST` whose array body is
    the whole instruction and deletes every entry in it. Reporting a
    many-backup deletion under one `backup_id` would hide most of what
    happened.
    """

    backup_ids: list[str] = Field(
        default_factory=list, description="Backups the deletion was sent for"
    )
    accepted: bool = Field(
        True,
        description=(
            "False when the API answered 200 but reported no per-backup result at all, "
            "which in practice means the request was silently ignored rather than queued."
        ),
    )
    results: list[BackupDeleteResult] = Field(
        default_factory=list, description="Per-backup results"
    )
    warning: str | None = Field(
        None, description="Set when the response cannot be read as success"
    )
    next_step: str = Field(
        "", description="Deletion is asynchronous; how to confirm the backup is gone"
    )


class AttachedInstance(BaseModel):
    """An instance a configuration group is attached to."""

    id: str = Field("", description="Instance ID")
    name: str = Field("", description="Instance name")

    @classmethod
    def from_api(cls, data: dict) -> "AttachedInstance":
        """Build from a raw `TinyDbInstanceInfo` row."""
        return cls(id=data.get("id") or "", name=data.get("name") or "")


class DatabaseConfiguration(BaseModel):
    """One relational configuration group.

    `values` holds only the parameters this group *overrides*; a group that
    overrides nothing reports `{}` rather than the engine's defaults. The
    settable parameters and their bounds come from
    `list_relational_configuration_params`, not from here.
    """

    id: str = Field(
        "",
        description=(
            "Configuration group ID. A single-node group is prefixed 'cfg-'; a group whose "
            "deploy_type is 'cluster' is prefixed 'pg-cfg-' instead."
        ),
    )
    name: str = Field("", description="Configuration group name")
    description: str = Field("", description="Configuration group description")
    datastore_type: str = Field("", description="Engine this group applies to")
    datastore_version: str = Field("", description="Engine version this group applies to")
    deploy_type: str = Field(
        "",
        description=(
            "'single_node' or 'cluster'. A 'cluster' group can only be attached to a "
            "PostgreSQL Cluster, never to a standalone instance."
        ),
    )
    values: dict[str, Any] = Field(
        default_factory=dict, description="Parameters this group overrides, name to value"
    )
    instance_count: int | None = Field(
        None, description="How many instances currently use this group"
    )
    instances: list[AttachedInstance] = Field(
        default_factory=list,
        description=(
            "The instances using this group. Changing a value here affects every one of "
            "them, so check their status after an update."
        ),
    )
    created: str = Field("", description="Creation timestamp")
    updated: str = Field("", description="Last update timestamp")

    @classmethod
    def from_api(cls, data: dict) -> "DatabaseConfiguration":
        """Build from a raw `ItemConfigInfo` row."""
        raw_instances = data.get("instances")
        return cls(
            id=data.get("id") or "",
            name=data.get("name") or "",
            description=data.get("description") or "",
            datastore_type=data.get("datastoreName") or "",
            datastore_version=str(data.get("datastoreVersionName") or ""),
            deploy_type=data.get("deployType") or "",
            values=data.get("values") or {},
            instance_count=data.get("instanceCount"),
            instances=[AttachedInstance.from_api(row) for row in (raw_instances or [])],
            created=data.get("created") or "",
            updated=data.get("updated") or "",
        )


class ConfigurationParam(BaseModel):
    """One settable engine parameter, with the bounds the platform enforces."""

    name: str = Field("", description="Parameter name, as used as a key in `values`")
    type: str = Field("", description="'integer', 'string' or 'boolean'")
    restart_required: bool = Field(
        False,
        description=(
            "True when changing this parameter puts every attached instance into "
            "RESTART_REQUIRED until it is rebooted. The instance keeps serving on the old "
            "value in the meantime -- the change is NOT live."
        ),
    )
    modifiable: bool = Field(True, description="False when the platform refuses any change")
    minimum: str | None = Field(None, description="Smallest accepted value (numeric params)")
    maximum: str | None = Field(None, description="Largest accepted value (numeric params)")
    allowed_values: list[str] = Field(
        default_factory=list,
        description=(
            "The complete set of accepted values, for parameters that have one (engine "
            "enums such as character sets and time zones). EMPTY for a numeric parameter -- "
            "use minimum/maximum there. The upstream field carries the two range endpoints "
            "for numeric parameters, which reads like a two-value enum and is not one."
        ),
    )
    description: str = Field("", description="Platform description; usually empty")

    @classmethod
    def from_api(cls, data: dict) -> "ConfigurationParam":
        """Build from a raw `ConfigurationParamInfo` row.

        The upstream `values` field means two different things depending on
        `type`. For a string parameter it is a genuine enum (39 character sets,
        ~500 time zones). For a numeric one it is just ``[min, max]`` again --
        so `innodb_buffer_pool_size` arrives as ``["5242880", "2147483647"]``,
        which presented as an enum would say those are the only two legal
        sizes. Numeric parameters therefore expose the range and no enum.
        """
        raw_values = [str(v) for v in (data.get("values") or [])]
        minimum = data.get("min")
        maximum = data.get("max")
        is_numeric_range = data.get("type") == "integer"
        return cls(
            name=data.get("name") or "",
            type=data.get("type") or "",
            restart_required=bool(data.get("restartRequired")),
            modifiable=data.get("modifiable") is not False,
            minimum=minimum,
            maximum=maximum,
            allowed_values=[] if is_numeric_range else raw_values,
            description=data.get("description") or "",
        )


class ConfigurationParamListData(BaseModel):
    """The parameters settable for one engine and version."""

    datastore_type: str = Field("", description="Engine the parameters belong to")
    datastore_version: str = Field("", description="Engine version")
    deploy_type: str | None = Field(None, description="Deploy type filter that was applied")
    count: int = Field(0, description="Parameters returned")
    restart_required_count: int = Field(
        0, description="How many of them force a restart of every attached instance"
    )
    filtered: bool = Field(
        False, description="True when a client-side name or restart filter narrowed the list"
    )
    unknown_engine: bool = Field(
        False,
        description=(
            "True when the platform returned nothing at all. This endpoint answers an "
            "unrecognised combination with an empty list rather than an error, so an empty "
            "result never means 'this engine has no settable parameters'. Read "
            "empty_result_hint for what to change."
        ),
    )
    empty_result_hint: str = Field(
        "",
        description=(
            "Set only when the platform returned nothing: what to try next. The usual cause "
            "is a deploy_type that does not match the engine version -- PostgreSQL 17 and 16 "
            "exist only as clusters and answer nothing without deploy_type='cluster'."
        ),
    )
    items: list[ConfigurationParam] = Field(default_factory=list, description="Parameters")


class ConfigurationUpdateData(BaseModel):
    """The result of changing a configuration group's values."""

    configuration: DatabaseConfiguration | None = Field(
        None,
        description=(
            "The group, re-read after the change. Its `values` can lag a moment behind the "
            "write and still show the previous set; `datastore_*` and `instances` are "
            "reliable. Re-read with get_relational_configuration to confirm the values "
            "landed."
        ),
    )
    changed_parameters: list[str] = Field(
        default_factory=list, description="Parameter names that were sent"
    )
    restart_required_parameters: list[str] = Field(
        default_factory=list,
        description=(
            "Those of the changed parameters that the platform marks restart_required. Every "
            "attached instance will report RESTART_REQUIRED until it is rebooted, and keeps "
            "running on the OLD value until then."
        ),
    )
    affected_instances: list[AttachedInstance] = Field(
        default_factory=list, description="Instances using this group, all of them affected"
    )
    next_step: str = Field("", description="What the caller has to check or do now")


class ConfigurationDeleteResult(BaseModel):
    """The per-group result of a delete request."""

    config_id: str = Field("", description="Configuration group the result is for")
    success: bool | None = Field(None, description="Whether the API accepted the deletion")
    error_message: str = Field("", description="Error detail when it did not")
    code: int | None = Field(None, description="Result code")

    @classmethod
    def from_api(cls, data: dict) -> "ConfigurationDeleteResult":
        """Build from a raw `DeleteConfigResponse` row."""
        return cls(
            config_id=data.get("configId") or "",
            success=data.get("success"),
            error_message=data.get("errorMsg") or "",
            code=data.get("code"),
        )


class ConfigurationDeleteData(BaseModel):
    """The result of deleting configuration groups."""

    config_ids: list[str] = Field(
        default_factory=list, description="Groups the deletion was sent for"
    )
    accepted: bool = Field(
        True, description="False when the API reported no result, or reported a failure"
    )
    results: list[ConfigurationDeleteResult] = Field(
        default_factory=list, description="Per-group results"
    )
    warning: str | None = Field(
        None, description="Set when the response cannot be read as success"
    )
    next_step: str = Field("", description="How to confirm the groups are gone")


class BackupStoragePackage(BaseModel):
    """One purchasable backup-storage quota package.

    Upstream this is `DbBackupPackageResponse.packages[]`, referenced by both
    families' price-list endpoints -- one catalogue behind two paths, so one
    model serves both.
    """

    package_id: str = Field("", description="Package ID -- what a create or resize sends")
    name: str = Field("", description="Package name, e.g. 'db.backup.quota.500GB'")
    sku: str = Field("", description="Billing SKU")
    quota_gb: int | None = Field(None, description="Quota the package grants, in GB")
    description: str = Field("", description="Package description")
    price: int | None = Field(
        None, description="Indicative price; comes back null on some accounts"
    )

    @classmethod
    def from_api(cls, data: dict) -> "BackupStoragePackage":
        """Build from a raw `DbBackupPackageDetail` row."""
        raw_quota = data.get("packageQuota")
        try:
            quota = int(raw_quota) if raw_quota is not None else None
        except (TypeError, ValueError):
            quota = None
        return cls(
            package_id=str(data.get("packageId") or ""),
            name=data.get("packageName") or "",
            sku=data.get("sku") or "",
            quota_gb=quota,
            description=data.get("description") or "",
            price=data.get("price"),
        )


class BackupStoragePackageGroup(BaseModel):
    """The packages offered for one engine group."""

    engine_group: int | None = Field(
        None,
        description=(
            "Which family of engines these packages are offered to; a backup reports the "
            "same number as `engineGroup`. A group can legitimately offer no packages."
        ),
    )
    count: int = Field(0, description="Packages in this group")
    packages: list[BackupStoragePackage] = Field(
        default_factory=list, description="Packages offered"
    )


class BackupStoragePackageListData(BaseModel):
    """The backup-storage price list.

    The API repeats the catalogue once per engine group, and measured live the
    repetitions are **identical** -- engine groups 1 and 2 offer the same eight
    package ids with the same SKUs and quotas, and group 3 offers none. So a
    package id is unambiguous on its own; there is nothing to match against the
    group when choosing one.

    Both views are kept because that equality is an observation, not a
    guarantee: `packages` is the deduplicated list to pick from, `groups` is
    what the API actually said, and `groups_are_identical` says whether the two
    can be used interchangeably on this account.
    """

    count: int = Field(0, description="Distinct packages available to buy")
    packages: list[BackupStoragePackage] = Field(
        default_factory=list,
        description="The deduplicated catalogue -- pick a `package_id` from here",
    )
    groups: list[BackupStoragePackageGroup] = Field(
        default_factory=list, description="Exactly what the API returned, per engine group"
    )
    groups_are_identical: bool = Field(
        True,
        description=(
            "True when every non-empty engine group offers the same package ids, which is "
            "what the platform does today. If this is ever False the deduplicated `packages` "
            "list is hiding a difference and `groups` must be used instead."
        ),
    )


class BackupStorage(BaseModel):
    """One purchased backup-storage quota, and how much of it is used."""

    id: str = Field("", description="Backup storage ID (starts with 'db-bk-storage-')")
    name: str = Field("", description="Backup storage name")
    status: str = Field("", description="Status, verbatim from the API")
    status_kind: StatusKind = Field("unknown", description="What the status means to act on")
    status_guidance: str = Field(
        "", description="What to do about this status, derived from status_kind"
    )
    quota_gb: int | None = Field(None, description="Quota purchased, in GB")
    usage_gb: float | None = Field(None, description="Quota currently used, in GB")
    engine_group: int | None = Field(
        None, description="Engine family this quota serves; matches a backup's engineGroup"
    )
    package_id: str = Field("", description="Package this quota was bought as")
    package_name: str = Field("", description="Package name")

    @classmethod
    def from_api(cls, data: dict) -> "BackupStorage":
        """Build from a raw `BackupStorageDetail` row."""
        status = data.get("status") or ""
        return cls(
            id=data.get("id") or "",
            name=data.get("name") or "",
            status=status,
            status_kind=classify(status),
            status_guidance=describe(status),
            quota_gb=data.get("quota"),
            usage_gb=data.get("usage"),
            engine_group=data.get("engineGroup"),
            package_id=str(data.get("backupPackageId") or ""),
            package_name=data.get("backupPackageName") or "",
        )


class BackupStorageListData(BaseModel):
    """The paid backup storage this project currently holds.

    Empty is the normal state for a project that has never bought any: backups
    then run against the free allowance reported by
    `get_relational_free_backup_usage`, not against nothing.
    """

    count: int = Field(0, description="Backup storage quotas held")
    none_purchased: bool = Field(
        False,
        description=(
            "True when the project holds no paid backup storage. Not an error and not a "
            "failed lookup -- check the free allowance instead."
        ),
    )
    items: list[BackupStorage] = Field(default_factory=list, description="Backup storage quotas")


class BackupStorageActionData(BaseModel):
    """The result of a backup-storage lifecycle action."""

    storage_ids: list[str] = Field(
        default_factory=list, description="Backup storage the action was sent for"
    )
    accepted: bool = Field(
        True,
        description=(
            "False when the API answered 200 but reported no result, or reported a failure. "
            "An empty result means the action was silently ignored rather than queued."
        ),
    )
    results: list[ActionResult] = Field(default_factory=list, description="Per-storage results")
    warning: str | None = Field(
        None, description="Set when the response cannot be read as success"
    )
    next_step: str = Field("", description="How to confirm the action actually took effect")
