# The api (JSON routes + web pages) as a container image. Used by docker-compose.yml.
#
# On start it brings the database tables up to date (alembic upgrade head), then runs the server.

FROM python:3.13-slim

# uv, the same tool used outside Docker. Pinned so builds are repeatable.
COPY --from=ghcr.io/astral-sh/uv:0.12.24 /uv /uvx /bin/

# Use the Python in this image (never download another), and copy files into the
# virtual environment instead of linking (the uv cache isn't kept in the image).
ENV UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Step 1: the third-party packages only. This layer is rebuilt only when uv.lock or a
# pyproject.toml changes, so editing our own code doesn't reinstall FastAPI etc.
COPY pyproject.toml uv.lock ./
COPY packages/engine/pyproject.toml packages/engine/
COPY apps/cli/pyproject.toml apps/cli/
COPY apps/api/pyproject.toml apps/api/
RUN uv sync --frozen --no-dev --no-install-workspace

# Step 2: our own code (engine, CLI incl. the example day files for `seed`, api).
COPY alembic.ini ./
COPY packages/engine packages/engine
COPY apps/cli apps/cli
COPY apps/api apps/api
RUN uv sync --frozen --no-dev

# Commands from the virtual environment (alembic, uvicorn, seed, check) work without `uv run`.
ENV PATH="/app/.venv/bin:$PATH"

# Don't run as root inside the container.
RUN useradd --create-home --uid 1000 app
USER app

EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn jiak_api.main:app --host 0.0.0.0 --port 8000"]
