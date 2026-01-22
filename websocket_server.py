"""
WebSocket Server - Pushes state updates to browser clients.

Uses Flask-SocketIO for WebSocket support. All state changes from
the StateManager are automatically broadcast to connected clients.
"""

import json
import logging
from typing import Optional

from flask import Flask
from flask_socketio import SocketIO, emit

from state import get_state_manager

log = logging.getLogger("comfy-viewer.websocket")

# Will be initialized by the main app
socketio: Optional[SocketIO] = None


def init_socketio(app: Flask) -> SocketIO:
    """Initialize SocketIO with the Flask app."""
    global socketio

    socketio = SocketIO(
        app,
        cors_allowed_origins="*",
        async_mode="threading",
        logger=False,
        engineio_logger=False
    )

    # Subscribe to state changes
    state = get_state_manager()
    state.subscribe(_broadcast_to_clients)

    # Register event handlers
    @socketio.on("connect")
    def handle_connect():
        log.info("Client connected")
        # Send full state to new client
        emit("state", state.get_full_state())

    @socketio.on("disconnect")
    def handle_disconnect():
        log.info("Client disconnected")

    @socketio.on("request_state")
    def handle_request_state():
        """Client requests full state refresh."""
        emit("state", state.get_full_state())

    @socketio.on("ping")
    def handle_ping():
        """Simple ping/pong for connection health."""
        emit("pong")

    log.info("SocketIO initialized")
    return socketio


def _broadcast_to_clients(message: dict):
    """
    Callback for StateManager - broadcasts state changes to all clients.

    This is called automatically whenever state changes.
    """
    if socketio is None:
        return

    try:
        socketio.emit("state", message)
    except Exception as e:
        log.error(f"Failed to broadcast to clients: {e}")


def get_socketio() -> Optional[SocketIO]:
    """Get the SocketIO instance."""
    return socketio
