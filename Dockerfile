FROM python:3.12-slim

# Install system deps (as root)
RUN apt-get update && apt-get install -y --no-install-recommends \
    openssl ca-certificates curl && \
    rm -rf /var/lib/apt/lists/*

RUN useradd -u 10001 -m appuser
WORKDIR /app

# Install Poetry
RUN python3 -m pip install --upgrade pip setuptools && \
    python3 -m pip install "poetry==2.1.4"

COPY ./pyproject.toml ./poetry.lock /app/
RUN poetry config virtualenvs.create false && \
    poetry install --no-root

USER appuser
