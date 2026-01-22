/**
 * Client-side State Manager
 *
 * Connects to the server via WebSocket and maintains a synchronized
 * copy of the application state. Components subscribe to changes.
 */

class StateManager {
  constructor() {
    this.state = {
      comfy_connected: false,
      templates: [],
      current_template: null,
      settings: [],
      images: [],
      images_total: 0,
      generation: {
        is_generating: false,
        queued: [],
        current_prompt_id: null,
        progress: 0,
        completed: 0,
        total: 0,
      },
    };

    this.subscribers = new Map(); // key -> Set of callbacks
    this.socket = null;
    this.reconnectDelay = 1000;
    this.maxReconnectDelay = 30000;
  }

  /**
   * Connect to the WebSocket server.
   */
  connect() {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const wsUrl = `${protocol}//${window.location.host}`;

    // Using Socket.IO client
    this.socket = io(wsUrl, {
      transports: ["websocket", "polling"],
      reconnection: true,
      reconnectionDelay: this.reconnectDelay,
      reconnectionDelayMax: this.maxReconnectDelay,
    });

    this.socket.on("connect", () => {
      console.log("WebSocket connected");
      this.reconnectDelay = 1000;
      this._notify("connected", {});
    });

    this.socket.on("disconnect", () => {
      console.log("WebSocket disconnected");
      this._notify("disconnected", {});
    });

    this.socket.on("state", (message) => {
      this._handleMessage(message);
    });

    this.socket.on("connect_error", (error) => {
      console.error("WebSocket error:", error);
    });
  }

  /**
   * Handle incoming state messages from server.
   */
  _handleMessage(message) {
    const { type, data, state: newState } = message;

    // Update local state
    if (newState) {
      this._mergeState(newState);
    }

    // Notify subscribers based on event type
    this._notify(type, data);

    // Also notify general state subscribers
    this._notify("*", { type, data, state: this.state });
  }

  /**
   * Merge incoming state with local state.
   */
  _mergeState(newState) {
    // Deep merge for generation object
    if (newState.generation) {
      this.state.generation = { ...this.state.generation, ...newState.generation };
      delete newState.generation;
    }

    // Shallow merge for everything else
    Object.assign(this.state, newState);
  }

  /**
   * Subscribe to state changes.
   * @param {string} event - Event type to subscribe to, or '*' for all
   * @param {function} callback - Function to call on changes
   * @returns {function} Unsubscribe function
   */
  subscribe(event, callback) {
    if (!this.subscribers.has(event)) {
      this.subscribers.set(event, new Set());
    }
    this.subscribers.get(event).add(callback);

    return () => {
      this.subscribers.get(event)?.delete(callback);
    };
  }

  /**
   * Notify subscribers of an event.
   */
  _notify(event, data) {
    const callbacks = this.subscribers.get(event);
    if (callbacks) {
      callbacks.forEach((cb) => {
        try {
          cb(data, this.state);
        } catch (e) {
          console.error("Subscriber callback error:", e);
        }
      });
    }
  }

  /**
   * Request full state refresh from server.
   */
  requestState() {
    if (this.socket?.connected) {
      this.socket.emit("request_state");
    }
  }

  // ─────────────────────────────────────────────────────────────
  // Convenience Getters
  // ─────────────────────────────────────────────────────────────

  get isConnected() {
    return this.socket?.connected ?? false;
  }

  get isComfyConnected() {
    return this.state.comfy_connected;
  }

  get templates() {
    return this.state.templates;
  }

  get currentTemplate() {
    return this.state.current_template;
  }

  get settings() {
    return this.state.settings;
  }

  get images() {
    return this.state.images;
  }

  get imagesTotal() {
    return this.state.images_total;
  }

  get generation() {
    return this.state.generation;
  }

  get isGenerating() {
    return this.state.generation.is_generating;
  }
}

// Global instance
const appState = new StateManager();
