# smallfactory Web Interface

A lightweight, modern web interface for the smallfactory CLI tool. Provides both a user-friendly web UI and a complete REST API for inventory management.

## 🚀 Quick Start

### Option 1: Using the startup script
```bash
./start_web.sh
```

### Option 2: Manual setup
```bash
# Install dependencies
pip3 install flask pyyaml

# Start the web server
python3 web_app.py
```

The web interface will be available at: **http://localhost:8080**

## 📋 Features

### Web UI
- **Dashboard**: Overview and quick access to all features
- **Inventory Management**: View, add, edit, and delete inventory items
- **Search & Filter**: Real-time search across all inventory fields
- **Quantity Adjustments**: Quick +/- buttons and custom adjustments
- **Responsive Design**: Works on desktop, tablet, and mobile
- **Modern UI**: Clean, Bootstrap-based interface with intuitive navigation

### REST API
Complete API for programmatic access to all smallfactory features:

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/inventory` | List all inventory items |
| `GET` | `/api/inventory/{sku}` | Get specific inventory item |
| `POST` | `/api/inventory` | Add new inventory item |
| `PUT` | `/api/inventory/{sku}` | Update inventory item |
| `POST` | `/api/inventory/{sku}/adjust` | Adjust quantity (+/- delta) |
| `DELETE` | `/api/inventory/{sku}` | Delete inventory item |

### API Examples

**Add a new item:**
```bash
curl -X POST http://localhost:8080/api/inventory \
  -H "Content-Type: application/json" \
  -d '{
    "sku": "MOTOR-001",
    "name": "BLDC Motor 2205",
    "quantity": 10,
    "location": "Shelf A1",
    "notes": "High-performance racing motor"
  }'
```

**Adjust quantity:**
```bash
curl -X POST http://localhost:8080/api/inventory/MOTOR-001/adjust \
  -H "Content-Type: application/json" \
  -d '{"delta": -2}'
```

**Get all items:**
```bash
curl http://localhost:8080/api/inventory
```

## 🏗️ Architecture

The web interface is built as a lightweight Flask application that wraps the existing `sf.py` CLI tool:

- **Backend**: Flask web server with REST API endpoints
- **Frontend**: Modern HTML5/CSS3/JavaScript with Bootstrap 5
- **Data Layer**: Uses the existing CLI tool via subprocess calls
- **Storage**: All data remains in Git-tracked YAML files (no database required)

## 🔧 Configuration

The web interface automatically uses your existing smallfactory configuration:
- Reads from `.smallfactory.yml` for default data repository
- All changes are committed to Git automatically
- Supports all existing CLI features and custom fields

## 🎨 UI Features

- **Smart Search**: Search across SKU, name, location, and notes
- **Stock Level Indicators**: Color-coded quantity badges (red/yellow/green)
- **Quick Actions**: One-click quantity adjustments
- **Keyboard Shortcuts**: 
  - `Ctrl/Cmd + K`: Focus search
  - `Escape`: Close modals
- **Responsive Tables**: Mobile-friendly inventory views
- **Toast Notifications**: User-friendly success/error messages

## 🔌 Integration

The web interface is designed to work alongside the CLI tool:
- All web changes are visible in CLI and vice versa
- Same Git repository and file structure
- API can be used by external tools and scripts
- No migration required from existing CLI usage

## 🛠️ Development

To extend or customize the web interface:

1. **Templates**: HTML templates in `templates/` directory
2. **Static Assets**: CSS/JS files in `static/` directory  
3. **API Routes**: Add new endpoints in `web_app.py`
4. **Styling**: Customize appearance in `static/css/style.css`

## 📱 Mobile Support

The interface is fully responsive and works well on:
- Desktop browsers
- Tablets (iPad, Android tablets)
- Mobile phones (iOS Safari, Chrome, etc.)

## 🔒 Security Notes

- The web interface runs locally by default
- No authentication required for local use
- For production deployment, consider adding authentication
- API endpoints return JSON errors for invalid requests

## 🐛 Troubleshooting

**Port 5000 already in use?**
- The app now uses port 8080 by default
- On macOS, disable AirPlay Receiver if needed

**CLI commands not working?**
- Ensure `sf.py` is in the same directory
- Check that `.smallfactory.yml` exists (run `python3 sf.py create` first)

**Missing dependencies?**
- Run `pip3 install flask pyyaml`
- Or use the `start_web.sh` script

## 🤝 Contributing

The web interface maintains the same philosophy as smallfactory:
- Keep it simple and lightweight
- No unnecessary dependencies
- Git-native approach
- Designed for small teams (1-2 people)

Feel free to submit issues or pull requests to improve the web interface!
