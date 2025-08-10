# smallFactory Web UI

A modern, clean web interface for the smallFactory Git-native PLM system.

## Features

- **Dashboard**: Overview of inventory with quick stats and recent items
- **Inventory Management**: 
  - List all items with search and filtering
  - View detailed item information
  - Add new inventory items with custom fields
  - Edit item metadata
  - Adjust quantities by location
  - Delete items with confirmation
- **Modern UI**: Clean, responsive design using Tailwind CSS
- **Extensible**: Built to accommodate future PLM modules beyond inventory

## Quick Start

1. **Install dependencies**:
   ```bash
   cd web
   pip install -r requirements.txt
   ```

2. **Ensure smallFactory is configured**:
   ```bash
   # Make sure you have a data repository set up
   cd ..
   python sf.py init
   ```

3. **Start the web server**:
   ```bash
   # from project root
   python sf.py web --port 8080
   # development mode with auto-reload
   FLASK_ENV=development python sf.py web --port 8080 --debug
   ```

4. **Access the interface**:
   Open your browser to `http://localhost:8080`

## Development

To run in development mode with auto-reload:

```bash
FLASK_ENV=development python sf.py web --port 8080 --debug
```

## Architecture

The web UI is built as a Flask application that uses the smallFactory core v1 API:

- `app.py`: Main Flask application with routes
- `templates/`: Jinja2 HTML templates
  - `base.html`: Base template with navigation and common elements
  - `index.html`: Dashboard page
  - `inventory/`: Inventory-specific templates
- `sf.py web`: CLI entrypoint for the development server

## API Integration

The web UI directly imports and uses the smallFactory core API:

```python
from smallfactory.core.v1.inventory import (
    list_items, view_item, add_item, 
    update_item, delete_item, adjust_quantity
)
```

This ensures consistency with the CLI interface and leverages all the Git-native features.

## Future Extensions

The UI is designed to be extensible for additional PLM modules:

- Parts/BOM management
- Project tracking
- Document management
- Supplier management
- Reporting and analytics

Each new module can follow the same pattern with its own template directory and routes.

## Vision (Qwen2‑VL via Ollama)

The web UI can call a local or remote Visual LLM (VLM) hosted by Ollama. We recommend Qwen2‑VL 2B Instruct for a lightweight, high‑quality model.

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
cd web
pip install -r requirements.txt
cd ..
python sf.py web --port 8080
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

If you see an error, ensure Ollama is running and the model is pulled.
