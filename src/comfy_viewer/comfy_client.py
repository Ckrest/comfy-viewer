"""
ComfyUI Client - Handles communication with ComfyUI server.

Two modes of communication:
1. HTTP: For submitting prompts and querying state
2. WebSocket: For real-time progress updates
"""

import json
import logging
import threading
import time
from typing import Any, Callable, Optional
from urllib.parse import urljoin

import requests
import websocket

from .state import get_state_manager

log = logging.getLogger("comfy-viewer.comfy_client")


class ComfyClient:
    """
    Client for ComfyUI server communication.

    Maintains a WebSocket connection for real-time progress updates
    and provides HTTP methods for API calls.
    """

    def __init__(self, host: str = "http://127.0.0.1:8188"):
        self.host = host.rstrip("/")
        self.ws_url = self.host.replace("http://", "ws://").replace("https://", "wss://")
        self._ws: Optional[websocket.WebSocketApp] = None
        self._ws_thread: Optional[threading.Thread] = None
        self._running = False
        self._reconnect_delay = 1.0
        self._max_reconnect_delay = 30.0
        self._state = get_state_manager()

        # Track which prompt_ids we're watching
        self._watched_prompts: set[str] = set()

    # ─────────────────────────────────────────────────────────────
    # HTTP API Methods
    # ─────────────────────────────────────────────────────────────

    def health_check(self) -> bool:
        """Check if ComfyUI is reachable."""
        try:
            response = requests.get(f"{self.host}/system_stats", timeout=5)
            connected = response.status_code == 200
            self._state.set_comfy_connected(connected)
            return connected
        except Exception as e:
            log.warning(f"Health check failed: {e}")
            self._state.set_comfy_connected(False)
            return False

    def post_prompt(self, prompt: dict, client_id: Optional[str] = None) -> dict:
        """
        Submit a prompt to ComfyUI.

        Args:
            prompt: The workflow prompt dict
            client_id: Optional client ID for tracking

        Returns:
            Response dict with prompt_id
        """
        payload = {"prompt": prompt}
        if client_id:
            payload["client_id"] = client_id

        try:
            response = requests.post(
                f"{self.host}/prompt",
                json=payload,
                timeout=30
            )
            response.raise_for_status()
            result = response.json()

            # Watch this prompt for progress updates
            if "prompt_id" in result:
                self._watched_prompts.add(result["prompt_id"])
                self._state.add_to_queue(result["prompt_id"])

            return result
        except requests.RequestException as e:
            log.error(f"Failed to post prompt: {e}")
            raise

    def get_history(self, prompt_id: str) -> Optional[dict]:
        """Get execution history for a prompt."""
        try:
            response = requests.get(f"{self.host}/history/{prompt_id}", timeout=10)
            if response.status_code == 200:
                return response.json()
            return None
        except Exception as e:
            log.error(f"Failed to get history for {prompt_id}: {e}")
            return None

    def get_queue(self) -> dict:
        """Get current queue status."""
        try:
            response = requests.get(f"{self.host}/queue", timeout=10)
            return response.json()
        except Exception as e:
            log.error(f"Failed to get queue: {e}")
            return {"queue_running": [], "queue_pending": []}

    # ─────────────────────────────────────────────────────────────
    # WebSocket Connection for Real-time Updates
    # ─────────────────────────────────────────────────────────────

    def connect_websocket(self):
        """Start WebSocket connection in background thread."""
        if self._running:
            return

        self._running = True
        self._ws_thread = threading.Thread(target=self._ws_loop, daemon=True)
        self._ws_thread.start()
        log.info("WebSocket connection thread started")

    def disconnect_websocket(self):
        """Stop WebSocket connection."""
        self._running = False
        if self._ws:
            self._ws.close()
        if self._ws_thread:
            self._ws_thread.join(timeout=5)
        log.info("WebSocket disconnected")

    def _ws_loop(self):
        """WebSocket connection loop with automatic reconnection."""
        delay = self._reconnect_delay

        while self._running:
            try:
                ws_url = f"{self.ws_url}/ws"
                log.info(f"Connecting to ComfyUI WebSocket: {ws_url}")

                self._ws = websocket.WebSocketApp(
                    ws_url,
                    on_open=self._on_ws_open,
                    on_message=self._on_ws_message,
                    on_error=self._on_ws_error,
                    on_close=self._on_ws_close
                )

                self._ws.run_forever(ping_interval=30, ping_timeout=10)

                # If we get here, connection closed
                if self._running:
                    log.warning(f"WebSocket closed, reconnecting in {delay}s...")
                    time.sleep(delay)
                    delay = min(delay * 2, self._max_reconnect_delay)

            except Exception as e:
                log.error(f"WebSocket error: {e}")
                if self._running:
                    time.sleep(delay)
                    delay = min(delay * 2, self._max_reconnect_delay)

    def _on_ws_open(self, ws):
        """Handle WebSocket connection opened."""
        log.info("ComfyUI WebSocket connected")
        self._state.set_comfy_connected(True)
        self._reconnect_delay = 1.0  # Reset delay on successful connection

    def _on_ws_message(self, ws, message):
        """Handle incoming WebSocket messages from ComfyUI."""
        try:
            data = json.loads(message)
            msg_type = data.get("type")

            if msg_type == "status":
                # Queue status update
                queue_info = data.get("data", {}).get("status", {}).get("exec_info", {})
                log.debug(f"Queue status: {queue_info}")

            elif msg_type == "execution_start":
                prompt_id = data.get("data", {}).get("prompt_id")
                if prompt_id in self._watched_prompts:
                    log.info(f"Execution started: {prompt_id}")
                    self._state.set_generation_progress(prompt_id, 0)

            elif msg_type == "executing":
                prompt_id = data.get("data", {}).get("prompt_id")
                node = data.get("data", {}).get("node")
                if prompt_id in self._watched_prompts:
                    if node is None:
                        # Execution complete
                        log.info(f"Execution complete: {prompt_id}")
                        self._watched_prompts.discard(prompt_id)
                        self._state.complete_generation(prompt_id)
                    else:
                        log.debug(f"Executing node {node} for {prompt_id}")

            elif msg_type == "progress":
                prompt_id = data.get("data", {}).get("prompt_id")
                value = data.get("data", {}).get("value", 0)
                max_val = data.get("data", {}).get("max", 100)
                if prompt_id in self._watched_prompts and max_val > 0:
                    progress = (value / max_val) * 100
                    self._state.set_generation_progress(prompt_id, progress)

            elif msg_type == "executed":
                prompt_id = data.get("data", {}).get("prompt_id")
                output = data.get("data", {}).get("output", {})
                if prompt_id in self._watched_prompts:
                    log.debug(f"Node executed for {prompt_id}: {list(output.keys())}")

        except json.JSONDecodeError:
            log.warning(f"Invalid JSON from WebSocket: {message[:100]}")
        except Exception as e:
            log.error(f"Error processing WebSocket message: {e}")

    def _on_ws_error(self, ws, error):
        """Handle WebSocket error."""
        log.error(f"WebSocket error: {error}")
        self._state.set_comfy_connected(False)

    def _on_ws_close(self, ws, close_status_code, close_msg):
        """Handle WebSocket connection closed."""
        log.warning(f"WebSocket closed: {close_status_code} - {close_msg}")
        self._state.set_comfy_connected(False)


# Global client instance
_client: Optional[ComfyClient] = None


def get_comfy_client(host: Optional[str] = None) -> ComfyClient:
    """Get or create the global ComfyClient instance."""
    global _client
    if _client is None:
        _client = ComfyClient(host or "http://127.0.0.1:8188")
    return _client
