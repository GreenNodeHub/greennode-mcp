"""MemoryStore (Redis) configuration-group tools.

A configuration group is a named set of engine parameter overrides that
instances attach to. The resource is shared with the relational family --
`ItemConfigInfo`, `ConfigurationParamInfo`, `UpdateConfigGroupRequest` and
`DeleteConfigGroupRequest` are one schema each -- so the models and the
update/delete DTOs are shared too. Three things differ:

* **`get` is a normal path lookup**: `/configurations/{id}/detail`. The
  relational family instead puts the id in a **query parameter** with the
  literal word `id` as the path segment (`/configurations/id?id=cfg-...`).
* **Delete is a `POST`**, not a `DELETE` carrying a body. The array body is
  the same either way.
* **Create has no `deployType`.** That field is required in practice in the
  relational family despite the spec calling it optional; here the schema does
  not have it at all, and this API accepts unknown fields silently rather than
  rejecting them -- so `extra="forbid"` on the DTO is what stops a copied
  relational payload from meaning something other than it says.

What does *not* differ is the part that matters most: changing a group changes
every instance attached to it, and a parameter marked `restartRequired` leaves
each one running the OLD value until it is rebooted.
"""

from __future__ import annotations

from greennode.mcp_core.validators import validate_id
from greennode.vdb_mcp_server.client import VdbClient
from greennode.vdb_mcp_server.config import VDB_MEMORY_SERVICE, VdbConfig
from greennode.vdb_mcp_server.discovery_cache import DiscoveryCache
from greennode.vdb_mcp_server.guards import require_write
from greennode.vdb_mcp_server.models import (
    ConfigurationDeleteData,
    ConfigurationDeleteResult,
    ConfigurationParam,
    ConfigurationParamListData,
    ConfigurationUpdateData,
    CreateMemoryConfigurationDto,
    DatabaseConfiguration,
    MemoryConfigurationListData,
    PageInfo,
    UpdateConfigurationDto,
)
from greennode.vdb_mcp_server.paging import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    as_list,
    build_page_params,
    unwrap_content_list,
    unwrap_wrapped,
)
from greennode.vdb_mcp_server.tool_annotations import DESTRUCTIVE, READ, WRITE
from pydantic import Field
from typing import Any


BASE = "/v1/configurations"

RESTART_NEXT_STEP = (
    "At least one changed parameter is marked restart_required, so every attached instance "
    "is expected to move to RESTART_REQUIRED and to keep running on the OLD value until it "
    "is rebooted. Check each instance with get_memory_instance, then reboot it with "
    "reboot_memory_instance once the user agrees to the downtime -- do not reboot a database "
    "on your own initiative, and note that rebooting Redis discards an unpersisted dataset."
)

LIVE_NEXT_STEP = (
    "None of the changed parameters is marked restart_required, so the platform should apply "
    "them without a restart. Confirm with get_memory_instance anyway: a status of "
    "RESTART_REQUIRED means a reboot is needed after all, and it is the instance status, not "
    "this response, that decides."
)

NO_INSTANCE_NEXT_STEP = (
    "No instance uses this group yet, so nothing restarted. The values take effect when an "
    "instance attaches to it via update_memory_instance_config_group."
)

DELETE_NEXT_STEP = (
    "Confirm with list_memory_configurations. A group still attached to an instance cannot be "
    "deleted -- detach it first, either by pointing the instance at another group or by "
    "passing an empty string to update_memory_instance_config_group."
)

EMPTY_DELETE_WARNING = (
    "The API answered 200 but reported no per-group result, and a deletion it accepted "
    "always reports one. Treat this as NOT deleted: re-read list_memory_configurations and "
    "tell the user the call had no effect rather than reporting success."
)

FAILED_DELETE_WARNING = (
    "The API answered HTTP 200 but the result says the deletion did NOT happen. The HTTP "
    "status describes the call, not the deletion -- report the per-result error. Two known "
    "causes of a 'Resource not found' here, neither of which means the id is wrong: the "
    "group is still attached to an instance, or it was created moments ago -- a brand-new "
    "group is listed and readable but not yet deletable, and answers 404 for the first "
    "several seconds. Re-read list_memory_configurations before telling the user the id does "
    "not exist, and retry after a short wait if it is a group they just made."
)

MISSING_CONFIG_MESSAGE = (
    "No configuration group with id {config_id!r}. The relational twin of this endpoint "
    "answers an unknown id with HTTP 200 and an empty payload rather than a 404, so this is "
    "'not found' rather than an empty group. Check list_memory_configurations -- and note "
    "that a group id says nothing about its family, so if it exists at all it may be a "
    "relational group, reachable through get_relational_configuration."
)


class MemoryConfigHandler:
    """Register and serve the MemoryStore configuration-group tools."""

    def __init__(
        self,
        mcp,
        config: VdbConfig,
        client: VdbClient,
        cache: DiscoveryCache,
        allow_write: bool = False,
    ):
        self.mcp = mcp
        self.config = config
        self.client = client
        self.cache = cache
        self.allow_write = allow_write

        # One literal name per registration: the monorepo Conventions job reads
        # these names statically, and a loop would hide every tool from it.
        self.mcp.tool(name="list_memory_configurations", annotations=READ)(
            self.list_memory_configurations
        )
        self.mcp.tool(name="get_memory_configuration", annotations=READ)(
            self.get_memory_configuration
        )
        self.mcp.tool(name="list_memory_configuration_params", annotations=READ)(
            self.list_memory_configuration_params
        )

        if self.allow_write:
            self.mcp.tool(name="create_memory_configuration", annotations=WRITE)(
                self.create_memory_configuration
            )
            self.mcp.tool(name="update_memory_configuration", annotations=WRITE)(
                self.update_memory_configuration
            )
            self.mcp.tool(name="delete_memory_configurations", annotations=DESTRUCTIVE)(
                self.delete_memory_configurations
            )

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    async def _get(self, path: str, params: dict | None = None) -> Any:
        return await self.client.call(
            "GET", path, service=VDB_MEMORY_SERVICE, params=params or None
        )

    async def _fetch_params(
        self, datastore_type: str, datastore_version: str, refresh: bool
    ) -> list[dict]:
        """Fetch the raw parameter catalogue, through the discovery cache."""
        params: dict[str, Any] = {
            "datastoreType": datastore_type,
            "datastoreVersion": datastore_version,
        }

        async def fetch():
            return as_list(await self._get(f"{BASE}/params", params))

        # The engine pair is part of the key: caching on the tool name alone
        # would serve Redis 7.2's parameters for a Redis 4.0 question.
        return await self.cache.get_or_fetch(
            "list_memory_configuration_params",
            (datastore_type.lower(), str(datastore_version)),
            fetch,
            refresh=refresh,
        )

    async def _restart_required_names(
        self, configuration: DatabaseConfiguration, changed: list[str]
    ) -> list[str]:
        """Which of *changed* the platform says force a restart.

        Best effort: this is an extra call purely to enrich a warning, so a
        failure here must not turn a successful update into an error. An empty
        answer therefore means "could not tell", which is why the caller still
        points at the instance status as the authority.
        """
        try:
            rows = await self._fetch_params(
                configuration.datastore_type,
                configuration.datastore_version,
                refresh=False,
            )
        except Exception:
            return []
        wanted = set(changed)
        return [
            row.get("name")
            for row in rows
            if row.get("name") in wanted and row.get("restartRequired")
        ]

    # ------------------------------------------------------------------
    # reads
    # ------------------------------------------------------------------

    async def list_memory_configurations(
        self,
        page: int = Field(1, ge=1, description="Page number (1-based, not 0-based)"),
        page_size: int = Field(
            DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE, description="Items per page"
        ),
    ) -> MemoryConfigurationListData:
        """List the MemoryStore configuration groups in this project.

        Each row carries the instances attached to it, so this is the quickest
        way to see what a change to a group would affect. It takes no filters.

        The API reports `max_page_size` as 30 here, lower than the 50 it
        reports for backups and the 100 for instances.
        """
        raw = await self._get(BASE, build_page_params(page, page_size))
        result = unwrap_content_list(raw)
        return MemoryConfigurationListData(
            count=len(result.items),
            page=PageInfo.from_page(result),
            items=[DatabaseConfiguration.from_api(row) for row in result.items],
        )

    async def get_memory_configuration(
        self,
        config_id: str = Field(..., description="Configuration group ID, e.g. 'cfg-1234abcd-...'"),
    ) -> DatabaseConfiguration:
        """Get one MemoryStore configuration group by ID.

        A plain path lookup — `/configurations/{id}/detail`. The relational
        family is the odd one here: it puts the id in a **query parameter**
        with the literal word `id` as the path segment.

        **This endpoint is not family-scoped**: measured live, it returns a
        MySQL configuration group without complaint. So a result proves nothing
        about which family the group belongs to — read `datastore_type` before
        attaching it to anything, because a group can only be used by an
        instance running the same engine and version.

        Read this before an update: `values` is the group's current overrides
        and `instances` is everything the update would affect. A listing row
        carries both too, but the detail is the authority.
        """
        validate_id(config_id, "config_id")
        raw = await self._get(f"{BASE}/{config_id}/detail")
        payload = unwrap_wrapped(raw)
        # Guarded rather than assumed: the relational twin answers an unknown
        # id with 200 and an empty payload instead of a 404, and a blank model
        # would read as a real group with no overrides.
        if not isinstance(payload, dict) or not payload.get("id"):
            raise ValueError(MISSING_CONFIG_MESSAGE.format(config_id=config_id))
        return DatabaseConfiguration.from_api(payload)

    async def list_memory_configuration_params(
        self,
        datastore_version: str = Field(
            ..., description="Redis version from list_memory_datastores, e.g. '7.2'"
        ),
        datastore_type: str = Field(
            "Redis",
            description=(
                "Engine. Redis is the only one this family runs, which is why it is "
                "defaulted -- but the endpoint itself is not scoped to it and will serve a "
                "relational engine's parameters if asked, so leave the default alone unless "
                "you mean it."
            ),
        ),
        name: str | None = Field(
            None,
            description=(
                "Case-insensitive substring filter on the parameter name, applied here "
                "rather than by the API."
            ),
        ),
        restart_required: bool | None = Field(
            None,
            description=(
                "Keep only parameters that do (True) or do not (False) force a restart of "
                "every attached instance. Also applied client-side."
            ),
        ),
        refresh: bool = Field(
            False, description="Bypass the cache and re-read the catalogue from the API"
        ),
    ) -> ConfigurationParamListData:
        """List the Redis parameters a configuration group can set.

        **Read this before `update_memory_configuration`.** Each parameter
        reports `restart_required`, and changing one of those puts every
        attached instance into `RESTART_REQUIRED` — the instance keeps serving
        the old value until it is rebooted, and rebooting Redis discards an
        unpersisted dataset.

        Three things about the bounds, all measured:

        - Numeric parameters report `minimum`/`maximum` and an **empty**
          `allowed_values`. Upstream, `values` echoes the bounds as a list,
          which reads like an enum and is not one.
        - **`maximum` is usually absent.** For Redis, most integer parameters
          come back with `max: null` and only a `min` — `repl-backlog-size`
          reports a floor of 16384 and no ceiling at all. Do not tell a user a
          parameter has an upper bound unless `maximum` is actually set.
        - String parameters report the real enum in `allowed_values`.

        `datastore_version` is **required** and an unrecognised pair returns an
        empty list rather than an error — the result sets `unknown_engine` when
        that happens, so it is not mistaken for an engine with no settable
        parameters.

        Parameters differ sharply between Redis versions, so pass the version
        of the group you are about to change rather than whatever is handy:
        measured live, Redis 4.0 exposes 24 parameters (one of them
        restart-required) while 6.2 and 7.2 expose 8, none restart-required.
        """
        if not str(datastore_version).strip():
            raise ValueError(
                "datastore_version must not be empty; take the engine and version together "
                "from list_memory_datastores."
            )
        rows = await self._fetch_params(datastore_type, datastore_version, refresh=refresh)
        items = [ConfigurationParam.from_api(row) for row in rows]

        selected = items
        if name:
            needle = name.lower()
            selected = [p for p in selected if needle in p.name.lower()]
        if restart_required is not None:
            selected = [p for p in selected if p.restart_required is restart_required]

        return ConfigurationParamListData(
            datastore_type=datastore_type,
            datastore_version=str(datastore_version),
            deploy_type=None,
            count=len(selected),
            restart_required_count=sum(1 for p in selected if p.restart_required),
            filtered=bool(name) or restart_required is not None,
            unknown_engine=not items,
            items=selected,
        )

    # ------------------------------------------------------------------
    # writes
    # ------------------------------------------------------------------

    async def create_memory_configuration(
        self,
        spec: CreateMemoryConfigurationDto = Field(
            ..., description="The configuration group to create"
        ),
    ) -> DatabaseConfiguration:
        """Create an empty MemoryStore configuration group. Free, and immediate.

        ## Requirements

        - `--allow-write` must be enabled.
        - `datastoreVersion` must be a version from `list_memory_datastores`.
          It is **fixed at creation**: no update changes it, and a group cannot
          be attached to an instance running a different version.
        - `deployType` is sent for you and defaults to `single_node`. The
          field is **absent from this endpoint's schema** yet required: a body
          carrying only the four declared fields is rejected as
          `400 bad_request`. Do not remove it.
        - `datastoreType` is case-sensitive here (`"redis"` is rejected), so
          the DTO normalises it. Nothing to do, but do not bypass the DTO.

        ## Workflow

        1. `list_memory_datastores` → pick the Redis version.
        2. This tool. The group is created **empty**: it overrides nothing
           until values are set.
        3. `list_memory_configuration_params` → see what can be set.
        4. `update_memory_configuration` → set the values.
        5. `update_memory_instance_config_group` → attach it to an instance.

        Creating a group changes no running database, so it is safe to do
        before deciding which instance will use it.

        The response is very thin — measured live it carries only `id` and
        `deployType`, with `name` and the engine fields null — so the group is
        re-read before being returned. Note also that a new group cannot be
        deleted for the first several seconds: `delete_memory_configurations`
        answers "Resource not found" until it settles.
        """
        require_write(self.allow_write)
        raw = await self.client.call(
            "POST",
            f"{BASE}/create",
            service=VDB_MEMORY_SERVICE,
            json=spec.model_dump(exclude_none=True),
        )
        payload = unwrap_wrapped(raw)
        created = DatabaseConfiguration.from_api(payload if isinstance(payload, dict) else {})
        if created.id and not created.name:
            # The create echo carries only `id` and `deployType`. A re-read
            # normally fills the rest in -- but measured live, the detail
            # endpoint answers an *empty payload* for the first few seconds
            # after a create, so the re-read can fail too. When it does, fill
            # from the request: these are values this call just sent, not
            # guesses, and returning blanks would show the user a group that
            # looks unnamed and engine-less.
            try:
                return await self.get_memory_configuration(config_id=created.id)
            except Exception:
                return created.model_copy(
                    update={
                        "name": spec.name,
                        "description": spec.description or "",
                        "datastore_type": spec.datastoreType,
                        "datastore_version": spec.datastoreVersion,
                        "deploy_type": created.deploy_type or spec.deployType,
                    }
                )
        return created

    async def update_memory_configuration(
        self,
        config_id: str = Field(..., description="Configuration group to change"),
        spec: UpdateConfigurationDto = Field(
            ..., description="Parameter values the group should have"
        ),
    ) -> ConfigurationUpdateData:
        """Change a configuration group's parameter values. Affects every attached instance.

        ## Requirements

        - `--allow-write` must be enabled.
        - **Check what is attached first.** `get_memory_configuration` lists
          the instances using this group; all of them are affected by this
          call, including production ones. Confirm the list with the user
          before changing a group with attachments.
        - Every key must be a parameter name from
          `list_memory_configuration_params` for **this group's** Redis
          version, and every value must respect the `minimum`/`maximum` or
          `allowed_values` reported there.
        - Know which of your parameters are `restart_required` before sending.
          Those put each attached instance into `RESTART_REQUIRED`, where it
          **keeps serving the old value** until someone reboots it — so the
          change silently does not take effect until that downtime is taken,
          and for Redis the reboot also discards an unpersisted dataset.

        ## Workflow

        1. `get_memory_configuration` → read the current `values` and the
           attached `instances`.
        2. `list_memory_configuration_params` with the group's version → check
           bounds and `restart_required`. Passing `restart_required=true`
           shows exactly which parameters force a reboot.
        3. This tool. The result names the changed parameters, those needing a
           restart, and the affected instances.
        4. `get_memory_instance` on each affected instance. If one reports
           `RESTART_REQUIRED`, tell the user a reboot is needed and let **them**
           decide when.

        Send the full set of overrides the group should end up with: whether
        the API merges or replaces is not documented.
        """
        require_write(self.allow_write)
        validate_id(config_id, "config_id")
        body = {"id": config_id, "values": spec.values}
        raw = await self.client.call(
            "PUT", f"{BASE}/update", service=VDB_MEMORY_SERVICE, json=body
        )
        payload = unwrap_wrapped(raw)
        configuration = DatabaseConfiguration.from_api(
            payload if isinstance(payload, dict) else {}
        )
        # The relational update response comes back with `values: {}` and no
        # `instances`, which once made this tool report "nothing affected" at
        # the moment it had pushed a running database into RESTART_REQUIRED.
        # Re-read rather than trusting a thin echo to answer that question.
        if not configuration.datastore_type or not configuration.instances:
            try:
                configuration = await self.get_memory_configuration(config_id=config_id)
            except Exception:
                pass

        changed = sorted(spec.values)
        needs_restart = await self._restart_required_names(configuration, changed)
        instances = configuration.instances

        if not instances:
            next_step = NO_INSTANCE_NEXT_STEP
        elif needs_restart:
            next_step = RESTART_NEXT_STEP
        else:
            next_step = LIVE_NEXT_STEP

        return ConfigurationUpdateData(
            configuration=configuration,
            changed_parameters=changed,
            restart_required_parameters=sorted(n for n in needs_restart if n),
            affected_instances=list(instances),
            next_step=next_step,
        )

    async def delete_memory_configurations(
        self,
        config_ids: list[str] = Field(
            ..., min_length=1, description="Configuration groups to delete"
        ),
    ) -> ConfigurationDeleteData:
        """Delete one or more MemoryStore configuration groups. Cannot be undone.

        ## Requirements

        - `--allow-write` must be enabled.
        - Confirm with the user first, and check each group with
          `get_memory_configuration`: a group with a non-empty `instances`
          list is in use, and the platform refuses to delete it rather than
          silently detaching it.
        - A group created moments ago is **not yet deletable**: the call
          answers `success: false, code: 404` for the first several seconds
          even though the group is listed and readable. That is a timing
          window, not a wrong id — wait and retry.
        - The parameter values in the group are lost. An instance that used the
          group keeps running, but the overrides are no longer reproducible
          from the platform.

        ## Workflow

        1. `list_memory_configurations` → find the ids and see what is
           attached.
        2. This tool, then confirm with `list_memory_configurations`.

        This endpoint takes a JSON **array** of ids and can delete several at
        once, so the argument is a list; each id gets its own result row —
        verified live with a two-id batch. Note it is a `POST` here where the
        relational family uses a `DELETE` with a body; the payload is the same,
        and the array element's field is `id` even though the *response* calls
        it `configId` (sending `configId` is accepted and does nothing).

        Deletion is asynchronous: a successful row means accepted, and the
        group still resolves for a few seconds afterwards.
        """
        require_write(self.allow_write)
        # `min_length=1` on the Field only constrains the MCP input schema; a
        # direct call bypasses it, and an empty array would send a destructive
        # request that names nothing.
        if not config_ids:
            raise ValueError(
                "config_ids must name at least one configuration group; an empty list is a "
                "delete request that identifies nothing."
            )
        for config_id in config_ids:
            validate_id(config_id, "config_ids")
        raw = await self.client.call(
            "POST",
            f"{BASE}/delete",
            service=VDB_MEMORY_SERVICE,
            json=[{"id": config_id} for config_id in config_ids],
        )
        results = [ConfigurationDeleteResult.from_api(row) for row in as_list(raw)]
        if not results:
            return ConfigurationDeleteData(
                config_ids=list(config_ids),
                accepted=False,
                results=[],
                warning=EMPTY_DELETE_WARNING,
                next_step=DELETE_NEXT_STEP,
            )
        failures = [r for r in results if r.success is False]
        if failures:
            detail = "; ".join(
                f"{r.config_id or 'unknown'}: {r.error_message or 'no message'} (code {r.code})"
                for r in failures
            )
            return ConfigurationDeleteData(
                config_ids=list(config_ids),
                accepted=False,
                results=results,
                warning=f"{FAILED_DELETE_WARNING} Reported: {detail}.",
                next_step=DELETE_NEXT_STEP,
            )
        return ConfigurationDeleteData(
            config_ids=list(config_ids), results=results, next_step=DELETE_NEXT_STEP
        )
