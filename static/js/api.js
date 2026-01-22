/**
 * API Client
 *
 * Clean wrapper for all REST API calls. Returns promises.
 * State updates happen automatically via WebSocket after
 * successful mutations.
 */

const api = {
  url(path) {
    return path;
  },

  /**
   * Check ComfyUI health status.
   */
  async health() {
    const res = await fetch(this.url("/api/health"));
    return res.json();
  },

  /**
   * Get application configuration.
   */
  async getConfig() {
    const res = await fetch(this.url("/api/config"));
    return res.json();
  },

  /**
   * Update application configuration.
   */
  async updateConfig(config) {
    const res = await fetch(this.url("/api/config"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(config),
    });
    if (!res.ok) {
      const error = await res.json();
      throw new Error(error.error || "Failed to update config");
    }
    return res.json();
  },

  /**
   * Get list of available templates.
   */
  async getTemplates() {
    const res = await fetch(this.url("/api/templates"));
    return res.json();
  },

  /**
   * Get settings for a specific template.
   */
  async getTemplateSettings(template) {
    const res = await fetch(this.url(`/api/templates/${encodeURIComponent(template)}/settings`));
    if (!res.ok) throw new Error("Failed to load settings");
    return res.json();
  },

  /**
   * Save settings for a template.
   */
  async saveTemplateSettings(template, settings) {
    const res = await fetch(this.url(`/api/templates/${encodeURIComponent(template)}/settings`), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ settings }),
    });
    if (!res.ok) throw new Error("Failed to save settings");
    return res.json();
  },

  /**
   * Get paginated list of images.
   */
  async getImages(offset = 0, limit = 50) {
    const res = await fetch(this.url(`/api/images?offset=${offset}&limit=${limit}`));
    return res.json();
  },

  /**
   * Find an image's position in the sorted list.
   * Returns { filename, index, total } or throws if not found.
   */
  async findImage(filename) {
    const res = await fetch(this.url(`/api/images/find/${encodeURIComponent(filename)}`));
    if (!res.ok) throw new Error("Image not found");
    return res.json();
  },

  /**
   * Generate images with current template and settings.
   * Uses the conduit run endpoint for proper image registration.
   */
  async generate(template, settings, count = 1) {
    // Convert settings array to inputs object if needed
    const inputs = Array.isArray(settings)
      ? settings.reduce((acc, s) => { acc[s.name] = s.value; return acc; }, {})
      : settings;

    const res = await fetch(this.url(`/api/conduit/run/${encodeURIComponent(template)}`), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ inputs, count }),
    });
    if (!res.ok) {
      const error = await res.json();
      throw new Error(error.error || "Generation failed");
    }
    return res.json();
  },

  /**
   * Quick save an image to favorites.
   */
  async quickSave(filename) {
    const res = await fetch(this.url("/api/quick-save"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ filename }),
    });
    if (!res.ok) throw new Error("Quick save failed");
    return res.json();
  },

  /**
   * Toggle flag status on an image.
   */
  async toggleFlag(filename) {
    const res = await fetch(this.url(`/api/images/${encodeURIComponent(filename)}/flag`), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    if (!res.ok) throw new Error("Flag toggle failed");
    return res.json();
  },

  /**
   * Set rating on an image (-1=dislike, 0=neutral, 1=like).
   */
  async setRating(filename, rating) {
    const res = await fetch(this.url(`/api/images/${encodeURIComponent(filename)}/rate`), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ rating }),
    });
    if (!res.ok) throw new Error("Rating failed");
    return res.json();
  },

  /**
   * Get registration data for an image.
   * Registration includes image + associated data extracted by hooks.
   */
  async getRegistration(filename) {
    const res = await fetch(this.url(`/api/images/${encodeURIComponent(filename)}/registration`));
    if (!res.ok) return null;
    const data = await res.json();
    return data.has_data ? data.registration : null;
  },
};
