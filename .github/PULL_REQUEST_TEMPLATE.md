<!--
PR title must follow Conventional Commits: feat: / fix: / docs: / chore: / feat!: ...
It becomes the squash-merge commit message and drives release automation.
-->

## Description

<!-- What & why. Link the greennode-cli command / API endpoint this mirrors, if any. -->

## Type of change

- [ ] Bug fix
- [ ] New feature / new tool
- [ ] Breaking change (tool rename, DTO field change, ...)
- [ ] Documentation / chore

## Checklist

- [ ] PR title follows Conventional Commits
- [ ] TDD: tests written first and passing (`uv run pytest tests/ -v`)
- [ ] `uv run ruff check . && uv run ruff format --check .`
- [ ] Tool names are `verb_noun` (mirror the greennode-cli command where one exists)
- [ ] Write DTOs are typed Pydantic models with `extra="forbid"`
- [ ] Docs updated: README tool tables, CHANGELOG, CLAUDE.md (per the documentation rule)
- [ ] MCP prompts updated if tool routing / fields changed

## Related issues

Closes #
