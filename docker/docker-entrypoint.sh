#!/bin/bash
set -e

# Start Ollama in the background as root (required)
echo "Starting Ollama server..."
ollama serve &

# Wait for Ollama to be ready
echo "Waiting for Ollama to be ready..."
max_attempts=15
attempt=0
while ! curl -s http://localhost:11434/api/tags > /dev/null; do
    if [ $attempt -ge $max_attempts ]; then
        echo "ERROR: Ollama failed to start within 30 seconds"
        exit 1
    fi
    sleep 2
    attempt=$((attempt + 1))
done

echo "Ollama is ready! Model ${SF_VISION_MODEL} is available."

# Handle git repository setup
if [ ! -z "$SF_GIT_REPO_URL" ] && [ ! -d "/app/data/.git" ]; then
    echo "Cloning git repository from $SF_GIT_REPO_URL..."
    git clone "$SF_GIT_REPO_URL" /app/data
elif [ -d "/app/data" ] && [ ! -d "/app/data/.git" ]; then
    echo "No git repository found in /app/data. You may want to initialize one or set SF_GIT_REPO_URL."
fi

# Set git configuration if provided
if [ ! -z "$SF_GIT_USER_NAME" ] && [ ! -z "$SF_GIT_USER_EMAIL" ]; then
    echo "Configuring git user..."
    git config --global user.name "$SF_GIT_USER_NAME"
    git config --global user.email "$SF_GIT_USER_EMAIL"
fi

echo "Starting smallFactory web application as appuser..."
# Switch to non-root user for the web application
cd /app
exec su-exec appuser "$@"