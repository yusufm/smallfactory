# Multi-stage build for smallfactory PLM/BOM management tool
FROM python:3.11-slim as base

# Install system dependencies including curl for Ollama
RUN apt-get update && apt-get install -y \
    git \
    curl \
    su-exec \
    && rm -rf /var/lib/apt/lists/*

# Install Ollama
RUN curl -fsSL https://ollama.com/install.sh | sh

# Pre-download the vision model during build to avoid runtime download
RUN ollama serve & \
    server_pid=$! && \
    sleep 5 && \
    ollama pull qwen2.5vl:3b && \
    kill $server_pid && \
    wait

# Set working directory
WORKDIR /app

# Copy requirements and install Python dependencies
COPY requirements.txt ./
COPY web/requirements.txt ./web/
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install --no-cache-dir -r web/requirements.txt
RUN pip install --no-cache-dir gunicorn

# Copy application code
COPY . .

# Copy and set permissions for entrypoint script
COPY docker-entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# Create a non-root user for security (but don't switch to it yet, Ollama needs root)
RUN groupadd -r appuser && useradd -r -g appuser appuser
RUN chown -R appuser:appuser /app

# Expose port for web interface only (Ollama is internal)
EXPOSE 8080

# Environment variables
ENV PYTHONPATH=/app
ENV FLASK_ENV=production
ENV SF_OLLAMA_BASE_URL=http://localhost:11434
ENV SF_VISION_MODEL=qwen2.5vl:3b

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
  CMD python -c "import requests; requests.get('http://localhost:8080')" || exit 1

# Set entrypoint to start Ollama and then run Gunicorn
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "4", "--timeout", "120", "web.app:app"]