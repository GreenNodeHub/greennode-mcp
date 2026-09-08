"""Shared MCP ToolAnnotations for tool registration.

Hints let clients auto-approve read-only calls and warn before destructive
ones. Pick by effect, not by API verb: a dry-run validation is READ; replacing
a pool's whole member list is DESTRUCTIVE even though the API calls it an
update, while resizing a load balancer is a plain WRITE.
"""

from __future__ import annotations

from mcp.types import ToolAnnotations


READ = ToolAnnotations(readOnlyHint=True)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False)
DESTRUCTIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=True)
