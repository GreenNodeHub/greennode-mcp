# Copyright 2026 Green Node. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# Build stage: install dependencies and the project with uv (workspace-aware)
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS uv

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_FROZEN=true

# Resolve deps first (better layer caching): root workspace pyproject + lock + member pyproject
COPY pyproject.toml uv.lock ./
COPY src/vks-mcp-server/pyproject.toml src/vks-mcp-server/pyproject.toml
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-workspace \
        --package greennode.vks-mcp-server --extra http

# Add full source and install the project. --no-editable copies the package into
# site-packages (instead of an editable .pth pointing at /app/src), so the runtime
# stage — which copies only /app/.venv — is self-contained.
COPY . /app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable --package greennode.vks-mcp-server --extra http

# Runtime stage
FROM python:3.13-slim-bookworm

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends procps \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system app \
    && useradd app -g app -d /app

COPY --from=uv --chown=app:app /app/.venv /app/.venv
COPY ./src/vks-mcp-server/docker-healthcheck.sh /usr/local/bin/docker-healthcheck.sh
RUN chmod +x /usr/local/bin/docker-healthcheck.sh

EXPOSE 8080
USER app

HEALTHCHECK --interval=60s --timeout=10s --start-period=10s --retries=3 CMD ["docker-healthcheck.sh"]
ENTRYPOINT ["vks-mcp-server"]
CMD ["--transport", "streamable-http", "--host", "0.0.0.0", "--port", "8080"]
