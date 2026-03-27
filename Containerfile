FROM ghcr.io/astral-sh/uv:bookworm-slim AS builder

ENV UV_NO_DEV=1
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
ENV UV_PYTHON_INSTALL_DIR=/python
ENV UV_PYTHON_CACHE_DIR=/root/.cache/uv/python
ENV UV_PYTHON_PREFERENCE=only-managed

RUN uv python install 3.11

# Install dependencies
WORKDIR /app
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project

# Copy the project into the image
COPY . /app

# Sync the project
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked


FROM debian:bookworm-slim

# Copy files
COPY --from=builder /python /python
COPY --from=builder /app /app

ENV PATH="/app/.venv/bin:$PATH"
WORKDIR /app
CMD ["python", "main.py"]
