"""
Redis Subscriber - Real-time gallery updates via Redis pub/sub.

This is a custom subscriber for comfy-viewer. Drop this file in the
subscribers/ folder to enable Redis-based event listening.

REQUIRES:
- Redis server running (redis-server)
- Python redis package: pip install redis

ENVIRONMENT VARIABLES:
- REDIS_HOST: Redis server host (default: localhost)
- REDIS_PORT: Redis server port (default: 6379)
- REDIS_CHANNEL: Channel to subscribe to (default: systems.events)

This subscriber listens for Conduit events published to Redis and updates
the gallery in real-time. Useful for multi-service architectures where
events are broadcast via pub/sub rather than direct HTTP calls.
"""

import json
import logging
import os
import threading
from pathlib import Path
from typing import Optional, Callable

import config as app_config
from registrations import select_preferred_image, get_relative_image_path

log = logging.getLogger("comfy-viewer.redis")

def _load_display_config() -> dict:
    """Load display configuration from the config file."""
    try:
        config = app_config.load_config()
        return config.get("display", {}) or {}
    except Exception:
        return {}


def map_display_fields(reg: dict) -> dict:
    """Map extracted fields to title/data display slots based on config."""
    display_cfg = _load_display_config()
    title_cfg = display_cfg.get("title", {"field": "char_str", "label": "Title"})
    data_cfg = display_cfg.get("data", {"field": "prompt", "label": "Data"})

    def get_field_value(field_name: str):
        if field_name == "char_str":
            return reg.get("char_str")
        data_blob = reg.get("data", {}) or {}
        return data_blob.get(field_name)

    return {
        "title": {
            "value": get_field_value(title_cfg.get("field", "char_str")),
            "label": title_cfg.get("label", "Title"),
        },
        "data": {
            "value": get_field_value(data_cfg.get("field", "prompt")),
            "label": data_cfg.get("label", "Data"),
        },
    }

# Redis configuration (override with environment variables)
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
CHANNEL = os.getenv("REDIS_CHANNEL", "systems.events")


class RedisEventSubscriber:
    """
    Background thread that subscribes to Redis and processes events.

    Uses the same registration logic as the HTTP endpoint but receives
    events via Redis pub/sub instead of HTTP POST.
    """

    def __init__(self, output_dir: Path, register_callback: Callable, add_image_callback: Callable):
        """
        Initialize the subscriber.

        Args:
            output_dir: Base output directory for resolving relative paths
            register_callback: Function to register an image (from registrations store)
            add_image_callback: Function to add image to state (for WebSocket broadcast)
        """
        self.output_dir = output_dir
        self.register = register_callback
        self.add_image = add_image_callback
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._client = None

    def start(self):
        """Start the subscriber in a background thread."""
        if self._running:
            log.warning("Redis subscriber already running")
            return

        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True, name="redis-subscriber")
        self._thread.start()
        log.info("Redis subscriber started")

    def stop(self):
        """Stop the subscriber."""
        self._running = False
        if self._thread and self._thread.is_alive():
            # The thread will exit on next message or timeout
            self._thread.join(timeout=2.0)
        log.info("Redis subscriber stopped")

    def _run(self):
        """Main subscriber loop (runs in background thread)."""
        try:
            import redis
        except ImportError:
            log.error("redis package not installed - subscriber disabled")
            return

        while self._running:
            try:
                self._client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
                pubsub = self._client.pubsub()
                pubsub.subscribe(CHANNEL)

                log.info(f"Subscribed to {CHANNEL}")

                # Process messages
                for message in pubsub.listen():
                    if not self._running:
                        break

                    if message["type"] != "message":
                        continue

                    try:
                        event = json.loads(message["data"])
                        self._handle_event(event)
                    except json.JSONDecodeError as e:
                        log.error(f"Invalid JSON in Redis message: {e}")
                    except Exception as e:
                        log.error(f"Error processing event: {e}")

                pubsub.close()

            except redis.ConnectionError as e:
                if self._running:
                    log.warning(f"Redis connection lost, reconnecting in 5s: {e}")
                    import time
                    time.sleep(5)
            except Exception as e:
                if self._running:
                    log.error(f"Redis subscriber error: {e}")
                    import time
                    time.sleep(5)

    def _handle_event(self, event: dict):
        """Process an event from Redis."""
        event_type = event.get("event_type")
        source = event.get("source", {})
        data = event.get("data", {})

        tool = source.get("tool", data.get("tool", "unknown"))

        # Only process Conduit operation.completed events
        if event_type != "operation.completed" or tool != "conduit":
            return

        log.info(f"Received Conduit event: {data.get('operation_id', 'unknown')}")
        self._process_conduit_event(data)

    def _process_conduit_event(self, data: dict):
        """
        Process a Conduit operation.completed event.

        Uses shared logic from registrations module:
        1. Select preferred image (CharImg > FinalImage > Output > first)
        2. Register in store (runs hooks for char_str extraction)
        3. Broadcast to WebSocket clients
        """
        outputs = data.get("outputs", [])
        operation_id = data.get("operation_id", "unknown")
        metadata = data.get("metadata", {})
        output_folder = metadata.get("output_folder")
        caller_context = metadata.get("caller_context", {})

        if not outputs:
            return

        folder_path = Path(output_folder) if output_folder else None

        # Use shared logic to select preferred image
        selected_output, selected_tag = select_preferred_image(outputs)

        if not selected_output:
            return

        log.info(f"Selected tag '{selected_tag}'")

        # Get the file path
        filepath = Path(selected_output.get("file_path", ""))
        relative_path = get_relative_image_path(filepath, self.output_dir)

        # Skip if file doesn't exist
        if not filepath.exists():
            log.warning(f"Skipping non-existent file: {filepath}")
            return

        # Register with hooks (hooks extract char_str, etc. from folder)
        reg = self.register(
            image_path=relative_path,
            source="conduit",
            folder_path=folder_path,
            registration_id=operation_id,
            caller_context=caller_context,
        )

        if reg:
            # Notify connected WebSocket clients about new image
            if filepath.exists():
                # Apply display field mapping for title/data slots
                mapped = map_display_fields(reg)
                self.add_image({
                    "filename": relative_path,
                    "size": filepath.stat().st_size,
                    "modified": int(reg["created_at"]),
                    "id": reg["id"],
                    "char_str": reg.get("char_str"),
                    "title": mapped.get("title"),
                    "data": mapped.get("data"),
                    "tag_name": selected_tag,
                })
                log.info(f"Registered {relative_path} (tag={selected_tag}, title={mapped.get('title', {}).get('value')})")


# Global instance
_subscriber: Optional[RedisEventSubscriber] = None


def start(output_dir: Path, register_callback: Callable, add_image_callback: Callable):
    """
    Start the Redis subscriber.

    This subscriber listens to Redis pub/sub for Conduit events.
    Requires: pip install redis

    Environment variables:
    - REDIS_HOST: Redis server host (default: localhost)
    - REDIS_PORT: Redis server port (default: 6379)
    - REDIS_CHANNEL: Channel to subscribe to (default: systems.events)
    """
    global _subscriber

    if _subscriber is not None:
        log.warning("Redis subscriber already initialized")
        return

    _subscriber = RedisEventSubscriber(output_dir, register_callback, add_image_callback)
    _subscriber.start()


def stop():
    """Stop the Redis subscriber."""
    global _subscriber

    if _subscriber is not None:
        _subscriber.stop()
        _subscriber = None
