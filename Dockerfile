# One Dockerfile, two targets (docker-compose.yml picks one per service):
#   runtime - what a word count server runs: the service code only
#   dev     - runtime + benchmark harness, plotting and tests (the client/tools role)
#
# Base image: python:3.12-slim. The assignment's example base, python:3.9-slim-buster,
# no longer builds: Debian Buster is end-of-life, its package repositories moved to
# archive.debian.org, and `apt-get update` fails. This project needs no apt packages.

FROM python:3.12-slim AS runtime

# PYTHONUNBUFFERED: log lines reach `docker compose logs` immediately instead of
#   sitting in a buffer (the Phase 3/4 terminal screenshots depend on this).
# PYTHONDONTWRITEBYTECODE / PIP_*: no .pyc files or pip cache baked into the image.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies before code: editing code does not invalidate the cached pip layer.
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY wordcount/ wordcount/

EXPOSE 18861
CMD ["python", "-m", "wordcount.server"]


FROM runtime AS dev

COPY requirements-dev.txt .
RUN pip install -r requirements-dev.txt
COPY pyproject.toml .
COPY bench/ bench/
COPY scripts/ scripts/
COPY tests/ tests/

# Without an explicit command, the dev image runs the test suite.
CMD ["python", "-m", "pytest"]
