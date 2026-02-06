# Notes - comfy-viewer

Brief context for agents working with this package.

## Build / Run

No build step required. Pure Python with Flask.

```bash
# First time setup
cd /path/to/comfy-viewer
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp config.example.yaml config.local.yaml
# Edit config.local.yaml with your paths

# Run directly
source venv/bin/activate
./comfy-viewer

# Or via systemd
systemctl --user start comfy-viewer
systemctl --user status comfy-viewer
journalctl --user -u comfy-viewer -f
```

**Requirements:** Python 3.10+, packages in requirements.txt

## Path Dependencies

Configuration is in `config.local.yaml` (or `~/.config/comfy-viewer/config.yaml` via platformdirs). Key paths:

| Setting | Description |
|---------|-------------|
| `comfy_host` | ComfyUI server URL (default: http://127.0.0.1:8188) |
| `output_dir` | ComfyUI output directory to watch |
| `quicksaves_dir` | Where to save quicksaved images |

Environment variables override config values:
- `COMFY_VIEWER_COMFY_HOST`
- `COMFY_VIEWER_DATA_DIR`
- `COMFY_VIEWER_CACHE_DIR`
- `COMFY_VIEWER_OUTPUT_DIR`
- `COMFY_VIEWER_QUICKSAVES_DIR`

## Service File

Symlinked from package:
```bash
ln -s /path/to/comfy-viewer/comfy-viewer.service \
      ~/.config/systemd/user/comfy-viewer.service
```

Note: Service file not in repo - create locally or copy from Systems.

## Architecture

- **app.py**: Main Flask application with all routes
- **file_service.py**: File operations, thumbnails, metadata
- **file_watcher.py**: Watchdog-based directory monitoring
- **comfy_client.py**: ComfyUI WebSocket client
- **hooks/**: Extensible metadata extraction (PNG chunks, etc.)
- **subscribers/**: Event subscriber plugins (auto-discovered at startup)

## Integration Points

**Standalone mode**: Works as a gallery viewer for any ComfyUI output directory.

**With Conduit**: When [ComfyUI-Conduit](https://github.com/NickPittas/ComfyUI-Conduit) is installed:
- Workflow browser and runner
- Rich metadata (character names, semantic tags)
- Real-time updates on generation complete

See CONDUIT.md for integration details.

## Known Issues

- Thumbnail generation can be slow on first load for large libraries
- WebSocket reconnection sometimes needs manual page refresh
