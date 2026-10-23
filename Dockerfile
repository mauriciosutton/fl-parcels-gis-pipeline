# syntax=docker/dockerfile:1
# Florida parcels -> PMTiles: the Python pipeline plus tippecanoe, in one image.
#   docker build -t flparcels .
#   docker run --rm -v /srv/fl-parcels:/data flparcels run --zip /data/Cadastral_Statewide.zip

# Both stages share a base so the tippecanoe binary links against the same glibc it runs on.
ARG PYTHON_IMAGE=python:3.12-slim-trixie

FROM ${PYTHON_IMAGE} AS tippecanoe
ARG TIPPECANOE_VERSION=2.79.0
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential ca-certificates curl libsqlite3-dev zlib1g-dev \
 && rm -rf /var/lib/apt/lists/*
RUN curl -fsSL "https://github.com/felt/tippecanoe/archive/refs/tags/${TIPPECANOE_VERSION}.tar.gz" | tar -xz -C /tmp \
 && make -C "/tmp/tippecanoe-${TIPPECANOE_VERSION}" -j"$(nproc)" \
 && make -C "/tmp/tippecanoe-${TIPPECANOE_VERSION}" install PREFIX=/opt/tippecanoe

FROM ${PYTHON_IMAGE}
RUN apt-get update \
 && apt-get install -y --no-install-recommends libsqlite3-0 libstdc++6 zlib1g \
 && rm -rf /var/lib/apt/lists/*
COPY --from=tippecanoe /opt/tippecanoe/bin/ /usr/local/bin/
COPY --from=ghcr.io/astral-sh/uv:0.11.15 /uv /usr/local/bin/uv

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv
# Dependencies first so code changes don't reinstall them.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

ENV PATH=/opt/venv/bin:$PATH FLPARCELS_DATA_DIR=/data
VOLUME /data
ENTRYPOINT ["python", "-m", "flparcels"]
CMD ["--help"]
