"""
Event Subscribers - Extensible real-time event sources.

This module auto-discovers and loads custom subscriber modules from the
subscribers/ directory. Use this to add custom event sources like Redis,
MQTT, webhooks, etc.

HOW TO ADD A SUBSCRIBER:

1. Create a Python file in this folder (e.g., my_subscriber.py)
2. Implement the required interface:

   ```python
   # subscribers/my_subscriber.py

   def start(output_dir, register_callback, add_image_callback):
       '''Called at startup to begin listening for events.'''
       # Your connection/subscription logic here
       pass

   def stop():
       '''Called at shutdown to clean up.'''
       pass
   ```

3. The subscriber will be auto-loaded on startup

CALLBACKS:

- register_callback(image_path, source, folder_path, registration_id, caller_context)
  Registers an image in the database and runs hooks for metadata extraction.
  Returns the registration dict or None if already registered.

- add_image_callback(image_dict)
  Broadcasts the image to all connected WebSocket clients.
  image_dict should have: filename, size, modified, id, char_str, title, data

NOTE: Files in this folder (except __init__.py) are gitignored.
This is intentional - subscribers are for local customization.
"""

import importlib.util
import logging
from pathlib import Path
from typing import Callable, List, Any

log = logging.getLogger("comfy-viewer.subscribers")

# Track loaded subscriber modules for cleanup
_loaded_subscribers: List[Any] = []


def start_subscribers(output_dir: Path, register_callback: Callable, add_image_callback: Callable):
    """
    Discover and start all subscriber modules in the subscribers/ directory.

    Each module should have a start(output_dir, register_callback, add_image_callback)
    function that begins listening for events.
    """
    global _loaded_subscribers

    subscribers_dir = Path(__file__).parent

    # Find all .py files except __init__.py
    subscriber_files = [
        f for f in subscribers_dir.glob("*.py")
        if f.name != "__init__.py" and not f.name.startswith("_")
    ]

    if not subscriber_files:
        log.debug("No custom subscribers found in subscribers/")
        return

    for filepath in sorted(subscriber_files):
        module_name = filepath.stem
        try:
            # Load the module
            spec = importlib.util.spec_from_file_location(module_name, filepath)
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)

                # Check for required start function
                if hasattr(module, "start"):
                    module.start(output_dir, register_callback, add_image_callback)
                    _loaded_subscribers.append(module)
                    log.info(f"Started subscriber: {module_name}")
                else:
                    log.warning(f"Subscriber {module_name} has no start() function, skipping")

        except Exception as e:
            log.error(f"Failed to load subscriber {module_name}: {e}")


def stop_subscribers():
    """Stop all loaded subscriber modules."""
    global _loaded_subscribers

    for module in _loaded_subscribers:
        try:
            if hasattr(module, "stop"):
                module.stop()
                log.info(f"Stopped subscriber: {module.__name__}")
        except Exception as e:
            log.error(f"Error stopping subscriber {module.__name__}: {e}")

    _loaded_subscribers.clear()
