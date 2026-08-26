"""Shared MCP ToolAnnotations for tool registration.

Hints let clients auto-approve read-only calls and warn before destructive
ones. Pick by effect, not by API verb: a dry-run delete is READ; deleting a
server with its volumes is DESTRUCTIVE, while starting a stopped server is a
plain WRITE.
"""

from __future__ import annotations

from mcp.types import ToolAnnotations


READ = ToolAnnotations(readOnlyHint=True)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False)
DESTRUCTIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=True)
