#!/bin/bash

# smallfactory Web Interface Startup Script

echo "🏭 Starting smallfactory Web Interface..."

# Check if virtual environment exists
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

# Activate virtual environment
source venv/bin/activate

# Install/upgrade dependencies
echo "Installing dependencies..."
pip install -r requirements.txt

# Check if smallfactory is initialized
if [ ! -f ".smallfactory.yml" ]; then
    echo "⚠️  Warning: smallfactory not initialized."
    echo "   Run 'python3 sf.py create' first to set up your data repository."
    echo "   Or use the web interface to create one."
fi

echo "🚀 Starting web server at http://localhost:5000"
echo "   Press Ctrl+C to stop"
echo ""

# Start the web application
python3 web_app.py
