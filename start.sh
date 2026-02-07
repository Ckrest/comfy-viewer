#!/bin/bash
# Start ComfyUI Viewer

cd "$(dirname "$0")"

# Create venv if needed
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
    source venv/bin/activate
    pip install -e .
else
    source venv/bin/activate
fi

# Run the app
comfy-viewer
