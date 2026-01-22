/**
 * Shared Settings Modal Component
 *
 * A reusable settings modal that can be configured for different pages.
 * Used by both Viewer and Library pages to maintain consistent UI/UX.
 *
 * Usage:
 *   const settings = new SettingsModal({
 *     modalId: 'settingsModal',
 *     formId: 'settingsForm',
 *     sections: { ... },
 *     elements: { ... },
 *     storageKey: 'comfy-viewer-settings',
 *     onSettingChange: (key, value) => { ... }
 *   });
 *   settings.init();
 */

class SettingsModal {
  constructor(config) {
    this.modalId = config.modalId || 'settingsModal';
    this.formId = config.formId || 'settingsForm';
    this.sections = config.sections || {};
    this.elements = config.elements || {};
    this.storageKey = config.storageKey || 'comfy-viewer-settings';
    this.apiEndpoint = config.apiEndpoint || null;
    this.onSettingChange = config.onSettingChange || (() => {});

    this.modal = null;
    this.form = null;
    this.settings = {};
    this.restartRequired = false;
    this.restartBtn = null;
  }

  init() {
    this.modal = document.getElementById(this.modalId);
    this.form = document.getElementById(this.formId);

    if (!this.modal || !this.form) {
      console.warn('Settings modal elements not found');
      return;
    }

    // Load settings
    this.load();

    // Render form
    this.render();

    // Setup event listeners
    this.setupEvents();

    // Apply initial settings
    this.applyAll();
  }

  setupEvents() {
    // Settings button (looks for common patterns)
    const settingsBtn = document.getElementById('settingsBtn') ||
                        document.getElementById('gallerySettingsBtn');
    settingsBtn?.addEventListener('click', (e) => {
      e.stopPropagation();
      this.open();
    });

    // Close button
    const closeBtn = this.modal.querySelector('.modal-close') ||
                     document.getElementById('modalClose');
    closeBtn?.addEventListener('click', (e) => {
      e.stopPropagation();
      this.close();
    });

    // Reset defaults button
    const resetBtn = document.getElementById('resetDefaults');
    resetBtn?.addEventListener('click', (e) => {
      e.stopPropagation();
      this.resetToDefaults();
    });

    // Restart button (created dynamically in footer)
    this.restartBtn = document.getElementById('restartRequired');
    if (!this.restartBtn) {
      // Create restart button if it doesn't exist
      const footer = this.modal.querySelector('.modal-footer');
      if (footer) {
        this.restartBtn = document.createElement('button');
        this.restartBtn.id = 'restartRequired';
        this.restartBtn.className = 'btn btn-warning';
        this.restartBtn.textContent = 'Restart to Apply';
        this.restartBtn.style.display = 'none';
        this.restartBtn.addEventListener('click', (e) => {
          e.stopPropagation();
          this.triggerRestart();
        });
        footer.insertBefore(this.restartBtn, footer.firstChild);
      }
    }

    // Click outside to close
    this.modal.addEventListener('click', (e) => {
      if (e.target === this.modal) {
        this.close();
      }
    });

    // Escape key to close
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && this.isOpen()) {
        this.close();
      }
    });
  }

  showRestartButton() {
    this.restartRequired = true;
    if (this.restartBtn) {
      this.restartBtn.style.display = '';
    }
  }

  hideRestartButton() {
    this.restartRequired = false;
    if (this.restartBtn) {
      this.restartBtn.style.display = 'none';
    }
  }

  async triggerRestart() {
    try {
      this.restartBtn.textContent = 'Restarting...';
      this.restartBtn.disabled = true;
      await fetch('/api/restart', { method: 'POST' });
      // Wait a moment then reload
      setTimeout(() => window.location.reload(), 2000);
    } catch (e) {
      console.error('Failed to restart:', e);
      this.restartBtn.textContent = 'Restart Failed';
      setTimeout(() => {
        this.restartBtn.textContent = 'Restart to Apply';
        this.restartBtn.disabled = false;
      }, 2000);
    }
  }

  isOpen() {
    return this.modal?.classList.contains('open');
  }

  open() {
    this.render();
    this.modal?.classList.add('open');
  }

  close() {
    this.modal?.classList.remove('open');
  }

  load() {
    // Try localStorage first
    const saved = localStorage.getItem(this.storageKey);
    if (saved) {
      try {
        this.settings = JSON.parse(saved);
      } catch (e) {
        console.error('Failed to parse settings:', e);
        this.settings = {};
      }
    }

    // Merge with defaults
    for (const [id, config] of Object.entries(this.elements)) {
      if (this.settings[id] === undefined) {
        this.settings[id] = config.default;
      }
    }

    return this.settings;
  }

  save(key, value) {
    this.settings[key] = value;
    localStorage.setItem(this.storageKey, JSON.stringify(this.settings));

    // Also save to API if endpoint configured
    if (this.apiEndpoint) {
      fetch(this.apiEndpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(this.settings)
      }).catch(e => console.error('Failed to save settings to API:', e));
    }
  }

  get(key) {
    return this.settings[key] ?? this.elements[key]?.default;
  }

  resetToDefaults() {
    for (const [id, config] of Object.entries(this.elements)) {
      this.settings[id] = config.default;
    }
    localStorage.setItem(this.storageKey, JSON.stringify(this.settings));
    this.render();
    this.applyAll();

    // Notify of all changes
    for (const [id, config] of Object.entries(this.elements)) {
      this.onSettingChange(id, config.default, this);
    }
  }

  applyAll() {
    for (const [id, config] of Object.entries(this.elements)) {
      const value = this.get(id);
      this.onSettingChange(id, value, this);
    }
  }

  render() {
    if (!this.form) return;

    this.form.innerHTML = '';

    for (const [sectionName, elementIds] of Object.entries(this.sections)) {
      // Section header
      const header = document.createElement('div');
      header.className = 'settings-section-header';
      header.textContent = sectionName;
      this.form.appendChild(header);

      // Elements in section
      for (const id of elementIds) {
        const config = this.elements[id];
        if (!config) continue;

        const row = this.createRow(id, config);
        this.form.appendChild(row);
      }
    }
  }

  createRow(id, config) {
    const current = this.get(id);
    const row = document.createElement('div');
    row.className = 'settings-row';

    const label = document.createElement('label');
    label.textContent = config.label;

    let input;

    switch (config.type) {
      case 'visibility':
        // Three-way toggle: visible/hidden/off
        input = this.createVisibilityControl(id, config, current);
        break;

      case 'number':
        input = document.createElement('input');
        input.type = 'number';
        input.className = 'settings-input';
        input.value = current;
        if (config.min !== undefined) input.min = config.min;
        if (config.max !== undefined) input.max = config.max;
        if (config.step !== undefined) input.step = config.step;
        input.addEventListener('change', () => {
          const value = parseFloat(input.value);
          this.save(id, value);
          this.onSettingChange(id, value, this);
        });
        break;

      case 'text':
        if (config.browse) {
          // Wrap input and browse button in a container
          input = document.createElement('div');
          input.className = 'settings-path-group';

          const textInput = document.createElement('input');
          textInput.type = 'text';
          textInput.className = 'settings-input settings-input-wide';
          textInput.value = current || '';
          textInput.placeholder = config.placeholder || '';
          textInput.addEventListener('change', () => {
            this.save(id, textInput.value);
            this.onSettingChange(id, textInput.value, this);
            if (config.requiresRestart) this.showRestartButton();
          });

          const browseBtn = document.createElement('button');
          browseBtn.type = 'button';
          browseBtn.className = 'btn btn-small settings-browse-btn';
          browseBtn.textContent = '...';
          browseBtn.title = 'Browse';
          browseBtn.addEventListener('click', async () => {
            try {
              const res = await fetch('/api/browse-folder');
              const data = await res.json();
              if (data.path) {
                textInput.value = data.path;
                this.save(id, data.path);
                this.onSettingChange(id, data.path, this);
                if (config.requiresRestart) this.showRestartButton();
              }
            } catch (e) {
              console.error('Browse failed:', e);
            }
          });

          input.appendChild(textInput);
          input.appendChild(browseBtn);
        } else {
          input = document.createElement('input');
          input.type = 'text';
          input.className = 'settings-input' + (config.wide ? ' settings-input-wide' : '');
          input.value = current || '';
          input.placeholder = config.placeholder || '';
          input.addEventListener('change', () => {
            this.save(id, input.value);
            this.onSettingChange(id, input.value, this);
            if (config.requiresRestart) this.showRestartButton();
          });
        }
        break;

      case 'toggle':
        input = document.createElement('div');
        input.className = 'settings-toggle' + (current ? ' active' : '');
        input.addEventListener('click', () => {
          input.classList.toggle('active');
          const value = input.classList.contains('active');
          this.save(id, value);
          this.onSettingChange(id, value, this);
        });
        break;

      case 'select':
        // Dropdown with predefined options
        input = document.createElement('select');
        input.className = 'settings-select';
        for (const opt of (config.options || [])) {
          const option = document.createElement('option');
          option.value = opt.value;
          option.textContent = opt.label;
          option.selected = current === opt.value;
          input.appendChild(option);
        }
        input.addEventListener('change', () => {
          const value = input.value;
          this.save(id, value);
          this.onSettingChange(id, value, this);
          if (config.requiresRestart) this.showRestartButton();
        });
        break;

      case 'readonly':
        // Read-only display value (for paths, info)
        input = document.createElement('span');
        input.className = 'settings-readonly';
        input.textContent = config.getValue ? config.getValue() : (current || config.placeholder || '—');
        input.title = input.textContent;
        break;

      default:
        input = document.createElement('span');
        input.textContent = current;
    }

    row.appendChild(label);
    row.appendChild(input);
    return row;
  }

  createVisibilityControl(id, config, current) {
    const group = document.createElement('div');
    group.className = 'radio-group';

    const options = [
      { value: 'visible', label: 'Visible' },
      { value: 'hidden', label: 'Hidden' },
      { value: 'off', label: 'Off', disabled: !config.canTurnOff }
    ];

    for (const opt of options) {
      const label = document.createElement('label');
      if (opt.disabled) label.className = 'disabled';

      const radio = document.createElement('input');
      radio.type = 'radio';
      radio.name = id;
      radio.value = opt.value;
      radio.checked = current === opt.value;
      radio.disabled = opt.disabled;

      radio.addEventListener('change', () => {
        this.save(id, opt.value);
        this.onSettingChange(id, opt.value, this);
      });

      label.appendChild(radio);
      label.appendChild(document.createTextNode(opt.label));
      group.appendChild(label);
    }

    return group;
  }
}

// Export for use in both pages
window.SettingsModal = SettingsModal;
