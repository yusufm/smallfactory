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
   python run.py
   ```

4. **Access the interface**:
   Open your browser to `http://localhost:5000`

## Development

To run in development mode with auto-reload:

```bash
FLASK_ENV=development python run.py
# or
python run.py --debug
```

## Architecture

The web UI is built as a Flask application that uses the smallFactory core v1 API:

- `app.py`: Main Flask application with routes
- `templates/`: Jinja2 HTML templates
  - `base.html`: Base template with navigation and common elements
  - `index.html`: Dashboard page
  - `inventory/`: Inventory-specific templates
- `run.py`: Development server launcher

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
