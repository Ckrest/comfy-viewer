"""
ComfyUI Viewer - Main Application

A clean, real-time interface for ComfyUI with:
- WebSocket-based state synchronization
- Single source of truth architecture
- Gallery viewer and settings editor
"""

import json
import logging
import os
import random
import shutil
import uuid
from collections import OrderedDict
from pathlib import Path

import yaml
from flask import Flask, jsonify, request, render_template, send_from_directory, Response

from state import get_state_manager
from comfy_client import get_comfy_client
from websocket_server import init_socketio
from file_watcher import start_watching, stop_watching
from thumbnails import get_thumbnail, get_thumbnail_for_bytes, generate_all_thumbnails, get_cache_stats, cleanup_orphaned_thumbnails, CACHE_DIR
from registrations import get_store, select_preferred_image, get_relative_image_path
from subscribers import start_subscribers, stop_subscribers
from file_service import get_file_service, reset_file_service, FileService

# ─────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).parent.resolve()

DEFAULT_CONFIG = {
    "host": "0.0.0.0",
    "port": 5000,
    "comfy_host": os.getenv("COMFY_HOST", "http://127.0.0.1:8188"),
    "templates_dir": "workflows",
    "quicksaves_dir": "quicksaves",
    "output_dir": "output",
    "randomize_seed": True,
    # File backend: "local" or "remote"
    "file_backend": "local",
    # Remote server URL (only used when file_backend is "remote")
    "remote_url": None,
    # Polling interval in seconds (for remote backend change detection)
    "poll_interval": 2.0,
}

# Conduit integration messages (centralized for easy customization)
# Conduit is required for workflow runner and real-time features.
# Install from: https://github.com/NickPittas/ComfyUI-Conduit
CONDUIT_MESSAGES = {
    "error": "Conduit not available",
    "hint": "Install ComfyUI-Conduit to enable workflow features: https://github.com/NickPittas/ComfyUI-Conduit",
    "docs": "See CONDUIT.md for detailed setup instructions.",
}


def load_config() -> dict:
    """Load configuration from settings.yaml."""
    config = DEFAULT_CONFIG.copy()
    config_path = BASE_DIR / "settings.yaml"

    if config_path.exists():
        with open(config_path, "r") as f:
            user_config = yaml.safe_load(f) or {}
        config.update(user_config)

    # Resolve relative paths
    for key in ["templates_dir", "quicksaves_dir", "output_dir"]:
        path = Path(config[key])
        if not path.is_absolute():
            config[key] = str(BASE_DIR / path)

    return config


CONFIG = load_config()

# ─────────────────────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s"
)
log = logging.getLogger("comfy-viewer")

# ─────────────────────────────────────────────────────────────
# Flask App
# ─────────────────────────────────────────────────────────────

app = Flask(__name__)
socketio = init_socketio(app)

# Global instances
state = get_state_manager()
comfy = get_comfy_client(CONFIG["comfy_host"])
file_service: FileService = get_file_service(CONFIG)

# ─────────────────────────────────────────────────────────────
# Display Field Mapping
# ─────────────────────────────────────────────────────────────

def map_display_fields(reg: dict) -> dict:
    """
    Map extracted fields to title/data display slots based on config.

    The config determines which hook fields map to which UI slots,
    making it easy to change what's displayed without code changes.
    """
    display_cfg = CONFIG.get("display", {})
    title_cfg = display_cfg.get("title", {"field": "char_str", "label": "Title"})
    data_cfg = display_cfg.get("data", {"field": "prompt", "label": "Data"})

    def get_field_value(field_name: str):
        """Get field value from char_str column or data blob."""
        if field_name == "char_str":
            return reg.get("char_str")
        # Look in the data blob for other fields
        data_blob = reg.get("data", {}) or {}
        return data_blob.get(field_name)

    # Add mapped display fields to registration
    reg["title"] = {
        "value": get_field_value(title_cfg.get("field", "char_str")),
        "label": title_cfg.get("label", "Title"),
    }
    reg["data"] = {
        "value": get_field_value(data_cfg.get("field", "prompt")),
        "label": data_cfg.get("label", "Data"),
    }

    return reg


def map_display_fields_list(registrations: list[dict]) -> list[dict]:
    """Apply display field mapping to a list of registrations."""
    return [map_display_fields(reg) for reg in registrations]


# ─────────────────────────────────────────────────────────────
# Template & Settings Helpers
# ─────────────────────────────────────────────────────────────

TEMPLATES_DIR = Path(CONFIG["templates_dir"])
OUTPUT_DIR = Path(CONFIG["output_dir"])
QUICKSAVES_DIR = Path(CONFIG["quicksaves_dir"])


def scan_templates() -> list[str]:
    """Scan for available workflow templates (via file service)."""
    return file_service.list_templates()


def load_template_settings(template: str) -> list[dict]:
    """Load settings for a template (via file service)."""
    settings = file_service.get_template_settings(template)
    return settings if settings is not None else []


def save_template_settings(template: str, settings: list[dict]):
    """Save settings for a template (via file service)."""
    file_service.save_template_settings(template, settings)


def load_template_graph(template: str) -> dict:
    """Load the workflow graph for a template (via file service)."""
    graph = file_service.get_template_graph(template)
    return graph if graph is not None else {}


def apply_settings_to_graph(graph: dict, settings: list[dict]) -> dict:
    """Apply settings values to a workflow graph."""
    WIDGET_TYPES = {"INT", "FLOAT", "STRING", "COMBO", "BOOLEAN"}

    try:
        max_id = max(int(k) for k in graph.keys())
    except (ValueError, TypeError):
        max_id = 0
    next_node_id = max_id + 1000

    for setting in settings:
        if setting.get("side") != "input" or setting.get("value") is None:
            continue

        node_id = setting.get("node_id")
        input_name = setting.get("internalName")
        value = setting.get("value")
        input_type = setting.get("type")

        if not all([node_id, input_name, input_type]):
            continue

        if input_type in WIDGET_TYPES:
            if node_id in graph and "inputs" in graph[node_id]:
                graph[node_id]["inputs"][input_name] = value
        else:
            # Create a conduit node for non-widget types
            safe_type = "".join(c if c.isalnum() else "_" for c in str(input_type))
            new_node = {
                "inputs": {"value": value},
                "class_type": f"ConduitInput_{safe_type}",
                "_meta": {"title": f"ConduitInput_{safe_type}"},
            }
            new_id = str(next_node_id)
            graph[new_id] = new_node
            if node_id in graph and "inputs" in graph[node_id]:
                graph[node_id]["inputs"][input_name] = [new_id, 0]
            next_node_id += 1

    return graph


def randomize_seeds(settings: list[dict]):
    """Randomize seed values in settings (in-place)."""
    for setting in settings:
        name = str(setting.get("name", "")).lower()
        internal = str(setting.get("internalName", "")).lower()
        if name == "seed" or internal == "seed":
            setting["value"] = random.randrange(0, 2**64)


def scan_images(offset: int = 0, limit: int = 50) -> tuple[list[dict], int]:
    """Scan output directory for images."""
    if not OUTPUT_DIR.exists():
        return [], 0

    all_images = []
    for p in OUTPUT_DIR.iterdir():
        if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
            stat = p.stat()
            all_images.append({
                "filename": p.name,
                "size": stat.st_size,
                "modified": int(stat.st_mtime),
            })

    # Sort by modification time (newest first)
    all_images.sort(key=lambda x: x["modified"], reverse=True)
    total = len(all_images)

    # Apply pagination
    return all_images[offset:offset + limit], total


# ─────────────────────────────────────────────────────────────
# Page Routes
# ─────────────────────────────────────────────────────────────

@app.route("/")
def viewer():
    """Viewer page - single image focus view."""
    return render_template("viewer.html")


@app.route("/library")
def library():
    """Library page - multi-image grid, workflow runner, and settings."""
    return render_template("library.html")


@app.route("/settings")
def settings_redirect():
    """Redirect old /settings URL to /library for backwards compatibility."""
    from flask import redirect
    return redirect("/library", code=301)


@app.route("/static/<path:filename>")
def static_files(filename):
    """Serve static files."""
    return send_from_directory("static", filename)


@app.route("/images/<path:filename>")
def serve_image(filename):
    """
    Serve images from output directory.

    Uses the file service abstraction, so this works identically
    whether files are local or remote.
    """
    if not file_service.image_exists(filename):
        return "Not found", 404

    # Stream the image through the file service
    content_type = file_service.get_content_type(filename)

    def generate():
        for chunk in file_service.stream_image(filename):
            yield chunk

    return Response(generate(), mimetype=content_type)


@app.route("/thumbnails/<path:filename>")
def serve_thumbnail(filename):
    """
    Serve thumbnails for images.

    Generates on-demand if not cached. Works with both local and remote
    file backends - for remote, downloads the image first to generate thumbnail.
    """
    if not file_service.image_exists(filename):
        return "Image not found", 404

    # For local backend, use the fast path with direct file access
    local_path = file_service.get_image_path(filename)

    if local_path:
        # Local mode: direct thumbnail generation from file path
        thumb_path = get_thumbnail(local_path)
        if thumb_path and thumb_path.exists():
            return send_from_directory(thumb_path.parent, thumb_path.name)
        else:
            # Fallback to streaming original
            return Response(
                file_service.stream_image(filename),
                mimetype=file_service.get_content_type(filename)
            )
    else:
        # Remote mode: get image bytes and generate thumbnail from memory
        image_data = file_service.get_image(filename)
        if image_data is None:
            return "Failed to fetch image", 500

        thumb_data = get_thumbnail_for_bytes(filename, image_data)
        if thumb_data:
            return Response(thumb_data, mimetype="image/webp")
        else:
            # Fallback to original image
            return Response(image_data, mimetype=file_service.get_content_type(filename))


# ─────────────────────────────────────────────────────────────
# API Routes
# ─────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────
# File Service API - Endpoints for remote file access
# These endpoints allow remote comfy-viewer instances to access files
# ─────────────────────────────────────────────────────────────

@app.route("/api/files/list")
def api_files_list():
    """
    List all images in the output directory.

    Query params:
        subdir: Optional subdirectory to list

    Returns:
        {"images": [ImageInfo...]}
    """
    subdir = request.args.get("subdir", "")
    images = file_service.list_images(subdir)
    return jsonify({"images": [img.to_dict() for img in images]})


@app.route("/api/files/image/<path:filename>")
def api_files_image(filename):
    """
    Get image data for remote access.

    This endpoint is used by RemoteFileService to fetch images.
    """
    if not file_service.image_exists(filename):
        return "Not found", 404

    content_type = file_service.get_content_type(filename)

    def generate():
        for chunk in file_service.stream_image(filename):
            yield chunk

    return Response(generate(), mimetype=content_type)


@app.route("/api/files/info/<path:filename>")
def api_files_info(filename):
    """
    Get metadata for a specific image.

    Returns:
        ImageInfo as JSON, or 404 if not found
    """
    info = file_service.get_image_info(filename)
    if info is None:
        return "Not found", 404
    return jsonify(info.to_dict())


@app.route("/api/files/exists/<path:filename>")
def api_files_exists(filename):
    """Check if an image exists."""
    exists = file_service.image_exists(filename)
    return jsonify({"exists": exists, "filename": filename})


@app.route("/api/files/backend")
def api_files_backend():
    """Get information about the current file backend."""
    return jsonify({
        "backend": file_service.get_backend_type(),
        "output_dir": str(OUTPUT_DIR),
    })


@app.route("/api/thumbnails/stats")
def api_thumbnail_stats():
    """Get thumbnail cache statistics."""
    stats = get_cache_stats()
    return jsonify(stats)


@app.route("/api/thumbnails/cleanup", methods=["POST"])
def api_thumbnail_cleanup():
    """
    Clean up orphaned thumbnails.

    POST body (optional):
    {
        "dry_run": true  // Just report what would be deleted
    }
    """
    data = request.get_json() or {}
    dry_run = data.get("dry_run", False)

    result = cleanup_orphaned_thumbnails(
        OUTPUT_DIR,
        recursive=True,
        dry_run=dry_run
    )

    return jsonify(result)


@app.route("/api/registrations/cleanup", methods=["POST"])
def api_registration_cleanup():
    """
    Clean up orphaned registrations (images that no longer exist on disk).

    POST body (optional):
    {
        "dry_run": true  // Just report what would be deleted
    }

    Returns:
    {
        "deleted": 5,           // Number of registrations deleted
        "orphaned": [...],      // List of image paths that were orphaned
        "dry_run": false
    }
    """
    data = request.get_json() or {}
    dry_run = data.get("dry_run", False)

    store = get_store()
    result = store.cleanup_orphaned(OUTPUT_DIR, dry_run=dry_run)

    if result["deleted"] > 0:
        # Refresh the image list for connected clients
        registrations, total = store.get_all(0, 50)
        state.set_images(registrations, total)

    return jsonify(result)


@app.route("/api/health")
def api_health():
    """Health check - also updates state."""
    connected = comfy.health_check()
    return jsonify({"status": "healthy" if connected else "unhealthy"})


@app.route("/api/config", methods=["GET", "POST"])
def api_config():
    """Get or update application configuration."""
    global CONFIG, OUTPUT_DIR, QUICKSAVES_DIR

    if request.method == "GET":
        # Return current configuration (user-facing paths)
        return jsonify({
            "output_dir": str(OUTPUT_DIR),
            "quicksaves_dir": str(QUICKSAVES_DIR),
            "comfy_host": CONFIG.get("comfy_host", ""),
        })

    else:  # POST
        data = request.get_json() or {}
        config_path = BASE_DIR / "settings.yaml"

        # Load existing config
        existing = {}
        if config_path.exists():
            with open(config_path, "r") as f:
                existing = yaml.safe_load(f) or {}

        # Update with new values
        updated = False

        if "output_dir" in data:
            new_path = Path(data["output_dir"])
            if new_path.exists() and new_path.is_dir():
                existing["output_dir"] = str(new_path)
                OUTPUT_DIR = new_path
                updated = True
            else:
                return jsonify({"error": f"Directory not found: {data['output_dir']}"}), 400

        if "quicksaves_dir" in data:
            new_path = Path(data["quicksaves_dir"])
            # Create if doesn't exist
            new_path.mkdir(parents=True, exist_ok=True)
            existing["quicksaves_dir"] = str(new_path)
            QUICKSAVES_DIR = new_path
            updated = True

        if updated:
            # Save config
            with open(config_path, "w") as f:
                yaml.dump(existing, f, default_flow_style=False)

            # Restart file watcher with new output dir
            stop_watching()
            start_watching(OUTPUT_DIR, on_new_image, delete_callback=on_deleted_image)

            log.info(f"Config updated - output_dir: {OUTPUT_DIR}, quicksaves_dir: {QUICKSAVES_DIR}")

        return jsonify({
            "success": True,
            "output_dir": str(OUTPUT_DIR),
            "quicksaves_dir": str(QUICKSAVES_DIR),
        })


@app.route("/api/client-config")
def api_client_config():
    """
    Get configuration values needed by the frontend.

    This endpoint provides dynamic config to avoid hardcoded values in templates.
    Values are safe to expose to the browser (no secrets).

    Note: comfy_url is NOT exposed - all ComfyUI access goes through proxy endpoints.
    This ensures the browser only connects to comfy-viewer, never directly to ComfyUI.
    """
    return jsonify({
        "thumbnail_size": 256,
        "version": "1.0.0",
    })


# Default gallery UI visibility settings
# States: "visible" (always shown), "hidden" (show on interact), "off" (never shown)
GALLERY_UI_DEFAULTS = {
    "nav_buttons": "hidden",
    "action_buttons": "hidden",
    "position_badge": "hidden",
    "settings_button": "visible",
    "gallery_settings_button": "hidden",
    "execution_block": "hidden",
    "title_overlay": "visible",
    "data_overlay": "hidden",
    "progress_bar": "hidden",
}


@app.route("/api/settings", methods=["GET", "POST"])
def api_settings():
    """Get or update application settings."""
    store = get_store()

    if request.method == "POST":
        # Save settings to database
        data = request.get_json() or {}
        store.set_setting("app_settings", json.dumps(data))
        return jsonify({"success": True})

    # GET - return current settings (merged: defaults < config file < database overrides)
    saved_json = store.get_setting("app_settings")
    saved = json.loads(saved_json) if saved_json else {}

    return jsonify({
        # Current server values (from config file)
        "output_dir": str(OUTPUT_DIR),
        "quicksaves_dir": str(QUICKSAVES_DIR),
        "comfy_host": CONFIG.get("comfy_host", "http://127.0.0.1:8188"),
        "server_port": CONFIG.get("port", 5000),
        # Include any saved overrides
        **saved
    })


@app.route("/api/browse-folder")
def api_browse_folder():
    """Open a folder picker dialog and return selected path."""
    import subprocess

    # Try tkinter first (cross-platform)
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        path = filedialog.askdirectory(title="Select Folder", parent=root)
        root.destroy()
        if path:
            return jsonify({"path": path})
        return jsonify({"path": None})  # User cancelled
    except Exception:
        pass  # tkinter not available, try platform-specific tools

    # Fall back to Linux-specific tools (zenity/kdialog)
    for cmd in [
        ["zenity", "--file-selection", "--directory", "--title=Select Folder"],
        ["kdialog", "--getexistingdirectory", "."],
    ]:
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if result.returncode == 0:
                path = result.stdout.strip()
                if path:
                    return jsonify({"path": path})
            break  # User cancelled
        except FileNotFoundError:
            continue  # Try next dialog
        except subprocess.TimeoutExpired:
            break

    return jsonify({"path": None})


@app.route("/api/restart", methods=["POST"])
def api_restart():
    """Restart the comfy-viewer service."""
    import subprocess
    import threading
    import sys
    import os

    def do_restart():
        import time

        time.sleep(0.5)  # Let response complete

        # Try systemctl first (Linux with systemd service)
        try:
            result = subprocess.run(
                ["systemctl", "--user", "is-active", "comfy-viewer"],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                subprocess.run(["systemctl", "--user", "restart", "comfy-viewer"])
                return
        except FileNotFoundError:
            pass  # systemctl not available

        # Fall back to process self-restart (cross-platform)
        os.execv(sys.executable, [sys.executable] + sys.argv)

    threading.Thread(target=do_restart, daemon=True).start()
    return jsonify({"success": True, "message": "Restarting..."})


@app.route("/api/gallery-settings", methods=["GET", "POST"])
def api_gallery_settings():
    """Get or update gallery UI visibility settings."""
    store = get_store()

    if request.method == "GET":
        settings_json = store.get_setting("gallery_ui")
        if settings_json:
            # Merge with defaults to ensure all fields present
            settings = {**GALLERY_UI_DEFAULTS, **json.loads(settings_json)}
            return jsonify(settings)
        return jsonify(GALLERY_UI_DEFAULTS)

    # POST - update settings
    data = request.get_json() or {}
    # Merge with defaults to ensure all fields present
    settings = {**GALLERY_UI_DEFAULTS, **data}
    # Enforce: settings buttons can be "visible" or "hidden", but not "off"
    if settings.get("settings_button") == "off":
        settings["settings_button"] = "visible"
    if settings.get("gallery_settings_button") == "off":
        settings["gallery_settings_button"] = "visible"
    store.set_setting("gallery_ui", json.dumps(settings))
    return jsonify({"success": True})


@app.route("/api/templates")
def api_templates():
    """Get available templates."""
    templates = scan_templates()
    state.set_templates(templates)
    return jsonify(templates)


@app.route("/api/templates/<template>/settings", methods=["GET", "POST"])
def api_template_settings(template):
    """Get or save template settings."""
    if request.method == "GET":
        try:
            settings = load_template_settings(template)
            state.set_current_template(template)
            state.set_settings(settings)
            return jsonify(settings)
        except FileNotFoundError:
            return jsonify({"error": "Template not found"}), 404

    else:  # POST
        data = request.get_json() or {}
        settings = data.get("settings")
        if not isinstance(settings, list):
            return jsonify({"error": "settings must be a list"}), 400

        try:
            save_template_settings(template, settings)
            state.set_settings(settings)
            return jsonify({"success": True})
        except Exception as e:
            log.error(f"Failed to save settings: {e}")
            return jsonify({"error": str(e)}), 500


@app.route("/api/templates/<template>/graph")
def api_template_graph(template):
    """
    Get the workflow graph for a template.

    This endpoint is used by RemoteFileService to fetch template graphs.
    """
    graph = load_template_graph(template)
    if not graph:
        return jsonify({"error": "Template not found"}), 404
    return jsonify({"graph": graph})


@app.route("/api/images")
def api_images():
    """Get paginated list of registrations (images with associated data)."""
    offset = int(request.args.get("offset", 0))
    limit = int(request.args.get("limit", 50))

    # Use registration store for unified timeline
    store = get_store()
    registrations, total = store.get_all(offset, limit)

    # Apply display field mapping (title/data slots from config)
    registrations = map_display_fields_list(registrations)

    state.set_images(registrations, total)
    return jsonify({"images": registrations, "total": total})


@app.route("/api/images/<path:filename>/registration")
def api_image_registration(filename):
    """Get the registration data for an image."""
    store = get_store()
    reg = store.get_by_image(filename)

    if reg:
        return jsonify({"has_data": True, "registration": map_display_fields(reg)})
    else:
        return jsonify({"has_data": False, "registration": None})


@app.route("/api/registrations/<registration_id>")
def api_get_registration(registration_id):
    """Get a registration by ID.

    Returns 200 with null for missing registrations (expected for older images).
    This prevents browser console errors for normal missing data.
    """
    store = get_store()
    reg = store.get(registration_id)
    if reg:
        reg = map_display_fields(reg)
    return jsonify(reg)  # Returns null if registration doesn't exist


@app.route("/api/images/<path:filename>/flag", methods=["POST"])
def api_flag_image(filename):
    """Toggle or set the flagged status of an image."""
    data = request.get_json() or {}
    store = get_store()

    # If 'flagged' is specified, set it; otherwise toggle
    if "flagged" in data:
        new_status = bool(data["flagged"])
        success = store.set_flagged(filename, new_status)
    else:
        new_status = store.toggle_flag(filename)
        success = new_status is not None

    if success:
        log.info(f"Image {'flagged' if new_status else 'unflagged'}: {filename}")
        return jsonify({"success": True, "flagged": new_status})
    else:
        return jsonify({"error": "Image not found or update failed"}), 404


@app.route("/api/images/flagged")
def api_flagged_images():
    """Get all flagged registrations."""
    store = get_store()
    registrations = map_display_fields_list(store.get_flagged())
    return jsonify({"images": registrations, "total": len(registrations)})


@app.route("/api/images/<path:filename>/rate", methods=["POST"])
def api_rate_image(filename):
    """Set the rating of an image (-1=dislike, 0=neutral, 1=like)."""
    data = request.get_json() or {}
    store = get_store()

    rating = data.get("rating", 0)
    if not isinstance(rating, int):
        try:
            rating = int(rating)
        except (ValueError, TypeError):
            return jsonify({"error": "Invalid rating value"}), 400

    # Clamp to valid range
    rating = max(-1, min(1, rating))

    success = store.set_rating(filename, rating)

    if success:
        rating_name = {-1: "disliked", 0: "neutral", 1: "liked"}.get(rating, "rated")
        log.info(f"Image {rating_name}: {filename}")
        return jsonify({"success": True, "rating": rating})
    else:
        return jsonify({"error": "Image not found or update failed"}), 404


@app.route("/api/images/liked")
def api_liked_images():
    """Get all liked registrations (rating = 1)."""
    store = get_store()
    registrations = map_display_fields_list(store.get_liked())
    return jsonify({"images": registrations, "total": len(registrations)})


@app.route("/api/images/disliked")
def api_disliked_images():
    """Get all disliked registrations (rating = -1)."""
    store = get_store()
    registrations = map_display_fields_list(store.get_disliked())
    return jsonify({"images": registrations, "total": len(registrations)})


@app.route("/api/images/find/<filename>")
def api_find_image(filename):
    """
    Find an image's position in the sorted list.

    Returns the offset where this image appears, useful for
    jumping directly to a specific image in the gallery.
    """
    if not OUTPUT_DIR.exists():
        return jsonify({"error": "Output directory not found"}), 404

    # Get all images sorted by modification time (newest first)
    all_images = []
    for p in OUTPUT_DIR.iterdir():
        if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
            stat = p.stat()
            all_images.append({
                "filename": p.name,
                "modified": int(stat.st_mtime),
            })

    all_images.sort(key=lambda x: x["modified"], reverse=True)

    # Find the index
    for i, img in enumerate(all_images):
        if img["filename"] == filename:
            return jsonify({
                "filename": filename,
                "index": i,
                "total": len(all_images)
            })

    return jsonify({"error": "Image not found"}), 404


@app.route("/api/generate", methods=["POST"])
def api_generate():
    """Queue a generation job."""
    data = request.get_json() or {}
    template = data.get("template")
    user_settings = data.get("settings", [])
    count = data.get("count", 1)

    if not template:
        return jsonify({"error": "template is required"}), 400

    try:
        # Load and merge settings
        settings = load_template_settings(template)

        # Apply user overrides
        user_map = {(s.get("node_id"), s.get("internalName")): s for s in user_settings}
        for setting in settings:
            key = (setting.get("node_id"), setting.get("internalName"))
            if key in user_map:
                setting["value"] = user_map[key]["value"]

        # Randomize seeds if configured
        if CONFIG.get("randomize_seed"):
            randomize_seeds(settings)

        # Build prompt
        graph = load_template_graph(template)
        prompt = apply_settings_to_graph(graph, settings)

        # Start generation tracking
        state.start_generation(count)

        # Submit to ComfyUI
        prompt_ids = []
        for i in range(count):
            if i > 0 and CONFIG.get("randomize_seed"):
                randomize_seeds(settings)
                graph = load_template_graph(template)
                prompt = apply_settings_to_graph(graph, settings)

            client_id = str(uuid.uuid4())
            result = comfy.post_prompt(prompt, client_id)
            prompt_ids.append(result.get("prompt_id"))

        return jsonify({
            "success": True,
            "prompt_ids": prompt_ids,
            "count": count
        })

    except FileNotFoundError:
        return jsonify({"error": "Template not found"}), 404
    except Exception as e:
        log.error(f"Generation failed: {e}")
        state.cancel_generation()
        return jsonify({"error": str(e)}), 500


# ─────────────────────────────────────────────────────────────
# Conduit Workflow API (REQUIRES ComfyUI-Conduit)
# ─────────────────────────────────────────────────────────────
# These endpoints require ComfyUI-Conduit to be installed in ComfyUI.
# Without Conduit, these return 503 errors with installation instructions.
# Install: https://github.com/NickPittas/ComfyUI-Conduit
# ─────────────────────────────────────────────────────────────

@app.route("/api/conduit/status")
def api_conduit_status():
    """
    Check Conduit availability and ComfyUI status.

    Returns:
        - available: Can we reach Conduit?
        - comfyui_busy: Is ComfyUI currently generating?
    """
    import requests

    # Check if Conduit is reachable by hitting the workflows endpoint
    try:
        response = requests.get(
            f"{CONFIG['comfy_host']}/conduit/workflows",
            timeout=5
        )
        conduit_available = response.ok
    except requests.RequestException:
        conduit_available = False

    # Check ComfyUI busy state from our internal tracking
    comfyui_busy = state.state.generation.is_generating

    return jsonify({
        "available": conduit_available,
        "comfyui_busy": comfyui_busy,
    })


@app.route("/api/conduit/workflows")
def api_conduit_workflows():
    """
    Proxy: List available Conduit workflows.

    This allows the frontend to get workflow information without
    direct access to ComfyUI - all requests go through comfy-viewer.
    """
    import requests
    try:
        response = requests.get(
            f"{CONFIG['comfy_host']}/conduit/workflows",
            timeout=10
        )
        return jsonify(response.json()), response.status_code
    except requests.RequestException as e:
        log.error(f"Failed to fetch workflows from ComfyUI: {e}")
        return jsonify({
            "error": CONDUIT_MESSAGES["error"],
            "workflows": [],
            "hint": CONDUIT_MESSAGES["hint"],
        }), 503


@app.route("/api/conduit/workflows/<path:workflow_name>")
def api_conduit_workflow_schema(workflow_name):
    """
    Proxy: Get workflow schema/definition.

    Returns the input/output schema for a specific Conduit workflow.
    """
    import requests
    try:
        response = requests.get(
            f"{CONFIG['comfy_host']}/conduit/workflows/{workflow_name}",
            timeout=10
        )
        return jsonify(response.json()), response.status_code
    except requests.RequestException as e:
        log.error(f"Failed to fetch workflow schema for {workflow_name}: {e}")
        return jsonify({
            "error": CONDUIT_MESSAGES["error"],
            "hint": CONDUIT_MESSAGES["hint"],
        }), 503


@app.route("/api/conduit/workflows/<path:workflow_name>/inputs")
def api_conduit_workflow_inputs(workflow_name):
    """
    Proxy: Get enriched workflow inputs with defaults and metadata.

    Returns inputs with workflow_value, registry defaults, options, min/max etc.
    Add ?refresh=true to force fresh COMBO options from ComfyUI.
    """
    import requests
    try:
        url = f"{CONFIG['comfy_host']}/conduit/workflows/{workflow_name}/inputs"
        if request.args.get('refresh'):
            url += "?refresh=true"
        response = requests.get(url, timeout=10)
        return jsonify(response.json()), response.status_code
    except requests.RequestException as e:
        log.error(f"Failed to fetch workflow inputs for {workflow_name}: {e}")
        return jsonify({
            "error": CONDUIT_MESSAGES["error"],
            "hint": CONDUIT_MESSAGES["hint"],
        }), 503


@app.route("/api/conduit/run/<path:workflow_name>", methods=["POST"])
def api_conduit_run(workflow_name):
    """
    Run a Conduit workflow through comfy-viewer.

    This proxies to ComfyUI's conduit endpoint while also:
    - Tracking generation state (for progress UI)
    - Triggering image registration via handler

    Expects JSON:
    {
        "inputs": { ... },  // Input overrides
        "count": 1          // Number of times to run
    }

    Uses wait=true so the Conduit handler runs and publishes to Redis,
    which triggers image registration in comfy-viewer.
    """
    import requests
    import threading

    data = request.get_json() or {}
    inputs = data.get("inputs", {})
    count = data.get("count", 1)

    # Start generation tracking
    state.start_generation(count)

    def run_generation():
        """Background thread to run generation with wait=true."""
        try:
            for i in range(count):
                # Call with wait=true so handler runs and publishes to Redis
                res = requests.post(
                    f"{CONFIG['comfy_host']}/conduit/run/{workflow_name}",
                    json={
                        "inputs": inputs,
                        "wait": True,
                        "timeout": 300,
                        "context": {"generation_type": "normal"},
                    },
                    timeout=310  # Slightly longer than Conduit timeout
                )

                if res.ok:
                    result = res.json()
                    prompt_id = result.get('prompt_id', f'unknown_{i}')
                    log.info(f"Conduit generation complete: {prompt_id} ({i+1}/{count})")
                    # Mark this generation as complete
                    state.complete_generation(prompt_id)
                else:
                    log.error(f"Conduit generation failed: {res.status_code}")
                    # Still mark as complete to update progress
                    state.complete_generation(f'failed_{i}')

        except Exception as e:
            log.error(f"Conduit generation error: {e}")
            state.cancel_generation()

    # Start generation in background thread
    thread = threading.Thread(target=run_generation, daemon=True)
    thread.start()

    log.info(f"Conduit generation queued: {workflow_name} x{count}")

    return jsonify({
        "success": True,
        "count": count,
        "workflow": workflow_name,
        "status": "queued"
    })


# ─────────────────────────────────────────────────────────────
# Workflow Input Overrides API
# ─────────────────────────────────────────────────────────────

@app.route("/api/workflow-inputs/<path:workflow_name>", methods=["GET"])
def api_get_workflow_inputs(workflow_name):
    """Get saved input overrides for a workflow."""
    store = get_store()
    inputs = store.get_workflow_inputs(workflow_name)
    return jsonify({"inputs": inputs, "workflow": workflow_name})


@app.route("/api/workflow-inputs/<path:workflow_name>", methods=["POST"])
def api_set_workflow_inputs(workflow_name):
    """Save input overrides for a workflow."""
    data = request.get_json() or {}
    inputs = data.get("inputs", {})

    if not isinstance(inputs, dict):
        return jsonify({"error": "inputs must be an object"}), 400

    store = get_store()
    success = store.set_workflow_inputs(workflow_name, inputs)

    if success:
        log.info(f"Saved {len(inputs)} input overrides for workflow: {workflow_name}")
        return jsonify({"success": True, "saved": len(inputs)})
    else:
        return jsonify({"error": "Failed to save inputs"}), 500


@app.route("/api/workflow-inputs/<path:workflow_name>/<input_key>", methods=["POST"])
def api_set_workflow_input(workflow_name, input_key):
    """Save a single input override for a workflow."""
    data = request.get_json() or {}

    if "value" not in data:
        return jsonify({"error": "value is required"}), 400

    store = get_store()
    success = store.set_workflow_input(workflow_name, input_key, data["value"])

    if success:
        return jsonify({"success": True})
    else:
        return jsonify({"error": "Failed to save input"}), 500


@app.route("/api/workflow-inputs/<path:workflow_name>", methods=["DELETE"])
def api_clear_workflow_inputs(workflow_name):
    """Clear all saved inputs for a workflow (reset to defaults)."""
    store = get_store()
    success = store.clear_workflow_inputs(workflow_name)

    if success:
        log.info(f"Cleared input overrides for workflow: {workflow_name}")
        return jsonify({"success": True})
    else:
        return jsonify({"error": "Failed to clear inputs"}), 500


# ─────────────────────────────────────────────────────────────
# Last Workflow API - Remembers the last viewed workflow
# ─────────────────────────────────────────────────────────────

@app.route("/api/last-workflow", methods=["GET"])
def api_get_last_workflow():
    """Get the last viewed workflow name and its saved inputs."""
    store = get_store()
    workflow_name = store.get_setting("last_workflow")

    if not workflow_name:
        return jsonify({"workflow": None, "inputs": {}})

    inputs = store.get_workflow_inputs(workflow_name)
    return jsonify({"workflow": workflow_name, "inputs": inputs})


@app.route("/api/last-workflow", methods=["POST"])
def api_set_last_workflow():
    """Set the last viewed workflow."""
    data = request.get_json() or {}
    workflow_name = data.get("workflow")

    if not workflow_name:
        return jsonify({"error": "workflow name required"}), 400

    store = get_store()
    store.set_setting("last_workflow", workflow_name)
    log.info(f"Set last workflow: {workflow_name}")

    return jsonify({"success": True, "workflow": workflow_name})


@app.route("/api/quick-save", methods=["POST"])
def api_quick_save():
    """
    Copy an image to quicksaves folder.

    Works with both local and remote backends - for remote,
    downloads the image first then saves locally.
    """
    data = request.get_json() or {}
    filename = data.get("filename")

    if not filename:
        return jsonify({"error": "filename required"}), 400

    if not file_service.image_exists(filename):
        return jsonify({"error": "Image not found"}), 404

    try:
        dest = QUICKSAVES_DIR / filename
        success = file_service.copy_image(filename, dest)

        if success:
            return jsonify({"success": True, "path": str(dest)})
        else:
            return jsonify({"error": "Failed to copy image"}), 500
    except Exception as e:
        log.error(f"Quick save failed: {e}")
        return jsonify({"error": str(e)}), 500


# ─────────────────────────────────────────────────────────────
# Conduit Event Endpoint
# ─────────────────────────────────────────────────────────────


@app.route("/api/conduit-event", methods=["POST"])
def api_conduit_event():
    """
    Receive events from Conduit (ComfyUI output capture system).

    Expects JSON:
    {
        "prompt_id": "...",
        "output_folder": "...",
        "created_at": 1703298806.0,  # Unix timestamp from prompt_id
        "outputs": [
            {"file_path": "...", "tag_name": "...", "file_type": "image", ...},
            ...
        ]
    }

    Registers the PREFERRED image (CharImg > FinalImage > Output > first image)
    in the registration store. Hooks run during registration to extract
    associated data (like char_str from CharStr.txt).
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "No JSON data"}), 400

    outputs = data.get("outputs", [])
    prompt_id = data.get("prompt_id", "unknown")
    output_folder = data.get("output_folder")

    if not outputs:
        return jsonify({"success": True, "images_added": 0, "total_outputs": 0})

    # Get the folder path for hooks to use
    folder_path = Path(output_folder) if output_folder else None

    # Use shared logic to select preferred image
    selected_output, selected_tag = select_preferred_image(outputs)

    if not selected_output:
        return jsonify({"success": True, "images_added": 0, "total_outputs": len(outputs)})

    log.info(f"Conduit: selected tag '{selected_tag}'")

    store = get_store()
    image_count = 0

    # Register the selected image
    filepath = Path(selected_output.get("file_path", ""))
    relative_path = get_relative_image_path(filepath, OUTPUT_DIR)

    # Register with hooks (hooks extract char_str, etc. from folder)
    reg = store.register(
        image_path=relative_path,
        source="conduit",
        folder_path=folder_path,
        registration_id=prompt_id,
    )

    if reg:
        # Notify connected WebSocket clients about new image
        if filepath.exists():
            # Apply display field mapping for title/data slots
            mapped_reg = map_display_fields(reg)
            state.add_image({
                "filename": relative_path,
                "size": filepath.stat().st_size,
                "modified": int(reg["created_at"]),
                "id": reg["id"],
                "char_str": reg.get("char_str"),
                "title": mapped_reg.get("title"),
                "data": mapped_reg.get("data"),
                "tag_name": selected_tag,
            })
            image_count += 1
            log.info(f"Conduit: registered {relative_path} "
                     f"(tag={selected_tag}, title={mapped_reg.get('title', {}).get('value')})")

    return jsonify({
        "success": True,
        "images_added": image_count,
        "total_outputs": len(outputs),
        "selected_tag": selected_tag,
    })


# ─────────────────────────────────────────────────────────────
# File Watcher Callbacks
# ─────────────────────────────────────────────────────────────

def on_new_image_from_service(filename: str, image_info):
    """
    Called when a new image is detected by the file service.

    This callback is used by both local (inotify) and remote (polling) backends.

    Args:
        filename: Image filename (relative to output dir)
        image_info: ImageInfo object from the file service
    """
    # Skip if this is in the conduit subfolder (handled by conduit-event endpoint)
    if filename.startswith("conduit/") or "/conduit/" in filename:
        log.debug(f"Skipping conduit image (registered via API): {filename}")
        return

    log.info(f"New image detected by file service: {filename}")

    # For local backend, get the folder path for hooks
    local_path = file_service.get_image_path(filename)
    folder_path = local_path.parent if local_path else None

    # Register in the store (hooks may run but likely won't find data for standalone images)
    store = get_store()
    reg = store.register(
        image_path=filename,
        source="file_service",
        folder_path=folder_path,
    )

    if reg:
        # Apply display field mapping for title/data slots
        mapped_reg = map_display_fields(reg)
        # Add to state (this broadcasts to all WebSocket clients)
        state.add_image({
            "filename": filename,
            "size": image_info.size,
            "modified": int(reg["created_at"]),
            "id": reg["id"],
            "char_str": reg.get("char_str"),
            "title": mapped_reg.get("title"),
            "data": mapped_reg.get("data"),
        })
    else:
        log.debug(f"Image already registered: {filename}")


def on_deleted_image_from_service(filename: str):
    """
    Called when an image is deleted (detected by file service).

    Removes the registration from the database and notifies connected clients.
    """
    log.info(f"Image deleted: {filename}")

    # Remove from registration store
    store = get_store()
    deleted = store.delete_by_image(filename)

    if deleted:
        # Notify connected clients by refreshing the image list
        registrations, total = store.get_all(0, 50)
        state.set_images(registrations, total)


# Legacy callbacks for backward compatibility with old file_watcher module
def on_new_image(filepath, file_info):
    """Legacy callback - converts to new format."""
    from file_service import ImageInfo
    info = ImageInfo(
        filename=file_info["filename"],
        size=file_info["size"],
        modified=file_info["modified"],
    )
    on_new_image_from_service(file_info["filename"], info)


def on_deleted_image(filepath):
    """Legacy callback - converts to new format."""
    try:
        relative_path = str(filepath.relative_to(OUTPUT_DIR))
    except ValueError:
        relative_path = filepath.name
    on_deleted_image_from_service(relative_path)


# ─────────────────────────────────────────────────────────────
# Startup
# ─────────────────────────────────────────────────────────────

def scan_and_register_images():
    """
    Scan the output directory and register any unregistered images.

    This ensures the registration store has entries for all existing images,
    using their file mtime as the registration timestamp. Hooks run during
    registration to extract associated data.

    Also cleans up orphaned registrations (images that were deleted from disk).
    """
    if not OUTPUT_DIR.exists():
        return 0

    store = get_store()

    # First, clean up orphaned registrations (files deleted from disk)
    cleanup_result = store.cleanup_orphaned(OUTPUT_DIR)
    if cleanup_result["deleted"] > 0:
        log.info(f"Cleaned up {cleanup_result['deleted']} orphaned registrations")

    registered_count = 0

    # Scan direct children of output dir (standalone images)
    for p in OUTPUT_DIR.iterdir():
        if p.is_file() and p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
            filename = p.name
            if not store.is_registered(filename):
                reg = store.register(
                    image_path=filename,
                    source="scan",
                    folder_path=p.parent,
                )
                if reg:
                    registered_count += 1

    # Also scan conduit subfolder for orphaned images (jobs without events)
    conduit_dir = OUTPUT_DIR / "conduit"
    if conduit_dir.exists():
        for job_dir in conduit_dir.iterdir():
            if not job_dir.is_dir():
                continue
            for p in job_dir.iterdir():
                if p.is_file() and p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
                    # Relative path from OUTPUT_DIR
                    relative_path = str(p.relative_to(OUTPUT_DIR))
                    if not store.is_registered(relative_path):
                        # Use folder name as registration ID
                        reg = store.register(
                            image_path=relative_path,
                            source="scan",
                            folder_path=job_dir,  # Hooks extract data (CharStr.txt, etc.) from this folder
                            registration_id=job_dir.name,
                        )
                        if reg:
                            registered_count += 1

    return registered_count


def initialize():
    """Initialize application state on startup."""
    # Load templates
    templates = scan_templates()
    state.set_templates(templates)

    # Initialize registration store and scan for unregistered images
    store = get_store()
    stats = store.get_stats()
    log.info(f"Registration store: {stats['total']} registrations, {stats['with_char_str']} with char_str")

    # Register any images not yet in the store (only for local backend)
    if file_service.get_backend_type() == "local":
        new_images = scan_and_register_images()
        if new_images > 0:
            log.info(f"Registered {new_images} new images from disk scan")

    # Load registrations for initial state
    registrations, total = store.get_all(0, 50)
    state.set_images(registrations, total)

    # Check ComfyUI connection
    comfy.health_check()

    # Start ComfyUI WebSocket for progress updates
    comfy.connect_websocket()

    # Start file watching through the file service (works for both local and remote)
    file_service.watch_changes(
        on_created=on_new_image_from_service,
        on_deleted=on_deleted_image_from_service,
    )

    # Start custom event subscribers (from subscribers/ folder)
    # Drop Python files in subscribers/ to add custom event sources (e.g., Redis, MQTT)
    if file_service.get_backend_type() == "local":
        start_subscribers(OUTPUT_DIR, store.register, state.add_image)

    backend_type = file_service.get_backend_type()
    log.info(f"Initialized with {len(templates)} templates, {total} images")
    log.info(f"File backend: {backend_type}")
    if backend_type == "local":
        log.info(f"Watching for new images in: {OUTPUT_DIR}")
    else:
        log.info(f"Polling remote server for changes")


def shutdown():
    """Clean shutdown."""
    log.info("Shutting down...")
    stop_subscribers()
    file_service.stop_watching()
    comfy.disconnect_websocket()


# Initialize when module is imported (for service/production use)
# This ensures the registry, file watcher, etc. are set up
import atexit
import signal
import sys


def handle_sigterm(signum, frame):
    """Handle SIGTERM for graceful shutdown."""
    shutdown()
    sys.exit(0)


atexit.register(shutdown)
signal.signal(signal.SIGTERM, handle_sigterm)
signal.signal(signal.SIGINT, handle_sigterm)

# Initialize on module load
initialize()


if __name__ == "__main__":
    log.info(f"Starting server on {CONFIG['host']}:{CONFIG['port']}")

    try:
        socketio.run(
            app,
            host=CONFIG["host"],
            port=CONFIG["port"],
            debug=False,
            use_reloader=False,
            allow_unsafe_werkzeug=True  # Required for Flask-SocketIO 5.x
        )
    finally:
        shutdown()
