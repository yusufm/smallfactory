# syntax=docker/dockerfile:1
FROM python:3.11-slim

# Set environment
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8080 \
    SF_REPO_ROOT=/datarepos \
    SF_REPO_NAME=datarepo \
    SF_CONFIG_PATH=/datarepos/.smallfactory.yml \
    SF_REPO_PATH=/datarepos/datarepo

# Install system dependencies (git, curl for healthcheck)
RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Create app directory
WORKDIR /app

# Copy only requirements first for better layer caching
COPY requirements.txt ./requirements.txt
COPY web/requirements.txt ./web/requirements.txt

# Install Python dependencies
RUN pip install --upgrade pip && \
    pip install -r requirements.txt -r web/requirements.txt

# Copy the rest of the source
COPY . .

# Create a non-root user and ensure writable dirs
RUN useradd -u 10001 -m sf && \
    mkdir -p /datarepos /datarepos/datarepo && \
    chown -R sf:sf /app /datarepos

# Copy entrypoint
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

USER sf

EXPOSE 8080

# Healthcheck: basic GET on root (adjust as needed)
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -fsS http://127.0.0.1:${PORT}/ || exit 1

ENTRYPOINT ["/entrypoint.sh"]
