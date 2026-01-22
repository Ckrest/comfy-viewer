"""
Centralized State Manager - Single Source of Truth

All application state lives here. Changes flow through this module,
which then broadcasts updates to connected WebSocket clients.
"""

import json
import logging
import threading
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Callable, Optional
from collections import OrderedDict

log = logging.getLogger("comfy-viewer.state")


@dataclass
class GenerationState:
    """Tracks ongoing generation jobs."""
    is_generating: bool = False
    queued: list[str] = field(default_factory=list)  # prompt_ids
    current_prompt_id: Optional[str] = None
    progress: float = 0.0  # 0-100
    completed: int = 0
    total: int = 0


@dataclass
class AppState:
    """Complete application state."""
    # Connection status
    comfy_connected: bool = False

    # Templates
    templates: list[str] = field(default_factory=list)
    current_template: Optional[str] = None
    settings: list[dict] = field(default_factory=list)

    # Images
    images: list[dict] = field(default_factory=list)
    images_total: int = 0

    # Generation
    generation: GenerationState = field(default_factory=GenerationState)

    def to_dict(self) -> dict:
        """Convert to JSON-serializable dict."""
        d = asdict(self)
        return d


class StateManager:
    """
    Singleton state manager with observer pattern.

    Usage:
        state = StateManager()
        state.subscribe(callback)  # Called on any state change
        state.update_templates(['t1', 't2'])  # Triggers broadcast
    """

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self._state = AppState()
        self._subscribers: list[Callable[[dict], None]] = []
        self._state_lock = threading.RLock()
        self._initialized = True

        log.info("StateManager initialized")

    @property
    def state(self) -> AppState:
        """Read-only access to current state."""
        return self._state

    def subscribe(self, callback: Callable[[dict], None]) -> Callable[[], None]:
        """
        Subscribe to state changes.
        Returns an unsubscribe function.
        """
        self._subscribers.append(callback)
        log.debug(f"Subscriber added, total: {len(self._subscribers)}")

        def unsubscribe():
            if callback in self._subscribers:
                self._subscribers.remove(callback)
                log.debug(f"Subscriber removed, total: {len(self._subscribers)}")

        return unsubscribe

    def _broadcast(self, event_type: str, data: Optional[dict] = None):
        """Notify all subscribers of a state change."""
        message = {
            "type": event_type,
            "data": data or {},
            "state": self._state.to_dict()
        }

        for callback in self._subscribers:
            try:
                callback(message)
            except Exception as e:
                log.error(f"Subscriber callback failed: {e}")

    # ─────────────────────────────────────────────────────────────
    # State Update Methods - Each triggers a broadcast
    # ─────────────────────────────────────────────────────────────

    def set_comfy_connected(self, connected: bool):
        """Update ComfyUI connection status."""
        with self._state_lock:
            if self._state.comfy_connected != connected:
                self._state.comfy_connected = connected
                self._broadcast("comfy_status", {"connected": connected})

    def set_templates(self, templates: list[str]):
        """Update available templates list."""
        with self._state_lock:
            self._state.templates = templates
            if templates and not self._state.current_template:
                self._state.current_template = templates[0]
            self._broadcast("templates_updated", {"templates": templates})

    def set_current_template(self, template: str):
        """Switch to a different template."""
        with self._state_lock:
            if template in self._state.templates:
                self._state.current_template = template
                self._broadcast("template_changed", {"template": template})

    def set_settings(self, settings: list[dict]):
        """Update current template settings."""
        with self._state_lock:
            self._state.settings = settings
            self._broadcast("settings_updated", {"settings": settings})

    def update_setting(self, node_id: str, internal_name: str, value: Any):
        """Update a single setting value."""
        with self._state_lock:
            for setting in self._state.settings:
                if setting.get("node_id") == node_id and setting.get("internalName") == internal_name:
                    setting["value"] = value
                    self._broadcast("setting_changed", {
                        "node_id": node_id,
                        "internalName": internal_name,
                        "value": value
                    })
                    return True
            return False

    def set_images(self, images: list[dict], total: Optional[int] = None):
        """Update images list (typically from pagination)."""
        with self._state_lock:
            self._state.images = images
            if total is not None:
                self._state.images_total = total
            self._broadcast("images_updated", {
                "images": images,
                "total": self._state.images_total
            })

    def add_image(self, image: dict):
        """Add a new image to the front of the list."""
        with self._state_lock:
            self._state.images.insert(0, image)
            self._state.images_total += 1
            self._broadcast("image_added", {"image": image})

    # ─────────────────────────────────────────────────────────────
    # Generation State Methods
    # ─────────────────────────────────────────────────────────────

    def start_generation(self, count: int):
        """Begin a generation batch."""
        with self._state_lock:
            gen = self._state.generation
            gen.is_generating = True
            gen.total = count
            gen.completed = 0
            gen.queued = []
            gen.progress = 0.0
            self._broadcast("generation_started", {"total": count})

    def add_to_queue(self, prompt_id: str):
        """Add a prompt to the generation queue."""
        with self._state_lock:
            self._state.generation.queued.append(prompt_id)
            self._broadcast("prompt_queued", {"prompt_id": prompt_id})

    def set_generation_progress(self, prompt_id: str, progress: float):
        """Update progress for current generation (0-100)."""
        with self._state_lock:
            gen = self._state.generation
            gen.current_prompt_id = prompt_id
            gen.progress = progress
            self._broadcast("generation_progress", {
                "prompt_id": prompt_id,
                "progress": progress
            })

    def complete_generation(self, prompt_id: str):
        """Mark a single generation as complete."""
        with self._state_lock:
            gen = self._state.generation
            if prompt_id in gen.queued:
                gen.queued.remove(prompt_id)
            gen.completed += 1
            gen.current_prompt_id = None
            gen.progress = 0.0

            # Check if batch is done
            if gen.completed >= gen.total:
                gen.is_generating = False
                self._broadcast("generation_batch_complete", {
                    "completed": gen.completed,
                    "total": gen.total
                })
            else:
                self._broadcast("generation_complete", {
                    "prompt_id": prompt_id,
                    "completed": gen.completed,
                    "total": gen.total
                })

    def cancel_generation(self):
        """Cancel ongoing generation batch."""
        with self._state_lock:
            gen = self._state.generation
            gen.is_generating = False
            gen.queued = []
            gen.current_prompt_id = None
            gen.progress = 0.0
            self._broadcast("generation_cancelled", {
                "completed": gen.completed,
                "total": gen.total
            })

    # ─────────────────────────────────────────────────────────────
    # Snapshot for new clients
    # ─────────────────────────────────────────────────────────────

    def get_full_state(self) -> dict:
        """Get complete state snapshot for new WebSocket clients."""
        with self._state_lock:
            return {
                "type": "full_state",
                "state": self._state.to_dict()
            }


# Global instance
_state_manager: Optional[StateManager] = None


def get_state_manager() -> StateManager:
    """Get the global StateManager instance."""
    global _state_manager
    if _state_manager is None:
        _state_manager = StateManager()
    return _state_manager
