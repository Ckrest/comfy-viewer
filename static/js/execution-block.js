/**
 * ExecutionBlock - Shared Component for Generation Controls
 *
 * Layout: ● [count] [▶ Run]  (or "● unavailable" when offline)
 *
 * States (dot color is the ONLY indicator):
 *   - unavailable: Conduit API not reachable (red dot)
 *   - ready: Conduit available, ComfyUI idle (green dot)
 *   - busy: ComfyUI is generating (yellow dot)
 *
 * Check triggers (no polling):
 *   - Startup (with retry: 30s, then 60s, then give up)
 *   - New image received
 *   - Request error
 *
 * Usage:
 *   const execBlock = new ExecutionBlock({
 *     containerId: 'execBlockContainer',
 *     getRunConfig: async () => ({ workflow, inputs }),
 *   });
 *   execBlock.init();
 */

class ExecutionBlock {
  constructor(options = {}) {
    this.containerId = options.containerId || 'executionBlock';
    this.getRunConfig = options.getRunConfig || null;
    this.runEndpoint = options.runEndpoint || '/api/conduit/run/';
    this.statusEndpoint = options.statusEndpoint || '/api/conduit/status';

    // State
    this.state = 'unavailable'; // unavailable, ready, busy
    this.workflowsAvailable = true; // false = no workflows, hide controls
    this.retryCount = 0;
    this.retryTimer = null;

    // DOM elements
    this.container = null;
    this.block = null;
    this.dot = null;
    this.countInput = null;
    this.runBtn = null;
    this.unavailableMsg = null;
  }

  init() {
    this.container = document.getElementById(this.containerId);
    if (!this.container) {
      console.warn(`ExecutionBlock: container #${this.containerId} not found`);
      return;
    }

    this.buildDOM();
    this.loadSavedCount();
    this.setupEvents();
    this.checkStatus();
  }

  buildDOM() {
    // Clear and rebuild
    this.container.innerHTML = '';

    // Single line: dot + controls (or "unavailable")
    this.block = document.createElement('div');
    this.block.className = 'execution-block';

    // Dot
    this.dot = document.createElement('div');
    this.dot.className = 'exec-dot';
    this.block.appendChild(this.dot);

    // Count input
    this.countInput = document.createElement('input');
    this.countInput.type = 'number';
    this.countInput.className = 'exec-count';
    this.countInput.value = '1';
    this.countInput.min = '1';
    this.countInput.max = '100';
    this.countInput.title = 'Number of runs';
    this.block.appendChild(this.countInput);

    // Run button
    this.runBtn = document.createElement('button');
    this.runBtn.className = 'exec-run-btn';
    this.runBtn.innerHTML = `
      <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
        <path d="M8 5v14l11-7z"/>
      </svg>
      Run
    `;
    this.block.appendChild(this.runBtn);

    // Unavailable message (shown when offline, replaces controls)
    // Clicking it triggers a status recheck
    this.unavailableMsg = document.createElement('span');
    this.unavailableMsg.className = 'exec-unavailable-msg';
    this.unavailableMsg.textContent = 'unavailable';
    this.unavailableMsg.style.display = 'none';
    this.unavailableMsg.style.cursor = 'pointer';
    this.unavailableMsg.title = 'Click to retry';
    this.unavailableMsg.addEventListener('click', (e) => {
      e.stopPropagation();
      this.checkStatus();
    });
    this.block.appendChild(this.unavailableMsg);

    this.container.appendChild(this.block);
  }

  setupEvents() {
    this.runBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      this.run();
    });

    this.countInput.addEventListener('click', (e) => e.stopPropagation());

    // Persist count to localStorage on change
    this.countInput.addEventListener('change', () => {
      localStorage.setItem('execBlockCount', this.countInput.value);
    });
  }

  loadSavedCount() {
    const saved = localStorage.getItem('execBlockCount');
    if (saved && !isNaN(parseInt(saved))) {
      this.countInput.value = saved;
    }
  }

  // ─────────────────────────────────────────────────────────────
  // Status Checking
  // ─────────────────────────────────────────────────────────────

  async checkStatus() {
    try {
      const res = await fetch(this.statusEndpoint);
      if (!res.ok) throw new Error('Status check failed');

      const data = await res.json();

      // Check if conduit is available first
      if (!data.available) {
        this.setState('unavailable');
        this.scheduleRetry();
        return;
      }

      // Reset retry on success
      this.retryCount = 0;
      if (this.retryTimer) {
        clearTimeout(this.retryTimer);
        this.retryTimer = null;
      }

      // Check ComfyUI state - busy = yellow dot, idle = green dot
      if (data.comfyui_busy) {
        this.setState('busy');
      } else {
        this.setState('ready');
      }

    } catch (e) {
      console.warn('ExecutionBlock: Status check failed', e);
      this.setState('unavailable');
      this.scheduleRetry();
    }
  }

  scheduleRetry() {
    if (this.retryCount >= 2) {
      console.log('ExecutionBlock: Max retries reached, giving up');
      return;
    }

    const delay = this.retryCount === 0 ? 30000 : 60000;
    this.retryCount++;

    console.log(`ExecutionBlock: Retrying in ${delay / 1000}s (attempt ${this.retryCount}/2)`);

    this.retryTimer = setTimeout(() => {
      this.checkStatus();
    }, delay);
  }

  // ─────────────────────────────────────────────────────────────
  // State Management
  // ─────────────────────────────────────────────────────────────

  setState(newState) {
    this.state = newState;

    // Update dot
    this.dot.classList.remove('ready', 'busy');
    if (newState === 'ready') {
      this.dot.classList.add('ready');
    } else if (newState === 'busy') {
      this.dot.classList.add('busy');
    }

    this.updateControlsVisibility();
  }

  updateControlsVisibility() {
    // Hide controls if unavailable OR no workflows
    const hideControls = this.state === 'unavailable' || !this.workflowsAvailable;

    if (hideControls) {
      this.countInput.style.display = 'none';
      this.runBtn.style.display = 'none';
      this.unavailableMsg.style.display = 'inline';
      // Show appropriate message
      this.unavailableMsg.textContent = this.state === 'unavailable'
        ? 'image generation unavailable'
        : 'no workflows';
    } else {
      this.countInput.style.display = '';
      this.runBtn.style.display = '';
      this.unavailableMsg.style.display = 'none';
    }
  }

  setWorkflowsAvailable(available) {
    this.workflowsAvailable = available;
    this.updateControlsVisibility();
  }

  // ─────────────────────────────────────────────────────────────
  // Run Workflow
  // ─────────────────────────────────────────────────────────────

  getCount() {
    return parseInt(this.countInput?.value) || 1;
  }

  async run() {
    if (this.state === 'unavailable') return;
    if (!this.getRunConfig) {
      console.error('ExecutionBlock: getRunConfig not provided');
      return;
    }

    this.runBtn.disabled = true;

    try {
      const config = await this.getRunConfig();

      if (!config || !config.workflow) {
        console.error('ExecutionBlock: No workflow available');
        this.checkStatus();
        return;
      }

      const { workflow, inputs = {} } = config;
      const count = this.getCount();

      const res = await fetch(`${this.runEndpoint}${encodeURIComponent(workflow)}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ inputs, count })
      });

      const result = await res.json();

      if (!res.ok) {
        throw new Error(result.error || 'Run failed');
      }

      // Successfully queued - set to busy (progress will show when generation actually starts)
      this.setState('busy');

    } catch (e) {
      console.error('ExecutionBlock run failed:', e);
      this.checkStatus(); // Check what went wrong
    } finally {
      this.runBtn.disabled = false;
    }
  }

  // ─────────────────────────────────────────────────────────────
  // Event Handlers (called from WebSocket handlers)
  // Dot color is the ONLY indicator of state
  // ─────────────────────────────────────────────────────────────

  onGenerationStarted() {
    this.setState('busy');
  }

  onProgress(percent) {
    // Dot stays yellow - no text display
  }

  onGenerationComplete(completed, total) {
    // Dot stays yellow until batch complete
  }

  // Called when a new image is received - check if ComfyUI is still busy
  onImageReceived() {
    this.checkStatus();
  }

  // Called on errors to diagnose
  onError() {
    this.checkStatus();
  }
}

// Export for module usage or make global
if (typeof module !== 'undefined' && module.exports) {
  module.exports = ExecutionBlock;
} else {
  window.ExecutionBlock = ExecutionBlock;
}
