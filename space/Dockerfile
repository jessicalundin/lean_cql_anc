# Stage 1: build Google CQL binary
FROM golang:1.22 AS go-builder
RUN go install github.com/google/cql/cmd/cql@latest

# Stage 2: main image
FROM python:3.11-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
COPY --from=go-builder /go/bin/cql /usr/local/bin/google-cql

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    git \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Lean 4 via elan (binary built live in demo)
ENV ELAN_HOME=/root/.elan
ENV PATH="/root/.elan/bin:${PATH}"
RUN curl -fsSL https://raw.githubusercontent.com/leanprover/elan/master/elan-init.sh \
    | sh -s -- -y --default-toolchain none
# Pre-fetch the toolchain version declared in lean-toolchain so the demo build is fast
COPY lean/lean-toolchain /tmp/lean-toolchain
RUN elan toolchain install "$(cat /tmp/lean-toolchain)"

WORKDIR /app

# Python deps
ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Lean project (sources only — lake build runs in demo)
COPY lean/ /app/lean/

# App, fixtures, CQL, scripts
COPY fixtures/ /app/fixtures/
COPY cql/ /app/cql/
COPY app.py README.md package.json ./

EXPOSE 7860
ENV PATH="/app/.venv/bin:${PATH}"

CMD ["uv", "run", "python", "app.py"]
