# smallFactory Web UI

A modern, clean web interface for the smallFactory Git-native PLM system.

## Features

- **Dashboard**: Inventory overview with quick stats and recent items
- **Inventory**:
  - List and view items with per-location breakdown
  - Add Stock (journal entry) with required Location SFID (`l_sfid`)
  - Adjust quantities by location
  - Note: Deleting inventory items is not supported in the journal model. Use negative adjustments instead.
- **Entities (PLM)**:
  - Create, list, and view canonical entities (SFIDs)
  - Inline editing on the entity view page (separate Edit page is deprecated)
  - Revisions: bump and release; released pointer at `entities/<sfid>/refs/released`
  - BOM: add, remove, set lines, and manage alternates; `rev: released` resolves via pointer
  - Files working area: manage under `entities/<sfid>/files/` (list, mkdir, upload, move, delete)
- **Stickers**: Batch PDF generation of QR code labels for multiple SFIDs
- **Vision (Ollama)**: Generic image Q&A and invoice part extraction
- **Modern UI**: Clean, responsive Tailwind CSS design
- **Git-native**: Optional auto-commit on writes (configurable)

## Quick Start

1. **Install dependencies**:
   ```bash
   pip3 install -r web/requirements.txt
   ```

2. **Ensure smallFactory is configured**:
   ```bash
   # Make sure you have a data repository set up
   python3 sf.py init
   ```

3. **Start the web server**:
   ```bash
   # from project root
   python3 sf.py web --port 8080
   # development mode with auto-reload
   FLASK_ENV=development python3 sf.py web --port 8080 --debug
   ```

4. **Access the interface**:
   Open your browser to `http://localhost:8080`

## Development

To run in development mode with auto-reload:

```bash
FLASK_ENV=development python3 sf.py web --port 8080 --debug
```

## Configuration

- **SF_WEB_SECRET**: Flask secret key. Defaults to an insecure dev value.
- **SF_WEB_AUTOCOMMIT**: Enable/disable Git auto-commit on writes. Default ON. Disable with `SF_WEB_AUTOCOMMIT=0`.
- **PORT** / `--port`: Port for the web server (default 8080).
- **FLASK_ENV** / `--debug`: Set `development` or pass `--debug` for auto-reload.
- **SF_OLLAMA_BASE_URL**: Base URL for the Ollama server (default `http://localhost:11434`).
- **SF_VISION_MODEL**: Vision model name (default `qwen2.5vl:3b`).
- Optional: **SF_REPO** to point to a specific data repository path (follows the same resolution as the CLI).

### Proxy-auth user identity for Git commits (web only)

When the web UI performs write operations, it will use proxy-provided headers to attribute Git commits to the active user. If headers are absent, behavior falls back to existing Git config/environment (no change for CLI).

- Default header names checked (first match wins):
  - User: `X-Forwarded-User`, `X-Auth-Request-User`
  - Email: `X-Forwarded-Email`, `X-Auth-Request-Email`
- Override header names via env (comma-separated list allowed):
  - `SF_WEB_IDENTITY_HEADER_NAME` (e.g., `SF_WEB_IDENTITY_HEADER_NAME=X-Forwarded-Preferred-User`)
  - `SF_WEB_IDENTITY_HEADER_EMAIL`
- If only an email-like value is present, the display name is derived from the local part (e.g., `jane.doe` -> `Jane Doe`).
- Applies only to web requests; the CLI continues to use the caller's local Git identity.

## Architecture

The web UI is built as a Flask application that uses the smallFactory core v1 API:

- `app.py`: Main Flask application with routes
- `templates/`: Jinja2 HTML templates
  - `base.html`: Base template with navigation and common elements
  - `index.html`: Dashboard page
  - `inventory/`: Inventory pages
  - `entities/`: Entity list/view/build and related pages
  - `stickers/`: Batch stickers UI
  - `vision.html`: Mobile-friendly camera/upload page for Vision
- `static/`: Static assets (CSS, images, JS)
- `sf.py web`: CLI entrypoint to run the development server

## API Integration

The web UI directly imports the smallFactory core v1 API for consistency with the CLI, e.g.:

```python
from smallfactory.core.v1.inventory import inventory_onhand, inventory_post
from smallfactory.core.v1.entities import (
    get_entity, create_entity, update_entity_fields,
    get_revisions, bump_revision, release_revision,
    bom_list, bom_add_line, bom_remove_line, bom_set_line, bom_alt_add, bom_alt_remove,
)
from smallfactory.core.v1.files import list_files, mkdir, rmdir, upload_file, delete_file, move_file, move_dir
from smallfactory.core.v1.stickers import generate_sticker_for_entity
from smallfactory.core.v1.vision import ask_image, extract_invoice_part
```

This ensures feature parity with the CLI while keeping storage Git-native and YAML-based. The working area root for entity files is `files/`.

## Future Extensions

The UI is designed to be extensible for additional PLM modules:

- Project tracking
- Supplier management
- Approval/change control workflows
- Reporting and analytics

Each new module can follow the same pattern with its own template directory and routes.

## Vision (Ollama)

The web UI can call a local or remote Visual LLM (VLM) hosted by Ollama. We recommend `qwen2.5vl:3b` for a lightweight, high-quality model.

### 1) Start Ollama and pull the model

macOS (Homebrew):

```bash
brew install ollama
ollama serve &
ollama pull qwen2.5vl:3b
```

Linux: install from https://ollama.com/download, then:

```bash
ollama serve &
ollama pull qwen2.5vl:3b
```

Verify the API:

```bash
curl http://localhost:11434/api/tags
```

### 2) Configure smallFactory to talk to Ollama

Defaults assume a local Ollama at `http://localhost:11434`. To override, set:

```bash
export SF_OLLAMA_BASE_URL=http://<ollama-host>:11434
export SF_VISION_MODEL=qwen2.5vl:3b
```

### 3) Install web deps and run

```bash
pip3 install -r requirements.txt
python3 sf.py web --port 8080
```

### 4) Use the Vision API

- Generic ask (prompt + image):

```bash
curl -s -X POST http://localhost:8080/api/vision/ask \
  -F "prompt=Summarize the contents of this image in 1-2 sentences." \
  -F "file=@/path/to/invoice.jpg" | jq
```

- Extract part fields from an invoice:

```bash
curl -s -X POST http://localhost:8080/api/vision/extract/part \
  -F "file=@/path/to/invoice.jpg" | jq
```

If you see an error, ensure Ollama is running and the model is pulled. You can also open the web Vision page at `/vision` for a camera/upload UI.

## Troubleshooting: Vision (Ollama)

- **Missing prompt (400)**
  - Error: `Missing prompt`
  - Fix: Include `-F "prompt=..."` in the form when calling `/api/vision/ask`.

- **No image or wrong field (400)**
  - Error: `No image file uploaded under field 'file'.`
  - Fix: Send the image as `-F "file=@/path/to/image.jpg"`.

- **Unsupported file type (400)**
  - Error: `Unsupported file type; expected an image.`
  - Fix: Upload a valid image (jpg/png). The server re-encodes to PNG.

- **Image too large (400)**
  - Error: `Image too large (max 10MB).`
  - Fix: Reduce the image size below 10MB.

- **Ollama not running or model missing (500)**
  - Symptom: 500 with hint to start/pull model.
  - Fix:
    - Start Ollama: `ollama serve`
    - Pull model: `ollama pull qwen2.5vl:3b`
    - Verify: `curl http://localhost:11434/api/tags`
  - Remote host: `export SF_OLLAMA_BASE_URL=http://<host>:11434`
  - Model name: set/confirm `SF_VISION_MODEL` (default `qwen2.5vl:3b`).

## Routes and API Reference
  
  - **UI Routes**
  - `/` — Dashboard
  - `/vision` — Vision camera/upload page
  - `/inventory` — Inventory list
  - `/inventory/<item_id>` — Inventory item view
  - `/inventory/add` — Add Stock (GET, POST)
  - `/inventory/<item_id>/edit` — Edit inventory item (GET, POST)
  - `/inventory/<item_id>/adjust` — Adjust quantity (POST)
  - `/entities` — Entities list
  - `/entities/<sfid>` — Entity view (inline editing)
  - `/entities/add` — Create entity (GET, POST)
  - `/entities/<sfid>/edit` — Deprecated; use inline editing
  - `/entities/<sfid>/retire` — Retire entity (POST)
  - `/entities/<sfid>/build` — Build flow (GET, POST)
  - `/entities/<sfid>/build/create-revision` — Create next draft revision (POST)
  - `/stickers` — Redirect to batch stickers UI
  - `/stickers/batch` — Batch stickers UI (GET, POST)

- **API Endpoints**
  - `/api/inventory` — Inventory API (GET)
  - `/api/inventory/<item_id>` — Inventory item (GET)
  - `/api/entities` — Entities API (GET)
  - `/api/entities/<sfid>` — Entity (GET)
  - `/api/entities/<sfid>/update` — Update entity fields (POST)
  - `/api/entities/<sfid>/revisions` — Get revisions and released pointer (GET)
  - `/api/entities/<sfid>/revisions/bump` — Cut and release next revision (POST)
  - `/api/entities/<sfid>/revisions/<rev>/release` — Release a specific revision (POST)
  - `/api/entities/<sfid>/bom` — BOM list (GET)
  - `/api/entities/<sfid>/bom/deep` — Resolved BOM tree (GET)
  - `/api/entities/<sfid>/bom/add` — Add BOM line (POST)
  - `/api/entities/<sfid>/bom/remove` — Remove BOM line(s) (POST)
  - `/api/entities/<sfid>/bom/set` — Update BOM line (POST)
  - `/api/entities/<sfid>/bom/alt-add` — Add alternate part (POST)
  - `/api/entities/<sfid>/bom/alt-remove` — Remove alternate part (POST)
  - `/api/entities/<sfid>/files` — List working files (GET)
  - `/api/entities/<sfid>/files/mkdir` — Create folder (POST)
  - `/api/entities/<sfid>/files/rmdir` — Remove folder (POST)
  - `/api/entities/<sfid>/files/upload` — Upload file (POST)
  - `/api/entities/<sfid>/files/delete` — Delete file (POST)
  - `/api/entities/<sfid>/files/move` — Move file/folder (POST)
  - `/api/entities/specs/<sfid>` — Entity specs (GET)
  - `/api/vision/ask` — Vision ask (POST)
  - `/api/vision/extract/part` — Extract part fields (POST)
