"""Relational configuration-group tools (MySQL, MariaDB, standalone PostgreSQL).

A configuration group is a named set of engine parameter overrides that
instances attach to. Four things here differ from the rest of this server:

* **`get` takes its id as a QUERY parameter**, not a path segment:
  `GET /v1/configurations/id?id=cfg-...`. The path segment is the literal word
  "id".
* **Changing a group changes every instance attached to it.** For 13 of
  MySQL 8.0's 67 parameters the platform marks `restartRequired`, and those
  push each attached instance into `RESTART_REQUIRED` -- where it keeps
  serving the OLD value until someone reboots it. The update tool reports
  which parameters did that and which instances are affected.
* **The parameter catalogue needs an engine and version**, like flavours, and
  answers an unrecognised pair with an empty list rather than an error.
* **Delete takes a JSON array** and is a `DELETE` with a body.
"""

from __future__ import annotations

from greennode.mcp_core.validators import validate_id
from greennode.vdb_mcp_server.client import VdbClient
from greennode.vdb_mcp_server.config import VDB_RELATIONAL_SERVICE, VdbConfig
from greennode.vdb_mcp_server.discovery_cache import DiscoveryCache
from greennode.vdb_mcp_server.guards import require_write
from greennode.vdb_mcp_server.models import (
    DEPLOY_TYPE_CLUSTER,
    DEPLOY_TYPE_SINGLE_NODE,
    ConfigurationDeleteData,
    ConfigurationDeleteResult,
    ConfigurationParam,
    ConfigurationParamListData,
    ConfigurationUpdateData,
    CreateRelationalConfigurationDto,
    DatabaseConfiguration,
    PageInfo,
    RelationalConfigurationListData,
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
from typing import Any, Literal


BASE = "/v1/configurations"

RESTART_NEXT_STEP = (
    "At least one changed parameter is marked restart_required, so every attached instance "
    "is expected to move to RESTART_REQUIRED and to keep running on the OLD value until it "
    "is rebooted. Check each instance with get_relational_instance, then reboot it with "
    "reboot_relational_instance once the user agrees to the downtime -- do not reboot a "
    "database on your own initiative."
)

LIVE_NEXT_STEP = (
    "None of the changed parameters is marked restart_required, so the platform should apply "
    "them without a restart. Confirm with get_relational_instance anyway: a status of "
    "RESTART_REQUIRED means a reboot is needed after all, and it is the instance status, not "
    "this response, that decides."
)

NO_INSTANCE_NEXT_STEP = (
    "No instance uses this group yet, so nothing restarted. The values take effect when an "
    "instance attaches to it via update_relational_instance_config_group."
)

DELETE_NEXT_STEP = (
    "Confirm with list_relational_configurations. A group still attached to an instance "
    "cannot be deleted -- detach it first by pointing the instance at another group."
)

EMPTY_DELETE_WARNING = (
    "The API answered 200 but reported no per-group result, and a deletion it accepted "
    "always reports one. Treat this as NOT deleted: re-read list_relational_configurations "
    "and tell the user the call had no effect rather than reporting success."
)

FAILED_DELETE_WARNING = (
    "The API answered HTTP 200 but the result says the deletion did NOT happen. The HTTP "
    "status describes the call, not the deletion -- report the per-result error. The usual "
    "cause is a group still attached to an instance."
)

MISSING_CONFIG_MESSAGE = (
    "No configuration group with id {config_id!r}. This endpoint answers an unknown id with "
    "HTTP 200 and an empty payload rather than a 404, so this is 'not found' rather than an "
    "empty group. Check list_relational_configurations, and note that memory-family groups "
    "live behind a different endpoint."
)


def _empty_params_hint(
    datastore_type: str, datastore_version: str, deploy_type: str | None
) -> str:
    """Explain an empty parameter catalogue, which is never an error response.

    The endpoint answers any combination it does not recognise with an empty
    list, so the caller has to be told which part to change. By far the most
    common cause is `deploy_type`: it defaults to `single_node`, and the
    PostgreSQL versions a cluster runs (17 and 16) have no single-node
    parameters at all.
    """
    engine = (datastore_type or "").strip().lower()
    if engine == "postgresql" and deploy_type != DEPLOY_TYPE_CLUSTER:
        return (
            f"No parameters for PostgreSQL {datastore_version} as a "
            f"{deploy_type or DEPLOY_TYPE_SINGLE_NODE} deployment. deploy_type defaults to "
            "single_node, and PostgreSQL 17 and 16 exist only as clusters -- retry with "
            "deploy_type='cluster' before concluding the version is wrong. Read the "
            "group's own deploy_type with get_relational_configuration."
        )
    if engine == "postgresql" and deploy_type == DEPLOY_TYPE_CLUSTER:
        return (
            f"No CLUSTER parameters for PostgreSQL {datastore_version}. Versions 14 and "
            "below are single-node only -- retry without deploy_type. Check which versions "
            "a cluster supports with list_postgresql_datastores."
        )
    return (
        "The platform recognised no such engine/version combination. Take the pair from "
        "list_relational_datastores, and note that deploy_type defaults to single_node."
    )


class RelationalConfigHandler:
    """Register and serve the relational configuration-group tools."""

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
        self.mcp.tool(name="list_relational_configurations", annotations=READ)(
            self.list_relational_configurations
        )
        self.mcp.tool(name="get_relational_configuration", annotations=READ)(
            self.get_relational_configuration
        )
        self.mcp.tool(name="list_relational_configuration_params", annotations=READ)(
            self.list_relational_configuration_params
        )

        if self.allow_write:
            self.mcp.tool(name="create_relational_configuration", annotations=WRITE)(
                self.create_relational_configuration
            )
            self.mcp.tool(name="update_relational_configuration", annotations=WRITE)(
                self.update_relational_configuration
            )
            self.mcp.tool(name="delete_relational_configurations", annotations=DESTRUCTIVE)(
                self.delete_relational_configurations
            )

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    async def _get(self, path: str, params: dict | None = None) -> Any:
        return await self.client.call(
            "GET", path, service=VDB_RELATIONAL_SERVICE, params=params or None
        )

    async def _fetch_params(
        self, datastore_type: str, datastore_version: str, deploy_type: str | None, refresh: bool
    ) -> list[dict]:
        """Fetch the raw parameter catalogue, through the discovery cache."""
        params: dict[str, Any] = {
            "datastoreType": datastore_type,
            "datastoreVersion": datastore_version,
        }
        if deploy_type:
            params["deployType"] = deploy_type

        async def fetch():
            return as_list(await self._get(f"{BASE}/params", params))

        # The engine pair is part of the key: caching on the tool name alone
        # would serve MySQL 8.0's parameters for a PostgreSQL 15 question.
        return await self.cache.get_or_fetch(
            "list_relational_configuration_params",
            (datastore_type.lower(), str(datastore_version), deploy_type or ""),
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
                configuration.deploy_type or None,
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

    async def list_relational_configurations(
        self,
        page: int = Field(1, ge=1, description="Page number (1-based, not 0-based)"),
        page_size: int = Field(
            DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE, description="Items per page"
        ),
    ) -> RelationalConfigurationListData:
        """List the relational configuration groups in this project.

        Each row carries the instances attached to it, so this is the quickest
        way to see what a change to a group would affect. It takes no filters.

        The API reports `max_page_size` as 30 here, lower than the 50 it
        reports for backups and the 100 for instances.
        """
        raw = await self._get(BASE, build_page_params(page, page_size))
        result = unwrap_content_list(raw)
        return RelationalConfigurationListData(
            count=len(result.items),
            page=PageInfo.from_page(result),
            items=[DatabaseConfiguration.from_api(row) for row in result.items],
        )

    async def get_relational_configuration(
        self,
        config_id: str = Field(..., description="Configuration group ID, e.g. 'cfg-1234abcd-...'"),
    ) -> DatabaseConfiguration:
        """Get one relational configuration group by ID.

        Note the shape of this endpoint: the id goes in a **query parameter**,
        and the literal word `id` is the path segment
        (`/configurations/id?id=cfg-...`). The memory family uses
        `/configurations/{id}/detail` instead.
        """
        validate_id(config_id, "config_id")
        raw = await self._get(f"{BASE}/id", {"id": config_id})
        payload = unwrap_wrapped(raw)
        if not isinstance(payload, dict) or not payload.get("id"):
            raise ValueError(MISSING_CONFIG_MESSAGE.format(config_id=config_id))
        return DatabaseConfiguration.from_api(payload)

    async def list_relational_configuration_params(
        self,
        datastore_type: str = Field(
            ..., description="Engine from list_relational_datastores, e.g. 'MySQL'"
        ),
        datastore_version: str = Field(
            ..., description="Engine version from list_relational_datastores, e.g. '8.0'"
        ),
        deploy_type: Literal["single_node", "cluster"] | None = Field(
            None,
            description=(
                "Which deployment's parameters to list. Omitting it means 'single_node', "
                "NOT 'any' -- so a PostgreSQL Cluster group needs deploy_type='cluster' "
                "explicitly or the answer is empty. Measured: PostgreSQL 17 and 16 return "
                "nothing without it (they exist only as clusters), and PostgreSQL 15 "
                "returns a DIFFERENT set for each -- 41 single-node against 25 cluster."
            ),
        ),
        name: str | None = Field(
            None,
            description=(
                "Case-insensitive substring filter on the parameter name, applied here "
                "rather than by the API. MySQL 8.0 alone exposes 67 parameters and some "
                "carry ~500 allowed values, so filtering keeps the answer readable."
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
        """List the engine parameters a configuration group can set.

        **Read this before `update_relational_configuration`.** Each parameter
        reports `restart_required`, and changing one of those puts every
        attached instance into `RESTART_REQUIRED` — the instance keeps serving
        the old value until it is rebooted. For MySQL 8.0, 13 of 67 parameters
        behave that way.

        Two things about the bounds:

        - Numeric parameters report `minimum`/`maximum` and an **empty**
          `allowed_values`. Upstream those two endpoints are echoed as a
          two-element list, which reads like an enum and is not one.
        - String parameters report the real enum in `allowed_values`.

        `datastore_type` and `datastore_version` are both **required**, and an
        unrecognised combination returns an empty list rather than an error —
        the result sets `unknown_engine` and fills `empty_result_hint` when
        that happens, so it is never mistaken for an engine with no settable
        parameters.

        ## Configuring a PostgreSQL Cluster

        This is also the parameter catalogue for cluster configuration groups
        (`pg-cfg-…`), and **`deploy_type` decides which catalogue you get**.
        Omitting it means `single_node`, so measured on 2026-09-14:

        | PostgreSQL | omitted / `single_node` | `cluster` |
        |---|---|---|
        | 17, 16 | **0** | 25 |
        | 15 | 41 | 25 |
        | 14, 13, 12 | 40, 40, 73 | **0** |

        So 17 and 16 exist only as clusters, 14 and below only as single nodes,
        and 15 is the one version in both worlds — with a *different* set of
        parameters in each. Read `deploy_type` off the group
        (`get_relational_configuration`) and pass the same value here.

        `datastore_type` is `PostgreSQL` for a cluster; there is no separate
        cluster engine name. MySQL ignores `deploy_type` entirely (67 either
        way), so it costs nothing to pass.
        """
        if not str(datastore_version).strip():
            raise ValueError(
                "datastore_version must not be empty; take the engine and version together "
                "from list_relational_datastores."
            )
        rows = await self._fetch_params(
            datastore_type, datastore_version, deploy_type, refresh=refresh
        )
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
            deploy_type=deploy_type,
            count=len(selected),
            restart_required_count=sum(1 for p in selected if p.restart_required),
            filtered=bool(name) or restart_required is not None,
            unknown_engine=not items,
            empty_result_hint=_empty_params_hint(datastore_type, datastore_version, deploy_type)
            if not items
            else "",
            items=selected,
        )

    # ------------------------------------------------------------------
    # writes
    # ------------------------------------------------------------------

    async def create_relational_configuration(
        self,
        spec: CreateRelationalConfigurationDto = Field(
            ..., description="The configuration group to create"
        ),
    ) -> DatabaseConfiguration:
        """Create an empty relational configuration group. Free, and immediate.

        ## Requirements

        - `--allow-write` must be enabled.
        - `datastoreType` + `datastoreVersion` must be a pair from
          `list_relational_datastores`. They are **fixed at creation**: no
          update changes them, and a group cannot be attached to an instance
          running anything else.
        - `deployType` only means something for PostgreSQL. A `cluster` group
          is usable only by a PostgreSQL Cluster, a `single_node` group only by
          a standalone instance — picking the wrong one makes the group
          unattachable rather than failing here.

        ## Workflow

        1. `list_relational_datastores` → pick engine and version.
        2. This tool. The group is created **empty**: it overrides nothing
           until values are set.
        3. `list_relational_configuration_params` → see what can be set.
        4. `update_relational_configuration` → set the values.
        5. `update_relational_instance_config_group` → attach it to an instance.

        Creating a group changes no running database, so it is safe to do
        before deciding which instance will use it.
        """
        require_write(self.allow_write)
        raw = await self.client.call(
            "POST",
            f"{BASE}/create",
            service=VDB_RELATIONAL_SERVICE,
            json=spec.model_dump(exclude_none=True),
        )
        payload = unwrap_wrapped(raw)
        created = DatabaseConfiguration.from_api(payload if isinstance(payload, dict) else {})
        # The create response leaves `name` empty -- measured live, the group is
        # named correctly and a later GET shows it, but the immediate answer
        # does not echo it. Re-read so the caller gets the group as it really
        # is rather than one that looks unnamed.
        if created.id and not created.name:
            try:
                return await self.get_relational_configuration(config_id=created.id)
            except Exception:
                return created
        return created

    async def update_relational_configuration(
        self,
        config_id: str = Field(..., description="Configuration group to change"),
        spec: UpdateConfigurationDto = Field(
            ..., description="Parameter values the group should have"
        ),
    ) -> ConfigurationUpdateData:
        """Change a configuration group's parameter values. Affects every attached instance.

        ## Requirements

        - `--allow-write` must be enabled.
        - **Check what is attached first.** `get_relational_configuration`
          lists the instances using this group; all of them are affected by
          this call, including production ones. Confirm the list with the user
          before changing a group with attachments.
        - Every key must be a parameter name from
          `list_relational_configuration_params` for **this group's** engine
          and version, and every value must respect the `minimum`/`maximum` or
          `allowed_values` reported there.
        - Know which of your parameters are `restart_required` before sending.
          Those put each attached instance into `RESTART_REQUIRED`, where it
          **keeps serving the old value** until someone reboots it — so the
          change silently does not take effect until that downtime is taken.

        ## Workflow

        1. `get_relational_configuration` → read the current `values` and the
           attached `instances`.
        2. `list_relational_configuration_params` with the group's engine and
           version → check bounds and `restart_required`. Passing
           `restart_required=true` shows exactly which parameters force a
           reboot.
        3. This tool. The result names the changed parameters, those needing a
           restart, and the affected instances.
        4. `get_relational_instance` on each affected instance. If one reports
           `RESTART_REQUIRED`, tell the user a reboot is needed and let **them**
           decide when — `reboot_relational_instance` drops every connection.

        Send the full set of overrides the group should end up with: whether
        the API merges or replaces is not documented.
        """
        require_write(self.allow_write)
        validate_id(config_id, "config_id")
        body = {"id": config_id, "values": spec.values}
        raw = await self.client.call(
            "PUT", f"{BASE}/update", service=VDB_RELATIONAL_SERVICE, json=body
        )
        payload = unwrap_wrapped(raw)
        configuration = DatabaseConfiguration.from_api(
            payload if isinstance(payload, dict) else {}
        )
        # The update response is as thin as the create one: measured live it
        # comes back with `values: {}` and no `instances`, so it cannot say
        # which engine this group is for or what is attached to it. Both are
        # needed to answer the only question that matters here -- whether the
        # caller just put a running database into RESTART_REQUIRED -- so re-read
        # the group rather than reporting "nothing affected" from an empty echo.
        if not configuration.datastore_type or not configuration.instances:
            try:
                configuration = await self.get_relational_configuration(config_id=config_id)
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

    async def delete_relational_configurations(
        self,
        config_ids: list[str] = Field(
            ..., min_length=1, description="Configuration groups to delete"
        ),
    ) -> ConfigurationDeleteData:
        """Delete one or more relational configuration groups. Cannot be undone.

        ## Requirements

        - `--allow-write` must be enabled.
        - Confirm with the user first, and check each group with
          `get_relational_configuration`: a group with a non-empty `instances`
          list is in use, and the platform refuses to delete it rather than
          silently detaching it.
        - The parameter values in the group are lost. An instance that used the
          group keeps running, but the overrides are no longer reproducible
          from the platform.

        ## Workflow

        1. `list_relational_configurations` → find the ids and see what is
           attached.
        2. This tool, then confirm with `list_relational_configurations`.

        This endpoint takes a JSON **array** of ids and can delete several at
        once, so the argument is a list; each id gets its own result row.
        """
        require_write(self.allow_write)
        for config_id in config_ids:
            validate_id(config_id, "config_ids")
        raw = await self.client.call(
            "DELETE",
            f"{BASE}/delete",
            service=VDB_RELATIONAL_SERVICE,
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
