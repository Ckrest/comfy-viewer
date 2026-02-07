# Conduit Integration

comfy-viewer has optional integration with [Conduit](https://github.com/Ckrest/comfyui-conduit), a ComfyUI workflow gateway that simplifies running and managing workflows.

## What Conduit Enables

Without Conduit, comfy-viewer provides:
- Basic image gallery with file watching
- Manual image browsing and navigation
- Quicksave functionality
- Thumbnail generation
- Search and filtering

With Conduit, you also get:
- **Workflow Browser**: See all available workflows with their inputs and schemas
- **Workflow Runner**: Run workflows directly from the viewer with custom inputs
- **Rich Metadata**: Character names, prompts, and other data extracted from outputs
- **Instant Updates**: Images appear immediately when generation completes
- **Semantic Tagging**: Images tagged with CharImg, FinalImage, etc.

## Setup

### 1. Install Conduit in ComfyUI

```bash
cd /path/to/ComfyUI/custom_nodes
git clone https://github.com/Ckrest/comfyui-conduit.git
```

Restart ComfyUI after installation.

### 2. Configure comfy-viewer

Set `COMFY_VIEWER_COMFY_HOST` if ComfyUI runs on a different address:

```bash
export COMFY_VIEWER_COMFY_HOST=http://192.168.1.100:8188
```

## How It Works

### Event Flow

**Path 1 — File watching (standalone):**
1. ComfyUI saves an image to the output directory
2. File watcher (inotify) detects the new file
3. Image is registered and appears in the gallery

**Path 2 — Conduit events (via event bus or direct POST):**
1. You run a workflow via the Library page or ComfyUI directly
2. Conduit captures tagged outputs to `output/conduit/{prompt_id}/`
3. A completion event is delivered to `POST /api/conduit-event`
4. The image is registered with full metadata and appears instantly

Path 2 can be driven by any system that POSTs the correct JSON to
`/api/conduit-event`. For example, a lifecycle hook in `hooks.local/`
can subscribe to an event bus and forward matching events.

### API Endpoints

When Conduit is available, these endpoints become functional:

| Endpoint | Description |
|----------|-------------|
| `GET /api/conduit/workflows` | List available workflows |
| `GET /api/conduit/workflows/<name>` | Get workflow schema |
| `GET /api/conduit/workflows/<name>/inputs` | Get enriched inputs with defaults |
| `POST /api/conduit/run/<name>` | Run a workflow |
| `POST /api/conduit-event` | Receive completion events from Conduit |

### Hooks

Conduit outputs include rich metadata. To extract it, add a hook in `hooks/`:

```python
# hooks/my_conduit_hook.py
def extract(folder_path, current_data):
    """Extract metadata from Conduit output folder."""
    char_str_file = folder_path / "CharStr.txt"
    if char_str_file.exists():
        current_data["char_str"] = char_str_file.read_text().strip()
    return current_data
```

Hooks are loaded in alphabetical order. The built-in `_default.py` hook extracts PNG metadata.

## Troubleshooting

### "Conduit not available" error

1. **Check ComfyUI is running**: `curl http://localhost:8188/conduit/workflows`
2. **Check Conduit is installed**: Look for `conduit` in ComfyUI's custom_nodes
3. **Check the URL**: Set `COMFY_VIEWER_COMFY_HOST` if ComfyUI is on a different host/port

### Images not appearing immediately

1. **Check the event delivery**: Look for "Forwarded conduit event" in comfy-viewer logs
2. **Check Conduit handler**: Ensure Conduit's handler ran successfully (check ComfyUI console)
3. **Check the API directly**: `curl -X POST http://localhost:5000/api/conduit-event -H 'Content-Type: application/json' -d '{"prompt_id":"test","outputs":[]}'`
4. **File watching fallback**: Images saved directly to the output root still appear via inotify (but conduit subfolder images require the API path)

### Workflow inputs not loading

1. **Refresh workflows**: Click the refresh button in the Library sidebar
2. **Check workflow file**: Ensure the workflow JSON is valid
3. **Check Conduit logs**: Look for errors in ComfyUI console
