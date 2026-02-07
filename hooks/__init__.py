"""
Hook Manager for Registration Data Extraction & Lifecycle

Hooks can be:
1. Single Python files (e.g., `_default.py`) with an `extract()` function
2. Hook packages (folders with `__init__.py` containing `extract()`)

Hooks run in alphabetical order. Later hooks can override earlier hooks' values.
Use `_` prefix to control ordering (e.g., `_default.py` runs before `conduit/`).

Extract Interface (registration-time):
    def extract(folder_path: Path, current_data: dict) -> dict

Lifecycle Interface (startup/shutdown):
    def on_startup(context: dict) -> None
    def on_shutdown(context: dict) -> None
"""

import importlib.util
import logging
import sys
from pathlib import Path
from typing import Optional

log = logging.getLogger("comfy-viewer.hooks")

HOOKS_DIR = Path(__file__).parent
HOOKS_LOCAL_DIR = HOOKS_DIR.parent / "hooks.local"
EXTRA_HOOKS_DIR: Optional[Path] = None


def set_extra_hooks_dir(path: Optional[Path]) -> None:
    """Override or clear the external hooks directory."""
    global EXTRA_HOOKS_DIR
    if path is None:
        EXTRA_HOOKS_DIR = None
    else:
        EXTRA_HOOKS_DIR = Path(path)


def _hook_dirs() -> list[Path]:
    dirs = [HOOKS_DIR]
    if HOOKS_LOCAL_DIR.is_dir():
        dirs.append(HOOKS_LOCAL_DIR)
    if EXTRA_HOOKS_DIR:
        dirs.append(EXTRA_HOOKS_DIR)
    return dirs


def _load_module(name: str, file_path: Path, is_package: bool = False):
    """
    Dynamically load a Python module from a file path.

    Modules are registered under comfy_viewer_hooks.{name} to avoid
    polluting sys.modules with bare names like "conduit" or "_default".
    """
    qualified_name = f"comfy_viewer_hooks.{name}"

    if is_package:
        spec = importlib.util.spec_from_file_location(
            qualified_name,
            file_path,
            submodule_search_locations=[str(file_path.parent)]
        )
    else:
        spec = importlib.util.spec_from_file_location(qualified_name, file_path)

    module = importlib.util.module_from_spec(spec)
    sys.modules[qualified_name] = module
    spec.loader.exec_module(module)
    return module


def _get_hooks() -> list[tuple[str, Path, bool]]:
    """
    Find all hooks (single files and packages) sorted alphabetically.

    Returns:
        List of (hook_name, hook_file_path, is_package) tuples
    """
    hooks_by_name: dict[str, tuple[str, Path, bool]] = {}

    for hook_dir in _hook_dirs():
        if not hook_dir.exists() or not hook_dir.is_dir():
            continue

        # Find single-file hooks (*.py, skip __*.py like __init__.py)
        for hook_file in hook_dir.glob("*.py"):
            if hook_file.name.startswith("__"):
                continue
            hooks_by_name[hook_file.stem] = (hook_file.stem, hook_file, False)

        # Find hook packages (folders with __init__.py)
        for item in hook_dir.iterdir():
            if item.is_dir() and not item.name.startswith("__"):
                init_file = item / "__init__.py"
                if init_file.exists():
                    hooks_by_name[item.name] = (item.name, init_file, True)

    # Sort by hook name (alphabetically)
    return sorted(hooks_by_name.values(), key=lambda x: x[0])


def run_all(folder_path: Path, current_data: dict) -> dict:
    """
    Run all hooks and merge their results.

    Hooks are loaded in alphabetical order. Each hook's extract() function
    is called with the folder path and current data. Results are merged
    into current_data (later hooks can override earlier values).

    Args:
        folder_path: Path to folder containing the image
        current_data: Base registration data (image_path, source, caller_context, etc.)

    Returns:
        Updated data dict with all hook contributions merged in
    """
    for hook_name, hook_file, is_package in _get_hooks():
        try:
            module = _load_module(hook_name, hook_file, is_package)

            if hasattr(module, "extract"):
                result = module.extract(folder_path, current_data)
                if result and isinstance(result, dict):
                    current_data.update(result)
                    log.debug(f"Hook '{hook_name}': added {list(result.keys())}")
            else:
                log.warning(f"Hook '{hook_name}' has no extract() function")

        except Exception as e:
            log.error(f"Hook '{hook_name}' failed: {e}")

    return current_data


def list_hooks() -> list[str]:
    """Return list of available hook names."""
    return [name for name, _, _ in _get_hooks()]


# ─────────────────────────────────────────────────────────────
# Lifecycle hooks (startup / shutdown)
# ─────────────────────────────────────────────────────────────

_lifecycle_modules: list = []


def run_lifecycle(point: str, context: dict) -> None:
    """
    Run lifecycle hooks across all hook directories.

    Scans hooks/, hooks.local/, and extra dirs for modules with
    an on_{point}(context) function and calls them in order.
    """
    fn_name = f"on_{point}"

    for hook_name, hook_file, is_package in _get_hooks():
        try:
            module = _load_module(hook_name, hook_file, is_package)
            if hasattr(module, fn_name):
                getattr(module, fn_name)(context)
                if module not in _lifecycle_modules:
                    _lifecycle_modules.append(module)
                log.info(f"Lifecycle '{point}': {hook_name}")
        except Exception as e:
            log.error(f"Lifecycle hook '{hook_name}' failed on {point}: {e}")


def shutdown_lifecycle() -> None:
    """Call on_shutdown on all modules that participated in lifecycle."""
    for module in _lifecycle_modules:
        try:
            if hasattr(module, "on_shutdown"):
                module.on_shutdown({})
                log.info(f"Shutdown: {module.__name__}")
        except Exception as e:
            log.error(f"Shutdown hook '{module.__name__}' failed: {e}")
    _lifecycle_modules.clear()
