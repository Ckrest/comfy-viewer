# ComfyUI Viewer

A web-based image viewer and workflow manager for ComfyUI.

## Features

- **Viewer**: Single-image focus view with keyboard navigation
- **Library**: Multi-image grid for browsing your outputs
- **Real-time Updates**: New images appear automatically via file watching
- **Quicksave**: Save favorite images with one click
- **Thumbnails**: Fast browsing with cached WebP thumbnails
- **Search & Filter**: Find images by metadata
- **Hooks**: Extensible metadata extraction system

### Optional: Conduit Integration

> **Note**: The workflow runner and real-time features require [ComfyUI-Conduit](https://github.com/Ckrest/comfyui-conduit) to be installed in ComfyUI. Without it, comfy-viewer works as a standalone gallery.

With Conduit installed, you also get:
- **Workflow Runner**: Browse and run workflows directly from the UI
- **Rich Metadata**: Character names, prompts, and semantic tags (CharImg, FinalImage)
- **Instant Updates**: New images appear immediately when generation completes

See [CONDUIT.md](CONDUIT.md) for setup instructions.

## Requirements

- Python 3.10+
- ComfyUI (running and accessible)

## Installation

```bash
# Clone the repository
git clone https://github.com/Ckrest/comfy-viewer.git
cd comfy-viewer

# Create virtual environment
python -m venv venv
source venv/bin/activate  # Linux/macOS
# or: venv\Scripts\activate  # Windows

# Install the package
pip install -e .

# Copy and configure settings
cp config.example.yaml config.local.yaml
# Edit config.local.yaml with your paths
```

## Configuration

Copy `config.example.yaml` to `config.local.yaml` and configure:

```yaml
# ComfyUI connection
comfy_host: http://127.0.0.1:8188

# Directories
output_dir: /path/to/ComfyUI/output
quicksaves_dir: /path/to/quicksaves
```

### Environment Variables

These override config file values:

| Variable | Description | Default |
|----------|-------------|---------|
| `COMFY_VIEWER_COMFY_HOST` | ComfyUI server URL | `http://127.0.0.1:8188` |
| `COMFY_VIEWER_DATA_DIR` | Base directory for persistent data | platform data dir |
| `COMFY_VIEWER_CACHE_DIR` | Base directory for cache files | platform cache dir |
| `COMFY_VIEWER_OUTPUT_DIR` | Output directory path | from config file |
| `COMFY_VIEWER_QUICKSAVES_DIR` | Quicksaves directory | from config file |

## Usage

```bash
# Activate virtual environment
source venv/bin/activate

# Run the server
comfy-viewer
```

Open http://localhost:5000 in your browser.

### Pages

- **Viewer** (`/`): Single image view with navigation
- **Library** (`/library`): Grid view with workflow runner

### Keyboard Shortcuts (Viewer)

| Key | Action |
|-----|--------|
| `←` / `→` | Previous / Next image |
| `S` | Quicksave current image |
| `F` | Flag image |
| `G` | Toggle gallery settings |

## Hooks

Hooks extract metadata from images. They're Python files in the `hooks/` directory.

Example hook (`hooks/my_hook.py`):

```python
def extract(folder_path, current_data):
    """Extract custom metadata."""
    # Add your extraction logic
    current_data["my_field"] = "value"
    return current_data
```

Hooks run in alphabetical order. See `hooks/_default.py` for an example.

## Project Structure

```
comfy-viewer/
├── comfy-viewer              # CLI entry point
├── pyproject.toml            # Package metadata and dependencies
├── config.example.yaml       # Configuration template
├── start.sh                  # Quick-start script
├── src/comfy_viewer/         # Python package
│   ├── app.py                # Main Flask application
│   ├── cli.py                # CLI helpers (introspection + server)
│   ├── comfy_client.py       # ComfyUI WebSocket client
│   ├── config.py             # Configuration loader
│   ├── emit.py               # Structured event emission
│   ├── file_service.py       # File operations (local + remote)
│   ├── registrations.py      # Image registration database
│   ├── state.py              # Shared application state
│   ├── thumbnails.py         # Thumbnail generation
│   ├── version.py            # Package version
│   └── websocket_server.py   # WebSocket push to browser
├── templates/                # HTML templates
│   ├── viewer.html           # Single image view
│   └── library.html          # Grid view + workflows
├── static/                   # CSS, JS assets
├── hooks/                    # Metadata extraction hooks
│   ├── __init__.py           # Hook loader + lifecycle dispatch
│   ├── _default.py           # Default PNG metadata hook
│   └── conduit/              # Conduit metadata extraction
│       └── plugins/          # Pluggable extractors (scene_gen, etc.)
├── hooks.local/              # User/integration hooks (gitignored)
└── CONDUIT.md                # Conduit integration guide
```

## License

MIT License - see [LICENSE](LICENSE) for details.
