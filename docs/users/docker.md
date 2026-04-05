# Docker Guide

smallFactory can run cleanly in Docker, but it behaves best when you treat the
container as the runtime for both the web app and the CLI against the same
mounted data repository.

## What runs well in Docker

- Web UI on port `8080`
- MCP endpoint on `/mcp`
- CLI commands against the same persisted repo
- Git-backed writes and local commits
- Sticker PDF generation and file uploads/downloads
- Metrics endpoint on `/metrics`

## Important Docker nuances

- smallFactory is Git-native. The data repo must live on a persistent volume.
- The web app, CLI, and MCP should all point at the same mounted repo.
- `localhost` inside the container is the container, not your host machine.
  This matters for Ollama and other local services.
- Git push credentials do not automatically follow you into the container.
  Local commits work out of the box; remote push usually needs extra setup.
- Advisory file locks are used for repo and journal mutations. Prefer a local
  Docker volume or a local-disk bind mount over a network filesystem.

## Quick Start With The Official Image

Pull and start the latest stable image:

```bash
docker pull ghcr.io/yusufm/smallfactory:latest
docker run --rm -it \
  -p 8080:8080 \
  -v smallfactory-data:/data \
  ghcr.io/yusufm/smallfactory:latest
```

Then open:

- Web UI: `http://127.0.0.1:8080`
- MCP: `http://127.0.0.1:8080/mcp`

For reproducible production or team setups, prefer a pinned release tag instead
of `latest`, for example `ghcr.io/yusufm/smallfactory:v1.1.0`.

## Quick Start From Source

If you are working from a local checkout and want to build the image yourself:

```bash
docker compose up --build
```

The included [compose.yaml](../../compose.yaml) stores data in the named volume
`smallfactory-data` and is best suited for local development, source testing,
and contributors.

## Easy CLI Usage Without Entering The Running Container

Run ad hoc CLI commands against the same persisted volume:

```bash
docker run --rm -it \
  -v smallfactory-data:/data \
  ghcr.io/yusufm/smallfactory:latest \
  inventory onhand --readonly
docker run --rm -it \
  -v smallfactory-data:/data \
  ghcr.io/yusufm/smallfactory:latest \
  entities ls
docker run --rm -it \
  -v smallfactory-data:/data \
  ghcr.io/yusufm/smallfactory:latest \
  repo validate
```

From a source checkout, there is also a thin wrapper in this repo:

```bash
./scripts/sf-docker.sh inventory onhand --readonly
./scripts/sf-docker.sh entities ls
```

And from a source checkout you can use Compose directly:

```bash
docker compose run --rm smallfactory inventory onhand --readonly
docker compose run --rm smallfactory entities ls
```

The image accepts direct smallFactory commands, so you do not need
`docker exec ... bash` for normal CLI usage.

## Local Product Testing Workflow

Yes, this can be a clean workflow even when the user is working on their host
machine outside the container.

Typical pattern:

1. Keep the web app running in Docker.
2. From the host shell, run ad hoc CLI commands with `docker compose run --rm`
   or `./scripts/sf-docker.sh`.
3. If you need host files such as test logs, firmware artifacts, or photos,
   mount an extra host path into the one-off CLI container and reference that
   mounted path.

Example:

```bash
docker run --rm -it \
  -v smallfactory-data:/data \
  -v "$PWD:/work" \
  ghcr.io/yusufm/smallfactory:latest \
  entities files add p_widget /work/test-output/log.txt logs/test-output.txt
```

That keeps SmallFactory's repo state in Docker while still letting the user work
from the host machine where the physical-product tooling is actually running.

## Existing Repo Or First-Time Repo

By default the container uses:

- config: `/data/.smallfactory.yml`
- repo: `/data/datarepo`

If `/data/datarepo` is empty:

- `SF_REPO_GIT_URL` clones a repo into that path on first start
- otherwise smallFactory initializes a fresh repo there

Examples:

```bash
docker run --rm -it \
  -p 8080:8080 \
  -e SF_REPO_GIT_URL=https://github.com/you/your-datarepo.git \
  -v smallfactory-data:/data \
  ghcr.io/yusufm/smallfactory:latest
```

If your repo should live at a different path, mount its parent and set
`SF_REPO_PATH` accordingly.

## Bind Mounts And Permissions

Named volumes are the least fragile option.

If you prefer a host bind mount, the main risk is file ownership mismatch
between the host directory and the container user. On Linux, a common pattern is:

```bash
docker run --rm -it \
  --user "$(id -u):$(id -g)" \
  -p 8080:8080 \
  -v "$PWD/docker-data:/data" \
  ghcr.io/yusufm/smallfactory:latest
```

## MCP In Docker

The container starts smallFactory through `sf web`, so MCP is available on the
same port as the web UI:

- `http://127.0.0.1:8080/mcp`

That matches the local non-Docker behavior and keeps docs/client setup the same.

## Vision / Ollama In Docker

The default Ollama URL is `http://localhost:11434`, which usually does not work
from inside a container unless Ollama is running in the same container.

Typical host-machine setup:

```bash
docker run --rm -it \
  -p 8080:8080 \
  -e SF_VISION_PROVIDER=ollama \
  -e SF_OLLAMA_BASE_URL=http://host.docker.internal:11434 \
  -v smallfactory-data:/data \
  ghcr.io/yusufm/smallfactory:latest
```

On Linux, you may also need:

```bash
--add-host=host.docker.internal:host-gateway
```

If you use OpenRouter instead, pass `SF_OPENROUTER_API_KEY` and related env vars
as normal.

## Git Pushes From Docker

Local commits work with the repo inside the mounted volume.

Remote pushes are the part that needs care:

- HTTPS remotes usually need a token or credential helper in the container
- SSH remotes usually need mounted keys or forwarded agent access
- If you do not need container-side pushes, local commits still preserve history

For many teams, the simplest first Docker setup is:

- keep local commits enabled
- leave push credentials out of the container initially
- add credentials only when remote push from the web app is required
