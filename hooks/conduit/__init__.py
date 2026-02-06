"""
Conduit Integration Hook

This hook handles images coming from Conduit (the ComfyUI workflow gateway).
It routes to generation-type-specific plugins based on caller_context.

Behavior:
- If generation_type is specified: Load matching plugin, use its output
- If generation_type is unknown/missing: Read CharStr.txt, run all plugins

This hook runs after _default.py and can override its values.
"""

from pathlib import Path

from .plugins import load_plugin, get_all_plugins


def extract(folder_path: Path, current_data: dict) -> dict:
    """
    Extract data using Conduit-specific logic.

    Routes to plugins based on generation_type, or falls back to
    running all plugins for backwards compatibility.

    Args:
        folder_path: Path to folder containing the image
        current_data: Data collected so far (includes caller_context)

    Returns:
        {"char_str": "...", "prompt": "..."} or partial dict
    """
    caller_context = current_data.get("caller_context", {})
    generation_type = caller_context.get("generation_type")

    # Try to load a specific plugin for the generation type
    if generation_type:
        plugin = load_plugin(generation_type)
        if plugin:
            return plugin.extract(folder_path, current_data)

    # Fallback: unknown/missing generation_type
    # Read CharStr.txt for title, run all plugins for data
    return _fallback_extract(folder_path, current_data)


def _fallback_extract(folder_path: Path, current_data: dict) -> dict:
    """
    Fallback extraction when generation_type is unknown.

    - char_str: Read from CharStr.txt (or keep default)
    - prompt: Try all plugins, use first that returns data
    """
    result = {}

    # Try to get char_str from CharStr.txt
    char_str = _read_charstr(folder_path)
    if char_str:
        result["char_str"] = char_str

    # Try all plugins for prompt data
    for plugin_name, plugin in get_all_plugins():
        try:
            plugin_result = plugin.extract(folder_path, current_data)
            if plugin_result and plugin_result.get("prompt"):
                result["prompt"] = plugin_result["prompt"]
                break
        except Exception:
            continue

    return result


def _read_charstr(folder_path: Path) -> str | None:
    """Read CharStr.txt if it exists."""
    charstr_file = folder_path / "CharStr.txt"
    if charstr_file.exists():
        try:
            content = charstr_file.read_text().strip()
            if content:
                return content
        except Exception:
            pass
    return None
