"""Log processor handler for the vMonitor MCP server (Log API).

Within a pipeline, a **processor group** routes logs from a source project to a
destination project, and its ordered **processors** parse/transform each log
(grok, date, ...). This handler covers processor groups, processors, the
processor-group library (reusable groups), and the grok/date helper tools.
"""

from __future__ import annotations

from greennode.vmonitor_mcp_server.client import VmonitorLogClient
from greennode.vmonitor_mcp_server.config import VmonitorConfig
from greennode.vmonitor_mcp_server.models import (
    CreateProcessorGroupDto,
    DebugGrokDto,
    LogResource,
    ProcessorDto,
    ReorderProcessorsDto,
    UpdateProcessorGroupDto,
)
from greennode.vmonitor_mcp_server.tool_annotations import DESTRUCTIVE, READ, WRITE
from greennode.vmonitor_mcp_server.validators import validate_id
from pydantic import Field


class LogProcessorHandler:
    """Register and serve vMonitor Log API processor-group / processor MCP tools."""

    def __init__(
        self, mcp, config: VmonitorConfig, client: VmonitorLogClient, allow_write: bool = False
    ):
        self.mcp = mcp
        self.config = config
        self.client = client
        self.allow_write = allow_write

        self.mcp.tool(name="get_processor_group", annotations=READ)(self.get_processor_group)
        self.mcp.tool(name="list_processor_group_libraries", annotations=READ)(
            self.list_processor_group_libraries
        )
        self.mcp.tool(name="list_date_formats", annotations=READ)(self.list_date_formats)
        self.mcp.tool(name="validate_grok_parser", annotations=READ)(self.validate_grok_parser)

        if self.allow_write:
            self.mcp.tool(name="create_processor_group", annotations=WRITE)(
                self.create_processor_group
            )
            self.mcp.tool(name="update_processor_group", annotations=WRITE)(
                self.update_processor_group
            )
            self.mcp.tool(name="delete_processor_group", annotations=DESTRUCTIVE)(
                self.delete_processor_group
            )
            self.mcp.tool(name="update_processor_order", annotations=WRITE)(
                self.update_processor_order
            )
            self.mcp.tool(name="create_processor_group_library", annotations=WRITE)(
                self.create_processor_group_library
            )
            self.mcp.tool(name="create_processor", annotations=WRITE)(self.create_processor)
            self.mcp.tool(name="update_processor", annotations=WRITE)(self.update_processor)
            self.mcp.tool(name="delete_processor", annotations=DESTRUCTIVE)(self.delete_processor)

    async def get_processor_group(
        self,
        pipeline_id: str = Field(..., description="Pipeline ID the group belongs to"),
        processor_group_id: str = Field(..., description="Processor group ID to retrieve"),
    ) -> LogResource:
        """Get a single processor group by ID (with its processors)."""
        validate_id(pipeline_id, "pipeline_id")
        validate_id(processor_group_id, "processor_group_id")
        data = await self.client.get(f"/v1/processor-groups/{pipeline_id}/{processor_group_id}")
        return LogResource.from_api(data)

    async def list_processor_group_libraries(
        self,
        query: str | None = Field(None, description="Free-text filter"),
    ) -> list[dict]:
        """List reusable processor groups saved in the library."""
        params = {"query": query} if query else {}
        data = await self.client.get("/v1/processor-group-libraries", params=params)
        return data if isinstance(data, list) else []

    async def list_date_formats(self) -> list[str]:
        """List the date formats a date processor supports."""
        data = await self.client.get("/v1/processors/formats-date")
        return [str(v) for v in data] if isinstance(data, list) else []

    async def create_processor_group(
        self,
        pipeline_id: str = Field(..., description="Pipeline ID to add the group to"),
        body: CreateProcessorGroupDto = Field(
            ...,
            description=(
                "CreateProcessorGroupDto body. Required: name, pipelineId, source, "
                "destination (project references). Optional: filter/query/processors."
            ),
        ),
    ) -> LogResource:
        """Add a processor group to a pipeline (source -> destination routing).

        ## Requirements
        - Server must run with --allow-write
        - `source` and `destination` are project references; `pipelineId` must match the path.
        """
        validate_id(pipeline_id, "pipeline_id")
        data = await self.client.post(
            f"/v1/processor-groups/{pipeline_id}", json=body.model_dump(exclude_none=True)
        )
        return LogResource.from_api(data)

    async def update_processor_group(
        self,
        pipeline_id: str = Field(..., description="Pipeline ID the group belongs to"),
        processor_group_id: str = Field(..., description="Processor group ID to update"),
        body: UpdateProcessorGroupDto = Field(
            ...,
            description="UpdateProcessorGroupDto body. Required: name, pipelineId.",
        ),
    ) -> str:
        """Update a processor group's metadata/routing.

        ## Requirements
        - Server must run with --allow-write
        """
        validate_id(pipeline_id, "pipeline_id")
        validate_id(processor_group_id, "processor_group_id")
        await self.client.put(
            f"/v1/processor-groups/{pipeline_id}/{processor_group_id}",
            json=body.model_dump(exclude_none=True),
        )
        return f"Processor group {processor_group_id} updated."

    async def delete_processor_group(
        self,
        pipeline_id: str = Field(..., description="Pipeline ID the group belongs to"),
        processor_group_id: str = Field(
            ..., description="Processor group ID to delete. IRREVERSIBLE."
        ),
    ) -> str:
        """Delete a processor group and its processors. IRREVERSIBLE.

        ## Requirements
        - Server must run with --allow-write
        - Confirm the id with get_processor_group first.
        """
        validate_id(pipeline_id, "pipeline_id")
        validate_id(processor_group_id, "processor_group_id")
        await self.client.delete(f"/v1/processor-groups/{pipeline_id}/{processor_group_id}")
        return f"Processor group {processor_group_id} deleted."

    async def update_processor_order(
        self,
        pipeline_id: str = Field(..., description="Pipeline ID the group belongs to"),
        processor_group_id: str = Field(..., description="Processor group ID to reorder"),
        body: ReorderProcessorsDto = Field(
            ...,
            description=(
                "ReorderProcessorsDto body. Required: pipelineId, processorGroupId, "
                "processors (IDs in the new order)."
            ),
        ),
    ) -> str:
        """Reorder the processors within a processor group.

        ## Requirements
        - Server must run with --allow-write
        - `processors` must list every processor ID of the group in the desired order.
        """
        validate_id(pipeline_id, "pipeline_id")
        validate_id(processor_group_id, "processor_group_id")
        await self.client.put(
            f"/v1/processor-groups/{pipeline_id}/{processor_group_id}/re-order",
            json=body.model_dump(exclude_none=True),
        )
        return f"Processor group {processor_group_id} processors reordered."

    async def create_processor_group_library(
        self,
        body: CreateProcessorGroupDto = Field(
            ...,
            description=(
                "CreateProcessorGroupDto body — saves (duplicates) a processor group "
                "into the reusable library."
            ),
        ),
    ) -> LogResource:
        """Save a processor group into the reusable library (duplicate it there).

        ## Requirements
        - Server must run with --allow-write
        """
        data = await self.client.post(
            "/v1/processor-group-libraries", json=body.model_dump(exclude_none=True)
        )
        return LogResource.from_api(data)

    async def create_processor(
        self,
        pipeline_id: str = Field(..., description="Pipeline ID the group belongs to"),
        processor_group_id: str = Field(..., description="Processor group ID to add to"),
        body: ProcessorDto = Field(
            ...,
            description=(
                "ProcessorDto body. Required: name, pipelineId, processorGroupId, "
                "parserType, parserRule. Optional: filter/query/rulePreset."
            ),
        ),
    ) -> LogResource:
        """Add a processor (parser/transform step) to a processor group.

        ## Requirements
        - Server must run with --allow-write
        - Validate a grok `parserRule` first with validate_grok_parser.
        """
        validate_id(pipeline_id, "pipeline_id")
        validate_id(processor_group_id, "processor_group_id")
        data = await self.client.post(
            f"/v1/processors/{pipeline_id}/{processor_group_id}",
            json=body.model_dump(exclude_none=True),
        )
        return LogResource.from_api(data)

    async def update_processor(
        self,
        pipeline_id: str = Field(..., description="Pipeline ID the group belongs to"),
        processor_group_id: str = Field(..., description="Processor group ID the processor is in"),
        processor_id: str = Field(..., description="Processor ID to update"),
        body: ProcessorDto = Field(
            ..., description="ProcessorDto body (same shape as create_processor)."
        ),
    ) -> str:
        """Update a processor's parser rule/config.

        ## Requirements
        - Server must run with --allow-write
        """
        validate_id(pipeline_id, "pipeline_id")
        validate_id(processor_group_id, "processor_group_id")
        validate_id(processor_id, "processor_id")
        await self.client.put(
            f"/v1/processors/{pipeline_id}/{processor_group_id}/{processor_id}",
            json=body.model_dump(exclude_none=True),
        )
        return f"Processor {processor_id} updated."

    async def delete_processor(
        self,
        pipeline_id: str = Field(..., description="Pipeline ID the group belongs to"),
        processor_group_id: str = Field(..., description="Processor group ID the processor is in"),
        processor_id: str = Field(..., description="Processor ID to delete. IRREVERSIBLE."),
    ) -> str:
        """Delete a processor from a group. IRREVERSIBLE.

        ## Requirements
        - Server must run with --allow-write
        """
        validate_id(pipeline_id, "pipeline_id")
        validate_id(processor_group_id, "processor_group_id")
        validate_id(processor_id, "processor_id")
        await self.client.delete(
            f"/v1/processors/{pipeline_id}/{processor_group_id}/{processor_id}"
        )
        return f"Processor {processor_id} deleted."

    async def validate_grok_parser(
        self,
        body: DebugGrokDto = Field(
            ...,
            description=(
                "DebugGrokDto body. Required: pattern (grok). Optional: log (sample "
                "line to test the pattern against)."
            ),
        ),
    ) -> LogResource:
        """Test a grok pattern against a sample log line (read-only debug)."""
        data = await self.client.post(
            "/v1/processors/debug-grok-parser", json=body.model_dump(exclude_none=True)
        )
        return LogResource.from_api(data)
