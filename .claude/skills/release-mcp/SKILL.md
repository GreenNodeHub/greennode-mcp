---
name: release-mcp
description: Use when releasing an MCP server package, bumping a version, tagging, publishing to production, or asked "release <package>" / "deploy prod" in this monorepo. Everything is release-please driven — never tag or edit versions by hand.
---

# Releasing an MCP server package

Releases are fully automated by release-please. **Never** edit versions,
CHANGELOG files, or create tags manually.

## How a release happens

1. Conventional Commit titles merged into `main` (`feat:` → minor, `fix:` →
   patch, `feat!:`/`BREAKING CHANGE:` → major once 1.0, minor while 0.x)
   accumulate into a per-package **release PR** (title:
   `chore(main): release <package> X.Y.Z`).
2. Merging that release PR:
   - bumps `pyproject.toml` + `__init__.py` (`x-release-please-version` marker)
   - regenerates the package CHANGELOG
   - creates tag `<component>-vX.Y.Z` (e.g. `vks-mcp-server-v0.5.0`)
3. The release workflow then **dispatches** `deploy.yml` for the released tag
   → **production** environment (image tag `vX.Y.Z`, component prefix
   stripped). The production environment requires reviewer approval: open the
   Actions run → **Review deployments** → approve.

## To cut a release

1. Check the open release PR (`gh pr list` — author `app/github-actions`).
2. If required checks haven't started (GITHUB_TOKEN limitation): **close &
   reopen the PR**. (Not needed when the `RELEASE_PLEASE_TOKEN` PAT secret is
   configured.)
3. Before merging, confirm the **production environment is configured**
   (Settings → Environments → production: `IMAGE_REGISTRY` variable +
   `REGISTRY_USERNAME`/`REGISTRY_PASSWORD` secrets) — otherwise the prod
   deploy fails after tagging.
4. Squash-merge the release PR. Verify the tag exists, approve the pending
   production deployment, and confirm the Deploy run succeeds
   (`gh run list --workflow=deploy.yml`).

To re-deploy a released version manually:
`gh workflow run deploy.yml --ref main -f tag=vks-mcp-server-vX.Y.Z`

## Dev deploys (no release needed)

Every merge to `main` touching a package's watched paths already deploys to
the `develop` environment automatically (image tag = commit sha).

## Version discipline

The next version is computed from commit types — if a release looks wrong,
fix the **commit/PR titles**, not the version files. Config lives in
`release-please-config.json` + `.release-please-manifest.json` (new packages
are registered automatically by `scripts/new_server.py`).
