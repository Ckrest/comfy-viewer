"""
Conduit Plugin Loader

Loads generation-type handlers from this folder.
Each plugin is a Python file with an extract(folder_path, current_data) function.

Plugins:
- normal.py: Frontend/Comfy-Viewer generations
- scene_gen.py: SillyTavern SceneGen generations
"""

import importlib.util
import logging
from pathlib import Path

log = logging.getLogger("comfy-viewer.hooks.conduit.plugins")

PLUGINS_DIR = Path(__file__).parent


def _load_module(name: str, file_path: Path):
    """Dynamically load a Python module from a file path."""
    spec = importlib.util.spec_from_file_location(f"conduit_plugin_{name}", file_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_plugin(generation_type: str):
    """
    Load a specific plugin by generation_type.

    Args:
        generation_type: The type to look up (e.g., "normal", "scene_gen")

    Returns:
        Module with extract() function, or None if not found
    """
    plugin_file = PLUGINS_DIR / f"{generation_type}.py"

    if not plugin_file.exists():
        log.debug(f"No plugin for generation_type '{generation_type}'")
        return None

    try:
        module = _load_module(generation_type, plugin_file)
        if hasattr(module, "extract"):
            return module
        log.warning(f"Plugin '{generation_type}' has no extract() function")
    except Exception as e:
        log.error(f"Failed to load plugin '{generation_type}': {e}")

    return None


def get_all_plugins() -> list[tuple[str, object]]:
    """
    Get all available plugins.

    Returns:
        List of (plugin_name, module) tuples
    """
    plugins = []

    for plugin_file in sorted(PLUGINS_DIR.glob("*.py")):
        if plugin_file.name.startswith("__"):
            continue

        name = plugin_file.stem
        try:
            module = _load_module(name, plugin_file)
            if hasattr(module, "extract"):
                plugins.append((name, module))
        except Exception as e:
            log.error(f"Failed to load plugin '{name}': {e}")

    return plugins
