"use strict";

/* Une seule vignette visible à la fois ; les actions restent prioritaires.
   Pas de dépendance : les fonctions injectables servent aux tests Node. */
class ThumbnailQueue {
  constructor(options = {}) {
    this.fetcher = options.fetcher || globalThis.fetch.bind(globalThis);
    this.createURL = options.createURL || ((blob) => URL.createObjectURL(blob));
    this.revokeURL = options.revokeURL || ((url) => URL.revokeObjectURL(url));
    this.onFailure = options.onFailure || (() => {});
    this.timeoutMs = options.timeoutMs || 30000;
    this.retryMinMs = options.retryMinMs ?? 500;
    this.items = new Map();
    this.active = 0;
    this.holds = 0;
    this.paused = false;
    this.retryTimer = null;
    this.retryItem = null;
    const factory = options.observerFactory || (
      typeof IntersectionObserver === "function"
        ? (callback) => new IntersectionObserver(callback, { rootMargin: "160px 0px" })
        : null
    );
    this.observer = factory ? factory((entries) => this.visibility(entries)) : null;
  }

  observe(root) {
    if (!root) return;
    for (const image of root.querySelectorAll("img[data-thumb]")) {
      if (this.items.has(image)) continue;
      const item = { image, url: image.dataset.thumb, visible: !this.observer,
                     loading: false, loaded: false, failed: false,
                     controller: null, objectURL: null };
      this.items.set(image, item);
      image.closest(".thumb, .strip-item")?.classList.add("pending");
      if (this.observer) this.observer.observe(image);
    }
    this.pump();
  }

  visibility(entries) {
    for (const entry of entries) {
      const item = this.items.get(entry.target);
      if (!item) continue;
      item.visible = Boolean(entry.isIntersecting);
      if (!item.visible) {
        if (item.controller) item.controller.abort();
        this.cancelRetry(item);
      }
    }
    this.pump();
  }

  release(root) {
    if (!root) return;
    for (const [image, item] of this.items) {
      if (image !== root && !root.contains(image)) continue;
      this.items.delete(image);
      if (this.observer) this.observer.unobserve(image);
      if (item.controller) item.controller.abort();
      this.cancelRetry(item);
      if (item.objectURL) this.revokeURL(item.objectURL);
    }
    this.pump();
  }

  abortActive() {
    for (const item of this.items.values()) {
      if (item.controller) item.controller.abort();
    }
  }

  setPaused(paused) {
    this.paused = Boolean(paused);
    if (this.paused) {
      this.abortActive();
      clearTimeout(this.retryTimer);
      this.retryTimer = null;
      this.retryItem = null;
    } else {
      this.pump();
    }
  }

  // Pendant une action utilisateur, aucune nouvelle vignette n'est lancée.
  hold() {
    this.holds += 1;
    this.abortActive();
    let released = false;
    return () => {
      if (released) return;
      released = true;
      this.holds = Math.max(0, this.holds - 1);
      this.pump();
    };
  }

  pump() {
    if (this.paused || this.holds || this.active || this.retryTimer) return;
    for (const item of this.items.values()) {
      if (item.visible && !item.loading && !item.loaded && !item.failed) {
        this.start(item);
        return;
      }
    }
  }

  cancelRetry(item) {
    if (this.retryItem !== item) return;
    clearTimeout(this.retryTimer);
    this.retryTimer = null;
    this.retryItem = null;
  }

  retry(response, item) {
    const seconds = Number(response.headers?.get("Retry-After"));
    const delay = Math.max(this.retryMinMs,
      Math.min(5000, Number.isFinite(seconds) && seconds > 0 ? seconds * 1000 : 750));
    this.retryItem = item;
    this.retryTimer = setTimeout(() => {
      this.retryTimer = null;
      this.retryItem = null;
      this.pump();
    }, delay);
  }

  fail(item, message) {
    item.failed = true;
    item.image.dataset.broken = "1";
    item.image.title = message;
    item.image.classList.add("broken");
    const box = item.image.closest(".thumb, .strip-item");
    box?.classList.remove("pending");
    box?.classList.add("broken");
    if (this.observer) this.observer.unobserve(item.image);
    this.onFailure(item.image, message);
  }

  async start(item) {
    this.active += 1;
    item.loading = true;
    const controller = new AbortController();
    item.controller = controller;
    let timedOut = false;
    const timeout = setTimeout(() => { timedOut = true; controller.abort(); }, this.timeoutMs);
    try {
      const response = await this.fetcher(item.url, { signal: controller.signal });
      if (response.status === 429 || response.status === 503) {
        if (this.items.get(item.image) === item && !controller.signal.aborted) this.retry(response, item);
        return;
      }
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const blob = await response.blob();
      if (this.items.get(item.image) !== item || controller.signal.aborted) return;
      item.objectURL = this.createURL(blob);
      item.loaded = true;
      item.image.src = item.objectURL;
      item.image.closest(".thumb, .strip-item")?.classList.remove("pending");
      if (this.observer) this.observer.unobserve(item.image);
    } catch (error) {
      if (this.items.get(item.image) === item && (!controller.signal.aborted || timedOut)) {
        this.fail(item, timedOut ? "Aperçu trop long" : `Aperçu indisponible : ${error.message}`);
      }
    } finally {
      clearTimeout(timeout);
      item.controller = null;
      item.loading = false;
      this.active -= 1;
      this.pump();
    }
  }
}

if (typeof module !== "undefined" && module.exports) module.exports = { ThumbnailQueue };
